# Sa-TPU 架构总览

写给第一次接触本项目的读者。读完本文应能建立完整心智模型，再按需跳到 deep doc。

---

## 1. 项目定位

Sa-TPU 是一个对标 Google TPU v1 的自制小型 TPU，目标是在 Xilinx AU15P (Zynq UltraScale+) 上跑通
SimpleCNN / MNIST INT8 推理。当前阶段：**8×8 INT8 systolic array、Weight-Stationary 数据流**，
配合 PyTorch 模型 → 编译器 tiling → Python 周期级仿真 → SystemVerilog RTL 的全栈流水线。

> 功能边界（实现什么、不实现什么、对齐 vs 偏离 TPU v1）见 [scope.md](scope.md)。

设计原则：每一步性能优化先在 Python cycle-accurate 模型里验证，再去写 RTL；硬件不做投机优化。

## 2. 四阶段流水线

```
1. 模型     PyTorch SimpleCNN + 权重 .pth                       model/
   ↓
2. 编译器   解析 IR → 量化 → tiling/mapping → 指令流              compiler/
   ↓
3. 仿真     ┌─ functional：数值正确性（不计周期）                  simulator/functional/
            └─ cycle：逐拍精确（黄金参考 + 性能预测）              simulator/cycle/
   ↓
4. RTL      SystemVerilog 硬件实现 + 对拍 testbench                rtl/ + tb/
```

详细路线见 [roadmap.md](roadmap.md)；ISA 草案见 [isa.txt](isa.txt)。

## 3. 硬件模块图（WS 主线）

```
┌──────────────────────┐        o_acc_{wen,waddr,accen,outen}[AC]
│  controller_ws       │ ───────────────────────────────┐
│  指令级 FSM + 三通路地址生成 │                          │
└─────────┬────────────┘                                ▼
          │                                    ┌──────────────────────┐
          │                                    │  weight_fifo         │
          │                                    │  AC lanes            │
          │                                    └──────────┬───────────┘
          │                                               │ b
          │                                               ▼
          │          a / a_vld                   ┌──────────────────────┐
┌─────────▼────────────┐ ───────────────────────▶│  systolic_array      │
│  activation_buf      │                         │  AR × AC PEs         │
│  per-lane RAM, AR    │                         │  + 每列 weight_buf    │
│  raw 写口             │                         │    shadow + active   │
└──────────────────────┘                         └──────────┬───────────┘
          ▲                                                 │ out / out_vld
          │ o_act_ren / o_act_raddr                         ▼
          │                                    ┌──────────────────────┐
          └─────────────────────────────────── │  accumulator         │
                                               │  per-col 直收 (无 SR) │
                                               │  → per-col sdpram    │
                                               └──────────────────────┘

```

i_weight_loaded = !|wfifo_rdy
所有列 SA shadow 满后反馈给 controller_ws
5 个模块 + 顶层 `tinytpu_top` 整合。raw 写口（abuf 写、wfifo 写、accumulator 读）暴露在 top
对外，便于宿主 / 测试驱动按需驱。详见 [rtl/tinytpu_top.sv](../rtl/tinytpu_top.sv)。

## 4. 数据面 / 地址面分离

这是贯穿整个设计的核心原则：


| 面         | 谁产生                                        | 谁消费                       | 内容                                                   |
| ---------- | --------------------------------------------- | ---------------------------- | ------------------------------------------------------ |
| **数据面** | activation_buf / weight_fifo / systolic_array | systolic_array / accumulator | `a / b / out`（矩阵元素 / 部分和）                     |
| **地址面** | controller_ws                                 | activation_buf / accumulator | `act_raddr / acc_waddr[AC]`（在哪个 buf 槽 / 哪一行） |

systolic_array 是**纯数据面**——内部不持有任何 slot/tile 地址，只算乘加 + 路由数据。
accumulator 写地址 per-AC `o_acc_waddr[c]` 由 controller 内部 deskew SR 展开后送进 accumulator。

## 5. 维度约定（**全局符号表，所有 doc 共用**）


| 符号            | 含义                                                 | 当前取值          |
| --------------- | ---------------------------------------------------- | ----------------- |
| `AR`            | 阵列**行**数（systolic_array.ROW_N）                 | 8（顶层`N` 参数） |
| `AC`            | 阵列**列**数（systolic_array.COL_N）                 | 8                 |
| `LATENCY` (`L`) | PE 流水延迟（cycles，1 或 2）                        | 2                 |
| **`W`**         | **warmup / drain 拍数 = `AR + LATENCY`**            | 10                |
| `wtile_num`     | 单条指令跑几个 weight tile（指令内常量）             | 运行时            |
| `feed_num`      | 每 weight tile 的 feed 行数（指令内常量, `≥W`）     | 运行时            |
| **`M`**         | 单条指令输出行数；`M = wtile_num × feed_num`        | 运行时            |
| `N`             | 完整矩阵乘的输出列数；约束`N ≤ AC` 或多 N-tile 切分 | 运行时            |
| `K`             | 收缩维；约束`K ≤ AR` 或多 K-chunk 切分              | 运行时            |
| `Gk`            | （仅 OS 历史）相邻两 drain 间隔；WS 不涉             | —                |

> 旧 `F` / `tile_num` 字段已删（[decisions.md](decisions.md) D7 / D8）。
> `W` 由硬件流水深度决定、是编译期常数；`wtile_num` / `feed_num` 是运行时变量但指令内必须稳定。

## 6. 关键不变式


| 不变式                                                                    | 出处                                      |
| ------------------------------------------------------------------------- | ----------------------------------------- |
| `feed_num ≥ W` 且 `wtile_num ≥ 1`，二者指令内稳定                         | controller FSM 阈值依赖                   |
| 所有 wtile 共用同一份 activation（`act_staddr` 不递推）；acc 按 wtile 递推 | controller 地址生成                       |
| WS shadow 载入仅靠 wb↔sa 反压自握手，**controller 权重侧无信号**         | [decisions.md](decisions.md) D4           |
| accumulator 写控制 per-AC 直收（controller 内部已 deskew）                | [decisions.md](decisions.md) D8（失效 D6）|

## 7. 一次 matmul 的控制流（7 态 FSM 概览）

详见 [controller_ws.md](controller_ws.md) §3；这里给最简版："上升沿启动 → 灌权重 → 喂激活 → 排空"，
中间 wtile 切换可能因 weight 慢就绪走 REWAIT 中转：

```
        i_start ↑
IDLE  ────────────────────────▶ WLOAD     (wb 广播写灌满 sa 内 shadow 反压链)
                                  │
              i_weight_loaded     │
              & i_activ_available │
                                  ▼
                                FEED      (W 拍 warmup; 首 wtile 入口)
                                  │
        cnt==W-1, feed_num>W      │  cnt==W-1, feed_num==W
                                  ▼              ▼
                              CAPTURE      ── ▶ OVERLAP / REWAIT / DRAIN
                                  │
        cnt==cap_last             │
                                  ▼
       last? DRAIN : (weight_loaded? OVERLAP : REWAIT)
                                  │
                                  ▼
        OVERLAP (W 拍, 旧 drain + 新 warmup 重叠)
        ovl_last → idx++ → CAPTURE / OVERLAP / REWAIT / DRAIN
                                  │
        REWAIT (等下一份 weight, drain 顺势走)
        weight_loaded↑ & cnt<W  → OVERLAP (boundary inject)
        weight_loaded↑ & cnt≥W  → FEED    (cold inject, 新 wtile 入口)
                                  ▼
                              DRAIN → IDLE
```

四道控制波：

1. `o_weight_sw[AR]` — 反对角线翻 active↔shadow，每 wtile 切换一次
2. `o_act_ren[AR]` / `o_act_raddr[AR]` — abuf 逐 lane 读使能 + 地址（行 stagger SR）
3. `o_acc_{wen,waddr,accen,outen}[AC]` — accumulator 写控制（controller 内部 per-column deskew SR）
4. `o_acc_{ren,raddr}[AC]` — 占位，本指令恒 0，待 STORE 类指令再驱动

## 8. sim ↔ RTL 一一映射

`simulator/cycle/sim_model/controller_ws_ref.py` 是 RTL 的可执行规格。两段式：


| Python                          | Verilog                              | 角色       |
| ------------------------------- | ------------------------------------ | ---------- |
| `__init__` 参数                 | `parameter` / `localparam`           | 编译期常数 |
| `update(...)` 形参              | module IO 端口                       | 每拍输入   |
| `*_next`（`update` 算出）       | 组合逻辑（`always_comb`）            | 次态       |
| `commit()` 落定的 `state`/`reg` | 时序逻辑（`always_ff @posedge clk`） | 触发器     |

testbench `tb/tpu_top_tb/` 让 Python 生成逐拍向量 + golden，VCS 跑 RTL 比对。

## 9. 模块速查


| 模块           | RTL 路径                                                | sim 路径                                                                  | 一句话职责                          | deep doc                                      |
| -------------- | ------------------------------------------------------- | ------------------------------------------------------------------------- | ----------------------------------- | --------------------------------------------- |
| controller_ws  | [rtl/controller_ws.sv](../rtl/controller_ws.sv)         | [controller_ws_ref.py](../simulator/cycle/sim_model/controller_ws_ref.py) | WS 6 态 FSM + abuf 地址生成         | [controller_ws.md](controller_ws.md)          |
| systolic_array | [rtl/systolic_array.sv](../rtl/systolic_array.sv)       | [spatial_array.py](../simulator/cycle/sim_model/spatial_array.py)         | AR×AC PE 网格 + b_sw 传播          | [systolic_array.md](systolic_array.md)        |
| pe             | [rtl/pe.sv](../rtl/pe.sv)                               | [pe.py](../simulator/cycle/sim_model/pe.py)                               | INT8×INT8→INT32 MAC，饱和、流水   | [systolic_array.md](systolic_array.md) §PE   |
| weight_buf     | [rtl/common/weight_buf.sv](../rtl/common/weight_buf.sv) | （sa 内置）                                                               | active + shadow 双缓冲，i_sw 一拍换 | [systolic_array.md](systolic_array.md) §权重 |
| activation_buf | [rtl/activation_buf.sv](../rtl/activation_buf.sv)       | [commonbuf.py](../simulator/cycle/sim_model/commonbuf.py)                 | 纯被动 per-lane RAM                 | [controller_ws.md](controller_ws.md) §abuf   |
| weight_fifo    | [rtl/weight_fifo.sv](../rtl/weight_fifo.sv)             | [commonfifo.py](../simulator/cycle/sim_model/commonfifo.py)               | AC lane sync_fifo + 广播写          | [controller_ws.md](controller_ws.md) §wfifo  |
| accumulator    | [rtl/accumulator.sv](../rtl/accumulator.sv)             | [accumulator.py](../simulator/cycle/sim_model/accumulator.py)             | per-col 直收 + per-col sdpram       | [controller_ws.md](controller_ws.md) §accum  |
| tinytpu_top    | [rtl/tinytpu_top.sv](../rtl/tinytpu_top.sv)             | —                                                                        | 5 模块整合 + raw 端口暴露           | 本文 §3                                      |

## 10. 延伸阅读

- **为什么是当前架构** → [decisions.md](decisions.md)
- **下一步要做什么** → [roadmap.md](roadmap.md)
- **指令集草案** → [isa.txt](isa.txt)
- **WS 数据流细节** → [systolic_array.md](systolic_array.md) + [controller_ws.md](controller_ws.md)
- **OS / 三模式统一 早期设计稿** → [legacy/](legacy/)（DEPRECATED，仅供未来补 OS 时参考）
