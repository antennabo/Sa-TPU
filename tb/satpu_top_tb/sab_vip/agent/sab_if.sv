`ifndef SAB_IF_SV
`define SAB_IF_SV

interface sab_if #(
    parameter ADDR_W = 32,
    parameter DATA_W = 32
)(
    input logic clk,
    input logic rst_n
);
    logic                   sab_req_valid;
    logic                   sab_req_ready;
    logic                   sab_req_wen;    // 1: write, 0: read
    logic [ADDR_W - 1 : 0]  sab_req_addr;
    logic [DATA_W - 1 : 0]  sab_req_wdata;
    logic                   sab_resp_valid;
    logic                   sab_resp_ready;
    logic                   sab_resp_err;
    logic [DATA_W - 1 : 0]  sab_resp_rdata;

    // modport master (
    //     output req, wen, addr, wdata,
    //     input  ack, rdata, err
    // );

    // modport slave (
    //     input  req, wen, addr, wdata,
    //     output ack, rdata, err
    // );

endinterface : sab_if

`endif // SAB_IF_SV