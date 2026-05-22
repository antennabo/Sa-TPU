from .sram import SRAM
from .module import module


class Accumulator(module):
    """
    Lane-based Accumulator: M (row mode) or N (col mode) independent SRAM instances.

    Row mode  : srams[i] holds row i across num_tiles tiles; depth=num_tiles, data=row vector
    Col mode  : srams[j] holds col j across num_tiles tiles; depth=num_tiles, data=col vector

    Per-cycle usage:
      load_write(tile_id, data_list, mask)  -- schedule write; mask[i]=1 activates srams[i]
      load_read(tile_id, mask)              -- schedule read;  mask[i]=1 activates srams[i]
      compute()                             -- dispatch to all SRAMs → next_state
      commit()                              -- all SRAMs commit → state
      state                                 -- list[num_lanes]: per-lane read result (None if not read)
    """

    NUM_TILES = 16

    def __init__(self, num_lanes: int):
        """
        num_lanes: M for row mode, N for col mode.
        """
        super().__init__()
        self.num_lanes = num_lanes
        self._srams    = [SRAM(depth=self.NUM_TILES) for _ in range(num_lanes)]

        self._cmd_write = None  # (tile_id, data_list, mask)
        self._cmd_read  = None  # (tile_id, mask)
        self.state      = [None] * num_lanes
        self.next_state = None

    def load_write(self, tile_id: int, data_list: list, mask: list):
        """Schedule a write. mask[i]=1 → srams[i].write(tile_id, data_list[i])."""
        self._cmd_write = (tile_id, data_list, mask)

    def load_read(self, tile_id: int, mask: list):
        """Schedule a read. mask[i]=1 → srams[i].request_read(tile_id)."""
        self._cmd_read = (tile_id, mask)

    def compute(self):
        if self._cmd_write is not None:
            tile_id, data_list, mask = self._cmd_write
            for i, active in enumerate(mask):
                if active:
                    self._srams[i].write(tile_id, data_list[i])
                    print(f"  [accum] write tile={tile_id} lane={i} data={data_list[i]}")
            self._cmd_write = None

        if self._cmd_read is not None:
            tile_id, mask = self._cmd_read
            for i, active in enumerate(mask):
                if active:
                    self._srams[i].request_read(tile_id)
            self._cmd_read = None

        for sram in self._srams:
            sram.compute()
        self.next_state = [sram.next_state for sram in self._srams]

    def commit(self):
        for sram in self._srams:
            sram.commit()
        self.state      = [sram.state for sram in self._srams]
        self.next_state = None

    def get_tile(self, tile_id: int) -> list:
        """Debug: read tile directly from SRAM memory (bypasses cycle-accurate read path)."""
        return [sram._mem[tile_id] for sram in self._srams]

    def reset(self):
        for sram in self._srams:
            sram.reset()
        self._cmd_write = None
        self._cmd_read  = None
        self.state      = [None] * self.num_lanes
        self.next_state = None
