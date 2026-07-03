// SAB VIP
-F ${SAB_VIP_HOME}/sab_vip.f

// RTL common
../../rtl/common/dff_rst_en.sv
../../rtl/common/dff_rst_en_clr.sv
../../rtl/common/data_buf.sv
../../rtl/common/weight_buf.sv
../../rtl/common/sdpram.sv
../../rtl/common/sync_fifo.sv
../../rtl/common/pingpong_buf.sv

// RTL cfg (yaml-generated: pkg first, then module)
../../rtl/cfg/satpu_cfg_addr_pkg.sv
../../rtl/cfg/satpu_cfg.sv

// RTL DUT
../../rtl/pe.sv
../../rtl/systolic_array.sv
../../rtl/activation_buf.sv
../../rtl/weight_fifo.sv
../../rtl/accumulator.sv
../../rtl/controller_ws.sv
../../rtl/satpu_top.sv

// Testbench
sab_tb_pkg.sv
tb_top.sv
