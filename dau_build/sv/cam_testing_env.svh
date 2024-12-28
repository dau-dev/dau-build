`timescale 1ns/1ns

/* these are used to determine how frequently
 * to run a read/write/search/reset op
 */
class testing_env;
    rand int unsigned rn;

    rand int unsigned write_value;
    rand logic[4:0] write_index;
    rand logic[4:0] read_index;
    rand int unsigned search_value;

    bit read;
    bit write;
    bit search;
    bit reset;

    int read_prob;
    int write_prob;
    int search_prob;
    int reset_prob;

    int iter;

    function new ();
        rn = 'b0;
        write_value = 'b0;
        write_index = 'b0;
        read_index = 'b0;
        search_value = 'b0;
    
        read = 'b0;
        write = 'b0;
        search = 'b0;
        reset = 'b0;
    
        read_prob = 20;
        write_prob = 10;
        search_prob = 20;
        reset_prob = 1;
    
        iter = 10000;
    endfunction

    function void read_config(string filename);
        int file, chars_returned, seed, value;
        string param;
        file = $fopen(filename, "r");

        while(!$feof(file)) begin
            chars_returned = $fscanf(file, "%s %d", param, value);
            if("RANDOM_SEED" == param) begin
                seed = value;
                // $srandom(seed);
            end else if("ITERATIONS" == param) begin
                iter = value;
            end else if("READ_PROB" == param) begin
                read_prob = value;
            end else if("WRITE_PROB" == param) begin
                write_prob = value;
            end else if("SEARCH_PROB" == param) begin
                search_prob = value;
            end else if("RESET_PROB" == param) begin
                reset_prob = value;
            end
        end
    endfunction


    /* these all have granularity of
     * tenths of a percent, see ff_tb
     * for more details
     */
    function bit get_read();
        return((rn%100)<read_prob);
    endfunction

    function bit get_write();
        return((rn%100)<write_prob);
    endfunction

    function bit get_search();
        return((rn%100)<search_prob);
    endfunction

    function bit get_reset();
        return((rn%100)<reset_prob);
    endfunction

endclass
