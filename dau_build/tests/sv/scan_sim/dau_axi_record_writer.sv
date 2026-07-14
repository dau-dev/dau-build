`default_nettype none

// Test-only behavioral double of the dau-core AXI record writer: same
// module name, parameters, and ports. Lands each record beat as a
// single-beat AXI write burst; the unit-status handshake terminates the
// job (record_last alone does not — an empty lane closes out through its
// producer's status with no stream beats at all).
module dau_axi_record_writer #(
    parameter int unsigned ADDR_WIDTH = 32,
    parameter int unsigned BURST_BEATS = 16
) (
    input  wire logic        clk,
    input  wire logic        rst,

    input  wire logic        start,
    input  wire logic [ADDR_WIDTH-1:0] output_address,
    output logic             busy,
    output logic             done,
    output logic             error,
    output logic [7:0]       error_code,
    output logic [31:0]      result_length_bytes,

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
    output logic             m_axi_bready,

    input  wire logic        record_valid,
    output logic             record_ready,
    input  wire logic [63:0] record_data,
    input  wire logic        record_last,
    input  wire logic        status_valid,
    output logic             status_ready,
    input  wire logic        status_error,
    input  wire logic [7:0]  status_error_code
);
    localparam logic [2:0] S_IDLE = 3'd0;
    localparam logic [2:0] S_RUN = 3'd1;
    localparam logic [2:0] S_AW = 3'd2;
    localparam logic [2:0] S_W = 3'd3;
    localparam logic [2:0] S_B = 3'd4;

    logic [2:0] state;
    logic [ADDR_WIDTH-1:0] beat_address;
    logic [63:0] beat;

    assign record_ready = (state == S_RUN);
    assign status_ready = (state == S_RUN) && !record_valid;

    assign m_axi_awaddr = beat_address;
    assign m_axi_awlen = 8'd0;
    assign m_axi_awsize = 3'd3;
    assign m_axi_awburst = 2'b01;
    assign m_axi_awvalid = (state == S_AW);
    assign m_axi_wdata = beat;
    assign m_axi_wstrb = 8'hFF;
    assign m_axi_wlast = 1'b1;
    assign m_axi_wvalid = (state == S_W);
    assign m_axi_bready = (state == S_B);

    always_ff @(posedge clk) begin
        if (rst) begin
            state <= S_IDLE;
            busy <= 1'b0;
            done <= 1'b0;
            error <= 1'b0;
            error_code <= 8'd0;
            result_length_bytes <= 32'd0;
        end else begin
            case (state)
                S_IDLE: begin
                    if (start) begin
                        busy <= 1'b1;
                        done <= 1'b0;
                        error <= 1'b0;
                        error_code <= 8'd0;
                        result_length_bytes <= 32'd0;
                        beat_address <= output_address;
                        state <= S_RUN;
                    end
                end
                S_RUN: begin
                    if (record_valid) begin
                        beat <= record_data;
                        state <= S_AW;
                    end else if (status_valid) begin
                        error <= status_error;
                        error_code <= status_error_code;
                        done <= 1'b1;
                        busy <= 1'b0;
                        state <= S_IDLE;
                    end
                end
                S_AW: begin
                    if (m_axi_awready) begin
                        state <= S_W;
                    end
                end
                S_W: begin
                    if (m_axi_wready) begin
                        state <= S_B;
                    end
                end
                S_B: begin
                    if (m_axi_bvalid) begin
                        beat_address <= beat_address + 8;
                        result_length_bytes <= result_length_bytes + 32'd8;
                        state <= S_RUN;
                    end
                end
                default: state <= S_IDLE;
            endcase
        end
    end
endmodule

`default_nettype wire
