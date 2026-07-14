`default_nettype none

// Test-only behavioral double of the dau-core backdoor AXI RAM: same
// module name, parameters, and ports so generated scan-composition sim
// harnesses can be benched inside dau-build without shipping dau-core
// HDL. Single outstanding read burst and write burst; READ_LATENCY is
// accepted for signature parity only.
module dau_axi_ram_sim #(
    parameter int unsigned ADDR_WIDTH = 32,
    parameter int unsigned MEM_WORDS = 65536,
    parameter int unsigned READ_LATENCY = 4
) (
    input  wire logic        clk,
    input  wire logic        rst,

    input  wire logic [ADDR_WIDTH-1:0] s_axi_araddr,
    input  wire logic [7:0]  s_axi_arlen,
    input  wire logic [2:0]  s_axi_arsize,
    input  wire logic [1:0]  s_axi_arburst,
    input  wire logic        s_axi_arvalid,
    output logic             s_axi_arready,
    output logic [63:0]      s_axi_rdata,
    output logic [1:0]       s_axi_rresp,
    output logic             s_axi_rlast,
    output logic             s_axi_rvalid,
    input  wire logic        s_axi_rready,
    input  wire logic [ADDR_WIDTH-1:0] s_axi_awaddr,
    input  wire logic [7:0]  s_axi_awlen,
    input  wire logic [2:0]  s_axi_awsize,
    input  wire logic [1:0]  s_axi_awburst,
    input  wire logic        s_axi_awvalid,
    output logic             s_axi_awready,
    input  wire logic [63:0] s_axi_wdata,
    input  wire logic [7:0]  s_axi_wstrb,
    input  wire logic        s_axi_wlast,
    input  wire logic        s_axi_wvalid,
    output logic             s_axi_wready,
    output logic [1:0]       s_axi_bresp,
    output logic             s_axi_bvalid,
    input  wire logic        s_axi_bready,

    input  wire logic        bd_write,
    input  wire logic [31:0] bd_index,
    input  wire logic [63:0] bd_wdata,
    output logic [63:0]      bd_rdata
);
    localparam int unsigned WORD_BITS = $clog2(MEM_WORDS);

    logic [63:0] mem [0:MEM_WORDS-1];

    logic [WORD_BITS-1:0] read_word;
    logic [8:0]           read_left;
    logic                 read_busy;

    assign s_axi_arready = !read_busy;
    assign s_axi_rresp = 2'b00;

    always_ff @(posedge clk) begin
        if (rst) begin
            read_busy <= 1'b0;
            s_axi_rvalid <= 1'b0;
            s_axi_rlast <= 1'b0;
        end else begin
            if (s_axi_rvalid && s_axi_rready) begin
                s_axi_rvalid <= 1'b0;
                if (s_axi_rlast) begin
                    read_busy <= 1'b0;
                end
            end
            if (read_busy && read_left != 9'd0 && (!s_axi_rvalid || (s_axi_rready && !s_axi_rlast))) begin
                s_axi_rdata <= mem[read_word];
                s_axi_rvalid <= 1'b1;
                s_axi_rlast <= (read_left == 9'd1);
                read_word <= read_word + 1'b1;
                read_left <= read_left - 9'd1;
            end
            if (s_axi_arvalid && s_axi_arready) begin
                read_busy <= 1'b1;
                read_word <= s_axi_araddr[WORD_BITS+2:3];
                read_left <= {1'b0, s_axi_arlen} + 9'd1;
            end
        end
    end

    logic [WORD_BITS-1:0] write_word;
    logic                 write_busy;

    assign s_axi_awready = !write_busy && !s_axi_bvalid;
    assign s_axi_wready = write_busy;
    assign s_axi_bresp = 2'b00;

    always_ff @(posedge clk) begin
        if (rst) begin
            write_busy <= 1'b0;
            s_axi_bvalid <= 1'b0;
        end else begin
            if (s_axi_bvalid && s_axi_bready) begin
                s_axi_bvalid <= 1'b0;
            end
            if (s_axi_awvalid && s_axi_awready) begin
                write_busy <= 1'b1;
                write_word <= s_axi_awaddr[WORD_BITS+2:3];
            end
            if (s_axi_wvalid && s_axi_wready) begin
                write_word <= write_word + 1'b1;
                if (s_axi_wlast) begin
                    write_busy <= 1'b0;
                    s_axi_bvalid <= 1'b1;
                end
            end
        end
    end

    // memory writes live in one process (AXI write beats + the backdoor)
    always_ff @(posedge clk) begin
        if (s_axi_wvalid && s_axi_wready) begin
            mem[write_word] <= s_axi_wdata;
        end
        if (bd_write) begin
            mem[bd_index[WORD_BITS-1:0]] <= bd_wdata;
        end
    end

    assign bd_rdata = mem[bd_index[WORD_BITS-1:0]];
endmodule

`default_nettype wire
