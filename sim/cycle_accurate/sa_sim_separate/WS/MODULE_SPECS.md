# TPU v1-style Weight-Stationary Cycle-Accurate Simulator — Module Specs

This document is the per-module specification for the TPU v1 weight-stationary cycle-accurate simulator. It captures all port lists, bit widths, exact cycle counts, and locked design decisions. It is the authoritative reference for implementation.

Location: `sim/cycle_accurate/sa_sim_separate/WS/`

---

## 0. Locked design decisions

| # | Decision |
|---|---|
| 1 | Array size **N = 4** by default; N = 8 for stress tests. |
| 2 | `load_enable` is a **global broadcast bit**. Weight data still shifts down cycle-by-cycle through the array (like activations, but vertical and unskewed). |
| 3 | `WeightFIFO(N, depth=None)` — parametric depth, unbounded by default. |
| 4 | Accumulator stays single-tile. K-direction reduction happens in `matmul.py` via numpy. |
| 5 | Trace mode: each module returns a dict of per-cycle port values when `trace=True`. |
| 6 | Dataflow: **activations left → right**, **psum top → bottom**, **weights stationary**, **weight preload from top with shift-down**. |
| 7 | Arithmetic: **INT8 × INT8 → INT32**, no truncation between PEs. Two's-complement wrap on overflow (numpy default). |
| 8 | Weight load order: **reversed rows** — WF emits `W[N-1]` first, `W[0]` last, so after N shifts each `W[i][j]` ends up at `PE[i][j]`. The reversal lives inside `WeightFIFO`; callers `push_tile(W_tile)` in natural row order. |

---

## 1. Dataflow recap

For matmul `C = A @ W` where `A : int8[M, K]` and `W : int8[K, N_w]` — **Convention A**: A streams, W stationary, **no transpose** on load.

- Tile `W` into N×N blocks. **`PE[i][j]` holds `W[i][j]` after preload** (loaded as-is, no transpose).
- The **K dimension of A maps to SA row index**; the **M dimension maps to time within a row**; the **N dimension maps to SA column index**.
- Stream `A` into the SA's **left edge** with **row-skew**: **row `k` of SA receives column `k` of A over time, delayed by `k` cycles**.
- At compute tick `T`, row `k` of SA receives `A[T − k, k]` if `0 ≤ T − k < M`, else 0 (bubble).
- Partial sums flow **top → bottom**, accumulating into the final dot product at the bottom of each column.
- Output `C[m, j]` is captured at the bottom edge of column `j` after compute tick `T = m + (N − 1) + j`.

**2×2 left-edge feed example.** With `A = [[a00, a01], [a10, a11]]`:

| Cycle T | Row 0 (column 0 of A) | Row 1 (column 1 of A) |
|---|---|---|
| 0 | a00 | — (bubble) |
| 1 | a10 | a01 |
| 2 | — | a11 |

Drawn as a "newest-about-to-enter on the left" 2×2 snapshot (the order values flow into the array's left edge):

```
Row 0:   a10,  a00
Row 1:   a11,  a01
```

This is the row-skewed rotation of the previous top-fed convention (the staggered shape now runs across rows instead of down columns).

**Cycle counts per single N×N tile** (with M activation rows):

| Phase | Ticks | Notes |
|---|---|---|
| Preload | **N** | `load_enable=1`, WF shifts weight rows down in reverse order. |
| Compute | **M + 2N − 2** | First output captured after tick `N − 1`; last output captured after tick `M + 2N − 3`. |
| **Total per tile** | **M + 3N − 2** | |

For N=4, M=4: 4 preload + 10 compute = **14 ticks**.

---

## 2. Inter-module wiring

```
                                      ┌──────────────┐
                                      │ WeightFIFO   │
                                      │              │
                                      │ push_tile()  │
                                      └──────┬───────┘
                                             │ (weight_in_top[N], load_enable)
                                             ▼
   ┌──────────────┐  (act_in_left[N])   ┌───────────────┐  (psum_out_bottom[N])   ┌──────────────┐
   │ DataSetup    │ ───────────────────▶│ SystolicArray │ ───────────────────────▶│ Accumulators │
   │              │                     │   N×N PEs     │                         │              │
   │ load_chunk() │                     │  top row:     │                         │ finish()     │
   └──────────────┘                     │  psum_in = 0  │                         └──────────────┘
                                        │  (hardwired)  │
                                        └───────────────┘
```

The driver (`matmul.py`) advances all four modules in lockstep, one tick per loop iteration.

---

## 3. Module: `pe.py` — `PE`

### Purpose
Atomic MAC processing element. Hardwired datapath: no per-PE control beyond the global `load_enable`. Does a MAC every cycle whether useful data is present or not.

### Internal state (registered)

| Name | Width | Description |
|---|---|---|
| `weight` | int8 | Stationary weight register. |
| `act_out` | int8 | Registered activation pass-through (this cycle's outgoing activation). |
| `psum_out` | int32 | Registered partial-sum pass-through (this cycle's outgoing psum). |
| `_weight_next` | int8 | Shadow for non-blocking update. |
| `_act_out_next` | int8 | Shadow. |
| `_psum_out_next` | int32 | Shadow. |

### Per-cycle interface

```
pe.tick(act_in, psum_in, weight_in, load_enable) -> None
```

| Input | Width | Source |
|---|---|---|
| `act_in` | int8 | West neighbor's `act_out`, or SA left-edge for j=0. |
| `psum_in` | int32 | North neighbor's `psum_out`, or SA top-edge for i=0. |
| `weight_in` | int8 | North neighbor's `weight`, or SA top-edge for i=0. |
| `load_enable` | bool | Global, broadcast from SA boundary. |

### Combinational logic (computed before tick commits)

```
product      = int32(weight) * int32(act_in)
psum_next    = psum_in + product                       # always int32
act_next     = act_in
weight_next  = weight_in if load_enable else weight    # implicit hold via mux
```

### State commit (on `tick()`)

```
weight   ← _weight_next
act_out  ← _act_out_next
psum_out ← _psum_out_next
```

### Read-only attributes
- `pe.weight`, `pe.act_out`, `pe.psum_out` — current registered values.

### Methods

| Method | Behavior |
|---|---|
| `__init__()` | Zero all registers. |
| `tick(act_in, psum_in, weight_in, load_enable)` | Compute next-state, commit. Single-call (no two-phase). |
| `reset()` | Zero all registers and shadows. |

### Invariants
- Every call to `tick()` does a MAC unconditionally (no enable gating).
- Reads (`pe.weight`, `pe.act_out`, `pe.psum_out`) reflect the state *after* the most recent `tick()`.
- INT32 multiply: int8 operands are promoted to int32 *before* multiplication.

---

## 4. Module: `systolic_array.py` — `SystolicArray`

### Purpose
A passive N×N grid of PEs. Owns the neighbor-to-neighbor wiring and the global clock. **No skew, no preload sequencing, no output framing.** Just raw edge ports.

### Internal state
- `N : int`
- `pes : list[list[PE]]`, shape N×N.

### Ports (per-cycle interface via `tick()`)

```
sa.tick(
    weight_in_top : np.ndarray[int8, shape=(N,)],
    load_enable   : bool,
    act_in_left   : np.ndarray[int8, shape=(N,)],
    trace         : bool = False,
) -> dict
```

| Input | Width | Description |
|---|---|---|
| `weight_in_top[j]` | int8 | Weight value driven into the top of column `j` (from WeightFIFO). |
| `load_enable` | bool | Global; True = shift weights down, False = hold. |
| `act_in_left[i]` | int8 | Activation value driven into the left of row `i` (from DataSetup). |

### Returned dict

| Key | Width | Description |
|---|---|---|
| `psum_out_bottom` | int32[N] | Each column's bottom-edge psum, post-tick. Consumed by Accumulators. |
| `act_out_right` | int8[N] | Each row's right-edge activation, post-tick. Unused in TPU v1; exposed for diagnostics. |
| `weight_out_bottom` | int8[N] | Each column's bottom-edge weight, post-tick. Unused; diagnostic / future weight-chain. |
| `trace` (if `trace=True`) | dict | Snapshots of all PE state: `{'weight': int8[N,N], 'act_out': int8[N,N], 'psum_out': int32[N,N]}`. |

### Internal wiring (per tick, executed before any `pe.tick()` is called)

For each `(i, j)`:
```
act_in_ij    = act_in_left[i]        if j == 0  else pes[i][j-1].act_out
psum_in_ij   = np.int32(0)           if i == 0  else pes[i-1][j].psum_out   # top row hardwired to 0
weight_in_ij = weight_in_top[j]      if i == 0  else pes[i-1][j].weight
```

Then for every PE: `pes[i][j].tick(act_in_ij, psum_in_ij, weight_in_ij, load_enable)`.

**Critical:** all `act_in_ij`, `psum_in_ij`, `weight_in_ij` values must be sampled from *current* PE registers BEFORE any `pe.tick()` runs. Because `tick()` is single-call (no two-phase), the SA must sample all neighbor values into local variables *first*, then call `tick()` on every PE. Use a buffered loop.

### Methods

| Method | Behavior |
|---|---|
| `__init__(N)` | Allocate N×N PEs, all reset. |
| `tick(weight_in_top, load_enable, psum_in_top, act_in_left, trace=False)` | One cycle. Returns output dict. |
| `reset()` | Reset every PE. |
| `snapshot() -> dict` | Read-only: returns `{'weight','act_out','psum_out'}` as int8/int32 ndarrays. |

### Invariants
- No internal state beyond `pes`. No mode bits, no cycle counters, no buffers.
- `tick()` is the only state-mutating method.

---

## 5. Module: `weight_fifo.py` — `WeightFIFO`

### Purpose
Drives the SA's top edge during weight preload. Queues weight tiles, emits them in reversed-row order over N cycles each, asserts `load_enable` during preload.

### Internal state
- `N : int`
- `depth : int | None` — max number of queued tiles; `None` = unbounded.
- `queue : collections.deque[np.ndarray[int8, (N, N)]]` — pending tiles.
- `_current_tile : np.ndarray | None` — tile being shifted in right now.
- `_load_counter : int` — counts 0 → N−1 during preload, then resets.
- `_state : str` — `'idle'` or `'loading'`.

### Ports

```
wf.push_tile(W_tile : np.ndarray[int8, (N, N)]) -> bool
```
Push a tile into the queue. Returns `True` on success, `False` if full (only relevant when `depth` is finite). Caller decides what to do with `False`.

```
wf.step() -> (weight_in_top : np.ndarray[int8, (N,)], load_enable : bool)
```
Called once per simulation cycle. Returns what to drive into SA's top edge this cycle.

### Per-cycle behavior of `step()`

```
if _state == 'idle':
    if queue is empty:
        return (zeros(N, int8), False)
    else:
        _current_tile = queue.popleft()
        _state = 'loading'
        _load_counter = 0
        # fall through

if _state == 'loading':
    row_idx = N - 1 - _load_counter            # reversed order
    out_row = _current_tile[row_idx, :].copy()
    _load_counter += 1
    if _load_counter == N:
        _state = 'idle'
        _current_tile = None
    return (out_row, True)
```

### Methods

| Method | Behavior |
|---|---|
| `__init__(N, depth=None)` | Set up state. |
| `push_tile(W_tile)` | Enqueue. Return False if full. |
| `step()` | Per-cycle. Returns `(weight_in_top, load_enable)`. |
| `is_idle() -> bool` | True iff no tile currently loading and queue empty. |
| `reset()` | Clear queue and state. |

### Invariants
- A tile, once started loading, finishes (N consecutive `load_enable=True` cycles). No mid-load preemption.
- Cycle count from `push_tile` followed by N consecutive `step()` calls: weights end up correctly placed in the SA (assuming `step()` outputs are wired straight to `sa.tick`'s top-edge inputs and SA is otherwise idle).

---

## 6. Module: `data_setup.py` — `DataSetup`

### Purpose
Drives the SA's left edge during compute. Implements the row-skew: row `i` starts emitting its activation row at compute tick `i`, with one activation per cycle thereafter.

### Internal state
- `N : int`
- `_chunk : np.ndarray[int8, (M, N)] | None` — the currently-being-streamed A-chunk.
- `_cycle : int` — counts compute-phase cycles since `load_chunk()`.
- `_M : int` — number of activation rows in current chunk.

### Ports

```
ds.load_chunk(A_chunk : np.ndarray[int8, (M, N)]) -> None
```
Replace internal buffer with `A_chunk`. Resets `_cycle` to 0.

```
ds.step() -> act_in_left : np.ndarray[int8, (N,)]
```
Called once per compute cycle. Returns the activation vector to drive into the SA's left edge.

### Per-cycle behavior of `step()`

```
T = _cycle
out = zeros(N, int8)
for i in range(N):
    m = T - i                # which activation row goes into SA row i at this tick
    if 0 <= m < _M:
        out[i] = _chunk[m, i]   # SA row i receives column i of A_chunk
    else:
        out[i] = 0           # bubble (pre-warmup or post-drain)
_cycle += 1
return out
```

**Feed example (M=2, N=2)** — with `A_chunk = [[a00, a01], [a10, a11]]`:
- T=0 → `[a00, 0]` (only row 0 active; row 1 still in skew warmup)
- T=1 → `[a10, a01]`
- T=2 → `[0, a11]` (row 0 drained; row 1 finishing)

Row `k` of the SA sees column `k` of the chunk over time, delayed by `k` cycles. This is the row-skewed pattern visualized in §1.

### Methods

| Method | Behavior |
|---|---|
| `__init__(N)` | Empty state. |
| `load_chunk(A_chunk)` | Load and reset counter. |
| `step()` | Per-cycle. Returns one length-N int8 vector. |
| `reset()` | Clear buffer and counter. |
| `cycles_needed(M) -> int` | Returns `M + N − 1` — the number of `step()` calls needed to drain row N−1's last activation. (Excludes downstream propagation.) |

### Invariants
- `load_chunk` must be called before the first `step()` of a compute phase.
- After `M + N − 1` calls, all useful activations have been injected; further `step()` calls return zeros (drain bubbles).

---

## 7. Module: `accumulators.py` — `Accumulators`

### Purpose
Captures bottom-edge outputs from the SA at the correct `(m, j)` positions to reconstruct the output tile `C[M, N]`.

### Internal state
- `N : int`
- `_buffer : np.ndarray[int32, (M, N)] | None` — output buffer.
- `_M : int`
- `_cycle : int` — counts compute-phase cycles since `start_collect()`.

### Ports

```
ac.start_collect(M : int) -> None
```
Allocate an M×N int32 buffer; reset cycle counter.

```
ac.step(psum_out_bottom : np.ndarray[int32, (N,)]) -> None
```
Called once per compute cycle, right after `sa.tick()`. Samples the bottom-edge outputs and stores into `_buffer[m, j]` for valid `(m, j)`.

```
ac.finish() -> np.ndarray[int32, (M, N)]
```
Returns the completed output buffer. Caller is responsible for downstream use.

### Per-cycle behavior of `step()`

```
T = _cycle
for j in range(N):
    m = T - (N - 1) - j
    if 0 <= m < _M:
        _buffer[m, j] = psum_out_bottom[j]
_cycle += 1
```

### Methods

| Method | Behavior |
|---|---|
| `__init__(N)` | Empty state. |
| `start_collect(M)` | Allocate buffer, reset counter. |
| `step(psum_out_bottom)` | Capture per-cycle. |
| `finish() -> np.ndarray` | Return the buffer. |
| `reset()` | Clear buffer and counter. |
| `cycles_needed(M) -> int` | Returns `M + 2N − 2`. Total compute ticks needed to capture all outputs. |

### Invariants
- `start_collect` must be called before any `step()`.
- After `M + 2N − 2` calls, every `_buffer[m, j]` for `0 ≤ m < M, 0 ≤ j < N` has been written exactly once.

---

## 8. Module: `tiling.py` — `Tiler`

### Purpose
Pure data layout. No cycle awareness, no dataflow awareness.

### Internal state
- `N : int`

### Methods

```
tiler.weight_tiles(W : np.ndarray[int8, (K, N_w)])
    -> Iterator[(W_tile : int8[N, N], k_block : int, j_block : int)]
```
Yield N×N tiles of `W` in row-major order (`k_block` slow, `j_block` fast). Zero-pad K and N_w dimensions if not divisible.

```
tiler.act_chunk(A : np.ndarray[int8, (M, K)], k_block : int)
    -> np.ndarray[int8, (M, N)]
```
Return the K-direction slice `A[:, k_block*N : (k_block+1)*N]` with zero-pad on the K dimension.

```
tiler.accumulate(C : np.ndarray[int32, (M, N_w)],
                 C_tile : np.ndarray[int32, (M, N)],
                 j_block : int) -> None
```
In-place: `C[:, j_block*N : (j_block+1)*N] += C_tile[:, :j_width]`, clipped to `N_w` and `M` bounds.

```
tiler.alloc_C(M : int, N_w : int) -> np.ndarray[int32, (M, N_w)]
```
Zero-filled output buffer.

### Invariants
- Padded cells are zero, so they contribute zero to the matmul.
- No knowledge of SA or cycles.

---

## 9. Module: `matmul.py` — top-level orchestrator (CU stub)

### Purpose
Wire all four hardware modules together and run a tiled matmul. Plays the role of the Control Unit at Phase 0.

### Entry point

```
matmul(A : np.ndarray[int8, (M, K)],
       W : np.ndarray[int8, (K, N_w)],
       N : int = 4,
       trace : bool = False)
    -> np.ndarray[int32, (M, N_w)]
```

### Algorithm

```
sa     = SystolicArray(N)
wf     = WeightFIFO(N)
ds     = DataSetup(N)
ac     = Accumulators(N)
tiler  = Tiler(N)
C      = tiler.alloc_C(M, N_w)

for W_tile, k_blk, j_blk in tiler.weight_tiles(W):
    A_chunk = tiler.act_chunk(A, k_blk)        # (M, N), zero-padded

    sa.reset(); wf.reset(); ds.reset(); ac.reset()

    wf.push_tile(W_tile)
    ds.load_chunk(A_chunk)
    ac.start_collect(M)

    # Preload phase: N cycles
    for _ in range(N):
        w_top, load_en = wf.step()
        sa.tick(weight_in_top=w_top, load_enable=load_en,
                act_in_left=zeros(N, int8))

    # Compute phase: M + 2N - 2 cycles
    for _ in range(M + 2*N - 2):
        w_top, load_en = wf.step()               # WF idle → returns zeros, False
        act_left       = ds.step()
        out = sa.tick(weight_in_top=w_top, load_enable=load_en,
                      act_in_left=act_left)
        ac.step(out['psum_out_bottom'])

    C_tile = ac.finish()                          # (M, N)
    tiler.accumulate(C, C_tile, j_blk)            # K-direction reduction via numpy

return C
```

### Invariants
- Total ticks per tile: `N + (M + 2N − 2) = M + 3N − 2`.
- All four modules' `reset()` is called between tiles to flush stale state.
- INT32 wrap on overflow is acceptable (matches HW).

---

## 10. Implementation order and verification stages

### Stage 1 — `pe.py`
**Implement:** PE class with register-transfer semantics.

**Test file:** `tests/test_pe.py`. Validate:
- T1: After init, all registers are 0.
- T2: Drive `tick(act_in=3, psum_in=4, weight_in=2, load_enable=True)` → after tick: `weight==2`, `act_out==3`, `psum_out == 4 + (initial_weight=0)*3 == 4`. **Note:** product uses *current* weight (still 0), not the new one being loaded — so psum_out = 4. This is correct HW behavior (weight update and MAC are concurrent; the new weight isn't visible until next cycle).
- T3: Drive again with `load_enable=False`, same `weight_in` → weight holds at 2.
- T4: With weight=2, drive `tick(act=3, psum=4, w_in=99, load_enable=False)` → psum_out = 4 + 2*3 = 10, weight still 2.
- T5: `reset()` zeros everything.

**Gate:** All tests pass. PE-level register-transfer behavior is correct.

### Stage 2 — `systolic_array.py`
**Implement:** SystolicArray with passive wiring + `tick()`.

**Test file:** `tests/test_systolic_array.py`. Validate by hand-driving edges:
- T1: N=2. Drive 2 cycles of preload manually: cycle 0 with `weight_in_top=[W[1][0], W[1][1]]`, `load_enable=True`; cycle 1 with `weight_in_top=[W[0][0], W[0][1]]`, `load_enable=True`. After 2 ticks: snapshot weights == `W`.
- T2: Same array, with weights loaded. Drive a known activation pattern with manual skewing for one compute pass. Track `psum_out_bottom[j]` per cycle, verify the cycle-when-valid and the values against numpy.
- T3: N=4 random int8: same as T2.
- T4: Pure pass-through test — weights all zero, drive activations — psum_out_bottom should always be 0.
- T5: Trace mode produces correct dict shape (`(N,N)` for each field).

**Gate:** SA + PE together pass all tests. **This is the core cycle-accurate validation point** — everything else is plumbing.

### Stage 3 — `weight_fifo.py` + `data_setup.py` + `accumulators.py`
**Implement:** Three small modules. Then write a single integration test wiring all of them with the SA.

**Test files:** `tests/test_weight_fifo.py`, `tests/test_data_setup.py`, `tests/test_accumulators.py`, `tests/test_mmu_block.py`.

**Per-module unit tests:**
- WF: push 2 tiles, call `step()` 2N times, verify each cycle's emitted row matches the expected reversed-row order. Verify `load_enable` is True during loading and False between tiles.
- DS: `load_chunk` an M=3, N=2 matrix. Call `step()` for `cycles_needed(M)` times. Verify the emitted sequence matches the skewed pattern by hand.
- AC: `start_collect(M=3)` for N=2. Feed synthetic `psum_out_bottom` vectors per cycle. Verify `_buffer` is filled at the correct `(m, j)` positions.

**Integration test (`test_mmu_block.py`):**
- T1: N=2, A=[[5,6],[7,8]], W=[[1,2],[3,4]]. Wire WF + DS + AC to SA. Run preload (N=2 ticks) + compute (M + 2N − 2 = 4 ticks). Expected: `ac.finish() == A @ W == [[23, 34], [31, 46]]`. In the test, always derive the expected value via numpy (`A.astype(np.int32) @ W.astype(np.int32)`) rather than hardcoding.
- T2: N=4, M=4, random int8: matches `A @ W` from numpy.
- T3: M=1 (single row): correct.
- T4: M >> N (e.g., M=10, N=4): correct.

**Gate:** A complete single-tile MMU operation matches numpy for arbitrary M.

### Stage 4 — `tiling.py`
**Implement:** Tiler.

**Test file:** `tests/test_tiling.py`.
- T1: Divisible shapes: `weight_tiles` yields the right number of tiles, each with correct content.
- T2: Non-divisible: padding cells are zero.
- T3: Round-trip: build a fake `C` via tile decomposition + numpy multiplies + `accumulate`; should equal `A @ W`.

**Gate:** Tiler is a pure data-layout function that round-trips correctly.

### Stage 5 — `matmul.py`
**Implement:** Top-level orchestrator.

**Test file:** `tests/test_matmul.py`.
- T1: Divisible: N=2, A[4,4], W[4,4] → matches numpy.
- T2: Non-divisible: N=2, A[5,3], W[3,7] → matches numpy.
- T3: Single-tile fast-path: A and W both fit in one N×N tile.
- T4: K >> N (many K-tiles, accumulated): matches numpy.
- T5: N=8, A[16,24], W[24,12]: matches numpy.

**Gate:** Full tiled matmul matches numpy for arbitrary int8 shapes.

---

## 11. File layout

```
sa_sim_separate/WS/
  MODULE_SPECS.md          ← this file
  pe.py
  systolic_array.py
  weight_fifo.py
  data_setup.py
  accumulators.py
  tiling.py
  matmul.py
  tests/
    test_pe.py
    test_systolic_array.py
    test_weight_fifo.py
    test_data_setup.py
    test_accumulators.py
    test_mmu_block.py
    test_tiling.py
    test_matmul.py
```

---

## 12. Future-proofing hooks

These are explicit API extension points for Phase 1+ modules.

| Future module | Hook | How it plugs in |
|---|---|---|
| Unified Buffer | `DataSetup.load_chunk()` becomes pull-based | Replace `load_chunk(A_chunk)` with `attach_ub(ub, read_addr, length)`. SA-facing `step()` unchanged. |
| DRAM / Weight Memory | `WeightFIFO` push backpressure | Use finite `depth`. Upstream caller blocks on `push_tile() → False`. |
| Activation Pipeline | `Accumulators.finish()` becomes streaming | Replace with `drain_to(activation_pipeline, dest_addr)`. SA-facing `step()` unchanged. |
| K-tile carry inside AC | `Accumulators.start_collect(..., accumulate=True)` | New parameter; first tile uses `accumulate=False`, subsequent K-tiles use `True`. Removes numpy-side reduction in `matmul.py`. |
| Control Unit | `matmul.py` becomes `control_unit.py` | The Phase-0 loop migrates verbatim into a CU instruction handler. `matmul.py` becomes a thin wrapper. |

---

## 13. Hand-off notes for next session

To resume:
1. Read this file in full.
2. Confirm decisions in §0 are still locked.
3. Start with **Stage 1** (`pe.py` + `tests/test_pe.py`). Do not begin Stage 2 until Stage 1's gate passes.
4. User preferences (from auto-memory): assisted mode — share full code in chat; user writes files. Only use Write/Edit tools when explicitly asked.
5. Cycle counts and port widths in this document are authoritative. If you find a discrepancy during implementation, fix this document first, then re-derive the code.