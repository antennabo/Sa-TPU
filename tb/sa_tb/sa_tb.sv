// sa_tb — systolic_array 逐拍对拍 testbench（回放 golden 向量）。
//
// 读 build/sa_cosim/ws1.txt（由 ws1_e2e_test.py::test_ws1_dump_cosim 导出）：
//   第1行: AR AC F NCYC
//   其后每拍: a_data[AR] a_vld[AR] b_data[AC] b_vld[AC] b_sw[AR] exp_out[AC]
// 每拍喂边界激励、过一个时钟沿，比对 out 是否等于 golden 该拍底行。
//
// 跑: iverilog -g2012 -s sa_tb -o /tmp/sa_tb.out tb/sa_tb.sv rtl/pe.sv \
//        rtl/systolic_array.sv rtl/common/*.sv && vvp /tmp/sa_tb.out
`timescale 1ns/1ps
module sa_tb;
    localparam int ROW_N = 2;   // = AR
    localparam int COL_N = 2;   // = AC
    localparam int A_W   = 8;
    localparam int B_W   = 8;
    localparam int OUT_W = 32;

    logic                clk, rst_n;
    logic [A_W-1:0]      a     [ROW_N];
    logic                a_vld [ROW_N];
    logic                b_sw  [ROW_N];
    logic                b_rdy [COL_N];
    logic                b_vld [COL_N];
    logic [B_W-1:0]      b     [COL_N];
    logic [OUT_W-1:0]    out   [COL_N];

    systolic_array #(
        .ROW_N(ROW_N), .COL_N(COL_N), .A_W(A_W), .B_W(B_W), .OUT_W(OUT_W)
    ) dut (
        .clk(clk), .rst_n(rst_n),
        .a(a), .a_vld(a_vld), .b_sw(b_sw),
        .b_rdy(b_rdy), .b_vld(b_vld), .b(b), .out(out)
    );

    initial clk = 1'b0;
    always #5 clk = ~clk;

    integer fd, code, t, i, tmp;
    integer ar, ac, f, ncyc, errors;
    integer exp [COL_N];

    initial begin
`ifdef DUMP_VCD
        $dumpfile("cpu_wave.vcd");
        $dumpvars(0, sa_tb);
`endif
        errors = 0;
        fd = $fopen("../../build/sa_cosim/ws1.txt", "r");   // 从 tb/sa_tb/ 跑
        if (fd == 0) begin
            $display("FATAL: 打不开 ../../build/sa_cosim/ws1.txt（先跑 test_ws1_dump_cosim）");
            $finish;
        end
        code = $fscanf(fd, "%d %d %d %d", ar, ac, f, ncyc);
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
