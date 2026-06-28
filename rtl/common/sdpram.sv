// sdpram
// Simple dual-port RAM interface.
//
// One write port and one read port share the same storage array.
//
// Standardized RAM parameters:
//   DATA_W    : data width in bits
//   DEPTH     : number of entries
//   ADDR_W    : address width, normally derived from DEPTH
//   BYTE_EN_W : byte-enable width, normally DATA_W / 8
//
// Write port convention:
//   wr_en    : write-port enable
//   wr_be    : byte write enable
//   wr_addr  : write address
//   wr_wdata : write data
//
// Read port convention:
//   rd_en    : read-port enable
//   rd_addr  : read address
//   rd_rdata : read data

module sdpram #(
    parameter int DATA_W    = 32,
    parameter int DEPTH     = 1024,
    parameter int ADDR_W    = (DEPTH <= 1) ? 1 : $clog2(DEPTH),
    parameter int BYTE_EN_W = DATA_W / 8,
    parameter INIT_FILE = "",
    parameter bit REG_OUT = 1'b0
)(
    input  logic                    clk,

    // Write port
    input  logic                    wr_en,
    input  logic [BYTE_EN_W-1:0]    wr_be,
    input  logic [ADDR_W-1:0]       wr_addr,
    input  logic [DATA_W-1:0]       wr_wdata,

    // Read port
    input  logic                    rd_en,
    input  logic [ADDR_W-1:0]       rd_addr,
    output logic [DATA_W-1:0]       rd_rdata
);

logic [DATA_W-1:0] mem [0:DEPTH-1];

initial begin
    if (DEPTH <= 0) begin
        $error("sdpram: DEPTH must be greater than 0");
    end
    if ((DATA_W % 8) != 0) begin
        $error("sdpram: DATA_W must be a multiple of 8");
    end
    if (BYTE_EN_W != (DATA_W / 8)) begin
        $error("sdpram: BYTE_EN_W must match DATA_W / 8");
    end
    if (INIT_FILE == "") begin
        for (int idx = 0; idx < DEPTH; idx++) begin
            mem[idx] = '0;
        end
    end else begin
        $readmemh(INIT_FILE, mem);
    end
end

always @(posedge clk) begin
    if (wr_en) begin
        for (int lane = 0; lane < BYTE_EN_W; lane++) begin
            if (wr_be[lane]) begin
                mem[wr_addr][8*lane +: 8] <= wr_wdata[8*lane +: 8];
            end
        end
    end
end

logic [DATA_W-1:0] rd_rdata_r;

always_ff @(posedge clk) begin
    if((wr_addr==rd_addr)&&wr_en)begin
        rd_rdata_r <= wr_wdata;
    end else begin
        rd_rdata_r <= mem[rd_addr];
    end

end

generate
    if (REG_OUT) begin : g_reg_out
        logic [DATA_W-1:0] rd_rdata_rr;
        always_ff @(posedge clk) begin
            rd_rdata_rr <= rd_rdata_r;
        end
        assign rd_rdata = rd_rdata_rr;
    end else begin : g_no_reg_out
        assign rd_rdata = rd_rdata_r;
    end
endgenerate

endmodule
