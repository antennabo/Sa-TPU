import dataclasses
import os
import numpy as np
from abc import ABC, abstractmethod
from backend.hw import HardwareConfig
from frontend.ir import OpIR, ReductionOrder, Conv2dIR, MatMulIR, ElementwiseIR
from utils.utils import im2col, conv2d, np_linear, np_relu, maxpool, quantize_int8

class Transform(ABC):
    name: str
    debug: bool = False

    def validate_input(self, op: OpIR) -> None:
        if not isinstance(op, OpIR):
            raise TypeError(f"期望 OpIR，收到 {type(op)}")

    @abstractmethod
    def transform(self, op: OpIR, **kwargs) -> OpIR:
        pass

    def run(self, op: OpIR, **kwargs) -> OpIR:
        self.validate_input(op)
        return self.transform(op, **kwargs)
    

class TilingTransform(Transform):
    name = "tiling"

    def transform(self, op: OpIR, hw: HardwareConfig) -> OpIR:
        tm, tn = hw.mxu_dim
        if isinstance(op, Conv2dIR):
            tk   = op.C * op.R * op.S
            tile = {"tm": tm, "tn": tn, "tk": tk}
            if self.debug: print(f"  [tiling] Conv2d  tile={tile}")
        elif isinstance(op, MatMulIR):
            tile = {"tm": tm, "tn": tn, "tk": tm}
            if self.debug: print(f"  [tiling] MatMul  tile={tile}")
        else:
            return op
        return dataclasses.replace(op, tile=tile)

    def tile_all(self, irs: list, data, hw: HardwareConfig, tile_dir: str = "tiles") -> tuple:

        if self.debug:
            print("=== tile_all weights ===")
            for i, (W, b, scale_w) in enumerate(data.weights):
                print(f"  [{i}] W={W.shape} b={b.shape if b is not None else None} scale_w={scale_w:.6f}")
        os.makedirs(tile_dir, exist_ok=True)
        tm, tn = hw.mxu_dim
        tk = tm
        tiled_irs = [self.transform(op, hw) for op in irs]

        # 每层activation的计算暂时放到这个模块，后续会移到numerical analyzer
        # 情况1：每层激活已预存；情况2：只有 input_data，需前向传播
        use_cached = len(data.activations) > 0
        x  = data.activations[0].astype(np.float32) if use_cached else data.input_data.astype(np.float32)
        wi = 0
        ai = 0  # activations index
        for op in irs:
            A_tiles = []
            B_tiles = []
            if isinstance(op, Conv2dIR):
                W, bias, scale_w = data.weights[wi]; wi += 1
                
                if not use_cached:
                    x_int8, _, scale_x = quantize_int8(x)
                    scale_b = scale_x * scale_w
                    bias_int32 = np.round(bias / scale_b).astype(np.int32) if bias is not None else 0
                    x = conv2d(x_int8.astype(np.int32), W.astype(np.int32), bias_int32).astype(np.float32) * scale_b
                    np.save(os.path.join(tile_dir, f"layer{wi}_output.npy"), x)
                    if self.debug: print("do not support conv tiling!!")
            elif isinstance(op, MatMulIR):
                W, bias, scale_w = data.weights[wi]; wi += 1
                x_in = data.activations[ai].astype(np.float32) if use_cached else x; ai += 1
                x_int8, _, scale_x = quantize_int8(x_in)
                A = x_int8.reshape(op.M, op.K)
                B = W.T
                M, K = A.shape
                _, N  = B.shape
                # OS
                if self.debug: print(f"  [tiling] MatMul  A={A.shape}→ {-(-M//tm)}x{-(-K//tk)} B={B.shape} → {-(-K//tk)}x{-(-N//tn)} tiles")
                tidx = len(data.instr_queue)
                for m0 in range(0, M, tm):
                    for k0 in range(0, K, tk):
                        tile = A[m0:min(m0+tm, M), k0:min(k0+tk, K)]
                        A_tiles.append(self.pad_tile(tile, (tm, tk)))

                for k0 in range(0, K, tk):
                    for n0 in range(0, N, tn):
                        tile = B[k0:min(k0+tk, K), n0:min(n0+tn, N)]
                        B_tiles.append(self.pad_tile(tile, (tk, tn)))
                # save all together
                num_m = -(-M // tm)
                num_k = -(-K // tk)
                num_n = -(-N // tn)
                np.save(os.path.join(tile_dir, f"layer{wi}_A_tiles.npy"), np.array(A_tiles).reshape(num_m, num_k, tm, tk))
                np.save(os.path.join(tile_dir, f"layer{wi}_B_tiles.npy"), np.array(B_tiles).reshape(num_k, num_n, tk, tn))

                if not use_cached:
                    scale_b = scale_x * scale_w
                    bias_int32 = np.round(bias / scale_b).astype(np.int32) if bias is not None else 0
                    x = np_linear(x_int8.astype(np.int32), W.astype(np.int32), bias_int32).astype(np.float32) * scale_b
                    np.save(os.path.join(tile_dir, f"layer{wi}_bias.npy"), bias if bias is not None else np.zeros(N, dtype=np.float32))
                    if self.debug: print(f"  [backend MatMulIR] scale_x={scale_x:.4f} scale_w={scale_w:.4f} out={x.flatten()[:4]}")
                    np.save(os.path.join(tile_dir, f"layer{wi}_output.npy"), x)
            elif isinstance(op, ElementwiseIR):
                if not use_cached:
                    if op.op == "relu":      x = np_relu(x)
                    elif op.op == "maxpool": x = maxpool(x)
                    elif op.op == "flatten": x = x.flatten()

        return tiled_irs, data

    def pad_tile(self, tile, target_shape):
        out = np.zeros(target_shape, dtype=tile.dtype)
        out[:tile.shape[0], :tile.shape[1]] = tile
        return out

class MappingTransform(Transform):
    name = "mapping"

    def transform(self, op: OpIR, mapping: dict, reduction_order: ReductionOrder) -> OpIR:
        return dataclasses.replace(op, mapping=mapping, reduction_order=reduction_order)
