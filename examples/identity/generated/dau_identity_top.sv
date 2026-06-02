`timescale 1ns/1ns

module dau_identity_top (
  input wire logic clk,
  input wire logic reset,
  input wire logic register_read_enable,
  input wire logic register_write_enable,
  input wire logic [15:0] register_address,
  input wire logic [31:0] register_write_data,
  output logic [31:0] register_read_data,
  output logic register_ready,
  input wire logic stream_input_valid,
  output logic stream_input_ready,
  input wire logic [63:0] stream_input_data,
  input wire logic stream_input_last,
  output logic stream_output_valid,
  input wire logic stream_output_ready,
  output logic [63:0] stream_output_data,
  output logic stream_output_last,
  output logic stream_status_valid,
  input wire logic stream_status_ready,
  output logic stream_status_error,
  output logic [7:0] stream_status_error_code,
  output logic [63:0] dma_input_address,
  output logic [31:0] dma_input_length,
  output logic [63:0] dma_output_address,
  output logic [31:0] dma_output_length,
  output logic dma_start,
  input wire logic dma_done,
  input wire logic dma_error,
  output logic [31:0] capability_magic,
  output logic [31:0] capability_register_map_version,
  output logic [31:0] capability_stream_protocol_version,
  output logic [31:0] capability_operator_bitmap,
  input logic [31:0] identity_sample_in,
  output logic [31:0] identity_sample_out
);

  localparam logic [31:0] DAU_MAGIC = 32'h44415531;
  localparam logic [31:0] DAU_REGISTER_MAP_VERSION = 32'h00000001;
  localparam logic [31:0] DAU_STREAM_PROTOCOL_VERSION = 32'h00000001;
  localparam logic [31:0] DAU_OPERATOR_BITMAP = 32'h00000000;
  localparam logic [15:0] DAU_REGISTER_MAGIC_OFFSET = 16'h0000;
  localparam logic [15:0] DAU_REGISTER_MAP_VERSION_OFFSET = 16'h0004;
  localparam logic [15:0] DAU_STREAM_PROTOCOL_VERSION_OFFSET = 16'h0008;
  localparam logic [15:0] DAU_REGISTER_OPERATOR_BITMAP_OFFSET = 16'h0028;
  localparam logic [15:0] DAU_REGISTER_LAST_ERROR_OFFSET = 16'h002c;
  localparam logic [15:0] DAU_REGISTER_JOB_CONTROL_OFFSET = 16'h0050;
  localparam logic [15:0] DAU_REGISTER_JOB_STATUS_OFFSET = 16'h0054;
  localparam logic [15:0] DAU_REGISTER_INPUT_ADDRESS_LOW_OFFSET = 16'h0058;
  localparam logic [15:0] DAU_REGISTER_INPUT_ADDRESS_HIGH_OFFSET = 16'h005c;
  localparam logic [15:0] DAU_REGISTER_INPUT_LENGTH_LOW_OFFSET = 16'h0060;
  localparam logic [15:0] DAU_REGISTER_INPUT_LENGTH_HIGH_OFFSET = 16'h0064;
  localparam logic [15:0] DAU_REGISTER_OUTPUT_ADDRESS_LOW_OFFSET = 16'h0068;
  localparam logic [15:0] DAU_REGISTER_OUTPUT_ADDRESS_HIGH_OFFSET = 16'h006c;
  localparam logic [15:0] DAU_REGISTER_OUTPUT_LENGTH_LOW_OFFSET = 16'h0070;
  localparam logic [15:0] DAU_REGISTER_OUTPUT_LENGTH_HIGH_OFFSET = 16'h0074;
  localparam logic [15:0] DAU_REGISTER_OPERATION_OFFSET = 16'h0078;
  localparam logic [15:0] DAU_REGISTER_RESULT_LENGTH_LOW_OFFSET = 16'h007c;
  localparam logic [15:0] DAU_REGISTER_RESULT_LENGTH_HIGH_OFFSET = 16'h0080;
  localparam logic [63:0] DAU_DEFAULT_INPUT_ADDRESS = 64'h0000000000000000;
  localparam logic [63:0] DAU_DEFAULT_INPUT_LENGTH = 64'h0000000000100000;
  localparam logic [63:0] DAU_DEFAULT_OUTPUT_ADDRESS = 64'h0000000000100000;
  localparam logic [63:0] DAU_DEFAULT_OUTPUT_LENGTH = 64'h0000000000100000;
  localparam logic [63:0] DAU_DEFAULT_RESULT_BYTES = 64'h0000000000000088;

  logic [63:0] job_input_address;
  logic [63:0] job_input_length;
  logic [63:0] job_output_address;
  logic [63:0] job_output_length;
  logic [63:0] job_result_length;
  logic [31:0] job_operation;
  logic [31:0] job_last_error;
  logic job_busy;
  logic job_done;
  logic job_error;
  logic stream_job_start_pulse;
  logic [31:0] job_status_value;

  assign register_ready = register_read_enable || register_write_enable;
  assign stream_job_start_pulse = register_write_enable && (register_address == DAU_REGISTER_JOB_CONTROL_OFFSET) && register_write_data[0];
  assign job_status_value = {28'd0, job_error, job_done, job_busy, !job_busy};
  assign dma_input_address = job_input_address;
  assign dma_input_length = job_input_length[31:0];
  assign dma_output_address = job_output_address;
  assign dma_output_length = job_output_length[31:0];
  assign dma_start = stream_job_start_pulse;
  assign capability_magic = 32'h44415531;
  assign capability_register_map_version = DAU_REGISTER_MAP_VERSION;
  assign capability_stream_protocol_version = DAU_STREAM_PROTOCOL_VERSION;
  assign capability_operator_bitmap = DAU_OPERATOR_BITMAP;

  assign stream_input_ready = 1'b0;
  assign stream_output_valid = 1'b0;
  assign stream_output_data = 64'd0;
  assign stream_output_last = 1'b0;
  assign stream_status_valid = 1'b0;
  assign stream_status_error = 1'b0;
  assign stream_status_error_code = 8'd0;

  always_ff @(posedge clk) begin
    if (reset) begin
      job_input_address <= DAU_DEFAULT_INPUT_ADDRESS;
      job_input_length <= DAU_DEFAULT_INPUT_LENGTH;
      job_output_address <= DAU_DEFAULT_OUTPUT_ADDRESS;
      job_output_length <= DAU_DEFAULT_OUTPUT_LENGTH;
      job_result_length <= 64'd0;
      job_operation <= 32'd0;
      job_last_error <= 32'd0;
      job_busy <= 1'b0;
      job_done <= 1'b0;
      job_error <= 1'b0;
    end else begin
      if (register_write_enable) begin
        unique case (register_address)
          DAU_REGISTER_INPUT_ADDRESS_LOW_OFFSET: job_input_address[31:0] <= register_write_data;
          DAU_REGISTER_INPUT_ADDRESS_HIGH_OFFSET: job_input_address[63:32] <= register_write_data;
          DAU_REGISTER_INPUT_LENGTH_LOW_OFFSET: job_input_length[31:0] <= register_write_data;
          DAU_REGISTER_INPUT_LENGTH_HIGH_OFFSET: job_input_length[63:32] <= register_write_data;
          DAU_REGISTER_OUTPUT_ADDRESS_LOW_OFFSET: job_output_address[31:0] <= register_write_data;
          DAU_REGISTER_OUTPUT_ADDRESS_HIGH_OFFSET: job_output_address[63:32] <= register_write_data;
          DAU_REGISTER_OUTPUT_LENGTH_LOW_OFFSET: job_output_length[31:0] <= register_write_data;
          DAU_REGISTER_OUTPUT_LENGTH_HIGH_OFFSET: job_output_length[63:32] <= register_write_data;
          DAU_REGISTER_OPERATION_OFFSET: job_operation <= register_write_data;
          default: begin end
        endcase
      end
      if (stream_job_start_pulse) begin
        job_busy <= 1'b1;
        job_done <= 1'b0;
        job_error <= 1'b0;
        job_last_error <= 32'd0;
        job_result_length <= 64'd0;
      end
      if (stream_status_valid && stream_status_ready) begin
        job_busy <= 1'b0;
        job_done <= !stream_status_error;
        job_error <= stream_status_error;
        job_last_error <= {24'd0, stream_status_error_code};
        job_result_length <= stream_status_error ? 64'd0 : DAU_DEFAULT_RESULT_BYTES;
      end else if (dma_error) begin
        job_busy <= 1'b0;
        job_done <= 1'b0;
        job_error <= 1'b1;
        job_last_error <= 32'h0000_0003;
        job_result_length <= 64'd0;
      end else if (dma_done && job_busy) begin
        job_busy <= 1'b0;
        job_done <= 1'b1;
        job_error <= 1'b0;
        job_result_length <= DAU_DEFAULT_RESULT_BYTES;
      end
    end
  end

  always_comb begin
    unique case (register_address)
      DAU_REGISTER_MAGIC_OFFSET: register_read_data = DAU_MAGIC;
      DAU_REGISTER_MAP_VERSION_OFFSET: register_read_data = DAU_REGISTER_MAP_VERSION;
      DAU_STREAM_PROTOCOL_VERSION_OFFSET: register_read_data = DAU_STREAM_PROTOCOL_VERSION;
      DAU_REGISTER_OPERATOR_BITMAP_OFFSET: register_read_data = DAU_OPERATOR_BITMAP;
      DAU_REGISTER_LAST_ERROR_OFFSET: register_read_data = job_last_error;
      DAU_REGISTER_JOB_CONTROL_OFFSET: register_read_data = 32'd0;
      DAU_REGISTER_JOB_STATUS_OFFSET: register_read_data = job_status_value;
      DAU_REGISTER_INPUT_ADDRESS_LOW_OFFSET: register_read_data = job_input_address[31:0];
      DAU_REGISTER_INPUT_ADDRESS_HIGH_OFFSET: register_read_data = job_input_address[63:32];
      DAU_REGISTER_INPUT_LENGTH_LOW_OFFSET: register_read_data = job_input_length[31:0];
      DAU_REGISTER_INPUT_LENGTH_HIGH_OFFSET: register_read_data = job_input_length[63:32];
      DAU_REGISTER_OUTPUT_ADDRESS_LOW_OFFSET: register_read_data = job_output_address[31:0];
      DAU_REGISTER_OUTPUT_ADDRESS_HIGH_OFFSET: register_read_data = job_output_address[63:32];
      DAU_REGISTER_OUTPUT_LENGTH_LOW_OFFSET: register_read_data = job_output_length[31:0];
      DAU_REGISTER_OUTPUT_LENGTH_HIGH_OFFSET: register_read_data = job_output_length[63:32];
      DAU_REGISTER_OPERATION_OFFSET: register_read_data = job_operation;
      DAU_REGISTER_RESULT_LENGTH_LOW_OFFSET: register_read_data = job_result_length[31:0];
      DAU_REGISTER_RESULT_LENGTH_HIGH_OFFSET: register_read_data = job_result_length[63:32];
      default: register_read_data = 32'hffff_ffff;
    endcase
  end

  identity identity_inst (
    .clk(clk),
    .reset(reset),
    .sample_in(identity_sample_in),
    .sample_out(identity_sample_out)
  );

endmodule
