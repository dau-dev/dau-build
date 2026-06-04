`timescale 1ns/1ps
`default_nettype none

module dau_int32_stream_aggregation_tb;
    localparam logic [15:0] LT_INT32   = 16'd12;
    localparam logic [15:0] LT_INT64   = 16'd13;
    localparam logic [15:0] LT_UINT64  = 16'd23;
    localparam logic [15:0] LT_FLOAT32 = 16'd30;

    localparam logic [2:0] OP_SUM   = 3'd3;
    localparam logic [2:0] OP_COUNT = 3'd4;

    logic clk = 1'b0;
    logic rst = 1'b1;
    logic descriptor_valid = 1'b0;
    logic descriptor_ready;
    logic [63:0] descriptor_row_count = 64'd0;
    logic [15:0] descriptor_logical_type = LT_INT32;
    logic [15:0] descriptor_physical_width_bits = 16'd32;
    logic [63:0] descriptor_payload_length = 64'd0;
    logic [2:0] descriptor_opcode = OP_SUM;
    logic input_valid = 1'b0;
    logic input_ready;
    logic signed [31:0] input_value = 32'sd0;
    logic result_valid;
    logic result_ready = 1'b0;
    logic signed [63:0] result_value;
    logic [15:0] result_logical_type;
    logic [63:0] result_payload_length;
    logic [63:0] result_row_count;
    logic error;
    logic [7:0] error_code;

    dau_int32_stream_aggregation dut (
        .clk(clk),
        .rst(rst),
        .descriptor_valid(descriptor_valid),
        .descriptor_ready(descriptor_ready),
        .descriptor_row_count(descriptor_row_count),
        .descriptor_logical_type(descriptor_logical_type),
        .descriptor_physical_width_bits(descriptor_physical_width_bits),
        .descriptor_payload_length(descriptor_payload_length),
        .descriptor_opcode(descriptor_opcode),
        .input_valid(input_valid),
        .input_ready(input_ready),
        .input_value(input_value),
        .result_valid(result_valid),
        .result_ready(result_ready),
        .result_value(result_value),
        .result_logical_type(result_logical_type),
        .result_payload_length(result_payload_length),
        .result_row_count(result_row_count),
        .error(error),
        .error_code(error_code)
    );

    always #5 clk = ~clk;

    task automatic tick;
        begin
            @(posedge clk);
            #1;
        end
    endtask

    task automatic apply_reset;
        begin
            rst = 1'b1;
            descriptor_valid = 1'b0;
            input_valid = 1'b0;
            result_ready = 1'b0;
            tick();
            rst = 1'b0;
            tick();
        end
    endtask

    task automatic send_descriptor(
        input logic [2:0] opcode,
        input logic [63:0] row_count,
        input logic [15:0] logical_type,
        input logic [15:0] physical_width_bits,
        input logic [63:0] payload_length
    );
        begin
            if (descriptor_ready !== 1'b1) begin
                $fatal(1, "descriptor_ready was low before descriptor");
            end
            descriptor_opcode = opcode;
            descriptor_row_count = row_count;
            descriptor_logical_type = logical_type;
            descriptor_physical_width_bits = physical_width_bits;
            descriptor_payload_length = payload_length;
            descriptor_valid = 1'b1;
            tick();
            descriptor_valid = 1'b0;
        end
    endtask

    task automatic send_value(input logic signed [31:0] value);
        begin
            if (input_ready !== 1'b1) begin
                $fatal(1, "input_ready was low before value");
            end
            input_value = value;
            input_valid = 1'b1;
            tick();
            input_valid = 1'b0;
        end
    endtask

    task automatic wait_for_result;
        begin
            repeat (4) begin
                if (result_valid === 1'b1) begin
                    return;
                end
                tick();
            end
            $fatal(1, "timed out waiting for result_valid");
        end
    endtask

    task automatic accept_result;
        begin
            result_ready = 1'b1;
            tick();
            result_ready = 1'b0;
        end
    endtask

    initial begin
        apply_reset();

        send_descriptor(OP_SUM, 64'd3, LT_INT32, 16'd32, 64'd12);
        send_value(32'sd10);
        send_value(-32'sd4);
        send_value(32'sd7);
        wait_for_result();
        if (error !== 1'b0 || result_value !== 64'sd13 || result_logical_type !== LT_INT64 || result_payload_length !== 64'd8 || result_row_count !== 64'd1) begin
            $fatal(1, "sum metadata/result mismatch");
        end
        if (descriptor_ready !== 1'b0 || input_ready !== 1'b0) begin
            $fatal(1, "wrapper accepted traffic while result was held");
        end
        accept_result();

        send_descriptor(OP_COUNT, 64'd4, LT_INT32, 16'd32, 64'd16);
        send_value(32'sd1);
        send_value(32'sd2);
        send_value(32'sd3);
        send_value(32'sd4);
        wait_for_result();
        if (error !== 1'b0 || result_value !== 64'sd4 || result_logical_type !== LT_UINT64) begin
            $fatal(1, "count result mismatch");
        end
        accept_result();

        send_descriptor(OP_SUM, 64'd2, LT_FLOAT32, 16'd32, 64'd8);
        wait_for_result();
        if (error !== 1'b1 || error_code !== 8'd1 || input_ready !== 1'b0) begin
            $fatal(1, "malformed descriptor was not rejected");
        end

        $display("DAU_INT32_STREAM_AGGREGATION_TB_OK");
        $finish;
    end
endmodule

`default_nettype wire
