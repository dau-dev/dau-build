`default_nettype none

// Simulation wrapper for dau_mm_stream_job: attaches behavioral input/output
// block RAMs (1-cycle read latency, matching axi_bram_ctrl-paired BRAM) and
// exposes backdoor load/read ports so benches can stage batches and inspect
// results without modeling the AXI MM path.
module dau_mm_stream_job_sim (
    input  wire logic        clk,
    input  wire logic        rstn,

    // AXI-Lite register aperture (driven by the bench)
    input  wire logic [15:0] s_axi_awaddr,
    input  wire logic        s_axi_awvalid,
    output logic             s_axi_awready,
    input  wire logic [31:0] s_axi_wdata,
    input  wire logic [3:0]  s_axi_wstrb,
    input  wire logic        s_axi_wvalid,
    output logic             s_axi_wready,
    output logic [1:0]       s_axi_bresp,
    output logic             s_axi_bvalid,
    input  wire logic        s_axi_bready,
    input  wire logic [15:0] s_axi_araddr,
    input  wire logic        s_axi_arvalid,
    output logic             s_axi_arready,
    output logic [31:0]      s_axi_rdata,
    output logic [1:0]       s_axi_rresp,
    output logic             s_axi_rvalid,
    input  wire logic        s_axi_rready,

    // backdoor staging
    input  wire logic        load_en,
    input  wire logic [13:0] load_addr,
    input  wire logic [63:0] load_data,
    input  wire logic [8:0]  peek_addr,
    output logic [63:0]      peek_data
);
    logic [63:0] input_mem [16384];
    logic [63:0] output_mem [512];

    logic [31:0] bram_in_addr;
    logic        bram_in_en;
    logic [63:0] bram_in_dout;
    logic [31:0] bram_out_addr;
    logic        bram_out_en;
    logic [7:0]  bram_out_we;
    logic [63:0] bram_out_din;
    logic [63:0] bram_in_din_unused;
    logic [63:0] bram_out_dout_unused;

    assign peek_data = output_mem[peek_addr];
    assign bram_out_dout_unused = 64'd0;

    always_ff @(posedge clk) begin
        if (load_en) begin
            input_mem[load_addr] <= load_data;
        end
        if (bram_in_en) begin
            bram_in_dout <= input_mem[bram_in_addr[16:3]];
        end
        if (bram_out_en && bram_out_we[0]) begin
            output_mem[bram_out_addr[11:3]] <= bram_out_din;
        end
    end

    dau_mm_stream_job dut (
        .s_axi_aclk(clk),
        .s_axi_aresetn(rstn),
        .s_axi_awaddr(s_axi_awaddr),
        .s_axi_awvalid(s_axi_awvalid),
        .s_axi_awready(s_axi_awready),
        .s_axi_wdata(s_axi_wdata),
        .s_axi_wstrb(s_axi_wstrb),
        .s_axi_wvalid(s_axi_wvalid),
        .s_axi_wready(s_axi_wready),
        .s_axi_bresp(s_axi_bresp),
        .s_axi_bvalid(s_axi_bvalid),
        .s_axi_bready(s_axi_bready),
        .s_axi_araddr(s_axi_araddr),
        .s_axi_arvalid(s_axi_arvalid),
        .s_axi_arready(s_axi_arready),
        .s_axi_rdata(s_axi_rdata),
        .s_axi_rresp(s_axi_rresp),
        .s_axi_rvalid(s_axi_rvalid),
        .s_axi_rready(s_axi_rready),
        .bram_in_addr(bram_in_addr),
        .bram_in_clk(),
        .bram_in_din(bram_in_din_unused),
        .bram_in_dout(bram_in_dout),
        .bram_in_en(bram_in_en),
        .bram_in_rst(),
        .bram_in_we(),
        .bram_out_addr(bram_out_addr),
        .bram_out_clk(),
        .bram_out_din(bram_out_din),
        .bram_out_dout(bram_out_dout_unused),
        .bram_out_en(bram_out_en),
        .bram_out_rst(),
        .bram_out_we(bram_out_we)
    );
endmodule

`default_nettype wire
