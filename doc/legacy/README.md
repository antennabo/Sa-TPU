# 旧设计稿（DEPRECATED）

这些是早期 OS / WS / 三模式统一设计稿，保留为历史参考。**不要照这些 doc 写新代码**——
字段命名 / 接口几何与现 RTL 不一致（如 `i_F`、`shadow_load`、`w_switch[AR][AC]`、`output_sel`、
`acc_clr`、`Gk≥M+N-1`）。

保留原因：

- **OS 路径**：当前 WS-only 主线（[../decisions.md](../decisions.md) D1）不再维护 OS，
  但 sim 代码（`simulator/cycle/sim_model/controller.py`）还在。
  未来若补 OS 落地，这些 doc 是设计起点。
- **WS 旧版**：原理（ping-pong、反对角线波前等）已被 [../systolic_array.md](../systolic_array.md) +
  [../controller_ws.md](../controller_ws.md) 吸收并对齐到现 RTL，但旧推导过程在新版没复述。
  想看推导细节回来读 legacy。

## 旧 → 新 映射

| 旧 doc | 内容去向 |
|---|---|
| [SA_array_design.md](SA_array_design.md) | [architecture.md](../architecture.md) (§3 / §4 / §8) + [systolic_array.md](../systolic_array.md) (WS 切片) |
| [controller_design.md](controller_design.md) | [controller_ws.md](../controller_ws.md)（WS 部分；FSM 已从 5 态改 6 态）|
| [OS_restore_design.md](OS_restore_design.md) | [decisions.md](../decisions.md) D2（`restore_data` 删除）+ D6（`wr_tile` per-column）|
| [WS_weight_design.md](WS_weight_design.md) | [systolic_array.md](../systolic_array.md) §5–§6（权重双缓冲 + 反对角线波前推导）+ [decisions.md](../decisions.md) D4 |
| [accumulator_design.md](accumulator_design.md) | [controller_ws.md](../controller_ws.md) §5.3（接口表）|
| [weight_fifo_design.md](weight_fifo_design.md) | [controller_ws.md](../controller_ws.md) §5.2（接口表）|
| [common_buf_design.md](common_buf_design.md) | [controller_ws.md](../controller_ws.md) §5.1（activation_buf 是 per-lane RAM，无 page/skew）|
| [ca_model_design.txt](ca_model_design.txt) | [roadmap.md](../roadmap.md)（步 1a cycle 模型线）|

## 矛盾点统一对照

旧 doc 里多处冲突，落地版本在 [decisions.md](../decisions.md)：

| 议题 | 旧 doc 分歧 | 现实现 |
|---|---|---|
| `Gk` 不变式 | OS_restore §6 = `Gk≥M+N-1`；SA §12 = `Gk≥M` | 只剩 `Gk≥M`（WS 不涉）|
| `wr_tile` 几何 | OS_restore = 标量；SA = per-column | 标量注入 + accumulator per-col SR 展开 |
| `b_sw` 几何 | WS_weight = `[AR][AC]` per-PE mask；SA = `[AR]` 边缘 + 右推 | `[AR]` 边缘 + sa 内右推 |
| shadow 载入 | WS_weight = `shadow_load` 窗口 + 倒序喂；SA = 反压 FIFO | 反压 FIFO（controller 不门控）|
| `F` 字段 | 旧 doc 全文用 | 已删，改 `W = AR + LATENCY` + `tile_num` |
