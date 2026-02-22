`timescale 1ns / 1ps
//
// doubler_engine.sv - AXI DMA Engine for Double-Precision Multiply-by-2
//
// Self-contained engine that:
//   1. Accepts configuration via AXI4-Lite slave (src addr, dst addr, count, go)
//   2. Reads 64-bit doubles from DDR via AXI4 master
//   3. Streams each double through an internal `doubler` (AXI-Stream)
//   4. Writes the doubled result back to DDR via the same AXI4 master
//   5. Signals completion via a status register
//
// Register Map (AXI4-Lite slave, 32-bit registers):
//   0x00  SRC_ADDR_LO   RW   Source address low 32 bits
//   0x04  SRC_ADDR_HI   RW   Source address high 32 bits
//   0x08  DST_ADDR_LO   RW   Destination address low 32 bits
//   0x0C  DST_ADDR_HI   RW   Destination address high 32 bits
//   0x10  NUM_DOUBLES   RW   Number of doubles to process (max 2^32 - 1)
//   0x14  CONTROL       RW   bit[0] = go (write 1 to start, self-clearing)
//   0x18  STATUS        RO   bit[0] = done, bit[1] = busy
//   0x1C  VERSION       RO   0x0001_0000
//

/* verilator lint_off UNUSEDSIGNAL */
module doubler_engine #(
    parameter int ADDR_WIDTH = 32,
    parameter int AXI_ID_WIDTH = 1
) (
    input  logic aclk,
    input  logic aresetn,

    // ================================================================
    // AXI4-Lite Slave  (control registers)
    // ================================================================
    input  logic [4:0]          s_axi_awaddr,
    input  logic [2:0]          s_axi_awprot,
    input  logic                s_axi_awvalid,
    output logic                s_axi_awready,

    input  logic [31:0]         s_axi_wdata,
    input  logic [3:0]          s_axi_wstrb,
    input  logic                s_axi_wvalid,
    output logic                s_axi_wready,

    output logic [1:0]          s_axi_bresp,
    output logic                s_axi_bvalid,
    input  logic                s_axi_bready,

    input  logic [4:0]          s_axi_araddr,
    input  logic [2:0]          s_axi_arprot,
    input  logic                s_axi_arvalid,
    output logic                s_axi_arready,

    output logic [31:0]         s_axi_rdata,
    output logic [1:0]          s_axi_rresp,
    output logic                s_axi_rvalid,
    input  logic                s_axi_rready,

    // ================================================================
    // AXI4 Full Master  (DDR memory access, 64-bit data)
    // ================================================================
    output logic [AXI_ID_WIDTH-1:0] m_axi_awid,
    output logic [ADDR_WIDTH-1:0]   m_axi_awaddr,
    output logic [7:0]              m_axi_awlen,
    output logic [2:0]              m_axi_awsize,
    output logic [1:0]              m_axi_awburst,
    output logic                    m_axi_awlock,
    output logic [3:0]              m_axi_awcache,
    output logic [2:0]              m_axi_awprot,
    output logic [3:0]              m_axi_awqos,
    output logic                    m_axi_awvalid,
    input  logic                    m_axi_awready,

    output logic [63:0]             m_axi_wdata,
    output logic [7:0]              m_axi_wstrb,
    output logic                    m_axi_wlast,
    output logic                    m_axi_wvalid,
    input  logic                    m_axi_wready,

    input  logic [AXI_ID_WIDTH-1:0] m_axi_bid,
    input  logic [1:0]              m_axi_bresp,
    input  logic                    m_axi_bvalid,
    output logic                    m_axi_bready,

    output logic [AXI_ID_WIDTH-1:0] m_axi_arid,
    output logic [ADDR_WIDTH-1:0]   m_axi_araddr,
    output logic [7:0]              m_axi_arlen,
    output logic [2:0]              m_axi_arsize,
    output logic [1:0]              m_axi_arburst,
    output logic                    m_axi_arlock,
    output logic [3:0]              m_axi_arcache,
    output logic [2:0]              m_axi_arprot,
    output logic [3:0]              m_axi_arqos,
    output logic                    m_axi_arvalid,
    input  logic                    m_axi_arready,

    input  logic [AXI_ID_WIDTH-1:0] m_axi_rid,
    input  logic [63:0]             m_axi_rdata,
    input  logic [1:0]              m_axi_rresp,
    input  logic                    m_axi_rlast,
    input  logic                    m_axi_rvalid,
    output logic                    m_axi_rready
);

    // ================================================================
    // Constants
    // ================================================================
    localparam logic [31:0] VERSION = 32'h0001_0000;

    // ================================================================
    // Control Registers
    // ================================================================
    logic [31:0] reg_src_addr_lo;
    logic [31:0] reg_src_addr_hi;
    logic [31:0] reg_dst_addr_lo;
    logic [31:0] reg_dst_addr_hi;
    logic [31:0] reg_num_doubles;
    logic        reg_go;
    logic        reg_done;
    logic        reg_busy;

    // ================================================================
    // Internal AXI-Stream wires (to/from doubler)
    // ================================================================
    logic [63:0] axis_in_tdata;
    logic        axis_in_tvalid;
    logic        axis_in_tready;
    logic        axis_in_tlast;

    logic [63:0] axis_out_tdata;
    logic        axis_out_tvalid;
    logic        axis_out_tready;
    logic        axis_out_tlast;

    // ================================================================
    // Doubler instance
    // ================================================================
    doubler u_doubler (
        .aclk           (aclk),
        .aresetn        (aresetn),
        .s_axis_tdata   (axis_in_tdata),
        .s_axis_tvalid  (axis_in_tvalid),
        .s_axis_tready  (axis_in_tready),
        .s_axis_tlast   (axis_in_tlast),
        .m_axis_tdata   (axis_out_tdata),
        .m_axis_tvalid  (axis_out_tvalid),
        .m_axis_tready  (axis_out_tready),
        .m_axis_tlast   (axis_out_tlast)
    );

    // ================================================================
    // AXI4-Lite Slave : Register Read/Write
    // ================================================================
    // Write path: accept AW+W together, generate B
    logic        aw_accepted;
    logic        w_accepted;
    logic [4:0]  aw_addr_latched;

    always_ff @(posedge aclk) begin
        if (!aresetn) begin
            aw_accepted     <= 1'b0;
            w_accepted      <= 1'b0;
            aw_addr_latched <= 5'b0;
            s_axi_awready   <= 1'b0;
            s_axi_wready    <= 1'b0;
            s_axi_bvalid    <= 1'b0;
            s_axi_bresp     <= 2'b00;
            reg_src_addr_lo <= 32'h0;
            reg_src_addr_hi <= 32'h0;
            reg_dst_addr_lo <= 32'h0;
            reg_dst_addr_hi <= 32'h0;
            reg_num_doubles <= 32'h0;
            reg_go          <= 1'b0;
        end else begin
            // Default: deassert readies
            s_axi_awready <= 1'b0;
            s_axi_wready  <= 1'b0;

            // Self-clear go after one cycle
            if (reg_go && reg_busy)
                reg_go <= 1'b0;

            // Accept AW
            if (s_axi_awvalid && !aw_accepted && !s_axi_bvalid) begin
                s_axi_awready   <= 1'b1;
                aw_accepted     <= 1'b1;
                aw_addr_latched <= s_axi_awaddr;
            end

            // Accept W
            if (s_axi_wvalid && !w_accepted && !s_axi_bvalid) begin
                s_axi_wready <= 1'b1;
                w_accepted   <= 1'b1;
            end

            // Both accepted -> write the register, generate B
            if (aw_accepted && w_accepted) begin
                aw_accepted <= 1'b0;
                w_accepted  <= 1'b0;
                s_axi_bvalid <= 1'b1;
                s_axi_bresp  <= 2'b00; // OKAY

                case (aw_addr_latched[4:2])
                    3'd0: reg_src_addr_lo <= s_axi_wdata;
                    3'd1: reg_src_addr_hi <= s_axi_wdata;
                    3'd2: reg_dst_addr_lo <= s_axi_wdata;
                    3'd3: reg_dst_addr_hi <= s_axi_wdata;
                    3'd4: reg_num_doubles <= s_axi_wdata;
                    3'd5: reg_go          <= s_axi_wdata[0];
                    default: ; // ignore writes to read-only / reserved
                endcase
            end

            // B handshake
            if (s_axi_bvalid && s_axi_bready)
                s_axi_bvalid <= 1'b0;
        end
    end

    // Read path
    logic ar_accepted;
    logic [4:0] ar_addr_latched;

    always_ff @(posedge aclk) begin
        if (!aresetn) begin
            s_axi_arready   <= 1'b0;
            s_axi_rvalid    <= 1'b0;
            s_axi_rdata     <= 32'h0;
            s_axi_rresp     <= 2'b00;
            ar_accepted      <= 1'b0;
            ar_addr_latched  <= 5'b0;
        end else begin
            s_axi_arready <= 1'b0;

            // Accept AR
            if (s_axi_arvalid && !ar_accepted && !s_axi_rvalid) begin
                s_axi_arready   <= 1'b1;
                ar_accepted     <= 1'b1;
                ar_addr_latched <= s_axi_araddr;
            end

            // Generate R
            if (ar_accepted) begin
                ar_accepted  <= 1'b0;
                s_axi_rvalid <= 1'b1;
                s_axi_rresp  <= 2'b00;

                case (ar_addr_latched[4:2])
                    3'd0: s_axi_rdata <= reg_src_addr_lo;
                    3'd1: s_axi_rdata <= reg_src_addr_hi;
                    3'd2: s_axi_rdata <= reg_dst_addr_lo;
                    3'd3: s_axi_rdata <= reg_dst_addr_hi;
                    3'd4: s_axi_rdata <= reg_num_doubles;
                    3'd5: s_axi_rdata <= {31'b0, reg_go};
                    3'd6: s_axi_rdata <= {30'b0, reg_busy, reg_done};
                    3'd7: s_axi_rdata <= VERSION;
                endcase
            end

            // R handshake
            if (s_axi_rvalid && s_axi_rready)
                s_axi_rvalid <= 1'b0;
        end
    end

    // ================================================================
    // DMA Engine FSM
    // ================================================================
    typedef enum logic [3:0] {
        ST_IDLE,
        ST_RD_ADDR,
        ST_RD_DATA,
        ST_PROCESS,
        ST_CAPTURE,
        ST_WR_ADDR,
        ST_WR_DATA,
        ST_WR_RESP,
        ST_ADVANCE,
        ST_DONE
    } state_t;

    state_t state;

    logic [ADDR_WIDTH-1:0] src_addr;
    logic [ADDR_WIDTH-1:0] dst_addr;
    logic [31:0]           elem_count;
    logic [31:0]           elem_index;
    logic [63:0]           read_data;
    logic [63:0]           doubled_data;

    // Combinational address computation
    logic [ADDR_WIDTH-1:0] cur_rd_addr;
    logic [ADDR_WIDTH-1:0] cur_wr_addr;
    assign cur_rd_addr = src_addr + ADDR_WIDTH'({elem_index, 3'b000});
    assign cur_wr_addr = dst_addr + ADDR_WIDTH'({elem_index, 3'b000});

    // AXI4 master - constant/default signal assignments
    assign m_axi_awid    = '0;
    assign m_axi_awlen   = 8'h00;        // single beat
    assign m_axi_awsize  = 3'b011;       // 8 bytes
    assign m_axi_awburst = 2'b01;        // INCR
    assign m_axi_awlock  = 1'b0;
    assign m_axi_awcache = 4'b0011;      // bufferable
    assign m_axi_awprot  = 3'b000;
    assign m_axi_awqos   = 4'b0000;
    assign m_axi_wstrb   = 8'hFF;        // all bytes valid
    assign m_axi_wlast   = 1'b1;         // always last (single beat)

    assign m_axi_arid    = '0;
    assign m_axi_arlen   = 8'h00;        // single beat
    assign m_axi_arsize  = 3'b011;       // 8 bytes
    assign m_axi_arburst = 2'b01;        // INCR
    assign m_axi_arlock  = 1'b0;
    assign m_axi_arcache = 4'b0011;
    assign m_axi_arprot  = 3'b000;
    assign m_axi_arqos   = 4'b0000;

    // FSM
    always_ff @(posedge aclk) begin
        if (!aresetn) begin
            state         <= ST_IDLE;
            reg_done      <= 1'b0;
            reg_busy      <= 1'b0;
            elem_index    <= 32'h0;
            elem_count    <= 32'h0;
            src_addr      <= '0;
            dst_addr      <= '0;
            read_data     <= 64'h0;
            doubled_data  <= 64'h0;

            m_axi_arvalid <= 1'b0;
            m_axi_araddr  <= '0;
            m_axi_rready  <= 1'b0;
            m_axi_awvalid <= 1'b0;
            m_axi_awaddr  <= '0;
            m_axi_wvalid  <= 1'b0;
            m_axi_wdata   <= 64'h0;
            m_axi_bready  <= 1'b0;

            axis_in_tdata  <= 64'h0;
            axis_in_tvalid <= 1'b0;
            axis_in_tlast  <= 1'b0;
            axis_out_tready <= 1'b0;
        end else begin
            case (state)
                // ------------------------------------------------
                ST_IDLE: begin
                    reg_busy <= 1'b0;
                    if (reg_go) begin
                        reg_done   <= 1'b0;
                        reg_busy   <= 1'b1;
                        elem_index <= 32'h0;
                        elem_count <= reg_num_doubles;
                        src_addr   <= reg_src_addr_lo[ADDR_WIDTH-1:0];
                        dst_addr   <= reg_dst_addr_lo[ADDR_WIDTH-1:0];
                        state      <= ST_RD_ADDR;
                    end
                end

                // ------------------------------------------------
                // Issue AXI read address
                ST_RD_ADDR: begin
                    m_axi_arvalid <= 1'b1;
                    m_axi_araddr  <= cur_rd_addr;
                    if (m_axi_arvalid && m_axi_arready) begin
                        m_axi_arvalid <= 1'b0;
                        state         <= ST_RD_DATA;
                    end
                end

                // ------------------------------------------------
                // Receive AXI read data
                ST_RD_DATA: begin
                    m_axi_rready <= 1'b1;
                    if (m_axi_rvalid && m_axi_rready) begin
                        read_data    <= m_axi_rdata;
                        m_axi_rready <= 1'b0;
                        state        <= ST_PROCESS;
                    end
                end

                // ------------------------------------------------
                // Feed double into the AXI-Stream doubler
                ST_PROCESS: begin
                    axis_in_tdata  <= read_data;
                    axis_in_tvalid <= 1'b1;
                    axis_in_tlast  <= (elem_index == elem_count - 1);
                    if (axis_in_tvalid && axis_in_tready) begin
                        axis_in_tvalid <= 1'b0;
                        state          <= ST_CAPTURE;
                    end
                end

                // ------------------------------------------------
                // Capture doubled result from AXI-Stream master
                ST_CAPTURE: begin
                    axis_out_tready <= 1'b1;
                    if (axis_out_tvalid && axis_out_tready) begin
                        doubled_data    <= axis_out_tdata;
                        axis_out_tready <= 1'b0;
                        state           <= ST_WR_ADDR;
                    end
                end

                // ------------------------------------------------
                // Issue AXI write address
                ST_WR_ADDR: begin
                    m_axi_awvalid <= 1'b1;
                    m_axi_awaddr  <= cur_wr_addr;
                    if (m_axi_awvalid && m_axi_awready) begin
                        m_axi_awvalid <= 1'b0;
                        state         <= ST_WR_DATA;
                    end
                end

                // ------------------------------------------------
                // Issue AXI write data
                ST_WR_DATA: begin
                    m_axi_wvalid <= 1'b1;
                    m_axi_wdata  <= doubled_data;
                    if (m_axi_wvalid && m_axi_wready) begin
                        m_axi_wvalid <= 1'b0;
                        state        <= ST_WR_RESP;
                    end
                end

                // ------------------------------------------------
                // Wait for write response
                ST_WR_RESP: begin
                    m_axi_bready <= 1'b1;
                    if (m_axi_bvalid && m_axi_bready) begin
                        m_axi_bready <= 1'b0;
                        state        <= ST_ADVANCE;
                    end
                end

                // ------------------------------------------------
                // Advance to next element or finish
                ST_ADVANCE: begin
                    elem_index <= elem_index + 1;
                    if (elem_index + 1 >= elem_count)
                        state <= ST_DONE;
                    else
                        state <= ST_RD_ADDR;
                end

                // ------------------------------------------------
                ST_DONE: begin
                    reg_done <= 1'b1;
                    reg_busy <= 1'b0;
                    state    <= ST_IDLE;
                end

                default: state <= ST_IDLE;
            endcase
        end
    end

endmodule
/* verilator lint_on UNUSEDSIGNAL */
