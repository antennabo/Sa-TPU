`ifndef SAB_MONITOR_SV
`define SAB_MONITOR_SV

class sab_monitor #(ADDR_W=32, DATA_W=32) extends uvm_monitor;
    virtual sab_if#(ADDR_W, DATA_W)               u_vif;
    uvm_analysis_port#(sab_seq_item#(ADDR_W, DATA_W)) ap;
    `uvm_component_param_utils(sab_monitor#(ADDR_W, DATA_W))

    function new (string name = "sab_monitor", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    extern function void build_phase(uvm_phase phase);
    extern task run_phase(uvm_phase phase);
    extern task collect_item(output sab_seq_item#(ADDR_W, DATA_W) item);
endclass : sab_monitor

function void sab_monitor::build_phase(uvm_phase phase);
    super.build_phase(phase);
    if (!uvm_config_db#(virtual sab_if#(ADDR_W, DATA_W))::get(this, "", "u_vif", u_vif))
        `uvm_fatal("NO_VIF", "virtual interface not found")
    `uvm_info(get_type_name(), "u_vif connected", UVM_HIGH)
    ap = new("ap", this);
endfunction : build_phase

task sab_monitor::run_phase(uvm_phase phase);
    sab_seq_item#(ADDR_W, DATA_W) req_item;
    sab_seq_item#(ADDR_W, DATA_W) rsp_item;
    sab_seq_item#(ADDR_W, DATA_W) pending_q[$];
    forever begin
        @(posedge u_vif.clk);
        if (!u_vif.rst_n) begin
            pending_q.delete();
            continue;
        end

        // Capture request on request-channel handshake.
        if (u_vif.sab_req_valid && u_vif.sab_req_ready) begin
            req_item = sab_seq_item#(ADDR_W, DATA_W)::type_id::create("req_item");
            req_item.rw   = u_vif.sab_req_wen;
            req_item.addr = u_vif.sab_req_addr;
            if (u_vif.sab_req_wen) begin
                req_item.wdata = u_vif.sab_req_wdata;
            end
            pending_q.push_back(req_item);
        end

        // Match response in-order on response-channel handshake.
        if (u_vif.sab_resp_valid && u_vif.sab_resp_ready) begin
            if (pending_q.size() == 0) begin
                `uvm_error(get_type_name(), "[MON] response handshake seen with empty pending queue")
            end else begin
                rsp_item = pending_q.pop_front();
                if (!rsp_item.rw) begin
                    rsp_item.rdata = u_vif.sab_resp_rdata;
                end
                rsp_item.resp_err = u_vif.sab_resp_err;
                `uvm_info(get_type_name(), $sformatf("[MON] %s addr=0x%0h %s=0x%0h",
                    rsp_item.rw ? "WR" : "RD", rsp_item.addr,
                    rsp_item.rw ? "wdata" : "rdata",
                    rsp_item.rw ? rsp_item.wdata : rsp_item.rdata), UVM_MEDIUM)
                `uvm_info(get_type_name(), $sformatf("[MON] trans detail:\n%s", rsp_item.sprint()), UVM_HIGH)
                ap.write(rsp_item);
            end
        end
    end
endtask : run_phase

task sab_monitor::collect_item(output sab_seq_item#(ADDR_W, DATA_W) item);
    // Legacy API kept for compatibility; collection now handled in run_phase.
    item = null;
endtask : collect_item

`endif // SAB_MONITOR_SV
