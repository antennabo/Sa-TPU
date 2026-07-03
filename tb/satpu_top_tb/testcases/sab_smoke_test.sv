`ifndef SAB_SMOKE_TEST_SV
`define SAB_SMOKE_TEST_SV

class sab_smoke_seq extends sab_sequence#(15, 32);
    `uvm_object_utils(sab_smoke_seq)

    function new (string name = "sab_smoke_seq");
        super.new(name);
    endfunction

    task body();
        REQ item;   // REQ resolves to the VIP's seq_item type via uvm_sequence parameterization
        `uvm_info(get_type_name(), "Smoke seq start", UVM_LOW)
        repeat (num_trans) begin
            item = REQ::type_id::create("item");
            start_item(item);
            if (!item.randomize() with { delay_kind inside {sab_pkg::ZERO, sab_pkg::SHORT}; })
                `uvm_fatal("RAND_FAIL", "randomization failed")
            finish_item(item);
            `uvm_info(get_type_name(), item.sprint(), UVM_LOW)
        end
        `uvm_info(get_type_name(), "Smoke seq done", UVM_LOW)
    endtask : body
endclass : sab_smoke_seq

class sab_smoke_test extends sab_base_test;
    `uvm_component_utils(sab_smoke_test)

    function new (string name = "sab_smoke_test", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    task run_phase(uvm_phase phase);
        sab_smoke_seq seq;
        phase.raise_objection(this);
        `uvm_info(get_type_name(), "sab smoke test start", UVM_LOW)
        seq = sab_smoke_seq::type_id::create("seq");
        seq.num_trans = 20;
        #1000
        seq.start(u_env.u_master.u_sequencer);
        #1000
        `uvm_info(get_type_name(), "sab smoke test done", UVM_LOW)
        phase.drop_objection(this);
    endtask : run_phase
endclass : sab_smoke_test

`endif // SAB_SMOKE_TEST_SV
