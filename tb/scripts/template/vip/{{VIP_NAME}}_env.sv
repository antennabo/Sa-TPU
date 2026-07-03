`ifndef {{VIP_NAME_UPPER}}_ENV_SV
`define {{VIP_NAME_UPPER}}_ENV_SV

class {{VIP_NAME}}_env #(ADDR_W={{ADDR_W_DEFAULT}}, DATA_W={{DATA_W_DEFAULT}}) extends uvm_env;
    `uvm_component_param_utils({{VIP_NAME}}_env#(ADDR_W, DATA_W))

    {{VIP_NAME}}_agent#(ADDR_W, DATA_W)    u_master;
    {{VIP_NAME}}_coverage#(ADDR_W, DATA_W) u_coverage;
    {{VIP_NAME}}_cfg                       cfg;

    function new (string name = "{{VIP_NAME}}_env", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    extern virtual function void build_phase(uvm_phase phase);
    extern virtual function void connect_phase(uvm_phase phase);
endclass : {{VIP_NAME}}_env

function void {{VIP_NAME}}_env::build_phase(uvm_phase phase);
    super.build_phase(phase);
    if (!uvm_config_db#({{VIP_NAME}}_cfg)::get(this, "", "cfg", cfg)) begin
        `uvm_info(get_type_name(), "cfg not found, using defaults", UVM_HIGH)
        cfg = {{VIP_NAME}}_cfg::type_id::create("cfg");
    end
    u_master               = {{VIP_NAME}}_agent#(ADDR_W, DATA_W)::type_id::create("u_master", this);
    u_coverage             = {{VIP_NAME}}_coverage#(ADDR_W, DATA_W)::type_id::create("u_coverage", this);
    u_master.is_active     = cfg.master_is_active;
    u_coverage.coverage_enable = cfg.coverage_enable;
endfunction : build_phase

function void {{VIP_NAME}}_env::connect_phase(uvm_phase phase);
    u_master.u_monitor.ap.connect(u_coverage.analysis_export);
endfunction : connect_phase

`endif // {{VIP_NAME_UPPER}}_ENV_SV
