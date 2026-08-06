// Smoke fixture for the cocotb/verilator runner, not a DAU tile.
//
// `out` starts at zero from its declaration rather than from a separate
// initial block: driving it from both an initial and an always_ff makes it
// multiply-driven, which Verilator treats as an error rather than a warning.
module counter #(parameter int OUT_SIZE=32) (
  input clk,
  output logic[OUT_SIZE-1:0] out = '0
);

always_ff @ (posedge clk) begin
  out <= out + 1;
end
endmodule
