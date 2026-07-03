`ifndef E2E_MINIMAL_TEST_SV
`define E2E_MINIMAL_TEST_SV

// e2e_minimal_test — 端到端最小 matmul 通路 (N=8 pad 版).
//
// 参数 (来自 tb_top default): N=8 (AR=AC=8), LATENCY=2, WTILE_NUM_MAX=128,
//                              ABUF_DEPTH=2048, ACCUM_DEPTH=1024, WFIFO_DEPTH=1024.
//
// 单 wtile K=AR=8. 只前 2×2 有非零数据, 其余 pad 0 让硬件仍跑全 8×8 pipeline:
//   A[K=0..1][M=0..1] = [[1,2],[3,4]]           A[K][M] 其余 = 0
//   B[K=0..1][N=0..1] = [[7,8],[5,6]]           B[K][N] 其余 = 0
// 手算:
//   Y[m][n] = Σ_{k=0..7} A[k][m] * B[k][n]  (K>=2 项为 0)
//   前 2×2 = [[22,26],[34,40]], 其余 = 0
//
// Layout 见 doc/satpu_cfg.md §3.3:
//   ABUF offset = (K << ACT_ADDR_W) | M    (ACT_ADDR_W=11)
//   WFIFO push per col K 反序: K=7 first, K=0 last
//   ACCUM offset = (N << ACC_ADDR_W) | M   (ACC_ADDR_W=10)

class e2e_minimal_test extends sab_base_test;
    `uvm_component_utils(e2e_minimal_test)

    localparam int POLL_TIMEOUT = 500;
    localparam int N_LANE       = 8;
    localparam int K_TILE       = 8;   // = AR

    function new (string name = "e2e_minimal_test", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    task run_phase(uvm_phase phase);
        uvm_status_e   status;
        uvm_reg_data_t rdata;
        int            errors;
        int            poll_cnt;

        // A[K][M], B[K][N], golden[M][N] — 全 8×8, 只前 2×2 有值
        logic [7:0] A_mat  [8][8];
        logic [7:0] B_mat  [8][8];
        int         golden [8][8];

        phase.raise_objection(this);
        `uvm_info(get_type_name(), "e2e minimal test start", UVM_LOW)

        #200;
        errors = 0;

        // ── 初始化数据矩阵 ───────────────────────────────────────
        foreach (A_mat[k, m]) A_mat[k][m] = 8'd0;
        foreach (B_mat[k, n]) B_mat[k][n] = 8'd0;
        foreach (golden[m, n]) golden[m][n] = 0;

        A_mat[0][0] = 8'd1; A_mat[0][1] = 8'd2;
        A_mat[1][0] = 8'd3; A_mat[1][1] = 8'd4;

        B_mat[0][0] = 8'd7; B_mat[0][1] = 8'd8;
        B_mat[1][0] = 8'd5; B_mat[1][1] = 8'd6;

        golden[0][0] = 22; golden[0][1] = 26;
        golden[1][0] = 34; golden[1][1] = 40;

        // ── 1) 灌 ABUF: offset = (K << ACT_ADDR_W) | M ─────────
        for (int k = 0; k < K_TILE; k++) begin
            for (int m = 0; m < N_LANE; m++) begin
                u_env.reg_model.ABUF.write(status, (k << 11) | m, A_mat[k][m]);
            end
        end
        `uvm_info(get_type_name(), "ABUF loaded (8x8, only [0..1][0..1] nonzero)", UVM_MEDIUM)

        // ── 2) 灌 WFIFO: per col push K=7 first, K=0 last ──────
        for (int col = 0; col < N_LANE; col++) begin
            for (int k = K_TILE - 1; k >= 0; k--) begin
                u_env.reg_model.WFIFO.write(status, col, B_mat[k][col]);
            end
        end
        `uvm_info(get_type_name(), "WFIFO loaded (8 cols x 8 K, K-reverse push)", UVM_MEDIUM)

        // ── 3) 写指令 ───────────────────────────────────────────
        u_env.reg_model.WTILE_NUM .write(status, 32'd1);
        u_env.reg_model.ACT_STADDR.write(status, 32'd0);
        u_env.reg_model.ACC_STADDR.write(status, 32'd0);
        u_env.reg_model.FEED_NUM  .write(status, 32'd10);   // W = AR + LATENCY = 8 + 2

        // ── 4) ACTIV_AVAIL = 1 ─────────────────────────────────
        u_env.reg_model.ACTIV_AVAIL.write(status, 32'd1);

        // ── 5) START pulse ─────────────────────────────────────
        u_env.reg_model.START.write(status, 32'd1);
        u_env.reg_model.START.write(status, 32'd0);
        `uvm_info(get_type_name(), "START fired", UVM_MEDIUM)

        // ── 6) Poll STATUS.ws_state 直到 IDLE=0 ────────────────
        poll_cnt = 0;
        forever begin
            u_env.reg_model.STATUS.read(status, rdata);
            `uvm_info(get_type_name(),
                $sformatf("poll[%0d] STATUS=0x%0h ws_state=%0d",
                          poll_cnt, rdata, rdata[2:0]), UVM_HIGH)
            if (rdata[2:0] == 3'd0) break;
            poll_cnt++;
            if (poll_cnt >= POLL_TIMEOUT) begin
                `uvm_fatal(get_type_name(),
                    $sformatf("STATUS.ws_state did not return to IDLE after %0d polls",
                              POLL_TIMEOUT))
            end
        end
        `uvm_info(get_type_name(),
            $sformatf("controller returned to IDLE after %0d polls", poll_cnt), UVM_LOW)

        // ── 7) 读 ACCUM 8x8 对拍 golden ────────────────────────
        for (int col = 0; col < N_LANE; col++) begin
            for (int row = 0; row < N_LANE; row++) begin
                u_env.reg_model.ACCUM.read(status, (col << 10) | row, rdata);
                if ($signed(rdata) !== golden[row][col]) begin
                    errors++;
                    `uvm_error(get_type_name(),
                        $sformatf("ACCUM[col=%0d row=%0d]: got %0d, expected %0d",
                                  col, row, $signed(rdata), golden[row][col]))
                end
                else if (golden[row][col] != 0) begin
                    `uvm_info(get_type_name(),
                        $sformatf("ACCUM[col=%0d row=%0d] = %0d  OK",
                                  col, row, $signed(rdata)), UVM_LOW)
                end
            end
        end

        if (errors == 0)
            `uvm_info(get_type_name(), "e2e minimal matmul (pad 8x8): PASS", UVM_LOW)
        else
            `uvm_error(get_type_name(),
                $sformatf("total %0d ACCUM mismatches", errors))

        `uvm_info(get_type_name(), "e2e minimal test done", UVM_LOW)
        phase.drop_objection(this);
    endtask
endclass

`endif // E2E_MINIMAL_TEST_SV