// ctrl_ws_tb — controller_ws 逐拍对拍 testbench（回放 golden 向量）。
//
// 读 +TXT=<path> 指定的 golden 向量。
// 文件格式（由 simulator/cycle/tests/controller_ws_test.py 生成）：
//   - 任意行 `#` 开头 → 注释，扫到第一个非 `#` 行作头
//   - 头行: AR AC NCYC
//   - 数据行 (按列序)：
//       cy start wL aA  wtn ac sa fn   state  weight_sw[AR]  act_ren[AR] act_raddr[AR]
//                                              acc_wen[AC] acc_waddr[AC] acc_outen[AC]
//   - row N 含义：drive[N]=cy=N TB 要驱动的输入；expect[N]=cy=N 应读到的 RTL 输出 (寄存器值)
//                即 commit drive[N-1] 之后的状态，row 0 expect = 复位后状态
//
// 维度通过 +define+CTRL_AR=N +CTRL_AC=N +CTRL_WTILE_NUM_MAX=N 配置（默认 2/2/4）。
// LATENCY=2，ACT_ADDR_W=4，ACC_ADDR_W=6（与 Python ref dump 默认对齐）。

`timescale 1ns/1ps
`ifndef CTRL_AR
  `define CTRL_AR 2
`endif
`ifndef CTRL_AC
  `define CTRL_AC 2
`endif
`ifndef CTRL_WTILE_NUM_MAX
  `define CTRL_WTILE_NUM_MAX 4
`endif
`ifndef CTRL_ACT_ADDR_W
  `define CTRL_ACT_ADDR_W 4
`endif
`ifndef CTRL_ACC_ADDR_W
  `define CTRL_ACC_ADDR_W 6
`endif

module ctrl_ws_tb;
    localparam int AR            = `CTRL_AR;
    localparam int AC            = `CTRL_AC;
    localparam int LATENCY       = 2;
    localparam int WTILE_NUM_MAX = `CTRL_WTILE_NUM_MAX;
    localparam int ACT_ADDR_W    = `CTRL_ACT_ADDR_W;
    localparam int ACC_ADDR_W    = `CTRL_ACC_ADDR_W;
    localparam int W             = AR + LATENCY;
    localparam int FEED_NUM_W    = ACT_ADDR_W + 1;
    localparam int WTILE_NUM_W   = (WTILE_NUM_MAX > 1) ? $clog2(WTILE_NUM_MAX + 1) : 1;

    logic clk, rst_n;

    // DUT 输入
    logic                       i_start;
    logic                       i_weight_loaded;
    logic                       i_activ_available;
    logic [WTILE_NUM_W-1:0]     i_wtile_num;
    logic [ACT_ADDR_W-1:0]      i_act_staddr;
    logic [ACC_ADDR_W-1:0]      i_acc_staddr;
    logic [FEED_NUM_W-1:0]      i_feed_num;

    // DUT 输出
    logic                       o_weight_sw   [AR];
    logic                       o_acc_wen     [AC];
    logic [ACC_ADDR_W-1:0]      o_acc_waddr   [AC];
    logic                       o_acc_accen   [AC];
    logic                       o_acc_outen   [AC];
    logic                       o_acc_ren     [AC];
    logic [ACC_ADDR_W-1:0]      o_acc_raddr   [AC];
    logic                       o_act_ren     [AR];
    logic [ACT_ADDR_W-1:0]      o_act_raddr   [AR];
    logic [2:0]                 o_ws_state;

    controller_ws #(
        .AR             (AR),
        .AC             (AC),
        .LATENCY        (LATENCY),
        .WTILE_NUM_MAX  (WTILE_NUM_MAX),
        .ACT_ADDR_W     (ACT_ADDR_W),
        .ACC_ADDR_W     (ACC_ADDR_W)
    ) dut (
        .clk(clk), .rst_n(rst_n),
        .i_start            (i_start),
        .i_weight_loaded    (i_weight_loaded),
        .i_activ_available  (i_activ_available),
        .i_wtile_num        (i_wtile_num),
        .i_act_staddr       (i_act_staddr),
        .i_acc_staddr       (i_acc_staddr),
        .i_feed_num         (i_feed_num),
        .o_weight_sw        (o_weight_sw),
        .o_acc_wen          (o_acc_wen),
        .o_acc_waddr        (o_acc_waddr),
        .o_acc_accen        (o_acc_accen),
        .o_acc_outen        (o_acc_outen),
        .o_acc_ren          (o_acc_ren),
        .o_acc_raddr        (o_acc_raddr),
        .o_act_ren          (o_act_ren),
        .o_act_raddr        (o_act_raddr),
        .o_ws_state         (o_ws_state)
    );

    initial clk = 1'b0;
    always #5 clk = ~clk;

    integer fd, code, t, i, tmp;
    integer ar_param, ac_param, ncyc, errors;
    integer exp_state;
    integer exp_weight_sw [AR];
    integer exp_act_ren   [AR];
    integer exp_act_raddr [AR];
    integer exp_acc_wen   [AC];
    integer exp_acc_waddr [AC];
    integer exp_acc_outen [AC];
    string  txt_path;
    string  line;
    int     idx, rc;

    initial begin
`ifdef DUMP_FSDB
        $fsdbDumpfile("cpu_wave.fsdb");
        $fsdbDumpvars(0, ctrl_ws_tb, "+all", "+mda", "+packedmda", "+struct");
`endif
        errors = 0;
        if (!$value$plusargs("TXT=%s", txt_path))
            txt_path = "../../build/ctrl_ws_cosim/single.txt";
        fd = $fopen(txt_path, "r");
        if (fd == 0) begin
            $display("FATAL: 打不开 %s（先跑对应的 dump 测试）", txt_path);
            $finish;
        end

        // 跳过 # 注释/空行扫到 "AR AC NCYC" 头
        rc = 0;
        while (!$feof(fd)) begin
            void'($fgets(line, fd));
            idx = 0;
            while (idx < line.len() && (line[idx] == " " || line[idx] == "\t")) idx = idx + 1;
            if (idx >= line.len()) continue;
            if (line[idx] == "#" || line[idx] == "\n" || line[idx] == "\r") continue;
            rc = $sscanf(line, "%d %d %d", ar_param, ac_param, ncyc);
            if (rc == 3) break;
        end
        if (rc != 3) begin
            $display("FATAL: 未找到头部 'AR AC NCYC' (file=%s)", txt_path);
            $finish;
        end
        if (ar_param != AR || ac_param != AC) begin
            $display("FATAL: 维度不匹配 文件 AR=%0d AC=%0d，tb AR=%0d AC=%0d",
                     ar_param, ac_param, AR, AC);
            $finish;
        end

        // 复位
        rst_n              = 1'b0;
        i_start            = 1'b0;
        i_weight_loaded    = 1'b0;
        i_activ_available  = 1'b0;
        i_wtile_num        = '0;
        i_act_staddr       = '0;
        i_acc_staddr       = '0;
        i_feed_num         = '0;
        repeat (3) @(posedge clk);
        @(negedge clk); rst_n = 1'b1;

        for (t = 0; t < ncyc; t = t + 1) begin
            @(negedge clk);
            // 读 row t (列顺序与 dump 严格一致)
            code = $fscanf(fd, "%d", tmp);                                       // cy 索引（丢弃）
            code = $fscanf(fd, "%d", tmp); i_start            = tmp[0];
            code = $fscanf(fd, "%d", tmp); i_weight_loaded    = tmp[0];
            code = $fscanf(fd, "%d", tmp); i_activ_available  = tmp[0];
            code = $fscanf(fd, "%d", tmp); i_wtile_num        = tmp[WTILE_NUM_W-1:0];
            code = $fscanf(fd, "%d", tmp); i_acc_staddr       = tmp[ACC_ADDR_W-1:0];
            code = $fscanf(fd, "%d", tmp); i_act_staddr       = tmp[ACT_ADDR_W-1:0];
            code = $fscanf(fd, "%d", tmp); i_feed_num         = tmp[FEED_NUM_W-1:0];

            code = $fscanf(fd, "%d", exp_state);
            for (i = 0; i < AR; i = i + 1) code = $fscanf(fd, "%d", exp_weight_sw[i]);
            for (i = 0; i < AR; i = i + 1) code = $fscanf(fd, "%d", exp_act_ren[i]);
            for (i = 0; i < AR; i = i + 1) code = $fscanf(fd, "%d", exp_act_raddr[i]);
            for (i = 0; i < AC; i = i + 1) code = $fscanf(fd, "%d", exp_acc_wen[i]);
            for (i = 0; i < AC; i = i + 1) code = $fscanf(fd, "%d", exp_acc_waddr[i]);
            for (i = 0; i < AC; i = i + 1) code = $fscanf(fd, "%d", exp_acc_outen[i]);

            // 比对 当前 (= commit drive[t-1] 后的寄存器值)
            if (o_ws_state !== exp_state[2:0]) begin
                $display("MISMATCH cy=%0d state got=%0d exp=%0d", t, o_ws_state, exp_state);
                errors = errors + 1;
            end
            for (i = 0; i < AR; i = i + 1) begin
                if (o_weight_sw[i] !== exp_weight_sw[i][0]) begin
                    $display("MISMATCH cy=%0d weight_sw[%0d] got=%0d exp=%0d", t, i, o_weight_sw[i], exp_weight_sw[i]);
                    errors = errors + 1;
                end
                if (o_act_ren[i] !== exp_act_ren[i][0]) begin
                    $display("MISMATCH cy=%0d act_ren[%0d] got=%0d exp=%0d", t, i, o_act_ren[i], exp_act_ren[i]);
                    errors = errors + 1;
                end
                if (o_act_raddr[i] !== exp_act_raddr[i][ACT_ADDR_W-1:0]) begin
                    $display("MISMATCH cy=%0d act_raddr[%0d] got=%0d exp=%0d", t, i, o_act_raddr[i], exp_act_raddr[i]);
                    errors = errors + 1;
                end
            end
            for (i = 0; i < AC; i = i + 1) begin
                if (o_acc_wen[i] !== exp_acc_wen[i][0]) begin
                    $display("MISMATCH cy=%0d acc_wen[%0d] got=%0d exp=%0d", t, i, o_acc_wen[i], exp_acc_wen[i]);
                    errors = errors + 1;
                end
                if (o_acc_waddr[i] !== exp_acc_waddr[i][ACC_ADDR_W-1:0]) begin
                    $display("MISMATCH cy=%0d acc_waddr[%0d] got=%0d exp=%0d", t, i, o_acc_waddr[i], exp_acc_waddr[i]);
                    errors = errors + 1;
                end
                if (o_acc_outen[i] !== exp_acc_outen[i][0]) begin
                    $display("MISMATCH cy=%0d acc_outen[%0d] got=%0d exp=%0d", t, i, o_acc_outen[i], exp_acc_outen[i]);
                    errors = errors + 1;
                end
            end
        end
        $fclose(fd);
        if (errors == 0) $display("PASS: 全部 %0d 拍输出与 golden 一致", ncyc);
        else             $display("FAIL: %0d 处不一致", errors);
        $finish;
    end
endmodule
