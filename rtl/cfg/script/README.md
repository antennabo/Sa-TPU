# scripts

## gen_module.py — 新建模块骨架

从 `module.v.tmpl` 生成一个空白 SystemVerilog 模块文件。

```
python3 gen_module.py <module_name> [output_dir]
```

| 参数 | 说明 |
|------|------|
| `module_name` | 模块名，同时作为输出文件名（`.sv`） |
| `output_dir` | 可选，默认当前目录 |

```bash
python3 gen_module.py spi_cfg ../../rtl/spi
# → ../../rtl/spi/spi_cfg.sv
```

---

## gen_cfg.py — 从 YAML 生成配置寄存器模块

根据寄存器映射描述文件同时生成三份产物（同目录，同一次运行）：

1. `<module>.sv` — SAB 接口的配置模块
2. `<module>_addr_pkg.sv` — 地址常量包（`ADDR_<REG>` / INT 的 `_EN` / RAM 段 `_ALEN`/`_DLEN`）
3. `<module>_ral.sv` — UVM RAL 模型（uvm_reg 子类 + uvm_mem + `<module>_reg_block`）

```
python3 gen_cfg.py <input.yaml> [output.sv]
```

```bash
python3 gen_cfg.py ../uart/uart_cfg.yaml
# → ../uart/uart_cfg.sv
# → ../uart/uart_cfg_addr_pkg.sv
# → ../uart/uart_cfg_ral.sv
```

yaml 是 single source of truth，三份产物永远同步。

### YAML 格式

```yaml
module: <module_name>
addr_w: <地址位宽>
data_w: <数据位宽>

registers:
  - name: <REG_NAME>        # 大写，用于 localparam 和 case label
    addr: 0x0               # 寄存器地址（十六进制）
    type: RW                # 见下表
    fields:
      - name: <field_name>  # 小写，用于端口名和内部寄存器名
        bits: "2:0"         # 位范围，单 bit 写 "0"
        reset: 0            # 复位值（RO/RC 不需要）
```

**寄存器类型：**

| type | 含义 | 内部寄存器 | 输出端口 | 输入端口 |
|------|------|-----------|---------|---------|
| `RW` | 读写 | 有 | 每个 field | — |
| `WO` | 只写，读回 0 | 有 | 每个 field | — |
| `RO` | 只读 | 无 | — | `src`（整个寄存器） |
| `RC` | 只读，读后清零 | 有（sticky-OR 累积） | — | `src`（整个寄存器） |

RO / RC 需要额外的 `src` 字段指定输入端口名：

```yaml
  - name: STATUS
    addr: 0x5
    type: RC
    src: uart_status        # 生成 input logic [W-1:0] uart_status
    fields:
      - name: status
        bits: "5:0"
```

完整示例见 [`../uart/uart_cfg.yaml`](../uart/uart_cfg.yaml)。
