`ifndef {{VIP_NAME_UPPER}}_DRIVER_SV
`define {{VIP_NAME_UPPER}}_DRIVER_SV

class {{VIP_NAME}}_driver #(ADDR_W={{ADDR_W_DEFAULT}}, DATA_W={{DATA_W_DEFAULT}}) extends uvm_driver#({{VIP_NAME}}_seq_item#(ADDR_W, DATA_W));
    virtual {{VIP_NAME}}_if#(ADDR_W, DATA_W) d_vif;
    int unsigned trans_cnt = 0;
    `uvm_component_param_utils({{VIP_NAME}}_driver#(ADDR_W, DATA_W))

    function new (string name = "{{VIP_NAME}}_driver", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    extern function void build_phase(uvm_phase phase);
    extern task run_phase(uvm_phase phase);
    extern task drive_item(input {{VIP_NAME}}_seq_item#(ADDR_W, DATA_W) item);
endclass : {{VIP_NAME}}_driver

function void {{VIP_NAME}}_driver::build_phase(uvm_phase phase);
    super.build_phase(phase);
    if (!uvm_config_db#(virtual {{VIP_NAME}}_if#(ADDR_W, DATA_W))::get(this, "", "d_vif", d_vif))
        `uvm_fatal("NO_VIF", "virtual interface not found")
    `uvm_info(get_type_name(), "d_vif connected", UVM_HIGH)
endfunction : build_phase

task {{VIP_NAME}}_driver::run_phase(uvm_phase phase);
    {{VIP_NAME}}_seq_item#(ADDR_W, DATA_W) item;

    // Idle until reset deasserts
    // TODO: drive protocol-specific idle signals here
    @(posedge d_vif.rst_n);

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

task {{VIP_NAME}}_driver::drive_item(input {{VIP_NAME}}_seq_item#(ADDR_W, DATA_W) item);
    `uvm_info(get_type_name(), $sformatf("[DRV] trans detail:\n%s", item.sprint()), UVM_HIGH)

    // Drive request via NBA (caller must be at a posedge boundary).
    // For back-to-back (delay=0): req<=1 and the previous req<=0 land in
    // the same NBA region — last-wins keeps req high with updated signals.
    d_vif.req   <= 1'b1;
    d_vif.wen   <= item.rw;
    d_vif.addr  <= item.addr;
    d_vif.wdata <= item.wdata;

    // Hold req until ack, then deassert
    @(posedge d_vif.ack);
    if (!item.rw)
        item.rdata = d_vif.rdata;   // capture read data (by-reference, visible to sequence)
    d_vif.req <= 1'b0;

    // Post-transaction idle gap.
    // delay=0: fall through immediately so the next drive_item call (if any)
    // runs in the same timestep and its req<=1 overwrites the req<=0 above.
    repeat (item.delay) @(posedge d_vif.clk);
endtask : drive_item

// ---------------------------------------------------------------------------
// Response modes (uncomment as needed)
// ---------------------------------------------------------------------------
// --- by reference (default above) ---
// driver and sequencer share the same object handle.
// item.rdata written in drive_item() is directly visible to the sequence.
// sequence side: use get_response(rsp) after finish_item() to sync.

// --- by value ---
// {{VIP_NAME}}_seq_item#(ADDR_W, DATA_W) rsp;
// seq_item_port.get_next_item(item);
// drive_item(item);
// rsp = {{VIP_NAME}}_seq_item#(ADDR_W,DATA_W)::type_id::create("rsp");
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
//     @(posedge d_vif.clk);
// end

`endif // {{VIP_NAME_UPPER}}_DRIVER_SV
