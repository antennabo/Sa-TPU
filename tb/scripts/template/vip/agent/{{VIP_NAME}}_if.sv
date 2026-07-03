`ifndef {{VIP_NAME_UPPER}}_IF_SV
`define {{VIP_NAME_UPPER}}_IF_SV

interface {{VIP_NAME}}_if #(
    parameter DATA_W = {{DATA_W_DEFAULT}},
    parameter ADDR_W = {{ADDR_W_DEFAULT}}
)(
    input logic clk,
    input logic rst_n
);
    logic                   req;
    logic                   ack;
    logic                   wen;    // 1: write, 0: read
    logic [ADDR_W - 1 : 0]  addr;
    logic [DATA_W - 1 : 0]  wdata;
    logic [DATA_W - 1 : 0]  rdata;
    logic                   err;

    modport master (
        output req, wen, addr, wdata,
        input  ack, rdata, err
    );

    modport slave (
        input  req, wen, addr, wdata,
        output ack, rdata, err
    );

endinterface : {{VIP_NAME}}_if

`endif // {{VIP_NAME_UPPER}}_IF_SV
