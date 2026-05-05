# Phase 0 任务分工表

**版本**: 0.1  
**日期**: 2026-05-05  
**阶段**: Phase 0 — NumPy Golden Reference

---

## 分工原则

- **Person A**: 计算核心（systolic array 实现）
- **Person B**: 验证基础设施（testing & integration）
- 两人在前 3 天并行独立推进，A4 完成后 B 接入集成

---

## Person A — 计算核心

| ID | 任务 | 输出文件 | 依赖 | 估时 |
|----|------|---------|------|------|
| A1 | 定义单个 PE 数学行为（MAC 操作，INT8 × INT8 → INT32） | `pe.py` | 无 | 0.5d |
| A2 | 参数化 N×N systolic array，Weight Stationary 数据流建模（权重 preload → 激活值按列流入 → partial sum 沿行累加） | `systolic_array.py` | A1 | 2d |
| A3 | 大矩阵 tiling 逻辑（输入矩阵分块 → 多次 matmul → 结果拼接） | `tiling.py` | A2 | 1d |
| A4 | 对外暴露统一 `matmul(W, X, N)` 接口，供 B 调用 | `matmul_api.py` | A3 | 0.5d |

> **关键产出**: `matmul_api.py` — B 的集成工作全部依赖此接口。

---

## Person B — 验证基础设施

| ID | 任务 | 输出文件 | 依赖 | 估时 |
|----|------|---------|------|------|
| B1 | 项目目录结构 + `requirements.txt`（numpy, torch, pytest） | `pyproject.toml` / `requirements.txt` | 无 | 0.5d |
| B2 | PyTorch/NumPy baseline：`ref_matmul(W, X)` 作为 ground truth | `reference.py` | 无 | 0.5d |
| B3 | ReLU 激活函数实现 | `activation.py` | 无 | 0.5d |
| B4 | MNIST FC 层权重提取（torchvision 下载，导出权重矩阵为 numpy array） | `load_mnist_weights.py` | 无 | 1d |
| B5 | bit-exact 比对框架（随机矩阵 fuzz test + 结果差异报告） | `test_matmul.py` | B2, A4 | 1d |
| B6 | FC 层端到端集成（`matmul` → `relu` → 输出，验证 MNIST 推理结果） | `test_fc_layer.py` | B3, B4, A4 | 1d |

---

## 共同任务

| ID | 任务 | 负责人 | 时间节点 |
|----|------|--------|---------|
| C1 | 对齐 INT8 量化截断规则（A 实现、B 验证，避免 bit-exact 陷阱） | A + B 共同讨论 | A2 开始前 |
| C2 | A4 接口 review（B 确认接口够用） | A 提 PR，B review | A4 完成后 |
| C3 | 集成测试通过，Phase 0 验收 | A + B | 最终 |

---

## 并行甘特图

```
Day:    1       2       3       4       5
A:    [ A1 ][ A2          ][ A3 ][ A4 ]
B:    [ B1 ][ B2  B3  B4       ][ B5  B6 ]
共同:                            [ C2 ][ C3 ]
C1: 第 1 天对齐后各自开始
```

B5 / B6 依赖 A4，B 在前 3 天先完成 B1–B4，A4 就绪后立刻接入。

---

## C1 必须提前对齐的细节

开始编码前，两人确认以下规则，否则 B5 bit-exact 比对永远无法通过：

- INT8 × INT8 累加使用 **INT32**，中间不截断
- 最终输出是否饱和截断至 INT8，还是保留 INT32（Phase 0 建议保留 INT32）
- 若截断：截断方式（round / floor / truncate）必须与 PyTorch baseline 完全一致

---

## Phase 0 完成标准

与 PyTorch / NumPy 标准矩阵乘法结果 **bit-exact** 一致，覆盖：

- 随机矩阵（多组尺寸，含 N 整除与不整除的情况）
- MNIST FC 层实际权重
