> ⚠ **DEPRECATED** — WS 权重双缓冲 + 切换波前推导稿。原理被
> [../systolic_array.md](../systolic_array.md) 吸收并对齐 RTL。本文 §4 `shadow_load` 窗口、
> §5 `w_switch[AR][AC]` per-PE mask、§6 5 态 FSM、全文 `F` 字段都与现 RTL 不一致——
> 现版本：shadow 是 sa 内反压 FIFO（valid/ready 自握手）、`b_sw[AR]` 边缘 + sa 右推、
> 6 态 FSM、`W = AR + LATENCY` + `tile_num`。保留为历史参考；新工作请读 doc/ 根下的新版。

---

# WS 数据流的权重设计（ping-pong 双缓冲 + 反对角线切换波前）

本文档记录 Sa-TPU golden model 里 **WS（weight-stationary，权重驻留）** 数据流的权重处理设计，
是 WS controller / pe / sa 实现的依据。读者应已了解 OS（output-stationary）的实现
（见 `sim/eval/analyzer/sim_model/`）。

---

## 0. 背景：OS vs WS 的维度映射

收缩维 k 的求和 `Σ_k` 要么在时间做、要么在空间做，谁在空间做谁就占阵列一维。

| | 阵列两维 | 时间维 | psum |
|---|---|---|---|
| **OS** | 输出 M × N | K（收缩，深度）| 在固定 PE 原地累加 |
| **WS** | **K × N**（权重 `B[k][n]` 驻留）| **M（输出行逐拍流入）** | 沿列**下流**、流经每行加一项 |

完整矩阵乘 `C = A·B`，A 是 M×K、B 是 K×N、C 是 M×N。

### 0.1 用 2×2 看清楚（M=N=K=2）

```
A = [a00 a01]    B = [b00 b01]    C = [C00 C01]
    [a10 a11]        [b10 b11]        [C10 C11]

C00 = a00·b00 + a01·b10     C01 = a00·b01 + a01·b11
C10 = a10·b00 + a11·b10     C11 = a10·b01 + a11·b11
```

两种数据流都是 systolic——**输入都按反对角线错开喂（skew）**，下面把错位画出来。

**OS：阵列 = M×N = 2×2，PE(m,n) 原地累加 C[m][n]，K=2 在时间**
```
          列 n=0          列 n=1
 行 m=0   PE(0,0)→C00     PE(0,1)→C01
 行 m=1   PE(1,0)→C10     PE(1,1)→C11

 激活 a 横流(行内)，权重 b 纵流(列内)，都按 k=0,1 两拍喂、且行/列间错 1 拍：
   PE(m,n) 在 k=0,1 两拍累加 a_mk·b_kn → C[m][n]     （Σ_k 在【时间】）
```

**WS：阵列 = K×N = 2×2，权重 B[k][n] 驻留，激活【斜着喂】，M 在时间**
```
          列 n=0      列 n=1
 行 k=0   b00         b01        ← 驻留不动
 行 k=1   b10         b11

 激活斜着进：行 k 比行 0 晚 k 拍（配合 psum 下流），不是同拍：
   cy0:  行0 ← a00
   cy1:  行0 ← a10 ,  行1 ← a01
   cy2:               行1 ← a11
 psum 沿列下流跟着斜的激活累加，从底部(行1)按反对角线斜着流出：
   cy1:  C00 = a00·b00 + a01·b10
   cy2:  C01 = a00·b01 + a01·b11 ;  C10 = a10·b00 + a11·b10
   cy3:  C11 = a10·b01 + a11·b11
 ⚠ 不是"列0、列1 同拍算"——激活进 / psum 出 都沿反对角线错开(cy = m+n+1，此处略去 PE latency L)。
```

同一个 2×2 物理阵列：OS 当它"输出 2×2、K 在时间"；WS 当它"权重 2×2(=K×N)、M 在时间"。
**两行就是 k=0,1**——"加这 2 项"靠 psum 往下流过 2 行实现；输出的多行 m 改走时间，斜着流入。

### 0.2 实例：2×2 阵列算 4×4（这才看得出权重怎么切）

单 tile 看不出权重切换。放大到 `A(4×4)·B(4×4)=C(4×4)`、物理阵列还是 2×2：

```
阵列 2×2 → AR=2(每段 K 长), AC=2(每块 N 宽), F=4(M=4 行走时间)
K=4 → 切 2 段:  kc=0 取 k0,k1 ; kc=1 取 k2,k3
N=4 → 切 2 块:  nt=0 取 n0,n1 ; nt=1 取 n2,n3
M=4 → 不切，4 行激活全程斜着流入
```

权重 B 切成 **4 个 2×2 tile**（`W[kc][nt] = B[kc 行段, nt 列块]`）：
```
 W00=B[0:2,0:2]   W01=B[0:2,2:4]
 W10=B[2:4,0:2]   W11=B[2:4,2:4]
```

输出按 nt 分块、kc 累加：
```
 C[:,0:2] = A[:,0:2]·W00 + A[:,2:4]·W10      → slot0
 C[:,2:4] = A[:,0:2]·W01 + A[:,2:4]·W11      → slot1
```

**驻留权重的调度**（ping-pong：算当前 tile 时后台把下一个 tile 权重载进 shadow；switch 波前在边界翻）：
```
 [载 W00]  冷启动暴露，AR=2 拍灌进 active
 STREAM A[:,0:2] × W00   ‖ shadow 载 W10   → 部分和入 slot0
 «switch→W10»  换 K 段、同 slot              （switch_weight=True，输出要累加）
 STREAM A[:,2:4] × W10   ‖ shadow 载 W01   → 累加进 slot0   ⟹ slot0 = C[:,0:2] ✓
 «switch→W01»  换 N 块、新 slot              （switch_weight=True，写新槽）
 STREAM A[:,0:2] × W01   ‖ shadow 载 W11   → 入 slot1
 «switch→W11»  换 K 段、同 slot
 STREAM A[:,2:4] × W11                       → 累加进 slot1   ⟹ slot1 = C[:,2:4] ✓
```

看点（本文档的全部权重机制都在这）：
- 每个 STREAM 喂 F=4 行激活（斜着进），对**驻留**的 2×2 权重算，结果从底行斜着流出。
- 4 个权重 tile 顺次驻留，靠 **ping-pong** 让换权重不停顿（下一个 tile 的 load 藏在当前 STREAM 里）。
- **switch 波前**在每个 `«switch»` 处沿反对角线翻 shadow→active。
- `W00→W10`、`W01→W11` 是换 **K 段** → 输出**累加**到同 slot；`W10→W01` 是换 **N 块** → 写**新 slot**。
  （累加 vs 新槽属输出存储层，controller 只管"切不切"。）

**controller 字段约定**（OS/WS 统一）：
- `self.M / self.N` 恒为**物理阵列**行/列；
- `self._K` 为**时间维 feed 拍数**。
- WS 下：`self.M = K_tile`（阵列行）、`self.N = N_tile`（阵列列）、`self._K = M`（流入行数）。
  驱动方构造 `Controller(M=K_tile, N=N_tile, K=M, mode="WS")`。

**符号表**（全文通用，务必区分大写维度 vs 小写索引）：

| 符号 | 含义 |
|---|---|
| `M, N, K` | 完整矩阵乘尺寸（大写）：A 是 M×K、B 是 K×N、C 是 M×N |
| `AR` | 阵列**行**数 = 一个 tile 的 K 段长（WS 下 = `self.M`）|
| `AC` | 阵列**列**数 = 一个 tile 的 N 块宽（= `self.N`）|
| `F` | feed 拍数 = `M`（斜着流入的输出行数；= `self._K`）|
| `L` | PE 流水延迟（1 或 2）|
| `k, n` | 阵列内**行/列索引**：`k=0..AR-1`（⚠小写 ≠ 完整 K）、`n=0..AC-1` |
| `m` | 输出**行索引** = `0..F-1`（走时间）|
| `T` | 某个 tile 的 feed **起点拍号**（背靠背时下一个 tile `T=F`）|

---

## 1. 为什么权重要双缓冲（ping-pong）

WS 下权重 `B[k][n]` 驻留在 PE(k,n)。换 tile（换 K-chunk 或换 N-tile）要换一整套权重，
逐拍移入需要 AR 拍。若只有一组权重寄存器，这 AR 拍是**纯停顿**（阵列空转）。

**ping-pong**：每个 PE 有两组权重寄存器
- `b`（active）——当前计算用；
- `b_shadow`（影子）——后台预载**下一个** tile 的权重。

算当前 tile 时，把下个 tile 权重逐拍灌进 `b_shadow`；到边界一**翻转**即用上，把 AR 拍 load 时间
**藏进当前 tile 的计算里**。

> 注意：ping-pong 隐藏的是**载权重**，不隐藏算/排空本身。

---

## 2. 切换波前 = 反对角线（关键）

### 2.1 为什么不能整阵列一拍翻、也不是按行翻
psum 沿列**下流**、会累加经过的每一行。若整阵列一拍翻权重，当前 tile 还在阵列里下流的 psum
会撞到新权重 → 混 tile。

按 PE 看：PE(k,n) 持 `B0[k][n]`，被激活 `a[m][k]` 用到——激活向右流，在 cycle `m+k+n` 到达 PE(k,n)
（行 skew + 列 skew）。所以每个 PE 的"用完旧权重"时刻同时取决于 **k 和 n**：

- 旧 tile 最后一次用 PE(k,n)：`(F-1)+k+n`
- 新 tile 第一次到达 PE(k,n)：`T+k+n`（T = 新 tile feed 起点）

PE(k,n) 必须在这两者之间翻 → 翻转时刻 **= T+k+n**，沿**反对角线 `k+n`** 一拍推一条。
（这与 OS 的 drain 波前同款几何。）

### 2.2 可以零气泡、完全背靠背
约束 `T+k+n > (F-1)+k+n` ⟹ `T ≥ F`。取 **T = F**：新 tile 在旧 tile 喂完的**下一拍**就开喂，
每个 PE 在"旧的刚走、新的刚到"那拍翻。**无气泡，每 tile 周期 = F**。
switch 波前正好贴着新 tile 的激活波前走（都在 `k+n` 反对角线上）。

> 对比：若整阵列一拍翻（错误做法），tile 间会有 ~AR+AC+L 的 drain 气泡。反对角线波前消掉它。

**为什么整阵列翻有气泡、斜波前没有**（旧 tile 的 psum 沿列下流、是**斜着**排空的：左上先出、右下最后出）：

- **整阵列一拍翻**：同拍翻就不能让任何旧 psum 还在阵列里（否则撞新权重、混 tile），
  必须**等旧 tile 全排空**才翻 → 空转 `≈AR+AC+L` 拍没喂新 tile = 气泡。
  ```
  2×2, F=2, L=0：
    cy0,1: 喂 T0
    cy2,3: 阵列排空 T0（空转，不能喂 T1）   ← 气泡
    cy4:   全翻 → 才开始喂 T1
  ```
- **反对角线波前**：每个 PE 各自翻——PE(k,n) 旧的用完(`(F-1)+k+n`)的下一拍就翻、立刻算新 tile，
  不等别的 PE。**新 tile 的填充波前紧贴旧 tile 的排空波前、斜着追(差 1 拍)**，阵列从不整体空闲。
  ```
  T0→T1 边界沿反对角线扫：
          cy1     cy2     cy3     cy4
   PE00:  T0末    T1      T1      T1
   PE01:  T0      T0末    T1      T1
   PE10:  T0      T0末    T1      T1
   PE11:  T0      T0      T0末    T1
   右下角还在排 T0 时，左上角已在算 T1 → 无空转拍；T1 喂从 cy2(=F) 起，紧接 T0 喂完(cy1)
  ```
  一句话：整阵列翻=等整个阵列空了再填；斜波前=哪个 PE 空了就填哪个 → 零气泡。

### 2.3 off-by-one
PE 的乘积用上一拍锁存的 a、b，所以 `w_switch` 要比"新激活到达"早 **1 拍**，使翻转后的 active
在乘积那拍就位。精确偏移（含 L）在实现时用测试钉死（类比 OS 的 `drain_delay=latency+1`）。

---

## 3. 切换是【受控事件】：切 / 不切

STREAM 里的 tile 边界分两种，由控制信号 `switch_weight` 门控（类比 OS 的 `new_tile`）：

术语（即 0.2 的 `kc`/`nt`）：**K-chunk = K 维切出的段**（同一块输出的下一段收缩，部分和要累加）；
**N-tile = N 维切出的块**（另一组输出列，互相独立、写新槽）。

| 边界 | switch_weight | 行为 | 用在 |
|---|---|---|---|
| 同权重续喂 | False | 不注入 switch 波前、不 shadow_load；feed/capture 继续 | M 分批 / 权重跨 batch 复用 |
| 换 K-chunk | True | 注入 switch 波前；输出**累加** | K-tiling |
| 换 N-tile | True | 注入 switch 波前；输出写**新槽** | N-tiling |

> "累加 vs 新槽" 属于**输出存储**（accumulator）层，controller 只管"切不切 = 注不注入 switch 波前"。

---

## 4. shadow load 机制

- 影子权重沿列**下移**载入（`is_shift_shadow` 路由：上边缘灌 `col_data`、其余取上邻 shadow）。
- **倒序喂权重行**：连续 AR 拍喂 `B[AR-1], B[AR-2], …, B[0]`，移位后 PE(k,n) 的 shadow = `B[k][n]`。
- `shadow_load` 是**逐拍动态信号**（不是静态拓扑 flag）：只在每个 tile 的 AR 拍 load 窗口高，
  灌满后拉低 → shadow **保持**到 switch（否则继续移会推走错位）。
- 约束：shadow 必须在该 tile 的 switch 波前（最早 PE(0,0)）之前填满 ⟹ 需要 **F ≥ AR**
  （否则 ping-pong 藏不住，属边界情况 WS-6，先 assert 挡住）。

---

## 5. pe / sa 接口（模式无感）

**原则：pe/sa 不知道 OS/WS，只认通用信号；模式判断全在 controller。**

- `pe`：权重寄存器 `b`（active）+ `b_shadow`；`prod = a * b`（恒用 active）。
  `pe.update(a_in, b_in, acc_in, shadow_in, w_switch)`：
  - `w_switch=True` → 翻转 `b ↔ b_shadow`（覆盖 b_in/shadow_in）；
  - 否则 `b_next=b_in`、`b_shadow_next=shadow_in`（None 则保持）。
- `sa`：静态构造拓扑 flag（`is_shift_col/row/acc_d/acc_l`，整 run 不变 = 模式）；
  `update(row_data, col_data, restore_data, restore_mask, w_switch, shadow_load)`：
  逐拍动态的是数据 + `shadow_load`（门控影子下移）+ **`w_switch`（per-PE 反对角线 mask）**。
  - **`w_switch` 从标量改为 `[AR][AC]` per-PE mask**（类比 `restore_mask`）：sa 把 `w_switch[r][c]`
    传给对应 PE，实现反对角线波前。

OS 调用方：`shadow_load=False`、`w_switch` 全 False、`is_shift_col=1/acc_d=0`，行为完全不变。

---

## 6. WS controller 状态机（5 态，独立于 OS）

**控制输入**（驱动/指令流给，类比 OS 的 `new_tile`/`tile_id`）：
- `switch_weight`(bool)：tile 边界要不要换权重 → 注不注 switch 波前（标准 GEMM 每边界都 True；
  False=同权重续喂，M 分批/复用，标准 GEMM 不触发、保留接口）。
- `tag`(int)：当前 tile 标识 → 打进 capture SR（见 §7、§A）。

```
IDLE  : avail                              → WLOAD,  cnt=0
WLOAD : cnt==AR-1 & shadow就绪              → STREAM, cnt=0   # 冷启动暴露载首 tile 权重
STREAM: feed 中、数据就绪                   → 继续喂 (cnt+1)
        cnt==F-1 边界:
          avail & (要切则 shadow 就绪)       → STREAM, cnt=0  # 按 switch_weight 注 switch 波前
          ¬avail (没了)                      → DRAIN,  cnt=0  # 末 tile 判定
          avail 但数据/shadow 没齐           → STALL          ★预留(v1 不触发)
        feed 中、激活 ¬avail (tile 内饥饿)   → STALL          ★预留(v1 不触发)
STALL : 数据齐                              → STREAM          # 恢复 feed/switch
        否则 hold：feed/switch 波前冻结，capture 波前继续，等权重则 shadow_load 继续
DRAIN : cnt==AR+AC+L-3                      → IDLE,   cnt=0   # 末 tile 排空到最后一个 capture
```

- 切换是 STREAM 期间的**反对角线波前事件**（per-PE mask），**不是独立状态**。
- **WLOAD→STREAM 恒注入一次 switch 波前**（首 tile 的 shadow→active，总是切，与 `switch_weight` 无关）。
- tile 全程连续、无气泡，**DRAIN 只在最后收尾一次**；后续 tile 的 shadow 在上一个 STREAM 里后台填好。
- **STALL（预留）**：统一处理 activation 饥饿 / weight 没备好两类间隙——`feed` 与 `switch` 波前
  一起冻、`capture` 波前不冻（在飞 psum 照排照出）；齐了回 STREAM。理想 `T=F` 退化成 `T>F`。
  v1：`assert avail 恒真 & F≥AR & shadow 恒就绪` → STALL 三条入边永不触发，但结构留着（见 §9 WS-5b/WS-6）。

---

## 7. 四道波前（移位寄存器，套 OS 思路；各自独立）

| 波前 | 几何 | 注入 / 内容 | 输出 |
|---|---|---|---|
| **feed**（read_activation）| 按行 skew，长 AR | `feeding=(STREAM)`，行 k 延 k | 行 k 在 `feed_start+[k,k+F-1]` 弹激活 |
| **switch**（w_switch）| 反对角线 `k+n`，per-PE mask | 首 tile(WLOAD→STREAM)**恒注入**；之后边界 `switch_weight=True` 时注入；riding feed（早 1 拍）| PE(k,n) 在 `feed_start+k+n` 翻转 |
| **shadow_load** | 沿列下移 | 当前 tile 进 STREAM(cnt==0)起、连灌 AR 拍后拉低保持(D1)；权重行来自 wb | 灌满下个 tile 的 b_shadow |
| **capture**（mask + 值-SR）| 底行、列 skew + 延迟 `(AR-1)+L` | 值-SR 每格存 **`(m, tag)`**：`inject=(喂的输出行 m, 当前 tile 的 tag)`（STREAM）| `cap[AR-1][n]=(m,tag)`，输出存储层据此写 `out`（§A：边界同拍不同列可不同 tag）|

- 冷启动 feed_start = AR（WLOAD 之后）；多 tile：tile t 的 feed_start = AR + t·F，各波前相对各自 feed_start 平移。
- capture 的延迟（`(AR-1)+L`）、switch 早 1 拍：精确值用测试校准（类比 OS `drain_delay`）。
- **capture 带 `tag` 是必须的**（§A）：背靠背时一个 tile 的 capture 尾巴和下个 tile 的头会在同拍、
  不同列出现，列与列可能属不同 tile/槽——光带 `m` 会写错。`tag`→槽/累加/列偏移 由输出存储层解释。

---

## 8. 同步约束小结
1. switch[k][n] ∈ (旧 tile 最后用 `(F-1)+k+n`，新 tile 首达 `T+k+n`]；取 `T=F` 背靠背。
2. shadow 必须在 switch 波前前填满 ⟹ `F ≥ AR`（否则 WS-6，先 assert）。
3. switch 早 feed 1 拍（pe 寄存器）。
4. DRAIN 必须跑到最后一个 capture 才结束（旧权重 psum 全输出后才会 IDLE）。

---

## 9. WS 案例清单（WS-1..WS-6）

按 ① K 切段（K vs AR）② N 切块（N vs AC）③ M 流长 ④ tile 节奏（背靠背 / gap）⑤ latency 枚举：

| 案例 | 场景 | 要点 |
|---|---|---|
| **WS-1** 单 tile | `K≤AR, N≤AC`，流 M 行 | 权重驻留、激活斜喂、psum 下流、底行 capture + row_map；latency 1&2、方阵+矩形。最基本 |
| **WS-2** K 切段 | `K>AR`，切多个 K-chunk | 部分和在**输出 buffer 累加(+=)**；ping-pong 后台换段；段间靠斜波前连续 |
| **WS-3** N 切块 | `N>AC`，切多个 N-tile | 各块写**不同槽(覆盖)**；ping-pong 跨块换权重 |
| **WS-4** K+N 全切 | `K>AR 且 N>AC` | 上两者叠加：nt 外、kc 内累加（见 0.2 例子）|
| **WS-5** 节奏 | (a) 数据就绪背靠背 (b) 下个数据迟到 | 5a：load 全藏住、零气泡；**5b gap**：DRAIN 完但权重/数据没好 → 停顿（对应 OS 的 OVERLAP_GAP）|
| **WS-6** `M<AR`(即 `F<AR`) | 流长 < 阵列行数 | ping-pong 的 AR 拍 load 窗口 > F 拍 STREAM → shadow 没灌满就到边界 → **藏不住、需额外停顿**。边界情况 |

> 第一版范围：**WS-6 先 `assert F≥AR` 挡住**、**WS-5b gap 先不做**（假设数据就绪，OS 的 gap 也是后加的）。

## 10. 实现 / 验证路线（与案例清单对应）
- **阶段 1 — WS-1 单 tile**（assert N≤AC、K≤AR、F≥AR）：WLOAD→STREAM（一次 switch 波前）→DRAIN；
  capture 进临时 buffer，校 `==A·B`，latency 1&2、方阵+矩形。
- **阶段 2 — WS-3 N 切块**：多 tile 背靠背，`switch_weight=True` 每边界切；各写各槽（覆盖）。
- **阶段 3 — 输出存储**：把 capture 落到 accumulator（OS/WS 用 `if` 分目标行；**待定**）。
- **阶段 4 — WS-2 K 切段（累加）+ WS-4 K+N**：依赖输出存储的累加。
- **阶段 5（以后）— WS-5b gap 停顿、WS-6 F<AR**。

> 输出存储（accumulator 复用 / 单开 buffer）**待定**，不挡阶段 1/2 的数据流与波前开发。

---

## 11. 数据通路（WS，驱动侧）

WS 的 FIFO 用法与 OS 不同（OS：权重按列流进 active、激活按行流过 K；WS：权重驻留、激活按行斜喂、M 走时间）：

- **ab（激活）= `CommonFIFO(AR, F)`**：AR 个 lane（每个 = 阵列一行 k），lane k 正常预载
  `A[:,k]`（当前 K 段第 k 列、长 F=M）。`read_activation[k]` 按行 skew 弹出。多 K-chunk 时每 tile 换段重载。
- **wb（权重）= `CommonFIFO(AC, AR)`**：AC 个 lane（每个 = 阵列一列 n），lane n 预载权重 tile 的
  第 n 列 `W[:,n]`（长 AR）。**倒序在写端**：预载时把行倒着写入；shadow_load 窗口**正常 FIFO 顺序**
  弹行 → col_data 自然是 `W[AR-1]…W[0]`，配合 shadow 下移让 `B[k]` 落到行 k。读逻辑不反着来。

> capture 的输出落到哪（accumulator 复用 / 单开 M×N buffer）属**输出存储层**，待定（§10 阶段 3）。
