`include "uvm_macros.svh"

package sab_pkg;
    import uvm_pkg::*;

    // config object
    `include "sab_cfg.sv"

    // transaction
    `include "agent/sab_seq_item.sv"

    // agent components
    `include "agent/sab_sqr.sv"
    `include "agent/sab_driver.sv"
    `include "agent/sab_monitor.sv"
    `include "agent/sab_agent.sv"

    // coverage
    `include "coverage/sab_coverage.sv"

    // sequences
    `include "sequence/sab_sequence.sv"

    // scoreboard
    `include "scoreboard/sab_scb.sv"

    // model (1-to-N address router)
    `include "rf_model/sab_model.sv"

    // memory responder
    `include "agent/sab_mem.sv"

    // env
    `include "sab_env.sv"

endpackage : sab_pkg
