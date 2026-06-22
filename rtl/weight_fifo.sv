// weight_fifo — WS 模式下的列向权重 fifo。
//
// 写口：DMA 同步广播一行 N 条权重 (i_wvalid + i_wdata[N]) → 各列同步入队。
// 读口：每列独立 valid/ready 反压（组合 peek + 同拍 pop），跟 sa 的 b/b_vld/b_rdy 对接：
//   o_vld[c]  = !empty[c] & i_ready[c]
//   o_data[c] = mem[rd_addr]（组合，REG_OUT=0）
// 跟 simulator/cycle/sim_model/commonfifo.py::CommonFIFO.ws_update 行为对齐。
//
// o_wfull = OR(per-lane full)；内部 wr 受 !any_full 门控保证多列同步写
// （即使外部 DMA 在 wfull=1 时仍送 i_wvalid，本拍也整体不写，保持各列对齐）。
module weight_fifo #(
    parameter int N      = 4,
    parameter int DEPTH  = 16,
    parameter int DATA_W = 8
) (
    input  logic              clk,
    input  logic              rst_n,
    // 写口
    input  logic              i_wvalid,
    input  logic [DATA_W-1:0] i_wdata [N],
    output logic              o_wfull,
    // 读口
    input  logic              i_ready  [N],
    output logic              o_vld    [N],
    output logic [DATA_W-1:0] o_data   [N]
);

    logic empty_lane [N];
    logic full_lane  [N];
    logic any_full;
    logic wr_eff;

    always_comb begin
        any_full = 1'b0;
        for (int i = 0; i < N; i = i + 1) begin
            if (full_lane[i]) any_full = 1'b1;
        end
    end
    assign o_wfull = any_full;
    assign wr_eff  = i_wvalid & !any_full;

    genvar c;
    generate
        for (c = 0; c < N; c = c + 1) begin : g_lane
            sync_fifo #(
                .DEPTH   (DEPTH),
                .DATA_W  (DATA_W),
                .REG_OUT (1'b0)
            ) u_fifo (
                .clk    (clk),
                .rst_n  (rst_n),
                .wr     (wr_eff),
                .din    (i_wdata[c]),
                .rd     (o_vld[c]),
                .dout   (o_data[c]),
                .full   (full_lane[c]),
                .empty  (empty_lane[c]),
                .afull  (),
                .aempty (),
                .cnt_out()
            );
            assign o_vld[c] = !empty_lane[c] & i_ready[c];
        end
    endgenerate

endmodule
