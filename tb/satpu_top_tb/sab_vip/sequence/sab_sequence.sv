`ifndef SAB_SEQUENCE_SV
`define SAB_SEQUENCE_SV

class sab_sequence #(ADDR_W=32, DATA_W=32) extends uvm_sequence#(sab_seq_item#(ADDR_W, DATA_W));
    `uvm_object_param_utils(sab_sequence#(ADDR_W, DATA_W))

    int unsigned num_trans = 1;

    function new (string name = "sab_sequence");
        super.new(name);
    endfunction

    extern task body();
endclass : sab_sequence

task sab_sequence::body();
    sab_seq_item#(ADDR_W, DATA_W) item;

    if (starting_phase != null)
        starting_phase.raise_objection(this);

    `uvm_info(get_type_name(), $sformatf("Sequence start: %0d transactions", num_trans), UVM_LOW)
    repeat (num_trans) begin
        item = sab_seq_item#(ADDR_W, DATA_W)::type_id::create("item");
        start_item(item);
        if (!item.randomize())
            `uvm_fatal("RAND_FAIL", "randomization failed")
        finish_item(item);
        `uvm_info(get_type_name(), $sformatf("[%s] addr=0x%0h wdata=0x%0h delay=%0d",
            item.rw ? "WR" : "RD", item.addr, item.wdata, item.delay), UVM_LOW)
    end
    `uvm_info(get_type_name(), "Sequence done", UVM_LOW)

    if (starting_phase != null)
        starting_phase.drop_objection(this);
endtask : body

`endif // SAB_SEQUENCE_SV
