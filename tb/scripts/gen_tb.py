#!/usr/bin/env python3
"""
Generate a testbench for an existing VIP.

Usage:
    python3 scripts/gen_tb.py <vip_name> [addr_w] [data_w] [--name <tb_dir>]

Example:
    python3 scripts/gen_tb.py sab 15 32 --name satpu_top_tb

Output:
    <repo_root>/<tb_dir>/   (default tb_dir = <vip_name>_tb)

Note:
    VIP is expected at <repo_root>/<vip_name>_vip/. Run gen_vip.py first if missing.
"""

import sys
import argparse
from pathlib import Path

def substitute(text: str, vip_name: str, vip_upper: str, addr_w: str, data_w: str) -> str:
    text = text.replace("{{VIP_NAME_UPPER}}", vip_upper)
    text = text.replace("{{VIP_NAME}}",       vip_name)
    text = text.replace("{{ADDR_W_DEFAULT}}", addr_w)
    text = text.replace("{{DATA_W_DEFAULT}}", data_w)
    return text

def main():
    parser = argparse.ArgumentParser(
        description="Generate a testbench for an existing VIP.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python3 scripts/gen_tb.py sab 15 32 --name satpu_top_tb\n"
            "  python3 scripts/gen_tb.py axi 64 128\n"
            "\nnote:\n"
            "  VIP expected at <repo_root>/<vip>_vip/. Fill DUT/stub in tb_top.sv after.\n"
        )
    )
    parser.add_argument("vip_name",          help="VIP name (e.g. sab, axi)")
    parser.add_argument("addr_w", nargs="?", help="address width in bits (default: 32)", default="32")
    parser.add_argument("data_w", nargs="?", help="data width in bits (default: 32)",    default="32")
    parser.add_argument("--name",            help="output tb directory name (default: <vip_name>_tb)", default="")
    args = parser.parse_args()

    vip_name  = args.vip_name.lower()
    addr_w    = args.addr_w
    data_w    = args.data_w
    vip_upper = vip_name.upper()
    tb_name   = args.name if args.name else f"{vip_name}_tb"

    script_dir   = Path(__file__).parent.resolve()
    repo_root    = script_dir.parent
    template_dir = script_dir / "template" / "tb"
    output_dir   = repo_root / tb_name
    vip_dir      = repo_root / f"{vip_name}_vip"

    print(f"[gen_tb] VIP_NAME = {vip_name}")
    print(f"[gen_tb] ADDR_W   = {addr_w}")
    print(f"[gen_tb] DATA_W   = {data_w}")
    print(f"[gen_tb] output   = {output_dir}")

    if not vip_dir.exists():
        print(f"[WARN] VIP not found at {vip_dir}. Run gen_vip.py first.")

    if output_dir.exists() and any(output_dir.iterdir()):
        print(f"[ERROR] {output_dir} already exists and is non-empty. Remove it first.")
        sys.exit(1)

    for src in template_dir.rglob("*"):
        if not src.is_file():
            continue
        rel      = src.relative_to(template_dir)
        dest_rel = Path(substitute(str(rel), vip_name, vip_upper, addr_w, data_w))
        dest     = output_dir / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(substitute(src.read_text(), vip_name, vip_upper, addr_w, data_w))

    print(f"[gen_tb] Testbench files generated in {output_dir}")
    print(f"[gen_tb] Remember to fill in the DUT/stub in {output_dir}/tb_top.sv")
    print("[gen_tb] Done.")

if __name__ == "__main__":
    main()
