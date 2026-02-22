`timescale 1ns / 1ps
//
// doubler.sv - IEEE 754 Double-Precision Multiply by 2
//
// Takes a 64-bit IEEE 754 double on an AXI-Stream slave port and
// produces 2*x on an AXI-Stream master port with 1-cycle latency.
//
// IEEE 754 double layout:
//   [63]    sign
//   [62:52] exponent (11 bits, bias 1023)
//   [51:0]  mantissa (52 bits)
//
// Multiply by 2 = increment exponent by 1, with special-case handling
// for zero, denormals, infinity, NaN, and overflow.
//

module doubler (
    input  logic        aclk,
    input  logic        aresetn,

    // AXI-Stream slave (input)
    input  logic [63:0] s_axis_tdata,
    input  logic        s_axis_tvalid,
    output logic        s_axis_tready,
    input  logic        s_axis_tlast,

    // AXI-Stream master (output)
    output logic [63:0] m_axis_tdata,
    output logic        m_axis_tvalid,
    input  logic        m_axis_tready,
    output logic        m_axis_tlast
);

    // ----------------------------------------------------------------
    // Combinational doubling logic
    // ----------------------------------------------------------------
    logic        sign_in;
    logic [10:0] exp_in;
    logic [51:0] mant_in;

    logic [63:0] doubled;

    assign sign_in = s_axis_tdata[63];
    assign exp_in  = s_axis_tdata[62:52];
    assign mant_in = s_axis_tdata[51:0];

    always_comb begin
        if (exp_in == 11'h7FF) begin
            // Infinity or NaN: pass through unchanged
            doubled = s_axis_tdata;

        end else if (exp_in == 11'h000) begin
            if (mant_in == 52'h0) begin
                // +/-0: stays zero (preserve sign)
                doubled = s_axis_tdata;
            end else begin
                // Denormalized: shift mantissa left by 1
                if (mant_in[51]) begin
                    // Top mantissa bit set -> becomes normalized (exp=1)
                    doubled = {sign_in, 11'h001, mant_in[50:0], 1'b0};
                end else begin
                    // Still denormalized
                    doubled = {sign_in, 11'h000, mant_in[50:0], 1'b0};
                end
            end

        end else if (exp_in == 11'h7FE) begin
            // Max normal exponent: overflow to infinity
            doubled = {sign_in, 11'h7FF, 52'h0};

        end else begin
            // Normal case: increment exponent
            doubled = {sign_in, exp_in + 11'h001, mant_in};
        end
    end

    // ----------------------------------------------------------------
    // 1-stage AXI-Stream pipeline register
    // ----------------------------------------------------------------
    logic [63:0] out_data;
    logic        out_valid;
    logic        out_last;

    always_ff @(posedge aclk) begin
        if (!aresetn) begin
            out_valid <= 1'b0;
            out_last  <= 1'b0;
            out_data  <= 64'h0;
        end else if (s_axis_tvalid && s_axis_tready) begin
            out_data  <= doubled;
            out_valid <= 1'b1;
            out_last  <= s_axis_tlast;
        end else if (m_axis_tready) begin
            out_valid <= 1'b0;
        end
    end

    assign m_axis_tdata  = out_data;
    assign m_axis_tvalid = out_valid;
    assign m_axis_tlast  = out_last;
    assign s_axis_tready = !out_valid || m_axis_tready;

endmodule
