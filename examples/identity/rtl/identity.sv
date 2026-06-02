module identity (
    input bit clk,
    input bit reset,
    input logic [31:0] sample_in,
    output logic [31:0] sample_out
);
    assign sample_out = sample_in;
endmodule