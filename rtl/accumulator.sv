// accumulator — per-AC 输出累加器
//
// 数据面：per-column psum 数据，每拍 i_psum[c] / i_psum_vld[c]（SA o_out / o_out_vld）
// 地址面：controller 已 deskew 的 per-column 控制
//          i_wr_vld[c] / i_wr_addr[c] / i_acc_en[c] / i_out_en[c]
//
// 存储：per-column sdpram，地址为 ACC_ADDR_W 位平坦地址（无 slot/row 二级结构）。
//   REG_OUT=0：sdpram 内部同步读，1 拍 latency。
//
// 内部 2 段流水（per-col）：
//   1stg (T):   寄存 controller / SA 信号
//   2stg (T+1): wdata = i_acc_en ? psum + rdata : psum
//               wr_en = psum_vld & i_wr_vld
//               waddr = i_wr_addr (delayed 1)
//               写 sdpram，同时 o_rlt / o_rlt_vld 给下游
//
// v1：acc_en 由 controller 恒 0（每 wtile 写独立区间，不累加）。K-tiling 累加需重审
//     accumulate 路径（rdata 应来自 waddr，当前实现 rdata 来自独立读口，K-tiling 时不对）。
//     详见 doc/decisions.md D8。

module accumulator #(
    parameter int AC                = 8,
    parameter int OUTPUT_W          = 32,
    parameter int ACC_ADDR_W        = 10,
    localparam int MEM_DEPTH        = 1 << ACC_ADDR_W
)(
    input  logic                    clk,
    input  logic                    rst_n,

    // ── 数据面（来自 SA o_out / o_out_vld）──
    input  logic [OUTPUT_W-1:0]     i_psum      [AC], // 1stg
    input  logic                    i_psum_vld  [AC], // 1stg

    // ── 控制面（来自 controller，已 per-column deskew）──
    input  logic                    i_wr_vld    [AC], // 1stg
    input  logic [ACC_ADDR_W-1:0]   i_wr_addr   [AC], // 1stg
    input  logic                    i_acc_en    [AC], // 1stg
    input  logic                    i_out_en    [AC], // 1stg
    output logic                    o_rlt_vld   [AC], // 2stg
    output logic [OUTPUT_W-1:0]     o_rlt       [AC], // 2stg

    // ── 读出口（同步读, 1 拍 latency）──
    input  logic                    i_rd_en     [AC], // 0stg
    input  logic [ACC_ADDR_W-1:0]   i_rd_addr   [AC], // 0stg
    output logic [OUTPUT_W-1:0]     o_rd_data   [AC]  // 1stg (sdpram rdata)
);

    // 1stg 寄存
    logic                       wr_vld_1stg     [AC];
    logic [ACC_ADDR_W-1:0]      waddr_1stg      [AC];
    logic                       acc_en_1stg     [AC];
    logic                       out_en_1stg     [AC];
    logic [OUTPUT_W-1:0]        psum_1stg       [AC];
    logic                       psum_vld_1stg   [AC];

    // 2stg 寄存
    logic                       wr_en_2stg      [AC];
    logic [ACC_ADDR_W-1:0]      waddr_2stg      [AC];
    logic [OUTPUT_W-1:0]        wdata_2stg      [AC];

    // sdpram 读出
    logic [OUTPUT_W-1:0]        rdata_1stg      [AC];

    // 1stg：直通输入（保持 per-AC 数组形式，便于下游 generate 引用）
    always_comb begin
        for (int c = 0; c < AC; c = c + 1) begin
            wr_vld_1stg[c]   = i_wr_vld[c];
            waddr_1stg[c]    = i_wr_addr[c];
            acc_en_1stg[c]   = i_acc_en[c];
            out_en_1stg[c]   = i_out_en[c];
            psum_1stg[c]     = i_psum[c];
            psum_vld_1stg[c] = i_psum_vld[c];
        end
    end

    genvar col;
    generate
        for (col = 0; col < AC; col = col + 1) begin : acc_col
            // wdata 2stg：accumulate (v1 acc_en=0 时退化为直通)
            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    wdata_2stg[col] <= '0;
                end else if (acc_en_1stg[col]) begin
                    wdata_2stg[col] <= psum_1stg[col] + rdata_1stg[col]; // TODO sat
                end else begin
                    wdata_2stg[col] <= psum_1stg[col];
                end
            end

            // wr_en 2stg：psum 有效 且 controller 要写
            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n) wr_en_2stg[col] <= 1'b0;
                else        wr_en_2stg[col] <= psum_vld_1stg[col] & wr_vld_1stg[col];
            end

            // waddr 2stg：跟 wdata 同节拍
            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n) waddr_2stg[col] <= '0;
                else        waddr_2stg[col] <= waddr_1stg[col];
            end

            // o_rlt_vld 2stg：psum 有效 且 out_en
            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n) o_rlt_vld[col] <= 1'b0;
                else        o_rlt_vld[col] <= psum_vld_1stg[col] & out_en_1stg[col];
            end

            assign o_rlt[col]    = wdata_2stg[col];
            assign o_rd_data[col] = rdata_1stg[col];

            sdpram #(
                .DATA_W   (OUTPUT_W),
                .DEPTH    (MEM_DEPTH),
                .REG_OUT  (1'b0)
            ) u_mem (
                .clk      (clk),
                .wr_en    (wr_en_2stg[col]),
                .wr_be    ({(OUTPUT_W/8){1'b1}}),
                .wr_addr  (waddr_2stg[col]),
                .wr_wdata (wdata_2stg[col]),
                .rd_en    (i_rd_en[col]),
                .rd_addr  (i_rd_addr[col]),
                .rd_rdata (rdata_1stg[col])
            );
        end
    endgenerate

endmodule
