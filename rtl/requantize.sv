// -----------------------------------------------------------------------------
// requantize
// -----------------------------------------------------------------------------
// Fixed-throughput requantization pipeline.
//
// Numerical behavior:
//   prod    = i_y * cfg_m0
//   shifted = (prod + 2^(cfg_shift-1)) >>> cfg_shift
//   o_data  = clip(shifted, cfg_out_min, cfg_out_max)
//
// cfg_shift == 0:
//   shifted = prod
//
// The rounding behavior intentionally matches:
//   (prod + (1 << (shift - 1))) >> shift
// This means the same positive rounding bias is added for both positive and
// negative products.
//
// Pipeline:
//   stg1: signed multiplication
//   stg2: rounding bias + arithmetic right shift
//   stg3: saturation / ReLU clipping
//
// Interface behavior:
//   - No ready/backpressure signal.
//   - At most one input is accepted per cycle when i_valid is high.
//   - Output latency is fixed at 3 cycles.
//   - After pipeline fill, throughput is one output per cycle.
//   - cfg_* must remain unchanged from the first accepted input of a layer until
//     o_idle becomes high after the final output leaves the pipeline.
//
// Typical configurations:
//   signed INT8: cfg_out_min = 8'sh80, cfg_out_max = 8'sh7f
//   INT8 + ReLU: cfg_out_min = 8'sd0,  cfg_out_max = 8'sh7f
// -----------------------------------------------------------------------------

module requantize #(
    parameter int unsigned Y_W = 32,
    parameter int unsigned M_W = 32,
    parameter int unsigned OUT_W = 8,

    // Must represent all supported shifts from 0 through Y_W + M_W.
    parameter int unsigned SHIFT_W = $clog2(Y_W + M_W + 1)
) (
    input  logic                          clk,
    input  logic                          rst_n,

    input  logic                          i_valid,
    input  logic signed [Y_W-1:0]         i_y,

    // Layer configuration. Keep stable while o_idle is low.
    input  logic signed [M_W-1:0]         cfg_m0,
    input  logic        [SHIFT_W-1:0]     cfg_shift,
    input  logic signed [OUT_W-1:0]       cfg_out_min,
    input  logic signed [OUT_W-1:0]       cfg_out_max,

    output logic                          o_valid,
    output logic signed [OUT_W-1:0]       o_data,
    output logic                          o_idle
);

    localparam int unsigned PROD_W = Y_W + M_W;

    // One extra bit prevents overflow when the positive rounding bias is added.
    // The max expression also guarantees that output limits can be sign-extended.
    localparam int unsigned CALC_W =
        ((PROD_W + 1) > (OUT_W + 1)) ? (PROD_W + 1) : (OUT_W + 1);

    localparam logic signed [CALC_W-1:0] CALC_ONE =
        {{(CALC_W-1){1'b0}}, 1'b1};


    logic                              valid_stg1;
    logic signed [PROD_W-1:0]          prod_stg1;

    logic signed [PROD_W-1:0]          prod_comb;

    logic                              valid_stg2;
    logic signed [CALC_W-1:0]          shifted_stg2;

    logic signed [CALC_W-1:0]          prod_ext_comb;
    logic signed [CALC_W-1:0]          round_bias_comb;
    logic signed [CALC_W-1:0]          rounded_comb;
    logic signed [CALC_W-1:0]          shifted_comb;

    logic                              valid_stg3;
    logic signed [OUT_W-1:0]           data_stg3;

    logic signed [CALC_W-1:0]          out_min_ext_comb;
    logic signed [CALC_W-1:0]          out_max_ext_comb;
    logic signed [OUT_W-1:0]           saturated_comb;

    always_comb begin
        prod_ext_comb = {
            {(CALC_W-PROD_W){prod_stg1[PROD_W-1]}},
            prod_stg1
        };

        round_bias_comb = '0;

        if (cfg_shift != '0) begin
            round_bias_comb =
                CALC_ONE <<< ($unsigned(cfg_shift) - 1'b1);
        end

        rounded_comb = prod_ext_comb + round_bias_comb;
        shifted_comb = rounded_comb >>> cfg_shift;
    end

    // -------------------------------------------------------------------------
    // Stage 3: saturation / ReLU clipping
    // -------------------------------------------------------------------------
    always_comb begin
        out_min_ext_comb = {
            {(CALC_W-OUT_W){cfg_out_min[OUT_W-1]}},
            cfg_out_min
        };

        out_max_ext_comb = {
            {(CALC_W-OUT_W){cfg_out_max[OUT_W-1]}},
            cfg_out_max
        };

        if (shifted_stg2 > out_max_ext_comb) begin
            saturated_comb = cfg_out_max;
        end
        else if (shifted_stg2 < out_min_ext_comb) begin
            saturated_comb = cfg_out_min;
        end
        else begin
            saturated_comb = shifted_stg2[OUT_W-1:0];
        end
    end

    // -------------------------------------------------------------------------
    // Fixed-throughput pipeline registers
    // -------------------------------------------------------------------------

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            valid_stg1   <= 1'b0;
            valid_stg2   <= 1'b0;
            valid_stg3   <= 1'b0;

            prod_stg1    <= '0;
            shifted_stg2 <= '0;
            data_stg3    <= '0;
        end
        else begin
            valid_stg1 <= i_valid;
            valid_stg2 <= valid_stg1;
            valid_stg3 <= valid_stg2;

            if (i_valid) begin
                prod_stg1 <= $signed(i_y) * $signed(cfg_m0);
            end

            if (valid_stg1) begin
                shifted_stg2 <= shifted_comb;
            end

            if (valid_stg2) begin
                data_stg3 <= saturated_comb;
            end
        end
    end

    assign o_valid = valid_stg3;
    assign o_data  = data_stg3;
    assign o_idle  = ~(valid_stg1 | valid_stg2 | valid_stg3);

endmodule
