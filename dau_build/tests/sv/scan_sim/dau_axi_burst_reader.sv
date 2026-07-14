`default_nettype none

// Test-only behavioral double of the dau-core AXI burst reader: same
// module name, parameters, and ports. Fetches one 64-bit word per AXI
// read burst (arlen = 0) and streams it out — slow but sufficient to
// bench generated scan-composition harness wiring.
module dau_axi_burst_reader #(
    parameter int unsigned ADDR_WIDTH = 32,
    parameter int unsigned BURST_BEATS = 16,
    parameter int unsigned LENGTH_ALIGN_BITS = 3
) (
    input  wire logic        clk,
    input  wire logic        rst,

    input  wire logic        start,
    input  wire logic [ADDR_WIDTH-1:0] read_address,
    input  wire logic [31:0] read_length_bytes,
    output logic             busy,
    output logic             done,
    output logic             error,
    output logic [7:0]       error_code,

    output logic [ADDR_WIDTH-1:0] m_axi_araddr,
    output logic [7:0]       m_axi_arlen,
    output logic [2:0]       m_axi_arsize,
    output logic [1:0]       m_axi_arburst,
    output logic             m_axi_arvalid,
    input  wire logic        m_axi_arready,
    input  wire logic [63:0] m_axi_rdata,
    input  wire logic [1:0]  m_axi_rresp,
    input  wire logic        m_axi_rlast,
    input  wire logic        m_axi_rvalid,
    output logic             m_axi_rready,

    output logic             stream_valid,
    input  wire logic        stream_ready,
    output logic [63:0]      stream_data,
    output logic             stream_last,

    output logic [63:0]      dbg_first_stream_word,
    output logic [31:0]      dbg_first_araddr,
    output logic [31:0]      dbg_beats_while_idle,
    output logic [31:0]      dbg_final_fifo_count
);
    localparam logic [1:0] S_IDLE = 2'd0;
    localparam logic [1:0] S_ADDR = 2'd1;
    localparam logic [1:0] S_DATA = 2'd2;
    localparam logic [1:0] S_EMIT = 2'd3;

    logic [1:0] state;
    logic [ADDR_WIDTH-1:0] beat_address;
    logic [31:0] words_left;
    logic [63:0] beat;

    assign m_axi_araddr = beat_address;
    assign m_axi_arlen = 8'd0;
    assign m_axi_arsize = 3'd3;
    assign m_axi_arburst = 2'b01;
    assign m_axi_arvalid = (state == S_ADDR);
    assign m_axi_rready = (state == S_DATA);

    assign stream_valid = (state == S_EMIT);
    assign stream_data = beat;
    assign stream_last = (words_left == 32'd1);

    assign dbg_first_stream_word = 64'd0;
    assign dbg_first_araddr = 32'd0;
    assign dbg_beats_while_idle = 32'd0;
    assign dbg_final_fifo_count = 32'd0;

    always_ff @(posedge clk) begin
        if (rst) begin
            state <= S_IDLE;
            busy <= 1'b0;
            done <= 1'b0;
            error <= 1'b0;
            error_code <= 8'd0;
        end else begin
            case (state)
                S_IDLE: begin
                    if (start) begin
                        busy <= 1'b1;
                        done <= 1'b0;
                        error <= 1'b0;
                        error_code <= 8'd0;
                        beat_address <= read_address;
                        words_left <= read_length_bytes >> 3;
                        state <= S_ADDR;
                    end
                end
                S_ADDR: begin
                    if (m_axi_arready) begin
                        state <= S_DATA;
                    end
                end
                S_DATA: begin
                    if (m_axi_rvalid) begin
                        if (m_axi_rresp != 2'b00) begin
                            error <= 1'b1;
                            error_code <= 8'h02;
                            done <= 1'b1;
                            busy <= 1'b0;
                            state <= S_IDLE;
                        end else begin
                            beat <= m_axi_rdata;
                            state <= S_EMIT;
                        end
                    end
                end
                S_EMIT: begin
                    if (stream_ready) begin
                        beat_address <= beat_address + 8;
                        words_left <= words_left - 32'd1;
                        if (words_left == 32'd1) begin
                            done <= 1'b1;
                            busy <= 1'b0;
                            state <= S_IDLE;
                        end else begin
                            state <= S_ADDR;
                        end
                    end
                end
                default: state <= S_IDLE;
            endcase
        end
    end
endmodule

`default_nettype wire
