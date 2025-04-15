module counter #(parameter int OUT_SIZE=32) (
  input clk,
  output logic[OUT_SIZE-1:0] out
);

initial begin
out = 'b0;
end

always_ff @ (posedge clk) begin
  out <= out + 1;
end
endmodule
