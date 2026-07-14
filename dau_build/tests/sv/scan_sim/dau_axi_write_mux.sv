`default_nettype none

// Test-only behavioral double of the dau-core AXI write mux: same module
// name, parameters, and (flattened) ports. Grants one whole write burst
// at a time, lowest-index requester first, and routes the response back.
module dau_axi_write_mux #(
    parameter int unsigned NUM_INPUTS = 4,
    parameter int unsigned ADDR_WIDTH = 32
) (
    input  wire logic        clk,
    input  wire logic        rst,

    input  wire logic [NUM_INPUTS*ADDR_WIDTH-1:0] s_awaddr,
    input  wire logic [NUM_INPUTS*8-1:0] s_awlen,
    input  wire logic [NUM_INPUTS-1:0] s_awvalid,
    output logic [NUM_INPUTS-1:0] s_awready,
    input  wire logic [NUM_INPUTS*64-1:0] s_wdata,
    input  wire logic [NUM_INPUTS-1:0] s_wlast,
    input  wire logic [NUM_INPUTS-1:0] s_wvalid,
    output logic [NUM_INPUTS-1:0] s_wready,
    output logic [1:0]       s_bresp,
    output logic [NUM_INPUTS-1:0] s_bvalid,
    input  wire logic [NUM_INPUTS-1:0] s_bready,

    output logic [ADDR_WIDTH-1:0] m_axi_awaddr,
    output logic [7:0]       m_axi_awlen,
    output logic [2:0]       m_axi_awsize,
    output logic [1:0]       m_axi_awburst,
    output logic             m_axi_awvalid,
    input  wire logic        m_axi_awready,
    output logic [63:0]      m_axi_wdata,
    output logic [7:0]       m_axi_wstrb,
    output logic             m_axi_wlast,
    output logic             m_axi_wvalid,
    input  wire logic        m_axi_wready,
    input  wire logic [1:0]  m_axi_bresp,
    input  wire logic        m_axi_bvalid,
    output logic             m_axi_bready
);
    localparam logic [1:0] S_IDLE = 2'd0;
    localparam logic [1:0] S_AW = 2'd1;
    localparam logic [1:0] S_W = 2'd2;
    localparam logic [1:0] S_B = 2'd3;

    localparam int unsigned SEL_BITS = (NUM_INPUTS > 1) ? $clog2(NUM_INPUTS) : 1;

    logic [1:0] state;
    logic [SEL_BITS-1:0] sel;

    assign m_axi_awaddr = s_awaddr[sel*ADDR_WIDTH +: ADDR_WIDTH];
    assign m_axi_awlen = s_awlen[sel*8 +: 8];
    assign m_axi_awsize = 3'd3;
    assign m_axi_awburst = 2'b01;
    assign m_axi_awvalid = (state == S_AW) && s_awvalid[sel];
    assign m_axi_wdata = s_wdata[sel*64 +: 64];
    assign m_axi_wstrb = 8'hFF;
    assign m_axi_wlast = s_wlast[sel];
    assign m_axi_wvalid = (state == S_W) && s_wvalid[sel];
    assign m_axi_bready = (state == S_B) && s_bready[sel];

    always_comb begin
        s_awready = '0;
        s_wready = '0;
        s_bvalid = '0;
        s_awready[sel] = (state == S_AW) && m_axi_awready;
        s_wready[sel] = (state == S_W) && m_axi_wready;
        s_bvalid[sel] = (state == S_B) && m_axi_bvalid;
    end

    assign s_bresp = m_axi_bresp;

    always_ff @(posedge clk) begin
        if (rst) begin
            state <= S_IDLE;
            sel <= '0;
        end else begin
            case (state)
                S_IDLE: begin
                    if (|s_awvalid) begin
                        for (int unsigned i = NUM_INPUTS; i > 0; i--) begin
                            if (s_awvalid[i-1]) begin
                                sel <= SEL_BITS'(i - 1);
                            end
                        end
                        state <= S_AW;
                    end
                end
                S_AW: begin
                    if (m_axi_awvalid && m_axi_awready) begin
                        state <= S_W;
                    end
                end
                S_W: begin
                    if (m_axi_wvalid && m_axi_wready && m_axi_wlast) begin
                        state <= S_B;
                    end
                end
                S_B: begin
                    if (m_axi_bvalid && m_axi_bready) begin
                        state <= S_IDLE;
                    end
                end
                default: state <= S_IDLE;
            endcase
        end
    end
endmodule

`default_nettype wire
