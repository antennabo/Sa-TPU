import torch
from ir import Conv2dIR, MatMulIR, ElementwiseIR

def export_to_ir(exported, dtype="fp32") -> list:
    irs = []
    graph = exported.graph
    state_dict = exported.state_dict
    # 建立图参数名 → state_dict key 的映射
    # 例：p_feature_0_weight → feature.0.weight
    param_map = {
        spec.arg.name: spec.target
        for spec in exported.graph_signature.input_specs
        if spec.kind.name == "PARAMETER"
    }

    for node in graph.nodes:
        if node.op != "call_function":
            continue

        name = node.target.__name__ if hasattr(node.target, "__name__") else str(node.target)

        if "conv2d" in name:
            W_node = node.args[1]
            b_node = node.args[2] if len(node.args) > 2 else None

            out_ch, in_ch, kH, kW = W_node.meta["val"].shape
            out_shape = node.meta["val"].shape  # [N, out_ch, H, W]

            W = state_dict[param_map[W_node.target]].detach().numpy()
            b = state_dict[param_map[b_node.target]].detach().numpy() if b_node else None

            irs.append(Conv2dIR(
                op_type="conv2d", dtype=dtype, accum_dtype="fp32",
                N=out_shape[0], H=out_shape[2], W=out_shape[3],
                C=in_ch, K=out_ch, R=kH, S=kW,
                input_weight=W, bias=b,
            ))

        elif "linear" in name:
            W_node = node.args[1]
            b_node = node.args[2] if len(node.args) > 2 else None

            N_out, K = W_node.meta["val"].shape
            M = node.args[0].meta["val"].shape[0]

            W = state_dict[param_map[W_node.target]].detach().numpy()
            b = state_dict[param_map[b_node.target]].detach().numpy() if b_node else None

            irs.append(MatMulIR(
                op_type="linear", dtype=dtype, accum_dtype="fp32",
                M=M, N=N_out, K=K,
                input_weight=W, bias=b,
            ))
        elif "relu" in name:
            shape = tuple(node.meta["val"].shape)
            irs.append(ElementwiseIR(op_type="elementwise", dtype="fp32", op="relu", shape=shape))

        elif "max_pool" in name:
            shape = tuple(node.meta["val"].shape)
            irs.append(ElementwiseIR(op_type="elementwise", dtype="fp32", op="maxpool", shape=shape))

        elif "view" in name:
            shape = tuple(node.meta["val"].shape)
            irs.append(ElementwiseIR(op_type="elementwise", dtype="fp32", op="flatten", shape=shape))
    return irs