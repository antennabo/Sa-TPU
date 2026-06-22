// activation_buf — M 路并行 lane buffer，模块内无控制状态，地址完全外部驱动。
//
// 模块本身不感知 page / tile / skew 等概念——只是 M 个独立的 RAM，per-lane 写口和读口完全分开。
// "双 page" 由外部用地址高位实现（外部 DEPTH 设两倍并把 page bit 拼在 wr_addr/rd_addr 高位）。
//
// 写口：per-lane (i_wr_en[c], i_wr_addr[c], i_wr_data[c])
// 读口：per-lane (i_rd_en[c], i_rd_addr[c]) → o_rd_data[c] / o_rd_vld[c]
//        sdpram REG_OUT=1：rd_data 寄存 1 拍输出，o_rd_vld = i_rd_en 同步打 1 拍
module activation_buf #(
    parameter int M       = 4,
    parameter int DEPTH   = 32,
    parameter int DATA_W  = 8,
    localparam int ADDR_W = $clog2(DEPTH)
) (
    input  logic              clk,
    input  logic              rst_n,

    input  logic              i_wr_en   [M],
    input  logic [ADDR_W-1:0] i_wr_addr [M],
    input  logic [DATA_W-1:0] i_wr_data [M],

    input  logic              i_rd_en   [M],
    input  logic [ADDR_W-1:0] i_rd_addr [M],
    output logic [DATA_W-1:0] o_rd_data [M],
    output logic              o_rd_vld  [M]
);

    genvar c;
    generate
        for (c = 0; c < M; c = c + 1) begin : g_lane
            sdpram #(
                .DATA_W     (DATA_W),
                .DEPTH      (DEPTH),
                .REG_OUT    (1'b0)
            ) u_mem (
                .clk        (clk),
                .wr_en      (i_wr_en[c]),
                .wr_be      (1'b1),
                .wr_addr    (i_wr_addr[c]),
                .wr_wdata   (i_wr_data[c]),
                .rd_en      (i_rd_en[c]),
                .rd_addr    (i_rd_addr[c]),
                .rd_rdata   (o_rd_data[c])
            );

            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    o_rd_vld[c] <= 1'b0;
                end else begin
                    o_rd_vld[c] <= i_rd_en[c];
                end
            end
        end
    endgenerate

endmodule
