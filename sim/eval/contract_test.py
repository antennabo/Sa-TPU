import pytest
from ir import MatMulIR, ReductionOrder
from hw import HardwareConfig
from result import AnalysisResult, NumericalResult
from analyzer import RooflinePerfAnalyzer, MemoryAnalyzer, NumericalAnalyzer

def make_op(reduction_order=ReductionOrder.SEQUENTIAL):
    return MatMulIR(op_type="matmul", dtype="fp16", accum_dtype="fp32",
                    M=64, N=64, K=64,
                    tile={"tm":32,"tn":32,"tk":32},
                    reduction_order=reduction_order)

def make_hw():
    return HardwareConfig(mxu_dim=(8,8), sram_bytes=16*1024*1024,
                          hbm_bw_gbps=900.0, freq_mhz=1000.0)

class AnalyzerContractTest:
    @pytest.fixture
    def analyzer(self):
        raise NotImplementedError

    # 防止 analyzer 返回 dict 或 None
    def test_returns_correct_result_type(self, analyzer):
        result = analyzer.run(make_op(), make_hw())
        assert isinstance(result, AnalysisResult)

    # 跑完 analyzer 后，检查 op 和跑之前一样。
    def test_does_not_mutate_ir(self, analyzer):
        op = make_op()
        analyzer.run(op, make_hw())
        assert op == make_op()   # frozen dataclass，直接比较
    
    # 验证 hw 没有被修改。
    def test_does_not_mutate_hw(self, analyzer):
        hw = make_hw()
        analyzer.run(make_op(), hw)
        assert hw == make_hw()

    # 同一个 op 和 hw，跑两次，结果必须完全相同。验证 analyzer 是纯函数，没有依赖随机状态或外部可变状态。
    def test_deterministic(self, analyzer):
        op, hw = make_op(), make_hw()
        assert analyzer.run(op, hw) == analyzer.run(op, hw)

    def test_handles_missing_tile(self, analyzer):
        op = make_op()
        op_no_tile = __import__('dataclasses').replace(op, tile=None)
        analyzer.run(op_no_tile, make_hw())  # 不崩溃即通过


class TestRooflineContract(AnalyzerContractTest):
    @pytest.fixture
    def analyzer(self):
        return RooflinePerfAnalyzer()

class TestMemoryContract(AnalyzerContractTest):
    @pytest.fixture
    def analyzer(self):
        return MemoryAnalyzer()

class TestNumericalContract:
    """NumericalAnalyzer 使用图级接口，单独测试"""

    def make_irs(self, dtype="int8"):
        import numpy as np
        from ir import MatMulIR
        W = np.random.randn(64, 64).astype(np.float32)
        b = np.random.randn(64).astype(np.float32)
        return [MatMulIR(op_type="linear", dtype=dtype, accum_dtype="fp32",
                         M=1, N=64, K=64, input_weight=W, bias=b)]

    def make_hw(self):
        return HardwareConfig(mxu_dim=(8,8), sram_bytes=16*1024*1024,
                              hbm_bw_gbps=900.0, freq_mhz=1000.0)

    def test_returns_numerical_result(self):
        import numpy as np
        analyzer = NumericalAnalyzer()
        x = np.random.randn(1, 64).astype(np.float32)
        result = analyzer.analyze_graph(self.make_irs(), self.make_hw(), x)
        assert isinstance(result, NumericalResult)

    def test_deterministic(self):
        import numpy as np
        analyzer = NumericalAnalyzer()
        irs = self.make_irs()
        x = np.random.randn(1, 64).astype(np.float32)
        assert analyzer.analyze_graph(irs, self.make_hw(), x) == \
               analyzer.analyze_graph(irs, self.make_hw(), x)

    def test_fp32_zero_error(self):
        """fp32 不量化，误差应为 0"""
        import numpy as np
        analyzer = NumericalAnalyzer()
        x = np.random.randn(1, 64).astype(np.float32)
        result = analyzer.analyze_graph(self.make_irs(dtype="fp32"), self.make_hw(), x)
        assert result.max_error == 0.0

    def test_int8_has_error(self):
        """int8 量化应有非零误差"""
        import numpy as np
        analyzer = NumericalAnalyzer()
        x = np.random.randn(1, 64).astype(np.float32)
        result = analyzer.analyze_graph(self.make_irs(dtype="int8"), self.make_hw(), x)
        assert result.max_error > 0.0