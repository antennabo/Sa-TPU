#!/usr/bin/env python3
"""
Generate a new VIP from template.

Usage:
    python3 scripts/gen_vip.py <vip_name> [addr_w] [data_w]

Example:
    python3 scripts/gen_vip.py apb 32 32

Output:
    ${VIP_LIB_HOME}/<vip_name>_vip/   (created from scripts/template/vip/)
    scripts/setup_env.sh              (updated with <VIP_NAME_UPPER>_VIP_HOME)
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

def update_setup_env(setup_env: Path, vip_name: str, vip_upper: str) -> None:
    var = f"{vip_upper}_VIP_HOME"
    content = setup_env.read_text()
    if f"export {var}" in content:
        print(f"[gen_vip] {var} already in setup_env.sh — skipped")
        return

    export_line = f'export {var}="${{VIP_LIB_HOME}}/{vip_name}_vip"\n'
    echo_line   = f'echo "[env] {var} = ${{{var}}}"\n'

    content = content.replace(
        "export RTL_LIB_HOME",
        export_line + "export RTL_LIB_HOME"
    )
    content = content.replace(
        'echo "[env] RTL_LIB_HOME',
        echo_line + 'echo "[env] RTL_LIB_HOME'
    )
    setup_env.write_text(content)
    print(f"[gen_vip] Added {var} to setup_env.sh")

def main():
    parser = argparse.ArgumentParser(
        description="Generate a new VIP from template.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python3 scripts/gen_vip.py apb\n"
            "  python3 scripts/gen_vip.py axi 64 128\n"
        )
    )
    parser.add_argument("vip_name",          help="VIP name (e.g. apb, axi)")
    parser.add_argument("addr_w", nargs="?", help="address width in bits (default: 32)", default="32")
    parser.add_argument("data_w", nargs="?", help="data width in bits (default: 32)",    default="32")
    args = parser.parse_args()

    vip_name  = args.vip_name.lower()
    addr_w    = args.addr_w
    data_w    = args.data_w
    vip_upper = vip_name.upper()

    script_dir   = Path(__file__).parent.resolve()
    repo_root    = script_dir.parent
    vip_lib      = repo_root.parent / "vip_lib"
    template_dir = script_dir / "template" / "vip"
    output_dir   = vip_lib / f"{vip_name}_vip"

    print(f"[gen_vip] VIP_NAME = {vip_name}")
    print(f"[gen_vip] ADDR_W   = {addr_w}")
    print(f"[gen_vip] DATA_W   = {data_w}")
    print(f"[gen_vip] output   = {output_dir}")

    if output_dir.exists():
        print(f"[ERROR] {output_dir} already exists. Remove it first.")
        sys.exit(1)

    for src in template_dir.rglob("*"):
        if not src.is_file():
            continue
        rel      = src.relative_to(template_dir)
        dest_rel = Path(substitute(str(rel), vip_name, vip_upper, addr_w, data_w))
        dest     = output_dir / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(substitute(src.read_text(), vip_name, vip_upper, addr_w, data_w))

    print(f"[gen_vip] VIP files generated in {output_dir}")

    update_setup_env(script_dir / "setup_env.sh", vip_name, vip_upper)

    print("[gen_vip] Done. Re-source scripts/setup_env.sh before compiling.")

if __name__ == "__main__":
    main()
