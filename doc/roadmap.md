# RISC-V Core + TPU 路线图

**平台**：AU15P (Zynq UltraScale+) · 纯片上起步 · 端到端跑通 MNIST 级小 CNN
**核**：自研 RV32IM 3 级 in-order + cooperative RTOS（控制器）
**TPU**：8×8 INT8 WS 脉动阵列（v0：当前；v1 计划扩到 16×16）

**量化前提**：输入模型**已离线 INT8 量化**（per-tensor / 整层一个 scale）。量化本身不在工作范围，
工作是"忠实地用定点硬件执行它"。当前缺口：浮点 scale 还没定点化（见 §1.2 / 步 1）。

**当前位置**：WS-only 主线（OS 路径已搁置，见 [decisions.md](decisions.md) D1）。
RTL `controller_ws + sa + abuf + wfifo + accumulator` 顶层整合完成 + ctrl_ws_tb / tpu_top_tb
逐拍对拍跑通；conv / 激活 / 跨层 / 定点化未做。

图例：✅ 已完成 · 🔧 进行中 · ⏳ 未开始 · ⚠️ 风险/关键

---
---

# 第一层 · 前置（地基，锁定不迭代）

## 0.1 架构边界 ✅
- 引导：综合固化进 IM，复位即跑
- 互联：TPU 配置挂 APB，数据走 AHB，TPU 纯从设备
- 存储：一阶段纯片上（BRAM/URAM），不上 DDR
- 搬运：PIO（CPU 经 AHB），无 DMA、无仲裁器
- 等待：RTOS yield 轮询等 done，中断后置
- 硬件：零采购，DDR4/flash/UART/JTAG 全板载

## 0.2 CPU 核（RV32IM）✅
功能已完备：RV32IM、3 级流水、AHB+APB、CSR、trap 完整、全功能 load/store、引导固化、
RTOS 运行成功、DSP 乘法器（1 拍延迟）。
剩余收尾（非阻塞，可与 TPU 并行）：MULH 对拍收口 · Predictor（优先级最低）· Mini 中断汇聚。

---
---

# 第二层 · 总体计划

## 1. 三条线

| 线 | 角色 | 内容 |
|---|---|---|
| **Python 线** | cycle-accurate 黄金模型 + 性能模型 | 始终领先。既算对数值、又算准周期。是 RTL 的可执行规格 + 正确性参考 + 性能预测器。 |
| **RTL 线** | 硬件实现 | 照 Python 模型复刻，波形逐拍对拍。阵列封装 → BRAM → APB → 状态机 → 指令解析器 → DMA → 流水。 |
| **编译器线** | 软硬桥梁 | 权重打包 → 调度/地址生成 → ISA → 指令流。**当前自研轻量**；TVM/MLIR 整条跑通后再作支线评估（倾向 MLIR）。 |

**核心原则**：每步性能优化先在 Python 模型里实现并测出加速比，确认值得做，再去写 RTL。
把"优化要不要做"的决策从 RTL 阶段（贵）前移到 Python 阶段（便宜）。

## 2. 三份共享规范（防三线漂移，单一权威）

跨线接口必须有单一权威定义，三条线都引用它，不各自实现。

1. **layout 规范**：权重/激活在 BRAM/DRAM 怎么摆，阵列灌权重顺序。
   （现状散在 `compiler/` tiling + [systolic_array.md](systolic_array.md) §5；待整合到 ISA 落定时一并冻结）
2. **ISA 规范**：指令编码 + 语义。**地址字段预留 DRAM 宽度（32 位）**，步 3 加 DRAM 不用改 ISA。
   （现状 [isa.txt](isa.txt) 草案 + `compiler/instr.py`，待定稿冻结）
3. **时序约定规范**：握手拍数语义（start/done 在第几拍）、各模块固定延迟、流水级数。
   Python 和 RTL 都照这份，否则会 debug 大量"其实只是约定不同"的假 bug。
   （现状 [controller_ws.md](controller_ws.md) §7 + [architecture.md](architecture.md) §5，待持续维护）

## 3. 五步演进（每步一个明确瓶颈，不提前）

| 步 | 存储 | 总线/搬运 | 控制方式 | 解决的瓶颈 |
|---|---|---|---|---|
| 1 | BRAM | APB | CPU 逐寄存器直驱 | 基线，阵列复用 |
| 2 | BRAM | APB | 指令流，硬件解析 | 控制下放硬件 |
| 3 | +DRAM | +DMA+AXI | 指令 | 容量 + 搬运带宽 |
| 4 | DRAM | DMA | 指令 | double buffer 隐藏访存 |
| 5 | DRAM | DMA | 指令并行 | 计算流水 |

- 贯穿：第 1 步结果做黄金参考，每步上板逐层对拍。
- ISA 在第 2 步定稿并冻结，第 3 步起不再改。
- 总线背景：RISC-V 当前只有 AHB/APB（AHB 暂不支持 burst）。第 1 步统一用 APB。

## 4. 全程风险 + 待确认 ⚠️

**风险**（从步 1 起盯）：
1. **量化数值对齐** — 浮点 scale 定点化的舍入 + requant 舍入/边界。软件 golden 阶段定死规则，RTL 严格照搬。
2. **AHB/AXI 握手时序** — 集成阶段主要坑，ILA 早介入。

**待确认**：
- zero-point 是否为 0（对称量化可简化 requant）— 翻量化参数即知。
- 激活饱和边界：有符号 [-128,127] vs 无符号 [0,255]。

---
---

# 第三层 · 执行层（五步，每步 目标 / 任务 / 验收）

> 蓝图描述"做成啥"，这里排"怎么一步步做"。每步三条线并行推进，Python 线领先。

## 步 1 · BRAM + APB + CPU 直驱（基线，阵列复用）

### 步 1a · cycle 模型线

**目标**：对整体 TPU 框架有基本建模——weight_fifo / activation_buf / systolic_array / accumulator
+ controller_ws，能计算 SimpleCNN 的单层（验证范围见 [scope.md](scope.md) §8）。
**产出 = 计算阵列固化**：systolic_array + pe 定型（WS-only，见 [decisions.md](decisions.md) D1），
之后一直复用、尽量不改。

- **Python · 逐拍**（B，单 tile）：B1 OS / B4 WS-1 / B6 K 切段累加 / B7 K 不整除补零 均已通过 ✅。
- **下一步**：导出对拍向量给 RTL（tpu_top_tb 已搭好，需把 compiler 端的 layer2/3 激活全零 bug 修掉）。

### 步 1b · 编译器
**任务**
- A1 定点化单层 → A2 requant 约定 → A3 conv(im2col) → A4 ReLU+bias
- **编译器**：权重打包、tile 调度/地址生成（`emit_matmul_program` 已起步）。

### 步 1c · rtl
**任务**
- 按照 Python 模型完成 RTL ✅（[architecture.md](architecture.md) §3 五模块已落地、tinytpu_top 整合完成）
- 通过 raw 写口（abuf/wfifo）+ controller_ws 上层握手，一步一步算一层
- **RTL**：PE 微架构 ✅ → 8×8 阵列 ✅ → APB 寄存器/状态机 ⏳ → requant 通路 ⏳

**验收条件**
- 单层 GEMM（单 tile）：RTL 波形**逐拍 + 逐值**对齐 Python golden（WS）✅（tpu_top_tb 已通过）
- 上板测试能正确计算单层网络 ⏳

## 步 2 · 指令流，硬件解析（控制下放硬件）

**目标**：控制逻辑从 CPU 逐寄存器直驱，下放成"CPU 发指令流、硬件解析执行"。**ISA 在此定稿并冻结。**

**任务**：RTL 指令解析器（取指→译码→驱动状态机）· 固件改成下发指令流 · 编译器线产出指令流（已有 `emit_matmul_program`）。

**验收**：CPU 发一段指令流跑完单层 GEMM，结果对齐步 1 golden；ISA 冻结文档。

## 步 3 · +DRAM + DMA + AXI（容量 + 搬运带宽）

**目标**：片上放不下时上 DRAM，引入 DMA 主口 + AXI 解决容量与搬运带宽。

**任务**：DMA 主口 + AXI · DRAM 控制 · layout 扩到 DRAM（ISA 地址字段已预留 32 位，不改 ISA）· AHB 仲裁。

**验收**：大于片上容量的层在 DRAM 上跑通，搬运带宽达标，结果对齐 golden。

## 步 4 · double buffer（隐藏访存）

**目标**：计算与搬运重叠，用双缓冲隐藏访存延迟。

**任务**：权重/激活 ping-pong 双缓冲（Python 线先测加速比再写 RTL）。

**验收**：访存被计算隐藏，PE 利用率较步 3 提升（Python 模型预测值 vs 上板实测对齐）。

## 步 5 · 指令并行（计算流水）

**目标**：指令级并行，拉满计算流水。

**任务**：指令并行调度 · 提频 · DSP packing（1 个 DSP48E2 塞 2 个 INT8 MAC）· 中断替代轮询。

**验收**：PE 利用率 ≥ 70%（目标），整网吞吐达标。

---
