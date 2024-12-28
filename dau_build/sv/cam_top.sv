`timescale 1ns/1ns

module cam_top (input bit clk);
   cam_ifc IFC(clk); // instantiate the interface file
   cam dut (
      IFC.dut.clk,
      IFC.dut.reset,
      IFC.dut.read_i,
      IFC.dut.read_index_i,
      IFC.dut.write_i,
      IFC.dut.write_index_i,
      IFC.dut.write_data_i,
      IFC.dut.search_i,
      IFC.dut.search_data_i,
      IFC.dut.read_valid_o,
      IFC.dut.read_value_o,
      IFC.dut.search_valid_o,
      IFC.dut.search_index_o
   );
   cam_tb bench (
      IFC.bench.clk,
      IFC.bench.reset,
      IFC.bench.read_i,
      IFC.bench.read_index_i,
      IFC.bench.write_i,
      IFC.bench.write_index_i,
      IFC.bench.write_data_i,
      IFC.bench.search_i,
      IFC.bench.search_data_i,
      IFC.bench.read_valid_o,
      IFC.bench.read_value_o,
      IFC.bench.search_valid_o,
      IFC.bench.search_index_o
   );
   // cam_tb_modport bench (IFC.bench);
endmodule
