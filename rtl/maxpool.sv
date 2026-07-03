// -----------------------------------------------------------------------------
// maxpool_pipe
// -----------------------------------------------------------------------------
// Streaming max-pooling datapath with a fixed LANE_N-wide input/output interface.
//
// Data layout:
//   - Lane direction is the vertical dimension.
//   - Consecutive valid cycles are the horizontal dimension.
//
// Modes:
//   MODE_BYPASS:
//     LANE_N input lanes -> LANE_N output lanes every valid cycle.
//
//   MODE_2X2:
//     Vertical:   LANE_N -> LANE_N/2 using adjacent-lane comparisons.
//     Horizontal: accumulate two consecutive valid columns.
//     Output:     lower LANE_N/2 lanes are valid.
//
//   MODE_4X4:
//     Vertical stg1: LANE_N   -> LANE_N/2.
//     Vertical stg2: LANE_N/2 -> LANE_N/4.
//     Horizontal:    accumulate four consecutive valid columns.
//     Output:        lower LANE_N/4 lanes are valid.
//
// The pooling stride is equal to the pooling size. Overlapping windows are not
// generated. i_valid may contain bubbles; only valid cycles advance a window.
//
// cfg_pool_mode must remain unchanged until o_idle is asserted.
// -----------------------------------------------------------------------------

module maxpool_pipe #(
    parameter int unsigned DATA_W = 8,
    parameter int unsigned LANE_N = 8
) (
    input  logic                         clk,
    input  logic                         rst_n,

    // Layer configuration. Keep stable while o_idle == 0.
    input  logic [1:0]                   cfg_pool_mode,

    // Fixed-throughput input. There is no ready/backpressure signal.
    input  logic                         i_valid,
    input  logic signed [DATA_W-1:0]     i_data [0:LANE_N-1],

    // Output width always equals input width. o_lane_mask marks valid lanes.
    output logic                         o_valid,
    output logic signed [DATA_W-1:0]     o_data [0:LANE_N-1],
    output logic        [LANE_N-1:0]     o_lane_mask,

    // High only when no pipeline data or partial pooling window remains.
    output logic                         o_idle
);

    localparam logic [1:0] MODE_BYPASS = 2'd0;
    localparam logic [1:0] MODE_2X2    = 2'd1;
    localparam logic [1:0] MODE_4X4    = 2'd2;

    localparam int unsigned POOL2_LANE_N = LANE_N / 2;
    localparam int unsigned POOL4_LANE_N = LANE_N / 4;

    localparam logic [LANE_N-1:0] MASK_BYPASS = {LANE_N{1'b1}};
    localparam logic [LANE_N-1:0] MASK_2X2    = {LANE_N{1'b1}} >> (LANE_N - POOL2_LANE_N);
    localparam logic [LANE_N-1:0] MASK_4X4    = {LANE_N{1'b1}} >> (LANE_N - POOL4_LANE_N);

    function automatic logic signed [DATA_W-1:0] max_signed (
        input logic signed [DATA_W-1:0] lhs,
        input logic signed [DATA_W-1:0] rhs
    );
        max_signed = (lhs >= rhs) ? lhs : rhs;
    endfunction

    // -------------------------------------------------------------------------
    // Registered signals: driven by exactly one always_ff block each.
    // -------------------------------------------------------------------------
    logic                     valid_stg1;
    logic signed [DATA_W-1:0] data_stg1 [0:POOL2_LANE_N-1];

    logic                     valid_stg2;
    logic signed [DATA_W-1:0] data_stg2 [0:POOL4_LANE_N-1];

    logic [1:0]               count;
    logic signed [DATA_W-1:0] horizontal_max_2x2 [0:POOL2_LANE_N-1];
    logic signed [DATA_W-1:0] horizontal_max_4x4 [0:POOL4_LANE_N-1];

    logic                     valid_stg3;
    logic signed [DATA_W-1:0] data_stg3 [0:LANE_N-1];
    logic        [LANE_N-1:0] lane_mask_stg3;

    // -------------------------------------------------------------------------
    // Block 1 — Vertical stg1: adjacent-lane pairs, shared by 2x2 and 4x4.
    // Drives: valid_stg1, data_stg1[].
    // -------------------------------------------------------------------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            valid_stg1 <= 1'b0;
            for (int lane = 0; lane < POOL2_LANE_N; lane++)
                data_stg1[lane] <= '0;
        end
        else begin
            valid_stg1 <= i_valid && (cfg_pool_mode != MODE_BYPASS);

            if (i_valid && (cfg_pool_mode != MODE_BYPASS)) begin
                for (int lane = 0; lane < POOL2_LANE_N; lane++)
                    data_stg1[lane] <= max_signed(i_data[2*lane], i_data[2*lane+1]);
            end
        end
    end

    // -------------------------------------------------------------------------
    // Block 2 — Vertical stg2: groups of four lanes, 4x4 mode only.
    // Drives: valid_stg2, data_stg2[].
    // -------------------------------------------------------------------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            valid_stg2 <= 1'b0;
            for (int lane = 0; lane < POOL4_LANE_N; lane++)
                data_stg2[lane] <= '0;
        end
        else begin
            valid_stg2 <= valid_stg1 && (cfg_pool_mode == MODE_4X4);

            if (valid_stg1 && (cfg_pool_mode == MODE_4X4)) begin
                for (int lane = 0; lane < POOL4_LANE_N; lane++)
                    data_stg2[lane] <= max_signed(data_stg1[2*lane], data_stg1[2*lane+1]);
            end
        end
    end

    // -------------------------------------------------------------------------
    // Block 3 — Horizontal state: counter + running-maximum accumulators.
    // Drives: count, horizontal_max_2x2[], horizontal_max_4x4[].
    // Note: shared counter (0..1 for 2x2, 0..3 for 4x4). No side-effect from
    // BYPASS mode. Downstream (Block 4) reads NBA-driven count/accumulator
    // in the same cycle to decide emission.
    // -------------------------------------------------------------------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            count <= 2'd0;
            for (int lane = 0; lane < POOL2_LANE_N; lane++)
                horizontal_max_2x2[lane] <= '0;
            for (int lane = 0; lane < POOL4_LANE_N; lane++)
                horizontal_max_4x4[lane] <= '0;
        end
        else begin
            case (cfg_pool_mode)
                MODE_2X2: begin
                    if (valid_stg1) begin
                        if (count == 2'd0) begin
                            // First column: load accumulators.
                            for (int lane = 0; lane < POOL2_LANE_N; lane++)
                                horizontal_max_2x2[lane] <= data_stg1[lane];
                            count <= 2'd1;
                        end
                        else begin
                            // Second column: emit happens in Block 4; here just reset counter.
                            count <= 2'd0;
                        end
                    end
                end

                MODE_4X4: begin
                    if (valid_stg2) begin
                        case (count)
                            2'd0: begin
                                for (int lane = 0; lane < POOL4_LANE_N; lane++)
                                    horizontal_max_4x4[lane] <= data_stg2[lane];
                                count <= 2'd1;
                            end
                            2'd1, 2'd2: begin
                                for (int lane = 0; lane < POOL4_LANE_N; lane++)
                                    horizontal_max_4x4[lane] <=
                                        max_signed(horizontal_max_4x4[lane], data_stg2[lane]);
                                count <= count + 2'd1;
                            end
                            2'd3: begin
                                count <= 2'd0;
                            end
                            default: ;
                        endcase
                    end
                end

                default: ;  // MODE_BYPASS and undefined: no horizontal state change
            endcase
        end
    end

    // -------------------------------------------------------------------------
    // Block 4 — Output stage: pack output vector + mask + one-cycle valid pulse.
    // Drives: valid_stg3, data_stg3[], lane_mask_stg3.
    // Emit condition is the last horizontal column for the mode (reads count
    // and accumulators driven by Block 3; both are NBA so read is old value).
    // -------------------------------------------------------------------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            valid_stg3     <= 1'b0;
            lane_mask_stg3 <= '0;
            for (int lane = 0; lane < LANE_N; lane++)
                data_stg3[lane] <= '0;
        end
        else begin
            valid_stg3 <= 1'b0;

            case (cfg_pool_mode)
                MODE_BYPASS: begin
                    if (i_valid) begin
                        for (int lane = 0; lane < LANE_N; lane++)
                            data_stg3[lane] <= i_data[lane];
                        lane_mask_stg3 <= MASK_BYPASS;
                        valid_stg3     <= 1'b1;
                    end
                end

                MODE_2X2: begin
                    if (valid_stg1 && count == 2'd1) begin
                        // Second (final) column of the 2x2 window: emit.
                        for (int lane = 0; lane < LANE_N; lane++)
                            data_stg3[lane] <= (lane < POOL2_LANE_N)
                                ? max_signed(horizontal_max_2x2[lane], data_stg1[lane])
                                : '0;
                        lane_mask_stg3 <= MASK_2X2;
                        valid_stg3     <= 1'b1;
                    end
                end

                MODE_4X4: begin
                    if (valid_stg2 && count == 2'd3) begin
                        // Fourth (final) column of the 4x4 window: emit.
                        for (int lane = 0; lane < LANE_N; lane++)
                            data_stg3[lane] <= (lane < POOL4_LANE_N)
                                ? max_signed(horizontal_max_4x4[lane], data_stg2[lane])
                                : '0;
                        lane_mask_stg3 <= MASK_4X4;
                        valid_stg3     <= 1'b1;
                    end
                end

                default: ;
            endcase
        end
    end

    // -------------------------------------------------------------------------
    // Output connections.
    // -------------------------------------------------------------------------
    assign o_valid     = valid_stg3;
    assign o_data      = data_stg3;
    assign o_lane_mask = lane_mask_stg3;
    assign o_idle      = ~(valid_stg1 | valid_stg2 | valid_stg3 | (|count));

endmodule