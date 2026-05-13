import numpy as np
from systolic_array import SystolicArray
from weight_fifo import WeightFIFO
from data_setup import DataSetup
from accumulators import Accumulators
from tiling import Tiler


def matmul(A, W, N=4, trace=False):
    M   = A.shape[0]
    N_w = W.shape[1]

    sa    = SystolicArray(N)
    wf    = WeightFIFO(N)
    ds    = DataSetup(N)
    ac    = Accumulators(N)
    tiler = Tiler(N)
    C     = tiler.alloc_C(M, N_w)
    
    z8  = np.zeros(N, dtype=np.int8)

    for W_tile, k_blk, j_blk in tiler.weight_tiles(W):
        A_chunk = tiler.act_chunk(A, k_blk)

        sa.reset(); wf.reset(); ds.reset(); ac.reset()

        wf.push_tile(W_tile)
        ds.load_chunk(A_chunk)
        ac.start_collect(M)

        for _ in range(N):
            w_top, load_en = wf.step()
            sa.tick(weight_in_top=w_top, load_enable=load_en,
                    act_in_left=z8, trace=trace)

        for _ in range(ac.cycles_needed(M)):
            w_top, load_en = wf.step()
            act_left = ds.step()
            out = sa.tick(weight_in_top=w_top, load_enable=load_en,
                          act_in_left=act_left, trace=trace)
            ac.step(out['psum_out_bottom'])

        tiler.accumulate(C, ac.finish(), j_blk)

    return C
