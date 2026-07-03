#!/usr/bin/env bash
# Source this script to configure the simulation environment.
#   source setup_env.sh
#
# Variables set:
#   MMIO_VIP_HOME  - root of the mmio_vip directory
#   RTL_LIB_HOME   - root of the RTL library repo (sibling of uvm-platform)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

export VIP_LIB_HOME="${REPO_ROOT}"
# export MMIO_VIP_HOME="${VIP_LIB_HOME}/mmio_vip"
# export AHB_VIP_HOME="${VIP_LIB_HOME}/ahb_vip"
# export UART_VIP_HOME="${VIP_LIB_HOME}/uart_vip"
export SAB_VIP_HOME="${VIP_LIB_HOME}/satpu_top_tb/sab_vip"
# export RTL_LIB_HOME="$(cd "${REPO_ROOT}/.." && pwd)/rtl_lib"

# echo "[env] VIP_LIB_HOME  = ${VIP_LIB_HOME}"
# echo "[env] MMIO_VIP_HOME = ${MMIO_VIP_HOME}"
# echo "[env] AHB_VIP_HOME  = ${AHB_VIP_HOME}"
# echo "[env] UART_VIP_HOME = ${UART_VIP_HOME}"
echo "[env] SAB_VIP_HOME  = ${SAB_VIP_HOME}"
# echo "[env] RTL_LIB_HOME  = ${RTL_LIB_HOME}"
