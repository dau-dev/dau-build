module shifter #(parameter int IN_SIZE, parameter int OUT_SIZE) (
  input clk,
  input [IN_SIZE-1:0] counter,
  output logic[OUT_SIZE-1:0] out
);

initial begin
  out = ~'b1;
end

always_ff @ (posedge clk) begin
  if (counter == 'b0) begin
    out <= {out[OUT_SIZE-2:0], out[OUT_SIZE-1]};
  end
end
endmodule