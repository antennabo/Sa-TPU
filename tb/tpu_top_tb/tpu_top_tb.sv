// tpu_top_tb — tinytpu_top 端到端逐拍 cosim testbench。
//
// 读 +TXT=<path> 的 dump 向量（由 simulator/cycle/tests/tpu_top_test.py 生成）。
// 文件格式：
//   - `#` 行注释，扫到第一个非 `#` 行作头
//   - 头行: AR AC WTILE_NUM ACT_STADDR ACC_STADDR FEED_NUM ABUF_DEPTH ACCUM_DEPTH NCYC
//   - 数据行 (NCYC 行)：
//       cy start aA  wr_en wr_addr wd[0..AR-1]  wfv wfd[0..AC-1]
//     wr_en/wr_addr 是 abuf 写口标量（所有 lane 同步写一行），wd 是 per-lane。
//   - golden 行 (WTILE_NUM * FEED_NUM 行)：每行 AC 个 int =
//       accum.mem[acc_staddr + wtile_idx * feed_num + r][c]
//
// 跑流程：复位 → NCYC 拍回放 inputs（含 abuf 预载 M 拍 + 主循环）
//        → 用 accumulator 读口逐 (wtile, row) 拉数据对 golden（1 拍 sdpram latency）。
//
// 指令字段 (wtile_num/act_staddr/acc_staddr/feed_num) 从 txt 头取，复位后常驱不变。
// 维度通过 +define+TPU_TOP_{N,LATENCY,WTILE_NUM_MAX,ABUF_DEPTH,ACCUM_DEPTH} 配置。

`timescale 1ns/1ps
`ifndef TPU_TOP_N
  `define TPU_TOP_N 2
`endif
`ifndef TPU_TOP_LATENCY
  `define TPU_TOP_LATENCY 2
`endif
`ifndef TPU_TOP_WTILE_NUM_MAX
  `define TPU_TOP_WTILE_NUM_MAX 2
`endif
`ifndef TPU_TOP_ABUF_DEPTH
  `define TPU_TOP_ABUF_DEPTH 8
`endif
`ifndef TPU_TOP_ACCUM_DEPTH
  `define TPU_TOP_ACCUM_DEPTH 64
`endif

module tpu_top_tb;
    localparam int N             = `TPU_TOP_N;
    localparam int A_W           = 8;
    localparam int B_W           = 8;
    localparam int OUT_W         = 32;
    localparam int LATENCY       = `TPU_TOP_LATENCY;
    localparam int WTILE_NUM_MAX = `TPU_TOP_WTILE_NUM_MAX;
    localparam int ABUF_DEPTH    = `TPU_TOP_ABUF_DEPTH;
    localparam int ACCUM_DEPTH   = `TPU_TOP_ACCUM_DEPTH;
    localparam int WFIFO_DEPTH   = 16;
    localparam int AR            = N;
    localparam int AC            = N;
    localparam int W             = AR + LATENCY;
    localparam int ACT_ADDR_W    = (ABUF_DEPTH  > 1) ? $clog2(ABUF_DEPTH)  : 1;
    localparam int ACC_ADDR_W    = (ACCUM_DEPTH > 1) ? $clog2(ACCUM_DEPTH) : 1;
    localparam int FEED_NUM_W    = ACT_ADDR_W + 1;
    localparam int WTILE_NUM_W   = (WTILE_NUM_MAX > 1) ? $clog2(WTILE_NUM_MAX + 1) : 1;

    logic clk, rst_n;

    // DUT inputs
    logic                       i_start;
    logic                       i_activ_available;
    logic [WTILE_NUM_W-1:0]     i_wtile_num;
    logic [ACT_ADDR_W-1:0]      i_act_staddr;
    logic [ACC_ADDR_W-1:0]      i_acc_staddr;
    logic [FEED_NUM_W-1:0]      i_feed_num;

    logic                       i_abuf_wr_en   [AR];
    logic [ACT_ADDR_W-1:0]      i_abuf_wr_addr [AR];
    logic [A_W-1:0]             i_abuf_wr_data [AR];

    logic                       i_wfifo_wvalid;
    logic [B_W-1:0]             i_wfifo_wdata  [AC];
    logic                       o_wfifo_full;

    logic                       i_accum_rd_en;
    logic [ACC_ADDR_W-1:0]      i_accum_rd_addr;
    logic [OUT_W-1:0]           o_accum_rd_data [AC];

    logic [2:0]                 o_ws_state;

    tinytpu_top #(
        .N             (N),
        .A_W           (A_W),
        .B_W           (B_W),
        .OUT_W         (OUT_W),
        .WTILE_NUM_MAX (WTILE_NUM_MAX),
        .ABUF_DEPTH    (ABUF_DEPTH),
        .ACCUM_DEPTH   (ACCUM_DEPTH),
        .WFIFO_DEPTH   (WFIFO_DEPTH),
        .LATENCY       (LATENCY)
    ) dut (
        .clk               (clk),
        .rst_n             (rst_n),
        .i_start           (i_start),
        .i_activ_available (i_activ_available),
        .i_wtile_num       (i_wtile_num),
        .i_act_staddr      (i_act_staddr),
        .i_acc_staddr      (i_acc_staddr),
        .i_feed_num        (i_feed_num),
        .i_abuf_wr_en      (i_abuf_wr_en),
        .i_abuf_wr_addr    (i_abuf_wr_addr),
        .i_abuf_wr_data    (i_abuf_wr_data),
        .i_wfifo_wvalid    (i_wfifo_wvalid),
        .i_wfifo_wdata     (i_wfifo_wdata),
        .o_wfifo_full      (o_wfifo_full),
        .i_accum_rd_en     (i_accum_rd_en),
        .i_accum_rd_addr   (i_accum_rd_addr),
        .o_accum_rd_data   (o_accum_rd_data),
        .o_ws_state        (o_ws_state)
    );

    initial clk = 1'b0;
    always #5 clk = ~clk;

    integer fd, code, tmp, i;
    integer file_AR, file_AC, file_ABUF_D, file_ACCUM_D;
    integer WTILE_NUM, ACT_STADDR, ACC_STADDR, FEED_NUM, NCYC;
    integer t, errors;
    integer drive_start, drive_aA, drive_wr_en, drive_wr_addr;
    integer drive_wr_data [AR];
    integer drive_wf_v;
    integer drive_wf_d    [AC];
    integer golden        [];     // dyn, size = WTILE_NUM * FEED_NUM * AC
    integer addr, wt, r, c, prev_addr;
    string  txt_path;
    string  line;
    int     idx, rc;

    initial begin
`ifdef DUMP_FSDB
        $fsdbDumpfile("cpu_wave.fsdb");
        $fsdbDumpvars(0, tpu_top_tb, "+all", "+mda", "+packedmda", "+struct");
`endif
        errors = 0;
        if (!$value$plusargs("TXT=%s", txt_path))
            txt_path = "../../build/tpu_top_cosim/small.txt";
        fd = $fopen(txt_path, "r");
        if (fd == 0) begin
            $display("FATAL: 打不开 %s（先跑 test_tpu_top_dump_small/switch）", txt_path);
            $finish;
        end

        // 扫到非 # 行作头：AR AC WTILE_NUM ACT_STADDR ACC_STADDR FEED_NUM ABUF_DEPTH ACCUM_DEPTH NCYC
        rc = 0;
        while (!$feof(fd)) begin
            void'($fgets(line, fd));
            idx = 0;
            while (idx < line.len() && (line[idx] == " " || line[idx] == "\t")) idx = idx + 1;
            if (idx >= line.len()) continue;
            if (line[idx] == "#" || line[idx] == "\n" || line[idx] == "\r") continue;
            rc = $sscanf(line, "%d %d %d %d %d %d %d %d %d",
                         file_AR, file_AC, WTILE_NUM, ACT_STADDR, ACC_STADDR, FEED_NUM,
                         file_ABUF_D, file_ACCUM_D, NCYC);
            if (rc == 9) break;
        end
        if (rc != 9) begin
            $display("FATAL: 未找到 9 列头行 (file=%s)", txt_path);
            $finish;
        end
        if (file_AR != AR || file_AC != AC ||
            file_ABUF_D != ABUF_DEPTH || file_ACCUM_D != ACCUM_DEPTH) begin
            $display("FATAL: 维度不匹配 文件 AR=%0d AC=%0d ABUF=%0d ACCUM=%0d, tb AR=%0d AC=%0d ABUF=%0d ACCUM=%0d",
                     file_AR, file_AC, file_ABUF_D, file_ACCUM_D,
                     AR, AC, ABUF_DEPTH, ACCUM_DEPTH);
            $finish;
        end

        // 复位 + 所有 input 清 0（指令常量 wtile_num/act_staddr/acc_staddr/feed_num
        // 在复位区间内就常驱，复位释放后保持不变；7 态 FSM 在 IDLE→WLOAD 边沿采）
        rst_n             = 1'b0;
        i_start           = 1'b0;
        i_activ_available = 1'b0;
        i_wtile_num       = WTILE_NUM[WTILE_NUM_W-1:0];
        i_act_staddr      = ACT_STADDR[ACT_ADDR_W-1:0];
        i_acc_staddr      = ACC_STADDR[ACC_ADDR_W-1:0];
        i_feed_num        = FEED_NUM[FEED_NUM_W-1:0];
        for (i = 0; i < AR; i = i + 1) begin
            i_abuf_wr_en[i]   = 1'b0;
            i_abuf_wr_addr[i] = '0;
            i_abuf_wr_data[i] = '0;
        end
        i_wfifo_wvalid    = 1'b0;
        for (i = 0; i < AC; i = i + 1) i_wfifo_wdata[i] = '0;
        i_accum_rd_en     = 1'b0;
        i_accum_rd_addr   = '0;
        repeat (3) @(posedge clk);
        @(negedge clk); rst_n = 1'b1;

        // ── NCYC 拍回放 inputs ──
        for (t = 0; t < NCYC; t = t + 1) begin
            @(negedge clk);
            code = $fscanf(fd, "%d", tmp);                                   // cy 丢弃
            code = $fscanf(fd, "%d", drive_start);
            code = $fscanf(fd, "%d", drive_aA);
            code = $fscanf(fd, "%d", drive_wr_en);
            code = $fscanf(fd, "%d", drive_wr_addr);
            for (i = 0; i < AR; i = i + 1) code = $fscanf(fd, "%d", drive_wr_data[i]);
            code = $fscanf(fd, "%d", drive_wf_v);
            for (i = 0; i < AC; i = i + 1) code = $fscanf(fd, "%d", drive_wf_d[i]);

            i_start            = drive_start[0];
            i_activ_available  = drive_aA[0];
            for (i = 0; i < AR; i = i + 1) begin
                i_abuf_wr_en[i]   = drive_wr_en[0];
                i_abuf_wr_addr[i] = drive_wr_addr[ACT_ADDR_W-1:0];
                i_abuf_wr_data[i] = drive_wr_data[i][A_W-1:0];
            end
            i_wfifo_wvalid     = drive_wf_v[0];
            for (i = 0; i < AC; i = i + 1) i_wfifo_wdata[i] = drive_wf_d[i][B_W-1:0];
        end

        // 主循环输入到此为止，把 input 拉零（指令常量不动）
        @(negedge clk);
        i_start            = 1'b0;
        i_activ_available  = 1'b0;
        for (i = 0; i < AR; i = i + 1) i_abuf_wr_en[i] = 1'b0;
        i_wfifo_wvalid     = 1'b0;

        // ── 读 golden = WTILE_NUM * FEED_NUM * AC ──
        golden = new[WTILE_NUM * FEED_NUM * AC];
        for (i = 0; i < WTILE_NUM * FEED_NUM * AC; i = i + 1)
            code = $fscanf(fd, "%d", golden[i]);
        $fclose(fd);

        // ── 用 accumulator 读口逐 (wtile, row) 拉数据 ──
        // sdpram 同步读 1 拍 latency：拍 T 给 (rd_en, addr)，拍 T+1 o_rd_data 出。
        prev_addr = 0;
        for (wt = 0; wt < WTILE_NUM; wt = wt + 1) begin
            for (r = 0; r < FEED_NUM; r = r + 1) begin
                addr = ACC_STADDR + wt * FEED_NUM + r;
                @(negedge clk);
                i_accum_rd_en   = 1'b1;
                i_accum_rd_addr = addr[ACC_ADDR_W-1:0];
                // 比对上一拍出来的数据（首拍 skip）
                if (!(wt == 0 && r == 0)) begin
                    for (c = 0; c < AC; c = c + 1) begin
                        if ($signed(o_accum_rd_data[c]) !==
                            golden[(prev_addr - ACC_STADDR) * AC + c]) begin
                            $display("MISMATCH addr=%0d col=%0d got=%0d exp=%0d",
                                     prev_addr, c,
                                     $signed(o_accum_rd_data[c]),
                                     golden[(prev_addr - ACC_STADDR) * AC + c]);
                            errors = errors + 1;
                        end
                    end
                end
                prev_addr = addr;
            end
        end

        // 拉最后一组数据
        @(negedge clk); i_accum_rd_en = 1'b0;
        for (c = 0; c < AC; c = c + 1) begin
            if ($signed(o_accum_rd_data[c]) !==
                golden[(prev_addr - ACC_STADDR) * AC + c]) begin
                $display("MISMATCH addr=%0d col=%0d got=%0d exp=%0d",
                         prev_addr, c,
                         $signed(o_accum_rd_data[c]),
                         golden[(prev_addr - ACC_STADDR) * AC + c]);
                errors = errors + 1;
            end
        end

        if (errors == 0) $display("PASS: 全部 %0d wtile * %0d 行 * %0d 列 与 golden 一致",
                                  WTILE_NUM, FEED_NUM, AC);
        else             $display("FAIL: %0d 处不一致", errors);
        $finish;
    end
endmodule
