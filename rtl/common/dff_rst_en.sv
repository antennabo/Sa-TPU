module DFF_RST_EN #(
    parameter  DATA_W = 32,
    parameter  RST_VALUE  = {DATA_W{1'b0}}
) (
    input                             clk,// clk
    input                             rst_n,   
    input                             en,
    input       [DATA_W-1:0]      d,// input data

    output reg  [DATA_W-1:0]      q//flipflop out   
);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            q <= RST_VALUE;
        else if (en)
            q <= d;
    end

endmodule