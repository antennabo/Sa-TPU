`ifndef SAB_SCB_SV
`define SAB_SCB_SV

class sab_scb #(int ADDR_W=32, int DATA_W=32) extends uvm_scoreboard;
    sab_seq_item#(ADDR_W, DATA_W) expect_queue[$];
    sab_seq_item#(ADDR_W, DATA_W) actual_queue[$];
    uvm_blocking_get_port #(sab_seq_item#(ADDR_W, DATA_W)) exp_port;
    uvm_blocking_get_port #(sab_seq_item#(ADDR_W, DATA_W)) act_port;
    int unsigned exp_rx_cnt;
    int unsigned act_rx_cnt;
    int unsigned cmp_cnt;
    int unsigned pass_cnt;
    int unsigned fail_cnt;

    `uvm_component_param_utils(sab_scb#(ADDR_W, DATA_W))

    function new(string name, uvm_component parent = null);
        super.new(name, parent);
    endfunction : new

    extern virtual function void build_phase(uvm_phase phase);
    extern virtual task run_phase(uvm_phase phase);
    extern virtual function void report_phase(uvm_phase phase);
endclass : sab_scb

function void sab_scb::build_phase(uvm_phase phase);
    super.build_phase(phase);
    exp_port = new("exp_port", this);
    act_port = new("act_port", this);
    exp_rx_cnt = 0;
    act_rx_cnt = 0;
    cmp_cnt    = 0;
    pass_cnt   = 0;
    fail_cnt   = 0;
endfunction : build_phase

task sab_scb::run_phase(uvm_phase phase);
    sab_seq_item#(ADDR_W, DATA_W) get_expect, get_actual;
    bit result;
    bit field_ok;
    fork
        // collect expected: if actual already queued, compare immediately
        while (1) begin
            exp_port.get(get_expect);
            exp_rx_cnt++;
            if (actual_queue.size() > 0) begin
                get_actual = actual_queue.pop_front();
                field_ok = 1'b1;
                if (get_actual.rw       !== get_expect.rw      ) field_ok = 1'b0;
                if (get_actual.addr     !== get_expect.addr    ) field_ok = 1'b0;
                if (get_actual.wdata    !== get_expect.wdata   ) field_ok = 1'b0;
                if (get_actual.resp_err !== get_expect.resp_err) field_ok = 1'b0;
                // rdata is meaningful only for successful reads.
                if ((get_expect.rw == 1'b0) && (get_expect.resp_err == 1'b0)) begin
                    if (get_actual.rdata !== get_expect.rdata) field_ok = 1'b0;
                end
                result = field_ok;
                cmp_cnt++;
                if (result) begin
                    pass_cnt++;
                    `uvm_info(get_type_name(), "[SCB] PASS", UVM_LOW)
                end else begin
                    fail_cnt++;
                    `uvm_error(get_type_name(), "[SCB] FAIL")
                    get_expect.print();
                    get_actual.print();
                end
            end else begin
                expect_queue.push_back(get_expect);
            end
        end
        // collect actual: if expected already queued, compare immediately
        while (1) begin
            act_port.get(get_actual);
            act_rx_cnt++;
            if (expect_queue.size() > 0) begin
                get_expect = expect_queue.pop_front();
                field_ok = 1'b1;
                if (get_actual.rw       !== get_expect.rw      ) field_ok = 1'b0;
                if (get_actual.addr     !== get_expect.addr    ) field_ok = 1'b0;
                if (get_actual.wdata    !== get_expect.wdata   ) field_ok = 1'b0;
                if (get_actual.resp_err !== get_expect.resp_err) field_ok = 1'b0;
                // rdata is meaningful only for successful reads.
                if ((get_expect.rw == 1'b0) && (get_expect.resp_err == 1'b0)) begin
                    if (get_actual.rdata !== get_expect.rdata) field_ok = 1'b0;
                end
                result = field_ok;
                cmp_cnt++;
                if (result) begin
                    pass_cnt++;
                    `uvm_info(get_type_name(), "[SCB] PASS", UVM_LOW)
                end else begin
                    fail_cnt++;
                    `uvm_error(get_type_name(), "[SCB] FAIL")
                    get_expect.print();
                    get_actual.print();
                end
            end else begin
                actual_queue.push_back(get_actual);
            end
        end
    join
endtask : run_phase

function void sab_scb::report_phase(uvm_phase phase);
    super.report_phase(phase);
    `uvm_info(get_type_name(),
        $sformatf("[SCB] summary exp_rx=%0d act_rx=%0d cmp=%0d pass=%0d fail=%0d exp_q=%0d act_q=%0d",
                  exp_rx_cnt, act_rx_cnt, cmp_cnt, pass_cnt, fail_cnt,
                  expect_queue.size(), actual_queue.size()),
        UVM_LOW)

    if (cmp_cnt == 0) begin
        `uvm_error(get_type_name(), "[SCB] No comparisons performed (possible false pass)")
    end
    if ((expect_queue.size() != 0) || (actual_queue.size() != 0)) begin
        `uvm_error(get_type_name(), $sformatf(
            "[SCB] Unmatched transactions remain: expect_q=%0d actual_q=%0d",
            expect_queue.size(), actual_queue.size()))
    end
    if ((cmp_cnt > 0) && (fail_cnt == 0) &&
        (expect_queue.size() == 0) && (actual_queue.size() == 0)) begin
        `uvm_info(get_type_name(), "[SCB] FINAL PASS", UVM_LOW)
    end
endfunction : report_phase

`endif // SAB_SCB_SV
