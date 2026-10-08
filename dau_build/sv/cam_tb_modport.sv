`timescale 1ns/1ns
`include "cam_transaction.svh"
`include "cam_testing_env.svh"

/* the testbench */
program cam_tb_modport(cam_ifc.bench ds);
transaction t;
testing_env v;

bit read;
bit write;
bit search;
bit reset;
bit pass;
bit result;

initial begin
    t = new();
    v = new();
    v.read_config("config.txt");
    pass = 1;

    /* flush hardware */
    repeat(2) begin
        ds.reset = 1'b1;
        @(posedge ds.clk);
    end
    /* end flush */

    /* begin testing */
    repeat(v.iter) begin
        int _ = v.randomize();

        //decide to read, write, search, or reset
        read = v.get_read();
        write = v.get_write();
        search = v.get_search();
        reset = v.get_reset();

        // drive inputs for next cycle
        if(reset) begin
            ds.reset = 1'b1;
        end else begin
            ds.reset = 1'b0;
            if(read) begin
                ds.read_i = 1'b1;
                ds.read_index_i = v.read_index;
                $display("[%0t]\tREAD\t[%10d]", $realtime, v.read_index);
            end else begin
                ds.read_i = 1'b0;
            end
            if(write) begin
                ds.write_i = 1'b1;
                ds.write_index_i = v.write_index;
                ds.write_data_i = v.write_value;
                $display("[%0t]\tWRITE\t[%10d,%10d]", $realtime, v.write_index, v.write_value);
            end else begin
                ds.write_i = 1'b0;
            end
            if(search) begin
                ds.search_i = 1'b1;
                ds.search_data_i = v.search_value;
                $display("[%0t]\tSEARCH\t[%10d]", $realtime, v.search_value);
            end else begin
                ds.search_i = 1'b0;
            end
        end

        @(posedge ds.clk);

        //golden results
        t.golden_result_write(write ,v.write_index, v.write_value);
        t.golden_result_read(read, v.read_index);
        t.golden_result_search(search, v.search_value);

        if(reset) begin
            result = t.check_reset(ds.read_valid_o, ds.search_valid_o);
            $display("[%0t]\tRESET\t[%10d,%10d] == [%10d,%10d]\t%s", $realtime, 0, 0, ds.read_valid_o, ds.search_valid_o, result ? "PASS": "FAIL");
            pass = pass & result;
        end else begin
            if(read) begin
                result = t.check_read_write(ds.read_value_o, ds.read_valid_o);
                $display("[%0t]\tREAD\t[%10d,%10d] == [%10d,%10d]\t%s", $realtime, t.out_read, t.out_read_valid, ds.read_value_o, ds.read_valid_o, result ? "PASS": "FAIL");
                pass = pass & result;
            end
            
            if(search) begin
                result = t.check_search(ds.search_index_o, ds.search_valid_o);
                $display("[%0t]\tSEARCH\t[%10d,%10d] == [%10d,%10d]\t%s", $realtime, t.out_search, t.out_search_valid, ds.search_index_o, ds.search_valid_o, result ? "PASS": "FAIL");
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
