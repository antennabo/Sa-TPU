SIM           ?= vcs
TEST          ?= sab_smoke_test
SEED          ?= $(shell date +%s)
UVM_VERBOSITY ?= UVM_MEDIUM
WAVES         ?= 0

TB_DIR    := $(abspath ..)
SIMRESULT := $(TB_DIR)/sim_result

# SAB_VIP_HOME must be set via source scripts/setup_env.sh

FILELIST := $(TB_DIR)/filelist_tb.f
INCDIR   := +incdir+$(SAB_VIP_HOME)

UVM_ARGS := +UVM_TESTNAME=$(TEST) \
            +UVM_VERBOSITY=$(UVM_VERBOSITY) \
            +ntb_random_seed=$(SEED) \
            +UVM_NO_RELNOTES

# -----------------------------------------------------------------------
# VCS
# -----------------------------------------------------------------------
VCS_COMPILE := vcs -sverilog -full64 +acc +vpi -lca \
               -ntb_opts uvm-1.2 \
               -timescale=1ns/1ps \
               $(INCDIR) \
               -f $(FILELIST) \
               -o $(SIMRESULT)/simv \
               -l $(SIMRESULT)/compile.log

VCS_SIM := cd $(SIMRESULT) && ./simv $(UVM_ARGS) -l sim.log

VERDI_HOME ?= $(shell echo $$VERDI_HOME)
FSDB_PLI   := -P $(VERDI_HOME)/share/PLI/VCS/LINUX64/novas.tab \
              $(VERDI_HOME)/share/PLI/VCS/LINUX64/pli.a

ifeq ($(WAVES),1)
VCS_COMPILE += -debug_access+all -kdb +define+DUMP_WAVE $(FSDB_PLI)
endif

# -----------------------------------------------------------------------
# Questa
# -----------------------------------------------------------------------
QUESTA_COMPILE := vlog -sv -timescale 1ns/1ps \
                  -L uvm $(INCDIR) -f $(FILELIST) \
                  | tee $(SIMRESULT)/compile.log

QUESTA_SIM := vsim -c tb_top -L uvm $(UVM_ARGS) \
              -do "run -all; quit -f" | tee $(SIMRESULT)/sim.log

ifeq ($(WAVES),1)
QUESTA_SIM := vsim tb_top -L uvm $(UVM_ARGS) \
              -do "log -r /*; run -all; quit -f" | tee $(SIMRESULT)/sim.log
endif

# -----------------------------------------------------------------------
# Xcelium
# -----------------------------------------------------------------------
XRUN_FLAGS := -sv -uvm -timescale 1ns/1ps $(INCDIR) \
              -f $(FILELIST) $(UVM_ARGS) -log $(SIMRESULT)/sim.log

ifeq ($(WAVES),1)
XRUN_FLAGS += -access +r -shm $(SIMRESULT)/waves.shm
endif

# -----------------------------------------------------------------------
# Targets
# -----------------------------------------------------------------------
.PHONY: all run compile sim wave clean help h

h: help
all: run
run: compile sim

# All tool invocations cd into TB_DIR so that filelist relative paths
# (../../rtl/...) resolve consistently, matching sa_tb / ctrl_ws_tb.
compile:
	@mkdir -p $(SIMRESULT)
ifeq ($(SIM),vcs)
	cd $(TB_DIR) && $(VCS_COMPILE)
else ifeq ($(SIM),questa)
	cd $(TB_DIR) && vlib work
	cd $(TB_DIR) && $(QUESTA_COMPILE)
else ifeq ($(SIM),xcelium)
	@echo "[INFO] xcelium: compile step merged into sim"
else
	$(error Unsupported SIM=$(SIM). Choose: vcs | questa | xcelium)
endif

sim:
ifeq ($(SIM),vcs)
	$(VCS_SIM)
else ifeq ($(SIM),questa)
	cd $(TB_DIR) && $(QUESTA_SIM)
else ifeq ($(SIM),xcelium)
	cd $(TB_DIR) && xrun $(XRUN_FLAGS)
endif
	@echo ""
	@grep -E "UVM_ERROR|UVM_FATAL|TEST PASSED|TEST FAILED" $(SIMRESULT)/sim.log || true

wave:
ifeq ($(SIM),vcs)
	@fsdb=$$(ls $(SIMRESULT)/*.fsdb 2>/dev/null | head -1); \
	 [ -z "$$fsdb" ] && echo "[WARN] no .fsdb found" || (verdi -ssf $$fsdb &)
else ifeq ($(SIM),questa)
	@[ -f $(SIMRESULT)/vsim.wlf ] && vsim -view $(SIMRESULT)/vsim.wlf & || echo "[WARN] no vsim.wlf found"
else ifeq ($(SIM),xcelium)
	@[ -f $(SIMRESULT)/waves.shm ] && simvision $(SIMRESULT)/waves.shm & || echo "[WARN] no waves.shm found"
endif

clean:
	rm -rf $(SIMRESULT)
	cd $(TB_DIR) && rm -rf csrc simv.daidir ucli.key work transcript xcelium.d 64 AN.DB DVEfiles

help:
	@echo "Usage: make [TARGET] [VAR=value ...]"
	@echo "Targets: run | compile | sim | wave | clean"
	@echo "  SIM=$(SIM)  TEST=$(TEST)  SEED=$(SEED)"
	@echo "  UVM_VERBOSITY=$(UVM_VERBOSITY)  WAVES=$(WAVES)"