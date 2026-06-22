module systolic_array #(
    parameter int ROW_N                 = 16    ,
    parameter int COL_N                 = 16    ,
    parameter int A_W                   = 8     ,
    parameter int B_W                   = 8     ,
    parameter int OUT_W                 = 32
) (
    input                               clk     ,
    input                               rst_n   ,
    input   logic                       a_vld     [ROW_N],
    input   logic [ A_W     - 1: 0]     a         [ROW_N],
    input   logic                       b_sw      [ROW_N],
    output  logic                       b_rdy     [COL_N],
    input   logic                       b_vld     [COL_N],
    input   logic [ B_W     - 1: 0]     b         [COL_N],
    output  logic                       out_vld   [COL_N],
    output  logic [OUT_W    - 1: 0]     out       [COL_N]
);

logic [ OUT_W   - 1: 0]                 pe_c      [ROW_N  ][COL_N  ];
logic [ OUT_W   - 1: 0]                 pe_out    [ROW_N  ][COL_N  ];
logic                                   pe_out_vld[ROW_N  ][COL_N  ];
logic [ A_W     - 1: 0]                 a_buf     [ROW_N  ][COL_N+1];
logic                                   a_buf_vld [ROW_N  ][COL_N+1];
logic                                   a_buf_rdy [ROW_N  ][COL_N+1];
logic [ B_W     - 1: 0]                 b_buf     [ROW_N+1][COL_N  ];
logic                                   b_buf_vld [ROW_N+1][COL_N  ];
logic                                   b_buf_rdy [ROW_N+1][COL_N  ];
logic                                   b_sw_grid [ROW_N  ][COL_N  ];
logic [ B_W     - 1: 0]                 b_work    [ROW_N  ][COL_N  ];      // weight_buf active 权重 → 喂 PE 的 .b

genvar row_a;
generate
    for(row_a = 0;row_a < ROW_N; row_a = row_a + 1)begin : g_a_edge
        assign a_buf[row_a][0]          = a[row_a];
        assign a_buf_vld[row_a][0]      = a_vld[row_a];      // 左边缘注入 valid（随数据右传）
        assign a_buf_rdy[row_a][COL_N]  = 1'b1;          // a 纯 valid 无反压：尾端 ready 恒高
    end
endgenerate

genvar col_b;
generate
    for(col_b = 0;col_b < COL_N; col_b = col_b + 1)begin : g_b_edge
        assign b_buf_vld[0][col_b]  = b_vld[col_b];
        assign b_buf[0][col_b]      = b[col_b];
        assign b_rdy[col_b]         = b_buf_rdy[0][col_b];
        assign b_buf_rdy[ROW_N][col_b] = 1'b0;   // 底行 shadow 无下游：ready=!满，权重沉底即停
    end
endgenerate

// _shift_right(bsw_grid) sa.py：b_sw 左边缘 [ROW_N] 注入，每拍右移一格成对角波（SA_array_design §11）
// 第 0 列也打 1 拍 FF（跟 a 的 data_buf 第 0 列 FF 对齐），否则 a 延 1 拍 b_sw 不延，
// PE 同拍看到的激活和 swap 命令对不上（错位 1 拍）。
genvar sw_row,sw_col;
generate
    for(sw_row = 0;sw_row < ROW_N; sw_row = sw_row + 1)begin : g_bsw_row
        always_ff @(posedge clk or negedge rst_n)begin
            if(!rst_n)begin
                b_sw_grid[sw_row][0] <= 1'b0;
            end else begin
                b_sw_grid[sw_row][0] <= b_sw[sw_row];     // 第 0 列也打拍
            end
        end
        for(sw_col = 1;sw_col < COL_N; sw_col = sw_col + 1)begin : g_bsw_col
            always_ff @(posedge clk or negedge rst_n)begin
                if(!rst_n)begin
                    b_sw_grid[sw_row][sw_col] <= 1'b0;
                end else begin
                    b_sw_grid[sw_row][sw_col] <= b_sw_grid[sw_row][sw_col-1];
                end
            end
        end
    end
endgenerate


// _route_acc(acc_d) sa.py
genvar row_c,col_c;
generate
    for(row_c = 0;row_c < ROW_N; row_c = row_c + 1)begin : g_acc_row
        for(col_c = 0;col_c < COL_N; col_c = col_c + 1)begin : g_acc_col
            if(row_c==0)begin : g_acc_top
                assign pe_c[row_c][col_c] = '0;
            end else begin : g_acc_inner
                assign pe_c[row_c][col_c] = pe_out[row_c-1][col_c];
            end

        end
    end
endgenerate

// sa.data[M-1]	sa.py
genvar col_out;
generate
    for(col_out = 0;col_out < COL_N; col_out = col_out + 1)begin : g_out_drive
        assign out[col_out]     = pe_out[ROW_N-1][col_out];
        assign out_vld[col_out] = pe_out_vld[ROW_N-1][col_out];
    end
endgenerate

genvar row,col;
generate
    for(row = 0;row < ROW_N; row = row + 1)begin : g_pe_row
        for(col = 0;col < COL_N; col = col + 1)begin : g_pe_col

            pe #(
                .A_W            (A_W                ),
                .B_W            (B_W                ),
                .OUT_W          (OUT_W              ),
                .PIPE_MUL       (1'b1               ),
                .SIGNED         (1'b1               )
            )u_pe(
                .clk            (clk                    ),
                .rst_n          (rst_n                  ),
                .a              (a_buf[row][col+1]      ),
                .a_vld          (a_buf_vld[row][col+1]  ),
                .b              (b_work[row][col]       ),
                .c              (pe_c[row][col]         ),
                .result         (pe_out[row][col]       ),
                .result_vld     (pe_out_vld[row][col]   ),
                .overflow       ()
            );

            data_buf #(
                .DATA_W         (A_W                )
            )u_a_buf(
                .clk            (clk                ),
                .rst_n          (rst_n              ),
                // _route_a() sa.py
                .i_in_valid     (a_buf_vld[row][col]),
                .o_in_ready     (a_buf_rdy[row][col]),
                .i_in_data      (a_buf[row][col]    ),
                .o_out_valid    (a_buf_vld[row][col+1]),
                .i_out_ready    (a_buf_rdy[row][col+1]),
                .o_out_data     (a_buf[row][col+1]  ),
                .i_clr          (1'b0)
            );

            weight_buf #(
                .DATA_W         (B_W                )
            )u_b_buf(
                .clk            (clk                ),
                .rst_n          (rst_n              ),
                // _route_b() sa.py：shadow 下沉填充链
                .i_in_rsv_vld   (b_buf_vld[row][col]),
                .o_in_rsv_rdy   (b_buf_rdy[row][col]),
                .i_in_rsv_data  (b_buf[row][col]    ),

                .o_out_rsv_vld  (b_buf_vld[row+1][col]),
                .i_out_rsv_rdy  (b_buf_rdy[row+1][col]),
                .o_out_rsv_data (b_buf[row+1][col]  ),

                // active 权重 → PE，i_sw 对角波切换 active<=shadow
                .o_work_data    (b_work[row][col]   ),
                .i_sw           (b_sw_grid[row][col])
            );
        end
    end
endgenerate
    
endmodule