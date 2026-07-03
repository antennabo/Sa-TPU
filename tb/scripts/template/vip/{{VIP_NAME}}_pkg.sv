`include "uvm_macros.svh"

package {{VIP_NAME}}_pkg;
    import uvm_pkg::*;

    // config object
    `include "{{VIP_NAME}}_cfg.sv"

    // transaction
    `include "agent/{{VIP_NAME}}_seq_item.sv"

    // agent components
    `include "agent/{{VIP_NAME}}_sqr.sv"
    `include "agent/{{VIP_NAME}}_driver.sv"
    `include "agent/{{VIP_NAME}}_monitor.sv"
    `include "agent/{{VIP_NAME}}_agent.sv"

    // coverage
    `include "coverage/{{VIP_NAME}}_coverage.sv"

    // sequences
    `include "sequence/{{VIP_NAME}}_sequence.sv"

    // env
    `include "{{VIP_NAME}}_env.sv"

endpackage : {{VIP_NAME}}_pkg
