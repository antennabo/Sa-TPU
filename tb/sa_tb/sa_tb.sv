// sa_tb — systolic_array 逐拍对拍 testbench（回放 golden 向量）。
//
// 读 +TXT=<path> 指定的 golden 向量（默认 ../../build/sa_cosim/ws1.txt）。
// 文件格式（由 simulator/cycle/tests 的 _dump_sa_ws() 生成）：
//   - 任意行 `#` 开头 → 注释（含矩阵公式与列头），扫到第一个非 `#` 行作头
//   - 头行: AR AC F NCYC
//   - 数据行（每行 1+AR+AR+AC+AC+AR+AC 个数）：cy a[AR] av[AR] b[AC] bv[AC] bs[AR] ex[AC]
//     cy 仅作人眼对齐，sa_tb 读后丢弃
//
// 维度通过 +define+SA_ROW_N=N +define+SA_COL_N=M 配置（默认 2x2）。
`timescale 1ns/1ps
`ifndef SA_ROW_N
  `define SA_ROW_N 2
`endif
`ifndef SA_COL_N
  `define SA_COL_N 2
`endif
module sa_tb;
    localparam int ROW_N = `SA_ROW_N;   // = AR
    localparam int COL_N = `SA_COL_N;   // = AC
    localparam int A_W   = 8;
    localparam int B_W   = 8;
    localparam int OUT_W = 32;

    logic                clk, rst_n;
    logic [A_W-1:0]      a       [ROW_N];
    logic                a_vld   [ROW_N];
    logic                b_sw    [ROW_N];
    logic                b_rdy   [COL_N];
    logic                b_vld   [COL_N];
    logic [B_W-1:0]      b       [COL_N];
    logic [OUT_W-1:0]    out     [COL_N];
    logic                out_vld [COL_N];

    systolic_array #(
        .ROW_N(ROW_N), .COL_N(COL_N), .A_W(A_W), .B_W(B_W), .OUT_W(OUT_W)
    ) dut (
        .clk(clk), .rst_n(rst_n),
        .a(a), .a_vld(a_vld), .b_sw(b_sw),
        .b_rdy(b_rdy), .b_vld(b_vld), .b(b),
        .out(out), .out_vld(out_vld)
    );

    initial clk = 1'b0;
    always #5 clk = ~clk;

    integer fd, code, t, i, tmp;
    integer ar, ac, f, ncyc, errors;
    integer exp [COL_N];
    string  txt_path;
    string  line;
    int     idx, rc;

    initial begin
`ifdef DUMP_FSDB
        $fsdbDumpfile("cpu_wave.fsdb");
        $fsdbDumpvars(0, sa_tb, "+all", "+mda", "+packedmda", "+struct");
`endif
        errors = 0;
        if (!$value$plusargs("TXT=%s", txt_path))
            txt_path = "../../build/sa_cosim/ws1.txt";
        fd = $fopen(txt_path, "r");
        if (fd == 0) begin
            $display("FATAL: 打不开 %s（先跑对应的 dump_cosim 测试）", txt_path);
            $finish;
        end

        // 跳过 `#` 注释/空行，扫到第一个非注释行作为 "AR AC F NCYC" 头
        rc = 0;
        while (!$feof(fd)) begin
            void'($fgets(line, fd));
            idx = 0;
            while (idx < line.len() && (line[idx] == " " || line[idx] == "\t")) idx = idx + 1;
            if (idx >= line.len()) continue;
            if (line[idx] == "#" || line[idx] == "\n" || line[idx] == "\r") continue;
            rc = $sscanf(line, "%d %d %d %d", ar, ac, f, ncyc);
            if (rc == 4) break;
        end
        if (rc != 4) begin
            $display("FATAL: 未找到头部 'AR AC F NCYC' (file=%s)", txt_path);
            $finish;
        end
        if (ar != ROW_N || ac != COL_N) begin
            $display("FATAL: 维度不匹配 文件 AR=%0d AC=%0d，tb ROW_N=%0d COL_N=%0d", ar, ac, ROW_N, COL_N);
            $finish;
        end

        // 复位
        rst_n = 1'b0;
        for (i = 0; i < ROW_N; i = i + 1) begin a[i] = '0; a_vld[i] = 1'b0; b_sw[i] = 1'b0; end
        for (i = 0; i < COL_N; i = i + 1) begin b[i] = '0; b_vld[i] = 1'b0; end
        repeat (3) @(posedge clk);
        @(negedge clk); rst_n = 1'b1;

        for (t = 0; t < ncyc; t = t + 1) begin
            @(negedge clk);                         // 边沿间驱动，保证 posedge 前稳定
            code = $fscanf(fd, "%d", tmp);          // cy 索引（仅人眼对齐，丢弃）
            for (i = 0; i < ROW_N; i = i + 1) begin code = $fscanf(fd, "%d", tmp); a[i]     = tmp[A_W-1:0]; end
            for (i = 0; i < ROW_N; i = i + 1) begin code = $fscanf(fd, "%d", tmp); a_vld[i] = tmp[0];       end
            for (i = 0; i < COL_N; i = i + 1) begin code = $fscanf(fd, "%d", tmp); b[i]     = tmp[B_W-1:0]; end
            for (i = 0; i < COL_N; i = i + 1) begin code = $fscanf(fd, "%d", tmp); b_vld[i] = tmp[0];       end
            for (i = 0; i < ROW_N; i = i + 1) begin code = $fscanf(fd, "%d", tmp); b_sw[i]  = tmp[0];       end
            for (i = 0; i < COL_N; i = i + 1) begin code = $fscanf(fd, "%d", exp[i]);                       end

            @(posedge clk);                         // 寄存器更新 → out 反映新状态
            #1;
            for (i = 0; i < COL_N; i = i + 1) begin
                if ($signed(dut.out[i]) !== exp[i]) begin
                    $display("MISMATCH cy=%0d col=%0d: got=%0d exp=%0d", t, i, $signed(dut.out[i]), exp[i]);
                    errors = errors + 1;
                end
            end
        end
        $fclose(fd);
        if (errors == 0) $display("PASS: 全部 %0d 拍 out 与 golden 一致", ncyc);
        else             $display("FAIL: %0d 处不一致", errors);
        $finish;
    end
endmodule
