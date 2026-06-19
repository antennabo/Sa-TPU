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
│   ├── functional/ 功能级（非周期）：数值正确性分析
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

### RTL 仿真与逐拍对拍（cosim）

让 RTL 阵列 `rtl/systolic_array.sv` 跟 Python golden（`simulator/cycle/sim_model/`）逐拍比对。
思路：golden 跑一遍 WS 计算、把每拍的边界激励和底行输出录成向量；testbench 回放同样的输入、
逐拍比对输出。需要 `iverilog` / `vvp`（看波形再装 `gtkwave`）。

```bash
# 1. 生成逐拍向量 build/sa_cosim/ws1.txt（latency=2 对应 RTL PIPE_MUL=1）
python -m pytest simulator/cycle/sim_model/ws1_e2e_test.py::test_ws1_dump_cosim

# 2. 进 testcase 目录跑 testbench
cd tb/sa_tb
python run_test.py sa_tb          # 逐拍比对，打印 PASS / MISMATCH
python run_test.py sa_tb -vcd     # 额外生成 cpu_wave.vcd，gtkwave 看波形
```

testcase 名 `sa_tb` 仅用于命名。新增一个被测模块时，在 `tb/` 下照 `sa_tb/` 建一个目录
（含 `*.sv` / `filelist_tb.f` / `run_test.py`）即可。

> 注意：testbench 读阵列输出走层次化引用 `dut.out[i]`，不读顶层端口 `out[i]`——
> iverilog 对 unpacked array 输出端口传播有缺陷，直接读端口会得到 X（是仿真器限制，非 RTL 缺陷）。

### 其它脚本

- `scripts/perf_sim.py` — 把功能级 + 周期级分析串起来的编排器（`class Simulator`）。
- `scripts/export_cosim.py` — 导出 RTL 对拍向量（`build/cosim/*.hex`）。

## 设计文档

设计细节见 [doc/](doc/)，按模块组织，例如 `SA_array_design.md`（脉动阵列）、
`controller_design.md`（控制器）、`accumulator_design.md`（累加器）、
`WS_weight_design.md` / `OS_restore_design.md`（WS / OS 两种数据流）、`isa.txt`（指令集）。

## 备注

- `build/` 下为生成产物，已被 `.gitignore` 忽略；全新 clone 后需重新生成（跑编译流程或训练脚本）
  才会出现 tiles / cosim / 数据集。
</content>
