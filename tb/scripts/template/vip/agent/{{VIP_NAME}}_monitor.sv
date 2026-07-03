`ifndef {{VIP_NAME_UPPER}}_MONITOR_SV
`define {{VIP_NAME_UPPER}}_MONITOR_SV

class {{VIP_NAME}}_monitor #(ADDR_W={{ADDR_W_DEFAULT}}, DATA_W={{DATA_W_DEFAULT}}) extends uvm_monitor;
    virtual {{VIP_NAME}}_if#(ADDR_W, DATA_W)               u_vif;
    uvm_analysis_port#({{VIP_NAME}}_seq_item#(ADDR_W, DATA_W)) ap;
    `uvm_component_param_utils({{VIP_NAME}}_monitor#(ADDR_W, DATA_W))

    function new (string name = "{{VIP_NAME}}_monitor", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    extern function void build_phase(uvm_phase phase);
    extern task run_phase(uvm_phase phase);
    extern task collect_item(output {{VIP_NAME}}_seq_item#(ADDR_W, DATA_W) item);
endclass : {{VIP_NAME}}_monitor

function void {{VIP_NAME}}_monitor::build_phase(uvm_phase phase);
    super.build_phase(phase);
    if (!uvm_config_db#(virtual {{VIP_NAME}}_if#(ADDR_W, DATA_W))::get(this, "", "u_vif", u_vif))
        `uvm_fatal("NO_VIF", "virtual interface not found")
    `uvm_info(get_type_name(), "u_vif connected", UVM_HIGH)
    ap = new("ap", this);
endfunction : build_phase

task {{VIP_NAME}}_monitor::run_phase(uvm_phase phase);
    {{VIP_NAME}}_seq_item#(ADDR_W, DATA_W) item;
    forever begin
        collect_item(item);
        `uvm_info(get_type_name(), $sformatf("[MON] %s addr=0x%0h %s=0x%0h",
            item.rw ? "WR" : "RD", item.addr,
            item.rw ? "wdata" : "rdata",
            item.rw ? item.wdata : item.rdata), UVM_MEDIUM)
        ap.write(item);
    end
endtask : run_phase

task {{VIP_NAME}}_monitor::collect_item(output {{VIP_NAME}}_seq_item#(ADDR_W, DATA_W) item);
    item = {{VIP_NAME}}_seq_item#(ADDR_W, DATA_W)::type_id::create("item");
    @(posedge u_vif.clk iff (u_vif.req && !u_vif.ack));
    item.rw   = u_vif.wen;
    item.addr = u_vif.addr;
    if (u_vif.wen) item.wdata = u_vif.wdata;
    @(posedge u_vif.clk iff u_vif.ack);
    if (!u_vif.wen) item.rdata = u_vif.rdata;
    `uvm_info(get_type_name(), $sformatf("[MON] trans detail:\n%s", item.sprint()), UVM_HIGH)
endtask : collect_item

`endif // {{VIP_NAME_UPPER}}_MONITOR_SV
