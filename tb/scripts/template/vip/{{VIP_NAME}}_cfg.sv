`ifndef {{VIP_NAME_UPPER}}_CFG_SV
`define {{VIP_NAME_UPPER}}_CFG_SV

class {{VIP_NAME}}_cfg extends uvm_object;
    `uvm_object_utils({{VIP_NAME}}_cfg)

    uvm_active_passive_enum master_is_active = UVM_ACTIVE;
    bit                     coverage_enable  = 1'b1;

    function new (string name = "{{VIP_NAME}}_cfg");
        super.new(name);
    endfunction
endclass : {{VIP_NAME}}_cfg

`endif // {{VIP_NAME_UPPER}}_CFG_SV
