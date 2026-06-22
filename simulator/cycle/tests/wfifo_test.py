"""weight_fifo（CommonFIFO）合并测试：
  - TestCommonFIFO：OS 模式 update(wdata, feed) 的 model 单测（avail / skew / feed 行为）
  - test_wfifo_ws_dump_cosim：WS 模式 ws_update(wdata, ready) 的 RTL 对拍黄金向量 dump

OS 路径用 update()；WS 路径用 ws_update()。两套 API 用同一个 CommonFIFO 实现。
"""
import numpy as np

from simulator.cycle.sim_model.commonfifo import CommonFIFO


# =========================================================================== #
# OS-mode model 单测
# =========================================================================== #

class TestCommonFIFO:
    """CommonFIFO：DMA 写攒 tile、标量 feed → 内部 lane 传播生成 skew、avail 握手。
    见 doc/weight_fifo_design.md。
    """

    def tick(self, f, wdata, feed):
        f.update(wdata, feed)
        f.commit()

    def preload(self, f, tile):
        """tile = K 条深度向量 [K][N]；逐拍 DMA 写入（feed=False，不读）。"""
        for vec in tile:
            self.tick(f, vec, False)

    def test_avail_after_full_tile(self):
        f = CommonFIFO(N=3, K=2)
        assert not f.avail
        self.tick(f, [1, 2, 3], False)        # 写第 1 条（tile 未满）
        assert not f.avail
        self.tick(f, [4, 5, 6], False)        # 写第 2 条 → 攒满 1 个 tile
        assert f.avail

    def test_scalar_feed_generates_skew(self):
        # N=3, K=3：tile 各 lane 的深度序列 = 列号*10 + 深度
        # lane c 深度 d 的值 = c*10 + d
        f = CommonFIFO(N=3, K=3)
        tile = [[0 + d, 10 + d, 20 + d] for d in range(3)]   # [K][N]
        self.preload(f, tile)
        assert f.avail

        # 标量 feed 拉高 K 拍：lane c 应延 c 拍才开始吐，且按深度递增 → skew
        outs = []
        for _ in range(3 + 3):                # 多跑几拍看完整 skew
            self.tick(f, None, True)
            outs.append(list(f.data))

        # lane0 立刻吐：深度 0,1,2 在第 0,1,2 拍
        assert outs[0][0] == 0 and outs[1][0] == 1 and outs[2][0] == 2
        # lane1 延 1 拍：第 0 拍还没点亮(=0)，第 1,2,3 拍吐 10,11,12
        assert outs[0][1] == 0
        assert outs[1][1] == 10 and outs[2][1] == 11 and outs[3][1] == 12
        # lane2 延 2 拍：第 0,1 拍=0，第 2,3,4 拍吐 20,21,22
        assert outs[0][2] == 0 and outs[1][2] == 0
        assert outs[2][2] == 20 and outs[3][2] == 21 and outs[4][2] == 22

    def test_feed_low_outputs_zero(self):
        f = CommonFIFO(N=2, K=2)
        self.preload(f, [[1, 2], [3, 4]])
        self.tick(f, None, False)             # feed=False → 全 0、不弹
        assert f.data == [0, 0]
        self.tick(f, None, True)              # feed=True → lane0 吐队头
        assert f.data[0] == 1

    def test_started_consumes_avail(self):
        f = CommonFIFO(N=2, K=2)
        self.preload(f, [[1, 2], [3, 4]])
        assert f.avail
        self.tick(f, None, True)              # lane0 弹出 tile-head → _started+1
        assert not f.avail                    # 已开喂 → avail 落


# =========================================================================== #
# WS-mode RTL co-sim 黄金向量 dump
# =========================================================================== #

def _dump_wfifo_ws(weights, ready_seq, dump_path):
    """单 tile WS 对拍向量 dump：
      - weights (K×N) — DMA 在 cy 0..K-1 每拍同步广播一行 N 个权重灌进 fifo
      - ready_seq (T×N) — cy < T 每拍每列下游 ready；之后默认 0
      golden = commonfifo.py::CommonFIFO.ws_update 同排程的逐拍 (vld[N], data[N])。
      wfifo_tb 用同一份 .txt 喂 RTL 比对。

    数据行：cy wvalid wdata[N] ready[N] vld[N] data[N]，每行 2+4N 个数。
    返回 rows 列表用于 sanity。"""
    weights = np.asarray(weights)
    K, N = weights.shape
    ready_seq = np.asarray(ready_seq)
    T = max(K, ready_seq.shape[0])

    fifo = CommonFIFO(N=N, K=K)
    rows = []
    for cy in range(T):
        wvalid = cy < K
        if wvalid:
            wdata = [int(weights[cy][c]) for c in range(N)]
            wdata_arg = wdata
        else:
            wdata = [0] * N
            wdata_arg = False

        ready = [bool(ready_seq[cy][c]) if cy < ready_seq.shape[0] else False
                 for c in range(N)]
        fifo.ws_update(wdata_arg, ready)
        out_data = [int(x) for x in fifo.data]
        out_vld  = [int(bool(v)) for v in fifo.vld]
        fifo.commit()

        rows.append([cy, int(wvalid)]
                    + [int(v) for v in wdata]
                    + [int(v) for v in ready]
                    + out_vld
                    + out_data)

    import os
    os.makedirs(os.path.dirname(dump_path), exist_ok=True)
    with open(dump_path, "w") as fh:
        _write_wfifo_dump(fh, weights, N, K, T, rows)

    return rows


def _write_wfifo_dump(fh, weights, N, K, T, rows):
    fh.write(f"# wfifo_tb golden vector (WS)  N={N} K={K} NCYC={T}\n")
    fh.write("# 数据行: cy wvalid wdata[N] ready[N] vld[N] data[N]，每行 2+4N 个数\n")
    fh.write("# 排程：cy 0..K-1 由 DMA 同步广播一行 N 条灌满 1 个 tile；之后 ready 控制读出\n")
    fh.write("#\n")
    fh.write(f"# weights ({K}x{N}) =\n")
    for r in range(weights.shape[0]):
        row_str = " ".join(f"{int(v):4d}" for v in weights[r])
        fh.write(f"#   [{row_str} ]\n")
    fh.write("#\n")

    parts = ["cy", "wv"]
    parts += [f"wd{c}" for c in range(N)]
    parts += [f"rd{c}" for c in range(N)]
    parts += [f"vl{c}" for c in range(N)]
    parts += [f"dt{c}" for c in range(N)]
    fh.write("# " + " ".join(f"{p:>4}" for p in parts) + "\n")

    fh.write(f"{N} {K} {T}\n")
    for row in rows:
        fh.write("  " + " ".join(f"{v:>4d}" for v in row) + "\n")


def test_wfifo_ws_dump_cosim():
    """单 tile WS dump：cy 0..3 写一行 4 条 × 4 拍；cy 4..7 全 ready 读出；cy 8 空 fifo 试 ready=1。

    这是 _dump_wfifo_ws 的示例模板。要改激励，复制本函数 + 改 weights / ready_seq / dump_path，
    再去 tb/wfifo_tb/run_vcs.py 的 TESTS 字典加一条 (n, depth, txt, pytest)。
    """
    import os
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    rng = np.random.default_rng(817)
    N, K = 4, 4
    weights = rng.integers(-3, 4, size=(K, N))

    T = 12
    ready_seq = np.zeros((T, N), dtype=int)
    ready_seq[4:8, :] = 1                                # cy 4..7 全开 ready
    ready_seq[8, :]   = 1                                # cy 8 ready=1 但 fifo 空 → vld=0

    _dump_wfifo_ws(weights, ready_seq,
                   dump_path=os.path.join(root, "build", "wfifo_cosim", "ws.txt"))
