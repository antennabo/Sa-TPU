// ctrl_ws_tb — controller_ws 逐拍对拍 testbench（回放 golden 向量）。
//
// 读 +TXT=<path> 指定的 golden 向量。
// 文件格式（由 simulator/cycle/tests/controller_ws_test.py 生成）：
//   - 任意行 `#` 开头 → 注释，扫到第一个非 `#` 行作头
//   - 头行: AR AC F NCYC
//   - 数据行：cy start wL aA sw tag tn sp  state feed b_sw[AR]  wr_row wr_tile wr_vld  rd_en[AR] rd_addr[AR]
//   - row N 含义：drive[N]=cy=N TB 要驱动的输入；expect[N]=cy=N 应读到的 RTL 输出 (寄存器值)
//                即 commit drive[N-1] 之后的状态，row 0 expect = 复位后状态
//
// 维度通过 +define+CTRL_AR=N +CTRL_AC=N +CTRL_F=N +CTRL_TILE_NUM_MAX=N 配置（默认 2/2/2/2）。
// K_ABUF 跟 controller_ws.sv 默认绑 F，LATENCY=2，TAG_W=4。

`timescale 1ns/1ps
`ifndef CTRL_AR
  `define CTRL_AR 2
`endif
`ifndef CTRL_AC
  `define CTRL_AC 2
`endif
`ifndef CTRL_F
  `define CTRL_F 2
`endif
`ifndef CTRL_TILE_NUM_MAX
  `define CTRL_TILE_NUM_MAX 2
`endif

module ctrl_ws_tb;
    localparam int AR           = `CTRL_AR;
    localparam int AC           = `CTRL_AC;
    localparam int F_MAX        = `CTRL_F;                 // 编译期最大 F（位宽用）
    localparam int LATENCY      = 2;
    localparam int TILE_NUM_MAX = `CTRL_TILE_NUM_MAX;
    localparam int K_ABUF_MAX   = F_MAX;
    localparam int TAG_W        = 4;
    localparam int WR_TILE_W    = TAG_W;
    localparam int M_W          = $clog2(F_MAX + 1);
    localparam int TILE_NUM_W   = $clog2(TILE_NUM_MAX + 1);
    localparam int PAGE_SPAN    = K_ABUF_MAX * TILE_NUM_MAX;
    localparam int ABUF_DEPTH   = 2 * PAGE_SPAN;
    localparam int LANE_ADDR_W  = $clog2(ABUF_DEPTH);

    logic clk, rst_n;

    // DUT 输入
    logic                       i_start;
    logic                       i_weight_loaded;
    logic                       i_activ_available;
    logic                       i_switch_weight;
    logic [TAG_W-1:0]           i_tag;
    logic [TILE_NUM_W-1:0]      i_tile_num;
    logic                       i_switch_page;
    logic [M_W-1:0]             i_F;                  // 当前作业 M 维（运行时）

    // DUT 输出
    logic                       o_feed;
    logic                       o_b_sw         [AR];
    logic [M_W-1:0]             o_wr_row;
    logic [WR_TILE_W-1:0]       o_wr_tile;
    logic                       o_wr_vld;
    logic                       o_rd_en        [AR];
    logic [LANE_ADDR_W-1:0]     o_rd_addr      [AR];
    logic [2:0]                 o_ws_state;

    controller_ws #(
        .AR(AR), .AC(AC), .LATENCY(LATENCY),
        .TILE_NUM_MAX(TILE_NUM_MAX), .K_ABUF_MAX(K_ABUF_MAX), .F_MAX(F_MAX),
        .TAG_W(TAG_W)
    ) dut (
        .clk(clk), .rst_n(rst_n),
        .i_start(i_start),
        .i_weight_loaded(i_weight_loaded),
        .i_activ_available(i_activ_available),
        .i_switch_weight(i_switch_weight),
        .i_tag(i_tag),
        .i_tile_num(i_tile_num),
        .i_switch_page(i_switch_page),
        .i_F(i_F),
        .o_feed(o_feed),
        .o_b_sw(o_b_sw),
        .o_wr_row(o_wr_row),
        .o_wr_tile(o_wr_tile),
        .o_wr_vld(o_wr_vld),
        .o_rd_en(o_rd_en), .o_rd_addr(o_rd_addr),
        .o_ws_state(o_ws_state)
    );

    initial clk = 1'b0;
    always #5 clk = ~clk;

    integer fd, code, t, i, tmp;
    integer ar_param, ac_param, f_param, ncyc, errors;
    integer exp_state, exp_feed;
    integer exp_b_sw    [AR];
    integer exp_wr_row, exp_wr_tile, exp_wr_vld;
    integer exp_rd_en   [AR];
    integer exp_rd_addr [AR];
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

        // 跳过 # 注释/空行扫到 "AR AC F NCYC" 头
        rc = 0;
        while (!$feof(fd)) begin
            void'($fgets(line, fd));
            idx = 0;
            while (idx < line.len() && (line[idx] == " " || line[idx] == "\t")) idx = idx + 1;
            if (idx >= line.len()) continue;
            if (line[idx] == "#" || line[idx] == "\n" || line[idx] == "\r") continue;
            rc = $sscanf(line, "%d %d %d %d", ar_param, ac_param, f_param, ncyc);
            if (rc == 4) break;
        end
        if (rc != 4) begin
            $display("FATAL: 未找到头部 'AR AC F NCYC' (file=%s)", txt_path);
            $finish;
        end
        if (ar_param != AR || ac_param != AC || f_param > F_MAX) begin
            $display("FATAL: 维度不匹配 文件 AR=%0d AC=%0d F=%0d，tb AR=%0d AC=%0d F_MAX=%0d",
                     ar_param, ac_param, f_param, AR, AC, F_MAX);
            $finish;
        end

        // 复位
        rst_n              = 1'b0;
        i_start            = 1'b0;
        i_weight_loaded    = 1'b0;
        i_activ_available  = 1'b0;
        i_switch_weight    = 1'b0;
        i_tag              = '0;
        i_tile_num         = '0;
        i_switch_page      = 1'b0;
        i_F                = f_param[M_W-1:0];        // 整段固定（运行时输入，scenario 期常数）
        repeat (3) @(posedge clk);
        @(negedge clk); rst_n = 1'b1;

        for (t = 0; t < ncyc; t = t + 1) begin
            @(negedge clk);
            // 读 row t
            code = $fscanf(fd, "%d", tmp);                                    // cy 索引（丢弃）
            code = $fscanf(fd, "%d", tmp); i_start            = tmp[0];
            code = $fscanf(fd, "%d", tmp); i_weight_loaded    = tmp[0];
            code = $fscanf(fd, "%d", tmp); i_activ_available  = tmp[0];
            code = $fscanf(fd, "%d", tmp); i_switch_weight    = tmp[0];
            code = $fscanf(fd, "%d", tmp); i_tag              = tmp[TAG_W-1:0];
            code = $fscanf(fd, "%d", tmp); i_tile_num         = tmp[TILE_NUM_W-1:0];
            code = $fscanf(fd, "%d", tmp); i_switch_page      = tmp[0];
            code = $fscanf(fd, "%d", exp_state);
            code = $fscanf(fd, "%d", exp_feed);
            for (i = 0; i < AR; i = i + 1) begin code = $fscanf(fd, "%d", exp_b_sw[i]);    end
            code = $fscanf(fd, "%d", exp_wr_row);
            code = $fscanf(fd, "%d", exp_wr_tile);
            code = $fscanf(fd, "%d", exp_wr_vld);
            for (i = 0; i < AR; i = i + 1) begin code = $fscanf(fd, "%d", exp_rd_en[i]);   end
            for (i = 0; i < AR; i = i + 1) begin code = $fscanf(fd, "%d", exp_rd_addr[i]); end

            // 比对 当前 (= commit drive[t-1] 后的寄存器值)
            // 顺序：先比第一个 mismatch 就 +errors 并继续，方便一次看清所有差异
            if (o_ws_state !== exp_state[2:0]) begin
                $display("MISMATCH cy=%0d state got=%0d exp=%0d", t, o_ws_state, exp_state);
                errors = errors + 1;
            end
            if (o_feed !== exp_feed[0]) begin
                $display("MISMATCH cy=%0d feed got=%0d exp=%0d", t, o_feed, exp_feed);
                errors = errors + 1;
            end
            for (i = 0; i < AR; i = i + 1) begin
                if (o_b_sw[i] !== exp_b_sw[i][0]) begin
                    $display("MISMATCH cy=%0d b_sw[%0d] got=%0d exp=%0d", t, i, o_b_sw[i], exp_b_sw[i]);
                    errors = errors + 1;
                end
            end
            if (o_wr_row !== exp_wr_row[M_W-1:0]) begin
                $display("MISMATCH cy=%0d wr_row got=%0d exp=%0d", t, o_wr_row, exp_wr_row);
                errors = errors + 1;
            end
            if (o_wr_tile !== exp_wr_tile[WR_TILE_W-1:0]) begin
                $display("MISMATCH cy=%0d wr_tile got=%0d exp=%0d", t, o_wr_tile, exp_wr_tile);
                errors = errors + 1;
            end
            if (o_wr_vld !== exp_wr_vld[0]) begin
                $display("MISMATCH cy=%0d wr_vld got=%0d exp=%0d", t, o_wr_vld, exp_wr_vld);
                errors = errors + 1;
            end
            for (i = 0; i < AR; i = i + 1) begin
                if (o_rd_en[i] !== exp_rd_en[i][0]) begin
                    $display("MISMATCH cy=%0d rd_en[%0d] got=%0d exp=%0d", t, i, o_rd_en[i], exp_rd_en[i]);
                    errors = errors + 1;
                end
            end
            for (i = 0; i < AR; i = i + 1) begin
                if (o_rd_addr[i] !== exp_rd_addr[i][LANE_ADDR_W-1:0]) begin
                    $display("MISMATCH cy=%0d rd_addr[%0d] got=%0d exp=%0d", t, i, o_rd_addr[i], exp_rd_addr[i]);
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