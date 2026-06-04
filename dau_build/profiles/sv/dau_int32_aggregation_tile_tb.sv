`timescale 1ns/1ps
`default_nettype none

module dau_int32_aggregation_tile_tb;
    localparam logic [2:0] OP_MIN   = 3'd1;
    localparam logic [2:0] OP_MAX   = 3'd2;
    localparam logic [2:0] OP_SUM   = 3'd3;
    localparam logic [2:0] OP_COUNT = 3'd4;

    logic clk = 1'b0;
    logic rst = 1'b1;
    logic input_valid = 1'b0;
    logic input_ready;
    logic input_last = 1'b0;
    logic signed [31:0] input_value = 32'sd0;
    logic [2:0] input_opcode = OP_SUM;
    logic result_valid;
    logic result_ready = 1'b0;
    logic signed [63:0] result_value;
    logic error;

    dau_int32_aggregation_tile dut (
        .clk(clk),
        .rst(rst),
        .input_valid(input_valid),
        .input_ready(input_ready),
        .input_last(input_last),
        .input_value(input_value),
        .input_opcode(input_opcode),
        .result_valid(result_valid),
        .result_ready(result_ready),
        .result_value(result_value),
        .error(error)
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
            input_valid = 1'b0;
            input_last = 1'b0;
            result_ready = 1'b0;
            tick();
            rst = 1'b0;
            tick();
        end
    endtask

    task automatic send_sample(input logic [2:0] opcode, input logic signed [31:0] value, input logic last_sample);
        begin
            if (input_ready !== 1'b1) begin
                $fatal(1, "input_ready was low before sample");
            end
            input_opcode = opcode;
            input_value = value;
            input_last = last_sample;
            input_valid = 1'b1;
            tick();
            input_valid = 1'b0;
            input_last = 1'b0;
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

        send_sample(OP_SUM, 32'sd10, 1'b0);
        send_sample(OP_SUM, -32'sd4, 1'b0);
        send_sample(OP_SUM, 32'sd7, 1'b1);
        if (result_valid !== 1'b1 || error !== 1'b0 || result_value !== 64'sd13) begin
            $fatal(1, "sum result mismatch: valid=%0b error=%0b value=%0d", result_valid, error, result_value);
        end
        if (input_ready !== 1'b0) begin
            $fatal(1, "input_ready did not deassert while result was held");
        end
        accept_result();

        send_sample(OP_MIN, 32'sd9, 1'b0);
        send_sample(OP_MIN, -32'sd8, 1'b1);
        if (result_value !== -64'sd8) begin
            $fatal(1, "min result mismatch: %0d", result_value);
        end
        accept_result();

        send_sample(OP_MAX, -32'sd2, 1'b0);
        send_sample(OP_MAX, 32'sd15, 1'b1);
        if (result_value !== 64'sd15) begin
            $fatal(1, "max result mismatch: %0d", result_value);
        end
        accept_result();

        send_sample(OP_COUNT, 32'sd99, 1'b0);
        send_sample(OP_COUNT, 32'sd100, 1'b1);
        if (result_value !== 64'sd2) begin
            $fatal(1, "count result mismatch: %0d", result_value);
        end
        accept_result();

        send_sample(3'd7, 32'sd1, 1'b1);
        if (result_valid !== 1'b1 || error !== 1'b1 || result_value !== 64'sd0) begin
            $fatal(1, "unsupported opcode did not report error");
        end

        $display("DAU_INT32_AGGREGATION_TILE_TB_OK");
        $finish;
    end
endmodule

`default_nettype wire
