`default_nettype none

// Generic streaming test module for dau-build's generated-top wiring tests:
// doubles each 64-bit word, passing last through; status reports success at
// stream end. Same generic port contract the top generator keys on.
module stream_doubler (
    input  wire logic        clk,
    input  wire logic        rst,
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
    output logic [7:0]       status_error_code
);
    assign input_ready = !output_valid || output_ready;
    assign status_error = 1'b0;
    assign status_error_code = 8'd0;

    always_ff @(posedge clk) begin
        if (rst) begin
            output_valid <= 1'b0;
            output_data <= 64'd0;
            output_last <= 1'b0;
            status_valid <= 1'b0;
        end else begin
            if (output_valid && output_ready) begin
                output_valid <= 1'b0;
            end
            if (status_valid && status_ready) begin
                status_valid <= 1'b0;
            end
            if (input_valid && input_ready) begin
                output_valid <= 1'b1;
                output_data <= input_data << 1;
                output_last <= input_last;
                if (input_last) begin
                    status_valid <= 1'b1;
                end
            end
        end
    end
endmodule

`default_nettype wire
