`timescale 1ns/1ns

module cam #(
  parameter ARRAY_WIDTH_LOG2 = 5,
  parameter ARRAY_SIZE_LOG2 = 5
)
(
  input logic clk,
  input logic reset,
  input logic read_i,
  input logic [ARRAY_WIDTH_LOG2 - 1:0] read_index_i,

  input logic write_i,
  input logic [ARRAY_WIDTH_LOG2 - 1:0] write_index_i,
  input logic [2**ARRAY_WIDTH_LOG2 - 1:0] write_data_i,

  input logic search_i,
  input logic [2**ARRAY_WIDTH_LOG2 - 1:0] search_data_i,

  output logic read_valid_o,
  output logic [2**ARRAY_WIDTH_LOG2 - 1:0] read_value_o,

  output logic search_valid_o,
  output logic [ARRAY_WIDTH_LOG2 - 1:0] search_index_o
);

/* combinational outputs */
logic [2**ARRAY_WIDTH_LOG2 - 1:0] out_value; /* if we are reading */
logic [ARRAY_WIDTH_LOG2 - 1:0] out_index; /* if we are searching */

/* indicate data found (search) or data previously written (read) */
logic found;
logic written;

/*
 * these connect a 32x32 CAM with registers we designed in class
 */
logic [2**ARRAY_WIDTH_LOG2 - 1:0] cam_i [2**ARRAY_SIZE_LOG2 - 1:0];
wire [2**ARRAY_WIDTH_LOG2 - 1:0] cam_o [2**ARRAY_SIZE_LOG2 - 1:0];
wire cam_v_o [2**ARRAY_SIZE_LOG2 - 1:0];

/* CAM search intermediate */
logic [2**ARRAY_SIZE_LOG2-1:0] cam_found; /* output of ANDing CAM entries with input */

/***** FUNCTIONALITY *****/
/* write functionality */
logic write_reg_enable [2**ARRAY_SIZE_LOG2-1:0];
decoder write_dec(
  .inp_i(write_index_i),
  .valid_i(write_i),
  .out_o(write_reg_enable)
);

always_comb begin
  for (int iter = 0; iter < 2**ARRAY_SIZE_LOG2; ++iter) begin
    cam_i[iter] = write_data_i;
  end
end

/* read functionality */
// read value
mux #(
  .SELECT_WIDTH(ARRAY_SIZE_LOG2),
  .DATA_WIDTH(ARRAY_WIDTH_LOG2)
) read_data_mux(
  .inp_i(cam_o),
  .selector_i(read_index_i),
  .out_o(out_value)
);

// read valid
mux #(
  .SELECT_WIDTH(ARRAY_SIZE_LOG2),
  .DATA_WIDTH(0)
) read_valid_mux(
  .inp_i(cam_v_o),
  .selector_i(read_index_i),
  .out_o(written)
);

/* search functionality */
equality_checker #(
  .DATA_WIDTH(ARRAY_WIDTH_LOG2),
  .NUM_COMP(2**ARRAY_SIZE_LOG2)
) eq_check_search (
  .inp_i(cam_o),
  .valid_i(cam_v_o),
  .data_i(search_data_i),
  .out_o(cam_found)
);
priorityencoder #(
  .SIZE(ARRAY_WIDTH_LOG2)
) search_priorityenc (
  .inp_i(cam_found),
  .out_o(out_index),
  .valid_o(found)
);


/***** ASSIGNMENTS *****/
assign read_valid_o = read_i && written && !reset; /* read if value was previously written */
assign search_valid_o = search_i && found && !reset; /* search_o if value found in CAM */
assign read_value_o = out_value; /* output read result */
assign search_index_o = out_index; /* output search index */

/* generate registers for CAM entries */
generate
  for(genvar iter = 0; iter < 2**ARRAY_SIZE_LOG2; iter++) begin
    register_ #(
      .SIZE(2**ARRAY_SIZE_LOG2)
    ) ar_inst(
      .clk(clk),
      .reset(reset),
      .data_i(cam_i[iter]),
      .data_o(cam_o[iter]),
      .valid_i(write_reg_enable[iter]),
      .valid_o(cam_v_o[iter])
    );
  end
endgenerate

`ifndef SYNTHESIS
initial begin
  $display("[%0t]\tTracing to logs/dump.vcd...", $time);
  $dumpfile("logs/dump.vcd");
  $dumpvars();
  $display("[%0t]\tModel running...", $time);
end
`endif

endmodule
