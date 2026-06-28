# 设计决策日志

按时间倒序记录已落地的设计决策。每条尽量短，只回答四件事：**背景 / 决策 / 影响范围 / 替代方案**。
旧 design doc 里"待统一 / 待定 / TBD"的悬空问题，落地后都收进这里成为权威。

> 仅当一个决策影响 ≥ 2 个模块、或推翻了之前的设计时才进本日志。模块内部细节走 doc 本身（architecture / systolic_array / controller_ws）。

---

## D8 · controller 升级为指令级执行器；per-column deskew 上移；slot 概念取消（2026-06-26）

**背景**：原 controller_ws 端口偏 session 粒度（`i_switch_weight / i_accum_slot / i_tile_num / i_start_addr`），
由 `tinytpu_top` 包裹后才映射到指令；要接 APB/PIO 指令路径还得再加一层胶水。
同时 accumulator 内部三条 SR（D6）和 controller 双向耦合 col-0 对齐，
未来扩 STORE / multi-K 累加要在两处同时改，维护面散。

**决策**：controller 端口压成 4 个"指令字段" —— `(i_wtile_num, i_act_staddr, i_acc_staddr, i_feed_num)` +
3 个流控（`i_start / i_weight_loaded / i_activ_available`）—— 把整段 matmul 一次性下到 controller，
内部自驱完 `wtile_num` 段 OVERLAP 串接：每个 wtile 复用同一份 activation（`act_staddr` 不变），
acc 写区间按 `wtile_idx × feed_num` 推进。**per-column deskew SR 从 accumulator 上移到 controller**，
accumulator 退化成纯 per-AC 直收（`i_wr_vld[AC] / i_wr_addr[AC] / i_acc_en[AC] / i_out_en[AC]`），
取消 slot 概念，地址平坦化为单一 `ACC_ADDR_W` 位。

**附带决策（FSM）**：FSM 从 6 态扩到 **7 态**，新增 `REWAIT` 处理多 wtile 之间 weight 慢
就绪的情况。REWAIT 出口分两路（变体 II 早退）：
- `weight_loaded ↑ & cnt<W`（旧 drain 未完）→ OVERLAP，复用部分重叠 + boundary inject
- `weight_loaded ↑ & cnt≥W`（旧 drain 已完）→ FEED，cold inject 当作"从空流水重启"

详见 [controller_ws.md](controller_ws.md) §3.3 转移表。

**影响**：
- `rtl/controller_ws.sv` 端口 + body 全量重写（7 态 FSM；wtile_idx 外层计数；
  REWAIT 双分支；内部 AC-1 长的 deskew SR）
- `rtl/accumulator.sv` 删 SR、`i_wr_slot` 移除、地址平坦化
- `rtl/tinytpu_top.sv` 顶层指令端口对齐 4 字段；accumulator 读口暂保持顶层 raw 直驱
- `simulator/cycle/sim_model/controller_ws_ref.py` 镜像新接口；
  `simulator/cycle/sim_model/accumulator.py` 同步删 SR
- 测试与 testbench 黄金向量列头改 `wtn/fn/ac/act`
- D6（per-column SR 在 accumulator 内）失效；不变式表更新见 architecture.md §6

**替代**：
- 保持 D6 + 顶层做指令翻译胶水 → 拒绝（耦合面散、扩 STORE 再要改两处）
- 上移到 controller 但保留 slot/row 二级地址 → 拒绝（与平坦寻址比无收益）

---

## D7 · `W = AR + LATENCY`，`F` 字段删除（2026-06-22）

**背景**：旧 doc 用 `F`（"feed 拍数"）做两件事——单 tile 的 abuf 容量、和 session FSM 末拍判定，
双语义混在一起。tile_num=1 时两者退化成同值，bug 没暴露。

**决策**：删除 `i_F`，硬件流水深度直接定义常数 `W = AR + LATENCY`（每 tile 的有效 feed 行数）；
session 内总 feed 行数 = `tile_num × W`，要求 `M = tile_num × W`。

**影响**：`rtl/controller_ws.sv` 端口去 `i_F`、`simulator/cycle/sim_model/controller_ws_ref.py` 同步；
`tb/ctrl_ws_tb` scenario 文件头改为 `AR AC NCYC`。`tile_num` 是 session 内常量，session 中途改会乱。

**替代**：保留 `F` 双语义 → 拒绝（语义混淆是 bug 源头）。

---

## D6 · `wr_tile` 改为标量注入 + accumulator per-column 传播

**背景**：早期两套互斥方案——OS_restore §6 "标量 + `Gk ≥ M+N-1`"（依赖一拍只有一条 drain 波前）；
SA §12 "per-column 标签随 psum 进 sa"。前者强约束、后者侵入 sa 数据面。

**决策**：controller 出**标量** `(o_wr_vld, o_wr_slot, o_wr_row)`（已 CAP_DELAY 对齐 col 0 psum），
accumulator 内部用 3 条并行 SR（长 AC-1）把标量逐列延 `c` 拍，写 `_mem[slot][row][c]`。

**影响**：sa 不持槽地址（数据面纯净）；accumulator 承担"标量 → per-column"的展开；
controller 调用顺序约定：controller.update → accumulator 接收已对齐的地址；
`Gk ≥ M+N-1` 约束作废，只剩 `Gk ≥ M`。

**替代**：sa 内部捎带 tag → 拒绝（破坏 sa "纯数据面" 不变式）。

---

## D5 · `b_sw` 几何：`[AR]` 左边缘注入 + sa 内右推

**背景**：WS_weight §5 设想 controller 算 `[AR][AC]` per-PE mask 直给 sa；
SA §11 / controller 后期方向是 controller 只发 `[AR]` 边缘 bool、sa 内逐拍右推成对角波。

**决策**：controller 出 `o_b_sw[AR]`（行 stagger SR：注入端 = cold|boundary，每拍下移 1 格）；
sa 内 `b_sw_grid[r][c]` 每拍右移 1 格、第 0 列额外打 1 拍 FF 与 a 通路对齐。
最终到达 PE(r,c) 的拍号 = `inject + r + c + 1`。

**影响**：`rtl/controller_ws.sv` 只输出 `o_b_sw[AR]`，不算 `[AR][AC]`；
`rtl/systolic_array.sv` 持有 `b_sw_grid` 移位逻辑；`rtl/common/weight_buf.sv` 接收标量 `i_sw`。

**替代**：per-PE mask → 拒绝（controller 算 `[AR][AC]` 浪费、把 sa 几何泄露到 controller）。

---

## D4 · WS shadow 载入：sa 内 valid/ready 反压 FIFO（无 controller 门控）

**背景**：旧 WS_weight §4 用 `shadow_load`（controller 精确开 AR 拍窗口），且要求倒序喂权重行。
该机制要 controller 数拍 + 上游配合倒序，脆弱。

**决策**：sa 每列的 weight_buf 串成下沉 FIFO 链——`o_in_rsv_rdy = i_out_rsv_rdy | !满`，
ready 链从底端组合往上。wfifo 出 `b_vld`，sa 出 `b_rdy`，`b_vld & b_rdy` 自动载入 shadow。
填满后该列反压拉起、自动停，保持到 `i_sw` 翻转。**controller 权重侧不出任何控制信号**。

**影响**：`controller_ws` 只观测 sa 反压（`weight_loaded = !|wfifo_rdy`，所有列反压拉起 = shadow 满）
决定 WLOAD→FEED 转移。`rtl/weight_fifo.sv` 上游只需正向喂权重（不再倒序），
`rtl/systolic_array.sv` 内自然沉底堆叠到对应行。

**替代**：保留 `shadow_load` 窗口 → 拒绝（需要精确 AR 拍 + 上游倒序，两层耦合 bug 源）。

---

## D3 · `Gk ≥ M` 唯一不变式（删 `Gk ≥ M+N-1`）

**背景**：旧 OS_restore §6 论证"标量 `wr_tile` 要求一拍至多一条 drain 斜线 → `Gk ≥ M+N-1`"；
SA §12 又论证 mux 约束只需 `Gk ≥ M`。两者并列让读者不知该信哪条。

**决策**：accumulator 改为 per-column SR 传播标量地址（见 D6）后，"一拍至多一条波前"假设作废。
唯一保留：`Gk ≥ M`（OS mux 每列 ≤1 个 PE 被点亮的物理约束）。WS 不走 mux，连 `Gk ≥ M` 也不需要。

**影响**：删 `rtl/` / sim 任何 `Gk ≥ M+N-1` 的 assert（如果有）；现 RTL 不带这条 assert。
实际负载 `Gk` 都很大（block 累加多 K-chunk 才 drain），都不挨边。

**替代**：保留 `Gk ≥ M+N-1` 作为软约束 → 拒绝（与 per-column 实现冗余）。

---

## D2 · `restore_data` 接口删除（OS 不支持 gap+驱逐续算）

**背景**：早期 sa `_route_acc` 支持把累加器值"回灌"到 PE 续算，仅服务"OS tile 算到一半被驱逐"场景。

**决策**：明确 tile 原子化（连续喂完不打断、不驱逐），删除 `restore_data` 形参，restore 分支恒给 0。

**影响**：`spatial_array.update / _route_acc`、`Accumulator.update`、`Controller.update` 同步去掉相关参数；
测试 `test_gap_restart_restore` 删除。若未来支持抢占，需恢复 "accumulator 读口→PE 累加输入" 反馈线，
旧 [legacy/OS_restore_design.md](legacy/OS_restore_design.md) §1–2 是彼时的设计参照。

**替代**：保留接口"以备未来" → 拒绝（YAGNI；未来要支持时再加，旧 doc 是设计依据）。

---

## D1 · WS-only 主线（OS / IS 落地搁置）

**背景**：早期目标是同一份 sa 跑 OS / WS / IS 三种数据流，但 RTL 落地资源只够 MNIST 推理。

**决策**：RTL 路径只走 WS（`rtl/tinytpu_top.sv` 只例化 `controller_ws`）；
OS sim 代码（`simulator/cycle/sim_model/controller.py`）保留供未来参考、不维护；
OS / IS 设计 doc 整体迁到 `doc/legacy/` 加 DEPRECATED banner。

**影响**：所有新 doc 描述 WS 一种；做出 WS-specific 决定（如 `b_sw`、shadow 反压）不再 caveat
"OS 怎么对应"。若未来补 OS 落地，从 legacy/ 起步、重走一遍 decision 评审。

**替代**：硬撑维持 OS+WS 双 RTL → 拒绝（验证成本翻倍、无对应业务驱动）。

---

## 留位

未来要落地的领域决策预留（写之前先有对应代码 / spec）：
- ISA 编码冻结（步 2，见 [roadmap.md](roadmap.md)）
- accumulator 增加 add（K 切段累加上层支持）
- 量化 scale 定点化方案（per-tensor symmetric vs asymmetric）
- DRAM / DMA / AXI 接入（步 3）
