import logging
from .module import module

logger = logging.getLogger(__name__)


class Accumulator(module):
    """
    Tile accumulator backed by a plain 3-D array: _mem[tile_id][row][col].

    load_write(tile_id, data_list, mask)  -- write; mask[i]=1 → _mem[tile_id][i] = data_list[i]
    load_read(tile_id, mask)              -- read;  mask[i]=1 → state[i] = _mem[tile_id][i]
    compute() / commit()                  -- no-op (no pipeline)
    get_tile(tile_id)                     -- return _mem[tile_id]
    """

    NUM_TILES = 16

    def __init__(self, num_rows: int, num_cols: int):
        super().__init__()
        self.num_rows = num_rows
        self.num_cols = num_cols
        # _mem[tile_id][row][col]
        self._mem  = [[[None] * num_cols for _ in range(num_rows)] for _ in range(self.NUM_TILES)]
        self.state = [None] * num_rows

    def load_write(self, tile_id: int, row_list: list, data_list: list):
        """按列写入。row_list[j] 指定第 j 列写入的行号，data_list[j] 为 None 则跳过。"""
        for col, (row, data) in enumerate(zip(row_list, data_list)):
            if data is None:
                continue
            self._mem[tile_id][row][col] = data
            logger.debug("[accum  ] write tile=%d row=%d col=%d data=%s", tile_id, row, col, data)

    def load_read(self, tile_id: int, mask: list):
        self.state = [
            self._mem[tile_id][i] if mask[i] else None
            for i in range(self.num_lanes)
        ]

    def compute(self):
        pass

    def commit(self):
        pass

    def get_tile(self, tile_id: int) -> list:
        """Return _mem[tile_id]: list of num_lanes row vectors."""
        return self._mem[tile_id]

    def reset(self):
        self._mem  = [[[None] * self.num_cols for _ in range(self.num_rows)] for _ in range(self.NUM_TILES)]
        self.state = [None] * self.num_rows
