`ifndef SAB_DRIVER_SV
`define SAB_DRIVER_SV

class sab_driver #(ADDR_W=32, DATA_W=32) extends uvm_driver#(sab_seq_item#(ADDR_W, DATA_W));
    virtual sab_if#(ADDR_W, DATA_W) u_vif;
    int unsigned trans_cnt = 0;
    `uvm_component_param_utils(sab_driver#(ADDR_W, DATA_W))

    function new (string name = "sab_driver", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    extern function void build_phase(uvm_phase phase);
    extern task run_phase(uvm_phase phase);
    extern task drive_item(input sab_seq_item#(ADDR_W, DATA_W) item);
endclass : sab_driver

function void sab_driver::build_phase(uvm_phase phase);
    super.build_phase(phase);
    if (!uvm_config_db#(virtual sab_if#(ADDR_W, DATA_W))::get(this, "", "u_vif", u_vif))
        `uvm_fatal("NO_VIF", "virtual interface not found")
    `uvm_info(get_type_name(), "u_vif connected", UVM_HIGH)
endfunction : build_phase

task sab_driver::run_phase(uvm_phase phase);
    sab_seq_item#(ADDR_W, DATA_W) item;

    @(posedge u_vif.clk);
    if (!u_vif.rst_n) begin
        `uvm_info(get_type_name(), "Waiting for reset release...", UVM_LOW)
        u_vif.sab_req_valid   <= 1'b0;
        u_vif.sab_req_wen     <= 1'b0;
        u_vif.sab_req_addr    <= '0;
        u_vif.sab_req_wdata   <= '0;
        u_vif.sab_resp_ready  <= 1'b1;
        @(posedge u_vif.rst_n);
        @(posedge u_vif.clk);
    end

    // Default to always-ready on response channel.
    u_vif.sab_resp_ready <= 1'b1;
    `uvm_info(get_type_name(), "Driver ready", UVM_LOW)
    forever begin
        seq_item_port.get_next_item(item);
        `uvm_info(get_type_name(), $sformatf("[DRV] trans[%0d] %s addr=0x%0h wdata=0x%0h",
            trans_cnt, item.rw ? "WR" : "RD", item.addr, item.wdata), UVM_MEDIUM)
        drive_item(item);
        seq_item_port.item_done();
        trans_cnt++;
    end
endtask : run_phase

task sab_driver::drive_item(input sab_seq_item#(ADDR_W, DATA_W) item);
    `uvm_info(get_type_name(), $sformatf("[DRV] trans detail:\n%s", item.sprint()), UVM_HIGH)

    // ------------------------------
    // Phase 1: request handshake
    // ------------------------------
    u_vif.sab_req_valid   <= 1'b1;
    u_vif.sab_req_wen     <= item.rw;
    u_vif.sab_req_addr    <= item.addr;
    u_vif.sab_req_wdata   <= item.wdata;

    // Keep request stable until accepted.
    do begin
        @(posedge u_vif.clk);
    end while (!(u_vif.sab_req_valid && u_vif.sab_req_ready));

    // Request transfer completed on this cycle; deassert for next cycle.
    u_vif.sab_req_valid <= 1'b0;

    // ------------------------------
    // Phase 2: response handshake
    // ------------------------------
    // Wait for response transfer (valid && ready). Response channel is
    // decoupled from request channel and can return after arbitrary latency.
    do begin
        @(posedge u_vif.clk);
    end while (!(u_vif.sab_resp_valid && u_vif.sab_resp_ready));

    // Capture read data only when response handshake succeeds.
    if (!item.rw) begin
        item.rdata = u_vif.sab_resp_rdata;
    end
    item.resp_err = u_vif.sab_resp_err;

    // Post-transaction idle gap.
    repeat (item.delay) @(posedge u_vif.clk);
endtask : drive_item

// ---------------------------------------------------------------------------
// Response modes (uncomment as needed)
// ---------------------------------------------------------------------------
// --- by reference (default above) ---
// driver and sequencer share the same object handle.
// item.rdata written in drive_item() is directly visible to the sequence.
// sequence side: use get_response(rsp) after finish_item() to sync.

// --- by value ---
// sab_seq_item#(ADDR_W, DATA_W) rsp;
// seq_item_port.get_next_item(item);
// drive_item(item);
// rsp = sab_seq_item#(ADDR_W,DATA_W)::type_id::create("rsp");
// rsp.copy(item);
// rsp.set_id_info(item);          // MUST: routes rsp to the correct sequence
// seq_item_port.item_done(rsp);   // method A: non-blocking, stored in sequencer FIFO
// seq_item_port.put_response(rsp);// method B: BLOCKING until sequence calls get_response()
// rsp_port.write(rsp);            // method C: broadcast — connect to scoreboard in agent

// --- pipelined bus (e.g. AXI outstanding transactions) ---
// seq_item_port.try_next_item(item);
// if (item != null) begin
//     fork drive_item(item); join_none
//     seq_item_port.item_done();
// end else begin
//     // no item: drive idle for one cycle
//     @(posedge u_vif.clk);
// end

`endif // SAB_DRIVER_SV
