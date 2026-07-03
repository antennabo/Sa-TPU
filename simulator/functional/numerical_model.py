import numpy as np
from utils import im2col

INT32_MIN = -(1 << 31)
INT32_MAX =  (1 << 31) - 1


def _round_up(size, multiple):
    return ((size + multiple - 1) // multiple) * multiple


def _sat_add_i32(a, b):
    """Signed saturating add on int32. numpy 里 int32 溢出会 wrap-around,
    这里借 int64 检测溢出, 再 clip 回 int32。逻辑上仍是 int32 accumulator。"""
    return np.clip(a.astype(np.int64) + b.astype(np.int64),
                   INT32_MIN, INT32_MAX).astype(np.int32)


def compute_multiplier_shift(M_float):
    """把浮点 M 拆成 (M0: int32, shift: int) 使得 M ≈ M0 * 2^(-shift)。
    RTL 定点 requantize 契约:
      - M0 归一化到 [2^30, 2^31), 保证 ~31 bit 精度
      - shift >= 1 (默认 M < 1, 即 MAC 输出量级大于 int8 目标量级)
    """
    assert M_float > 0, f"M 必须 > 0, 收到 {M_float}"
    assert M_float < 1, (
        f"M >= 1 情形暂不支持 (需要左移分支); 收到 M={M_float}. "
        "SaTPU v1 假设 MAC 输出量级 > int8 输出量级."
    )
    shift = 30
    while M_float * (1 << shift) < (1 << 30):
        shift += 1
    M0 = int(round(M_float * (1 << shift)))
    # round 到 2^31 时归一 (等价于 M0=2^30, shift-1)
    if M0 == (1 << 31):
        M0 >>= 1
        shift -= 1
    assert (1 << 30) <= M0 < (1 << 31), f"M0={M0} 未归一化"
    return M0, shift


def requantize_int32_to_int8(y, M0, shift, out_min=-128, out_max=127):
    """y_int32 * M0 >> shift, half-up 舍入, clip 到 int8。

    对应 RTL:
      prod (63-bit) = y (int32) * M0 (int32)       ← 硬件 32*32→64 乘法器
      shifted (int32) = (prod + 2^(shift-1)) >> shift
      out (int8) = sat_clip(shifted)
    zero_point = 0 (per SaTPU scope: per-tensor symmetric).
    """
    # int32 * int32 → int64: 数值上必需 (最大 ~2^62), 对应硬件宽乘法器输出。
    prod = np.asarray(y).astype(np.int64) * M0
    return np.clip((prod + (1 << (shift - 1))) >> shift,
                   out_min, out_max).astype(np.int8)


class NumericalModel:
    """SaTPU 数值参考模型 (RTL 数值对拍 golden)。

    位宽 & 累加语义严格对齐硬件:
      - 输入:   A / B 均为 int8
      - 乘积:   int8 * int8 -> int32 (最大 128*128=16384, 无溢出)
      - 累加:   int32 signed, 溢出时饱和到 [INT32_MIN, INT32_MAX]
      - 两级累加, 每级独立饱和:
          1) WTILE 内沿 K 逐步累加 (模拟 SA 内 PE psum 沿列的流动)
          2) WTILE 之间用 accumulator 累加 (模拟 accumulator 单元跨 K-tile 累加)

    与硬件排程解耦: 不模拟 cycle, 只给出最终 int32 矩阵。
    """

    @staticmethod
    def matmul(A, B, *, AR=8, AC=8):
        """两级 int32 累加 signed matmul, 输入 int8。

        A: (M, K) int8
        B: (K, N) int8
        AR / AC: SA 行 / 列尺寸, 决定 K-tile 大小与 pad 粒度
        return: (M_pad, N_pad) int32, M_pad/K_pad 对齐 AR, N_pad 对齐 AC
        """
        A_i8 = np.asarray(A, dtype=np.int8)
        B_i8 = np.asarray(B, dtype=np.int8)
        M, K = A_i8.shape
        K_b, N = B_i8.shape
        assert K == K_b, f"K 不匹配: A 有 {K}, B 有 {K_b}"

        M_pad = _round_up(M, AR)
        K_pad = _round_up(K, AR)
        N_pad = _round_up(N, AC)

        A_pad = np.zeros((M_pad, K_pad), dtype=np.int8)
        A_pad[:M, :K] = A_i8
        B_pad = np.zeros((K_pad, N_pad), dtype=np.int8)
        B_pad[:K, :N] = B_i8

        accumulator = np.zeros((M_pad, N_pad), dtype=np.int32)

        for kt in range(K_pad // AR):
            A_tile = A_pad[:, kt * AR:(kt + 1) * AR]   # int8, (M_pad, AR)
            B_tile = B_pad[kt * AR:(kt + 1) * AR, :]   # int8, (AR, N_pad)

            # SA 内沿 K 逐步累加, 每步饱和 (模拟 PE 沿列的 psum 流)
            psum = np.zeros((M_pad, N_pad), dtype=np.int32)
            for k in range(AR):
                # int8 * int8 -> int32, 无溢出风险
                step = A_tile[:, k:k + 1].astype(np.int32) * B_tile[k:k + 1, :].astype(np.int32)
                psum = _sat_add_i32(psum, step)

            # Accumulator 加上这个 WTILE 的 psum, 再饱和
            accumulator = _sat_add_i32(accumulator, psum)

        return accumulator

    @staticmethod
    def maxpool_int8(x, kernel, stride=None):
        """int8 → int8 MaxPool2d, NCHW 布局。纯 select, 不做算术, 不涉及饱和。

        x:      (N, K, H, W) int8/int-like
        kernel: 池化窗口大小 (int, 方形窗)
        stride: 默认 = kernel (非重叠)
        return: (N, K, H_out, W_out) int8
        """
        if stride is None:
            stride = kernel
        x_i8 = np.asarray(x, dtype=np.int8)
        N, K, H, W_in = x_i8.shape
        H_out = (H - kernel) // stride + 1
        W_out = (W_in - kernel) // stride + 1
        out = np.empty((N, K, H_out, W_out), dtype=np.int8)
        for i in range(H_out):
            for j in range(W_out):
                window = x_i8[:, :,
                              i * stride:i * stride + kernel,
                              j * stride:j * stride + kernel]
                out[:, :, i, j] = window.max(axis=(2, 3))
        return out

    @staticmethod
    def conv2d(x, W, *, stride=1, padding=0, AR=8, AC=8):
        """int8 Conv2d, 通过 im2col 展开到 matmul。返回未 pad 的 int32 结果。

        x: (N, C, H, W_in) int8
        W: (K, C, R, S)    int8
        return: (N, K, H_out, W_out) int32
        """
        N, C, H, W_in = x.shape
        K, C_w, R, S = W.shape
        assert C == C_w, f"通道数不匹配: x 有 {C}, W 有 {C_w}"

        H_out = (H + 2 * padding - R) // stride + 1
        W_out = (W_in + 2 * padding - S) // stride + 1

        cols = im2col(x, R, S, padding, stride)      # int8, (N*H_out*W_out, C*R*S)
        W_flat = W.reshape(K, C * R * S).T           # int8, (C*R*S, K)

        out2d_padded = NumericalModel.matmul(cols, W_flat, AR=AR, AC=AC)
        # 去 pad 回原尺寸 (bias-add / requantize 是后续 PR 的事)
        out2d = out2d_padded[:cols.shape[0], :K]

        return out2d.reshape(N, H_out, W_out, K).transpose(0, 3, 1, 2)
