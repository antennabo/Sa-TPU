import sys
import subprocess
import os
import re

TESTCASE_W = 18
STATUS_W = 6
INT_W = 8
FLOAT_W = 8


def parse_perf_metrics(stdout):
    patterns = {
        "cycle": r"Cycle\s*:\s*(\d+)",
        "mcycle": r"MCYCLE\s*:\s*(\d+)",
        "minstret": r"MINSTRET\s*:\s*(\d+)",
        "cpi": r"CPI\s*:\s*([0-9]+(?:\.[0-9]+)?)",
    }
    metrics = {}

    for key, pattern in patterns.items():
        match = re.search(pattern, stdout)
        if match:
            metrics[key] = float(match.group(1)) if key == "cpi" else int(match.group(1))

    mcycle = metrics.get("mcycle")
    minstret = metrics.get("minstret")
    if mcycle and minstret:
        metrics["ipc"] = minstret / mcycle
        metrics["stall_cycles"] = mcycle - minstret
        metrics["stall_per_inst"] = (mcycle - minstret) / minstret

    return metrics


def format_perf_metrics(metrics):
    parts = []

    if "cycle" in metrics:
        parts.append(f"cycle={metrics['cycle']}")
    if "mcycle" in metrics:
        parts.append(f"mcycle={metrics['mcycle']}")
    if "minstret" in metrics:
        parts.append(f"minstret={metrics['minstret']}")
    if "cpi" in metrics:
        parts.append(f"cpi={metrics['cpi']:.4f}")
    if "ipc" in metrics:
        parts.append(f"ipc={metrics['ipc']:.4f}")
    if "stall_cycles" in metrics:
        parts.append(f"stall_cycles={metrics['stall_cycles']}")
    if "stall_per_inst" in metrics:
        parts.append(f"stall_per_inst={metrics['stall_per_inst']:.4f}")

    return " | ".join(parts)


def format_result_line(testcase, status, metrics):
    def fmt_int(key):
        value = metrics.get(key)
        return f"{value:>{INT_W}d}" if value is not None else f"{'-':>{INT_W}}"

    def fmt_float(key):
        value = metrics.get(key)
        return f"{value:>{FLOAT_W}.4f}" if value is not None else f"{'-':>{FLOAT_W}}"

    columns = [
        f"{testcase:<{TESTCASE_W}}",
        f"{status:<{STATUS_W}}",
        fmt_int("mcycle"),
        fmt_int("minstret"),
        fmt_float("cpi"),
        fmt_float("ipc"),
        fmt_int("stall_cycles"),
        fmt_float("stall_per_inst"),
    ]
    return " | ".join(columns)


def format_result_header():
    columns = [
        f"{'testcase':<{TESTCASE_W}}",
        f"{'status':<{STATUS_W}}",
        f"{'mcycle':>{INT_W}}",
        f"{'minstret':>{INT_W}}",
        f"{'cpi':>{FLOAT_W}}",
        f"{'ipc':>{FLOAT_W}}",
        f"{'stall':>{INT_W}}",
        f"{'stall/ins':>{FLOAT_W}}",
    ]
    header = " | ".join(columns)
    return header, "-" * len(header)

def run_testcase(testcase, open_waveform, log_file=None, silent=False):
    if not silent:
        print(f"Running test case: {testcase}")
    
    vcd_file = "cpu_wave.vcd"
    try:
        os.remove(vcd_file)
    except FileNotFoundError:
        pass
    
    # if testcase.startswith("rv32um"):
    #     isa_dir = "rv32um"
    # elif testcase.startswith("rv32ui"):
    #     isa_dir = "rv32ui"
    # elif testcase.startswith("rv32cos"):
    #     isa_dir = "rv32cos"   # custom
    # else:
    #     raise ValueError(f"Unknown testcase: {testcase}")
    isa_dir = testcase.split('-')[0]

    iverilog_cmd = [
        "iverilog",
        f"-DTESTCASE=\"{testcase}\"",
        f"-DISA_DIR=\"{isa_dir}\"",
        "-v",
        "-DDEBUG",
        "-g2012",
        "-o", "cpu_sim",
        "-f", "filelist_tb.f"
    ]
    if open_waveform:
        iverilog_cmd.append("-DDUMP_VCD")
    result = subprocess.run(iverilog_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    with open("cmp.log", "w") as cmp_log:
        cmp_log.write(f"\n===== {testcase} - IVERILOG OUTPUT =====\n")
        cmp_log.write(result.stdout)
        cmp_log.write(result.stderr)

    if result.returncode != 0:
        status = "FAIL"
        perf_metrics = {}
    else:
        vvp_cmd = ["vvp", "cpu_sim"]
        result = subprocess.run(vvp_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace')
        perf_metrics = parse_perf_metrics(result.stdout)
        
        if "PASS" in result.stdout:
            status = "PASS"
        else:
            status = "FAIL"
        
        if result.returncode != 0:
            status = "FAIL"
        
        if open_waveform:
            subprocess.Popen(["gtkwave", "cpu_wave.vcd"])
    
    if log_file:
        log_file.write(f"{format_result_line(testcase, status, perf_metrics)}\n")
    
    if not silent:
        print(f"{testcase}: {status}\n")
        for line in result.stdout.strip().split('\n'):
            print(line)
        for line in result.stderr.strip().split('\n'):
            print(line)

        perf_summary = format_perf_metrics(perf_metrics)
        if perf_summary:
            print(f"[PERF] {format_result_line(testcase, status, perf_metrics)}")

    return {
        "testcase": testcase,
        "status": status,
        "perf_metrics": perf_metrics,
    }

def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <testcase> or {sys.argv[0]} -f [-vcd]")
        sys.exit(1)
    
    open_waveform = "-vcd" in sys.argv
    testcases = []
    log_file = False
    silent_mode = False
    results = []
    
    if "-f" in sys.argv:
        testcase_file = "testcase.f"
        if not os.path.exists(testcase_file):
            print(f"Error: {testcase_file} not found.")
            sys.exit(1)
        
        with open(testcase_file, "r") as f:
            # strip inline '#' comments and blank lines
            testcases = [line.split("#", 1)[0].strip() for line in f]
            testcases = [t for t in testcases if t]
        
        if not testcases:
            print("Error: No test cases found in testcase.f")
            sys.exit(1)
        
        log_file = open("sim_result.log", "w")
        header, separator = format_result_header()
        log_file.write(f"{header}\n{separator}\n")
        silent_mode = True
    else:
        testcases = [arg for arg in sys.argv[1:] if arg != "-vcd"]
    
    for testcase in testcases:
        results.append(run_testcase(testcase, open_waveform, log_file, silent_mode))
    
    if log_file:
        log_file.close()
        print("Results logged in sim_result.log")

        pass_results = [r for r in results if r["status"] == "PASS" and r["perf_metrics"]]
        if pass_results:
            avg_cpi = sum(r["perf_metrics"].get("cpi", 0.0) for r in pass_results) / len(pass_results)
            avg_ipc = sum(r["perf_metrics"].get("ipc", 0.0) for r in pass_results) / len(pass_results)
            max_cpi_case = max(pass_results, key=lambda r: r["perf_metrics"].get("cpi", float("-inf")))
            min_cpi_case = min(pass_results, key=lambda r: r["perf_metrics"].get("cpi", float("inf")))

            print(f"[PERF] avg_cpi={avg_cpi:.4f} avg_ipc={avg_ipc:.4f}")
            print(
                f"[PERF] best_cpi={min_cpi_case['testcase']}:{min_cpi_case['perf_metrics'].get('cpi', 0.0):.4f} "
                f"worst_cpi={max_cpi_case['testcase']}:{max_cpi_case['perf_metrics'].get('cpi', 0.0):.4f}"
            )
    
if __name__ == "__main__":
    main()
