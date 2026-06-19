// pipe_reg — Generic pipeline stage register
//
// Instantiate one per pipeline boundary to separate stages with a
// standard valid/ready handshake and flush support.
//
// Handshake model (1-entry registered buffer):
//   Input side : i_in_valid  — producer has valid data this cycle
//                o_in_ready  — this stage can accept (ready when not full OR downstream ready)
//   Output side: o_out_valid — this stage has valid data for consumer (= isfull, always registered)
//                i_out_ready — consumer is ready to accept
//
//   isfull tracks whether the internal buffer holds valid data.
//   o_out_valid is purely registered (= isfull); there is no combinational
//   transparent path from input to output.
//
// isfull register:
//   set  : wr & !rd  — write occurs without simultaneous read.
//   clear: i_flush, or rd & !wr (downstream reads, upstream has no new data).
//
// Data register:
//   Latches i_in_data whenever a write occurs (wr = o_in_ready & i_in_valid).
//   o_out_data is always driven from the registered buffer.
//
// Flush:
//   i_flush (synchronous, active-high) clears isfull and o_out_data,
//   discarding any in-flight buffered data.
//
// Usage:
//   Pack all signals crossing the stage boundary into i_in_data/o_out_data.
//   Caller slices o_out_data back into individual fields.
//
//   Example (ID→EX boundary, DATA_W = PC_W + 1 + 5 + 32 + 32 + 32 + 14):
//     buf #(.DATA_W(ID_EX_W)) u_id_ex_reg (
//         .i_in_valid  (idu_valid),
//         .o_in_ready  (idu_ready),
//         .i_in_data   ({pc, rd_wen, rd_idx, rs1, rs2, imm, info_bus}),
//         .o_out_valid (id_ex_valid),
//         .i_out_ready (exu_ready),
//         .o_out_data  (id_ex_data),
//         .i_flush     (flush_id2ex)
//     );

module data_buf #(
    parameter DATA_W                = 32
) (
    input  logic                    clk,
    input  logic                    rst_n,

    // ── input side ───────────────────────────────────────────
    input  logic                    i_in_valid,
    output logic                    o_in_ready,
    input  logic [DATA_W-1:0]       i_in_data,

    // ── output side ──────────────────────────────────────────
    output logic                    o_out_valid,
    input  logic                    i_out_ready,
    output logic [DATA_W-1:0]       o_out_data,

    // ── control ──────────────────────────────────────────────
    input  logic                    i_clr
);
// ── internal signals ─────────────────────────────────────────────────────
logic                   isfull;
logic                   clr, wr, rd, set_full;

// ── logic ─────────────────────────────────────────────────────────────────
assign o_in_ready = i_out_ready | !isfull;
assign clr        = i_clr | (!wr & rd);
assign wr         = (o_in_ready & i_in_valid);
assign rd         = i_out_ready & isfull;
assign set_full   = wr & !rd;
assign o_out_valid = isfull;
// isfull: set when writing without simultaneous read; cleared on flush/drain
DFF_RST_EN_CLR #(
    .DATA_W     (1),
    .RST_VALUE  ('0)
) u_isfull (
    .clk        (clk),
    .rst_n      (rst_n),
    .clr        (clr),
    .en         (set_full),
    .d          (1'b1),
    .q          (isfull)
);

// data buffer: latches on every accepted write
//DFF_RST_EN_CLR #(
DFF_RST_EN #(
    .DATA_W     (DATA_W),
    .RST_VALUE  ('0)
) u_data_buf (
    .clk        (clk),
    .rst_n      (rst_n),
    .en         (wr),
    .d          (i_in_data),
    .q          (o_out_data)
);
endmodule
