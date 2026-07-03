`ifndef {{VIP_NAME_UPPER}}_BASE_TEST_SV
`define {{VIP_NAME_UPPER}}_BASE_TEST_SV

class {{VIP_NAME}}_base_test extends uvm_test;
    `uvm_component_utils({{VIP_NAME}}_base_test)

    {{VIP_NAME}}_tb_env #(.ADDR_W({{ADDR_W_DEFAULT}}), .DATA_W({{DATA_W_DEFAULT}})) u_env;

    function new (string name = "{{VIP_NAME}}_base_test", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    extern virtual function void build_phase(uvm_phase phase);
    extern virtual task          run_phase  (uvm_phase phase);
endclass : {{VIP_NAME}}_base_test

function void {{VIP_NAME}}_base_test::build_phase(uvm_phase phase);
    super.build_phase(phase);
    u_env = {{VIP_NAME}}_tb_env#({{ADDR_W_DEFAULT}}, {{DATA_W_DEFAULT}})::type_id::create("u_env", this);
endfunction : build_phase

task {{VIP_NAME}}_base_test::run_phase(uvm_phase phase);
    phase.raise_objection(this);
    `uvm_info(get_type_name(), "{{VIP_NAME}} base test start", UVM_LOW)
    #1000;
    `uvm_info(get_type_name(), "{{VIP_NAME}} base test done",  UVM_LOW)
    phase.drop_objection(this);
endtask : run_phase

`endif // {{VIP_NAME_UPPER}}_BASE_TEST_SV
