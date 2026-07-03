// sab_reg_adapter — 翻译 uvm_reg_bus_op ↔ sab_seq_item#(16, 32)
//
// RAL 内部只认它自己的 bus_op struct; SAB VIP 只认 sab_seq_item.
// 这个 class 就是俩之间的双向翻译器, 两个函数搞定.

`ifndef SAB_REG_ADAPTER_SV
`define SAB_REG_ADAPTER_SV

class sab_reg_adapter extends uvm_reg_adapter;
    `uvm_object_utils(sab_reg_adapter)

    function new(string name = "sab_reg_adapter");
        super.new(name);
        // ── adapter 声明它自己的能力 ─────────────────────
        supports_byte_enable = 0;   // SAB 没有 byte-enable
        provides_responses   = 0;   // driver 用 by-ref 回 rdata (见 sab_driver),
                                    // 不通过独立 rsp item 回 response
    endfunction

    // ─────────────────────────────────────────────────────
    // reg2bus: RAL 要发起一次访问, 把 bus_op 翻成一个 sab_seq_item
    // 会被 RAL 内部自动调用 (在 sequencer 上 start_item 之前)
    // ─────────────────────────────────────────────────────
    virtual function uvm_sequence_item reg2bus(const ref uvm_reg_bus_op rw);
        sab_seq_item#(16, 32) item;
        item = sab_seq_item#(16, 32)::type_id::create("ral_item");

        item.rw    = (rw.kind == UVM_WRITE) ? 1'b1 : 1'b0;
        item.addr  = rw.addr[14:0];      // uvm_reg_addr_t 是 64-bit, 截 15
        item.wdata = rw.data;            // read 时也填, driver 会忽略

        // RAL 访问不希望被随机 delay 干扰 (间隔要可控, 不然 back-to-back 测不出)
        item.delay_kind = ZERO;
        item.delay      = 0;
        return item;
    endfunction

    // ─────────────────────────────────────────────────────
    // bus2reg: driver 跑完 item 回来 (rdata / resp_err 已填),
    // 把 sab_seq_item 反译回 bus_op, RAL 从 bus_op.data 拿 read 结果
    // ─────────────────────────────────────────────────────
    virtual function void bus2reg(uvm_sequence_item bus_item,
                                   ref uvm_reg_bus_op rw);
        sab_seq_item#(16, 32) item;
        if (!$cast(item, bus_item))
            `uvm_fatal("SAB_REG_ADAPTER", "bus_item cast to sab_seq_item#(16,32) failed")

        rw.kind   = item.rw ? UVM_WRITE : UVM_READ;
        rw.addr   = item.addr;
        rw.data   = item.rdata;
        rw.status = item.resp_err ? UVM_NOT_OK : UVM_IS_OK;
    endfunction
endclass

`endif // SAB_REG_ADAPTER_SV