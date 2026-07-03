package sab_tb_pkg;
    import uvm_pkg::*;
    import sab_pkg::*;
    import satpu_cfg_addr_pkg::*;
    `include "uvm_macros.svh"

    `include "../../rtl/cfg/satpu_cfg_ral.sv"
    `include "ral/sab_reg_adapter.sv"

    `include "env/sab_tb_env.sv"
    `include "testcases/sab_base_test.sv"
    `include "testcases/sab_smoke_test.sv"
    `include "testcases/ral_smoke_test.sv"
    `include "testcases/ral_all_regs_test.sv"
    `include "testcases/e2e_minimal_test.sv"

endpackage : sab_tb_pkg
