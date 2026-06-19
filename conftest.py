"""仓库根 conftest：把仓库根加入 sys.path，
使顶层包 (compiler / simulator / common / model / scripts) 可被测试直接 import。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
