// tb_top — UVM top for satpu_top (DUT 唯一对外是 SAB 总线).
//
// 总线 wiring:
//   master_if 是双通道带握手的 SAB (req + resp), satpu_top 只有单 sab_ready/sab_rdata.
//   - req:  master_if.sab_req_* → DUT.sab_*, req_ready ← DUT.sab_ready
//   - resp: resp_valid ← sab_ready 延 1 拍 (规避 driver 同拍 resp 卡死),
//           resp_rdata ← DUT.sab_rdata (reg 输出, 下一拍仍 hold),
//           resp_err   = 0 (DUT 无 err 通道).
//
// 维度由 +define+SATPU_{N,LATENCY,WTILE_NUM_MAX,ABUF_DEPTH,ACCUM_DEPTH,WFIFO_DEPTH} 注入.
// 参照 uvm-platform/testbench/mmio 的 pattern: 参数化 interface + SVA 直接例化.

`timescale 1ns/1ps

import uvm_pkg::*;
`include "uvm_macros.svh"
import sab_pkg::*;

`ifndef SATPU_N
  `define SATPU_N 8
`endif
`ifndef SATPU_LATENCY
  `define SATPU_LATENCY 2
`endif
`ifndef SATPU_WTILE_NUM_MAX
  `define SATPU_WTILE_NUM_MAX 128
`endif
`ifndef SATPU_ABUF_DEPTH
  `define SATPU_ABUF_DEPTH 2048
`endif
`ifndef SATPU_ACCUM_DEPTH
  `define SATPU_ACCUM_DEPTH 1024
`endif
`ifndef SATPU_WFIFO_DEPTH
  `define SATPU_WFIFO_DEPTH 1024
`endif

module tb_top;

localparam int N             = `SATPU_N;
localparam int A_W           = 8;
localparam int B_W           = 8;
localparam int OUT_W         = 32;
localparam int LATENCY       = `SATPU_LATENCY;
localparam int WTILE_NUM_MAX = `SATPU_WTILE_NUM_MAX;
localparam int ABUF_DEPTH    = `SATPU_ABUF_DEPTH;
localparam int ACCUM_DEPTH   = `SATPU_ACCUM_DEPTH;
localparam int WFIFO_DEPTH   = `SATPU_WFIFO_DEPTH;
localparam int ADDR_W        = 16;   // satpu_cfg 地址空间: 16-bit (64K)
localparam int DATA_W        = 32;

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
sab_if #(.ADDR_W(ADDR_W), .DATA_W(DATA_W)) master_if (.clk(clk), .rst_n(rst_n));

// -----------------------------------------------------------------------
// DUT instance
// -----------------------------------------------------------------------
logic                  dut_sab_ready;
logic [DATA_W-1:0]     dut_sab_rdata;

satpu_top #(
    .N             (N),
    .A_W           (A_W),
    .B_W           (B_W),
    .OUT_W         (OUT_W),
    .WTILE_NUM_MAX (WTILE_NUM_MAX),
    .ABUF_DEPTH    (ABUF_DEPTH),
    .ACCUM_DEPTH   (ACCUM_DEPTH),
    .WFIFO_DEPTH   (WFIFO_DEPTH),
    .LATENCY       (LATENCY),
    .CFG_ADDR_W    (ADDR_W),
    .CFG_DATA_W    (DATA_W)
) dut (
    .clk        (clk),
    .rst_n      (rst_n),
    .sab_valid  (master_if.sab_req_valid),
    .sab_wen    (master_if.sab_req_wen),
    .sab_addr   (master_if.sab_req_addr),
    .sab_wdata  (master_if.sab_req_wdata),
    .sab_ready  (dut_sab_ready),
    .sab_rdata  (dut_sab_rdata)
);

// -----------------------------------------------------------------------
// SAB single-channel → split-channel bridge
//   req_ready : 同拍 sab_ready
//   resp_valid: sab_ready 延 1 拍 (driver 的 resp wait 不能吃同拍)
//   resp_rdata: 直连 sab_rdata (寄存器输出, 下一拍仍 hold)
//   resp_err  : 0 (DUT 无错误通道)
// -----------------------------------------------------------------------
logic dut_sab_ready_d;
always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) dut_sab_ready_d <= 1'b0;
    else        dut_sab_ready_d <= dut_sab_ready;
end

assign master_if.sab_req_ready  = dut_sab_ready;
assign master_if.sab_resp_valid = dut_sab_ready_d;
assign master_if.sab_resp_err   = 1'b0;
assign master_if.sab_resp_rdata = dut_sab_rdata;

// -----------------------------------------------------------------------
// SVA: sab_sva 直接例化 (VCS 不允许 bind module 到 interface, 跟 mmio 一致)
// -----------------------------------------------------------------------
sab_sva #(.ADDR_W(ADDR_W), .DATA_W(DATA_W)) u_sva (
    .clk            (clk),
    .rst_n          (rst_n),
    .sab_req_valid  (master_if.sab_req_valid),
    .sab_req_ready  (master_if.sab_req_ready),
    .sab_req_wen    (master_if.sab_req_wen),
    .sab_req_addr   (master_if.sab_req_addr),
    .sab_req_wdata  (master_if.sab_req_wdata),
    .sab_resp_valid (master_if.sab_resp_valid),
    .sab_resp_ready (master_if.sab_resp_ready),
    .sab_resp_err   (master_if.sab_resp_err),
    .sab_resp_rdata (master_if.sab_resp_rdata)
);

// -----------------------------------------------------------------------
// Waveform dump
// -----------------------------------------------------------------------
`ifdef DUMP_WAVE
initial begin
    $fsdbDumpfile("wave.fsdb");
    $fsdbDumpvars(0, tb_top, "+all", "+mda", "+packedmda", "+struct");
end
`endif

// -----------------------------------------------------------------------
// UVM: config_db + run_test
// -----------------------------------------------------------------------
initial begin
    uvm_config_db #(virtual sab_if#(ADDR_W, DATA_W))::set(
        null, "uvm_test_top.u_env.u_master.u_driver",  "u_vif", master_if);
    uvm_config_db #(virtual sab_if#(ADDR_W, DATA_W))::set(
        null, "uvm_test_top.u_env.u_master.u_monitor", "u_vif", master_if);
    run_test();
end

endmodule