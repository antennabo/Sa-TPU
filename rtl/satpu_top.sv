// satpu_top — TPU 顶层: 外部只暴露 clk / rst_n / SAB (Simple Access Bus) 总线
//
// satpu_cfg (rtl/satpu_cfg.sv, 由 rtl/cfg/satpu_cfg.yaml 生成) 收纳所有控制字段 +
// abuf 写口 + wfifo 写口 + accum 读口 + 状态轮询。本顶层只做:
//   1. 例化 satpu_cfg + 5 个数据面子模块 (controller_ws / activation_buf /
//      weight_fifo / systolic_array / accumulator)
//   2. ABUF 写口 demux: cfg_abuf_addr 高 clog2(N) 位=lane idx, 低 ACT_ADDR_W 位=byte → per-AR
//   3. WFIFO 写口 demux: cfg_wfifo_addr[clog2(N)-1:0]=col → per-AC (列满则丢弃; 软件查 STATUS[3])
//   4. ACCUM 读口 demux+mux: cfg_accum_addr 高 clog2(N) 位=col, 低 ACC_ADDR_W 位=行 → per-AC + 读回 mux
//   5. STATUS 拼接: {wfifo_full, ws_state} 送 cfg.tpu_status
//   6. START 上升沿 1 拍脉冲送 controller.i_start
//
// 详见 doc/satpu_cfg.md。
//
// 注意: accumulator sdpram REG_OUT 必须为 0 (组合读), 否则 ACCUM 读延迟与 cfg 时序不对齐。

module satpu_top #(
    parameter int N             = 8,                // SA 边长 (ROW=COL=N)
    parameter int A_W           = 8,
    parameter int B_W           = 8,
    parameter int OUT_W         = 32,
    parameter int WTILE_NUM_MAX = 4,
    parameter int ABUF_DEPTH    = 256,
    parameter int ACCUM_DEPTH   = 1024,
    parameter int WFIFO_DEPTH   = 16,
    parameter int LATENCY       = 2,
    parameter int CFG_ADDR_W    = 16,
    parameter int CFG_DATA_W    = 32,
    localparam int AR           = N,
    localparam int AC           = N,
    localparam int W            = AR + LATENCY,
    localparam int LANE_W       = (N > 1) ? $clog2(N) : 1,
    localparam int ACT_ADDR_W   = (ABUF_DEPTH > 1)  ? $clog2(ABUF_DEPTH)  : 1,
    localparam int ACC_ADDR_W   = (ACCUM_DEPTH > 1) ? $clog2(ACCUM_DEPTH) : 1,
    localparam int FEED_NUM_W   = ACT_ADDR_W + 1,
    localparam int WTILE_NUM_W  = (WTILE_NUM_MAX > 1) ? $clog2(WTILE_NUM_MAX + 1) : 1
)(
    input  logic                       clk,
    input  logic                       rst_n,

    // 唯一对外接口: SAB 总线 (映射到 satpu_cfg)
    input  logic                       sab_valid,
    input  logic                       sab_wen,
    input  logic [CFG_ADDR_W-1:0]      sab_addr,
    input  logic [CFG_DATA_W-1:0]      sab_wdata,
    output logic                       sab_ready,
    output logic [CFG_DATA_W-1:0]      sab_rdata
);

    // ── cfg 输出 ────────────────────────────────────────────
    logic                      cfg_start;
    logic                      cfg_activ_avail;
    logic [7:0]                cfg_wtile_num;   // 8-bit (max 128)
    logic [10:0]               cfg_act_staddr;  // 11-bit (ABUF_DEPTH=2048)
    logic [9:0]                cfg_acc_staddr;
    logic [11:0]               cfg_feed_num;    // 12-bit (ACT_ADDR_W+1)

    logic [13:0]               cfg_abuf_addr;   // alen=14 (LANE_W+ACT_ADDR_W = 3+11)
    logic [7:0]                cfg_abuf_wdata;
    logic                      cfg_abuf_wen;

    logic [2:0]                cfg_wfifo_addr;
    logic [7:0]                cfg_wfifo_wdata;
    logic                      cfg_wfifo_wen;

    logic [12:0]               cfg_accum_addr;
    logic                      cfg_accum_ren;
    logic [31:0]               cfg_accum_rdata;

    logic [3:0]                tpu_status;

    // ── controller 内部互连 ─────────────────────────────────
    logic                      ctrl_weight_sw [AR];
    logic                      ctrl_act_ren   [AR];
    logic [ACT_ADDR_W-1:0]     ctrl_act_raddr [AR];
    logic                      ctrl_acc_wen   [AC];
    logic [ACC_ADDR_W-1:0]     ctrl_acc_waddr [AC];
    logic                      ctrl_acc_accen [AC];
    logic                      ctrl_acc_outen [AC];
    logic                      ctrl_acc_ren   [AC];   // 占位, 本指令恒 0
    logic [ACC_ADDR_W-1:0]     ctrl_acc_raddr [AC];   // 占位
    logic [2:0]                ws_state;

    // ── 数据面互连 ──────────────────────────────────────────
    logic [A_W-1:0]            abuf_rd_data   [AR];
    logic                      abuf_rd_vld    [AR];

    logic                      wfifo_rdy      [AC];
    logic                      wfifo_vld      [AC];
    logic [B_W-1:0]            wfifo_data     [AC];

    logic [OUT_W-1:0]          sa_out         [AC];
    logic                      sa_out_vld     [AC];

    // abuf 写口 (cfg demux 后)
    logic                      abuf_wr_en     [AR];
    logic [ACT_ADDR_W-1:0]     abuf_wr_addr   [AR];
    logic [A_W-1:0]            abuf_wr_data   [AR];

    // wfifo 写口 (cfg demux 后) + 反压
    logic                      wfifo_wr_en    [AC];
    logic [B_W-1:0]            wfifo_wdata    [AC];
    logic                      wfifo_full     [AC];
    logic                      any_wfifo_full;

    // accum 读口 (cfg demux 后) + 读回 mux
    logic                      accum_rd_en    [AC];
    logic [ACC_ADDR_W-1:0]     accum_rd_addr  [AC];
    logic [OUT_W-1:0]          accum_rd_data  [AC];

    // weight_loaded = !|wfifo_rdy (与重构前一致)
    logic                      weight_loaded;

    // ── START 上升沿 → 1 拍脉冲 (doc §5.5) ──────────────────
    logic                      cfg_start_d;
    logic                      ctrl_i_start;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) cfg_start_d <= 1'b0;
        else        cfg_start_d <= cfg_start;
    end
    assign ctrl_i_start = cfg_start & ~cfg_start_d;

    // ── weight_loaded 自推 ──────────────────────────────────
    always_comb begin
        weight_loaded = 1'b1;
        for (int c = 0; c < AC; c = c + 1)
            if (wfifo_rdy[c]) weight_loaded = 1'b0;
    end

    // ── ABUF 写口 demux ─────────────────────────────────────
    always_comb begin
        for (int r = 0; r < AR; r = r + 1) begin
            abuf_wr_en[r]   = cfg_abuf_wen && (cfg_abuf_addr[ACT_ADDR_W +: LANE_W] == r[LANE_W-1:0]);
            abuf_wr_addr[r] = cfg_abuf_addr[ACT_ADDR_W-1:0];
            abuf_wr_data[r] = cfg_abuf_wdata;
        end
    end

    // ── WFIFO 写口 demux + any_full 拼 STATUS ────────────────
    always_comb begin
        any_wfifo_full = 1'b0;
        for (int c = 0; c < AC; c = c + 1)
            if (wfifo_full[c]) any_wfifo_full = 1'b1;

        for (int c = 0; c < AC; c = c + 1) begin
            wfifo_wr_en[c] = cfg_wfifo_wen
                          && (cfg_wfifo_addr == c[2:0])
                          && !wfifo_full[c];
            wfifo_wdata[c] = cfg_wfifo_wdata;
        end
    end

    // ── ACCUM 读口 demux + 读回 mux ──────────────────────────
    always_comb begin
        for (int c = 0; c < AC; c = c + 1) begin
            accum_rd_en[c]   = cfg_accum_ren && (cfg_accum_addr[ACC_ADDR_W +: LANE_W] == c[LANE_W-1:0]);
            accum_rd_addr[c] = cfg_accum_addr[ACC_ADDR_W-1:0];
        end
        cfg_accum_rdata = '0;
        for (int c = 0; c < AC; c = c + 1)
            if (cfg_accum_addr[ACC_ADDR_W +: LANE_W] == c[LANE_W-1:0]) cfg_accum_rdata = accum_rd_data[c];
    end

    // ── STATUS 拼接 (cfg src) ───────────────────────────────
    assign tpu_status = {any_wfifo_full, ws_state};

    // ── satpu_cfg ───────────────────────────────────────────
    satpu_cfg #(
        .ADDR_W             (CFG_ADDR_W),
        .DATA_W             (CFG_DATA_W)
    ) u_cfg (
        .clk                (clk),
        .rst_n              (rst_n),
        .sab_valid          (sab_valid),
        .sab_wen            (sab_wen),
        .sab_addr           (sab_addr),
        .sab_wdata          (sab_wdata),
        .sab_ready          (sab_ready),
        .sab_rdata          (sab_rdata),
        .start              (cfg_start),
        .activ_avail        (cfg_activ_avail),
        .wtile_num          (cfg_wtile_num),
        .act_staddr         (cfg_act_staddr),
        .acc_staddr         (cfg_acc_staddr),
        .feed_num           (cfg_feed_num),
        .tpu_status         (tpu_status),
        .wfifo_addr         (cfg_wfifo_addr),
        .wfifo_wdata        (cfg_wfifo_wdata),
        .wfifo_wen          (cfg_wfifo_wen),
        .abuf_addr          (cfg_abuf_addr),
        .abuf_wdata         (cfg_abuf_wdata),
        .abuf_wen           (cfg_abuf_wen),
        .accum_addr         (cfg_accum_addr),
        .accum_rdata        (cfg_accum_rdata),
        .accum_ren          (cfg_accum_ren)
    );

    // ── controller ──────────────────────────────────────────
    controller_ws #(
        .AR                 (AR),
        .AC                 (AC),
        .LATENCY            (LATENCY),
        .WTILE_NUM_MAX      (WTILE_NUM_MAX),
        .ACT_ADDR_W         (ACT_ADDR_W),
        .ACC_ADDR_W         (ACC_ADDR_W)
    ) u_ctrl (
        .clk                (clk),
        .rst_n              (rst_n),
        .i_start            (ctrl_i_start),
        .i_weight_loaded    (weight_loaded),
        .i_activ_available  (cfg_activ_avail),
        .i_wtile_num        (cfg_wtile_num[WTILE_NUM_W-1:0]),
        .i_act_staddr       (cfg_act_staddr[ACT_ADDR_W-1:0]),
        .i_acc_staddr       (cfg_acc_staddr[ACC_ADDR_W-1:0]),
        .i_feed_num         (cfg_feed_num[FEED_NUM_W-1:0]),
        .o_weight_sw        (ctrl_weight_sw),
        .o_acc_wen          (ctrl_acc_wen),
        .o_acc_waddr        (ctrl_acc_waddr),
        .o_acc_accen        (ctrl_acc_accen),
        .o_acc_outen        (ctrl_acc_outen),
        .o_acc_ren          (ctrl_acc_ren),
        .o_acc_raddr        (ctrl_acc_raddr),
        .o_act_ren          (ctrl_act_ren),
        .o_act_raddr        (ctrl_act_raddr),
        .o_ws_state         (ws_state)
    );

    // ── activation buf ──────────────────────────────────────
    activation_buf #(
        .M                  (AR),
        .DEPTH              (ABUF_DEPTH),
        .DATA_W             (A_W)
    ) u_abuf (
        .clk                (clk),
        .rst_n              (rst_n),
        .i_wr_en            (abuf_wr_en),
        .i_wr_addr          (abuf_wr_addr),
        .i_wr_data          (abuf_wr_data),
        .i_rd_en            (ctrl_act_ren),
        .i_rd_addr          (ctrl_act_raddr),
        .o_rd_data          (abuf_rd_data),
        .o_rd_vld           (abuf_rd_vld)
    );

    // ── weight fifo ─────────────────────────────────────────
    weight_fifo #(
        .AC                 (AC),
        .DEPTH              (WFIFO_DEPTH),
        .DATA_W             (B_W)
    ) u_wfifo (
        .clk                (clk),
        .rst_n              (rst_n),
        .i_wr_en            (wfifo_wr_en),
        .i_wdata            (wfifo_wdata),
        .o_full             (wfifo_full),
        .i_ready            (wfifo_rdy),
        .o_vld              (wfifo_vld),
        .o_data             (wfifo_data)
    );

    // ── systolic array ──────────────────────────────────────
    systolic_array #(
        .ROW_N              (AR),
        .COL_N              (AC),
        .A_W                (A_W),
        .B_W                (B_W),
        .OUT_W              (OUT_W)
    ) u_sa (
        .clk                (clk),
        .rst_n              (rst_n),
        .a_vld              (abuf_rd_vld),
        .a                  (abuf_rd_data),
        .b_sw               (ctrl_weight_sw),
        .b_rdy              (wfifo_rdy),
        .b_vld              (wfifo_vld),
        .b                  (wfifo_data),
        .out_vld            (sa_out_vld),
        .out                (sa_out)
    );

    // ── accumulator ─────────────────────────────────────────
    accumulator #(
        .AC                 (AC),
        .OUTPUT_W           (OUT_W),
        .ACC_ADDR_W         (ACC_ADDR_W)
    ) u_accum (
        .clk                (clk),
        .rst_n              (rst_n),
        .i_psum             (sa_out),
        .i_psum_vld         (sa_out_vld),
        .i_wr_vld           (ctrl_acc_wen),
        .i_wr_addr          (ctrl_acc_waddr),
        .i_acc_en           (ctrl_acc_accen),
        .i_out_en           (ctrl_acc_outen),
        .o_rlt_vld          (),
        .o_rlt              (),
        .i_rd_en            (accum_rd_en),
        .i_rd_addr          (accum_rd_addr),
        .o_rd_data          (accum_rd_data)
    );

endmodule
