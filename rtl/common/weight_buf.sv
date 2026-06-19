// weight_buf — WS active/shadow 权重双缓冲（方案②：传递，非 mux ping-pong）
//
// 每个 PE 一份，对应 golden model pe.py 的 b(active) / b_buf(shadow) + spatial_array._route_b：
//   - shadow（影子）：复用 data_buf 的 1 深 valid/ready 缓冲，作为列内"下沉填充链"的一级。
//       上邻 shadow → 本级 shadow → 下邻 shadow；o_in_rsv_rdy = i_out_rsv_rdy | !满，
//       逐级串成整列组合 ready 链（= _route_b 的 ready_k = 空 or ready_{k+1}）。
//   - active（在算）：寄存器，直接喂 PE 的乘法器（o_work_data）。
//   - i_sw（对角 swap 脉冲）：active <= shadow 当前值，同拍清空 shadow（让出可再填）。
//       = pe.py b_sw 分支 b_next=b_buf / b_buf_vld_next=False 的硬件对应。
module weight_buf #(
    parameter int DATA_W = 8
)(
    input  logic                clk,
    input  logic                rst_n,
    // ── shadow 填充链：上邻进 ───────────────────────────────
    input  logic                i_in_rsv_vld,
    output logic                o_in_rsv_rdy,
    input  logic [DATA_W-1:0]   i_in_rsv_data,
    // ── shadow 填充链：下邻出 ───────────────────────────────
    output logic                o_out_rsv_vld,
    input  logic                i_out_rsv_rdy,
    output logic [DATA_W-1:0]   o_out_rsv_data,
    // ── active 权重 → PE ───────────────────────────────────
    output logic [DATA_W-1:0]   o_work_data,
    // ── 控制 ───────────────────────────────────────────────
    input  logic                i_sw            // swap 脉冲：active<=shadow，shadow 清空
);
    logic [DATA_W-1:0] act;

    // shadow = 标准 1 深 v/r 缓冲；swap 拍用 i_clr 清空（值已转入 active）
    data_buf #(
        .DATA_W     (DATA_W         )
    ) u_shadow (
        .clk        (clk            ),
        .rst_n      (rst_n          ),
        .i_in_valid (i_in_rsv_vld   ),
        .o_in_ready (o_in_rsv_rdy   ),
        .i_in_data  (i_in_rsv_data  ),
        .o_out_valid(o_out_rsv_vld  ),
        .i_out_ready(i_out_rsv_rdy  ),
        .o_out_data (o_out_rsv_data ),
        .i_clr      (i_sw           )
    );

    // active：swap 拍捕获 shadow 当前值（o_out_rsv_data 为寄存输出，本拍稳定）
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)     act <= '0;
        else if (i_sw)  act <= o_out_rsv_data;
    end
    assign o_work_data = act;

endmodule
