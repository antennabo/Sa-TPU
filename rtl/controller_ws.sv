// controller_ws — WS 模式 controller 状态机 + activation_buf 寻址生成。
//
// 镜像 simulator/cycle/sim_model/controller_ws_ref.py。
//   - 6 个 state: IDLE / WLOAD / FEED / CAPTURE / OVERLAP / DRAIN
//     * FEED: 纯 feed warmup (W=AR+L+1 拍)
//     * CAPTURE: feed 续 + capture (F-W 拍)
//     * OVERLAP: 下个 tile FEED + 当前 tile DRAIN (W 拍)
//     * DRAIN: 停 feed，排完最后 W 行 psum (W 拍)
//   - 状态机驱动 feed / b_sw[AR] / wr_row / wr_tile / wr_vld（基于 state_next 直接产生，无 SR）
//   - cur_tile / pending_tile FF 跟踪 capture 中和暂存的 tile tag
// 额外塞进来的 abuf 寻址生成：
//   - prop_SR[AR]：feed → lane 0 注入、每拍右推，lane c 延 c 拍点亮
//   - rd_ptr[AR]: 每 lane 自加，封顶 cap = i_tile_num * i_F 归 0 复用
//   - page: i_switch_page=1 时翻活动页 + 所有 rd_ptr 归 0
//
// 时序：所有输出寄存（commit 后生效）。prop_SR 注入端用本模块寄存器 o_feed（上拍 commit 后值）。
//
// 维度参数：AR/AC/LATENCY/TILE_NUM_MAX/K_ABUF_MAX = 编译期常数；F = 运行时输入（i_F，当前作业 M 维）
module controller_ws #(
    parameter int AR             = 4,
    parameter int AC             = 2,
    parameter int LATENCY        = 2,
    parameter int TILE_NUM_MAX   = 4,
    parameter int K_ABUF_MAX     = 8,                       // abuf 每 tile 最大深度（synth）
    parameter int F_MAX          = K_ABUF_MAX,              // F 最大值，用于位宽
    parameter int TAG_W          = 4,
    parameter int WR_TILE_W      = TAG_W,
    localparam int W             = AR + LATENCY + 1,        // FEED 长度 (warmup)
    localparam int M_W           = $clog2(F_MAX + 1),
    localparam int TILE_NUM_W    = $clog2(TILE_NUM_MAX + 1),
    localparam int PAGE_SPAN     = K_ABUF_MAX * TILE_NUM_MAX,
    localparam int ABUF_DEPTH    = 2 * PAGE_SPAN,
    localparam int LANE_ADDR_W   = $clog2(ABUF_DEPTH),
    localparam int CNT_MAX       = (F_MAX > W) ? F_MAX : W,
    localparam int CNT_W         = $clog2(CNT_MAX + 1)
) (
    input  logic                      clk,
    input  logic                      rst_n,

    // 上层握手
    input  logic                      i_start,              // 启动脉冲 (上升沿触发 IDLE→WLOAD)
    input  logic                      i_weight_loaded,      // SA shadow 满 (WLOAD→FEED 触发)
    input  logic                      i_activ_available,
    input  logic                      i_switch_weight,
    input  logic [TAG_W-1:0]          i_tag,
    input  logic [TILE_NUM_W-1:0]     i_tile_num,
    input  logic                      i_switch_page,
    input  logic [M_W-1:0]            i_F,                  // 当前作业 M 维（运行时）

    // 给 sa 的控制波
    output logic                      o_feed,
    output logic                      o_b_sw     [AR],

    // capture 标量（state-based，对齐 col 0 psum 到达）
    output logic [M_W-1:0]            o_wr_row,
    output logic [WR_TILE_W-1:0]      o_wr_tile,
    output logic                      o_wr_vld,

    // abuf 读口 per-lane
    output logic                      o_rd_en    [AR],
    output logic [LANE_ADDR_W-1:0]    o_rd_addr  [AR],

    // 状态观察
    output logic [2:0]                o_ws_state
);
    // ── state encoding (跟 WSStateRef IntEnum 对应) ──────
    localparam logic [2:0] S_IDLE    = 3'd0;
    localparam logic [2:0] S_WLOAD   = 3'd1;
    localparam logic [2:0] S_FEED    = 3'd2;   // warmup：纯 feed
    localparam logic [2:0] S_CAPTURE = 3'd3;   // feed + capture
    localparam logic [2:0] S_OVERLAP = 3'd4;   // 下个 tile feed + 当前 tile drain
    localparam logic [2:0] S_DRAIN   = 3'd5;

    // ── state regs ─────────────────────────────────────────
    logic [2:0]                 ws_state;
    logic [CNT_W-1:0]           ws_cnt;
    logic                       switch_sr [AR];
    logic                       prop      [AR];
    logic [LANE_ADDR_W-1:0]     rd_ptr    [AR];
    logic                       page;
    logic                       start_prev;
    logic [TAG_W-1:0]           cur_tile;       // 当前正在被 capture 的 tile
    logic [TAG_W-1:0]           pending_tile;   // 边界 inject 时暂存下一 tile

    // ── combinational next ────────────────────────────────
    logic [2:0]                 ws_state_next;
    logic [CNT_W-1:0]           ws_cnt_next;
    logic                       feed_next;
    logic                       switch_sr_next [AR];
    logic                       ws_switch_inject;
    logic                       b_sw_next [AR];
    logic [M_W-1:0]             wr_row_next;
    logic [WR_TILE_W-1:0]       wr_tile_next;
    logic                       wr_vld_next;
    logic [TAG_W-1:0]           cur_tile_next;
    logic [TAG_W-1:0]           pending_tile_next;
    logic                       prop_next [AR];
    logic                       rd_en_next [AR];
    logic [LANE_ADDR_W-1:0]     rd_addr_next [AR];
    logic [LANE_ADDR_W-1:0]     rd_ptr_next  [AR];
    logic                       page_next;
    logic [LANE_ADDR_W-1:0]     cap;
    logic [LANE_ADDR_W-1:0]     page_offset;
    logic                       cold;
    logic                       boundary;
    logic                       start_edge;
    logic                       feed_last;        // FEED ws_cnt == W-1（FEED 末拍）
    logic                       cap_last;         // CAPTURE ws_cnt == F-W-1（CAPTURE 末拍）
    logic                       overlap_last;     // OVERLAP ws_cnt == W-1（OVERLAP 末拍）
    logic                       drain_last;       // DRAIN ws_cnt == W-1（DRAIN 末拍）
    logic                       F_gt_W;           // F > W

    assign start_edge   = i_start & ~start_prev;
    assign F_gt_W       = (i_F > M_W'(W));
    assign feed_last    = (ws_cnt == CNT_W'(W - 1));
    assign cap_last     = (ws_cnt == CNT_W'(i_F - M_W'(W) - 1));
    assign overlap_last = (ws_cnt == CNT_W'(W - 1));
    assign drain_last   = (ws_cnt == CNT_W'(W - 1));

    // ── WS state machine ──────────────────────────────────
    always_comb begin
        ws_state_next = ws_state;
        ws_cnt_next   = ws_cnt + 1;
        unique case (ws_state)
            S_IDLE: begin
                ws_cnt_next = '0;
                if (start_edge) ws_state_next = S_WLOAD;
            end
            S_WLOAD: begin
                if (i_weight_loaded & i_activ_available) begin
                    ws_state_next = S_FEED;
                    ws_cnt_next   = '0;
                end
            end
            S_FEED: begin
                if (feed_last) begin
                    if (F_gt_W) begin
                        ws_state_next = S_CAPTURE;
                    end else begin
                        // F == W：CAPTURE 0 拍，直接 OVERLAP/DRAIN
                        ws_state_next = i_switch_weight ? S_OVERLAP : S_DRAIN;
                    end
                    ws_cnt_next = '0;
                end
            end
            S_CAPTURE: begin
                if (cap_last) begin
                    ws_state_next = i_switch_weight ? S_OVERLAP : S_DRAIN;
                    ws_cnt_next = '0;
                end
            end
            S_OVERLAP: begin
                if (overlap_last) begin
                    if (F_gt_W) begin
                        ws_state_next = S_CAPTURE;
                    end else begin
                        ws_state_next = i_switch_weight ? S_OVERLAP : S_DRAIN;
                    end
                    ws_cnt_next = '0;
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

    // ── switch inject + SR + b_sw ─────────────────────────
    // cold     : WLOAD→FEED 首段冷启动（单拍）
    // boundary : 进入 OVERLAP 的【那拍】= 段间切 weight（单拍）
    //   - FEED→OVERLAP (F==W) / CAPTURE→OVERLAP (F>W) / OVERLAP→OVERLAP (F==W 连续段, ws_cnt==W-1)
    assign cold     = (ws_state == S_WLOAD) && (ws_state_next == S_FEED);
    assign boundary = (((ws_state == S_FEED) || (ws_state == S_CAPTURE)) && (ws_state_next == S_OVERLAP))
                   || ((ws_state == S_OVERLAP) && overlap_last && (ws_state_next == S_OVERLAP));
    assign ws_switch_inject = cold | boundary;

    always_comb begin
        switch_sr_next[0] = ws_switch_inject;
        for (int k = 1; k < AR; k = k + 1) switch_sr_next[k] = switch_sr[k - 1];
    end
    always_comb begin
        for (int k = 0; k < AR; k = k + 1) b_sw_next[k] = switch_sr_next[k];
    end

    // ── tile tag latching ─────────────────────────────────
    // cur_tile: 当前 capture 的 tile（wr_tile 出口源）
    // pending_tile: 边界 inject 时暂存下一 tile 的 tag
    always_comb begin
        if (cold) begin
            cur_tile_next     = i_tag;
            pending_tile_next = pending_tile;
        end else if ((ws_state == S_OVERLAP) && overlap_last && (ws_state_next == S_OVERLAP)) begin
            // OVERLAP→OVERLAP（F==W 连续段）：cur_tile 推进，pending 锁新 i_tag
            cur_tile_next     = pending_tile;
            pending_tile_next = i_tag;
        end else if (boundary) begin
            // FEED/CAPTURE→OVERLAP：当前段还在 capture，下段 tag 暂存
            cur_tile_next     = cur_tile;
            pending_tile_next = i_tag;
        end else if ((ws_state == S_OVERLAP) && overlap_last && (ws_state_next == S_CAPTURE)) begin
            // OVERLAP→CAPTURE：切到新 tile capture
            cur_tile_next     = pending_tile;
            pending_tile_next = pending_tile;
        end else if ((ws_state == S_OVERLAP) && overlap_last && (ws_state_next == S_DRAIN)) begin
            // F==W 时 OVERLAP→DRAIN：cur_tile 推进
            cur_tile_next     = pending_tile;
            pending_tile_next = pending_tile;
        end else begin
            cur_tile_next     = cur_tile;
            pending_tile_next = pending_tile;
        end
    end

    // ── feed / wr_vld / wr_row / wr_tile (基于 state_next) ──
    assign feed_next   = (ws_state_next == S_FEED) || (ws_state_next == S_CAPTURE)
                       || (ws_state_next == S_OVERLAP);
    assign wr_vld_next = (ws_state_next == S_CAPTURE) || (ws_state_next == S_OVERLAP)
                       || (ws_state_next == S_DRAIN);

    always_comb begin
        if (ws_state_next == S_CAPTURE) begin
            wr_row_next = ws_cnt_next[M_W-1:0];                          // 0..F-W-1
        end else if ((ws_state_next == S_OVERLAP) || (ws_state_next == S_DRAIN)) begin
            wr_row_next = (i_F - M_W'(W)) + ws_cnt_next[M_W-1:0];        // F-W..F-1
        end else begin
            wr_row_next = '0;
        end
    end

    assign wr_tile_next = wr_vld_next ? cur_tile_next[WR_TILE_W-1:0] : '0;

    // ── abuf addr gen ─────────────────────────────────────
    always_comb begin
        prop_next[0] = o_feed;
        for (int c = 1; c < AR; c = c + 1) prop_next[c] = prop[c - 1];
    end

    assign cap         = i_tile_num * i_F;
    assign page_offset = page ? LANE_ADDR_W'(PAGE_SPAN) : '0;

    always_comb begin
        for (int c = 0; c < AR; c = c + 1) begin
            rd_en_next[c]   = prop_next[c];
            rd_addr_next[c] = page_offset + rd_ptr[c];
            if (prop_next[c]) begin
                if (rd_ptr[c] + 1 >= cap) rd_ptr_next[c] = '0;
                else                       rd_ptr_next[c] = rd_ptr[c] + 1;
            end else begin
                rd_ptr_next[c] = rd_ptr[c];
            end
        end
    end

    assign page_next = page ^ i_switch_page;

    // ── commit ─────────────────────────────────────────
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            ws_state     <= S_IDLE;
            ws_cnt       <= '0;
            o_feed       <= 1'b0;
            for (int k = 0; k < AR; k = k + 1) begin
                switch_sr[k] <= 1'b0;
                o_b_sw[k]    <= 1'b0;
            end
            o_wr_row     <= '0;
            o_wr_tile    <= '0;
            o_wr_vld     <= 1'b0;
            cur_tile     <= '0;
            pending_tile <= '0;
            for (int c = 0; c < AR; c = c + 1) begin
                prop[c]      <= 1'b0;
                rd_ptr[c]    <= '0;
                o_rd_en[c]   <= 1'b0;
                o_rd_addr[c] <= '0;
            end
            page         <= 1'b0;
            start_prev   <= 1'b0;
            o_ws_state   <= S_IDLE;
        end else begin
            ws_state     <= ws_state_next;
            ws_cnt       <= ws_cnt_next;
            start_prev   <= i_start;
            o_feed       <= feed_next;
            for (int k = 0; k < AR; k = k + 1) begin
                switch_sr[k] <= switch_sr_next[k];
                o_b_sw[k]    <= b_sw_next[k];
            end
            o_wr_row     <= wr_row_next;
            o_wr_tile    <= wr_tile_next;
            o_wr_vld     <= wr_vld_next;
            cur_tile     <= cur_tile_next;
            pending_tile <= pending_tile_next;
            for (int c = 0; c < AR; c = c + 1) begin
                prop[c]      <= prop_next[c];
                rd_ptr[c]    <= i_switch_page ? '0 : rd_ptr_next[c];
                o_rd_en[c]   <= rd_en_next[c];
                o_rd_addr[c] <= rd_addr_next[c];
            end
            page         <= page_next;
            o_ws_state   <= ws_state_next;
        end
    end

endmodule
