# Sa-TPU

一个脉动阵列（Systolic Array）TPU 的全栈实验项目：从 PyTorch 训练的 AI 模型出发，经编译器
解析/分块/量化，到 Python 仿真模型（功能级 + 周期级）验证，最终落到 RTL 硬件实现。

## 流水线与目录

项目按工作流分为四个阶段，每个阶段对应一个顶层目录：

```
阶段                         产物                          目录
──────────────────────────────────────────────────────────────────
1. torch 训练的 AI 模型   →  训练好的网络 / 权重        →  model/
2. compiler 解析它        →  量化、tiling               →  compiler/
                             输出 RAM 结构 + 指令
3. 仿真模型 (Python)       →  ┌ functional 功能/数值     →  simulator/
                             └ cycle 周期精确
4. rtl                    →  硬件实现                    →  rtl/

doc/  贯穿全程的设计文档        build/  生成的中间产物（已 gitignore）
```

```
Sa-TPU/
├── model/          阶段1：PyTorch 模型 (SimpleCNN)、训练与推理脚本、权重 .pth
├── compiler/       阶段2：编译器
│   ├── frontend/   解析 torch 模型 → IR (modelparser, ir)
│   ├── hw.py       硬件配置 (HardwareConfig)
│   ├── transform.py  tiling + mapping
│   ├── quantize.py   量化
│   ├── instr.py      指令发射 (emit_matmul_program)
│   ├── data.py       后端数据结构
│   └── backend.py    IR → 量化 → 分块 → 映射 → 编译
├── simulator/      阶段3：Python 仿真模型
│   ├── functional/ 功能级（非周期）：静态分析（analyzer）+ 数值参考模型
│   │                （numerical_model：MAC/requant/pool）+ tb golden 生成
│   │                （tb_stimulus.dump_{linear,conv}_layer）
│   └── cycle/      周期精确：cycle_analyzer + sim_model/（pe / controller /
│                   spatial_array / accumulator / commonbuf / commonfifo ...）
│                   tests/  本层全部单元/e2e 测试
├── rtl/            阶段4：SystemVerilog 硬件实现
│   ├── pe.sv             处理单元 (MAC)
│   ├── systolic_array.sv 脉动阵列
│   └── common/           原语：data_buf / weight_buf / dff_* ...
├── tb/             SystemVerilog testbench（按 testcase 分目录，如 sa_tb/）
├── utils/          共享数值算子 (conv2d / np_linear / quantize ...)
├── scripts/        入口脚本（见下）
├── doc/            设计文档
├── build/          生成产物：tiles/*.npy、cosim/*.hex、sa_cosim/*.txt、data/MNIST（已 gitignore）
└── conftest.py     把仓库根注入 import path，使顶层包可被测试直接 import
```

## 环境

```bash
pip install -r requirements.txt   # numpy, torch, torchvision, pytest
```

## 使用指导

所有命令都从**仓库根目录**执行。

### 跑测试

```bash
python -m pytest            # 全部（当前 139 个）
python -m pytest -q simulator/cycle   # 只跑周期级仿真模型
```

### 周期级单跑（只依赖 numpy，不走 torch 全链路）

从 `build/tiles/` 载入某层 tile，跑 `CycleAccurateAnalyzer`，打印结果：

```bash
python scripts/run_cycle.py [layer]   # layer 默认 layer3
```

### 全链路（torch 模型 → 编译 → 仿真）

```bash
python scripts/run.py
```

需要 `model/simple_cnn.pth` 与 `build/data/`（MNIST）就位。模型可用 `python model/train.py`
（在 `model/` 目录下运行）重新训练得到。

### satpu_top 逐层 golden（per-tensor symmetric qint8，契约见 [D10](doc/decisions.md)）

给 `satpu_top_tb` 生成每一层的对拍向量。从 `SimpleCNN` fp32 权重出发，走 fp32 forward +
自建对称量化（`scale = max_abs / 127`，`zp = 0`），产出 `A / B / bias / Y_int32 / Y_int8 / params`
供 tb 逐层比对。

**不走** `torch.ao.quantization`：其 quantized Linear 只支持 quint8 activation，无法给出
zp = 0 的 signed int8，与 SaTPU 契约冲突。整套流程走 numpy 自己做量化 + 两级 int32 累加
（SA 内 psum 沿 K 流 + accumulator 跨 WTILE 累加，每处独立饱和）。

```bash
python -m scripts.gen_linear_golden               # 默认 chained（端到端串联），batch=1
python -m scripts.gen_linear_golden --batch 8     # 8 张 MNIST 图，一起校验 argmax + accuracy
python -m scripts.gen_linear_golden --isolated    # 每层从 fp32 独立采样，不用上一层 int8 输出
```

**产物**：`build/satpu_top_cosim/{conv0, fc1, fc3}/`

| 文件 | 内容 |
|---|---|
| `A.txt` | `(M_pad, K_pad)` int8，本层输入激活 |
| `B.txt` | `(K_pad, N_pad)` int8，权重（已按 SA 面朝向转置） |
| `bias.txt` | `(N_pad,)` int32，`round(bias_fp32 / (scale_x·scale_w))` 折算后 |
| `Y_int32.txt` | `(M_pad, N_pad)` int32，MAC + bias 之后、requant 之前 |
| `Y_int8.txt` | `(M_pad, N_pad)` int8，requant + 可选 ReLU 融合之后 |
| `Y_int8_pool.txt` | 仅 conv0：post-MaxPool 的 int8（下一层 A 的来源，chained 模式用） |
| `params.txt` | `scale_x/w/y`、`M0`、`shift`、`relu` 位、`a_max/y_max`、conv 层元信息 |

**Chained vs Isolated**：
- Chained（默认）：conv0 的 `Y_int8_pool` 直接喂进 fc.1 作 A，fc.1 的 `scale_x = conv0.scale_y`
  （硬件真实数据流）
- Isolated：每层的 A 都从 fp32 网络重新抓 activation 再量化（每层独立测试用）

**末尾自动跑 argmax 对拍**：
```
[verify] fp32 ↔ golden agree: 507/512  (99.02%)
[verify] fp32   accuracy:             87.70%
[verify] golden accuracy:             87.89%
[verify] quantization loss (Δacc):    -0.20 pp
```

- **agree ≥ 99%**：pipeline 数值语义健康（fp32 与 golden argmax 一致）
- **Δacc 在 ±0.5 pp 内**：量化损失在业界"接近无损"区间（MNIST 尺度）
- 若绝对 accuracy 低于预期，先怀疑 `simple_cnn.pth` 训练不充分（跑 `model/train.py` 补训），
  跟量化 pipeline 无关

### RTL 仿真与逐拍对拍（cosim）

让 RTL 阵列 `rtl/systolic_array.sv` 跟 Python golden（`simulator/cycle/sim_model/spatial_array.py`）
逐拍比对。思路：通用 dump 函数 `_dump_sa_ws(A, B, AR, AC, ...)` 接受**完整** A (M×AR) 和 B (AR×N)，
自动按 N 方向切 `G_N = N // AC` 个 tile 串行执行 WS（`WLOAD B[0] → sw → STREAM_i + WLOAD B[i+1] → ...`），
逐拍录下 `sa.data[AR-1]` 作为 golden；testbench 用同一份 .txt 喂 RTL 比对。

工具链：VCS（编译 + 跑）+ verdi（看 fsdb 波形）。

```bash
# 1. 生成逐拍向量 build/sa_cosim/ws_switch.txt
python -m pytest -k test_ws_dump

# 2. 进 testcase 目录跑 RTL（默认 -t switch）
cd tb/sa_tb
python run_vcs.py                 # 编译 + 跑，打印 PASS / MISMATCH
python run_vcs.py -wave           # 同上 + 跑完自动 verdi 打开 cpu_wave.fsdb
python run_vcs.py -fsdb           # 只 dump fsdb，不自动开 verdi
python run_vcs.py -clean          # 清 VCS 产物 (simv / csrc / verdiLog / ...)
```

**改矩阵 / 加新用例：**

1. 编辑 `simulator/cycle/tests/sa_test.py::test_ws_dump`（或仿照它新建一个 test 函数），
   改 `A` / `B` / `AR` / `AC` / `dump_path`。约束：K 维度（`A.shape[1]` 和 `B.shape[0]`）必须 = `AR`；
   N 维度（`B.shape[1]`）必须能整除 `AC`。
2. 改 `tb/sa_tb/run_vcs.py::TESTS` 字典里对应条目的 `row` / `col` 跟 `AR` / `AC` 对上
   （否则 sa_tb 编译会用错维度 → "FATAL: 维度不匹配"）；新用例就加新 key。
3. 重跑上面的两步。

`tb/sa_tb/sa_tb.sv` 通过 `+define+SA_ROW_N=N +define+SA_COL_N=M` 编译时参数化，
通过 `+TXT=<path>` plusarg 运行时指定 golden 文件——`run_vcs.py` 自动按 TESTS 字典传这些。

**weight_fifo（[tb/wfifo_tb/](tb/wfifo_tb/)）—— 同一套机制，被测对象换成 [rtl/weight_fifo.sv](rtl/weight_fifo.sv)**

`_dump_wfifo_ws(weights, ready_seq, dump_path)`（[simulator/cycle/tests/wfifo_test.py](simulator/cycle/tests/wfifo_test.py)）
用 `commonfifo.py::CommonFIFO.ws_update` 跑一遍逐拍，dump 每拍
`(cy, wvalid, wdata[N], ready[N], vld[N], data[N])` 到 `build/wfifo_cosim/ws.txt`；
wfifo_tb 用同一份 .txt 喂 RTL，比对 `vld/data`。

```bash
# 1. 生成 golden
python -m pytest -k test_wfifo_ws_dump_cosim

# 2. 跑 RTL
cd tb/wfifo_tb
python run_vcs.py                 # 默认 -t ws
python run_vcs.py -wave           # + 自动 verdi 打开 fsdb


# 1) 生成 switch.txt（包含 inputs + golden）
pytest -k test_tpu_top_dump_switch

# 2) 进 tb 目录，跑 VCS
cd tb/tpu_top_tb
python run_vcs.py -t switch              # 只跑 sim
python run_vcs.py -t switch -fsdb        # + 出 cpu_wave.fsdb 波形
python run_vcs.py -t switch -wave        # + 自动 verdi 打开
python run_vcs.py -clean                 # 清理产物
```

`tb/wfifo_tb/wfifo_tb.sv` 通过 `+define+WFIFO_N=N +define+WFIFO_DEPTH=D` 配置；改激励 / 加新用例的姿势跟
sa_tb 一致（改 pytest 里的 `weights` / `ready_seq` + 同步 `TESTS` 字典里的 `n` / `depth`）。

### 其它脚本

- `scripts/perf_sim.py` — 把功能级 + 周期级分析串起来的编排器（`class Simulator`）。
- `scripts/export_cosim.py` — 导出 RTL 对拍向量（`build/cosim/*.hex`）。

## 设计文档

入口：[doc/README.md](doc/README.md)。结构：
- [doc/architecture.md](doc/architecture.md) — 系统总览（先读这个）
- [doc/systolic_array.md](doc/systolic_array.md) — sa + pe + 权重双缓冲
- [doc/controller_ws.md](doc/controller_ws.md) — 6 态 FSM + 三道控制波 + 邻接接口
- [doc/decisions.md](doc/decisions.md) — 设计决策日志
- [doc/roadmap.md](doc/roadmap.md) — RV core + TPU 联动路线
- [doc/isa.txt](doc/isa.txt) — ISA 草案
- [doc/legacy/](doc/legacy/) — 早期 OS / 三模式统一设计稿（DEPRECATED）

## 备注

- `build/` 下为生成产物，已被 `.gitignore` 忽略；全新 clone 后需重新生成（跑编译流程或训练脚本）
  才会出现 tiles / cosim / 数据集。
</content>
