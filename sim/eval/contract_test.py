import pytest
from ir import MatMulIR, ReductionOrder
from hw import HardwareConfig
from result import AnalysisResult, NumericalResult
from analyzer import RooflinePerfAnalyzer, MemoryAnalyzer, StaticNumericalAnalyzer

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

class TestNumericalContract(AnalyzerContractTest):
    @pytest.fixture
    def analyzer(self):
        return StaticNumericalAnalyzer()

    # 不只是近似相等，必须完全一样，每个 bit 都相同。
    def test_same_reduction_order_bit_exact(self, analyzer):
        op = make_op(ReductionOrder.SEQUENTIAL)
        assert analyzer.run(op, make_hw()) == analyzer.run(op, make_hw())

    # SEQUENTIAL 和 TREE 的结果应该不同。如果两者结果一样，说明 analyzer 根本没有读 reduction_order 字段
    def test_different_reduction_order_differs(self, analyzer):
        r1 = analyzer.run(make_op(ReductionOrder.SEQUENTIAL), make_hw())
        r2 = analyzer.run(make_op(ReductionOrder.TREE), make_hw())
        assert r1 != r2