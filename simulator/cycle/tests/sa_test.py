"""SA-level cosim 黄金向量 dump。

不走 controller，直接按已知排程驱动 spatial_array model，逐拍 dump 输入 + 期望
sa.data[AR-1] 到 build/sa_cosim/*.txt。tb/sa_tb 用同一份 .txt 喂 RTL 对拍。

跟 controller_ws_test 区别：那个 dump controller 的输出信号；这个 dump SA 的
出口 psum。两者是不同层级的 RTL co-sim。
"""

import numpy as np
from simulator.cycle.sim_model.spatial_array import spatial_array
from simulator.cycle.sim_model.accumulator import Accumulator


def _dump_sa_ws(A, B, AR, AC, dump_path):
    """通用 WS dump：完整 A (M×AR) × 完整 B (AR×N)，按物理 (AR, AC) 自动切 N 方向 G_N 个 tile。
      - K 方向 = AR（不切，即 A.shape[1] == B.shape[0] == AR）
      - N 方向：N 必须能被 AC 整除，G_N = N // AC 个权重 tile，每个写不同 accum slot
      - M 方向 = A.shape[0]（不切，一次喂完）

    排程（不用 controller）：WLOAD B[0] → sw → [STREAM_i + WLOAD B[i+1]] → sw → ... → STREAM_{G_N-1} → DRAIN。
      - 激活按 lane skew（lane r 晚 r 拍）
      - 下一 tile WLOAD 按 col stagger（col c 晚 c 拍，等 sw 让出 shadow）
    golden = spatial_array model 同一份排程的逐拍 sa.data[AR-1]，sa_tb 用同 .txt 喂 RTL 对拍。

    返回 (output, A @ B)，output 形状 (M, N) = G_N 个 accum slot tile 横向拼接。"""
    A = np.asarray(A)
    B = np.asarray(B)
    M, K = A.shape
    K2, N = B.shape
    assert K == AR and K2 == AR, f"K 方向必须 = AR={AR}（A.shape={A.shape}, B.shape={B.shape}）"
    assert N % AC == 0, f"N={N} 必须能整除 AC={AC}"
    G_N = N // AC
    F = M
    L = 2

    B_tiles = [B[:, g * AC:(g + 1) * AC] for g in range(G_N)]

    sa = spatial_array(AR, AC, latency=L,
                       is_shift_col=0, is_shift_row=1, is_shift_acc_d=1, is_shift_acc_l=0)
    cap_delay = (AR - 1) + L + 1
    accum = Accumulator(num_rows=F, num_cols=AC, cap_delay=cap_delay)

    # 注：b_sw 第 0 列加了 FF 后 swap 比之前晚 1 拍。为了保持 PE 计算时与新 active weight 对齐，
    # 把 sw 脉冲提前 1 拍（T_SW[0]=AR-1 而不是 AR），T_STR 保持原口径。
    T_SW  = [0] * G_N
    T_STR = [0] * G_N
    T_SW[0]  = AR - 1
    T_STR[0] = AR + 1                        # = T_SW[0] + 2，跟 b_sw FF 1 拍补偿对齐
    for i in range(1, G_N):
        T_SW[i]  = T_STR[i - 1] + (F + AR - 1) - 1   # 跟 T_SW[0] 一样的相对节奏
        T_STR[i] = T_SW[i] + 2
    NCYC = T_STR[G_N - 1] + (F + AR - 1) + cap_delay + AC

    rows = []
    for cy in range(NCYC):
        a_data = [0] * AR; a_vld = [False] * AR
        b_data = [0] * AC; b_vld = [False] * AC
        b_sw   = [False] * AR

        if cy < AR:                                          # WLOAD B_tiles[0] 同步喂
            k = cy
            for c in range(AC):
                b_data[c] = int(B_tiles[0][AR - 1 - k][c]); b_vld[c] = True
        else:                                                # WLOAD B_tiles[i>=1] 按列 stagger
            for i in range(1, G_N):
                base = T_STR[i - 1]
                for c in range(AC):
                    k = cy - (base + c)
                    if 0 <= k < AR:
                        b_data[c] = int(B_tiles[i][AR - 1 - k][c]); b_vld[c] = True

        if cy in T_SW:
            b_sw = [True] * AR

        for r in range(AR):                                  # a：lane r 晚 r 拍
            for tstr in T_STR:
                t = cy - tstr - r
                if 0 <= t < F:
                    a_data[r] = int(A[t][r]); a_vld[r] = True
                    break

        m, slot, cap_vld = 0, 0, False
        for i, tstr in enumerate(T_STR):
            if tstr <= cy < tstr + F:
                m, slot, cap_vld = cy - tstr, i, True
                break

        sa.update(a_data, a_vld, b_data, b_vld, b_sw=b_sw)
        accum.update(list(sa.data[AR - 1]), cap_vld, m, slot, False)
        sa.commit(); accum.commit()

        exp = [int(x) for x in sa.data[AR - 1]]
        rows.append([cy]
                    + [int(v) for v in a_data]
                    + [int(v) for v in a_vld]
                    + [int(v) for v in b_data]
                    + [int(v) for v in b_vld]
                    + [int(v) for v in b_sw]
                    + exp)

    import os
    os.makedirs(os.path.dirname(dump_path), exist_ok=True)
    with open(dump_path, "w") as fh:
        _write_sa_cosim_dump(fh, A, B, AR, AC, F, G_N, NCYC, rows)

    output = np.zeros((M, N), dtype=int)
    for g in range(G_N):
        output[:, g * AC:(g + 1) * AC] = np.array(accum.get_tile(g))
    return output, A @ B


def _write_sa_cosim_dump(fh, A, B, AR, AC, F, G_N, NCYC, rows):
    """带矩阵注释 + 列头的对拍向量文件。所有 `#` 注释行都在 `AR AC F NCYC` 头之前，
    sa_tb 读到第一个非 `#` 行即头部；后续数据行无 `#`，fscanf 按 token 解析。
    每行：cy a[0..AR-1] av[0..AR-1] b[0..AC-1] bv[0..AC-1] bs[0..AR-1] ex[0..AC-1]。"""
    M_, _ = A.shape
    _, N_ = B.shape

    def fmt_matrix(name, X):
        out = [f"# {name} ="]
        for r in range(X.shape[0]):
            row_str = " ".join(f"{int(v):4d}" for v in X[r])
            out.append(f"#   [{row_str} ]")
        return "\n".join(out) + "\n"

    fh.write(f"# sa_tb golden vector  M={M_} K={AR} N={N_}  systolic AR={AR} AC={AC}  G_N={G_N}  F={F}  NCYC={NCYC}  latency=2\n")
    fh.write(f"# schedule: WLOAD B[0] -> sw -> [STREAM_i + WLOAD B[i+1]] -> sw -> ... -> STREAM_{G_N-1} -> DRAIN\n")
    fh.write("#\n")
    fh.write(fmt_matrix(f"A ({M_}x{AR})", A))
    fh.write(fmt_matrix(f"B ({AR}x{N_})", B))
    fh.write("#\n")
    fh.write(fmt_matrix(f"A @ B ({M_}x{N_})", A @ B))
    fh.write("#\n")
    fh.write(f"# N 切分: G_N={G_N} 个 tile，tile g 喂 B[:, g*AC:(g+1)*AC]，写 accum slot=g，最终拼回 A@B\n")
    fh.write("#\n")
    fh.write("# 数据行布局：cy | a[0..AR-1] | av[0..AR-1] | b[0..AC-1] | bv[0..AC-1] | bs[0..AR-1] | ex[0..AC-1]\n")

    parts = ["cy"]
    parts += [f"a{r}"  for r in range(AR)]
    parts += [f"av{r}" for r in range(AR)]
    parts += [f"b{c}"  for c in range(AC)]
    parts += [f"bv{c}" for c in range(AC)]
    parts += [f"bs{r}" for r in range(AR)]
    parts += [f"ex{c}" for c in range(AC)]
    fh.write("# " + " ".join(f"{p:>4}" for p in parts) + "\n")
    fh.write(f"{AR} {AC} {F} {NCYC}\n")
    for row in rows:
        fh.write("  " + " ".join(f"{v:>4d}" for v in row) + "\n")


def test_ws_dump():
    """导出 sa_tb N 切分对拍向量 build/sa_cosim/ws_switch.txt（M=4 K=4 N=4，systolic AR=4 AC=2，G_N=2）。

    这是 _dump_sa_ws 的示例模板。要新增用例，复制本函数改名 + 改 A/B/AR/AC/dump_path，
    再去 tb/sa_tb/run_vcs.py 的 TESTS 字典加一条对应的 (row, col, txt, pytest) 项。
    """
    import os
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    rng = np.random.default_rng(4204)
    A = rng.integers(-3, 4, size=(4, 2))                  # M=4, K=2
    B = rng.integers(-3, 4, size=(2, 4))                  # K=2, N=4
    output, want = _dump_sa_ws(A, B, AR=2, AC=2,
                               dump_path=os.path.join(root, "build", "sa_cosim", "ws_switch.txt"))
    np.testing.assert_array_equal(output, want)
