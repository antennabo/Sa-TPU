import torch
from ir import Conv2dIR, MatMulIR

def export_to_ir(exported, dtype="fp32") -> list:
    irs = []
    graph = exported.graph

    for node in graph.nodes:
        if node.op != "call_function":
            continue

        name = node.target.__name__ if hasattr(node.target, "__name__") else str(node.target)

        if "conv2d" in name:
            # 从 node.meta["val"].shape 取输出 shape
            # 从参数节点的 meta["val"].shape 取权重 shape
            W_node = node.args[1]
            out_ch, in_ch, kH, kW = W_node.meta["val"].shape
            out_shape = node.meta["val"].shape  # [N, out_ch, H, W]
            irs.append(Conv2dIR(
                op_type="conv2d", dtype=dtype, accum_dtype="fp32",
                N=out_shape[0], H=out_shape[2], W=out_shape[3],
                C=in_ch, K=out_ch, R=kH, S=kW,
            ))

        elif "linear" in name:
            W_node = node.args[1]
            N_out, K = W_node.meta["val"].shape
            M = node.args[0].meta["val"].shape[0]
            irs.append(MatMulIR(
                op_type="linear", dtype=dtype, accum_dtype="fp32",
                M=M, N=N_out, K=K,
            ))

    return irs