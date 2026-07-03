`ifndef {{VIP_NAME_UPPER}}_COVERAGE_SV
`define {{VIP_NAME_UPPER}}_COVERAGE_SV

class {{VIP_NAME}}_coverage #(ADDR_W={{ADDR_W_DEFAULT}}, DATA_W={{DATA_W_DEFAULT}}) extends uvm_subscriber#({{VIP_NAME}}_seq_item#(ADDR_W, DATA_W));
    `uvm_component_param_utils({{VIP_NAME}}_coverage#(ADDR_W, DATA_W))

    {{VIP_NAME}}_seq_item#(ADDR_W, DATA_W) t;
    bit coverage_enable = 1'b1;

    covergroup {{VIP_NAME}}_rw_cg;
        cp_rw:   coverpoint t.rw;
        cp_addr: coverpoint t.addr;
    endgroup

    covergroup {{VIP_NAME}}_data_cg;
        cp_wdata: coverpoint t.wdata;
        cp_delay: coverpoint t.delay_kind;
    endgroup

    function new (string name = "{{VIP_NAME}}_coverage", uvm_component parent = null);
        super.new(name, parent);
        {{VIP_NAME}}_rw_cg   = new();
        {{VIP_NAME}}_data_cg = new();
    endfunction

    extern function void build_phase(uvm_phase phase);
    extern function void write({{VIP_NAME}}_seq_item#(ADDR_W, DATA_W) trans);
    extern function void coverage_check();
endclass : {{VIP_NAME}}_coverage

function void {{VIP_NAME}}_coverage::build_phase(uvm_phase phase);
    super.build_phase(phase);
    void'(uvm_config_db#(bit)::get(this, "", "coverage_enable", coverage_enable));
    `uvm_info(get_type_name(), $sformatf("coverage_enable = %0b", coverage_enable), UVM_HIGH)
endfunction : build_phase

function void {{VIP_NAME}}_coverage::write({{VIP_NAME}}_seq_item#(ADDR_W, DATA_W) trans);
    t = trans;
    if (coverage_enable)
        coverage_check();
endfunction : write

function void {{VIP_NAME}}_coverage::coverage_check();
    {{VIP_NAME}}_rw_cg.sample();
    {{VIP_NAME}}_data_cg.sample();
endfunction : coverage_check

`endif // {{VIP_NAME_UPPER}}_COVERAGE_SV
