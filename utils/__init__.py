import numpy as np

def im2col(x: np.ndarray, R: int, S: int, padding: int, stride: int) -> np.ndarray:
    N, C, H, W = x.shape
    H_out = (H + 2*padding - R) // stride + 1
    W_out = (W + 2*padding - S) // stride + 1
    x_pad = np.pad(x, ((0, 0), (0, 0), (padding, padding), (padding, padding)))
    cols  = np.zeros((N * H_out * W_out, C * R * S), dtype=x.dtype)
    idx   = 0
    for n in range(N):
        for i in range(H_out):
            for j in range(W_out):
                patch = x_pad[n, :, i*stride:i*stride+R, j*stride:j*stride+S]
                cols[idx] = patch.flatten()
                idx += 1
    return cols

def quantize_int8(W):
    scale = np.max(np.abs(W)) / 127
    W_int8 = np.round(W / scale).astype(np.int8)
    W_dequant = W_int8.astype(np.float32) * scale
    return W_int8, W_dequant, scale

def quantize_fp16(W):
    W_fp16 = W.astype(np.float16)
    W_dequant = W_fp16.astype(np.float32)
    return W_fp16, W_dequant

def quantize_weight(W, dtype):
    match dtype:
        case "int8":
            _, W_dq, _ = quantize_int8(W)
            return W_dq
        case "fp16":
            _, W_dq = quantize_fp16(W)
            return W_dq
        case "int4":
            # TODO
            raise NotImplementedError("int4 暂不支持")
        case _:
            return W  # fp32，无损

def np_linear(x, W, b):
    return x @ W.T + b

def np_relu(x):
    return np.maximum(0, x)

def conv2d(x, W, b, padding=1):
    N, C, H_in, W_in = x.shape
    out_ch, in_ch, kH, kW = W.shape
    x_pd = np.pad(x, ((0,0), (0,0), (padding,padding), (padding,padding)), mode='constant', constant_values=0)
    H_out = H_in + 2 * padding - kH + 1
    W_out = W_in + 2 * padding - kW + 1
    out = np.zeros((N, out_ch, H_out, W_out))
    for n in range(N):
        for c in range(out_ch):
            for i in range(H_out):
                for j in range(W_out):
                    patch = x_pd[n,:,i:i+kH,j:j+kW]
                    out[n,c,i,j] = np.sum(patch * W[c]) + (b[c] if b is not None and not np.isscalar(b) else 0)
    return out

def maxpool(x, kernel=2):
    N, C, H_in, W_in = x.shape
    H_out = int(H_in / kernel)
    W_out = int(W_in / kernel)
    out = np.zeros((N, C, H_out, W_out))
    for n in range(N):
        for c in range(C):
            for h in range(H_out):
                for w in range(W_out):
                    out[n,c,h,w] = np.max(x[n, c, h*kernel:h*kernel+kernel, w*kernel:w*kernel+kernel])
    return out


import dataclasses
from compiler.frontend.ir import Conv2dIR, MatMulIR, ElementwiseIR

def fill_activations(irs: list, x) -> list:
    """逐层传播激活，填入每个计算层的 input_data"""
    result = []
    x = x.astype(np.float32)
    for ir in irs:
        if isinstance(ir, Conv2dIR):
            result.append(dataclasses.replace(ir, input_data=x))
            x = np_relu(conv2d(x, ir.input_weight, ir.bias))
        elif isinstance(ir, MatMulIR):
            result.append(dataclasses.replace(ir, input_data=x))
            x = np_relu(np_linear(x, ir.input_weight, ir.bias))
        elif isinstance(ir, ElementwiseIR):
            result.append(ir)
            if ir.op == "relu":      x = np_relu(x)
            elif ir.op == "maxpool": x = maxpool(x)
            elif ir.op == "flatten": x = x.flatten()
        else:
            result.append(ir)
    return result
    #     """
    #     Run time-driven simulation of A (M×K) @ B (K×N).
    #     Returns total cycle count.

    #     Phase A dataflow (output-stationary tile):
    #       - Each PE[i][j] accumulates C[i][j] = Σ_k A[i,k] * B[k,j]
    #       - Per cycle k: dispatcher (acting as controller) feeds
    #         activations and weights into the array, then ticks.

    #     Weight-stationary scheduling (WS) will be implemented in
    #     Controller when it is added; for now the dispatcher directly
    #     writes PE inputs each cycle.
    #     """
    #     M, K = A.shape
    #     _, N  = B.shape
    #     mxu_m, mxu_n = hw.mxu_dim
    #     total_cycles = 0

    #     for m0 in range(0, M, mxu_m):
    #         for n0 in range(0, N, mxu_n):
    #             m1 = min(m0 + mxu_m, M)
    #             n1 = min(n0 + mxu_n, N)
    #             tm, tn = m1 - m0, n1 - n0

    #             arr = spatial_array(tm, tn)

    #             for k in range(K):
    #                 # ── Phase 1 dispatch (placeholder controller) ──────────
    #                 # Feed activations: A[m0+i, k] broadcast across row i
    #                 for i in range(tm):
    #                     arr.load_row(i, [A[m0 + i, k]] * tn)
    #                 # Feed weights: B[k, n0+j] broadcast down col j
    #                 for j in range(tn):
    #                     arr.load_col(j, [B[k, n0 + j]] * tm)
    #                 # Connect running accumulator: acc ← state (prev cycle)
    #                 for i in range(tm):
    #                     for j in range(tn):
    #                         arr.pes[i][j].load_acc(arr.pes[i][j].state)

    #                 # ── Phase 2: all modules compute (read state → next_state)
    #                 arr.compute()

    #                 # ── Phase 3: all modules commit (next_state → state) ───
    #                 arr.commit()

    #                 total_cycles += 1

    #     return total_cycles

    # ------------------------------------------------------------------
    # Per-op analysis
    # ------------------------------------------------------------------

    # def _analyze_linear(self, op: MatMulIR, hw: HardwareConfig) -> PerfResult:
    #     if op.input_data is None or op.input_weight is None:
    #         raise ValueError("CycleAccurateAnalyzer 需要 op.input_data 和 op.input_weight")

    #     A = np.array(op.input_data,   dtype=np.int8)        # (M, K)
    #     B = np.array(op.input_weight, dtype=np.int8).T       # (N, K) → (K, N)

    #     cycles     = self._count_cycles(A, B, hw)
    #     ops        = 2 * op.M * op.N * op.K
    #     latency_ns = cycles / (hw.freq_mhz * 1e6) * 1e9
    #     peak_cycles = (op.M * op.N * op.K) / (hw.mxu_dim[0] * hw.mxu_dim[1])
    #     return PerfResult(
    #         model_name="cycle_accurate",
    #         confidence="accurate",
    #         latency_ns=latency_ns,
    #         utilization=peak_cycles / cycles,
    #         throughput_ops=ops / latency_ns,
    #         pipeline_bubbles=0,
    #     )

    # def _analyze_conv(self, op: Conv2dIR, hw: HardwareConfig) -> PerfResult:
    #     if op.input_data is None or op.input_weight is None:
    #         raise ValueError("CycleAccurateAnalyzer 需要 op.input_data 和 op.input_weight")

    #     H_out = (op.H + 2*op.padding - op.R) // op.stride + 1
    #     W_out = (op.W + 2*op.padding - op.S) // op.stride + 1

    #     # im2col: Conv2d → equivalent MatMul (§4.2)
    #     # A: (N*H_out*W_out, C*R*S),  B: (C*R*S, K_filter)
    #     A = self._im2col(
    #         np.array(op.input_data, dtype=np.int8),
    #         op.R, op.S, op.padding, op.stride
    #     )
    #     B = np.array(op.input_weight, dtype=np.int8).reshape(op.K, -1).T

    #     cycles     = self._count_cycles(A, B, hw)
    #     ops        = 2 * op.N * op.K * H_out * W_out * op.C * op.R * op.S
    #     latency_ns = cycles / (hw.freq_mhz * 1e6) * 1e9
    #     M_eq       = op.N * H_out * W_out
    #     peak_cycles = (M_eq * op.K * op.C * op.R * op.S) / (hw.mxu_dim[0] * hw.mxu_dim[1])
    #     return PerfResult(
    #         model_name="cycle_accurate",
    #         confidence="accurate",
    #         latency_ns=latency_ns,
    #         utilization=peak_cycles / cycles,
    #         throughput_ops=ops / latency_ns,
    #         pipeline_bubbles=0,
    #     )

    # ------------------------------------------------------------------
    # im2col (§4.2)
    # ------------------------------------------------------------------

    # @staticmethod
    # def _im2col(x: np.ndarray, R: int, S: int, padding: int, stride: int) -> np.ndarray:
    #     N, C, H, W = x.shape
    #     H_out = (H + 2*padding - R) // stride + 1
    #     W_out = (W + 2*padding - S) // stride + 1
    #     x_pad = np.pad(x, ((0, 0), (0, 0), (padding, padding), (padding, padding)))
    #     cols  = np.zeros((N * H_out * W_out, C * R * S), dtype=x.dtype)
    #     idx   = 0
    #     for n in range(N):
    #         for i in range(H_out):
    #             for j in range(W_out):
    #                 patch = x_pad[n, :, i*stride:i*stride+R, j*stride:j*stride+S]
    #                 cols[idx] = patch.flatten()
    #                 idx += 1
    #     return cols

    # @staticmethod
    # def _im2col(x: np.ndarray, R: int, S: int, padding: int, stride: int) -> np.ndarray:
    #     N, C, H, W = x.shape
    #     H_out = (H + 2*padding - R) // stride + 1
    #     W_out = (W + 2*padding - S) // stride + 1
    #     x_pad = np.pad(x, ((0, 0), (0, 0), (padding, padding), (padding, padding)))
    #     cols  = np.zeros((N * H_out * W_out, C * R * S), dtype=x.dtype)
    #     idx   = 0
    #     for n in range(N):
    #         for i in range(H_out):
    #             for j in range(W_out):
    #                 patch = x_pad[n, :, i*stride:i*stride+R, j*stride:j*stride+S]
    #                 cols[idx] = patch.flatten()
    #                 idx += 1
    #     return cols
