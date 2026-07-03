`ifndef RAL_SMOKE_TEST_SV
`define RAL_SMOKE_TEST_SV

// RAL 最小可运行 test:
//   1) reg_model.WTILE_NUM.write() 发一次写
//   2) reg_model.WTILE_NUM.read()  读回来
//   3) 检查 mirror (无总线动作) 是否也同步
// 三项通过 → RAL + adapter + set_sequencer 配置都对.

class ral_smoke_test extends sab_base_test;
    `uvm_component_utils(ral_smoke_test)

    function new (string name = "ral_smoke_test", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    task run_phase(uvm_phase phase);
        uvm_status_e   status;
        uvm_reg_data_t rdata;

        phase.raise_objection(this);
        `uvm_info(get_type_name(), "ral smoke test start", UVM_LOW)

        // 等 hw rst 释放 (tb_top: rst_n 大约 100ns 后释放)
        #200;

        // ── 1) write WTILE_NUM = 3 ─────────────────────────
        u_env.reg_model.WTILE_NUM.write(status, 32'd3);
        if (status != UVM_IS_OK)
            `uvm_error(get_type_name(), $sformatf(
                "write WTILE_NUM failed, status=%s", status.name()))

        // ── 2) read WTILE_NUM 回来对拍 ─────────────────────
        u_env.reg_model.WTILE_NUM.read(status, rdata);
        if (status != UVM_IS_OK)
            `uvm_error(get_type_name(), $sformatf(
                "read WTILE_NUM failed, status=%s", status.name()))
        if (rdata[2:0] != 3'd3)
            `uvm_error(get_type_name(), $sformatf(
                "WTILE_NUM readback mismatch: got %0d, expected 3", rdata[2:0]))

        // ── 3) 检查 mirror ─────────────────────────────────
        // .get() 拿的是 RAL 内部 mirror, 无总线动作;
        // write() 后 implicit predictor 应把 mirror 更新到 3.
        if (u_env.reg_model.WTILE_NUM.get() != 3)
            `uvm_error(get_type_name(), $sformatf(
                "mirror mismatch: get()=%0d, expected 3",
                u_env.reg_model.WTILE_NUM.get()))

        `uvm_info(get_type_name(), "ral smoke test done", UVM_LOW)
        phase.drop_objection(this);
    endtask
endclass

`endif // RAL_SMOKE_TEST_SV
