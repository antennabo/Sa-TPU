`ifndef {{VIP_NAME_UPPER}}_SEQ_ITEM_SV
`define {{VIP_NAME_UPPER}}_SEQ_ITEM_SV

typedef enum {ZERO, SHORT, MEDIUM, LARGE, MAX} {{VIP_NAME}}_item_delay_e;

class {{VIP_NAME}}_seq_item #(ADDR_W={{ADDR_W_DEFAULT}}, DATA_W={{DATA_W_DEFAULT}}) extends uvm_sequence_item;
    rand bit [ADDR_W - 1 : 0]      addr;
    rand bit [DATA_W - 1 : 0]      wdata;     // driven by master (write)
    bit      [DATA_W - 1 : 0]      rdata;     // returned by slave (read), not randomized
    rand bit                       rw;        // 1: write, 0: read
    rand int unsigned              delay;
    rand {{VIP_NAME}}_item_delay_e delay_kind;

    `uvm_object_param_utils_begin({{VIP_NAME}}_seq_item#(ADDR_W, DATA_W))
        `uvm_field_int(addr,  UVM_ALL_ON)
        `uvm_field_int(wdata, UVM_ALL_ON | ((rw == '0) ? UVM_NOCOMPARE : 0))
        `uvm_field_int(rdata, UVM_ALL_ON | ((rw == '1) ? UVM_NOCOMPARE : 0))
        `uvm_field_int(rw,    UVM_ALL_ON)
        `uvm_field_enum({{VIP_NAME}}_item_delay_e, delay_kind, UVM_ALL_ON)
    `uvm_object_utils_end

    constraint delay_order_c { solve delay_kind before delay; }
    constraint delay_c {
        (delay_kind == ZERO  ) -> delay == 0;
        (delay_kind == SHORT ) -> delay inside {[1:10]};
        (delay_kind == MEDIUM) -> delay inside {[11:99]};
        (delay_kind == LARGE ) -> delay inside {[100:999]};
        (delay_kind == MAX   ) -> delay == 1000;
        delay >= 0; delay <= 1000;
    }
    constraint delay_kind_d {
        delay_kind dist {ZERO:=2, SHORT:=1, MEDIUM:=1, LARGE:=1, MAX:=2};
    }

    function new (string name = "{{VIP_NAME}}_seq_item");
        super.new(name);
    endfunction
endclass : {{VIP_NAME}}_seq_item

`endif // {{VIP_NAME_UPPER}}_SEQ_ITEM_SV
