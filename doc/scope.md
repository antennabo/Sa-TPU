# Sa-TPU 项目范围（Scope）

> 本文件回答一件事：作为 **MVP 交付**，Sa-TPU 实现什么、不实现什么。
>
> **范围**：本文描述的 §3 必交付对应 [roadmap.md](roadmap.md) **步 1**（BRAM + APB + CPU 直驱基线）。
> 步 2-5（硬件 ISA 解析、DMA/DRAM、双缓冲、指令并行）属于 MVP 之后的演进路径，列入 §5 Stretch 简述，
> 详细任务表见 roadmap。
>
> 它是项目 MVP 功能边界的**单一权威**。其他 doc 中任何关于"做什么 / 不做什么"的散落表述，以本文件为准。
> 模块级设计决策见 [decisions.md](decisions.md)。

---

## 1. 项目定位

Sa-TPU 是一个对标 Google TPU v1 的自研推理加速器：8×8 INT8 脉动阵列、Weight-Stationary 数据流，
集成一颗自研 RV32IM 软核作为指令发射 host，最终目标在 **Xilinx AU15P (Zynq UltraScale+)** 上跑通
INT8 量化后的 **SimpleCNN / MNIST 推理**，达到 ≥ 98% test accuracy。

| 项目身份 | 说明 |
|---|---|
| 类型 | 端到端学习项目（架构 + 编译 + RTL + 上板） |
| 不是 | 商用加速器、不是 TPU v1 的完整复刻、不是通用 NN 加速器 |
| 目标平台 | AU15P（基准板，单板） |
| 仓库边界 | 本仓库只含 TPU（计算 + 控制 + 编译）；RV32IM 软核 + RTOS 在独立仓库；本仓库不含 CPU RTL |
| **MVP 边界** | 对应 roadmap **步 1**：CPU 通过 APB 逐寄存器直驱 TPU，纯片上 BRAM，无 DMA / 无硬件 ISA 解析器 |

---

## 2. 输入假设

任何送入 Sa-TPU 的模型必须满足以下假设——超出这些假设的输入**不在项目范围内**：

- 模型已离线完成 **INT8 量化**（量化工具不由本项目提供）
- 量化方案：**per-tensor symmetric**（zero-point = 0，对称量化简化 requant 通路）
- 数值类型：激活 INT8 / 权重 INT8 / 累加 INT32
- 网络结构静态：**输入 shape 编译时已知**，无动态维度
- 模型规模：**片上可放下**（256 KB Weight FIFO + 256 KB Unified Buffer + 4 KB Accumulator，详见
  [isa.txt](isa.txt) §1）

---

## 3. 必实现功能（In-Scope）

按"计算 / 算子 / 控制 / IO / 编译"五栏列出 MVP 必交付清单。

### 3.1 计算与数据通路

| 项 | 规格 |
|---|---|
| 脉动阵列 | 8×8 INT8 MAC，Weight Stationary |
| 累加精度 | INT32 / 每列饱和处理 |
| Weight FIFO | 256 KB（4096 tile × 64 B），列并行广播 |
| Unified Buffer | 256 KB 激活存储，行并行 |
| Accumulator | 4 KB（16 tile），per-column 写地址展开 |
| 多 tile / 多 weight session | 支持单次 weight load 跑多 tile、连续 session 切换 |

### 3.2 算子（编译器 + 硬件联合支持）

| 算子 | 实现方式 |
|---|---|
| MatMul | 硬件原生 `MATMUL` 指令 |
| Conv2d | 编译器软件 **im2col** 展开 → 复用 `MATMUL` |
| ReLU | `ACTIVATE` 指令 func = 0x1 |
| Linear / 恒等 | `ACTIVATE` 指令 func = 0x0（透传，不修改数值） |

### 3.3 控制流

| 项 | 规格 |
|---|---|
| WS Controller FSM | 6 态（IDLE / WLOAD / FEED / CAPTURE / OVERLAP / DRAIN），详见 [controller_ws.md](controller_ws.md) |
| 权重切换 | session 间 `switch_weight` 无气泡过渡（OVERLAP 状态）(最大支持16个tile的切换，当前版本只支持所有weight ready之后的启动和切换，不支持wait状态) |
| Tile 原子性 | 单 tile 启动后必须连续跑完，不支持抢占 / 中途驱逐 |
| Host 驱动 | RV32 CPU 通过 APB 配置 + raw 写口推数（PIO，无 DMA） |

### 3.4 IO 与集成

| 项 | 规格 |
|---|---|
| 存储 | 纯片上 BRAM / URAM（无 DRAM） |
| 总线 | 配置走 APB，数据走 AHB；TPU 是纯从设备 |
| Host CPU | 自研 RV32IM 3 级流水 + cooperative RTOS（独立仓库） |
| 上板 | Xilinx AU15P 单板，固化进 IM、复位即跑 |

### 3.5 编译器

| 项 | 规格 |
|---|---|
| 前端 | PyTorch 模型 → 内部 IR（Conv2d / MatMul / ReLU / 恒等） |
| 量化输入 | 读取**已离线量化**的 INT8 权重 + per-tensor scale |
| 调度 | 权重打包、tile 切分、地址生成 |
| 后端 | 生成 ISA 指令流（`LOAD_WGT` / `MATMUL` / `ACTIVATE`），详见 [isa.txt](isa.txt) |

---

## 4. 明确不实现（Out-of-Scope）

凡是默认可能被读者理解为"TPU 自然能做"、但本项目**明确选择不做**的能力。

| 项 | 不做的理由 | 替代 / 上层负责 |
|---|---|---|
| **训练 (training)** | 项目定位是推理加速器；不支持反向传播 / 梯度 / 权重更新 | 训练在 PyTorch 离线完成，导出量化权重 |
| **量化工具本身** | 量化方法学非本项目研究内容 | 上游 PyTorch 量化（QAT 或 PTQ）离线完成 |
| **BN / MaxPool / Depthwise conv / Group conv** | 这些算子需要专用硬件单元或与 systolic 数据流不匹配；不在算子白名单 | 模型设计阶段避免使用，或 ARM CPU 软件执行 |
| **Transformer / 大模型 / LLM** | 8×8 阵列规模 + 片上存储不足以承载；权重无法放下 | 不在目标场景；这是教学规模 |
| **动态 shape / 动态 batch** | 编译期静态调度；运行时不重排 | 推理图需编译时确定 |
| **PCIe Host 接口** | 与 v1 不同：本项目用 FPGA 上的 RV32 软核做 host，不是被 x86 通过 PCIe 调用 | RV32 + APB/AHB 替代 |
| **OS / IS 数据流** | RTL 路径只走 WS，详见 [decisions.md](decisions.md) D1 | Python sim 保留 OS 行为模型供参考 |
| **Tile 抢占 / 驱逐续算** | tile 原子化（见 [decisions.md](decisions.md) D2） | 上层调度避免长 tile |
| **K 分块累加上层 add** | 单 chunk K ≤ AR 限制内不需要；超出需要 accumulator add 端口未实现 | 编译期保证 K ≤ 阵列行数 |
| **PyTorch 之外的前端** | TVM / MLIR / ONNX 切换非交付承诺 | 见 §5 Stretch |

---

## 5. 可选拓展（Stretch）

做了能让项目更完整，但**不影响 MNIST MVP 交付**。MVP 验收不要求这些。
其中前两类（roadmap 步 2-5）是项目原始路线图的延伸，不是 MVP 承诺。

- **roadmap 步 2 路径**：硬件 ISA 解析器，CPU 从"逐寄存器直驱"升级为"发指令流"；ISA 在此冻结
- **roadmap 步 3-5 路径**：
  - DMA + AXI + 片外 DRAM（步 3，解锁超片上容量模型）
  - 权重 / 激活 double buffer（步 4，隐藏访存）
  - 指令级并行 + DSP packing（步 5，拉满 PE 利用率）
- **算子扩展**：
  - 额外激活：ReLU6 / Sigmoid / Tanh（已在 [isa.txt](isa.txt) §3 ACTIVATE 预留 func 编号）
  - Accumulator add 端口（解锁 K 分块累加，支持更深网络）
- **编译器路径**：TVM 或 MLIR 前端替代（用于评估自研 vs 主流方案）
- **第二块板支持**：Zynq-7020（资源更紧，作为可移植性验证）

---

## 6. 对齐 TPU v1

| 维度 | TPU v1 | Sa-TPU | 对齐 |
|---|---|---|---|
| 计算数据流 | Weight Stationary 脉动阵列 | 同 | ✅ 完全对齐 |
| 数值类型 | INT8 in / INT32 accumulate | 同 | ✅ |
| 指令风格 | CISC（少量长指令） | 同（`MATMUL` 覆盖单条 GEMM） | ✅ |
| 卷积实现 | 软件 im2col + 矩阵乘 | 同 | ✅ |
| Unified Buffer 设计 | 大块片上 SRAM 共享激活 | 256 KB UB | ✅ 概念对齐，容量缩水 |
| Accumulator 分离 | 独立 SRAM 存累加结果 | 同（4 KB） | ✅ |
| 硬件激活单元 | 在累加后做非线性 + requant | 同（`ACTIVATE` 指令） | ✅ 路径对齐，算子集精简 |

---

## 7. 偏离 TPU v1

| 维度 | TPU v1 | Sa-TPU | 偏离原因 |
|---|---|---|---|
| 阵列规模 | 256 × 256 | **8 × 8** | 教学项目 / FPGA 资源限制 |
| Host 接口 | PCIe Gen3 加速卡，被 x86 调用 | **片上 RV32IM 软核** + APB/AHB | 单 FPGA 端到端，不依赖外部 host |
| 主存 | 片外 DDR3，靠 DMA 拉取权重 | **纯片上 BRAM / URAM**（MVP） | 片上模型规模够用；DMA/DRAM 列为 Stretch |
| Weight 加载粒度 | 一次性大批量 DMA | PIO 写口逐 tile | 无 DMA，配套 PIO |
| 算子覆盖 | 含 normalize / pool 专用单元 | 不实现，详见 §4 | 教学规模 + 软件可替代 |
| Scale 处理 | 硬件 requant 单元 | M0/shift 定点化 + half-up 舍入 (见 [decisions.md](decisions.md) D10) | 软件 golden 契约已定, RTL 待实施 |
| 量化方案 | per-channel 支持 | 仅 **per-tensor symmetric** | 简化通路 |

---

## 8. 验证范围与精度目标

| 项 | 选定 |
|---|---|
| 数据集 | MNIST（手写数字，10 类） |
| 模型 | SimpleCNN（典型 1–2 卷积 + 1–2 FC 结构） |
| 量化 | INT8 per-tensor symmetric（离线完成） |
| 精度阈值 | **≥ 98% test accuracy**（INT8 量化后实测） |
| 验收平台 | AU15P 上板实测，与 Python cycle-accurate golden 逐拍 + 逐值对齐 |

不在验证范围：其他数据集（CIFAR / ImageNet）、其他模型族（ResNet / Transformer）、浮点对比基线。

---

## 9. 引用

- [roadmap.md](roadmap.md) — 五步演进时间表（本文件回答"做什么"，roadmap 回答"什么时候做"）
- [decisions.md](decisions.md) — 模块级设计决策日志（D1 WS-only / D2 tile 原子化 / D3-D7 模块决策）
- [architecture.md](architecture.md) — 系统结构 + 全局符号表（AR / AC / W 等）
- [isa.txt](isa.txt) — 指令集草案 + 硬件规格
- [controller_ws.md](controller_ws.md) / [systolic_array.md](systolic_array.md) — 模块级 deep doc
