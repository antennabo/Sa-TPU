module DFF_RST_EN_CLR #(
    parameter  DATA_W = 32,
    parameter  RST_VALUE  = {DATA_W{1'b0}}
) (
    input                               clk,  
    input                               rst_n, 
    input                               clr,
    input                               en,  
    input       [DATA_W-1:0]            d,     

    output reg  [DATA_W-1:0]            q     
);

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)begin
            q <= RST_VALUE;
        end else if (clr)begin
            q <= 'h0;
        end else if (en)begin
            q <= d;
        end
    end

endmodule