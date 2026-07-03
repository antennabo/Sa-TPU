package {{VIP_NAME}}_tb_pkg;
    import uvm_pkg::*;
    import {{VIP_NAME}}_pkg::*;
    `include "uvm_macros.svh"

    `include "env/{{VIP_NAME}}_tb_env.sv"
    `include "testcases/{{VIP_NAME}}_base_test.sv"
    `include "testcases/{{VIP_NAME}}_smoke_test.sv"

endpackage : {{VIP_NAME}}_tb_pkg
