`ifndef SAB_AGENT_SV
`define SAB_AGENT_SV

class sab_agent #(int ADDR_W=32, int DATA_W=32) extends uvm_agent;
    `uvm_component_param_utils(sab_agent#(ADDR_W, DATA_W))

    sab_sqr#(ADDR_W, DATA_W)     u_sequencer;
    sab_driver#(ADDR_W, DATA_W)  u_driver;
    sab_monitor#(ADDR_W, DATA_W) u_monitor;

    function new (string name = "sab_agent", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    extern virtual function void build_phase(uvm_phase phase);
    extern virtual function void connect_phase(uvm_phase phase);
endclass : sab_agent

function void sab_agent::build_phase(uvm_phase phase);
    super.build_phase(phase);
    u_monitor = sab_monitor#(ADDR_W, DATA_W)::type_id::create("u_monitor", this);
    if (is_active == UVM_ACTIVE) begin
        u_sequencer = sab_sqr#(ADDR_W, DATA_W)::type_id::create("u_sequencer", this);
        u_driver    = sab_driver#(ADDR_W, DATA_W)::type_id::create("u_driver", this);
    end
endfunction : build_phase

function void sab_agent::connect_phase(uvm_phase phase);
    if (is_active == UVM_ACTIVE)
        u_driver.seq_item_port.connect(u_sequencer.seq_item_export);
endfunction : connect_phase

`endif // SAB_AGENT_SV
