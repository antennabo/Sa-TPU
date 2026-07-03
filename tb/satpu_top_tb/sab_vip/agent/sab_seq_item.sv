`ifndef SAB_SEQ_ITEM_SV
`define SAB_SEQ_ITEM_SV

typedef enum {ZERO, SHORT, MEDIUM, LARGE, MAX} sab_item_delay_e;

class sab_seq_item #(ADDR_W=32, DATA_W=32) extends uvm_sequence_item;
    rand bit [ADDR_W - 1 : 0]      addr;
    rand bit [DATA_W - 1 : 0]      wdata;     // driven by master (write)
    bit      [DATA_W - 1 : 0]      rdata;     // returned by slave (read), not randomized
    bit                           resp_err;    // response error from slave
    rand bit                       rw;        // 1: write, 0: read
    rand int unsigned              delay;
    rand sab_item_delay_e delay_kind;

    `uvm_object_param_utils_begin(sab_seq_item#(ADDR_W, DATA_W))
        `uvm_field_int(addr,  UVM_ALL_ON)
        `uvm_field_int(wdata, UVM_ALL_ON | ((rw == '0) ? UVM_NOCOMPARE : 0))
        `uvm_field_int(rdata, UVM_ALL_ON | ((rw == '1) ? UVM_NOCOMPARE : 0))
        `uvm_field_int(resp_err, UVM_ALL_ON)
        `uvm_field_int(rw,    UVM_ALL_ON)
        `uvm_field_enum(sab_item_delay_e, delay_kind, UVM_ALL_ON)
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

    function new (string name = "sab_seq_item");
        super.new(name);
    endfunction
endclass : sab_seq_item

`endif // SAB_SEQ_ITEM_SV
