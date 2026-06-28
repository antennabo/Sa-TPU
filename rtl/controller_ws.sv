// controller_ws — WS 模式指令级控制器
//
// 行为镜像 simulator/cycle/sim_model/controller_ws_ref.py；spec 详见
// doc/controller_ws.md。
//
// 指令字段（指令内常量）：
//   i_wtile_num   — 跑几个 weight tile (≥1)
//   i_act_staddr  — abuf 读起点 (所有 wtile 共用)
//   i_acc_staddr  — accumulator 写起点 (首 wtile)
//   i_feed_num    — 每 wtile 读多少行 activation = 写多少行 psum (≥W)
//
// 流控：
//   i_start            — 上升沿在 IDLE 触发
//   i_weight_loaded    — sa shadow 满 (= !|wfifo_rdy)
//   i_activ_available  — 上层确认本指令激活已在 abuf
//
// 7 态 FSM: IDLE / WLOAD / FEED / CAPTURE / OVERLAP / REWAIT / DRAIN
//   inter-wtile 切换若 weight 未就绪走 REWAIT；REWAIT 出口双分支：
//     cnt<W → OVERLAP (旧 drain 未完, 早退 + boundary inject)
//     cnt≥W → FEED    (旧 drain 已完, cold inject 从空流水重启)
//
// per-column deskew SR (长 AC-1) 在本模块内完成，accumulator 端直收 per-AC 信号。
module controller_ws #(
    parameter int AR             = 8,
    parameter int AC             = 8,
    parameter int LATENCY        = 2,
    parameter int WTILE_NUM_MAX  = 4,
    parameter int ACT_ADDR_W     = 8,
    parameter int ACC_ADDR_W     = 10,
    localparam int W             = AR + LATENCY,                       // warmup / drain 拍数
    localparam int WTILE_NUM_W   = (WTILE_NUM_MAX > 1) ? $clog2(WTILE_NUM_MAX + 1) : 1,
    // feed_num 取值范围 [W, 2^ACT_ADDR_W]，需要 ACT_ADDR_W+1 位
    localparam int FEED_NUM_W    = ACT_ADDR_W + 1,
    localparam int CNT_W         = (FEED_NUM_W > $clog2(W+1)) ? FEED_NUM_W : $clog2(W+1)
) (
    input  logic                      clk,
    input  logic                      rst_n,

    // ── 流控 ───────────────────────────────────────────
    input  logic                      i_start,
    input  logic                      i_weight_loaded,
    input  logic                      i_activ_available,

    // ── 指令字段 (指令内常量) ──────────────────────────
    input  logic [WTILE_NUM_W-1:0]    i_wtile_num,
    input  logic [ACT_ADDR_W-1:0]     i_act_staddr,
    input  logic [ACC_ADDR_W-1:0]     i_acc_staddr,
    input  logic [FEED_NUM_W-1:0]     i_feed_num,

    // ── SA weight swap (行 stagger SR) ─────────────────
    output logic                      o_weight_sw   [AR],

    // ── accumulator 写控制 (per-AC, 已 deskew) ─────────
    output logic                      o_acc_wen     [AC],
    output logic [ACC_ADDR_W-1:0]     o_acc_waddr   [AC],
    output logic                      o_acc_accen   [AC],
    output logic                      o_acc_outen   [AC],
    // ── accumulator 读 (本指令恒 0，占位) ──────────────
    output logic                      o_acc_ren     [AC],
    output logic [ACC_ADDR_W-1:0]     o_acc_raddr   [AC],

    // ── abuf 读 (per-AR, 行 stagger) ────────────────────
    output logic                      o_act_ren     [AR],
    output logic [ACT_ADDR_W-1:0]     o_act_raddr   [AR],

    // ── 观察 ───────────────────────────────────────────
    output logic [2:0]                o_ws_state
);
    // ── state encoding ──
    localparam logic [2:0]  S_IDLE    = 3'd0;
    localparam logic [2:0]  S_WLOAD   = 3'd1;
    localparam logic [2:0]  S_FEED    = 3'd2;
    localparam logic [2:0]  S_CAPTURE = 3'd3;
    localparam logic [2:0]  S_OVERLAP = 3'd4;
    localparam logic [2:0]  S_REWAIT  = 3'd5;
    localparam logic [2:0]  S_DRAIN   = 3'd6;

    // ── registers ──
    logic [2:0]              ws_state;
    logic [CNT_W-1:0]        ws_cnt;
    logic                    start_prev;
    logic [WTILE_NUM_W-1:0]  wtile_idx;
    logic [FEED_NUM_W-1:0]   wr_row;                   // 与 feed_num 同宽
    logic [ACT_ADDR_W-1:0]   offset       [AR];

    // ── next-state / 派生 wire ──
    logic [2:0]              ws_state_next;
    logic [CNT_W-1:0]        ws_cnt_next;
    logic [WTILE_NUM_W-1:0]  wtile_idx_next;
    logic [FEED_NUM_W-1:0]   wr_row_next;
    logic                    start_edge;
    logic                    feed_last, cap_last, ovl_last, drain_last, rew_drain_done;
    logic                    is_last_wtile, is_last_wtile_next;
    logic                    cold, boundary, inject;
    logic                    offset_rst;
    logic                    out_feed_next;
    logic                    wr_vld_scalar;            // col-0 节奏 wr_vld
    logic [ACC_ADDR_W-1:0]   tile_acc_base;
    logic [ACC_ADDR_W-1:0]   acc_addr_scalar;
    logic                    lane_fire    [AR];
    logic [ACT_ADDR_W-1:0]   raddr_d      [AR];        // 本拍 o_act_raddr FF 输入
    logic [ACT_ADDR_W-1:0]   offset_d     [AR];        // 本拍 offset FF 输入 (= 下拍 offset 值)

    // ──────────────────────────────────────────────────────
    // 组合逻辑：FSM next-state
    // ──────────────────────────────────────────────────────
    assign start_edge         = i_start & ~start_prev;
    assign feed_last          = (ws_cnt == CNT_W'(W - 1));
    assign cap_last           = (ws_cnt == CNT_W'(i_feed_num) - CNT_W'(W) - CNT_W'(1));
    assign ovl_last           = (ws_cnt == CNT_W'(W - 1));
    assign drain_last         = (ws_cnt == CNT_W'(W - 1));
    assign rew_drain_done     = (ws_cnt >= CNT_W'(W));
    assign is_last_wtile      = (wtile_idx == i_wtile_num - WTILE_NUM_W'(1));
    assign is_last_wtile_next = (wtile_idx == i_wtile_num - WTILE_NUM_W'(2));

    always_comb begin
        ws_state_next  = ws_state;
        ws_cnt_next    = ws_cnt + CNT_W'(1);
        wtile_idx_next = wtile_idx;
        unique case (ws_state)
            S_IDLE: begin
                ws_cnt_next = '0;
                if (start_edge) begin
                    ws_state_next  = S_WLOAD;
                    wtile_idx_next = '0;
                end
            end
            S_WLOAD: begin
                if (i_weight_loaded & i_activ_available) begin
                    ws_state_next  = S_FEED;
                    ws_cnt_next    = '0;
                    wtile_idx_next = '0;
                end
            end
            S_FEED: begin
                if (feed_last) begin
                    ws_cnt_next = '0;
                    if (i_feed_num > FEED_NUM_W'(W)) begin
                        ws_state_next = S_CAPTURE;
                    end else if (is_last_wtile) begin
                        ws_state_next = S_DRAIN;
                    end else if (i_weight_loaded) begin
                        ws_state_next = S_OVERLAP;
                    end else begin
                        ws_state_next = S_REWAIT;
                    end
                end
            end
            S_CAPTURE: begin
                if (cap_last) begin
                    ws_cnt_next = '0;
                    if (is_last_wtile) begin
                        ws_state_next = S_DRAIN;
                    end else if (i_weight_loaded) begin
                        ws_state_next = S_OVERLAP;
                    end else begin
                        ws_state_next = S_REWAIT;
                    end
                end
            end
            S_OVERLAP: begin
                if (ovl_last) begin
                    ws_cnt_next    = '0;
                    wtile_idx_next = wtile_idx + WTILE_NUM_W'(1);
                    if (i_feed_num > FEED_NUM_W'(W)) begin
                        ws_state_next = S_CAPTURE;
                    end else if (is_last_wtile_next) begin
                        ws_state_next = S_DRAIN;
                    end else if (i_weight_loaded) begin
                        ws_state_next = S_OVERLAP;
                    end else begin
                        ws_state_next = S_REWAIT;
                    end
                end
            end
            S_REWAIT: begin
                if (i_weight_loaded) begin
                    ws_cnt_next = '0;
                    if (rew_drain_done) begin
                        ws_state_next  = S_FEED;
                        wtile_idx_next = wtile_idx + WTILE_NUM_W'(1);
                    end else begin
                        ws_state_next = S_OVERLAP;
                    end
                end else begin
                    // saturate ws_cnt at W to avoid wrap (rew_drain_done 一旦真就锁定)
                    ws_cnt_next = rew_drain_done ? ws_cnt : (ws_cnt + CNT_W'(1));
                end
            end
            S_DRAIN: begin
                if (drain_last) begin
                    ws_state_next = S_IDLE;
                    ws_cnt_next   = '0;
                end
            end
            default: begin
                ws_state_next = S_IDLE;
                ws_cnt_next   = '0;
            end
        endcase
    end

    // ──────────────────────────────────────────────────────
    // 组合逻辑：inject / offset_rst / wr_row / 输出闸
    // ──────────────────────────────────────────────────────
    // cold = (WLOAD|REWAIT) → FEED 那一拍
    assign cold     = ((ws_state == S_WLOAD)  && (ws_state_next == S_FEED))
                   || ((ws_state == S_REWAIT) && (ws_state_next == S_FEED));
    // boundary = TRANSITION to OVERLAP 那一拍 (1-cycle pulse, NOT held throughout OVERLAP)
    //   FEED/CAP/REW → OVL: state changes, fires once
    //   OVL → OVL (Case E inter-wtile): same state but cnt==W-1 (about to wrap to new OVL)
    assign boundary = (ws_state_next == S_OVERLAP)
                   && (((ws_state == S_FEED)    && feed_last)
                    || ((ws_state == S_CAPTURE) && cap_last)
                    || ((ws_state == S_REWAIT))
                    || ((ws_state == S_OVERLAP) && ovl_last));
    assign inject     = cold | boundary;
    // offset_rst 只在 REWAIT 退出时拉起。其他过渡（cold/CAPTURE-boundary 等）lane 是
    // 连续 firing 的，offset 自然 wrap 已经处理对行 stagger，不能强 reset (会破坏 stagger)
    assign offset_rst = (ws_state == S_REWAIT)
                     && ((ws_state_next == S_OVERLAP) || (ws_state_next == S_FEED));

    // out_feed_next: 下拍 lane_fire[0] (= next state ∈ FEED/CAPTURE/OVERLAP)
    assign out_feed_next = (ws_state_next == S_FEED)
                        || (ws_state_next == S_CAPTURE)
                        || (ws_state_next == S_OVERLAP);

    // wr_row: wtile_idx 变化那拍归 0；处于 {CAP, OVL, REW, DRAIN} 且 wr_row<feed_num 时 +1
    logic wr_row_active;
    assign wr_row_active = (ws_state == S_CAPTURE)
                        || (ws_state == S_OVERLAP)
                        || (ws_state == S_REWAIT)
                        || (ws_state == S_DRAIN);
    always_comb begin
        if (wtile_idx_next != wtile_idx) begin
            wr_row_next = '0;
        end else if (wr_row_active && (wr_row < i_feed_num)) begin
            wr_row_next = wr_row + 1'b1;
        end else begin
            wr_row_next = wr_row;
        end
    end

    // wr_vld_scalar = 处于写态 && wr_row 未到 cap
    assign wr_vld_scalar = wr_row_active && (wr_row < i_feed_num);

    // tile_acc_base / acc_addr_scalar (基于本拍寄存值)
    assign tile_acc_base   = i_acc_staddr + ACC_ADDR_W'(wtile_idx) * ACC_ADDR_W'(i_feed_num);
    assign acc_addr_scalar = tile_acc_base + ACC_ADDR_W'(wr_row);

    // ──────────────────────────────────────────────────────
    // 组合逻辑：abuf offset[c] / o_act_raddr 直 mux on {offset_rst, lane_fire[c]}
    //   raddr_d[c]  = reset拍取 0, 否则取 offset[c]      (本拍要读的 row)
    //   offset_d[c] = 下拍 offset 值：
    //     {rst=1, fire=1}: 1  (本拍读 row 0, 下拍接着读 row 1)
    //     {rst=1, fire=0}: 0  (lane c≥1 在 inject 拍还没轮到 → 干净归 0)
    //     {rst=0, fire=1}: 递增/回卷
    //     {rst=0, fire=0}: 持值
    // ──────────────────────────────────────────────────────
    always_comb begin
        for (int c = 0; c < AR; c = c + 1) begin
            raddr_d[c]  = offset_rst ? '0 : offset[c];
            unique case ({offset_rst, lane_fire[c]})
                2'b11: offset_d[c] = ACT_ADDR_W'(1);
                2'b10: offset_d[c] = '0;
                2'b01: offset_d[c] = ({1'b0, offset[c]} == i_feed_num - FEED_NUM_W'(1)) ? '0
                                                                                        : offset[c] + ACT_ADDR_W'(1);
                2'b00: offset_d[c] = offset[c];
            endcase
        end
    end

    // lane_fire[c]: lane 0 用 out_feed_next；c≥1 用 o_act_ren[c-1] (SR 链)
    always_comb begin
        lane_fire[0] = out_feed_next;
        for (int c = 1; c < AR; c = c + 1) lane_fire[c] = o_act_ren[c-1];
    end

    // ──────────────────────────────────────────────────────
    // 时序逻辑
    // ──────────────────────────────────────────────────────

    // FSM: state / cnt / start_prev / wtile_idx / o_ws_state
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            ws_state   <= S_IDLE;
            ws_cnt     <= '0;
            start_prev <= 1'b0;
            wtile_idx  <= '0;
            o_ws_state <= S_IDLE;
        end else begin
            ws_state   <= ws_state_next;
            ws_cnt     <= ws_cnt_next;
            start_prev <= i_start;
            wtile_idx  <= wtile_idx_next;
            o_ws_state <= ws_state_next;
        end
    end

    // wr_row
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)  wr_row <= '0;
        else         wr_row <= wr_row_next;
    end

    // o_weight_sw[AR]: 左边缘 FF + 行 stagger SR
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (int k = 0; k < AR; k = k + 1) o_weight_sw[k] <= 1'b0;
        end else begin
            o_weight_sw[0] <= inject;
            for (int k = 1; k < AR; k = k + 1) o_weight_sw[k] <= o_weight_sw[k-1];
        end
    end

    // o_act_ren[AR]: lane_fire SR (本身当 SR)
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (int c = 0; c < AR; c = c + 1) o_act_ren[c] <= 1'b0;
        end else begin
            for (int c = 0; c < AR; c = c + 1) o_act_ren[c] <= lane_fire[c];
        end
    end

    // o_act_raddr[AR] + offset[c] —— 直 mux 落 FF
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (int c = 0; c < AR; c = c + 1) begin
                offset[c]      <= '0;
                o_act_raddr[c] <= '0;
            end
        end else begin
            for (int c = 0; c < AR; c = c + 1) begin
                o_act_raddr[c] <= i_act_staddr + raddr_d[c];
                offset[c]      <= offset_d[c];
            end
        end
    end

    // o_acc_wen[AC] / o_acc_waddr[AC] / o_acc_outen[AC]: per-column deskew SR
    //   col 0 直接吃标量；col c≥1 吃 *_q[c-1]，即 o_acc_*[c-1] 本身做 SR
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (int c = 0; c < AC; c = c + 1) begin
                o_acc_wen[c]   <= 1'b0;
                o_acc_waddr[c] <= '0;
                o_acc_outen[c] <= 1'b0;
            end
        end else begin
            o_acc_wen[0]   <= wr_vld_scalar;
            o_acc_waddr[0] <= acc_addr_scalar;
            o_acc_outen[0] <= wr_vld_scalar;
            for (int c = 1; c < AC; c = c + 1) begin
                o_acc_wen[c]   <= o_acc_wen[c-1];
                o_acc_waddr[c] <= o_acc_waddr[c-1];
                o_acc_outen[c] <= o_acc_outen[c-1];
            end
        end
    end

    // o_acc_accen[AC] 恒 0；o_acc_ren / o_acc_raddr 占位恒 0
    always_comb begin
        for (int c = 0; c < AC; c = c + 1) begin
            o_acc_accen[c] = 1'b0;
            o_acc_ren[c]   = 1'b0;
            o_acc_raddr[c] = '0;
        end
    end

endmodule
