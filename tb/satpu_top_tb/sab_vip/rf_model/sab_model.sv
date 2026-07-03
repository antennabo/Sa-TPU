`ifndef SAB_MODEL_SV
`define SAB_MODEL_SV

class sab_model #(
    int ADDR_W   = 32,
    int DATA_W   = 32,
    int S_ADDR_W = 12,
    int N_SLAVES = 2
) extends uvm_component;

    uvm_blocking_get_port #(sab_seq_item#(ADDR_W, DATA_W))   port;
    uvm_analysis_port     #(sab_seq_item#(S_ADDR_W, DATA_W)) ap[N_SLAVES];

    `uvm_component_param_utils(sab_model#(ADDR_W, DATA_W, S_ADDR_W, N_SLAVES))

    function new(string name = "sab_model", uvm_component parent = null);
        super.new(name, parent);
    endfunction : new

    extern virtual function void build_phase(uvm_phase phase);
    extern virtual task run_phase(uvm_phase phase);
endclass : sab_model

function void sab_model::build_phase(uvm_phase phase);
    super.build_phase(phase);
    port = new("port", this);
    foreach (ap[i])
        ap[i] = new($sformatf("ap[%0d]", i), this);
endfunction : build_phase

task sab_model::run_phase(uvm_phase phase);
    sab_seq_item#(ADDR_W, DATA_W)   mst_tr;
    sab_seq_item#(S_ADDR_W, DATA_W) slv_tr;
    int unsigned slave_idx;

    forever begin
        port.get(mst_tr);

        `uvm_info(get_type_name(), $sformatf("[MDL] trans detail:\n%s", mst_tr.sprint()), UVM_HIGH)

        slave_idx = int'(mst_tr.addr >> S_ADDR_W);

        if (slave_idx >= N_SLAVES) begin
            `uvm_error(get_type_name(), $sformatf(
                "addr=0x%0h -> slave_idx=%0d out of range [0:%0d]",
                mst_tr.addr, slave_idx, N_SLAVES-1))
            continue;
        end

        slv_tr       = sab_seq_item#(S_ADDR_W, DATA_W)::type_id::create("slv_tr");
        slv_tr.addr  = mst_tr.addr[S_ADDR_W-1:0];
        slv_tr.wdata = mst_tr.wdata;
        slv_tr.rw    = mst_tr.rw;

        `uvm_info(get_type_name(), $sformatf("[MDL] -> slave[%0d] %s addr=0x%0h wdata=0x%0h",
            slave_idx, slv_tr.rw ? "WR" : "RD", slv_tr.addr, slv_tr.wdata), UVM_MEDIUM)

        ap[slave_idx].write(slv_tr);
    end
endtask : run_phase

`endif // SAB_MODEL_SV
