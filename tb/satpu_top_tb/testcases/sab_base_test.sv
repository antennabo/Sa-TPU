`ifndef SAB_BASE_TEST_SV
`define SAB_BASE_TEST_SV

class sab_base_test extends uvm_test;
    `uvm_component_utils(sab_base_test)

    sab_tb_env #(.ADDR_W(16), .DATA_W(32)) u_env;

    function new (string name = "sab_base_test", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    extern virtual function void build_phase(uvm_phase phase);
    extern virtual task          run_phase  (uvm_phase phase);
endclass : sab_base_test

function void sab_base_test::build_phase(uvm_phase phase);
    super.build_phase(phase);
    u_env = sab_tb_env#(16, 32)::type_id::create("u_env", this);
endfunction : build_phase

task sab_base_test::run_phase(uvm_phase phase);
    phase.raise_objection(this);
    `uvm_info(get_type_name(), "sab base test start", UVM_LOW)
    #1000;
    `uvm_info(get_type_name(), "sab base test done",  UVM_LOW)
    phase.drop_objection(this);
endtask : run_phase

`endif // SAB_BASE_TEST_SV
