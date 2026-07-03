`timescale 1ns/1ps

import uvm_pkg::*;
`include "uvm_macros.svh"
import {{VIP_NAME}}_pkg::*;

module tb_top;

localparam int ADDR_W = {{ADDR_W_DEFAULT}};
localparam int DATA_W = {{DATA_W_DEFAULT}};

// -----------------------------------------------------------------------
// Clock & Reset
// -----------------------------------------------------------------------
logic clk;
logic rst_n;

initial clk = 1'b0;
always  #5 clk = ~clk;   // 100 MHz

initial begin
    rst_n = 1'b0;
    repeat (10) @(posedge clk);
    @(negedge clk);
    rst_n = 1'b1;
end

// -----------------------------------------------------------------------
// Interface
// -----------------------------------------------------------------------
{{VIP_NAME}}_if #(.ADDR_W(ADDR_W), .DATA_W(DATA_W)) master_if (
    .clk  (clk),
    .rst_n(rst_n)
);

// -----------------------------------------------------------------------
// TODO: instantiate DUT or stub slave here
// -----------------------------------------------------------------------

// -----------------------------------------------------------------------
// Waveform dump
// -----------------------------------------------------------------------
`ifdef DUMP_WAVE
initial begin
    $fsdbDumpfile("wave.fsdb");
    $fsdbDumpvars(0, tb_top);
end
`endif

// -----------------------------------------------------------------------
// UVM: config_db + run_test
// -----------------------------------------------------------------------
initial begin
    uvm_config_db #(virtual {{VIP_NAME}}_if#(ADDR_W, DATA_W))::set(
        null, "uvm_test_top.u_env.u_master.u_driver",  "d_vif", master_if);
    uvm_config_db #(virtual {{VIP_NAME}}_if#(ADDR_W, DATA_W))::set(
        null, "uvm_test_top.u_env.u_master.u_monitor", "u_vif", master_if);
    run_test();
end

endmodule
