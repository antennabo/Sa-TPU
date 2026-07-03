`ifndef SAB_COVERAGE_SV
`define SAB_COVERAGE_SV

class sab_coverage #(ADDR_W=32, DATA_W=32) extends uvm_subscriber#(sab_seq_item#(ADDR_W, DATA_W));
    `uvm_component_param_utils(sab_coverage#(ADDR_W, DATA_W))

    sab_seq_item#(ADDR_W, DATA_W) t;
    bit coverage_enable = 1'b1;

    covergroup sab_rw_cg;
        cp_rw:       coverpoint t.rw;
        cp_addr:     coverpoint t.addr;
        cp_resp_err: coverpoint t.resp_err;
        cross_rw_err: cross cp_rw, cp_resp_err;
    endgroup

    covergroup sab_data_cg;
        cp_wdata: coverpoint t.wdata iff (t.rw);
        cp_rdata: coverpoint t.rdata iff (!t.rw && !t.resp_err);
        cp_delay: coverpoint t.delay_kind;
        cross_rw_delay: cross t.rw, cp_delay;
    endgroup

    function new (string name = "sab_coverage", uvm_component parent = null);
        super.new(name, parent);
        sab_rw_cg   = new();
        sab_data_cg = new();
    endfunction

    extern function void build_phase(uvm_phase phase);
    extern function void write(sab_seq_item#(ADDR_W, DATA_W) trans);
    extern function void coverage_check();
endclass : sab_coverage

function void sab_coverage::build_phase(uvm_phase phase);
    super.build_phase(phase);
    void'(uvm_config_db#(bit)::get(this, "", "coverage_enable", coverage_enable));
    `uvm_info(get_type_name(), $sformatf("coverage_enable = %0b", coverage_enable), UVM_HIGH)
endfunction : build_phase

function void sab_coverage::write(sab_seq_item#(ADDR_W, DATA_W) trans);
    t = trans;
    if (coverage_enable)
        coverage_check();
endfunction : write

function void sab_coverage::coverage_check();
    sab_rw_cg.sample();
    sab_data_cg.sample();
endfunction : coverage_check

`endif // SAB_COVERAGE_SV
