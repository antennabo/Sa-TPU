`ifndef SAB_CFG_SV
`define SAB_CFG_SV

class sab_cfg extends uvm_object;
    `uvm_object_utils(sab_cfg)

    uvm_active_passive_enum master_is_active = UVM_ACTIVE;
    uvm_active_passive_enum slave_is_active  = UVM_PASSIVE;
    bit                     coverage_enable  = 1'b1;

    function new (string name = "sab_cfg");
        super.new(name);
    endfunction
endclass : sab_cfg

`endif // SAB_CFG_SV
