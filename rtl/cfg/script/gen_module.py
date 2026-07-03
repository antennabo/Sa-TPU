#!/usr/bin/env python3
"""
Generate a Verilog module from module.v.tmpl.

Usage:
    python3 gen_module.py <module_name> [output_dir]

Example:
    python3 gen_module.py pipe_reg ../../rtl/core
    python3 gen_module.py id_ex_reg
"""

import sys
import os
from datetime import date

TMPL_PATH = os.path.join(os.path.dirname(__file__), "module.v.tmpl")

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    module_name = sys.argv[1]
    out_dir     = sys.argv[2] if len(sys.argv) > 2 else "."

    with open(TMPL_PATH) as f:
        content = f.read()

    content = content.replace("{{MODULE_NAME}}", module_name)
    content = content.replace("{{DATE}}",        str(date.today()))

    out_path = os.path.join(out_dir, f"{module_name}.sv")
    if os.path.exists(out_path):
        print(f"Error: {out_path} already exists. Aborting.")
        sys.exit(1)

    with open(out_path, "w") as f:
        f.write(content)

    print(f"Created: {out_path}")

if __name__ == "__main__":
    main()
