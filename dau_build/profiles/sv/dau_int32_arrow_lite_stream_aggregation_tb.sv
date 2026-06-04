`timescale 1ns/1ps
`default_nettype none

module dau_int32_arrow_lite_stream_aggregation_tb;
    localparam logic [63:0] HEADER_WORD0 = 64'h0040010044415531;
    localparam logic [63:0] HEADER_COUNTS = 64'h0000000000010001;
    localparam logic [63:0] COLUMN_WORD0 = 64'h00000020000c0000;
    localparam logic [63:0] OP_SUM_WORD0 = 64'h0000000000030000;
    localparam logic [63:0] OP_COUNT_WORD0 = 64'h0000000000040000;
    localparam logic [63:0] OP_INT64_WORD1 = 64'h000000000040000d;
    localparam logic [63:0] OP_UINT64_WORD1 = 64'h0000000000400017;
    localparam logic [63:0] PAYLOAD_10_NEG3 = 64'hfffffffd0000000a;
    localparam logic [63:0] PAYLOAD_5_8 = 64'h0000000800000005;

    logic clk = 1'b0;
    logic rst = 1'b1;
    logic input_valid = 1'b0;
    logic input_ready;
    logic [63:0] input_data = 64'd0;
    logic input_last = 1'b0;
    logic output_valid;
    logic output_ready = 1'b0;
    logic [63:0] output_data;
    logic output_last;
    logic status_valid;
    logic status_ready = 1'b0;
    logic status_error;
    logic [7:0] status_error_code;

    dau_int32_arrow_lite_stream_aggregation dut (
        .clk(clk),
        .rst(rst),
        .input_valid(input_valid),
        .input_ready(input_ready),
        .input_data(input_data),
        .input_last(input_last),
        .output_valid(output_valid),
        .output_ready(output_ready),
        .output_data(output_data),
        .output_last(output_last),
        .status_valid(status_valid),
        .status_ready(status_ready),
        .status_error(status_error),
        .status_error_code(status_error_code)
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
            output_ready = 1'b0;
            status_ready = 1'b0;
            tick();
            rst = 1'b0;
            tick();
        end
    endtask

    task automatic send_word(input logic [63:0] word, input logic last_word);
        begin
            if (input_ready !== 1'b1) begin
                $fatal(1, "input_ready was low before input word");
            end
            input_data = word;
            input_last = last_word;
            input_valid = 1'b1;
            tick();
            input_valid = 1'b0;
            input_last = 1'b0;
        end
    endtask

    task automatic send_sum_stream;
        begin
            send_word(HEADER_WORD0, 1'b0);
            send_word(64'h0000000000001234, 1'b0);
            send_word(64'd7, 1'b0);
            send_word(64'd4, 1'b0);
            send_word(HEADER_COUNTS, 1'b0);
            send_word(64'd64, 1'b0);
            send_word(64'd192, 1'b0);
            send_word(64'd16, 1'b0);
            send_word(COLUMN_WORD0, 1'b0);
            send_word(64'd4, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(64'd192, 1'b0);
            send_word(64'd16, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(OP_SUM_WORD0, 1'b0);
            send_word(OP_INT64_WORD1, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(PAYLOAD_10_NEG3, 1'b0);
            send_word(PAYLOAD_5_8, 1'b1);
        end
    endtask

    task automatic send_count_stream;
        begin
            send_word(HEADER_WORD0, 1'b0);
            send_word(64'h0000000000001234, 1'b0);
            send_word(64'd8, 1'b0);
            send_word(64'd3, 1'b0);
            send_word(HEADER_COUNTS, 1'b0);
            send_word(64'd64, 1'b0);
            send_word(64'd192, 1'b0);
            send_word(64'd12, 1'b0);
            send_word(COLUMN_WORD0, 1'b0);
            send_word(64'd3, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(64'd192, 1'b0);
            send_word(64'd12, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(OP_COUNT_WORD0, 1'b0);
            send_word(OP_UINT64_WORD1, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(64'h0000000200000001, 1'b0);
            send_word(64'h0000000000000003, 1'b1);
        end
    endtask

    task automatic send_bad_descriptor_stream;
        begin
            send_word(HEADER_WORD0, 1'b0);
            send_word(64'h0000000000001234, 1'b0);
            send_word(64'd9, 1'b0);
            send_word(64'd4, 1'b0);
            send_word(HEADER_COUNTS, 1'b0);
            send_word(64'd64, 1'b0);
            send_word(64'd192, 1'b0);
            send_word(64'd8, 1'b0);
            send_word(COLUMN_WORD0, 1'b0);
            send_word(64'd4, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(64'd192, 1'b0);
            send_word(64'd16, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(OP_SUM_WORD0, 1'b0);
            send_word(OP_INT64_WORD1, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(64'd0, 1'b0);
            send_word(PAYLOAD_10_NEG3, 1'b0);
            send_word(PAYLOAD_5_8, 1'b1);
        end
    endtask

    task automatic expect_result(input logic [63:0] expected_value, input logic [15:0] expected_type);
        integer output_words;
        begin
            output_words = 0;
            wait (output_valid === 1'b1);
            output_ready = 1'b0;
            repeat (3) begin
                tick();
                if (output_valid !== 1'b1) begin
                    $fatal(1, "output_valid dropped under backpressure");
                end
            end
            output_ready = 1'b1;
            while (output_words < 17) begin
                if (output_valid === 1'b1) begin
                    output_words = output_words + 1;
                    if (output_words == 1 && output_data !== HEADER_WORD0) begin
                        $fatal(1, "result header word0 mismatch");
                    end
                    if (output_words == 4 && output_data !== 64'd1) begin
                        $fatal(1, "result row_count mismatch");
                    end
                    if (output_words == 8 && output_data !== 64'd8) begin
                        $fatal(1, "result payload_length mismatch");
                    end
                    if (output_words == 9 && output_data[31:16] !== expected_type) begin
                        $fatal(1, "result logical type mismatch");
                    end
                    if (output_words == 17) begin
                        if (output_data !== expected_value || output_last !== 1'b1) begin
                            $fatal(1, "result payload mismatch");
                        end
                    end
                end
                tick();
            end
            output_ready = 1'b0;
            status_ready = 1'b1;
            if (status_valid !== 1'b1 || status_error !== 1'b0 || status_error_code !== 8'd0) begin
                $fatal(1, "success status mismatch");
            end
            tick();
            status_ready = 1'b0;
        end
    endtask

    task automatic expect_error(input logic [7:0] expected_code);
        begin
            status_ready = 1'b1;
            wait (status_valid === 1'b1);
            if (status_error !== 1'b1 || status_error_code !== expected_code || output_valid !== 1'b0) begin
                $fatal(1, "error status mismatch");
            end
            tick();
            status_ready = 1'b0;
        end
    endtask

    initial begin
        apply_reset();
        send_sum_stream();
        expect_result(64'd20, 16'd13);

        send_count_stream();
        expect_result(64'd3, 16'd23);

        send_bad_descriptor_stream();
        expect_error(8'd1);

        $display("DAU_INT32_ARROW_LITE_STREAM_AGGREGATION_TB_OK");
        $finish;
    end
endmodule

`default_nettype wire
