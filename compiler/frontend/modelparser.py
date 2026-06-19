from .ir import Conv2dIR, MatMulIR, ElementwiseIR


class modelparser:
    def __init__(self, dtype="fp32", accum_dtype="fp32"):
        self.dtype = dtype
        self.accum_dtype = accum_dtype

    def export(self, exported) -> list:
        irs = []
        graph = exported.graph

        for node in graph.nodes:
            if node.op != "call_function":
                continue

            name = node.target.__name__ if hasattr(node.target, "__name__") else str(node.target)

            if "conv2d" in name:
                W_node = node.args[1]
                out_ch, in_ch, kH, kW = W_node.meta["val"].shape
                out_shape = node.meta["val"].shape  # [N, out_ch, H, W]

                irs.append(Conv2dIR(
                    op_type="conv2d", dtype=self.dtype, accum_dtype=self.accum_dtype,
                    N=out_shape[0], H=out_shape[2], W=out_shape[3],
                    C=in_ch, K=out_ch, R=kH, S=kW,
                ))

            elif "linear" in name:
                W_node = node.args[1]
                N_out, K = W_node.meta["val"].shape
                M = node.args[0].meta["val"].shape[0]

                irs.append(MatMulIR(
                    op_type="linear", dtype=self.dtype, accum_dtype=self.accum_dtype,
                    M=M, N=N_out, K=K,
                ))

            elif "relu" in name:
                shape = tuple(node.meta["val"].shape)
                irs.append(ElementwiseIR(op_type="elementwise", dtype="fp32", op="relu", shape=shape))

            elif "max_pool" in name:
                shape = tuple(node.meta["val"].shape)
                irs.append(ElementwiseIR(op_type="elementwise", dtype="fp32", op="maxpool", shape=shape))

            # elif "view" in name:
            elif "view" in name or "flatten" in name:
                shape = tuple(node.meta["val"].shape)
                irs.append(ElementwiseIR(op_type="elementwise", dtype="fp32", op="flatten", shape=shape))

        return irs

    def print_irs(self, irs: list):
        for i, op in enumerate(irs):
            if isinstance(op, Conv2dIR):
                print(f"  [{i}] Conv2d  N={op.N} H={op.H} W={op.W} C={op.C} K={op.K} R={op.R} S={op.S} dtype={op.dtype}/{op.accum_dtype}")
            elif isinstance(op, MatMulIR):
                print(f"  [{i}] MatMul  M={op.M} N={op.N} K={op.K} dtype={op.dtype}/{op.accum_dtype}")
            elif isinstance(op, ElementwiseIR):
                print(f"  [{i}] {op.op:<10} shape={op.shape}")