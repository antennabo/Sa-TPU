# TinyTPU 项目进度文档

> 面向 FPGA 的精简 TPU 实现 | 首期目标：CNN 推理 | 目标平台：PYNQ 开发板

---

## 总览

| 章节 | 内容 | 状态 |
|------|------|------|
| 阶段一 | 基准模型训练 | 已完成 |
| 阶段二 | 编译层 | 进行中 |
| ISA | 指令集架构设计 | 待决策 |
| 阶段三 | Python Cycle-Accurate 仿真 | 进行中 |
| 　3.1 | 静态分析（Roofline + Memory） | 已完成 |
| 　3.2 | 数值分析（FP32 vs INT8） | 已完成 |
| 　3.3 | 硬件仿真模型（PE / 脉动阵列） | 已完成 |
| 　3.4 | Cycle-Accurate 端到端集成 | 进行中 |
| 　3.5 | DDR 仿真模型 | 待完成 |
| 　3.6 | 指令驱动仿真 | 待完成 |
| 阶段四 | SystemVerilog RTL 实现 | 待开始 |
| 　4.1 | AXI 链路验证 | 待开始 |
| 　4.2 | 脉动阵列 RTL + Testbench | 待开始 |
| 　4.3 | 指令执行与控制逻辑 | 待开始 |
| 　4.4 | AXI 接口对接（集成） | 待开始 |
| 阶段五 | FPGA 上板验证 | 待开始 |

**整体技术路线：**

```
PyTorch 模型 → 编译层（Frontend + Backend） → Python 仿真 → SV RTL → PYNQ FPGA
```

**仿真最终目标：**

```
初始化：指令序列 → InstrBuffer | 激活数据 → SRAM Buffer | 网络权重 → DDR
执行：  InstrBuffer 逐条取指 → 驱动脉动阵列完成计算（含 DDR stall 建模）
结束：  计算结果 → 写回 DDR
```

---

## 阶段一：基准模型训练（已完成）

训练一个轻量 CNN 作为项目全程的验证基准（ground truth），用于检验编译器输出和硬件计算结果的正确性。

### 模型结构（SimpleCNN）

```
输入              [1, 1, 28, 28]
Conv2d(1→8, 3×3, padding=1)  → [1, 8, 28, 28]
ReLU
MaxPool2d(2×2)               → [1, 8, 14, 14]
Flatten                      → [1, 1568]
Linear(1568 → 64)
ReLU
Linear(64 → 10)              → logits [1, 10]
```

### 训练配置

| 项目 | 内容 |
|------|------|
| 数据集 | MNIST（均值/方差归一化） |
| 优化器 | Adam，lr = 1e-3 |
| 损失函数 | CrossEntropyLoss |
| 训练轮数 | 100 epochs，batch size = 64 |
| 测试精度 | 待填写 |
| 产出文件 | `simple_cnn.pth` |

---

## 阶段二：编译层（进行中）

将 PyTorch 模型转换为硬件可执行的中间表示（IR），并完成量化与分块，为后续仿真和 RTL 提供输入。

### 整体流程

```
PyTorch 模型
  → torch.export（导出计算图）
  → Frontend Parser（遍历图节点，映射到自定义 IR）
  → IR
  → 量化（Quantize）
  → Tiling
  → 指令映射（待完成）
```

### Frontend（已完成）

使用 `torch.export` 导出计算图，手写 Parser 遍历节点映射到自定义 IR。

| IR 类型 | 对应算子 | 状态 |
|---------|---------|------|
| MatMulIR | Linear / FC 层 | 已完成 |
| Conv2dIR | Conv2d | 已完成 |
| ElementwiseIR | ReLU / MaxPool / Flatten | 已完成 |
| FusedConvReluIR | Conv2d + ReLU 融合（预留） | 待完成 |

### Backend

| 子模块 | 状态 | 说明 |
|--------|------|------|
| 量化（Quantize） | 已完成 | 精度可配置（INT8 等），NumPy 类型转换，输出 scale 因子 |
| Tiling（线性层） | 已完成 | 按固定 tile 尺寸切分，适配 MXU 8×8 |
| Tiling（卷积层） | 待完成 | — |
| 算子融合（Fuse） | 待完成 | 如 Conv + ReLU → FusedConvReluIR |
| 指令映射 | 待完成 | IR → TPU ISA 指令序列 |

---

## 指令集架构设计（ISA）【待决策】

本项目采用固定 64-bit 宽度指令集，优先支持 5 条指令。LOAD/STORE 编码参考 RISC-V 64，MATMUL/ACTIVATION/POOL 参考 TPU v1。

### 指令列表（初步）

| 编码 | 指令 | 说明 |
|------|------|------|
| `000001` | LOAD | DDR → SRAM |
| `000010` | STORE | SRAM → DDR |
| `000011` | MATMUL | 脉动阵列矩阵乘 |
| `000100` | ACTIVATION | 逐元素激活函数 |
| `000101` | POOL | 池化，对应 TPU v1 Normalize/Pool |

### 通用格式

```
 63      58 57                                               0
┌──────────┬────────────────────────────────────────────────┐
│ opcode   │                   payload                      │
│  [6 bit] │                  [58 bit]                      │
└──────────┴────────────────────────────────────────────────┘
```

各条指令的 payload 字段编码待细化。MATMUL 预留 flag 字段（含 accumulate 模式位），其余 flag 位暂不定义语义。

---

## 阶段三：Python Cycle-Accurate 仿真（进行中）

在 RTL 实现之前，用 Python 搭建行为级仿真模型，验证硬件微架构的正确性，并提供逐 cycle 的性能数据作为 RTL 设计的参考基线。

### 最终目标

仿真的完整执行流程如下：

```
初始化：
  指令序列  → InstrBuffer
  激活数据  → SRAM Buffer
  网络权重  → DDR

执行：
  InstrBuffer 逐条取指 → 驱动脉动阵列完成计算（含 DDR stall 建模）

结束：
  计算结果  → 写回 DDR
```

各阶段的仿真均服务于这一目标的闭环验证。

### 硬件配置（HardwareConfig）

所有分析路径和仿真均依赖统一的硬件配置对象：

| 字段 | 示例值 | 说明 |
|------|--------|------|
| `mxu_dim` | `(8, 8)` | MXU 脉动阵列尺寸（M × N） |
| `sram_bytes` | `16 × 1024 × 1024` | 片上 SRAM 总容量 |
| `hbm_bw_gbps` | `900.0` | HBM 带宽（Gbps），用于 Roofline 和 DDR 仿真 |
| `freq_mhz` | `1.0` | 工作频率，cycle → 时间换算基准 |
| `dtype` | `"int8"` | 计算精度 |
| `accum_dtype` | `"int32"` | 累加器精度 |

### 分析路径

仿真器分为三条分析路径，可独立运行：

```
IR + 硬件配置
  ├── 静态分析（Roofline + Memory）   → 性能上界估算
  ├── 数值分析（Numerical）           → 量化精度验证
  └── Cycle 仿真（Cycle-Accurate）    → 逐周期硬件行为
```

### 3.1 静态分析（已完成）

基于 Roofline 模型，对每一层做理论性能分析。支持 Conv2d 和 Linear 层。

| 分析器 | 输出 | 说明 |
|--------|------|------|
| `RooflinePerfAnalyzer` | 延迟 / 利用率 / 吞吐量 | 取算力瓶颈与带宽瓶颈的最大值作为延迟估算 |
| `MemoryAnalyzer` | SRAM 用量 / HBM 用量 / 是否超限 | Tile 粒度 SRAM 估算，悲观假设每层均读写 HBM |

**Roofline 公式（以 Linear 层为例）：**
```
FLOPs       = 2 × M × N × K
算力瓶颈    = FLOPs / (freq × MXU_M × MXU_N × 2)
带宽瓶颈    = bytes / (HBM_BW / 8)
延迟        = max(算力瓶颈, 带宽瓶颈)
利用率      = 算力瓶颈 / 延迟          # < 1 表示内存受限
```

### 3.2 数值分析（已完成）

对整张计算图做全精度（FP32）与量化（INT8）两路前向推理，输出误差统计。

- 量化路径：`x_int8 × W_int8 → INT32 累加 → ×scale 还原 FP32`
- 误差指标：`max_error`、`mean_error`
- 作用：验证阶段二量化方案的精度损失是否在可接受范围内

### 3.3 硬件仿真模型（已完成）

#### 模块层次

```
CycleAccurateAnalyzer
  ├── spatial_array (M×N)
  │     └── pe  ×(M×N)         # 基础 MAC 单元
  ├── row_fifos  [×M]           # activation 入队列（列方向）
  └── col_fifos  [×N]           # weight 入队列（行方向）
```

#### PE（Processing Element）

最小计算单元，单 cycle 执行一次 MAC：

```
next_state = int32(b) × int32(a) + int32(acc)
```

- 输入：INT8（activation `a`，weight `b`）
- 累加器：INT32（防止溢出）
- 采用三段式：compute → commit（类似 RTL 的 next-state / state 分离）

#### 脉动阵列（Spatial Array）

M×N 的 PE 阵列，支持三种数据流：

| 数据流 | 固定数据 | 流动方向 | 适用场景 |
|--------|---------|---------|---------|
| WS（Weight Stationary）| Weight 驻留各 PE | Activation 从第 0 列向右流动，部分和从上向下传递 | 权重复用率高的 FC 层 |
| IS（Input Stationary） | Activation 驻留各 PE | Weight 从第 0 行向下流动，部分和从左向右传递 | 输入复用率高的场景 |
| OS（Output Stationary）| 部分和驻留各 PE | Activation 和 Weight 同时流入，双向脉动 | 通用，输出不需搬移 |

每个 cycle 的执行序列：

```
control()  → 从 FIFO 取数，完成阵列内数据移位
compute()  → 所有 PE 计算 next_state = b × a + acc
commit()   → 所有 PE 将 next_state 写入 state
```

#### 数据加载（FIFO 错排）

为实现脉动效果，数据在送入阵列前需按行/列索引做错排（staggered loading）：

```
WS/OS：col_fifo[i] 前插 i 个 0（activation 按行错排）
IS/OS：row_fifo[j] 前插 j 个 0（weight 按列错排）
```

#### 卷积的处理（待完成，本阶段不支持）

Conv2d 通过 im2col 转换为矩阵乘法后送入脉动阵列：

```
输入 [N, C, H, W]  --im2col-->  A [N×H_out×W_out, C×R×S]
权重 [K, C, R, S]  --reshape--> B [C×R×S, K]
                                ↓
                            脉动阵列 A × B
```

### 3.4 Cycle-Accurate 端到端集成（进行中）

硬件模型单 tile 仿真已验证通过，当前正在对接：

| 子任务 | 状态 |
|--------|------|
| 单 tile 脉动仿真（OS/WS/IS） | 已完成 |
| 多 tile 循环（tile loop） | 进行中 |
| 全图逐层 cycle 统计 | 进行中 |
| cycle 结果输出为性能报告 | 待完成 |

#### 多 tile 循环（tile loop）

Tiling 阶段将矩阵切分为 `(num_m, num_k)` 块的 A_tiles 和 `(num_k, num_n)` 块的 B_tiles，保存为 `.npy` 文件。Cycle 仿真需要遍历所有 tile 组合，将各块的结果累加还原出完整输出矩阵。

```
A_tiles: shape (num_m, num_k, tile_M, tile_K)
B_tiles: shape (num_k, num_n, tile_K, tile_N)

for im in range(num_m):
  for in in range(num_n):
    partial = zeros(tile_M, tile_N)
    for ik in range(num_k):                   # K 维规约
      simulate(A_tiles[im, ik], B_tiles[ik, in])
      partial += sa.get_result()
    output[im, in] = partial
```

**当前状态：** `analyze_from_tiles` 中仅运行了 `A_tiles[0,0] × B_tiles[0,0]` 单块，tile loop 尚未接入。

**待完成：**
- 实现 `(num_m, num_k, num_n)` 三重 tile 循环
- 跨 tile 的 partial sum 累加逻辑
- 边界 tile 的 padding 处理：Tiling 阶段已统一补零到 tile 尺寸（`pad_tile`），仿真时直接使用；累加时需记录各 tile 的有效区域，避免将 padding 引入的无效结果计入最终输出

**验证方式：** 以单层 Linear 层为目标，将 tile loop 仿真还原出的完整输出矩阵，与数值分析（`NumericalAnalyzer`）的 INT8 前向推理结果逐元素比对，两者一致则视为正确。

#### 全图逐层 cycle 统计

目前 `CycleAccurateAnalyzer.analyze()` 的核心逻辑被注释，尚未与 `Simulator.run_cycle()` 的全图遍历打通。需要为每个 Conv2dIR / MatMulIR 层独立完成 tile loop 仿真，并将各层的 cycle 数累计到 `total_cycles`。

```
全图遍历：
  for op in irs:
    if op 是 Conv2dIR 或 MatMulIR:
      load_tiles(layer_i)
      执行 tile loop 仿真
      layer_cycles = total_cycles（本层）
      graph_total_cycles += layer_cycles
    elif op 是 ElementwiseIR:
      cycle 数按固定延迟估算（或跳过）
```

**当前阻塞点：** Conv2d 的 tile 切分尚未完成（见阶段二），导致全图流水无法闭环。

#### cycle 结果输出为性能报告

单层仿真跑完后，需将 cycle 数转换为可读的性能指标：

```
latency_ns  = total_cycles / (freq_mhz × 1e6) × 1e9
peak_cycles = FLOPs / (MXU_M × MXU_N × 2)      # 理论最优
utilization = peak_cycles / total_cycles          # 越接近 1 越好
throughput  = FLOPs / latency_ns
```

输出结构复用现有 `PerfResult`，与静态分析的 Roofline 结果对比，可以直观看出仿真与理论上界的差距（pipeline bubble、FIFO 等待等开销）。

### 3.5 DDR 仿真模型（待完成）

在 cycle-accurate 仿真的基础上，加入简化的 DDR 访存模型，重点捕捉**计算被内存中断**和**burst 恢复**两种现象，量化内存瓶颈对实际性能的影响。

#### 模型结构

```
DDRModel
  ├── latency_cycles     # 单次 DDR 访问延迟（固定值，例如 100 cycles）
  ├── bandwidth_bytes    # 每 cycle 可传输字节数（由 hw.hbm_bw_gbps 推算）
  └── burst_size_bytes   # 单次 burst 传输的数据量

SRAMBuffer
  ├── capacity_bytes     # SRAM 总容量
  ├── occupancy          # 当前剩余数据量
  └── consume(n)         # 每 cycle 消耗 n 字节（送往脉动阵列）
```

#### 仿真循环逻辑

每个 cycle 判断 SRAM buffer 是否有足够数据供计算使用：

```
每个 cycle：
  if buffer.occupancy >= 本 tile 所需字节:
    → COMPUTE：推进脉动阵列仿真，buffer 消耗对应字节
  else:
    → STALL：等待 DDR burst 完成，stall_cycles += 1
      DDR burst 结束（经过 latency + 传输时间）后：
      → buffer 补充，恢复 COMPUTE 状态
```

#### 验证方式

与无 DDR 模型的纯计算 cycle 结果对比，差值即为内存引入的额外开销，验证 `stall_cycles + compute_cycles = total_cycles`。

#### 输出指标

| 指标 | 说明 |
|------|------|
| `compute_cycles` | 脉动阵列实际运算的周期数 |
| `stall_cycles` | 等待 DDR 数据的空转周期数 |
| `memory_efficiency` | `compute_cycles / (compute_cycles + stall_cycles)` |

### 3.6 指令驱动仿真（待完成）

3.4 的 tile loop 仿真由 `.npy` 文件直接驱动，3.6 目标是将驱动方式切换为**真实指令序列**，打通编译层 → 仿真器的完整链路。

**执行流程：**

```
编译层产出指令序列（LOAD / MATMUL / ACTIVATION / STORE）
  → 写入仿真器 InstrBuffer
  → 仿真器逐条译码，驱动 tile loop、DDR stall、activation/结果搬运
  → 输出结果与数值分析比对
```

**依赖：**
- 阶段二指令映射完成（IR → ISA 指令序列）
- 3.4 tile loop 和 3.5 DDR 仿真模型完成

**验证方式：**
- 编译同一组权重和输入，分别走数值分析路径和指令驱动路径，输出结果一致则闭环验证通过。

---

## 阶段四：SystemVerilog RTL 实现（待开始）

### 4.1 接口调试（AXI 链路验证）

第一步优先打通完整的数据链路，目的有两个：一是验证 ARM、共享缓存（BRAM）、DDR 三者之间的 AXI 通路正确性；二是确立 AXI 接口规范，作为后续 4.3 控制逻辑对接的设计基础。

**目标数据流：**

```
ARM (PS)
  → AXI → 共享缓存（BRAM in PL）
  → AXI DMA → DDR
  → AXI DMA → 读回共享缓存
  → AXI → 返回 ARM 验证
```

**涉及 IP：**

| 接口 | AXI 类型 | IP |
|------|----------|----|
| ARM ↔ 共享缓存 | AXI | AXI BRAM Controller |
| 共享缓存 ↔ DDR | AXI + DMA | AXI DMA |

**验证方式：**
- ARM 写入一组已知数据到共享缓存，经 DMA 搬至 DDR，再读回，与原始数据逐字节比对，全部一致则链路通过。
- 同时固化地址空间划分（Data RAM 段 / Instr FIFO 段）和接口时序，供 4.3 复用。

### 4.2 脉动阵列 RTL + Testbench

接口调试通过后，实现脉动阵列 RTL，并与阶段三 Python 仿真模型（3.3）做行为比对。

**实现内容：**
- PE 模块：INT8 输入，INT32 累加，三段式时序（与 Python 模型一致）
- Spatial Array：M×N PE 阵列，支持 OS/WS/IS 数据流
- FIFO：数据错排缓冲

**验证方式：**
- 用相同的输入 tile 分别驱动 Python 仿真（`spatial_array`）和 SV testbench
- 逐 cycle 比对寄存器输出，结果一致则通过

### 4.3 指令执行与控制逻辑

在脉动阵列基础上增加控制器和各级 buffer，整体结构参照阶段三 Cycle-Accurate 仿真模型。

**模块结构：**

```
ARM (PS)
  ↓ AXI
AXI → RAM 转换层
  ├── 地址段 A → Data RAM（activation）
  └── 地址段 B → Instr FIFO

DDR RAM 接口（本阶段用 RAM 替代实际 DDR）
  └── 存储权重 / 输出结果

Instr FIFO → Controller（取指、译码、发射）
                  ↓
    ┌─────────────┴──────────────┐
  Data RAM                  DDR RAM 接口
 (activation)               (weight 读取)
    └─────────────┬──────────────┘
            Spatial Array（4.2）
                  ↓
          Accumulator Buffer
                  ↓
            DDR RAM 接口（结果写回）
```

**接口说明：**

| 接口 | 本阶段实现 | 最终对接 |
|------|-----------|---------|
| ARM 写指令 | AXI → Instr FIFO（地址段 B） | 复用 4.1 AXI 链路 |
| ARM 读写 activation | AXI → Data RAM（地址段 A） | 复用 4.1 AXI 链路 |
| 权重 / 结果 ↔ DDR | RAM 接口（替代） | AXI DMA（4.1） |

**验证方式：**
- testbench 通过 AXI 写入指令到 Instr FIFO，写入 activation 到 Data RAM，写入权重到 DDR RAM 接口
- 执行完成后，读取 DDR RAM 接口的输出结果，与阶段三数值分析结果逐元素比对，一致则通过

### 4.4 AXI 接口对接（集成）

将 4.3 中的 RAM 接口 stub 替换为 4.1 已验证的真实 AXI 链路，完成完整的硬件系统集成。

**替换内容：**

| 4.3 中的 stub | 替换为 |
|--------------|--------|
| Data RAM（ARM 侧） | AXI BRAM Controller（4.1） |
| Instr FIFO（ARM 侧） | AXI BRAM Controller（地址段 B） |
| DDR RAM 接口 | AXI DMA（4.1） |

**验证方式：**
- 在 PYNQ 上由 ARM 通过 Python 写入指令和 activation，触发 TPU 计算，结果经 DMA 读回
- 与阶段三数值分析结果比对，一致则集成通过，为阶段五上板奠定基础

---

## 阶段五：FPGA 上板验证（待开始）

目标平台：PYNQ 开发板。

（待规划）

---

*最后更新：2026-05-16*
