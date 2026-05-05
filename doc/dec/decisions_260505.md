# TinyTPU 项目决策记录

---

## 2026-05-05 — 规划与需求阶段

**参与者**: 项目负责人  
**主题**: TinyTPU 核心架构方向确认

---

### 1. 目标架构

**决策**: 以 Google TPU v1（Jouppi et al., ISCA 2017）为参考架构。

**理由**: v1 架构最简洁，公开文献充分，专注推理（无训练），适合从零实现。

**范围限制**: 暂不实现 Host PCIe 接口，不兼容 XLA 编译器。

---

### 2. 实现语言与工具链

**决策**:
- 最终实现语言：SystemVerilog (RTL)
- 行为仿真：Python（分阶段，见第 5 条）
- C 层：暂不引入，Python 对当前规模足够

**理由**: 在 RTL 之前用 Python 建立数学正确性和时序行为的验证基准，降低 RTL 调试成本。

---

### 3. MXU 规模

**决策**: 默认 8×8 systolic array，实现完全参数化（顶层参数 `N`，默认 N=8）。

**理由**: 8×8（64 PE）可在 Artix-7 / Zynq-7010 级别 FPGA 上运行，保留扩展至 16/32 的能力。

**约束**: 大矩阵需软件分块（tiling），所有子模块（Weight FIFO、Accumulator 等）尺寸均跟随 N 缩放。

---

### 4. 卷积支持策略（分步）

**决策**:
- **Phase 1**: 只实现 MatMul，卷积通过 im2col 在软件层展开为矩阵乘法
- **Phase 2（后续）**: 根据需要添加原生 Convolve 指令，硬件处理滑窗

**理由**: im2col 方案硬件简单，8×8 规模下内存膨胀可接受；原生卷积复杂度高，留作扩展。

---

### 5. 行为仿真流程（4 层漏斗）

**决策**: 采用 Google 内部常见的分层验证流程：

```
L1  NumPy Golden Reference     — 验证数学正确性，无时序，输出作为 ground truth
L2  Python Cycle-Accurate Model — 验证每拍数据流、统计 cycle/利用率
L3  SV RTL + Verilator          — 与 L1 输出做 bit-exact 比对
L4  Xilinx FPGA Prototype       — 端到端推理验证
```

**理由**: 错误越早发现成本越低；L1 快速建立信心，L2 验证架构决策，L3 验证实现，L4 验证真实硬件。

---

### 6. 数据流策略

**决策**: 采用 **Weight Stationary** 数据流（与 TPU v1 一致）。

**含义**: 权重固定在 PE 内部寄存器中，激活值和部分和沿阵列流动，最大化权重复用、降低权重读取带宽。

**状态**: 待深入学习后在 L2 仿真中建模验证。

---

### 7. 功能完整性目标

**决策**: 对应 Level 4 ISA——有指令队列，支持核心推理指令集：

| 指令 | 功能 |
|------|------|
| `LOAD_WEIGHTS` | 从 Weight Memory 加载权重 tile |
| `MATRIX_MULTIPLY` | 触发 systolic array 计算 |
| `ACTIVATE` | 应用激活函数（ReLU 优先） |
| `STORE` | 将结果写回 Unified Buffer |

**最终目标**: 在 FPGA 上端到端运行一个真实小模型（如 MNIST FC 网络）的推理。

---

### 8. 目标平台

**决策**: Xilinx FPGA（具体型号待定，基准为 Zynq-7020 级别）。

---

### 待决事项

- [ ] Weight Stationary 数据流的具体建模方案（L2 阶段确认）
- [ ] Unified Buffer 大小和 Accumulator 位宽（行为仿真后定）
- [ ] 指令编码格式（L3 阶段前确认）
- [ ] 验证用小模型的选择（MNIST FC 或其他）
- [ ] Xilinx 具体板卡型号
