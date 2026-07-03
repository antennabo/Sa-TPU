`ifndef {{VIP_NAME_UPPER}}_AGENT_SV
`define {{VIP_NAME_UPPER}}_AGENT_SV

class {{VIP_NAME}}_agent #(int ADDR_W={{ADDR_W_DEFAULT}}, int DATA_W={{DATA_W_DEFAULT}}) extends uvm_agent;
    `uvm_component_param_utils({{VIP_NAME}}_agent#(ADDR_W, DATA_W))

    {{VIP_NAME}}_sqr#(ADDR_W, DATA_W)     u_sequencer;
    {{VIP_NAME}}_driver#(ADDR_W, DATA_W)  u_driver;
    {{VIP_NAME}}_monitor#(ADDR_W, DATA_W) u_monitor;

    function new (string name = "{{VIP_NAME}}_agent", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    extern virtual function void build_phase(uvm_phase phase);
    extern virtual function void connect_phase(uvm_phase phase);
endclass : {{VIP_NAME}}_agent

function void {{VIP_NAME}}_agent::build_phase(uvm_phase phase);
    super.build_phase(phase);
    u_monitor = {{VIP_NAME}}_monitor#(ADDR_W, DATA_W)::type_id::create("u_monitor", this);
    if (is_active == UVM_ACTIVE) begin
        u_sequencer = {{VIP_NAME}}_sqr#(ADDR_W, DATA_W)::type_id::create("u_sequencer", this);
        u_driver    = {{VIP_NAME}}_driver#(ADDR_W, DATA_W)::type_id::create("u_driver", this);
    end
endfunction : build_phase

function void {{VIP_NAME}}_agent::connect_phase(uvm_phase phase);
    if (is_active == UVM_ACTIVE)
        u_driver.seq_item_port.connect(u_sequencer.seq_item_export);
endfunction : connect_phase

`endif // {{VIP_NAME_UPPER}}_AGENT_SV
