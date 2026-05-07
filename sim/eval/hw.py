from dataclasses import dataclass
from typing import Optional, Tuple

@dataclass(frozen=True)
class HardwareConfig:
    mxu_dim:      Tuple[int, int]  # (8, 8)
    sram_bytes:   int              # 16 * 1024 * 1024
    hbm_bw_gbps:  float           # 900.0
    freq_mhz:     float           # 1.0
    microarch:    Optional[object] = None  # 中期 cycle-accurate 时填