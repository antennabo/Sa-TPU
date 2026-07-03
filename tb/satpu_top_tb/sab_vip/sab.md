# SAB (Simple Access Bus) Specification

## 1. Overview

**SAB (Simple Access Bus)** is a lightweight memory-mapped bus interface designed to balance **high throughput and low resource overhead** for on-chip communication.

It draws design inspiration from both AXI and AHB:
- From **AXI**: decoupled request and response channels, allowing response latency without stalling the interface
- From **AHB**: simple address/data structure with minimal handshake overhead

It is optimized for:
- Register (CFG) read/write access
- Simple RAM access
- Lightweight SoC internal interconnect where area and timing budget are constrained

**Supported Topology:**
- 1-to-1: single master, single slave
<!-- - 1-to-N: single master, multiple slaves (with address decoding) -->
- Multi-master configurations are not supported

**Transaction Characteristics:**
- No burst; each transaction is a single beat
- Multiple outstanding transactions supported, similar to AXI
- In-order completion only; responses must be returned in the same order as requests were issued
- Every read or write operation produces an address on the request channel

---

## 2. Interface Signals

### 2.1 Request Channel

| Signal          | Direction | Width  | Optional | Description |
|-----------------|-----------|--------|----------|-------------|
| `sab_req_valid` | Input     | 1      | No       | Master asserts to indicate a valid request |
| `sab_req_ready` | Output    | 1      | Yes      | Slave asserts to indicate it can accept the request |
| `sab_req_wen`   | Input     | 1      | No       | Write enable (1 = write, 0 = read) |
| `sab_req_addr`  | Input     | ADDR_W | No       | Target address |
| `sab_req_wdata` | Input     | DATA_W | No       | Write data |

A request is transferred when `sab_req_valid` is high and either `sab_req_ready` is high or `sab_req_ready` is not implemented (treated as always 1).

---

### 2.2 Response Channel

| Signal           | Direction | Width | Optional | Description |
|------------------|----------|--------|----------|-------------|
| `sab_resp_valid` | Output   | 1      | No       | Slave asserts to indicate a valid response |
| `sab_resp_ready` | Input    | 1      | Yes      | Master asserts to indicate it can accept the response |
| `sab_resp_err`   | Output   | 1      | Yes      | Slave asserts to indicate the transaction resulted in an error |
| `sab_resp_rdata` | Output   | DATA_W | No       | Read data returned from slave |

A response is transferred when `sab_resp_valid` is high and either `sab_resp_ready` is high or `sab_resp_ready` is not implemented (treated as always 1). When `sab_resp_err = 1`, `sab_resp_rdata` is don't care.

---

## 3. Timing Characteristics

- Request and response are **decoupled in time**
- Response may occur multiple cycles after request
- Multiple outstanding transactions are supported; responses are returned **in request order**
- A request is transferred when `sab_req_valid` and `sab_req_ready` are both high on the same rising edge; if `sab_req_ready` is not implemented, it is treated as always 1
- A response is transferred when `sab_resp_valid` and `sab_resp_ready` are both high on the same rising edge; if `sab_resp_ready` is not implemented, it is treated as always 1

> `x` = don't care. `(dc)` = don't care by protocol definition. Latencies shown are for illustration; actual latency is implementation-defined.

---

### 3.1 Write Transaction

**Case 1: Zero-latency (response in same cycle as request)**

```
Signal        T0      T1
-----------   ------  ------
req_valid     1       0
req_ready     1       x
req_wen       1       x
req_addr      ADDR    x
req_wdata     WDATA   x
resp_valid    1       0
resp_ready    1       x
resp_err      0       x
resp_rdata    (dc)    x
```

> Request accepted and response returned in the same cycle T0. Typical for simple register slaves.

**Case 2: Multi-cycle latency**

```
Signal        T0      T1      T2      T3
-----------   ------  ------  ------  ------
req_valid     1       0       0       0
req_ready     1       x       x       x
req_wen       1       x       x       x
req_addr      ADDR    x       x       x
req_wdata     WDATA   x       x       x
resp_valid    0       0       1       0
resp_ready    x       x       1       x
resp_err      x       x       0       x
resp_rdata    x       x       (dc)    x
```

> Request accepted at T0. Write completes at T2; `resp_rdata` is don't care for writes.

---

### 3.2 Read Transaction

```
Signal        T0      T1      T2      T3
-----------   ------  ------  ------  ------
req_valid     1       0       0       0
req_ready     1       x       x       x
req_wen       0       x       x       x
req_addr      ADDR    x       x       x
req_wdata     (dc)    x       x       x
resp_valid    0       0       1       0
resp_ready    x       x       1       x
resp_err      x       x       0       x
resp_rdata    x       x       RDATA   x
```

> `sab_resp_rdata` is valid only when `sab_resp_valid = 1` and `sab_resp_err = 0`.

---

### 3.3 Consecutive Requests (Pipelined Reads)

Master issues four read requests back-to-back at T0–T3. Responses are returned in request order starting at T3, overlapping with the last request.

```
Signal        T0      T1      T2      T3      T4      T5      T6
-----------   ------  ------  ------  ------  ------  ------  ------
req_valid     1       1       1       1       0       0       0
req_ready     1       1       1       1       x       x       x
req_wen       0       0       0       0       x       x       x
req_addr      ADDR1   ADDR2   ADDR3   ADDR4   x       x       x
req_wdata     (dc)    (dc)    (dc)    (dc)    x       x       x
resp_valid    0       0       0       1       1       1       1
resp_ready    x       x       x       1       1       1       1
resp_err      x       x       x       0       0       0       1
resp_rdata    x       x       x       RDATA1  RDATA2  RDATA3  (dc)
```

> All four requests accepted at T0–T3. First response (RDATA1) arrives at T3 (3-cycle latency), overlapping with the last request. Subsequent responses are returned one per cycle in request order. T6 returns an error (`resp_err = 1`); master must discard `resp_rdata`.

---
### 3.4 Consecutive Requests (Pipelined Write)

Master issues four write requests back-to-back at T0–T3. Responses are returned in request order starting at T3, overlapping with the last request.

```
Signal        T0      T1      T2      T3      T4      T5      T6
-----------   ------  ------  ------  ------  ------  ------  ------
req_valid     1       1       1       1       0       0       0
req_ready     1       1       1       1       x       x       x
req_wen       1       1       1       1       x       x       x
req_addr      ADDR1   ADDR2   ADDR3   ADDR4   x       x       x
req_wdata     WDATA1  WDATA2  WDATA3  WDATA4  x       x       x
resp_valid    0       0       0       1       1       1       1
resp_ready    x       x       x       1       1       1       1
resp_err      x       x       x       0       0       0       1
resp_rdata    x       x       x       (dc)    (dc)    (dc)    (dc)
```

> All four requests accepted at T0–T3. First response (RDATA1) arrives at T3 (3-cycle latency), overlapping with the last request. Subsequent responses are returned one per cycle in request order. T6 returns an error (`resp_err = 1`); master must discard `resp_rdata`.

---

### 3.5 Mixed Read/Write with Request Stall and Response Backpressure

Three transactions are issued: write ADDR1, read ADDR2, write ADDR3. The read request is stalled for one cycle at T1. The last write response is backpressured by the master at T4.

```
Signal        T0      T1      T2      T3      T4      T5
-----------   ------  ------  ------  ------  ------  ------
req_valid     1       1       1       1       0       0
req_ready     1       0       1       1       x       x
req_wen       1       0       0       1       x       x
req_addr      ADDR1   ADDR2   ADDR2   ADDR3   x       x
req_wdata     WDATA1  (dc)    (dc)    WDATA3  x       x
resp_valid    1       0       0       1       1       1
resp_ready    1       x       x       1       0       1
resp_err      0       x       x       0       0       0
resp_rdata    (dc)    x       x       RDATA2  (dc)    (dc)
```

> - **T0**: write ADDR1 accepted; write response returned in same cycle
> - **T1**: read ADDR2 stalled (`req_ready = 0`); master holds all request signals
> - **T2**: read ADDR2 accepted (`req_ready = 1`)
> - **T3**: write ADDR3 accepted; read response (RDATA2) returned in same cycle
> - **T4**: write ADDR3 response valid, but master not ready (`resp_ready = 0`); slave holds response
> - **T5**: write ADDR3 response accepted (`resp_ready = 1`)

---

### 3.6 Error Response

Slave returns `sab_resp_err = 1` to indicate a failed transaction. `sab_resp_rdata` is don't care.

```
Signal        T0      T1      T2      T3
-----------   ------  ------  ------  ------
req_valid     1       0       0       0
req_ready     1       x       x       x
req_wen       0       x       x       x
req_addr      ADDR    x       x       x
req_wdata     (dc)    x       x       x
resp_valid    0       0       1       0
resp_ready    x       x       1       x
resp_err      x       x       1       x
resp_rdata    x       x       (dc)    x
```

> `sab_resp_err = 1` at T2 indicates the transaction failed. Master must not use `sab_resp_rdata`.

---

## 5. Design Constraints

- No burst transactions
- No pipelined or out-of-order execution
- Single outstanding request only
- Simple memory-mapped addressing model
- No ready/backpressure signal — slave must accept request in the same cycle `sab_req_valid` is asserted

---

## 6. Typical Use Cases

- Configuration register access (CFG)
- Simple RAM read/write
- Control/status communication between modules
- Lightweight SoC internal interconnect

---