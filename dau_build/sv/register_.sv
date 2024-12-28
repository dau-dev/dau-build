`timescale 1ns/1ns

module register_ #(
	parameter SIZE = 32
)
(
	input clk,
	input reset,
	input valid_i,

	input logic [SIZE - 1: 0] data_i,
	output logic [SIZE - 1: 0] data_o,
	output bit valid_o
);

// not handled by parser
// ceff #(.SIZE(SIZE)) ff_inst(.*)
ceff #(.SIZE(SIZE)) ff_inst(
	clk,
	reset,
	valid_i,
	data_i,
	data_o,
	valid_o
);

endmodule
