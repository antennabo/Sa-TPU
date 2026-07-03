`ifndef SAB_ENV_SV
`define SAB_ENV_SV

class sab_env #(ADDR_W=32, DATA_W=32) extends uvm_env;
    `uvm_component_param_utils(sab_env#(ADDR_W, DATA_W))

    sab_agent#(ADDR_W, DATA_W)                      u_master;
    sab_agent#(ADDR_W, DATA_W)                      u_slave;
    sab_coverage#(ADDR_W, DATA_W)                   u_coverage;
    sab_model#(ADDR_W, DATA_W, ADDR_W, 1)          u_model;
    sab_scb#(ADDR_W, DATA_W)                        u_scb;
    uvm_tlm_analysis_fifo#(sab_seq_item#(ADDR_W, DATA_W)) mon2mdl_fifo;
    uvm_tlm_analysis_fifo#(sab_seq_item#(ADDR_W, DATA_W)) mon2scb_fifo;
    uvm_tlm_analysis_fifo#(sab_seq_item#(ADDR_W, DATA_W)) mdl2scb_fifo;
    sab_cfg                                         cfg;

    function new (string name = "sab_env", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    extern virtual function void build_phase(uvm_phase phase);
    extern virtual function void connect_phase(uvm_phase phase);
endclass : sab_env

function void sab_env::build_phase(uvm_phase phase);
    super.build_phase(phase);
    if (!uvm_config_db#(sab_cfg)::get(this, "", "cfg", cfg)) begin
        `uvm_info(get_type_name(), "cfg not found, using defaults", UVM_HIGH)
        cfg = sab_cfg::type_id::create("cfg");
    end
    u_master                   = sab_agent#(ADDR_W, DATA_W)::type_id::create("u_master", this);
    u_slave                    = sab_agent#(ADDR_W, DATA_W)::type_id::create("u_slave", this);
    u_coverage                 = sab_coverage#(ADDR_W, DATA_W)::type_id::create("u_coverage", this);
    u_model                    = sab_model#(ADDR_W, DATA_W, ADDR_W, 1)::type_id::create("u_model", this);
    u_scb                      = sab_scb#(ADDR_W, DATA_W)::type_id::create("u_scb", this);
    mon2mdl_fifo               = new("mon2mdl_fifo", this);
    mon2scb_fifo               = new("mon2scb_fifo", this);
    mdl2scb_fifo               = new("mdl2scb_fifo", this);
    u_master.is_active         = cfg.master_is_active;
    u_slave.is_active          = cfg.slave_is_active;
    u_coverage.coverage_enable = cfg.coverage_enable;
endfunction : build_phase

function void sab_env::connect_phase(uvm_phase phase);
    // Monitor stream fanout: coverage + expected path(model) + actual path(scoreboard)
    u_master.u_monitor.ap.connect(u_coverage.analysis_export);
    u_master.u_monitor.ap.connect(mon2mdl_fifo.analysis_export);
    u_slave.u_monitor.ap.connect(mon2scb_fifo.analysis_export);

    // Expected path: monitor -> model -> scb.exp_port
    u_model.port.connect(mon2mdl_fifo.blocking_get_export);
    u_model.ap[0].connect(mdl2scb_fifo.analysis_export);
    u_scb.exp_port.connect(mdl2scb_fifo.blocking_get_export);

    // Actual path: monitor -> scb.act_port
    u_scb.act_port.connect(mon2scb_fifo.blocking_get_export);
endfunction : connect_phase

`endif // SAB_ENV_SV
