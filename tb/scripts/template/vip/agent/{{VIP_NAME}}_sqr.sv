`ifndef {{VIP_NAME_UPPER}}_SQR_SV
`define {{VIP_NAME_UPPER}}_SQR_SV

class {{VIP_NAME}}_sqr #(ADDR_W={{ADDR_W_DEFAULT}}, DATA_W={{DATA_W_DEFAULT}}) extends uvm_sequencer#({{VIP_NAME}}_seq_item#(ADDR_W, DATA_W));
    `uvm_component_param_utils({{VIP_NAME}}_sqr#(ADDR_W, DATA_W))

    function new (string name = "{{VIP_NAME}}_sqr", uvm_component parent = null);
        super.new(name, parent);
    endfunction

endclass : {{VIP_NAME}}_sqr

`endif // {{VIP_NAME_UPPER}}_SQR_SV
