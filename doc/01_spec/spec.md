# TinyTPU 系统规格说明

**版本**: 0.1（草稿）  
**日期**: 2026-05-05  
**状态**: 进行中

---

## 1. 项目目标

在 Xilinx FPGA 上实现一个对标 Google TPU v1 的精简 TPU，能够端到端运行真实小模型（如 MNIST FC 网络）的推理。

---

## 2. 功能需求

### 2.1 计算核心

- [ ] 参数化 N×N systolic array（默认 N=8），支持 INT8 矩阵乘法
- [ ] Weight Stationary 数据流
- [ ] 32-bit 累加器

### 2.2 存储子系统

- [ ] Weight Memory：存储模型权重（大小 TBD）
- [ ] Unified Buffer：存储激活值（大小 TBD）
- [ ] Weight FIFO：MXU 权重预取缓冲

### 2.3 指令集（最小 ISA）

| 指令 | 操作数 | 描述 |
|------|--------|------|
| `LOAD_WEIGHTS` | addr, size | 从 Weight Memory 加载权重 tile 到 MXU |
| `MATRIX_MULTIPLY` | ub_src, acc_dst | 执行 systolic array 计算 |
| `ACTIVATE` | acc_src, ub_dst, func | 激活函数（ReLU 优先），写回 UB |
| `STORE` | ub_src, mem_dst | 将结果从 UB 写出 |

### 2.4 激活函数

- [ ] ReLU（Phase 1 必须）
- [ ] Sigmoid、Tanh（后续扩展）

### 2.5 卷积支持

- **Phase 1**: 软件 im2col 展开为 MatMul，硬件无感知
- **Phase 2**: 原生 Convolve 指令（待规格细化）

---

## 3. 非功能需求

### 3.1 参数化

- MXU 大小由顶层参数 `N` 控制（默认 8，支持 16/32）
- 所有子模块尺寸随 N 自动缩放

### 3.2 验证

- L1 NumPy Golden Reference：bit-exact 输出对比
- L2 Python Cycle-Accurate Model：cycle 级数据流验证
- L3 SV + Verilator：RTL 与 Golden 对比
- L4 FPGA：端到端小模型推理

### 3.3 目标平台

- Xilinx FPGA（基准：Zynq-7020，具体型号 TBD）

---

## 4. 接口规格

> 待 L2 仿真阶段细化

- MXU 输入/输出接口（位宽、握手协议）
- Unified Buffer 读写接口
- 指令队列接口
- 顶层控制接口（reset、clock）

---

## 5. 约束与限制

- 不实现 PCIe Host 接口
- 不支持训练（仅推理）
- 不兼容 XLA 编译器
- Phase 1 不支持原生卷积指令

---

## 6. 待决问题

- Unified Buffer 容量（影响最大支持模型规模）
- Accumulator 数量（流水线深度）
- 指令编码格式（固定长度 vs 可变）
- 验证用目标模型（MNIST FC 或其他）
- Xilinx 具体板卡型号
