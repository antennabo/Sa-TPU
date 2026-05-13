# Scheme D — General Cycle-Accurate Systolic Array Simulator: Design & Implementation Plan

## Context for a fresh session

This document is the design plan for a **general, dataflow-agnostic** rewrite of the cycle-accurate systolic-array simulator. The existing implementation lives in `sim/cycle_accurate/sa_sim/` and has separate `WSPE/OSPE` PE classes and `WSSystolicArray/OSSystolicArray` SA classes (i.e. dataflow is baked into the structural modules). The teammate's request is to **unify PE/SA/tiling so they are mode-agnostic**, and let dataflow live entirely in *control programs*. The simulator should remain **cycle-accurate**.

This plan is **Scheme D**: the most extreme separation of structure from behavior.

- **PE**: register file + combinational MAC. No modes, no ctrl signals.
- **SA**: passive N×N container of PEs with a global clock. No wiring code, no border injection, no mode awareness.
- **Tiling**: pure data layout. Zero-padding and reassembly. Dataflow-agnostic.
- **Control programs**: one Python file per dataflow (WS, OS, …). Cycle-by-cycle microcode that reads PE current values and writes PE shadow registers, then ticks. **All** protocol, skewing, preload timing, and readout lives here.

The central claim of Scheme D: **switching dataflows never touches PE/SA/Tiling code.** Add a new dataflow = add a new control file.

Tradeoff (eyes-open): the control program becomes verbose — it hand-plumbs neighbor-to-neighbor data movement every cycle. Total complexity is the same as Scheme A/B; we're just relocating it to a single file per dataflow.

This file location: `sim/cycle_accurate/sa_sim_general/SCHEME_D_PLAN.md` (parallel to existing `sa_sim/`).

---

## Module 1 — `pe.py` (`PE` class)

### Purpose
Model a single MAC PE as a **register file + combinational MAC**. Fully passive — no protocol awareness.

### State (instance attributes)
- `r_a : int8` — operand-A register. (Holds weight in WS; transient weight in OS.)
- `r_b : int8` — operand-B register. (Holds activation in both modes.)
- `r_acc : int32` — 32-bit register. (Holds psum-in-flight in WS; accumulator in OS.)
- `_writes : dict` — pending shadow writes for this cycle (internal, transient).

That's all. No mode, no `static_reg` distinct from `r_a`, no acc-vs-pass distinction.

### Interface
- **Reads** (return current registered values): `pe.r_a`, `pe.r_b`, `pe.r_acc` as plain attributes.
- **Writes** (queue shadow updates, committed at tick):
  - `pe.write_a(v)`, `pe.write_b(v)`, `pe.write_acc(v)`
- **Combinational MAC** (no state change): `pe.mac() → int32`, returns `r_acc + r_a * r_b` using *current* register values.
- **Clock edge**: `pe.tick()` — commits any queued shadow writes; unwritten registers hold.
- **Reset**: `pe.reset()` — zero all registers, clear shadows.

### Semantic rules (enforce as comments + tests)
1. **Reads see current values; writes go to shadow.** A `write_a(v)` followed immediately by `r_a` returns the OLD value. Only after `tick()` does `r_a` become `v`.
2. **Unwritten registers hold across `tick()`.** This is the "implicit hold" convention — control programs only have to write registers they want to change. This is the mechanism that lets WS keep a weight stationary without any ctrl bit.
3. **`mac()` is combinational and free to call any number of times per cycle.** It never mutates state.

### What the PE deliberately does NOT have
- No mode/ctrl signals.
- No `load_weight()` (control writes `r_a` directly via `write_a`).
- No knowledge of neighbors, position, or wiring.
- No notion of "stationary" — that's a property of how control chooses to drive it.

---

## Module 2 — `systolic_array.py` (`SystolicArray` class)

### Purpose
A passive N×N container of `PE`s with a single global clock. **No topology code, no border injection, no mode awareness.**

### State
- `pes : list[list[PE]]` — N×N grid.
- `N : int`.

### Interface
- **Construction**: `SystolicArray(N)` — allocates `N*N` PEs, all reset.
- **PE access**: `sa.pe(i, j) → PE` or just `sa.pes[i][j]`.
- **Global clock**: `sa.tick()` — calls `tick()` on every PE in the grid.
- **Reset**: `sa.reset()` — calls `reset()` on every PE.
- **Snapshot helper** (optional but useful for tracing): `sa.snapshot(field) → np.ndarray[N,N]` where `field ∈ {'r_a','r_b','r_acc','mac'}`. Pure read, for diagnostics and tests.

### What the SA deliberately does NOT have
- **No `compute()` method.** All orchestration is in control programs.
- **No wiring code.** Neighbor-to-neighbor data movement is the control program's job (it reads `sa.pes[i][j-1].r_a` and writes `sa.pes[i][j].write_a(...)`).
- **No border injection logic, no skew helpers, no mode signal.**

This is the price of Scheme D: the SA stays trivial.

---

## Module 3 — `tiling.py` (`Tiler` class)

### Purpose
Pure data-layout layer. Splits big matrices into N×N-friendly chunks with zero-padding; reassembles results. **Dataflow-agnostic.**

### State
- `N : int` — SA size.

### Interface
- **Tile decomposition for matmul `C = W @ X`**:
  - `tiler.tiles(W, X) → iterator[(W_tile, X_tile, i_block, j_block, k_block)]`
  - `W_tile : int8[N, N]`, `X_tile : int8[N, N]` (zero-padded as needed).
  - `i_block, j_block` — row/col block index in output C.
  - `k_block` — index along the contraction dimension (multiple k-blocks accumulate into the same output block).
- **Result accumulation**: `tiler.accumulate(C, C_tile, i_block, j_block)` — adds `C_tile` (clipped to real dims) into `C`.
- **Output allocator**: `tiler.alloc_C(M_rows, M_cols) → np.ndarray[int32]` zero-filled.

### What it deliberately does NOT have
- No knowledge of WS/OS, no skew application, no SA reference, no cycle concept.
- Tiles delivered as **unskewed raw matrices**. Skewing belongs to control programs.

---

## Module 4 — `control_os.py` (Output-Stationary control program)

### Purpose
Cycle-accurate microcode that drives a `SystolicArray` to perform `C_tile = W_tile @ X_tile` in OS dataflow. Single entry point.

### Entry point
`run_os_tile(sa, W_tile, X_tile, record_trace=False) → C_tile [, trace]`

Inputs: `W_tile [N, K]`, `X_tile [K, N]`. (For square tiles K=N; tiler can produce non-square if you want partial K-blocks.)

### Phases
1. **Reset**: `sa.reset()`.
2. **Compute** (`K + 2N − 2` cycles): each cycle the control program performs the following sequence:
   - **Sub-step A — read currents**: snapshot the values it needs for routing (read `sa.pes[i][j-1].r_a` and `sa.pes[i-1][j].r_b` for interior PEs, etc.).
   - **Sub-step B — write shadows**: for every PE, queue `write_a`, `write_b`, `write_acc`:
     - `r_a` flows left → right: `pes[i][j].write_a(pes[i][j-1].r_a)`, with left-border injection `pes[i][0].write_a(W_tile[i, t-i])` when `0 ≤ t-i < K`, else 0.
     - `r_b` flows top → bottom: `pes[i][j].write_b(pes[i-1][j].r_b)`, with top-border injection `pes[0][j].write_b(X_tile[t-j, j])` when `0 ≤ t-j < K`, else 0.
     - `r_acc` self-accumulates: `pes[i][j].write_acc(pes[i][j].mac())`.
   - **Sub-step C — tick**: `sa.tick()`.
   - (If `record_trace`: capture `sa.snapshot('r_acc')` after tick.)
3. **Readout**: `C_tile[i,j] = sa.pes[i][j].r_acc` for all i,j.

### Cycle-accuracy invariant
All Sub-step B writes use the values read in Sub-step A (i.e. *pre-tick* values). Because PE shadow writes don't affect `r_a/r_b/r_acc` until `tick()`, you can interleave reads and writes freely — but be explicit about reading neighbor state, not your own pending shadow.

### Skewing
Lives entirely in the border-injection formulas above. The tiler hands you unskewed `W_tile, X_tile`; the control program indexes them with `t − i` and `t − j`.

---

## Module 5 — `control_ws.py` (Weight-Stationary control program)

### Purpose
Cycle-accurate microcode for WS dataflow. Same SA, same PE, completely different protocol.

### Entry point
`run_ws_tile(sa, W_tile, X_tile, record_trace=False) → C_tile [, trace]`

Inputs: `W_tile [N, N]` (must match SA size for stationary preload), `X_tile [N, M]` (M is output-column count, can be ≤ N or > N depending on tiling).

### Phases
1. **Reset**: `sa.reset()`.
2. **Preload** (choose convention; see open question below):
   - *(Streaming preload, more HW-faithful)*: stream `W_tile` from the top over `2N − 1` cycles, with each column shifting down each cycle. After preload, `pes[i][j].r_a = W_tile[i][j]`. During preload, control writes `pes[i+1][j].write_a(pes[i][j].r_a)` and top-border `pes[0][j].write_a(W_tile[t][j])` with t-dependent skew.
   - *(Instant preload, simpler)*: one cycle: `pes[i][j].write_a(W_tile[i][j])`; tick.
3. **Compute** (`N + M − 1` cycles, or `2N + M − 2` if you want the same shape as today):
   - `r_a` is **not written** (holds the preloaded weight, per implicit-hold semantics).
   - `r_b` flows top → bottom: same shift pattern as OS, with top-border injection `pes[0][j].write_b(X_tile[j, t−j])` when in range.
   - `r_acc` flows left → right and carries the partial sum:
     - `pes[i][j].write_acc(pes[i][j-1].mac())` for interior (psum arrives from left's MAC result).
     - `pes[i][0].write_acc(0)` for left-border (fresh psum, no carry-in).
   - At the right border, each cycle, read `pes[i][N-1].mac()` and stash it into `C_tile[i, p]` where `p = t − (N−1)`.
   - tick.
4. **Drain** (a few extra cycles to let final values reach the right edge — already covered by the loop bound above).

### Output collection
`C_tile[i, p]` is written during compute phase at the cycle when the relevant MAC result reaches the right-edge PE. Exact formula depends on which preload convention you pick.

### Crucial detail
**`r_a` is never written during compute.** Implicit-hold semantics keep the weight in place. This is the only place where "stationary" gets enforced — purely as an absence of writes, not a ctrl bit.

---

## Module 6 — `matmul_api.py` (top-level glue)

### Purpose
Single user-facing function that picks a dataflow and tiles a full matmul.

### Entry point
`matmul(W, X, dataflow='WS', N=8, record_trace=False) → C`

### Algorithm
1. Allocate `C = tiler.alloc_C(W.shape[0], X.shape[1])`.
2. Build `sa = SystolicArray(N)` and `tiler = Tiler(N)`.
3. Pick the control function: `run = run_ws_tile if dataflow=='WS' else run_os_tile`.
4. For each `(W_tile, X_tile, i_blk, j_blk, k_blk)` from `tiler.tiles(W, X)`:
   - `C_tile = run(sa, W_tile, X_tile)`.
   - `tiler.accumulate(C, C_tile, i_blk, j_blk)` (this naturally sums across k-blocks).
5. Return `C`.

### What it deliberately does NOT do
No protocol logic, no cycle counts, no skewing. Pure dispatch + accumulate.

---

## Step-by-step implementation order

Each step ends with a clear validation gate. Do not move on until the gate passes.

### Step 1 — `PE`
Implement `PE` with the 3 registers, the shadow-write mechanism, `mac()`, `tick()`, `reset()`.

**Validation:**
- T1: write_a(5), read r_a immediately → still 0 (shadow isolation).
- T2: write_a(5), tick, read r_a → 5.
- T3: write_a(5), tick; write_b(7), tick → r_a still 5 (implicit hold).
- T4: set r_a=2, r_b=3, r_acc=4 → mac() == 4+2*3 == 10.
- T5: mac() called twice → same result, no state change.
- T6: reset() zeros everything.

### Step 2 — `SystolicArray`
Implement container + `tick()` + `reset()` + `snapshot()`.

**Validation:**
- T1: SA(N=2) has 4 distinct PE objects.
- T2: write to sa.pes[0][0].write_a(9), tick, then sa.pes[0][1].r_a still 0.
- T3: snapshot('r_a') returns correct N×N array.
- T4: sa.reset() zeros all PE state.

### Step 3 — `Tiler`
Implement `tiles()`, `accumulate()`, `alloc_C()`.

**Validation:**
- T1: tile a 4×4 matmul with N=2 → tiler yields 8 tiles (2×2×2 in i,j,k), all 2×2.
- T2: tile a 3×3 matmul with N=2 → padded to 4×4 effectively; tiles yielded, padding cells are zero.
- T3: round-trip: allocate C, accumulate all tiles from `W @ X` (computed with numpy as reference), result matches `W @ X`.

### Step 4 — `control_os.run_os_tile`
Implement the OS control program against an SA.

**Validation:**
- T1: N=2, W=[[1,2],[3,4]], X=[[5,6],[7,8]] → C == [[19,22],[43,50]].
- T2: N=2, non-square K=3: matches `W @ X` from numpy.
- T3: N=4 with random int8 matrices: matches numpy reference.
- T4: trace mode produces correct length sequence; final snapshot == result.

### Step 5 — `control_ws.run_ws_tile`
Implement the WS control program against the same SA.

**Validation:**
- T1: N=2, same W/X as OS T1 → same C (cross-mode equivalence).
- T2: M=1 (single output column): correct.
- T3: M=4 with N=2: correct.
- T4: weights actually held — after compute, snapshot('r_a') still equals W_tile.

### Step 6 — `matmul_api.matmul`
Implement the top-level glue using `Tiler` + control programs.

**Validation:**
- T1: tiled WS matmul of a 6×6 problem with N=2: matches numpy.
- T2: same problem with OS: matches numpy.
- T3: non-divisible shape (5×7 problem with N=2) for both modes: matches numpy.

### Step 7 — Cross-mode + reuse audit
A final pass to confirm Scheme D's central claim:
- T1: PE, SA, Tiler files were not edited between writing `control_os` and `control_ws` (or commit history shows no functional change). This is the success criterion for the whole architecture.
- T2: Add a third trivial control program (e.g., a "no-op" or a sanity dataflow) to confirm only the control file is needed.

---

## Open design questions to lock before any code

These should be answered (and the answers recorded back into this document) before implementation starts.

1. **Implicit-hold for unwritten registers** — confirm semantics: if control doesn't call `write_a`, `r_a` keeps its value after `tick()`. (Recommendation: **yes** — this is what makes WS preload work without ctrl bits.)
2. **PE shadow-write API style** — `pe.write_a(v)` methods, or plain attribute `pe.r_a_next` that you assign? Methods are friendlier (can validate dtypes); attributes are terser. (Recommendation: **methods**.)
3. **WS preload protocol** — instant 1-cycle preload (simpler, less HW-faithful) vs. streamed `2N−1`-cycle preload (matches a real WS accelerator)? (Recommendation: **streamed** to keep "cycle-accurate" meaningful.)
4. **Skew direction for OS** — keep today's convention (`PE[i][j]` processes `k = t − i − j`)? (Recommendation: **yes**, so existing test vectors are reusable.)
5. **`mac()` width semantics** — `r_acc + r_a*r_b` computed in int32 (with int8 operands promoted before multiply). Wrap on overflow (numpy default) or saturate? (Recommendation: **wrap**, faithful to two's-complement HW.)
6. **Trace format** — list of per-cycle snapshots (currently `acc`-grid)? (Recommendation: same shape as today for backward-compat with whatever you're already plotting.)
7. **Where do W/X bitwidths get enforced?** At tile boundary (Tiler casts to int8) or earlier (matmul API rejects non-int8)? (Recommendation: **at the Tiler** so anything entering the SA is already int8.)

---

## Reference: relationship to existing `sa_sim/`

Existing files under `sim/cycle_accurate/sa_sim/`:
- `pe.py` — has `WSPE` and `OSPE` (separate classes per mode).
- `systolic_array.py` — has `WSSystolicArray` and `OSSystolicArray` (separate classes per mode, `compute()` method baked in).
- `tiling.py` — currently contains tile decomposition logic; may already be mostly dataflow-agnostic (worth reading before rewriting).

These remain untouched. Scheme D is a parallel implementation under `sim/cycle_accurate/sa_sim_general/`. The two can coexist; cross-validation between them is a good integration test (Scheme D + numpy + existing `sa_sim` all agree on the same matmul).

---

## Hand-off notes for the next session

To resume this work, a fresh session should:
1. Read this file in full.
2. Read `sim/cycle_accurate/sa_sim/pe.py` and `sim/cycle_accurate/sa_sim/systolic_array.py` to understand the reference cycle-accurate behavior the new modules must match (specifically: total cycle counts, skew formulas, readout timing).
3. Read `sim/cycle_accurate/sa_sim/tiling.py` to see whether the existing Tiler is already dataflow-agnostic enough to reuse (it may be — only rewrite if needed).
4. Ask the user to lock the 7 open design questions above.
5. Implement Step 1 (PE) first with its full validation gate before proceeding.
6. Do not touch existing `sa_sim/` files — Scheme D is a parallel implementation.

User preferences (from prior session memory):
- Collaboration style is "assisted mode": share full code in chat; let the user write files. Only use `Write/Edit` tools when explicitly asked.
- INT8 × INT8 → INT32 contract is locked (no truncation between PEs).
- 8×8 array is the eventual target (current testing uses N=2 or N=4 for clarity).
