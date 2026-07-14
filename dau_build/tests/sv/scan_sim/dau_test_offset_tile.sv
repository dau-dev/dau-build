`default_nettype none

// Test-only conforming operator tile for scan-composition benches: a
// stream pass-through that adds cfg_offset to every 64-bit row, counts
// rows into row_count, and closes out with a success status after the
// last row is delivered.
module dau_test_offset_tile (
    input  wire logic        clk,
    input  wire logic        rst,
    input  wire logic [63:0] cfg_offset,
    input  wire logic        input_valid,
    output logic             input_ready,
    input  wire logic [63:0] input_data,
    input  wire logic        input_last,
    output logic             output_valid,
    input  wire logic        output_ready,
    output logic [63:0]      output_data,
    output logic             output_last,
    output logic             status_valid,
    input  wire logic        status_ready,
    output logic             status_error,
    output logic [7:0]       status_error_code,
    output logic [63:0]      row_count
);
    logic streaming;

    assign output_valid = streaming && input_valid;
    assign input_ready = streaming && output_ready;
    assign output_data = input_data + cfg_offset;
    assign output_last = input_last;
    assign status_valid = !streaming;
    assign status_error = 1'b0;
    assign status_error_code = 8'd0;

    always_ff @(posedge clk) begin
        if (rst) begin
            streaming <= 1'b1;
            row_count <= 64'd0;
        end else if (streaming) begin
            if (input_valid && input_ready) begin
                row_count <= row_count + 64'd1;
                if (input_last) begin
                    streaming <= 1'b0;
                end
            end
        end else if (status_ready) begin
            streaming <= 1'b1;
            row_count <= 64'd0;
        end
    end
endmodule

`default_nettype wire
