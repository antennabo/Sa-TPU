`ifndef SAB_SQR_SV
`define SAB_SQR_SV

class sab_sqr #(ADDR_W=32, DATA_W=32) extends uvm_sequencer#(sab_seq_item#(ADDR_W, DATA_W));
    `uvm_component_param_utils(sab_sqr#(ADDR_W, DATA_W))

    function new (string name = "sab_sqr", uvm_component parent = null);
        super.new(name, parent);
    endfunction

endclass : sab_sqr

`endif // SAB_SQR_SV
