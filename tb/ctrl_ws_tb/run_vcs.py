#!/usr/bin/env python3
# 用 VCS 跑 ctrl_ws_tb（controller_ws WS 逐拍对拍）。
#
# 用法（在 tb/ctrl_ws_tb/ 下执行）:
#   python run_vcs.py                # 跑默认 single
#   python run_vcs.py -t <name>      # TESTS 字典里某条 (单 tile: single / 2 N-tile: switch)
#   python run_vcs.py -fsdb          # + 出 cpu_wave.fsdb
#   python run_vcs.py -wave          # + 跑完用 verdi 打开 fsdb
#   python run_vcs.py -clean         # 清理 VCS 产物
#
# 依赖: vcs / verdi 在 PATH; VERDI_HOME 已设置;
#       golden 向量已由对应 pytest 生成（默认 test_ctrl_ws_single_dump / test_ctrl_ws_switch_dump）。

import os
import shutil
import subprocess
import sys

SIMV = "simv"
COMPILE_LOG = "compile.log"
SIM_LOG = "sim.log"
FSDB = "cpu_wave.fsdb"

TESTS = {
    "single": {"ar": 2, "ac": 2, "f": 2, "tnm": 2,
               "txt": "../../build/ctrl_ws_cosim/single.txt",
               "pytest": "test_ctrl_ws_single_dump"},
    "switch": {"ar": 2, "ac": 2, "f": 2, "tnm": 2,
               "txt": "../../build/ctrl_ws_cosim/switch.txt",
               "pytest": "test_ctrl_ws_switch_dump"},
}

CLEAN_TARGETS = [
    SIMV, COMPILE_LOG, SIM_LOG, FSDB,
    "simv.daidir", "csrc", "ucli.key", "vc_hdrs.h", ".vlogansetup.env",
    "novas.conf", "novas.rc", "verdiLog",
]


def clean():
    for t in CLEAN_TARGETS:
        if os.path.isdir(t):
            shutil.rmtree(t, ignore_errors=True)
        elif os.path.exists(t):
            os.remove(t)
    print("[CLEAN] done")


def compile_vcs(test: dict, dump_fsdb: bool) -> int:
    cmd = [
        "vcs", "-sverilog", "-full64",
        "-timescale=1ns/1ps",
        f"+define+CTRL_AR={test['ar']}",
        f"+define+CTRL_AC={test['ac']}",
        f"+define+CTRL_F={test['f']}",
        f"+define+CTRL_TILE_NUM_MAX={test['tnm']}",
        "-f", "filelist_tb.f",
        "-o", SIMV,
        "-l", COMPILE_LOG,
    ]
    if dump_fsdb:
        verdi_home = os.environ.get("VERDI_HOME")
        if not verdi_home:
            print("[ERROR] 需要 VERDI_HOME 环境变量来挂 fsdb PLI")
            return 1
        pli_dir = f"{verdi_home}/share/PLI/VCS/LINUX64"
        cmd += [
            "+define+DUMP_FSDB",
            "-debug_access+all", "-kdb",
            "-P", f"{pli_dir}/novas.tab", f"{pli_dir}/pli.a",
        ]
    print("[COMPILE]", " ".join(cmd))
    return subprocess.run(cmd).returncode


def run_simv(test: dict) -> tuple[int, str]:
    cmd = [f"./{SIMV}", f"+TXT={test['txt']}", "-l", SIM_LOG]
    print("[RUN]", " ".join(cmd))
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, errors="replace")
    sys.stdout.write(r.stdout)
    return r.returncode, r.stdout


def verdict(stdout: str) -> str:
    if "FATAL" in stdout:
        return "FATAL"
    if "FAIL" in stdout:
        return "FAIL"
    if "PASS" in stdout:
        return "PASS"
    return "UNKNOWN"


def parse_args(args):
    t = "single"
    if "-t" in args:
        i = args.index("-t")
        if i + 1 >= len(args):
            print(f"[ERROR] -t 需要参数 ({'|'.join(TESTS)})")
            sys.exit(2)
        t = args[i + 1]
        if t not in TESTS:
            print(f"[ERROR] 未知 -t {t}，可选: {', '.join(TESTS)}")
            sys.exit(2)
    return t


def main() -> int:
    args = sys.argv[1:]
    if "-clean" in args:
        clean()
        return 0

    test_name = parse_args(args)
    test = TESTS[test_name]
    open_wave = "-wave" in args
    dump_fsdb = open_wave or "-fsdb" in args

    if not os.path.exists(test["txt"]):
        print(f"[ERROR] golden 向量不存在: {test['txt']}")
        print(f"        先在仓库根目录运行: pytest -k {test['pytest']}")
        return 1

    print(f"[TEST] {test_name}  AR={test['ar']} AC={test['ac']} F={test['f']} "
          f"TILE_NUM_MAX={test['tnm']}  TXT={test['txt']}")

    if compile_vcs(test, dump_fsdb) != 0:
        print(f"[ERROR] VCS 编译失败，见 {COMPILE_LOG}")
        return 1

    _, stdout = run_simv(test)
    status = verdict(stdout)
    print(f"\n[RESULT] {test_name}: {status}")

    if dump_fsdb and os.path.exists(FSDB):
        print(f"[WAVE] {FSDB}")
        if open_wave:
            print("[WAVE] launching verdi ...")
            subprocess.Popen(["verdi", "-ssf", FSDB],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())