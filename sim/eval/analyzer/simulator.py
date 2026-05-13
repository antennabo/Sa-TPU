import numpy as np
from backend.hw import HardwareConfig
from frontend.ir import Conv2dIR, MatMulIR, ElementwiseIR
from .analyzer import RooflinePerfAnalyzer, MemoryAnalyzer, NumericalAnalyzer, AnalysisPipeline
from .cycle_analyzer import CycleAccurateAnalyzer
from utils.utils import quantize_weight, conv2d, np_linear, np_relu, maxpool, im2col


class Simulator:
    def __init__(self, irs: list, x_np, hw: HardwareConfig):
        self.irs       = irs
        self.x_np      = x_np
        self.hw        = hw
        self.static    = AnalysisPipeline([RooflinePerfAnalyzer(), MemoryAnalyzer()])
        self.numerical = NumericalAnalyzer()
        self.cycle     = CycleAccurateAnalyzer()

    def run_static(self) -> dict:
        results = self.static.run_graph(self.irs, self.hw)
        self.print_static(results)
        return results

    def run_numerical(self, exported):
        result = self.numerical.analyze_graph(self.irs, self.x_np, exported)
        self.print_numerical(result)
        return result

    def run_cycle(self, exported):
        weights = NumericalAnalyzer._extract_weights(exported)
        x = self.x_np.astype(np.float32)
        wi = 0
        for op in self.irs:
            if isinstance(op, (Conv2dIR, MatMulIR)):
                W, b = weights[wi]; wi += 1
                W_q = quantize_weight(W.astype(np.float32), op.dtype).astype(np.int8)
                x_q = x.astype(np.int8)
                if isinstance(op, Conv2dIR):
                    A = im2col(x_q, op.R, op.S, op.padding, op.stride)
                    B = W_q.reshape(op.K, -1).T
                    self.cycle.analyze(op, self.hw, A, B)
                    x = conv2d(x, W.astype(np.float32), b.astype(np.float32) if b is not None else 0)
                else:
                    A = x_q.reshape(op.M, op.K)
                    B = W_q.T
                    self.cycle.analyze(op, self.hw, A, B)
                    x = np_linear(x, W.astype(np.float32), b.astype(np.float32) if b is not None else 0)
            elif isinstance(op, ElementwiseIR):
                if op.op == "relu":      x = np_relu(x)
                elif op.op == "maxpool": x = maxpool(x)
                elif op.op == "flatten": x = x.flatten()
        self.print_cycle()

    def print_static(self, results: dict):
        print("\n=== 静态分析结果 ===")
        for key, r in results.items():
            if r is None:
                print(f"  {key}: 跳过")
            elif isinstance(r, dict):
                for name, result in r.items():
                    print(f"  {key} | {name}: {result}")
            else:
                print(f"  {key}: {r}")

    def print_numerical(self, result):
        print("\n=== 数值分析 ===")
        print(f"  max_error:  {result.max_error:.6f}")
        print(f"  mean_error: {result.mean_error:.6f}")

    def print_cycle(self):
        print("\n=== Cycle 仿真结果 ===")
        print(f"  total_cycles: {self.cycle.total_cycles}")
