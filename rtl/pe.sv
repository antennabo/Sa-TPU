// mac
// Integer multiply-add with configurable operand width and output width.
//
// Operation:
//   Every cycle, compute result = sat(a * b + c)
//   overflow = 1 when the internal sum exceeds the OUT_W range and saturation
//              is applied
//
// Notes:
//   - Integer only; no floating-point support.
//   - Registered output, free-running (no handshake / backpressure).
//   - SIGNED = 0: unsigned multiply-add
//   - SIGNED = 1: two's-complement signed multiply-add
//   - SIGNED is an elaboration-time parameter, so synthesis keeps only one path.
//   - The final add uses saturation instead of wrap-around.

module pe #(
    parameter int A_W       = 8,
    parameter int B_W       = 8,
    parameter int OUT_W     = 32,
    parameter bit PIPE_MUL  = 1'b1,
    parameter bit SIGNED    = 1'b0
)(
    input  logic                clk,
    input  logic                rst_n,
    input  logic [A_W-1:0]      a,
    input  logic [B_W-1:0]      b,
    input  logic [OUT_W-1:0]    c,
    output logic [OUT_W-1:0]    result,
    output logic                overflow
);

localparam int MUL_W = A_W + B_W;
localparam int SUM_W = ((MUL_W > OUT_W) ? MUL_W : OUT_W) + 1;

logic [OUT_W-1:0]    result_calc;
logic                overflow_calc;

generate
    if (SIGNED) begin : gen_signed
        logic signed [MUL_W-1:0]    product_full;
        logic signed [SUM_W-1:0]    product_ext;
        logic signed [SUM_W-1:0]    c_ext;
        logic signed [SUM_W-1:0]    result_full;
        logic signed [OUT_W-1:0]    sat_max;
        logic signed [OUT_W-1:0]    sat_min;
        logic signed [SUM_W-1:0]    sat_max_ext;
        logic signed [SUM_W-1:0]    sat_min_ext;
        logic                       sat_hi;
        logic                       sat_lo;

        // Compute the full-precision multiply-add in signed domain.
        if(PIPE_MUL==1'b1)begin
            always_ff @(posedge clk)begin
                product_full <= $signed(a) * $signed(b);
            end
        end else begin
            assign product_full = $signed(a) * $signed(b);
        end
        assign product_ext = {{(SUM_W-MUL_W){product_full[MUL_W-1]}}, product_full};
        assign c_ext       = {{(SUM_W-OUT_W){c[OUT_W-1]}}, c};
        assign result_full = product_ext + c_ext;

        // Saturation bounds for OUT_W signed two's-complement output.
        assign sat_max     = {1'b0, {(OUT_W-1){1'b1}}};
        assign sat_min     = {1'b1, {(OUT_W-1){1'b0}}};
        assign sat_max_ext = {{(SUM_W-OUT_W){1'b0}}, sat_max};
        assign sat_min_ext = {{(SUM_W-OUT_W){1'b1}}, sat_min};
        assign sat_hi      = (result_full > sat_max_ext);
        assign sat_lo      = (result_full < sat_min_ext);

        assign overflow_calc = sat_hi | sat_lo;
        assign result_calc = sat_hi ? sat_max :
                             sat_lo ? sat_min :
                             result_full[OUT_W-1:0];
    end else begin : gen_unsigned
        logic [MUL_W-1:0]         product_full;
        logic [SUM_W-1:0]         product_ext;
        logic [SUM_W-1:0]         c_ext;
        logic [SUM_W-1:0]         result_full;
        logic                     sat_hi;

        // Compute the full-precision multiply-add in unsigned domain.
        if(PIPE_MUL==1'b1)begin
            always_ff @(posedge clk)begin
                product_full <= a * b;
            end
        end else begin
            assign product_full =a * b;
        end
        assign product_ext = {{(SUM_W-MUL_W){1'b0}}, product_full};
        assign c_ext       = {{(SUM_W-OUT_W){1'b0}}, c};
        assign result_full = product_ext + c_ext;

        // Any bit above OUT_W-1 means the sum exceeded the unsigned range.
        assign sat_hi    = |result_full[SUM_W-1:OUT_W];
        assign overflow_calc  = sat_hi;
        assign result_calc = sat_hi ? {OUT_W{1'b1}} :
                             result_full[OUT_W-1:0];
    end
endgenerate

always_ff @(posedge clk or negedge rst_n) begin : output_reg
    if (!rst_n) begin
        result    <= '0;
        overflow  <= 1'b0;
    end else begin
        result   <= result_calc;
        overflow <= overflow_calc;
    end
end

endmodule
