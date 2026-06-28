# Sa-TPU 设计文档

Sa-TPU 是一个对标 Google TPU v1 的 8×8 INT8 WS 脉动阵列加速器，目标在 Xilinx AU15P 上跑
SimpleCNN / MNIST INT8 推理（详见 [scope.md](scope.md)）。

## 从哪里开始读

| 你是…… | 读这个 |
|---|---|
| 想知道"项目做什么、不做什么" | [scope.md](scope.md) — 功能边界单一权威（必交付 / 不做 / 对齐 vs 偏离 TPU v1） |
| 第一次接触本项目 | [architecture.md](architecture.md) — 系统总览 + 模块图 + 维度约定 |
| 想看 WS 数据流细节 | [systolic_array.md](systolic_array.md) — sa + pe + 权重双缓冲 |
| 想看控制器 / FSM / 接口 | [controller_ws.md](controller_ws.md) — 6 态 FSM + 三道控制波 |
| 想知道"为什么是当前方案" | [decisions.md](decisions.md) — 设计决策日志 |
| 想知道"下一步做什么" | [roadmap.md](roadmap.md) — RV core + TPU 联动路线 |
| 找指令集草案 | [isa.txt](isa.txt) — ISA v0 草案（待冻结）|
| 找早期 OS / 三模式统一设计稿 | [legacy/](legacy/) — DEPRECATED，仅供未来补 OS 时参考 |

## 维护原则

- doc/ 根下文件保持精简（当前 6 个），每篇 ≤300 行，互相引用尽量少
- 新落地的跨模块决策进 [decisions.md](decisions.md)（带 **Why / 影响范围 / 替代方案**）
- 模块内部细节走对应 deep doc；不在 architecture.md 重复
- legacy/ 只增不减，永远不要 "更新" legacy 内容（要么留旧版做参考，要么主线 doc 已吸收）
