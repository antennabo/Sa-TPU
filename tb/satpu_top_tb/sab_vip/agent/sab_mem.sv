`ifndef SAB_MEM_SV
`define SAB_MEM_SV

class sab_mem #(int ADDR_W=32, int DATA_W=32) extends uvm_component;
    `uvm_component_param_utils(sab_mem#(ADDR_W, DATA_W))

    virtual sab_if#(ADDR_W, DATA_W)                    u_vif;
    uvm_analysis_port#(sab_seq_item#(ADDR_W, DATA_W))  ap;

    // word-addressed associative array; default read = ~addr
    logic [DATA_W-1:0] mem [logic [ADDR_W-1:0]];

    // number of word entries pre-initialized at start_of_simulation
    int unsigned init_depth = 256;

    function new (string name = "sab_mem", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    extern function void build_phase              (uvm_phase phase);
    extern function void start_of_simulation_phase(uvm_phase phase);
    extern task          run_phase               (uvm_phase phase);
    extern protected virtual task respond();
endclass : sab_mem

function void sab_mem::build_phase(uvm_phase phase);
    super.build_phase(phase);
    if (!uvm_config_db#(virtual sab_if#(ADDR_W, DATA_W))::get(this, "", "u_vif", u_vif))
        `uvm_fatal("NO_VIF", "virtual interface not found")
    ap = new("ap", this);
endfunction : build_phase

// Pre-populate mem[word_addr] = ~(word_addr << 2)  (byte address of the word)
function void sab_mem::start_of_simulation_phase(uvm_phase phase);
    for (int unsigned i = 0; i < init_depth; i++) begin
        logic [ADDR_W-1:0] byte_addr = ADDR_W'(i) << 2;
        mem[byte_addr] = ~byte_addr;
    end
endfunction : start_of_simulation_phase

task sab_mem::run_phase(uvm_phase phase);
    u_vif.sab_req_ready  <= 1'b0;
    u_vif.sab_resp_valid <= 1'b0;
    u_vif.sab_resp_rdata <= '0;
    u_vif.sab_resp_err   <= 1'b0;
    @(posedge u_vif.rst_n);
    u_vif.sab_req_ready <= 1'b1;
    forever respond();
endtask : run_phase

task sab_mem::respond();
    sab_seq_item#(ADDR_W, DATA_W) item;

    // req_ready is 1 always; wait for master to assert req_valid
    @(posedge u_vif.clk iff u_vif.sab_req_valid);

    item       = sab_seq_item#(ADDR_W, DATA_W)::type_id::create("item");
    item.addr  = u_vif.sab_req_addr;
    item.rw    = u_vif.sab_req_wen;

    if (u_vif.sab_req_wen) begin
        mem[u_vif.sab_req_addr]  = u_vif.sab_req_wdata;
        item.wdata               = u_vif.sab_req_wdata;
        `uvm_info(get_type_name(), $sformatf("[MEM] WR addr=0x%0h wdata=0x%0h",
            u_vif.sab_req_addr, u_vif.sab_req_wdata), UVM_MEDIUM)
    end else begin
        // uninitialized addresses return ~addr as default
        item.rdata          = mem.exists(u_vif.sab_req_addr) ? mem[u_vif.sab_req_addr]
                                                              : ~u_vif.sab_req_addr;
        u_vif.sab_resp_rdata <= item.rdata;
        `uvm_info(get_type_name(), $sformatf("[MEM] RD addr=0x%0h rdata=0x%0h",
            u_vif.sab_req_addr, item.rdata), UVM_MEDIUM)
    end

    u_vif.sab_resp_valid <= 1'b1;
    u_vif.sab_resp_err   <= 1'b0;
    @(posedge u_vif.clk);
    u_vif.sab_resp_valid <= 1'b0;
    u_vif.sab_resp_rdata <= '0;
    ap.write(item);
endtask : respond

`endif // SAB_MEM_SV