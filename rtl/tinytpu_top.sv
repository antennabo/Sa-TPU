// tinytpu_top — WS MXU 顶层结构整合：controller_ws + activation_buf + weight_fifo + systolic_array + accumulator
//
// 数据通路：abuf → SA → accumulator。
// controller 给 SA 时序 (o_weight_sw / o_act_ren / o_act_raddr)，给 accumulator 写控制
// (o_acc_{wen,waddr,accen,outen}[AC]，已 per-column deskew，与 sa col c 的 psum 出口同节拍)。
//
// 指令字段（指令内常量）：i_wtile_num / i_act_staddr / i_acc_staddr / i_feed_num。
// 流控：i_start / i_activ_available。i_weight_loaded 由 wfifo 反压内部推 (!|wfifo_rdy)。
//
// accumulator 读口：本指令 controller 不驱动 (o_acc_ren/raddr 恒 0)，顶层 raw 直驱
// (i_accum_rd_en + i_accum_rd_addr 广播给 per-AC 读口；可后续扩 STORE 指令再切给 controller)。
//
// 时序对齐：activation_buf 用 sdpram REG_OUT=0 (组合读) + rd_vld FF (i_rd_en 同步打 1 拍)，
// 对齐 FPGA BRAM 的隐含 1 拍读延迟，直接喂 SA 的 a / a_vld。
//
// weight_loaded 信号内部推：!|wfifo_rdy (所有列 SA shadow 都拒收 = reserve 全满 = 已 loaded)。
module tinytpu_top #(
    parameter int N             = 8,                // SA 边长（ROW=COL=N）
    parameter int A_W           = 8,
    parameter int B_W           = 8,
    parameter int OUT_W         = 32,
    parameter int WTILE_NUM_MAX = 4,
    parameter int ABUF_DEPTH    = 256,
    parameter int ACCUM_DEPTH   = 1024,
    parameter int WFIFO_DEPTH   = 16,
    parameter int LATENCY       = 2,
    localparam int AR           = N,
    localparam int AC           = N,
    localparam int W            = AR + LATENCY,
    localparam int ACT_ADDR_W   = (ABUF_DEPTH > 1)  ? $clog2(ABUF_DEPTH)  : 1,
    localparam int ACC_ADDR_W   = (ACCUM_DEPTH > 1) ? $clog2(ACCUM_DEPTH) : 1,
    localparam int FEED_NUM_W   = ACT_ADDR_W + 1,
    localparam int WTILE_NUM_W  = (WTILE_NUM_MAX > 1) ? $clog2(WTILE_NUM_MAX + 1) : 1
)(
    input  logic                      clk,
    input  logic                      rst_n,

    // ── controller 上层指令字段 + 流控 ───────────────────
    input  logic                      i_start,
    input  logic                      i_activ_available,
    input  logic [WTILE_NUM_W-1:0]    i_wtile_num,
    input  logic [ACT_ADDR_W-1:0]     i_act_staddr,
    input  logic [ACC_ADDR_W-1:0]     i_acc_staddr,
    input  logic [FEED_NUM_W-1:0]     i_feed_num,

    // ── activation_buf 写口 (raw) ────────────────────────
    input  logic                      i_abuf_wr_en   [AR],
    input  logic [ACT_ADDR_W-1:0]     i_abuf_wr_addr [AR],
    input  logic [A_W-1:0]            i_abuf_wr_data [AR],

    // ── weight_fifo 写口 (raw, 同步广播) ────────────────
    input  logic                      i_wfifo_wvalid,
    input  logic [B_W-1:0]            i_wfifo_wdata  [AC],
    output logic                      o_wfifo_full,

    // ── accumulator 读口 (raw, 广播到 per-AC) ───────────
    input  logic                      i_accum_rd_en,
    input  logic [ACC_ADDR_W-1:0]     i_accum_rd_addr,
    output logic [OUT_W-1:0]          o_accum_rd_data [AC],

    // ── 观察 ─────────────────────────────────────────────
    output logic [2:0]                o_ws_state
);

    // ── 内部互连 ─────────────────────────────────────────
    logic                      ctrl_weight_sw [AR];
    logic                      ctrl_act_ren   [AR];
    logic [ACT_ADDR_W-1:0]     ctrl_act_raddr [AR];

    logic                      ctrl_acc_wen   [AC];
    logic [ACC_ADDR_W-1:0]     ctrl_acc_waddr [AC];
    logic                      ctrl_acc_accen [AC];
    logic                      ctrl_acc_outen [AC];
    // controller 占位读 (恒 0, 暂不用)
    logic                      ctrl_acc_ren   [AC];
    logic [ACC_ADDR_W-1:0]     ctrl_acc_raddr [AC];

    logic [A_W-1:0]            abuf_rd_data   [AR];
    logic                      abuf_rd_vld    [AR];

    logic                      wfifo_rdy      [AC];
    logic                      wfifo_vld      [AC];
    logic [B_W-1:0]            wfifo_data     [AC];

    logic                      sa_out_vld     [AC];
    logic [OUT_W-1:0]          sa_out         [AC];

    // weight_loaded：!|wfifo_rdy
    logic                      weight_loaded;
    always_comb begin
        weight_loaded = 1'b1;
        for (int c = 0; c < AC; c = c + 1) begin
            if (wfifo_rdy[c]) weight_loaded = 1'b0;
        end
    end

    // accumulator 读口：顶层 scalar 广播到 per-AC
    logic                      accum_rd_en   [AC];
    logic [ACC_ADDR_W-1:0]     accum_rd_addr [AC];
    always_comb begin
        for (int c = 0; c < AC; c = c + 1) begin
            accum_rd_en[c]   = i_accum_rd_en;
            accum_rd_addr[c] = i_accum_rd_addr;
        end
    end

    // ── controller ───────────────────────────────────────
    controller_ws #(
        .AR             (AR),
        .AC             (AC),
        .LATENCY        (LATENCY),
        .WTILE_NUM_MAX  (WTILE_NUM_MAX),
        .ACT_ADDR_W     (ACT_ADDR_W),
        .ACC_ADDR_W     (ACC_ADDR_W)
    ) u_ctrl (
        .clk                (clk),
        .rst_n              (rst_n),
        .i_start            (i_start),
        .i_weight_loaded    (weight_loaded),
        .i_activ_available  (i_activ_available),
        .i_wtile_num        (i_wtile_num),
        .i_act_staddr       (i_act_staddr),
        .i_acc_staddr       (i_acc_staddr),
        .i_feed_num         (i_feed_num),
        .o_weight_sw        (ctrl_weight_sw),
        .o_acc_wen          (ctrl_acc_wen),
        .o_acc_waddr        (ctrl_acc_waddr),
        .o_acc_accen        (ctrl_acc_accen),
        .o_acc_outen        (ctrl_acc_outen),
        .o_acc_ren          (ctrl_acc_ren),
        .o_acc_raddr        (ctrl_acc_raddr),
        .o_act_ren          (ctrl_act_ren),
        .o_act_raddr        (ctrl_act_raddr),
        .o_ws_state         (o_ws_state)
    );

    // ── activation buf ───────────────────────────────────
    activation_buf #(
        .M      (AR),
        .DEPTH  (ABUF_DEPTH),
        .DATA_W (A_W)
    ) u_abuf (
        .clk       (clk),
        .rst_n     (rst_n),
        .i_wr_en   (i_abuf_wr_en),
        .i_wr_addr (i_abuf_wr_addr),
        .i_wr_data (i_abuf_wr_data),
        .i_rd_en   (ctrl_act_ren),
        .i_rd_addr (ctrl_act_raddr),
        .o_rd_data (abuf_rd_data),
        .o_rd_vld  (abuf_rd_vld)
    );

    // ── weight fifo ──────────────────────────────────────
    // 顶层 DMA 写口仍是 scalar 广播 (i_wfifo_wvalid + i_wfifo_wdata[AC])，
    // 新 weight_fifo 是 per-column (i_wr_en[AC] + o_full[AC])。
    // 这里把 wvalid 广播给 wr_en[c]，并用 any_full 反压保持多列写入对齐。
    logic                      wfifo_wr_en   [AC];
    logic                      wfifo_full    [AC];
    logic                      any_wfifo_full;
    always_comb begin
        any_wfifo_full = 1'b0;
        for (int c = 0; c < AC; c = c + 1) begin
            if (wfifo_full[c]) any_wfifo_full = 1'b1;
        end
        for (int c = 0; c < AC; c = c + 1) begin
            wfifo_wr_en[c] = i_wfifo_wvalid & !any_wfifo_full;
        end
    end
    assign o_wfifo_full = any_wfifo_full;

    weight_fifo #(
        .AC     (AC),
        .DEPTH  (WFIFO_DEPTH),
        .DATA_W (B_W)
    ) u_wfifo (
        .clk      (clk),
        .rst_n    (rst_n),
        .i_wr_en  (wfifo_wr_en),
        .i_wdata  (i_wfifo_wdata),
        .o_full   (wfifo_full),
        .i_ready  (wfifo_rdy),
        .o_vld    (wfifo_vld),
        .o_data   (wfifo_data)
    );

    // ── systolic array ───────────────────────────────────
    systolic_array #(
        .ROW_N (AR),
        .COL_N (AC),
        .A_W   (A_W),
        .B_W   (B_W),
        .OUT_W (OUT_W)
    ) u_sa (
        .clk     (clk),
        .rst_n   (rst_n),
        .a_vld   (abuf_rd_vld),
        .a       (abuf_rd_data),
        .b_sw    (ctrl_weight_sw),
        .b_rdy   (wfifo_rdy),
        .b_vld   (wfifo_vld),
        .b       (wfifo_data),
        .out_vld (sa_out_vld),
        .out     (sa_out)
    );

    // ── accumulator ──────────────────────────────────────
    accumulator #(
        .AC         (AC),
        .OUTPUT_W   (OUT_W),
        .ACC_ADDR_W (ACC_ADDR_W)
    ) u_accum (
        .clk         (clk),
        .rst_n       (rst_n),
        .i_psum      (sa_out),
        .i_psum_vld  (sa_out_vld),
        .i_wr_vld    (ctrl_acc_wen),
        .i_wr_addr   (ctrl_acc_waddr),
        .i_acc_en    (ctrl_acc_accen),
        .i_out_en    (ctrl_acc_outen),
        .o_rlt_vld   (),
        .o_rlt       (),
        .i_rd_en     (accum_rd_en),
        .i_rd_addr   (accum_rd_addr),
        .o_rd_data   (o_accum_rd_data)
    );

endmodule
