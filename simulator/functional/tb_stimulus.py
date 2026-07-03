"""SaTPU tb 端 stimulus + golden 生成入口。

跟 numerical_model 分文件: 后者是纯 numpy 数学核心, 本文件负责
fp32 model → int8 → 文本文件的粘合, 依赖 torch (仅 driver 需要)。

设计取舍: **不用** torch.ao.quantization 的 quantized_model 路径。
- 原因: torch 的 quantized Linear 只支持 quint8 activation, 无法给出
  zp=0 的 signed int8, 与 SaTPU per-tensor symmetric qint8 契约冲突
  (scope.md:35, decisions.md D10)。
- 做法: 从 fp32 model 走前向, 用 hook 抓 fp32 activation, 自己按
  scale = max_abs / 127 做对称量化 (zp=0)。calibration 通过 fp32
  forward 遍历 loader 累计 max_abs 得稳定 scale。

Linear / Conv 差异只在"如何把权重和激活整成 (M,K) · (K,N) 矩阵乘"这一步;
之后的 matmul + bias + requant + 写文件由 _run_matmul_pipeline 统一处理。
"""

import os
import numpy as np
import torch

from utils import im2col
from simulator.functional.numerical_model import (
    NumericalModel,
    _sat_add_i32,
    compute_multiplier_shift,
    requantize_int32_to_int8,
)


def _write_matrix(path, mat, header_comment):
    """写入一个 2D 矩阵: 首行注释, 之后每行为空格分隔十进制数。"""
    with open(path, "w") as f:
        f.write(f"# {header_comment}\n")
        for row in mat:
            f.write(" ".join(str(int(v)) for v in row))
            f.write("\n")


def _write_vector(path, vec, header_comment):
    """写入一个 1D 数组: 首行注释, 之后一行一个数。"""
    with open(path, "w") as f:
        f.write(f"# {header_comment}\n")
        for v in vec:
            f.write(f"{int(v)}\n")


def _symmetric_qint8_scale(max_abs):
    """per-tensor symmetric qint8 scale: max_abs / 127, 底 clamp 到 1e-12 防除零。"""
    return max(float(max_abs), 1e-12) / 127.0


def _calibrate_and_capture(model, layer, calibration_loader, sample_x,
                           *, output_post_fn=None):
    """遍历 calibration_loader 累计 layer 输入 / 输出的 fp32 max_abs,
    并单独跑 sample_x 抓取那次的 fp32 输入 activation (原始 shape, 不 reshape)。

    output_post_fn: 可选 callable(torch.Tensor) → torch.Tensor, 在算 y_max 前应用,
                    用来把 layer 后续融合的 op (ReLU、MaxPool 等) 提前算进 scale_y 校准。
    return: (a_fp32_sample, a_max_calib, y_max_calib)
    """
    stats = {"a": 0.0, "y": 0.0, "a_max": 0.0, "y_max": 0.0, "a_sample": None}

    def _pre(_m, inputs):
        stats["a"] = float(inputs[0].detach().abs().max().item())
        stats["a_sample"] = inputs[0].detach().float().clone()

    def _post(_m, _inp, out):
        t = out.detach()
        if output_post_fn is not None:
            t = output_post_fn(t)
        stats["y"] = float(t.abs().max().item())

    h1 = layer.register_forward_pre_hook(_pre)
    h2 = layer.register_forward_hook(_post)
    try:
        with torch.no_grad():
            if calibration_loader is not None:
                for batch in calibration_loader:
                    x = batch[0] if isinstance(batch, (list, tuple)) else batch
                    model(x)
                    stats["a_max"] = max(stats["a_max"], stats["a"])
                    stats["y_max"] = max(stats["y_max"], stats["y"])
            # 最后跑 sample_x, 覆盖 a_sample 为 sample 的 activation
            model(sample_x)
            if calibration_loader is None:
                stats["a_max"] = stats["a"]
                stats["y_max"] = stats["y"]
            else:
                stats["a_max"] = max(stats["a_max"], stats["a"])
                stats["y_max"] = max(stats["y_max"], stats["y"])
    finally:
        h1.remove()
        h2.remove()

    return stats["a_sample"].numpy(), stats["a_max"], stats["y_max"]


def _fp32_bias_to_int32(bias_fp32, scale_b):
    """离线折算: bias_int32 = round(bias_fp32 / scale_b), 饱和到 int32。"""
    v = np.round(bias_fp32.astype(np.float64) / scale_b).astype(np.int64)
    return np.clip(v, -(1 << 31), (1 << 31) - 1).astype(np.int32)


def _run_matmul_pipeline(A_int8, B_int8, bias_int32, M0, shift,
                         out_dir, header, extra_params,
                         *, relu=False, AR=8, AC=8):
    """RTL 契约层 pipeline: 只接受硬件真收到的整数量, 走完整 MAC + bias + requant,
    写 6 个 txt 到 out_dir。fp32 → int32 折算 (bias, M0, shift) 在调用方 (dump_*) 做。

    - A_int8:     (M, K) int8
    - B_int8:     (K, N) int8, 已按 SA 面朝向排 (Linear: W.T; Conv: W.reshape(K_out, CRS).T)
    - bias_int32: (N,) int32, 已折算好 (round(bias_fp32 / (scale_x*scale_w)) + 饱和)
    - M0, shift:  requantize 定点参数 (compute_multiplier_shift 产出)
    - relu:       True 时 requant 的 out_min 从 -128 收到 0, 相当于把 ReLU 融进 requant unit
    - extra_params: dict, 追加到 params.txt (scale_x/w/y、a_max/y_max、conv 元信息 等)
    """
    assert A_int8.dtype == np.int8 and B_int8.dtype == np.int8
    assert bias_int32.dtype == np.int32

    # Pipeline: matmul → +bias (int32 sat) → requant [+relu 融合] → int8
    Y_int32 = NumericalModel.matmul(A_int8, B_int8, AR=AR, AC=AC)        # (M_pad, N_pad)
    M_pad, N_pad = Y_int32.shape
    K_pad = ((A_int8.shape[1] + AR - 1) // AR) * AR

    bias_pad = np.zeros(N_pad, dtype=np.int32)
    bias_pad[:bias_int32.shape[0]] = bias_int32
    Y_int32_biased = _sat_add_i32(Y_int32, bias_pad[np.newaxis, :])
    out_min = 0 if relu else -128
    Y_int8 = requantize_int32_to_int8(Y_int32_biased, M0, shift, out_min=out_min)

    A_pad = np.zeros((M_pad, K_pad), dtype=np.int8)
    A_pad[:A_int8.shape[0], :A_int8.shape[1]] = A_int8
    B_pad = np.zeros((K_pad, N_pad), dtype=np.int8)
    B_pad[:B_int8.shape[0], :B_int8.shape[1]] = B_int8

    full_header = f"M={M_pad} K={K_pad} N={N_pad} AR={AR} AC={AC} {header}"
    _write_matrix(os.path.join(out_dir, "A.txt"),        A_pad,           full_header)
    _write_matrix(os.path.join(out_dir, "B.txt"),        B_pad,           full_header)
    _write_vector(os.path.join(out_dir, "bias.txt"),     bias_pad,        full_header)
    _write_matrix(os.path.join(out_dir, "Y_int32.txt"),  Y_int32_biased,  full_header)
    _write_matrix(os.path.join(out_dir, "Y_int8.txt"),   Y_int8,          full_header)

    with open(os.path.join(out_dir, "params.txt"), "w") as f:
        f.write(f"# {full_header}\n")
        f.write(f"M0       {M0}\n")
        f.write(f"shift    {shift}\n")
        f.write(f"relu     {int(relu)}   # 1 = requant clip_min=0, 0 = clip_min=-128\n")
        for k, v in extra_params.items():
            f.write(f"{k:<8} {v}\n")
        f.write(f"note     per-tensor symmetric qint8, zp=0\n")

    return {
        "A": A_pad, "B": B_pad, "bias": bias_pad,
        "Y_int32": Y_int32_biased, "Y_int8": Y_int8,
        "M0": M0, "shift": shift,
    }


def dump_linear_layer(model, sample_x, layer_key, out_dir,
                      *, calibration_loader=None, post_relu=False,
                      input_int8_override=None, scale_x_override=None,
                      AR=8, AC=8):
    """从 fp32 nn.Linear 层生成 satpu_top_tb golden。

    post_relu: True 时把 ReLU 融进 requant 输出 clip (out_min=0), 同时用后置 ReLU 的
               fp32 输出做 y_max 校准, 拿到更紧的 scale_y。
    input_int8_override / scale_x_override: 端到端串联时, 用上一层的 int8 输出直接当 A,
               同时用上一层的 scale_y 作为本层的 scale_x (保证 bias 折算 + requant M 定点化
               与 DUT 数据流一致)。两个参数要一起给。
    """
    os.makedirs(out_dir, exist_ok=True)
    layer = model.get_submodule(layer_key)
    assert isinstance(layer, torch.nn.Linear), (
        f"{layer_key} type={type(layer).__name__}, 需 nn.Linear.")

    W_fp32 = layer.weight.detach().numpy().astype(np.float32)          # (N_out, K)
    bias_fp32 = layer.bias.detach().numpy().astype(np.float32)         # (N_out,)

    # 无论是否 override, 都跑 calibration 拿 y_max (scale_y 所需)
    output_post_fn = (lambda t: torch.clamp(t, min=0.0)) if post_relu else None
    a_fp32, a_max, y_max = _calibrate_and_capture(
        model, layer, calibration_loader, sample_x,
        output_post_fn=output_post_fn)

    scale_w = _symmetric_qint8_scale(np.max(np.abs(W_fp32)))
    scale_y = _symmetric_qint8_scale(y_max)

    if input_int8_override is not None:
        assert scale_x_override is not None, "input_int8_override 必须配 scale_x_override"
        A = np.asarray(input_int8_override, dtype=np.int8)
        scale_x = float(scale_x_override)
    else:
        a_fp32 = a_fp32.astype(np.float32)                             # (M, K)
        scale_x = _symmetric_qint8_scale(a_max)
        A = np.clip(np.round(a_fp32 / scale_x), -128, 127).astype(np.int8)
    B = np.clip(np.round(W_fp32.T / scale_w), -128, 127).astype(np.int8)    # (K, N_out)

    # 离线折算 (fp32 → int32): bias, requant M0/shift
    scale_b = scale_x * scale_w
    bias_int32 = _fp32_bias_to_int32(bias_fp32, scale_b)
    M_float = scale_b / scale_y
    M0, shift = compute_multiplier_shift(M_float)

    header = f"layer={layer_key} kind=linear"
    extra = {
        "scale_x": f"{scale_x:.10e}",
        "scale_w": f"{scale_w:.10e}",
        "scale_y": f"{scale_y:.10e}",
        "scale_b": f"{scale_b:.10e}",
        "M_float": f"{M_float:.10e}",
        "a_max":   f"{a_max:.6e}",
        "y_max":   f"{y_max:.6e}",
    }
    result = _run_matmul_pipeline(A, B, bias_int32, M0, shift,
                                  out_dir, header, extra,
                                  relu=post_relu, AR=AR, AC=AC)
    result.update(scale_x=scale_x, scale_w=scale_w, scale_y=scale_y, M_float=M_float)
    return result


def dump_conv_layer(model, sample_x, layer_key, out_dir,
                    *, calibration_loader=None,
                    post_relu=False, post_pool=None,
                    input_int8_override=None, scale_x_override=None,
                    AR=8, AC=8):
    """从 fp32 nn.Conv2d 层生成 satpu_top_tb golden。

    硬件角度看, conv 走 im2col 后 == matmul(A, B):
      A: (N*H_out*W_out, C*R*S) int8   ← im2col 展开
      B: (C*R*S, K_out)         int8   ← W.reshape(K_out, C*R*S).T
    下游 pipeline (matmul + bias + requant) 与 Linear 完全一致。

    仅支持 stride=1 (SaTPU 首版); padding 由 layer.padding 传入 im2col。

    post_relu: True 时把 ReLU 融进 requant clip (out_min=0)。
    post_pool: {"kernel": int, "stride": int?} 或 None。
               有值则 requant 后再走一步 int8 MaxPool2d, 多 dump Y_int8_pool.txt;
               scale_y 也用 ReLU+Pool 后的 fp32 输出做校准。
    """
    os.makedirs(out_dir, exist_ok=True)
    layer = model.get_submodule(layer_key)
    assert isinstance(layer, torch.nn.Conv2d), (
        f"{layer_key} type={type(layer).__name__}, 需 nn.Conv2d.")

    # nn.Conv2d 的属性
    stride  = layer.stride[0]  if isinstance(layer.stride,  tuple) else layer.stride
    padding = layer.padding[0] if isinstance(layer.padding, tuple) else layer.padding
    R, S    = layer.kernel_size if isinstance(layer.kernel_size, tuple) else (layer.kernel_size, layer.kernel_size)
    K_out   = layer.out_channels
    C_in    = layer.in_channels
    assert stride == 1, f"stride={stride} 未支持 (SaTPU v1 只做 stride=1)"

    W_fp32 = layer.weight.detach().numpy().astype(np.float32)          # (K_out, C_in, R, S)
    bias_fp32 = (layer.bias.detach().numpy().astype(np.float32)
                 if layer.bias is not None else np.zeros(K_out, dtype=np.float32))

    # y_max 校准要观测 post-fusion (ReLU + Pool) 张量, 才能给 requant 拿到紧 scale_y
    def _output_post_fn(t):
        if post_relu:
            t = torch.clamp(t, min=0.0)
        if post_pool is not None:
            k = post_pool.get("kernel", 2)
            s = post_pool.get("stride", k)
            t = torch.nn.functional.max_pool2d(t, kernel_size=k, stride=s)
        return t

    x_fp32, a_max, y_max = _calibrate_and_capture(
        model, layer, calibration_loader, sample_x,
        output_post_fn=_output_post_fn if (post_relu or post_pool) else None)

    scale_w = _symmetric_qint8_scale(np.max(np.abs(W_fp32)))
    scale_y = _symmetric_qint8_scale(y_max)

    if input_int8_override is not None:
        assert scale_x_override is not None, "input_int8_override 必须配 scale_x_override"
        X_int8 = np.asarray(input_int8_override, dtype=np.int8)
        assert X_int8.ndim == 4, f"conv override 需 (N, C, H, W) 4D, 收到 shape={X_int8.shape}"
        scale_x = float(scale_x_override)
    else:
        x_fp32 = x_fp32.astype(np.float32)                             # (N, C, H, W_in)
        scale_x = _symmetric_qint8_scale(a_max)
        X_int8 = np.clip(np.round(x_fp32 / scale_x), -128, 127).astype(np.int8)
    W_int8 = np.clip(np.round(W_fp32 / scale_w), -128, 127).astype(np.int8)

    A = im2col(X_int8, R, S, padding, stride)                          # (N*H_out*W_out, C*R*S)
    B = W_int8.reshape(K_out, C_in * R * S).T.astype(np.int8)          # (C*R*S, K_out)

    N, _, H, W_in = X_int8.shape
    H_out = (H + 2 * padding - R) // stride + 1
    W_out = (W_in + 2 * padding - S) // stride + 1

    # 离线折算 (fp32 → int32): bias, requant M0/shift
    scale_b = scale_x * scale_w
    bias_int32 = _fp32_bias_to_int32(bias_fp32, scale_b)
    M_float = scale_b / scale_y
    M0, shift = compute_multiplier_shift(M_float)

    header = (f"layer={layer_key} kind=conv2d "
              f"N={N} C={C_in} H={H} W={W_in} R={R} S={S} "
              f"stride={stride} padding={padding} "
              f"H_out={H_out} W_out={W_out} K_out={K_out}")
    extra = {
        "scale_x":  f"{scale_x:.10e}",
        "scale_w":  f"{scale_w:.10e}",
        "scale_y":  f"{scale_y:.10e}",
        "scale_b":  f"{scale_b:.10e}",
        "M_float":  f"{M_float:.10e}",
        "a_max":    f"{a_max:.6e}",
        "y_max":    f"{y_max:.6e}",
        "conv_N":   N,
        "conv_C":   C_in,
        "conv_H":   H,
        "conv_W":   W_in,
        "conv_R":   R,
        "conv_S":   S,
        "conv_H_out": H_out,
        "conv_W_out": W_out,
        "conv_K_out": K_out,
        "stride":   stride,
        "padding":  padding,
    }
    result = _run_matmul_pipeline(A, B, bias_int32, M0, shift,
                                  out_dir, header, extra,
                                  relu=post_relu, AR=AR, AC=AC)
    result.update(scale_x=scale_x, scale_w=scale_w, scale_y=scale_y, M_float=M_float)

    # Pool 融合: 把 Y_int8 (M_pad, N_pad) 中的有效区 reshape 回 (N, K_out, H_out, W_out),
    # 走 int8 MaxPool2d, 多 dump 一份 Y_int8_pool.txt。
    if post_pool is not None:
        pool_k = post_pool.get("kernel", 2)
        pool_s = post_pool.get("stride", pool_k)
        Y_int8 = result["Y_int8"]
        # 有效区: 行前 N*H_out*W_out, 列前 K_out
        Y_valid = Y_int8[:N * H_out * W_out, :K_out]
        Y_nchw = Y_valid.reshape(N, H_out, W_out, K_out).transpose(0, 3, 1, 2)
        Y_pool = NumericalModel.maxpool_int8(Y_nchw, pool_k, pool_s)   # (N, K, Hp, Wp)
        H_p, W_p = Y_pool.shape[2], Y_pool.shape[3]
        # 摊平回 (N*H_p*W_p, K_out) 供 tb 直接吃 (与下一层的 A 布局一致)
        Y_pool_flat = Y_pool.transpose(0, 2, 3, 1).reshape(N * H_p * W_p, K_out)
        pool_header = (f"M={N * H_p * W_p} K=- N={K_out} "
                       f"pool_k={pool_k} pool_s={pool_s} H_p={H_p} W_p={W_p} "
                       f"layer={layer_key} kind=conv2d+pool")
        _write_matrix(os.path.join(out_dir, "Y_int8_pool.txt"),
                      Y_pool_flat, pool_header)
        result["Y_int8_pool"] = Y_pool_flat
        result["Y_int8_pool_nchw"] = Y_pool

    return result
