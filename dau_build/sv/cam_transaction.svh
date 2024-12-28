`timescale 1ns/1ns

class transaction;

    bit [31:0] out_read;
    bit out_read_valid;

    bit [4:0] out_search;
    bit out_search_valid;

    bit [31:0] cam[32];
    bit cam_valid[32];

    bit [31:0] last;
    bit [4:0] last_index;
    bit last_valid;


    /* this checks that reset functions properly */
    function bit check_reset(bit read_valid_o, bit search_valid_o);
        int i;
        for(i=0;i<32;i=i+1) begin
            cam[i] = 0;
            cam_valid[i] = 0;
            out_search = 0;
            out_read = 0;
        end
        /* there is nothing to check from the output of the
         * hardware to ensure that reset was functional,
         * since both read_valid_o and search_valid_o are
         * both combinational outputs. Therefore the validity
         * of reset can only be confirmed by randomly interspersing
         * it with writes and reads. However, since we built the
         * testbench incrementally, and we AND both these outputs
         * with ~reset, we use these to confirm that the function
         * here is getting called.
         */
        return((read_valid_o == 0 ) && (search_valid_o == 0));
    endfunction


    /*
     * run a write operation NOTE: requires clock_tic before
     * it can be applied, to simulate sequential nature of logic
     */
    function void golden_result_write(bit v, bit [4:0] index, int value);
        if(v) begin
            last = cam[index];
            last_index = index;
            last_valid = cam_valid[index];
            cam[index] = value;
            cam_valid[index] = 1;
        end
    endfunction

    function void clock_tic();
        last = -1;
        last_index = -1;
    endfunction

    /* calulate golden output of a read op */
    function void golden_result_read(bit v, bit [4:0] index);
        if(v) begin
            // if(index == last_index) begin
            //     out_read = last;
            //     out_read_valid = last_valid;
            // end else begin
                out_read = cam[index];
                out_read_valid = cam_valid[index];
            // end
        end
    endfunction

    /* calulate the golden output of a search */
    function void golden_result_search(bit v, int value);
        if(v) begin
            bit [4:0] i = 0;
            int found = 0;
            for(i = 0; i<=31; i=i+1) begin
                if((cam[i] == value) && (cam_valid[i] == 1)) begin
                    found = 1;
                    break;
                end
                if (i==31) begin
                    break;
                end
            end
            if(found == 1) begin
                out_search = i;
                out_search_valid = 1;
            end else begin
                out_search_valid = 0;
            end
        end
    endfunction

    /* check if write/read functions correctly */
    function bit check_read_write(int value, bit valid);
        bit ret;
        ret = (valid == out_read_valid);
        if(out_read_valid == 1) begin
            ret = ret && (value == out_read);
        end
        return ret;
    endfunction

    /* check if search functions correctly */
    function bit check_search(bit [4:0] index, bit valid);
        bit ret;
        ret = (valid == out_search_valid);
        if(out_search_valid == 1) begin
            ret = ret && (index == out_search);
        end
        return ret;
    endfunction

endclass
