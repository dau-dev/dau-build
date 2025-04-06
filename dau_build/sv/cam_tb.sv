`timescale 1ns/1ns
`include "cam_transaction.svh"
`include "cam_testing_env.svh"

/* the testbench */
program cam_tb #(
    parameter ARRAY_WIDTH_LOG2 = 5,
    parameter ARRAY_SIZE_LOG2 = 5
)
(
  input logic clk,

  output logic reset,
  output logic read_i,
  output logic [ARRAY_WIDTH_LOG2 - 1:0] read_index_i,

  output logic write_i,
  output logic [ARRAY_WIDTH_LOG2 - 1:0] write_index_i,
  output logic [2**ARRAY_WIDTH_LOG2 - 1:0] write_data_i,

  output logic search_i,
  output logic [2**ARRAY_WIDTH_LOG2 - 1:0] search_data_i,

  input logic read_valid_o,
  input logic [2**ARRAY_WIDTH_LOG2 - 1:0] read_value_o,

  input logic search_valid_o,
  input logic [ARRAY_WIDTH_LOG2 - 1:0] search_index_o
);

transaction t;
testing_env v;

bit read;
bit write;
bit search;
bit to_reset;
bit pass;
bit result;

initial begin
    t = new();
    v = new();
    v.read_config("config.txt");
    pass = 1;

    /* flush hardware */
    repeat(2) begin
        reset = 1'b1;
        @(posedge clk);
    end
    /* end flush */

    /* begin testing */
    repeat(v.iter) begin
        int _ = v.randomize();

        //decide to read, write, search, or reset
        read = v.get_read();
        write = v.get_write();
        search = v.get_search();
        to_reset = v.get_reset();

        // drive inputs for next cycle
        if(to_reset) begin
            reset = 1'b1;
        end else begin
            reset = 1'b0;
            if(read) begin
                read_i = 1'b1;
                read_index_i = v.read_index;
                $display("[%0t]\tREAD\t[%10d]", $realtime, v.read_index);
            end else begin
                read_i = 1'b0;
            end
            if(write) begin
                write_i = 1'b1;
                write_index_i = v.write_index;
                write_data_i = v.write_value;
                $display("[%0t]\tWRITE\t[%10d,%10d]", $realtime, v.write_index, v.write_value);
            end else begin
                write_i = 1'b0;
            end
            if(search) begin
                search_i = 1'b1;
                search_data_i = v.search_value;
                $display("[%0t]\tSEARCH\t[%10d]", $realtime, v.search_value);
            end else begin
                search_i = 1'b0;
            end
        end

        @(posedge clk);

        //golden results
        t.golden_result_write(write ,v.write_index, v.write_value);
        t.golden_result_read(read, v.read_index);
        t.golden_result_search(search, v.search_value);

        if(to_reset) begin
            result = t.check_reset(read_valid_o, search_valid_o);
            $display("[%0t]\tRESET\t[%10d,%10d] == [%10d,%10d]\t%s", $realtime, 0, 0, read_valid_o, search_valid_o, result ? "PASS": "FAIL");
            pass = pass & result;
        end else begin
            if(read) begin
                result = t.check_read_write(read_value_o, read_valid_o);
                $display("[%0t]\tREAD\t[%10d,%10d] == [%10d,%10d]\t%s", $realtime, t.out_read, t.out_read_valid, read_value_o, read_valid_o, result ? "PASS": "FAIL");
                pass = pass & result;
            end
            
            if(search) begin
                result = t.check_search(search_index_o, search_valid_o);
                $display("[%0t]\tSEARCH\t[%10d,%10d] == [%10d,%10d]\t%s", $realtime, t.out_search, t.out_search_valid, search_index_o, search_valid_o, result ? "PASS": "FAIL");
                pass = pass & result;
            end
        end
        t.clock_tic();
    end
    /* end testing */
    assert (pass) else $fatal(1, "Test failed");
    $finish;
end
endprogram
