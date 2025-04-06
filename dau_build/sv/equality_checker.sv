`timescale 1ns/1ns

module equality_checker #(
	parameter DATA_WIDTH = 5,
	parameter NUM_COMP = 32
)
(
	input logic [2**DATA_WIDTH-1:0] inp_i [NUM_COMP],
	input logic valid_i [NUM_COMP-1:0],
	input logic [2**DATA_WIDTH-1:0] data_i,
	output logic [NUM_COMP-1:0] out_o
);

logic [NUM_COMP-1:0] out;

assign out_o = out;

always_comb begin
	for (int iter = 0; iter < NUM_COMP; ++iter) begin
		out[iter] = ((inp_i[iter] == data_i) & valid_i[iter]);
	end
end

endmodule
