// fifo
//

module sync_fifo #(
    parameter DEPTH                 = 32,
    parameter DATA_W                = 32,
    parameter bit REG_OUT           = 1'b1,
    parameter AFULL_TH              = 1,    // afull when cnt >= DEPTH - AFULL_TH
    parameter AEMPTY_TH             = 1,    // aempty when cnt <= AEMPTY_TH
    localparam DEPTH_W              = $clog2(DEPTH)
) (
    input                           clk,
    input                           rst_n,

    // ── upstream ─────────────────────────────────────────────
    input                           wr,
    input  [DATA_W-1:0]             din,

    // ── downstream ───────────────────────────────────────────
    input                           rd,
    output [DATA_W-1:0]             dout,
    output                          full,
    output                          empty,
    output                          afull,
    output                          aempty,
    output [DEPTH_W:0]              cnt_out
);

localparam BYTE_EN_W = DATA_W / 8;
// ── internal signals ─────────────────────────────────────────────────────
logic [DEPTH_W:0]   wr_addr;
logic [DEPTH_W:0]   rd_addr;
logic [DEPTH_W:0]   cnt;
logic               wr_real;
logic               rd_real;

// ── logic ─────────────────────────────────────────────────────────────────
assign wr_real = wr && !full;
assign rd_real = rd && !empty;

always_ff @(posedge clk or negedge rst_n) begin : wr_addr_ff
    if (!rst_n) begin
        wr_addr <= '0;
    end else if (wr_real) begin
        wr_addr <= wr_addr + 'd1;
    end
end

always_ff @(posedge clk or negedge rst_n) begin : rd_addr_ff
    if (!rst_n) begin
        rd_addr <= '0;
    end else if(rd_real) begin
        rd_addr <= rd_addr + 'd1;
    end
end

assign full   = (wr_addr[DEPTH_W] != rd_addr[DEPTH_W]) &&
                (wr_addr[DEPTH_W-1:0] == rd_addr[DEPTH_W-1:0]);
assign empty  = (wr_addr == rd_addr);
assign cnt     = wr_addr - rd_addr;
assign cnt_out = cnt;
assign afull  = cnt >= (DEPTH - AFULL_TH);
assign aempty = cnt <= AEMPTY_TH;

sdpram #(
    .DATA_W  (DATA_W),
    .DEPTH   (DEPTH),
    .REG_OUT (REG_OUT)
) u_data_ram (
    .clk      (clk),
    .wr_en    (wr_real),
    .wr_be    ({BYTE_EN_W{1'b1}}),
    .wr_addr  (wr_addr[DEPTH_W-1:0]),
    .wr_wdata (din),

    .rd_en    (rd_real),
    .rd_addr  (rd_addr[DEPTH_W-1:0]),
    .rd_rdata (dout)
);

endmodule
