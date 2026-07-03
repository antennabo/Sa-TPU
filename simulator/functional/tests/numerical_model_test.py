import numpy as np
from simulator.functional.numerical_model import (
    NumericalModel, INT32_MIN, INT32_MAX, _sat_add_i32,
    compute_multiplier_shift, requantize_int32_to_int8,
)


AR = 8
AC = 8


class TestMatMul:
    def test_small_hand_computed(self):
        # 2x3 · 3x4, 手算几个位置; 输出 shape 会 pad 到 (8, 8)
        A = np.array([[1, 2, 3],
                      [4, 5, 6]], dtype=np.int8)
        B = np.array([[1, 2, 3, 4],
                      [5, 6, 7, 8],
                      [9, 10, 11, 12]], dtype=np.int8)
        Y = NumericalModel.matmul(A, B)
        assert Y.shape == (AR, AC)   # M=2 → 8, N=4 → 8
        assert Y.dtype == np.int32
        # C[0,0] = 1*1 + 2*5 + 3*9 = 38；C[1,3] = 4*4 + 5*8 + 6*12 = 128
        assert Y[0, 0] == 38
        assert Y[1, 3] == 128
        # pad 区应为 0
        assert np.all(Y[2:, :] == 0)
        assert np.all(Y[:, 4:] == 0)
        # 未 pad 区应与朴素 int32 matmul 相同
        expected = A.astype(np.int32) @ B.astype(np.int32)
        np.testing.assert_array_equal(Y[:2, :4], expected)

    def test_random_matches_naive_int32_no_overflow(self):
        # 随机 int8, 值域小, 不触发 int32 饱和 → 未 pad 区应与 int64 一次算等价
        rng = np.random.default_rng(42)
        A = rng.integers(-64, 63, size=(8, 16), dtype=np.int8)
        B = rng.integers(-64, 63, size=(16, 12), dtype=np.int8)
        Y = NumericalModel.matmul(A, B)
        assert Y.shape == (8, 16)   # N=12 pad 到 16
        expected = A.astype(np.int32) @ B.astype(np.int32)
        np.testing.assert_array_equal(Y[:8, :12], expected)
        assert np.all(Y[:, 12:] == 0)  # pad 列

    def test_signed_boundary_neg128(self):
        # -128 * -128 = 16384; K=1 pad 到 AR=8, 其余 K 位为 0 → 结果不变
        A = np.array([[-128]], dtype=np.int8)
        B = np.array([[-128]], dtype=np.int8)
        Y = NumericalModel.matmul(A, B)
        assert Y[0, 0] == 16384

    def test_multi_wtile(self):
        # K = 16 → 2 个 WTILE, 验证 accumulator 分级累加正确
        rng = np.random.default_rng(1)
        A = rng.integers(-32, 32, size=(4, 16), dtype=np.int8)
        B = rng.integers(-32, 32, size=(16, 8), dtype=np.int8)
        Y = NumericalModel.matmul(A, B)
        expected = A.astype(np.int32) @ B.astype(np.int32)
        np.testing.assert_array_equal(Y[:4, :], expected)

    def test_pad_zeros(self):
        # M=1, K=6, N=3 → pad 到 (8, 8, 8), 数值不变, shape 变
        A = np.array([[1, 2, 3, 4, 5, 6]], dtype=np.int8)
        B = np.arange(6 * 3, dtype=np.int8).reshape(6, 3) - 8
        Y = NumericalModel.matmul(A, B)
        assert Y.shape == (AR, AC)
        expected = A.astype(np.int32) @ B.astype(np.int32)
        np.testing.assert_array_equal(Y[:1, :3], expected)
        assert np.all(Y[1:, :] == 0)
        assert np.all(Y[:, 3:] == 0)


class TestSatAddI32:
    """直接测 _sat_add_i32 的饱和语义。int8 输入不可能触发 int32 饱和,
    所以饱和路径只能对着这个 helper 单独测。"""

    def test_no_overflow_matches_regular_add(self):
        a = np.array([[100, -50]], dtype=np.int32)
        b = np.array([[200,  30]], dtype=np.int32)
        np.testing.assert_array_equal(_sat_add_i32(a, b),
                                      np.array([[300, -20]], dtype=np.int32))

    def test_positive_saturation(self):
        a = np.array([INT32_MAX - 5], dtype=np.int32)
        b = np.array([100], dtype=np.int32)
        assert _sat_add_i32(a, b)[0] == INT32_MAX

    def test_negative_saturation(self):
        a = np.array([INT32_MIN + 5], dtype=np.int32)
        b = np.array([-100], dtype=np.int32)
        assert _sat_add_i32(a, b)[0] == INT32_MIN

    def test_returns_int32(self):
        a = np.array([1], dtype=np.int32)
        b = np.array([2], dtype=np.int32)
        assert _sat_add_i32(a, b).dtype == np.int32


class TestConv2d:
    def test_shape_stride1_padding0(self):
        x = np.zeros((1, 3, 8, 8), dtype=np.int8)
        W = np.zeros((4, 3, 3, 3), dtype=np.int8)
        y = NumericalModel.conv2d(x, W, stride=1, padding=0)
        assert y.shape == (1, 4, 6, 6)
        assert y.dtype == np.int32

    def test_shape_stride2_padding1(self):
        x = np.zeros((2, 3, 8, 8), dtype=np.int8)
        W = np.zeros((4, 3, 3, 3), dtype=np.int8)
        y = NumericalModel.conv2d(x, W, stride=2, padding=1)
        assert y.shape == (2, 4, 4, 4)

    def test_matches_numpy_conv_naive(self):
        # 与朴素 int32 卷积 (不涉及饱和) 对拍, 确保 im2col + 两级累加路径正确。
        rng = np.random.default_rng(1)
        x = rng.integers(-10, 10, size=(1, 2, 5, 5), dtype=np.int8)
        W = rng.integers(-10, 10, size=(2, 2, 3, 3), dtype=np.int8)

        y = NumericalModel.conv2d(x, W, stride=1, padding=0)

        x_i32 = x.astype(np.int32)
        W_i32 = W.astype(np.int32)
        y_ref = np.zeros((1, 2, 3, 3), dtype=np.int32)
        for k in range(2):
            for i in range(3):
                for j in range(3):
                    y_ref[0, k, i, j] = np.sum(x_i32[0, :, i:i + 3, j:j + 3] * W_i32[k])
        np.testing.assert_array_equal(y, y_ref)


class TestComputeMultiplierShift:
    """M_float → (M0, shift) 定点化拆分。M0 应归一到 [2^30, 2^31), shift 正整数。"""

    def test_typical_M_small(self):
        # 常见: scale_x*scale_w << scale_y, M ~ 0.01
        M0, shift = compute_multiplier_shift(0.01)
        assert (1 << 30) <= M0 < (1 << 31)
        assert shift >= 30
        # 反算精度: |M0 * 2^-shift - 0.01| < 2^-31 (相对误差 <~ 1e-9)
        assert abs(M0 * 2 ** (-shift) - 0.01) < 1e-11

    def test_M_close_to_1(self):
        M0, shift = compute_multiplier_shift(0.9)
        assert (1 << 30) <= M0 < (1 << 31)
        assert abs(M0 * 2 ** (-shift) - 0.9) < 1e-9

    def test_M_close_to_half(self):
        M0, shift = compute_multiplier_shift(0.5)
        assert (1 << 30) <= M0 < (1 << 31)
        # 0.5 = 2^30 * 2^-31, 应该恰好归一
        assert M0 * 2 ** (-shift) == 0.5

    def test_M_ge_1_asserts(self):
        import pytest
        with pytest.raises(AssertionError):
            compute_multiplier_shift(1.5)

    def test_M_zero_asserts(self):
        import pytest
        with pytest.raises(AssertionError):
            compute_multiplier_shift(0)


class TestRequantizeInt32ToInt8:
    """int32 * M0 >> shift, 四舍五入 half-up, clip 到 int8。"""

    def test_identity_M_equals_1_over_2(self):
        # M = 0.5 → M0=2^30, shift=31. y * 0.5 应该四舍五入.
        M0, shift = compute_multiplier_shift(0.5)
        y = np.array([-10, -1, 0, 1, 10, 100], dtype=np.int32)
        out = requantize_int32_to_int8(y, M0, shift)
        # 期望: -5, 0(half up from -0.5 → 0), 0, 1(half up from 0.5), 5, 50
        # 注意 half up 语义: (y*M0 + rounding) >> shift
        # y=-1: (-1 * 2^30 + 2^30) >> 31 = 0 >> 31 = 0
        # y=1:  (1 * 2^30 + 2^30) >> 31 = 2^31 >> 31 = 1
        np.testing.assert_array_equal(out, [-5, 0, 0, 1, 5, 50])

    def test_saturation_high(self):
        # M ≈ 1 附近, y=10000 应该饱和到 127
        M0, shift = compute_multiplier_shift(0.9)
        y = np.array([100000], dtype=np.int32)
        assert requantize_int32_to_int8(y, M0, shift)[0] == 127

    def test_saturation_low(self):
        M0, shift = compute_multiplier_shift(0.9)
        y = np.array([-100000], dtype=np.int32)
        assert requantize_int32_to_int8(y, M0, shift)[0] == -128

    def test_returns_int8_dtype(self):
        M0, shift = compute_multiplier_shift(0.1)
        y = np.array([0], dtype=np.int32)
        assert requantize_int32_to_int8(y, M0, shift).dtype == np.int8

    def test_matches_fp32_reference_within_1lsb(self):
        # 与 fp32 参考对比, 定点误差应 ≤ 1 LSB.
        M_float = 0.0123
        M0, shift = compute_multiplier_shift(M_float)
        rng = np.random.default_rng(0)
        y = rng.integers(-200000, 200000, size=50, dtype=np.int32)
        out_fixed = requantize_int32_to_int8(y, M0, shift)
        out_fp = np.clip(np.round(y * M_float), -128, 127).astype(np.int8)
        diff = out_fixed.astype(np.int16) - out_fp.astype(np.int16)
        assert np.all(np.abs(diff) <= 1), f"定点与浮点差 > 1 LSB: max_diff={np.abs(diff).max()}"

    def test_relu_fused_via_out_min_0(self):
        # ReLU 融合: out_min=0 时负值直接夹到 0
        M0, shift = compute_multiplier_shift(0.5)
        y = np.array([-1000, -1, 0, 1, 1000], dtype=np.int32)
        out_normal = requantize_int32_to_int8(y, M0, shift, out_min=-128, out_max=127)
        out_relu   = requantize_int32_to_int8(y, M0, shift, out_min=0,    out_max=127)
        # normal 里可能有负数, relu 里不应有
        assert np.any(out_normal < 0), "normal 情况应有负值"
        assert np.all(out_relu >= 0), f"relu 情况不应有负值: {out_relu}"
        # 正半区应完全相同
        pos_mask = out_normal >= 0
        np.testing.assert_array_equal(out_normal[pos_mask], out_relu[pos_mask])


class TestMaxpoolInt8:
    def test_shape_2x2_stride2(self):
        x = np.zeros((1, 3, 8, 8), dtype=np.int8)
        y = NumericalModel.maxpool_int8(x, kernel=2, stride=2)
        assert y.shape == (1, 3, 4, 4)
        assert y.dtype == np.int8

    def test_default_stride_equals_kernel(self):
        x = np.arange(4 * 4, dtype=np.int8).reshape(1, 1, 4, 4)
        y = NumericalModel.maxpool_int8(x, kernel=2)  # stride 默认 = 2
        # 手算: 2x2 非重叠, 每窗取 max
        # [[0 1 2 3][4 5 6 7][8 9 10 11][12 13 14 15]]
        # → [[5 7][13 15]]
        np.testing.assert_array_equal(y[0, 0], [[5, 7], [13, 15]])

    def test_negative_values(self):
        # int8 signed: max 会挑最大 (最正) 的
        x = np.array([[[[-1, -3],
                        [-2, -4]]]], dtype=np.int8)
        y = NumericalModel.maxpool_int8(x, kernel=2)
        assert y[0, 0, 0, 0] == -1

    def test_multi_channel(self):
        rng = np.random.default_rng(0)
        x = rng.integers(-50, 50, size=(2, 4, 6, 6), dtype=np.int8)
        y = NumericalModel.maxpool_int8(x, kernel=3, stride=3)
        assert y.shape == (2, 4, 2, 2)
        # 每个 (n, k, i, j) 应等于原图对应 3x3 窗的 max
        for n in range(2):
            for k in range(4):
                for i in range(2):
                    for j in range(2):
                        expected = x[n, k, i*3:i*3+3, j*3:j*3+3].max()
                        assert y[n, k, i, j] == expected
