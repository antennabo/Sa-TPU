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
    parameter int AC            = 8,
    parameter int DEPTH         = 1024,
    parameter int DATA_W        = 8
) (
    input  logic              clk,
    input  logic              rst_n,
    // 写口
    input  logic              i_wr_en   [AC],
    input  logic [DATA_W-1:0] i_wdata   [AC],
    output logic              o_full    [AC],
    // 读口
    input  logic              i_ready   [AC],
    output logic              o_vld     [AC],
    output logic [DATA_W-1:0] o_data    [AC]
);

    logic                       wr_en     [AC];
    logic                       rden      [AC];
    logic                       empty_lane[AC];
    logic   [DATA_W-1:0]        fifo_data [AC];
    logic                       wr_buf     [AC];
    logic                       rden_r     [AC];
    logic                       buf_vld    [AC];
    logic   [DATA_W-1:0]        buf_data [AC];
    genvar c;
    generate
        for (c = 0; c < AC; c = c + 1) begin : g_lane

            assign wr_en[c] = i_wr_en[c] & (!o_full[c]);

            sync_fifo #(
                .DEPTH   (DEPTH),
                .DATA_W  (DATA_W),
                .REG_OUT (1'b0)
            ) u_fifo (
                .clk    (clk),
                .rst_n  (rst_n),
                .wr     (wr_en[c]),
                .din    (i_wdata[c]),
                .rd     (rden[c]),
                .dout   (fifo_data[c]),
                .full   (o_full[c]),
                .empty  (empty_lane[c]),
                .afull  (),
                .aempty (),
                .cnt_out()
            );
            assign rden[c] = !empty_lane[c] & i_ready[c];
            assign wr_buf[c] = !i_ready[c]&rden_r[c];
            data_buf #(
                .DATA_W (DATA_W)
            ) u_data_buf (
                .clk        (clk),
                .rst_n      (rst_n),

                .i_in_valid (wr_buf[c]),
                .o_in_ready (),
                .i_in_data  (fifo_data[c]),

                .o_out_valid(buf_vld[c]),
                .i_out_ready(i_ready[c]),
                .o_out_data (buf_data[c]),

                .i_clr      (1'b0)
            );

            always_ff@(posedge clk or negedge rst_n)begin
                if(!rst_n)begin
                    rden_r[c] <= 1'b0;
                end else begin
                    rden_r[c] <= rden[c];
                end
            end

            assign o_vld[c]  = buf_vld[c]|(i_ready[c]&rden_r[c]);
            assign o_data[c] = (buf_vld[c])? buf_data[c]: fifo_data[c];
        end
    endgenerate

endmodule
