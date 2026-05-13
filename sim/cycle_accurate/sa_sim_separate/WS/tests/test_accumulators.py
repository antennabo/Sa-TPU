import numpy as np
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from accumulators import Accumulators


def test_correct_positions_m3_n2():
    # N=2, M=3: capture positions m = T-(N-1)-j = T-1-j
    # T=1,j=0 → m=0;  T=2,j=0 → m=1, j=1 → m=0;  T=3,j=0 → m=2, j=1 → m=1;  T=4,j=1 → m=2
    N, M = 2, 3
    ac = Accumulators(N)
    ac.start_collect(M)

    # Feed distinct synthetic values so we can verify placement
    for t in range(ac.cycles_needed(M)):
        ac.step(np.array([t * 10, t * 10 + 1], dtype=np.int32))

    buf = ac.finish()
    # T=1,j=0 → m=0: value = 1*10 = 10
    assert buf[0, 0] == 10
    # T=2,j=0 → m=1: value = 2*10 = 20
    assert buf[1, 0] == 20
    # T=2,j=1 → m=0: value = 2*10+1 = 21
    assert buf[0, 1] == 21
    # T=3,j=0 → m=2: value = 30
    assert buf[2, 0] == 30
    # T=3,j=1 → m=1: value = 31
    assert buf[1, 1] == 31
    # T=4,j=1 → m=2: value = 41
    assert buf[2, 1] == 41


def test_cycles_needed():
    ac = Accumulators(4)
    assert ac.cycles_needed(1) == 7
    assert ac.cycles_needed(4) == 10


def test_reset():
    ac = Accumulators(2)
    ac.start_collect(2)
    ac.step(np.array([1, 2], dtype=np.int32))
    ac.reset()
    ac.start_collect(2)
    assert np.all(ac.finish() == 0)
