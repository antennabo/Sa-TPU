module pingpong_buf #(
    parameter DATA_W = 8
) (
    input  logic                    clk,
    input  logic                    rst_n,

    // ── input side ───────────────────────────────────────────
    input  logic                    i_in_rsv_vld,
    output logic                    o_in_rsv_rdy,
    input  logic [DATA_W-1:0]       i_in_rsv_data,

    // ── output side ──────────────────────────────────────────
    output logic                    o_out_rsv_vld,
    input  logic                    i_out_rsv_rdy,
    output logic [DATA_W-1:0]       o_out_rsv_data,

    output logic                    o_work_vld,
    output logic [DATA_W-1:0]       o_work_data,
    // ── control ──────────────────────────────────────────────
    input  logic                    i_sw,
    input  logic                    i_clr
);
    logic                   ping_clr, pong_clr;
    logic                   ping_in_valid, pong_in_valid;
    logic                   ping_in_ready, pong_in_ready;
    logic [DATA_W-1:0]      ping_in_data, pong_in_data;
    logic                   ping_out_valid, pong_out_valid;
    logic                   ping_out_ready, pong_out_ready;
    logic [DATA_W-1:0]      ping_out_data, pong_out_data;

    assign ping_clr = i_sw & i_clr;
    assign pong_clr = ~i_sw & i_clr;

    // i_sw==0: ping = fill(input) side, pong = drain(output) side
    // i_sw==1: pong = fill(input) side, ping = drain(output) side
    assign ping_in_valid = (i_sw)? 1'b0          : i_in_rsv_vld;
    assign pong_in_valid = (i_sw)? i_in_rsv_vld  : 1'b0;
    assign ping_in_data  = (i_sw)? '0            : i_in_rsv_data;
    assign pong_in_data  = (i_sw)? i_in_rsv_data : '0;

    assign ping_out_ready = (i_sw)? i_out_rsv_rdy : 1'b0;
    assign pong_out_ready = (i_sw)? 1'b0          : i_out_rsv_rdy;

    assign o_in_rsv_rdy   = (i_sw)? pong_in_ready  : ping_in_ready;
    assign o_out_rsv_vld  = (i_sw)? ping_out_valid : pong_out_valid;
    assign o_out_rsv_data = (i_sw)? ping_out_data  : pong_out_data;

    data_buf #(
        .DATA_W         (DATA_W)
    ) u_ping_buf (
        .clk            (clk            ),
        .rst_n          (rst_n          ),
        .i_in_valid     (ping_in_valid  ),
        .o_in_ready     (ping_in_ready  ),
        .i_in_data      (ping_in_data   ),
        .o_out_valid    (ping_out_valid ),
        .i_out_ready    (ping_out_ready ),
        .o_out_data     (ping_out_data  ),
        .i_clr          (ping_clr       )
    );

    data_buf #(
        .DATA_W         (DATA_W         )
    ) u_pong_buf (
        .clk            (clk            ),
        .rst_n          (rst_n          ),
        .i_in_valid     (pong_in_valid  ),
        .o_in_ready     (pong_in_ready  ),
        .i_in_data      (pong_in_data   ),
        .o_out_valid    (pong_out_valid ),
        .i_out_ready    (pong_out_ready ),
        .o_out_data     (pong_out_data  ),
        .i_clr          (pong_clr       )
    );


endmodule
