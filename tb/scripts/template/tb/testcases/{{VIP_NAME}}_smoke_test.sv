`ifndef {{VIP_NAME_UPPER}}_SMOKE_TEST_SV
`define {{VIP_NAME_UPPER}}_SMOKE_TEST_SV

class {{VIP_NAME}}_smoke_seq extends {{VIP_NAME}}_sequence#({{ADDR_W_DEFAULT}}, {{DATA_W_DEFAULT}});
    `uvm_object_utils({{VIP_NAME}}_smoke_seq)

    function new (string name = "{{VIP_NAME}}_smoke_seq");
        super.new(name);
    endfunction

    task body();
        REQ item;   // REQ resolves to the VIP's seq_item type via uvm_sequence parameterization
        `uvm_info(get_type_name(), "Smoke seq start", UVM_LOW)
        repeat (num_trans) begin
            item = REQ::type_id::create("item");
            start_item(item);
            if (!item.randomize() with { delay_kind inside {{{VIP_NAME}}_pkg::ZERO, {{VIP_NAME}}_pkg::SHORT}; })
                `uvm_fatal("RAND_FAIL", "randomization failed")
            finish_item(item);
            `uvm_info(get_type_name(), item.sprint(), UVM_LOW)
        end
        `uvm_info(get_type_name(), "Smoke seq done", UVM_LOW)
    endtask : body
endclass : {{VIP_NAME}}_smoke_seq

class {{VIP_NAME}}_smoke_test extends {{VIP_NAME}}_base_test;
    `uvm_component_utils({{VIP_NAME}}_smoke_test)

    function new (string name = "{{VIP_NAME}}_smoke_test", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    task run_phase(uvm_phase phase);
        {{VIP_NAME}}_smoke_seq seq;
        phase.raise_objection(this);
        `uvm_info(get_type_name(), "{{VIP_NAME}} smoke test start", UVM_LOW)
        seq = {{VIP_NAME}}_smoke_seq::type_id::create("seq");
        seq.num_trans = 20;
        #1000
        seq.start(u_env.u_master.u_sequencer);
        #1000
        `uvm_info(get_type_name(), "{{VIP_NAME}} smoke test done", UVM_LOW)
        phase.drop_objection(this);
    endtask : run_phase
endclass : {{VIP_NAME}}_smoke_test

`endif // {{VIP_NAME_UPPER}}_SMOKE_TEST_SV
