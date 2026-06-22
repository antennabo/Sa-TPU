// tinytpu_top — WS MXU 顶层结构整合：controller_ws + activation_buf + weight_fifo + systolic_array
//
// 不含 accumulator。SA 输出 (o_out / o_out_vld) 和 controller 给 accumulator 的标量
// (o_wr_row / o_wr_tile / o_wr_vld) 直接 expose 到 port，由外部消费。
// controller 内部已经把 wr_* 信号延迟 CAP_DELAY 拍对齐到 col 0 psum 到达时刻。
//
// 时序对齐：activation_buf 用 sdpram REG_OUT=0（组合读）+ rd_vld FF（i_rd_en 同步打 1 拍），
// 对齐 FPGA BRAM 的隐含 1 拍读延迟，直接喂 SA 的 a / a_vld。
//
// weight_loaded 信号内部推：!|wfifo_rdy（所有列 SA shadow 都拒收 = reserve 全满 = 已 loaded）。
//
// 写口完全 raw 暴露：abuf 写口 per-lane、weight_fifo 写口同步广播。
module tinytpu_top #(
    parameter int N             = 8,                // SA 边长（ROW=COL=N）
    parameter int F_MAX         = 16,               // 支持的最大 M 维（编译期常数，用于位宽）
    parameter int A_W           = 8,
    parameter int B_W           = 8,
    parameter int OUT_W         = 32,
    parameter int TILE_NUM_MAX  = 4,
    parameter int K_ABUF_MAX    = F_MAX,
    parameter int TAG_W         = 4,
    parameter int WFIFO_DEPTH   = 16,
    parameter int LATENCY       = 2,
    localparam int AR           = N,
    localparam int AC           = N,
    localparam int M_W          = $clog2(F_MAX + 1),
    localparam int TILE_NUM_W   = $clog2(TILE_NUM_MAX + 1),
    localparam int PAGE_SPAN    = K_ABUF_MAX * TILE_NUM_MAX,
    localparam int ABUF_DEPTH   = 2 * PAGE_SPAN,
    localparam int LANE_ADDR_W  = $clog2(ABUF_DEPTH),
    localparam int WR_TILE_W    = TAG_W
)(
    input  logic                      clk,
    input  logic                      rst_n,

    // ── controller 上层握手 ──────────────────────────────
    input  logic                      i_start,              // 启动脉冲 (上升沿 IDLE→WLOAD)
    input  logic                      i_activ_available,
    input  logic                      i_switch_weight,
    input  logic [TAG_W-1:0]          i_tag,
    input  logic [TILE_NUM_W-1:0]     i_tile_num,
    input  logic                      i_switch_page,
    input  logic [M_W-1:0]            i_F,                  // 当前作业 M 维（运行时）

    // ── activation_buf 写口（raw）────────────────────────
    input  logic                      i_abuf_wr_en   [AR],
    input  logic [LANE_ADDR_W-1:0]    i_abuf_wr_addr [AR],
    input  logic [A_W-1:0]            i_abuf_wr_data [AR],

    // ── weight_fifo 写口（raw, 同步广播）─────────────────
    input  logic                      i_wfifo_wvalid,
    input  logic [B_W-1:0]            i_wfifo_wdata  [AC],
    output logic                      o_wfifo_full,

    // ── SA 计算输出 ──────────────────────────────────────
    output logic                      o_out_vld      [AC],
    output logic [OUT_W-1:0]          o_out          [AC],

    // ── 给 accumulator 的捕获标量（已 CAP_DELAY 对齐到 col 0 psum 到达）──────
    output logic [M_W-1:0]            o_wr_row,
    output logic [WR_TILE_W-1:0]      o_wr_tile,
    output logic                      o_wr_vld,

    // ── 观察 ─────────────────────────────────────────────
    output logic                      o_feed,
    output logic [2:0]                o_ws_state
);

    // ── 内部互连 ─────────────────────────────────────────
    logic                      ctrl_b_sw     [AR];
    logic                      ctrl_rd_en    [AR];
    logic [LANE_ADDR_W-1:0]    ctrl_rd_addr  [AR];

    logic [A_W-1:0]            abuf_rd_data  [AR];
    logic                      abuf_rd_vld   [AR];

    logic                      wfifo_rdy     [AC];
    logic                      wfifo_vld     [AC];
    logic [B_W-1:0]            wfifo_data    [AC];

    // weight_loaded：!|wfifo_rdy 等价于「所有列 reserve 满，可切 active」
    logic                      weight_loaded;
    always_comb begin
        weight_loaded = 1'b1;
        for (int c = 0; c < AC; c = c + 1) begin
            if (wfifo_rdy[c]) weight_loaded = 1'b0;
        end
    end

    // ── controller ───────────────────────────────────────
    controller_ws #(
        .AR           (AR),
        .AC           (AC),
        .LATENCY      (LATENCY),
        .TILE_NUM_MAX (TILE_NUM_MAX),
        .K_ABUF_MAX   (K_ABUF_MAX),
        .F_MAX        (F_MAX),
        .TAG_W        (TAG_W)
    ) u_ctrl (
        .clk                (clk),
        .rst_n              (rst_n),
        .i_start            (i_start),
        .i_weight_loaded    (weight_loaded),
        .i_activ_available  (i_activ_available),
        .i_switch_weight    (i_switch_weight),
        .i_tag              (i_tag),
        .i_tile_num         (i_tile_num),
        .i_switch_page      (i_switch_page),
        .i_F                (i_F),
        .o_feed             (o_feed),
        .o_b_sw             (ctrl_b_sw),
        .o_wr_row           (o_wr_row),
        .o_wr_tile          (o_wr_tile),
        .o_wr_vld           (o_wr_vld),
        .o_rd_en            (ctrl_rd_en),
        .o_rd_addr          (ctrl_rd_addr),
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
        .i_rd_en   (ctrl_rd_en),
        .i_rd_addr (ctrl_rd_addr),
        .o_rd_data (abuf_rd_data),
        .o_rd_vld  (abuf_rd_vld)
    );

    // ── weight fifo ──────────────────────────────────────
    weight_fifo #(
        .N      (AC),
        .DEPTH  (WFIFO_DEPTH),
        .DATA_W (B_W)
    ) u_wfifo (
        .clk      (clk),
        .rst_n    (rst_n),
        .i_wvalid (i_wfifo_wvalid),
        .i_wdata  (i_wfifo_wdata),
        .o_wfull  (o_wfifo_full),
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
        .b_sw    (ctrl_b_sw),
        .b_rdy   (wfifo_rdy),
        .b_vld   (wfifo_vld),
        .b       (wfifo_data),
        .out_vld (o_out_vld),
        .out     (o_out)
    );

endmodule
