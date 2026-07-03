`ifndef {{VIP_NAME_UPPER}}_SEQUENCE_SV
`define {{VIP_NAME_UPPER}}_SEQUENCE_SV

class {{VIP_NAME}}_sequence #(ADDR_W={{ADDR_W_DEFAULT}}, DATA_W={{DATA_W_DEFAULT}}) extends uvm_sequence#({{VIP_NAME}}_seq_item#(ADDR_W, DATA_W));
    `uvm_object_param_utils({{VIP_NAME}}_sequence#(ADDR_W, DATA_W))

    int unsigned num_trans = 1;

    function new (string name = "{{VIP_NAME}}_sequence");
        super.new(name);
    endfunction

    extern task body();
endclass : {{VIP_NAME}}_sequence

task {{VIP_NAME}}_sequence::body();
    {{VIP_NAME}}_seq_item#(ADDR_W, DATA_W) item;

    if (starting_phase != null)
        starting_phase.raise_objection(this);

    `uvm_info(get_type_name(), $sformatf("Sequence start: %0d transactions", num_trans), UVM_LOW)
    repeat (num_trans) begin
        item = {{VIP_NAME}}_seq_item#(ADDR_W, DATA_W)::type_id::create("item");
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

`endif // {{VIP_NAME_UPPER}}_SEQUENCE_SV
