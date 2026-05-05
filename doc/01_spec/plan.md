# TinyTPU 项目计划

**版本**: 0.1（草稿）  
**日期**: 2026-05-05  
**状态**: 进行中

---

## 阶段概览

```
Phase 0  ──  NumPy Golden Reference        L1 验证层
Phase 1  ──  Python Cycle-Accurate Model   L2 验证层
Phase 2  ──  SystemVerilog RTL             L3 验证层
Phase 3  ──  Xilinx FPGA Prototype         L4 验证层
```

---

## Phase 0 — NumPy Golden Reference

**目标**: 建立数学正确性基准，输出作为后续所有阶段的 ground truth。

**交付物**:
- [ ] 参数化 systolic array 矩阵乘法（NumPy 实现，N 可配置）
- [ ] Weight Stationary 数据流建模
- [ ] ReLU 激活函数
- [ ] 单次 FC 层前向推理（输入 → MatMul → Activate → 输出）
- [ ] 测试集：随机矩阵 + MNIST FC 层权重验证

**完成标准**: 与 PyTorch/NumPy 标准矩阵乘法结果 bit-exact 一致。

---

## Phase 1 — Python Cycle-Accurate Model

**目标**: 验证架构决策，统计性能指标，为 RTL 提供时序参考。

**前置条件**: Phase 0 完成。

**交付物**:
- [ ] PE（Processing Element）cycle-level 建模
- [ ] Weight FIFO 建模
- [ ] Unified Buffer 建模
- [ ] 指令调度器（4 条指令）
- [ ] 性能统计：cycle count、MXU 利用率、内存带宽
- [ ] 与 Phase 0 输出做 bit-exact 对比

**待决**: Weight Stationary 具体建模方案（Phase 0 完成后确认）。

---

## Phase 2 — SystemVerilog RTL

**目标**: 可综合的 RTL 实现，仿真验证正确性。

**前置条件**: Phase 1 完成，接口规格确认。

**交付物**:
- [ ] 参数化 PE 模块
- [ ] N×N systolic array（`parameter N = 8`）
- [ ] Weight FIFO
- [ ] Unified Buffer（SRAM 模型）
- [ ] Accumulator 阵列
- [ ] Activation Unit（ReLU）
- [ ] 指令解码器与控制器
- [ ] Verilator testbench，与 Phase 0 golden 对比
- [ ] 综合报告（资源利用率）

---

## Phase 3 — Xilinx FPGA Prototype

**目标**: 在真实硬件上端到端运行小模型推理。

**前置条件**: Phase 2 完成，板卡型号确认。

**交付物**:
- [ ] Vivado 工程，时序收敛
- [ ] 权重加载机制（UART 或 AXI）
- [ ] 端到端推理：MNIST FC 网络输入 → 分类输出
- [ ] 性能测量：实际 cycle count、推理延迟

---

## 待决事项（阻塞后续规划）

| 问题 | 影响阶段 | 状态 |
|------|---------|------|
| Unified Buffer 容量 | Phase 1/2 | 待定 |
| Accumulator 数量 | Phase 1/2 | 待定 |
| 指令编码格式 | Phase 1/2 | 待定 |
| 验证目标模型 | Phase 0/3 | 候选：MNIST FC |
| Xilinx 具体板卡 | Phase 3 | 待定 |
