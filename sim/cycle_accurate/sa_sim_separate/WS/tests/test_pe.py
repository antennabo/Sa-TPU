import numpy as np
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pe import PE


def test_init_zeros():
    pe = PE()
    assert pe.weight == 0
    assert pe.act_out == 0
    assert pe.psum_out == 0


def test_load_weight_mac_uses_current_weight():
    # T2: initial weight=0; psum_out = psum_in + 0*act_in = 4+0=4
    pe = PE()
    pe.tick(act_in=3, psum_in=4, weight_in=2, load_enable=True)
    assert pe.weight == 2
    assert pe.act_out == 3
    assert pe.psum_out == 4   # new weight not yet visible for this cycle's MAC


def test_hold_weight_when_load_disabled():
    # T3: load_enable=False → weight ignores weight_in
    pe = PE()
    pe.tick(act_in=3, psum_in=4, weight_in=2, load_enable=True)
    pe.tick(act_in=0, psum_in=0, weight_in=99, load_enable=False)
    assert pe.weight == 2


def test_mac_with_stationary_weight():
    # T4: weight=2, psum_out = 4 + 2*3 = 10, weight unchanged
    pe = PE()
    pe.tick(act_in=3, psum_in=4, weight_in=2, load_enable=True)
    pe.tick(act_in=3, psum_in=4, weight_in=99, load_enable=False)
    assert pe.psum_out == 10
    assert pe.weight == 2


def test_reset_clears_all():
    # T5: reset zeros all registers
    pe = PE()
    pe.tick(act_in=3, psum_in=4, weight_in=2, load_enable=True)
    pe.reset()
    assert pe.weight == 0
    assert pe.act_out == 0
    assert pe.psum_out == 0
