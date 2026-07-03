`ifndef SAB_SVA_SV
`define SAB_SVA_SV

`include "uvm_macros.svh"

module sab_sva
    import uvm_pkg::*;
    #(
    parameter ADDR_W = 32,
    parameter DATA_W = 32
)(
    input logic                     clk,
    input logic                     rst_n,
    input logic                     sab_req_valid,
    input logic                     sab_req_ready,
    input logic                     sab_req_wen,
    input logic [ADDR_W-1:0]        sab_req_addr,
    input logic [DATA_W-1:0]        sab_req_wdata,
    input logic                     sab_resp_valid,
    input logic                     sab_resp_ready,
    input logic                     sab_resp_err,
    input logic [DATA_W-1:0]        sab_resp_rdata
);

    // -----------------------------------------------------------------------
    // X-state checks: no signal may be X/Z during active operation
    // -----------------------------------------------------------------------
    property no_x_on_req_valid;
        @(posedge clk) disable iff (!rst_n)
        !$isunknown(sab_req_valid);
    endproperty

    property no_x_on_req_ready;
        @(posedge clk) disable iff (!rst_n)
        !$isunknown(sab_req_ready);
    endproperty

    property no_x_on_resp_valid;
        @(posedge clk) disable iff (!rst_n)
        !$isunknown(sab_resp_valid);
    endproperty

    property no_x_on_resp_ready;
        @(posedge clk) disable iff (!rst_n)
        !$isunknown(sab_resp_ready);
    endproperty

    property no_x_on_addr_when_req;
        @(posedge clk) disable iff (!rst_n)
        sab_req_valid |-> !$isunknown(sab_req_addr);
    endproperty

    property no_x_on_wen_when_req;
        @(posedge clk) disable iff (!rst_n)
        sab_req_valid |-> !$isunknown(sab_req_wen);
    endproperty

    property no_x_on_wdata_when_req;
        @(posedge clk) disable iff (!rst_n)
        (sab_req_valid && sab_req_wen) |-> !$isunknown(sab_req_wdata);
    endproperty

    property no_x_on_resp_err_when_resp_valid;
        @(posedge clk) disable iff (!rst_n)
        sab_resp_valid |-> !$isunknown(sab_resp_err);
    endproperty

    property no_x_on_rdata_when_success_read_resp;
        @(posedge clk) disable iff (!rst_n)
        (sab_resp_valid && !sab_resp_err) |-> !$isunknown(sab_resp_rdata);
    endproperty

    // -----------------------------------------------------------------------
    // Stability checks under backpressure
    // -----------------------------------------------------------------------
    property req_payload_stable_when_wait;
        @(posedge clk) disable iff (!rst_n)
        (sab_req_valid && !sab_req_ready) |=> (sab_req_valid &&
                                               $stable(sab_req_wen) &&
                                               $stable(sab_req_addr) &&
                                               $stable(sab_req_wdata));
    endproperty

    property resp_payload_stable_when_wait;
        @(posedge clk) disable iff (!rst_n)
        (sab_resp_valid && !sab_resp_ready) |=> (sab_resp_valid &&
                                                 $stable(sab_resp_err) &&
                                                 $stable(sab_resp_rdata));
    endproperty

    assert property (no_x_on_req_valid)      else `uvm_error("SVA_X", "sab_req_valid is X/Z")
    assert property (no_x_on_req_ready)      else `uvm_error("SVA_X", "sab_req_ready is X/Z")
    assert property (no_x_on_resp_valid)     else `uvm_error("SVA_X", "sab_resp_valid is X/Z")
    assert property (no_x_on_resp_ready)     else `uvm_error("SVA_X", "sab_resp_ready is X/Z")
    assert property (no_x_on_addr_when_req)  else `uvm_error("SVA_X", "addr is X/Z while req")
    assert property (no_x_on_wen_when_req)   else `uvm_error("SVA_X", "wen is X/Z while req")
    assert property (no_x_on_wdata_when_req) else `uvm_error("SVA_X", "wdata is X/Z while req && wen")
    assert property (no_x_on_resp_err_when_resp_valid) else `uvm_error("SVA_X", "sab_resp_err is X/Z while resp_valid")
    assert property (no_x_on_rdata_when_success_read_resp) else `uvm_error("SVA_X", "sab_resp_rdata is X/Z on successful response")
    assert property (req_payload_stable_when_wait) else `uvm_error("SVA", "request payload changed while req_valid && !req_ready")
    assert property (resp_payload_stable_when_wait) else `uvm_error("SVA", "response payload changed while resp_valid && !resp_ready")

endmodule : sab_sva

`endif // SAB_SVA_SV