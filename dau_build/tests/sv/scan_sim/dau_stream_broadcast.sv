`default_nettype none

// Test-only behavioral double of the dau-core stream broadcast: one
// input beat is delivered to every output before the next is accepted.
module dau_stream_broadcast #(
    parameter int unsigned NUM_OUTPUTS = 2
) (
    input  wire logic        clk,
    input  wire logic        rst,
    input  wire logic        input_valid,
    output logic             input_ready,
    input  wire logic [63:0] input_data,
    input  wire logic        input_last,
    output logic [NUM_OUTPUTS-1:0] output_valid,
    input  wire logic [NUM_OUTPUTS-1:0] output_ready,
    output logic [63:0]      output_data,
    output logic             output_last
);
    logic [NUM_OUTPUTS-1:0] sent;
    logic [NUM_OUTPUTS-1:0] fire;

    assign output_valid = input_valid ? ~sent : '0;
    assign fire = output_valid & output_ready;
    assign output_data = input_data;
    assign output_last = input_last;
    assign input_ready = input_valid && (&(sent | fire));

    always_ff @(posedge clk) begin
        if (rst) begin
            sent <= '0;
        end else if (input_valid && input_ready) begin
            sent <= '0;
        end else begin
            sent <= sent | fire;
        end
    end
endmodule

`default_nettype wire
