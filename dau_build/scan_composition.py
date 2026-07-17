"""Generic scan-composition shell-top generator.

A scan composition is the one-scan-N-lanes shape: an AXI burst reader scans
the input window once and fans the row stream to N lanes — each an optional
partition filter feeding an ordered chain of operator stages into a
terminal operator tile and a record writer with its own output-address
register — behind an AXI-Lite register aperture. The walker
consumes plain data (module names, config-port bindings, register offsets)
and emits the plain-Verilog top (Vivado block-design module references
reject SystemVerilog tops), so it carries no registry and no private
imports: callers describe their composition as a ``ScanComposition`` and
optionally hand over the tiles' HDL sources for slang-backed interface
validation (``dau_build.sv_contract``) before emission.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ccflow import BaseModel

__all__ = (
    "LaneTile",
    "RegisterLayout",
    "ScanComposition",
    "ScanCompositionError",
    "TileInstance",
    "generate_scan_composition_sim_sv",
    "generate_scan_composition_top_sv",
)


class ScanCompositionError(ValueError):
    """The composition is not emittable (shape or interface violation)."""


class TileInstance(BaseModel):
    """One HDL module plus its config-port bindings (SystemVerilog literals
    or expressions in terms of the top's signals)."""

    module: str
    config: dict[str, str] = {}


class LaneTile(TileInstance):
    """One lane of the scan: an operator tile (with the name of its trailing
    status-counter port) behind an optional row-atomic partition filter and
    an optional ordered ``chain`` of mid-lane operator stages
    (filter -> map -> ... -> terminal tile). Chain stages speak the same
    stream+status contract but need no count port; their statuses feed the
    lane's status mux upstream-first, so a mid-chain close-out (zero rows,
    torn row, bad config) wins over the terminal tile's."""

    count_port: str
    partition: TileInstance | None = None
    chain: tuple[TileInstance, ...] = ()


class RegisterLayout(BaseModel):
    """Window-relative register offsets the generated top decodes (only
    addr[11:0] selects a register: the window sits at a 4 KiB-aligned BAR
    offset). Defaults match the DAU stream-job register contract and its
    broadcast-NoC lane register block: one ``lane_stride``-sized window per
    lane starting at ``lane_base``, lane registers relative to the lane
    window."""

    last_error: int = 0x02C
    job_control: int = 0x050
    job_status: int = 0x054
    input_address_low: int = 0x058
    input_length_low: int = 0x060
    lane_base: int = 0x100
    lane_stride: int = 0x20
    lane_output_address_low: int = 0x00
    lane_result_length_low: int = 0x04
    lane_record_count_low: int = 0x08
    lane_record_count_high: int = 0x0C
    lane_error: int = 0x10

    def lane_register(self, lane: int, offset: int) -> int:
        """Window offset of one lane's register."""
        return self.lane_base + lane * self.lane_stride + offset


class ScanComposition(BaseModel):
    """One scan fanned to ``len(lanes)`` lanes with per-lane output regions
    and the lane register block.

    Two fan-out shapes: the default broadcasts the scan to every lane (each
    lane's optional ``partition`` filter selects its rows), or a shared
    ``partitioner`` routes each row to exactly one lane — lanes then carry
    no per-lane partition."""

    name: str
    module_name: str
    lanes: tuple[LaneTile, ...]
    partitioner: TileInstance | None = None
    burst_beats: int = 32
    addr_width: int = 32
    # capability words the identity block advertises (register map 0.2).
    # These are caller-computed data — the walker never guesses them: the
    # bitmaps default to zero ("advertise nothing") so a composition only
    # advertises what its composer declared, and the lane-count word is
    # always the composed lane count.
    operator_bitmap: int = 0x0000_0000
    host_opcode_bitmap: int = 0x0000_0000
    sort_capacity: int = 0
    registers: RegisterLayout = RegisterLayout()

    def model_post_init(self, context) -> None:
        if not self.lanes:
            raise ScanCompositionError("a scan composition needs at least one lane")
        if self.partitioner is not None:
            for lane in self.lanes:
                if lane.partition is not None:
                    raise ScanCompositionError(f"lane tile {lane.module!r} carries a partition filter but the scan already has a shared partitioner")


_DEFAULT_GENERATED_BY = "dau_build.scan_composition.generate_scan_composition_top_sv"


def _s_axi_lite_ports_sv() -> str:
    """The AXI-Lite register aperture port block (16-bit BAR-offset
    addressing per the stream-job contract)."""
    return """    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWADDR" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXI, PROTOCOL AXI4LITE, DATA_WIDTH 32, ADDR_WIDTH 16, HAS_BURST 0, HAS_LOCK 0, HAS_PROT 0, HAS_CACHE 0, HAS_QOS 0, HAS_REGION 0, HAS_WSTRB 1, HAS_BRESP 1, HAS_RRESP 1" *)
    input wire [15:0] s_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWVALID" *)
    input wire s_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWREADY" *)
    output reg s_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WDATA" *)
    input wire [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WSTRB" *)
    input wire [3:0] s_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WVALID" *)
    input wire s_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WREADY" *)
    output reg s_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BRESP" *)
    output wire [1:0] s_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BVALID" *)
    output reg s_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BREADY" *)
    input wire s_axi_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARADDR" *)
    input wire [15:0] s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARVALID" *)
    input wire s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARREADY" *)
    output reg s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RDATA" *)
    output reg [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RRESP" *)
    output wire [1:0] s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RVALID" *)
    output reg s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RREADY" *)
    input wire s_axi_rready,"""


def _m_axi_ports_sv(*, addr_width: int, burst_beats: int) -> str:
    """The AXI4 memory-master port block (64-bit data, INCR bursts, no
    narrow bursts)."""
    return f"""    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARADDR" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME M_AXI, PROTOCOL AXI4, DATA_WIDTH 64, ADDR_WIDTH {addr_width}, HAS_BURST 1, HAS_LOCK 0, HAS_PROT 0, HAS_CACHE 0, HAS_QOS 0, HAS_REGION 0, HAS_WSTRB 1, HAS_BRESP 1, HAS_RRESP 1, MAX_BURST_LENGTH {burst_beats}, SUPPORTS_NARROW_BURST 0" *)
    output wire [{addr_width - 1}:0] m_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARLEN" *)
    output wire [7:0] m_axi_arlen,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARSIZE" *)
    output wire [2:0] m_axi_arsize,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARBURST" *)
    output wire [1:0] m_axi_arburst,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARVALID" *)
    output wire m_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARREADY" *)
    input wire m_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI RDATA" *)
    input wire [63:0] m_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI RRESP" *)
    input wire [1:0] m_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI RLAST" *)
    input wire m_axi_rlast,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI RVALID" *)
    input wire m_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI RREADY" *)
    output wire m_axi_rready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI AWADDR" *)
    output wire [{addr_width - 1}:0] m_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI AWLEN" *)
    output wire [7:0] m_axi_awlen,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI AWSIZE" *)
    output wire [2:0] m_axi_awsize,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI AWBURST" *)
    output wire [1:0] m_axi_awburst,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI AWVALID" *)
    output wire m_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI AWREADY" *)
    input wire m_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI WDATA" *)
    output wire [63:0] m_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI WSTRB" *)
    output wire [7:0] m_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI WLAST" *)
    output wire m_axi_wlast,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI WVALID" *)
    output wire m_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI WREADY" *)
    input wire m_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI BRESP" *)
    input wire [1:0] m_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI BVALID" *)
    input wire m_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI BREADY" *)
    output wire m_axi_bready"""


def _register_localparams_sv(offsets: Sequence[tuple[str, int]]) -> str:
    """Window-relative register-decode localparams. Only addr[11:0] selects
    a register: the window sits at a 4 KiB-aligned BAR offset, so full-width
    compares against the raw offsets would never match on hardware."""
    return "\n".join(f"    localparam [11:0] ADDR_{name} = 12'h{value:03X};" for name, value in offsets)


def _axi_lite_decode_wires_sv() -> str:
    """Wire declarations for the shared AXI-Lite register decode."""
    return """    wire write_fire;
    wire read_fire;
    wire [15:0] selected_addr;
    wire [31:0] identity_rdata;
    wire [31:0] reset_request_unused;"""


def _axi_lite_decode_assigns_sv() -> str:
    """Single-outstanding AXI-Lite decode: a write wins over a concurrent
    read; responses are always OKAY."""
    return """    assign write_fire = !s_axi_bvalid && s_axi_awvalid && s_axi_wvalid;
    assign read_fire = !write_fire && !s_axi_rvalid && s_axi_arvalid;
    assign selected_addr = write_fire ? s_axi_awaddr : s_axi_araddr;
    assign s_axi_bresp = 2'b00;
    assign s_axi_rresp = 2'b00;"""


def _identity_registers_instance_sv() -> str:
    """The read-only identity/capability register file that backs the
    default read path (capability words parameterized at the top).

    The caller-supplied ``dau_identity_registers`` module must accept the
    four capability parameters (register map 0.2) — an older identity
    block that only knows OPERATOR_BITMAP rejects the parameter override
    at elaboration."""
    return """    dau_identity_registers #(
        .OPERATOR_BITMAP(OPERATOR_BITMAP),
        .LANE_COUNT(LANE_COUNT),
        .HOST_OPCODE_BITMAP(HOST_OPCODE_BITMAP),
        .SORT_CAPACITY(SORT_CAPACITY)
    ) identity_registers (
        .addr({4'h0, selected_addr[11:0]}),
        .wen(1'b0),
        .wdata(32'd0),
        .reset_request(reset_request_unused),
        .rdata(identity_rdata)
    );"""


def _axi_lite_register_process_sv(
    *,
    write_default_comment: str,
    reset_extra: str = "",
    tick_extra: str = "",
    write_cases_extra: str = "",
    read_cases_extra: str = "",
) -> str:
    """The AXI-Lite register process shared by every register-windowed top:
    handshake reset, the JOB_CONTROL start pulse, and the
    JOB_STATUS/LAST_ERROR status-glue readback. Callers splice in their
    register cases; every extra carries its own trailing newline."""
    return f"""    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn) begin
            s_axi_awready <= 1'b0;
            s_axi_wready <= 1'b0;
            s_axi_bvalid <= 1'b0;
            s_axi_arready <= 1'b0;
            s_axi_rdata <= 32'h0000_0000;
            s_axi_rvalid <= 1'b0;
{reset_extra}        end else begin
            job_start <= 1'b0;
{tick_extra}            s_axi_awready <= write_fire;
            s_axi_wready <= write_fire;
            s_axi_arready <= read_fire;

            if (write_fire) begin
                s_axi_bvalid <= 1'b1;
                case (s_axi_awaddr[11:0])
                    ADDR_JOB_CONTROL: job_start <= s_axi_wdata[0];
{write_cases_extra}                    default: ;  // {write_default_comment}
                endcase
            end else if (s_axi_bvalid && s_axi_bready) begin
                s_axi_bvalid <= 1'b0;
            end

            if (read_fire) begin
                case (s_axi_araddr[11:0])
                    ADDR_JOB_CONTROL: s_axi_rdata <= 32'd0;
                    ADDR_JOB_STATUS: s_axi_rdata <= {{28'd0, job_error, job_done, job_busy, !job_busy}};
                    ADDR_LAST_ERROR: s_axi_rdata <= {{24'd0, job_error_code}};
{read_cases_extra}                    default: s_axi_rdata <= identity_rdata;
                endcase
                s_axi_rvalid <= 1'b1;
            end else if (s_axi_rvalid && s_axi_rready) begin
                s_axi_rvalid <= 1'b0;
            end
        end
    end"""


def _tile_config_binds_sv(config) -> str:
    """Config-port bindings for a tile instance."""
    return "".join(f"        .{port}({value}),\n" for port, value in config.items())


def _lane_front_sv(composition: ScanComposition, i: int, *, clk: str = "s_axi_aclk") -> str:
    """The lane front for lane ``i``: tap the shared partitioner's per-lane
    stream, tap the broadcast directly (filterless lane), or instantiate the
    lane's partition filter off the broadcast."""
    lane = composition.lanes[i]
    if composition.partitioner is not None:
        return f"""    assign filt_out_valid_{i} = part_out_valid[{i}];
    assign part_out_ready[{i}] = filt_out_ready_{i};
    assign filt_out_data_{i} = part_out_data[{64 * (i + 1) - 1}:{64 * i}];
    assign filt_out_last_{i} = part_out_last[{i}];
    assign filt_status_valid_{i} = part_status_valid[{i}];
    assign filt_status_error_{i} = part_status_error[{i}];
    assign filt_status_error_code_{i} = part_status_error_code[{8 * (i + 1) - 1}:{8 * i}];
    assign part_status_ready[{i}] = filt_status_ready_{i};
"""
    if lane.partition is None:
        return f"""    assign filt_out_valid_{i} = bcast_valid[{i}];
    assign bcast_ready[{i}] = filt_out_ready_{i};
    assign filt_out_data_{i} = bcast_data;
    assign filt_out_last_{i} = bcast_last;
    assign filt_status_valid_{i} = 1'b0;
    assign filt_status_error_{i} = 1'b0;
    assign filt_status_error_code_{i} = 8'd0;
"""
    return f"""    {lane.partition.module} partition_{i} (
        .clk({clk}),
        .rst(lane_rst),
{_tile_config_binds_sv(lane.partition.config)}        .input_valid(bcast_valid[{i}]),
        .input_ready(bcast_ready[{i}]),
        .input_data(bcast_data),
        .input_last(bcast_last),
        .output_valid(filt_out_valid_{i}),
        .output_ready(filt_out_ready_{i}),
        .output_data(filt_out_data_{i}),
        .output_last(filt_out_last_{i}),
        .status_valid(filt_status_valid_{i}),
        .status_ready(filt_status_ready_{i}),
        .status_error(filt_status_error_{i}),
        .status_error_code(filt_status_error_code_{i})
    );
"""


def _lane_status_glue_sv(composition: ScanComposition, i: int) -> str:
    """The per-unit status mux for lane ``i``: a filterless lane forwards
    the tile status; a filtered lane muxes the partition status (which
    wins) over the tile status; a chained lane muxes every stage's status
    upstream-first (the most upstream pending status wins, and each stage's
    ready fires only when nothing upstream of it is pending)."""
    lane = composition.lanes[i]
    if lane.chain:
        front_filtered = lane.partition is not None or composition.partitioner is not None
        stems = (["filt"] if front_filtered else []) + [f"chain{j}" for j in range(len(lane.chain))]
        valids = " || ".join(f"{stem}_status_valid_{i}" for stem in stems)
        error_mux = f"tile_status_error_{i}"
        code_mux = f"tile_status_error_code_{i}"
        for stem in reversed(stems):
            error_mux = f"{stem}_status_valid_{i} ? {stem}_status_error_{i} : {error_mux}"
            code_mux = f"{stem}_status_valid_{i} ? {stem}_status_error_code_{i} : {code_mux}"
        lines = [
            f"    assign unit_status_valid_{i} = tile_status_valid_{i} || {valids};",
            f"    assign unit_status_error_{i} = {error_mux};",
            f"    assign unit_status_error_code_{i} = {code_mux};",
        ]
        upstream: list[str] = []
        for stem in stems:
            gate = "".join(f" && !{name}_status_valid_{i}" for name in upstream)
            lines.append(f"    assign {stem}_status_ready_{i} = unit_status_ready_{i}{gate} && {stem}_status_valid_{i};")
            upstream.append(stem)
        gate = "".join(f" && !{name}_status_valid_{i}" for name in upstream)
        lines.append(f"    assign tile_status_ready_{i} = unit_status_ready_{i}{gate};")
        return "\n".join(lines) + "\n"
    if lane.partition is None and composition.partitioner is None:
        return f"""    assign unit_status_valid_{i} = tile_status_valid_{i};
    assign unit_status_error_{i} = tile_status_error_{i};
    assign unit_status_error_code_{i} = tile_status_error_code_{i};
    assign tile_status_ready_{i} = unit_status_ready_{i};
"""
    return f"""    assign unit_status_valid_{i} = tile_status_valid_{i} || filt_status_valid_{i};
    assign unit_status_error_{i} = filt_status_valid_{i} ? filt_status_error_{i} : tile_status_error_{i};
    assign unit_status_error_code_{i} = filt_status_valid_{i} ? filt_status_error_code_{i} : tile_status_error_code_{i};
    assign filt_status_ready_{i} = unit_status_ready_{i} && filt_status_valid_{i};
    assign tile_status_ready_{i} = unit_status_ready_{i} && !filt_status_valid_{i};
"""


def _lane_chain_wire_decls_sv(composition: ScanComposition, i: int) -> str:
    """Per-chain-stage wire declarations for lane ``i`` (empty for a
    chainless lane, keeping the chainless emission byte-identical)."""
    return "".join(
        f"""    wire chain{j}_out_valid_{i};
    wire chain{j}_out_ready_{i};
    wire [63:0] chain{j}_out_data_{i};
    wire chain{j}_out_last_{i};
    wire chain{j}_status_valid_{i};
    wire chain{j}_status_ready_{i};
    wire chain{j}_status_error_{i};
    wire [7:0] chain{j}_status_error_code_{i};
"""
        for j in range(len(composition.lanes[i].chain))
    )


def _lane_wire_decls_sv(composition: ScanComposition) -> str:
    """Per-lane internal wire declarations (lane front, chain stages, tile,
    status glue, writer, and the latched count register)."""
    addr_width = composition.addr_width
    return "\n".join(
        f"""    wire filt_out_valid_{i};
    wire filt_out_ready_{i};
    wire [63:0] filt_out_data_{i};
    wire filt_out_last_{i};
    wire filt_status_valid_{i};
    wire filt_status_ready_{i};
    wire filt_status_error_{i};
    wire [7:0] filt_status_error_code_{i};
{_lane_chain_wire_decls_sv(composition, i)}    wire tile_out_valid_{i};
    wire tile_out_ready_{i};
    wire [63:0] tile_out_data_{i};
    wire tile_out_last_{i};
    wire tile_status_valid_{i};
    wire tile_status_ready_{i};
    wire tile_status_error_{i};
    wire [7:0] tile_status_error_code_{i};
    wire [63:0] tile_bar_count_{i};
    wire unit_status_valid_{i};
    wire unit_status_ready_{i};
    wire unit_status_error_{i};
    wire [7:0] unit_status_error_code_{i};
    wire writer_busy_{i};
    wire writer_done_{i};
    wire writer_error_{i};
    wire [7:0] writer_error_code_{i};
    wire [31:0] lane_result_length_{i};
    wire [{addr_width - 1}:0] wr_awaddr_{i};
    wire [7:0] wr_awlen_{i};
    wire wr_awvalid_{i};
    wire wr_awready_{i};
    wire [63:0] wr_wdata_{i};
    wire wr_wlast_{i};
    wire wr_wvalid_{i};
    wire wr_wready_{i};
    wire wr_bvalid_{i};
    wire wr_bready_{i};
    reg [63:0] lane_bar_count_{i};"""
        for i in range(len(composition.lanes))
    )


def _wr_flat_decls_sv(composition: ScanComposition) -> str:
    """The flattened per-lane write-channel bundles toward the write mux."""
    num_lanes = len(composition.lanes)
    addr_width = composition.addr_width
    return f"""    wire [1:0] wr_bresp;
    wire [{num_lanes * addr_width - 1}:0] wr_awaddr_flat;
    wire [{num_lanes * 8 - 1}:0] wr_awlen_flat;
    wire [{num_lanes - 1}:0] wr_awvalid_flat;
    wire [{num_lanes - 1}:0] wr_awready_flat;
    wire [{num_lanes * 64 - 1}:0] wr_wdata_flat;
    wire [{num_lanes - 1}:0] wr_wlast_flat;
    wire [{num_lanes - 1}:0] wr_wvalid_flat;
    wire [{num_lanes - 1}:0] wr_wready_flat;
    wire [{num_lanes - 1}:0] wr_bvalid_flat;
    wire [{num_lanes - 1}:0] wr_bready_flat;"""


def _lane_flat_assigns_sv(composition: ScanComposition) -> str:
    """Per-lane write-channel taps into the flattened mux bundles."""
    addr_width = composition.addr_width
    return "\n".join(
        f"""    assign wr_awaddr_flat[{addr_width * (i + 1) - 1}:{addr_width * i}] = wr_awaddr_{i};
    assign wr_awlen_flat[{8 * (i + 1) - 1}:{8 * i}] = wr_awlen_{i};
    assign wr_awvalid_flat[{i}] = wr_awvalid_{i};
    assign wr_awready_{i} = wr_awready_flat[{i}];
    assign wr_wdata_flat[{64 * (i + 1) - 1}:{64 * i}] = wr_wdata_{i};
    assign wr_wlast_flat[{i}] = wr_wlast_{i};
    assign wr_wvalid_flat[{i}] = wr_wvalid_{i};
    assign wr_wready_{i} = wr_wready_flat[{i}];
    assign wr_bvalid_{i} = wr_bvalid_flat[{i}];
    assign wr_bready_flat[{i}] = wr_bready_{i};"""
        for i in range(len(composition.lanes))
    )


def _lane_chain_sv(composition: ScanComposition, i: int, *, clk: str) -> str:
    """The ordered chain-stage instances for lane ``i``, each consuming the
    previous stage's row stream (empty for a chainless lane, keeping the
    chainless emission byte-identical)."""
    parts = []
    for j, stage in enumerate(composition.lanes[i].chain):
        upstream = "filt_out" if j == 0 else f"chain{j - 1}_out"
        parts.append(
            f"""    {stage.module} chain_{i}_{j} (
        .clk({clk}),
        .rst(lane_rst),
{_tile_config_binds_sv(stage.config)}        .input_valid({upstream}_valid_{i}),
        .input_ready({upstream}_ready_{i}),
        .input_data({upstream}_data_{i}),
        .input_last({upstream}_last_{i}),
        .output_valid(chain{j}_out_valid_{i}),
        .output_ready(chain{j}_out_ready_{i}),
        .output_data(chain{j}_out_data_{i}),
        .output_last(chain{j}_out_last_{i}),
        .status_valid(chain{j}_status_valid_{i}),
        .status_ready(chain{j}_status_ready_{i}),
        .status_error(chain{j}_status_error_{i}),
        .status_error_code(chain{j}_status_error_code_{i})
    );

"""
        )
    return "".join(parts)


def _lane_tile_upstream(composition: ScanComposition, i: int) -> str:
    """The stream-wire prefix feeding lane ``i``'s terminal tile: the lane
    front directly, or the last chain stage."""
    chain = composition.lanes[i].chain
    return "filt_out" if not chain else f"chain{len(chain) - 1}_out"


def _lane_units_sv(composition: ScanComposition, *, clk: str, writer_rst: str) -> str:
    """Every lane unit: front (partitioner tap / broadcast tap / partition
    filter), chain stages, operator tile, status glue, and record writer."""
    addr_width = composition.addr_width
    burst_beats = composition.burst_beats
    return "\n\n".join(
        f"""{_lane_front_sv(composition, i, clk=clk)}
{_lane_chain_sv(composition, i, clk=clk)}    {composition.lanes[i].module} tile_{i} (
        .clk({clk}),
        .rst(lane_rst),
{_tile_config_binds_sv(composition.lanes[i].config)}        .input_valid({_lane_tile_upstream(composition, i)}_valid_{i}),
        .input_ready({_lane_tile_upstream(composition, i)}_ready_{i}),
        .input_data({_lane_tile_upstream(composition, i)}_data_{i}),
        .input_last({_lane_tile_upstream(composition, i)}_last_{i}),
        .output_valid(tile_out_valid_{i}),
        .output_ready(tile_out_ready_{i}),
        .output_data(tile_out_data_{i}),
        .output_last(tile_out_last_{i}),
        .status_valid(tile_status_valid_{i}),
        .status_ready(tile_status_ready_{i}),
        .status_error(tile_status_error_{i}),
        .status_error_code(tile_status_error_code_{i}),
        .{composition.lanes[i].count_port}(tile_bar_count_{i})
    );

{_lane_status_glue_sv(composition, i)}
    dau_axi_record_writer #(
        .ADDR_WIDTH({addr_width}),
        .BURST_BEATS({burst_beats})
    ) writer_{i} (
        .clk({clk}),
        .rst({writer_rst}),
        .start(unit_start),
        .output_address(lane_output_address_{i}),
        .busy(writer_busy_{i}),
        .done(writer_done_{i}),
        .error(writer_error_{i}),
        .error_code(writer_error_code_{i}),
        .result_length_bytes(lane_result_length_{i}),
        .m_axi_awaddr(wr_awaddr_{i}),
        .m_axi_awlen(wr_awlen_{i}),
        .m_axi_awsize(),
        .m_axi_awburst(),
        .m_axi_awvalid(wr_awvalid_{i}),
        .m_axi_awready(wr_awready_{i}),
        .m_axi_wdata(wr_wdata_{i}),
        .m_axi_wstrb(),
        .m_axi_wlast(wr_wlast_{i}),
        .m_axi_wvalid(wr_wvalid_{i}),
        .m_axi_wready(wr_wready_{i}),
        .m_axi_bresp(wr_bresp),
        .m_axi_bvalid(wr_bvalid_{i}),
        .m_axi_bready(wr_bready_{i}),
        .record_valid(tile_out_valid_{i}),
        .record_ready(tile_out_ready_{i}),
        .record_data(tile_out_data_{i}),
        .record_last(tile_out_last_{i}),
        .status_valid(unit_status_valid_{i}),
        .status_ready(unit_status_ready_{i}),
        .status_error(unit_status_error_{i}),
        .status_error_code(unit_status_error_code_{i})
    );"""
        for i in range(len(composition.lanes))
    )


def _fanout_sv(composition: ScanComposition, *, clk: str) -> tuple[str, str]:
    """The scan fan-out: wire declarations and the instance — the shared
    partitioner when the composition carries one, the stream broadcast
    otherwise."""
    num_lanes = len(composition.lanes)
    if composition.partitioner is not None:
        wire_decls = f"""    wire [{num_lanes - 1}:0] part_out_valid;
    wire [{num_lanes - 1}:0] part_out_ready;
    wire [{num_lanes * 64 - 1}:0] part_out_data;
    wire [{num_lanes - 1}:0] part_out_last;
    wire [{num_lanes - 1}:0] part_status_valid;
    wire [{num_lanes - 1}:0] part_status_ready;
    wire [{num_lanes - 1}:0] part_status_error;
    wire [{num_lanes * 8 - 1}:0] part_status_error_code;"""
        instance = f"""    {composition.partitioner.module} #(
        .NUM_PARTITIONS({num_lanes})
    ) partitioner (
        .clk({clk}),
        .rst(lane_rst),
{_tile_config_binds_sv(composition.partitioner.config)}        .input_valid(scan_valid),
        .input_ready(scan_ready),
        .input_data(scan_data),
        .input_last(scan_last),
        .output_valid(part_out_valid),
        .output_ready(part_out_ready),
        .output_data(part_out_data),
        .output_last(part_out_last),
        .status_valid(part_status_valid),
        .status_ready(part_status_ready),
        .status_error(part_status_error),
        .status_error_code(part_status_error_code)
    );"""
        return wire_decls, instance
    wire_decls = f"""    wire [{num_lanes - 1}:0] bcast_valid;
    wire [{num_lanes - 1}:0] bcast_ready;
    wire [63:0] bcast_data;
    wire bcast_last;"""
    instance = f"""    dau_stream_broadcast #(
        .NUM_OUTPUTS({num_lanes})
    ) broadcast (
        .clk({clk}),
        .rst(lane_rst),
        .input_valid(scan_valid),
        .input_ready(scan_ready),
        .input_data(scan_data),
        .input_last(scan_last),
        .output_valid(bcast_valid),
        .output_ready(bcast_ready),
        .output_data(bcast_data),
        .output_last(bcast_last)
    );"""
    return wire_decls, instance


def _writer_error_priority_sv(num_lanes: int, *, error: str, error_code: str) -> str:
    """First-error-wins fall-through over the lane writers (the reader's
    branch comes first at the call site)."""
    return "\n".join(
        f"""            end else if (writer_error_{i}) begin
                {error} = 1'b1;
                {error_code} = writer_error_code_{i};"""
        for i in range(num_lanes)
    )


def _validate_against_sources(composition: ScanComposition, sources: Sequence[Path | str]) -> None:
    """Slang-parse every tile of the composition out of ``sources`` and
    check it against the stream+status contract (``validate_stream_tile``,
    with the lane tile's ``count_port``; partition filters and the shared
    partitioner carry none) and its config-binding names (every config key
    must be an input port of the parsed module). Raises
    ``ScanCompositionError`` listing every violation."""
    from dau_build.sv_contract import StreamContractError, module_ports, validate_stream_tile

    tiles: list[tuple[TileInstance, str | None]] = []
    if composition.partitioner is not None:
        tiles.append((composition.partitioner, None))
    for lane in composition.lanes:
        if lane.partition is not None:
            tiles.append((lane.partition, None))
        for stage in lane.chain:
            tiles.append((stage, None))
        tiles.append((lane, lane.count_port))

    violations: list[str] = []
    for tile, count_port in tiles:
        try:
            ports = module_ports(sources, tile.module)
        except StreamContractError as exc:
            violations.append(f"{tile.module}: {exc}")
            continue
        violations.extend(f"{tile.module}: {violation}" for violation in validate_stream_tile(sources, tile.module, count_port=count_port))
        for key in tile.config:
            if ports.get(key) != "input":
                available = ", ".join(sorted(name for name, direction in ports.items() if direction == "input" and name.startswith("cfg_"))) or "none"
                violations.append(f"{tile.module}: config binding {key!r} is not an input port (cfg ports: {available})")
    if violations:
        raise ScanCompositionError(
            f"composition {composition.name!r} fails interface validation:\n" + "\n".join(f"  - {violation}" for violation in violations)
        )


def generate_scan_composition_top_sv(
    composition: ScanComposition,
    *,
    sources: Sequence[Path | str] | None = None,
    generated_by: str = _DEFAULT_GENERATED_BY,
) -> str:
    """Walk a ``ScanComposition``: one AXI burst reader scans the input
    window once and fans the row stream to the composition's lanes — each
    an optional partition filter feeding an operator tile in the binding
    the composition carries, and a record writer with its own
    OUTPUT_ADDRESS register (the lane register block). The lane writers
    share the M_AXI write channels through the write mux; the reader owns
    the read channels.

    When ``sources`` is given, every tile's slang-parsed interface is
    validated before anything is emitted (contract conformance plus every
    config-binding key checked against the module's real input ports);
    without sources the walker emits from data alone. ``generated_by``
    names the generator in the output banner."""
    if sources is not None:
        _validate_against_sources(composition, sources)
    regs = composition.registers
    addr_width = composition.addr_width
    burst_beats = composition.burst_beats
    module_name = composition.module_name
    num_lanes = len(composition.lanes)
    lanes = range(num_lanes)
    lane_localparams = "\n".join(
        f"    localparam [11:0] ADDR_LANE{i}_OUTPUT_ADDRESS = 12'h{regs.lane_register(i, regs.lane_output_address_low):03X};\n"
        f"    localparam [11:0] ADDR_LANE{i}_RESULT_LENGTH = 12'h{regs.lane_register(i, regs.lane_result_length_low):03X};\n"
        f"    localparam [11:0] ADDR_LANE{i}_RECORD_COUNT_LOW = 12'h{regs.lane_register(i, regs.lane_record_count_low):03X};\n"
        f"    localparam [11:0] ADDR_LANE{i}_RECORD_COUNT_HIGH = 12'h{regs.lane_register(i, regs.lane_record_count_high):03X};\n"
        f"    localparam [11:0] ADDR_LANE{i}_ERROR = 12'h{regs.lane_register(i, regs.lane_error):03X};"
        for i in lanes
    )
    lane_reg_decls = "\n".join(f"    reg [{addr_width - 1}:0] lane_output_address_{i};" for i in lanes)
    lane_wire_decls = _lane_wire_decls_sv(composition)
    lane_flat_assigns = _lane_flat_assigns_sv(composition)
    lane_instances = _lane_units_sv(composition, clk="s_axi_aclk", writer_rst="!s_axi_aresetn")
    fanout_wire_decls, fanout_instance = _fanout_sv(composition, clk="s_axi_aclk")

    all_writers_done = " && ".join(f"writer_done_{i}" for i in lanes)
    any_writer_busy = " || ".join(f"writer_busy_{i}" for i in lanes)
    error_priority = _writer_error_priority_sv(num_lanes, error="job_error", error_code="job_error_code")
    write_case_items = "\n".join(f"                    ADDR_LANE{i}_OUTPUT_ADDRESS: lane_output_address_{i} <= s_axi_wdata;" for i in lanes)
    read_case_items = "\n".join(
        f"""                    ADDR_LANE{i}_OUTPUT_ADDRESS: s_axi_rdata <= lane_output_address_{i}[31:0];
                    ADDR_LANE{i}_RESULT_LENGTH: s_axi_rdata <= lane_result_length_{i};
                    ADDR_LANE{i}_RECORD_COUNT_LOW: s_axi_rdata <= lane_bar_count_{i}[31:0];
                    ADDR_LANE{i}_RECORD_COUNT_HIGH: s_axi_rdata <= lane_bar_count_{i}[63:32];
                    ADDR_LANE{i}_ERROR: s_axi_rdata <= {{24'd0, writer_error_code_{i}}};"""
        for i in lanes
    )
    lane_reset_items = "\n".join(f"            lane_output_address_{i} <= {addr_width}'d0;" for i in lanes)
    lane_count_clear_items = "\n".join(f"                lane_bar_count_{i} <= 64'd0;" for i in lanes)
    lane_count_latch_items = "\n".join(
        f"""            if (tile_status_valid_{i} && tile_status_ready_{i}) begin
                lane_bar_count_{i} <= tile_bar_count_{i};
            end"""
        for i in lanes
    )
    localparams = _register_localparams_sv(
        (
            ("LAST_ERROR", regs.last_error),
            ("JOB_CONTROL", regs.job_control),
            ("JOB_STATUS", regs.job_status),
            ("INPUT_ADDRESS_LOW", regs.input_address_low),
            ("INPUT_LENGTH_LOW", regs.input_length_low),
        )
    )
    register_process = _axi_lite_register_process_sv(
        write_default_comment="other job fields accepted and ignored",
        reset_extra=f"""            input_address <= {addr_width}'d0;
            input_length_bytes <= 32'd0;
            job_start <= 1'b0;
            length_fail <= 1'b0;
            prev_done <= 1'b1;
            pipeline_error_reset <= 1'b0;
{lane_reset_items}
{lane_count_clear_items.replace("                ", "            ")}
""",
        tick_extra=f"""            prev_done <= job_done;
            pipeline_error_reset <= job_done && !prev_done && job_error;
            if (job_start) begin
                length_fail <= !length_ok;
{lane_count_clear_items}
            end
{lane_count_latch_items}
""",
        write_cases_extra=f"""                    ADDR_INPUT_ADDRESS_LOW: input_address <= s_axi_wdata[{addr_width - 1}:0];
                    ADDR_INPUT_LENGTH_LOW: input_length_bytes <= s_axi_wdata;
{write_case_items}
""",
        read_cases_extra=f"""                    ADDR_INPUT_ADDRESS_LOW: s_axi_rdata <= input_address[31:0];
                    ADDR_INPUT_LENGTH_LOW: s_axi_rdata <= input_length_bytes;
                    12'hFC0: s_axi_rdata <= dbg_first_stream_word[31:0];
                    12'hFC4: s_axi_rdata <= dbg_first_stream_word[63:32];
                    12'hFC8: s_axi_rdata <= dbg_first_araddr;
                    12'hFCC: s_axi_rdata <= dbg_beats_while_idle;
                    12'hFD0: s_axi_rdata <= dbg_final_fifo_count;
{read_case_items}
""",
    )

    return f"""`default_nettype none

// GENERATED by {generated_by} — do not
// edit. Scan composition {composition.name}: one scan fanned to {num_lanes}
// lane(s) behind the DAU stream-job register contract with the NoC lane
// register block. Plain-Verilog top (BD module references require it).
module {module_name} #(
    parameter [31:0] OPERATOR_BITMAP = 32'h{composition.operator_bitmap:08X},
    parameter [31:0] LANE_COUNT = 32'd{num_lanes},
    parameter [31:0] HOST_OPCODE_BITMAP = 32'h{composition.host_opcode_bitmap:08X},
    parameter [31:0] SORT_CAPACITY = 32'd{composition.sort_capacity}
) (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 s_axi_aclk CLK" *)
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF S_AXI:M_AXI, ASSOCIATED_RESET s_axi_aresetn" *)
    input wire s_axi_aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 s_axi_aresetn RST" *)
    (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    input wire s_axi_aresetn,

{_s_axi_lite_ports_sv()}

{_m_axi_ports_sv(addr_width=addr_width, burst_beats=burst_beats)}
);
    // window-relative decode (the AXI address carries the BAR offset)
{localparams}
{lane_localparams}

{_axi_lite_decode_wires_sv()}

    reg [{addr_width - 1}:0] input_address;
    reg [31:0] input_length_bytes;
    reg job_start;
{lane_reg_decls}

    wire reader_busy;
    wire reader_done;
    wire reader_error;
    wire [7:0] reader_error_code;
    wire [63:0] dbg_first_stream_word;
    wire [31:0] dbg_first_araddr;
    wire [31:0] dbg_beats_while_idle;
    wire [31:0] dbg_final_fifo_count;
    wire scan_valid;
    wire scan_ready;
    wire [63:0] scan_data;
    wire scan_last;
{fanout_wire_decls}
{_wr_flat_decls_sv(composition)}
{lane_wire_decls}

    // the 16-byte row grid is enforced before any unit starts: a rejected
    // length must not leave the writers waiting on a status
    reg length_fail;
    wire length_ok = (input_length_bytes != 32'd0) && (input_length_bytes[3:0] == 4'd0);
    wire unit_start = job_start && length_ok;

    wire job_busy = reader_busy || {any_writer_busy};
    wire job_done = length_fail || (reader_done && {all_writers_done});
    reg job_error;
    reg [7:0] job_error_code;
    reg prev_done;
    reg pipeline_error_reset;
    wire lane_rst = !s_axi_aresetn || pipeline_error_reset;

{_axi_lite_decode_assigns_sv()}

    always @(*) begin
        if (length_fail) begin
            job_error = 1'b1;
            job_error_code = 8'hFE;
        end else begin
            if (reader_error) begin
                job_error = 1'b1;
                job_error_code = reader_error_code;
{error_priority}
            end else begin
                job_error = 1'b0;
                job_error_code = 8'd0;
            end
        end
    end

{_identity_registers_instance_sv()}

    dau_axi_burst_reader #(
        .ADDR_WIDTH({addr_width}),
        .BURST_BEATS({burst_beats}),
        .LENGTH_ALIGN_BITS(4)
    ) reader (
        .clk(s_axi_aclk),
        .rst(!s_axi_aresetn),
        .start(unit_start),
        .read_address(input_address),
        .read_length_bytes(input_length_bytes),
        .busy(reader_busy),
        .done(reader_done),
        .error(reader_error),
        .error_code(reader_error_code),
        .m_axi_araddr(m_axi_araddr),
        .m_axi_arlen(m_axi_arlen),
        .m_axi_arsize(m_axi_arsize),
        .m_axi_arburst(m_axi_arburst),
        .m_axi_arvalid(m_axi_arvalid),
        .m_axi_arready(m_axi_arready),
        .m_axi_rdata(m_axi_rdata),
        .m_axi_rresp(m_axi_rresp),
        .m_axi_rlast(m_axi_rlast),
        .m_axi_rvalid(m_axi_rvalid),
        .m_axi_rready(m_axi_rready),
        .stream_valid(scan_valid),
        .stream_ready(scan_ready),
        .stream_data(scan_data),
        .stream_last(scan_last),
        .dbg_first_stream_word(dbg_first_stream_word),
        .dbg_first_araddr(dbg_first_araddr),
        .dbg_beats_while_idle(dbg_beats_while_idle),
        .dbg_final_fifo_count(dbg_final_fifo_count)
    );

{fanout_instance}

{lane_instances}

{lane_flat_assigns}

    dau_axi_write_mux #(
        .NUM_INPUTS({num_lanes}),
        .ADDR_WIDTH({addr_width})
    ) write_mux (
        .clk(s_axi_aclk),
        .rst(!s_axi_aresetn),
        .s_awaddr(wr_awaddr_flat),
        .s_awlen(wr_awlen_flat),
        .s_awvalid(wr_awvalid_flat),
        .s_awready(wr_awready_flat),
        .s_wdata(wr_wdata_flat),
        .s_wlast(wr_wlast_flat),
        .s_wvalid(wr_wvalid_flat),
        .s_wready(wr_wready_flat),
        .s_bresp(wr_bresp),
        .s_bvalid(wr_bvalid_flat),
        .s_bready(wr_bready_flat),
        .m_axi_awaddr(m_axi_awaddr),
        .m_axi_awlen(m_axi_awlen),
        .m_axi_awsize(m_axi_awsize),
        .m_axi_awburst(m_axi_awburst),
        .m_axi_awvalid(m_axi_awvalid),
        .m_axi_awready(m_axi_awready),
        .m_axi_wdata(m_axi_wdata),
        .m_axi_wstrb(m_axi_wstrb),
        .m_axi_wlast(m_axi_wlast),
        .m_axi_wvalid(m_axi_wvalid),
        .m_axi_wready(m_axi_wready),
        .m_axi_bresp(m_axi_bresp),
        .m_axi_bvalid(m_axi_bvalid),
        .m_axi_bready(m_axi_bready)
    );

{register_process}
endmodule

`default_nettype wire
"""


_DEFAULT_GENERATED_BY_SIM = "dau_build.scan_composition.generate_scan_composition_sim_sv"


def generate_scan_composition_sim_sv(
    composition: ScanComposition,
    *,
    module_name: str | None = None,
    mem_words: int = 65536,
    read_latency: int = 4,
    config_inputs: dict[str, int] | None = None,
    sources: Sequence[Path | str] | None = None,
    generated_by: str = _DEFAULT_GENERATED_BY_SIM,
) -> str:
    """Walk the same ``ScanComposition`` into its JOB-level simulation
    harness: the pipeline the shell top wires (burst reader -> fan-out ->
    per-lane optional partition filter -> tile -> record writer -> write
    mux) closed by a backdoor-loaded ``dau_axi_ram_sim`` instead of an
    external M_AXI, and driven by the job-level control surface (start /
    input window / per-lane output addresses / busy / done / first-error
    status) instead of the AXI-Lite register aperture — the shape of the
    hand-written ``*_noc_sim.sv`` tops.

    ``config_inputs`` maps extra top-level input ports (name -> bit width)
    onto the harness so tile config bindings can reference testbench-driven
    signals (the shared partitioner's splitters, typically) instead of
    literals. ``module_name`` defaults to the composition's shell module
    name with a ``_sim`` suffix; ``mem_words``/``read_latency`` parameterize
    the backdoor RAM. ``sources`` arms the same slang-backed interface
    validation as the shell walker."""
    if sources is not None:
        _validate_against_sources(composition, sources)
    addr_width = composition.addr_width
    burst_beats = composition.burst_beats
    num_lanes = len(composition.lanes)
    lanes = range(num_lanes)
    name = module_name if module_name is not None else f"{composition.module_name}_sim"
    config_input_ports = "".join(f"    input wire [{width - 1}:0] {port},\n" for port, width in (config_inputs or {}).items())
    lane_port_taps = "\n".join(
        f"""    wire [{addr_width - 1}:0] lane_output_address_{i} = lane_output_address[{addr_width * (i + 1) - 1}:{addr_width * i}];
    assign lane_result_length_bytes[{32 * (i + 1) - 1}:{32 * i}] = lane_result_length_{i};
    assign lane_count[{64 * (i + 1) - 1}:{64 * i}] = lane_bar_count_{i};"""
        for i in lanes
    )
    fanout_wire_decls, fanout_instance = _fanout_sv(composition, clk="clk")
    lane_instances = _lane_units_sv(composition, clk="clk", writer_rst="rst")
    all_writers_done = " && ".join(f"writer_done_{i}" for i in lanes)
    any_writer_busy = " || ".join(f"writer_busy_{i}" for i in lanes)
    error_priority = _writer_error_priority_sv(num_lanes, error="error", error_code="error_code")
    lane_count_clears = "\n".join(f"            lane_bar_count_{i} <= 64'd0;" for i in lanes)
    lane_count_start_clears = "\n".join(f"                lane_bar_count_{i} <= 64'd0;" for i in lanes)
    lane_count_latches = "\n".join(
        f"""            if (tile_status_valid_{i} && tile_status_ready_{i}) begin
                lane_bar_count_{i} <= tile_bar_count_{i};
            end"""
        for i in lanes
    )

    return f"""`default_nettype none

// GENERATED by {generated_by} — do not
// edit. Scan-composition sim harness {composition.name}: the pipeline the
// shell top wires — one scan fanned to {num_lanes} lane(s) through the shared
// write mux — behind the job-level control surface and closed by the
// backdoor AXI RAM standing in for the platform memory.
module {name} (
    input wire clk,
    input wire rst,

    input wire start,
    input wire [{addr_width - 1}:0] input_address,
    input wire [31:0] input_length_bytes,
{config_input_ports}    input wire [{num_lanes * addr_width - 1}:0] lane_output_address,
    output wire busy,
    output wire done,
    output reg error,
    output reg [7:0] error_code,
    output wire [{num_lanes * 32 - 1}:0] lane_result_length_bytes,
    output wire [{num_lanes * 64 - 1}:0] lane_count,

    input wire bd_write,
    input wire [31:0] bd_index,
    input wire [63:0] bd_wdata,
    output wire [63:0] bd_rdata
);
    wire reader_busy;
    wire reader_done;
    wire reader_error;
    wire [7:0] reader_error_code;
    wire [{addr_width - 1}:0] rd_araddr;
    wire [7:0] rd_arlen;
    wire [2:0] rd_arsize;
    wire [1:0] rd_arburst;
    wire rd_arvalid;
    wire rd_arready;
    wire [63:0] rd_rdata;
    wire [1:0] rd_rresp;
    wire rd_rlast;
    wire rd_rvalid;
    wire rd_rready;
    wire scan_valid;
    wire scan_ready;
    wire [63:0] scan_data;
    wire scan_last;
{fanout_wire_decls}
{_wr_flat_decls_sv(composition)}
    wire [{addr_width - 1}:0] mx_awaddr;
    wire [7:0] mx_awlen;
    wire [2:0] mx_awsize;
    wire [1:0] mx_awburst;
    wire mx_awvalid;
    wire mx_awready;
    wire [63:0] mx_wdata;
    wire [7:0] mx_wstrb;
    wire mx_wlast;
    wire mx_wvalid;
    wire mx_wready;
    wire [1:0] mx_bresp;
    wire mx_bvalid;
    wire mx_bready;
{_lane_wire_decls_sv(composition)}

{lane_port_taps}

    // recover mid-stream lanes after an error before the next job
    reg prev_done;
    reg pipeline_error_reset;
    wire lane_rst = rst || pipeline_error_reset;

    // the 16-byte row grid is enforced before any unit starts: a rejected
    // length must not leave the writers waiting on a status
    reg length_fail;
    wire length_ok = (input_length_bytes != 32'd0) && (input_length_bytes[3:0] == 4'd0);
    wire unit_start = start && length_ok;

    assign busy = reader_busy || {any_writer_busy};
    assign done = length_fail || (reader_done && {all_writers_done});

    always @(*) begin
        if (length_fail) begin
            error = 1'b1;
            error_code = 8'hFE;
        end else begin
            if (reader_error) begin
                error = 1'b1;
                error_code = reader_error_code;
{error_priority}
            end else begin
                error = 1'b0;
                error_code = 8'd0;
            end
        end
    end

    dau_axi_burst_reader #(
        .ADDR_WIDTH({addr_width}),
        .BURST_BEATS({burst_beats}),
        .LENGTH_ALIGN_BITS(4)
    ) reader (
        .clk(clk),
        .rst(rst),
        .start(unit_start),
        .read_address(input_address),
        .read_length_bytes(input_length_bytes),
        .busy(reader_busy),
        .done(reader_done),
        .error(reader_error),
        .error_code(reader_error_code),
        .m_axi_araddr(rd_araddr),
        .m_axi_arlen(rd_arlen),
        .m_axi_arsize(rd_arsize),
        .m_axi_arburst(rd_arburst),
        .m_axi_arvalid(rd_arvalid),
        .m_axi_arready(rd_arready),
        .m_axi_rdata(rd_rdata),
        .m_axi_rresp(rd_rresp),
        .m_axi_rlast(rd_rlast),
        .m_axi_rvalid(rd_rvalid),
        .m_axi_rready(rd_rready),
        .stream_valid(scan_valid),
        .stream_ready(scan_ready),
        .stream_data(scan_data),
        .stream_last(scan_last),
        // boundary debug taps (BAR-mapped on hardware; unused in this sim)
        .dbg_first_stream_word(),
        .dbg_first_araddr(),
        .dbg_beats_while_idle(),
        .dbg_final_fifo_count()
    );

{fanout_instance}

{lane_instances}

{_lane_flat_assigns_sv(composition)}

    dau_axi_write_mux #(
        .NUM_INPUTS({num_lanes}),
        .ADDR_WIDTH({addr_width})
    ) write_mux (
        .clk(clk),
        .rst(rst),
        .s_awaddr(wr_awaddr_flat),
        .s_awlen(wr_awlen_flat),
        .s_awvalid(wr_awvalid_flat),
        .s_awready(wr_awready_flat),
        .s_wdata(wr_wdata_flat),
        .s_wlast(wr_wlast_flat),
        .s_wvalid(wr_wvalid_flat),
        .s_wready(wr_wready_flat),
        .s_bresp(wr_bresp),
        .s_bvalid(wr_bvalid_flat),
        .s_bready(wr_bready_flat),
        .m_axi_awaddr(mx_awaddr),
        .m_axi_awlen(mx_awlen),
        .m_axi_awsize(mx_awsize),
        .m_axi_awburst(mx_awburst),
        .m_axi_awvalid(mx_awvalid),
        .m_axi_awready(mx_awready),
        .m_axi_wdata(mx_wdata),
        .m_axi_wstrb(mx_wstrb),
        .m_axi_wlast(mx_wlast),
        .m_axi_wvalid(mx_wvalid),
        .m_axi_wready(mx_wready),
        .m_axi_bresp(mx_bresp),
        .m_axi_bvalid(mx_bvalid),
        .m_axi_bready(mx_bready)
    );

    dau_axi_ram_sim #(
        .ADDR_WIDTH({addr_width}),
        .MEM_WORDS({mem_words}),
        .READ_LATENCY({read_latency})
    ) ram (
        .clk(clk),
        .rst(rst),
        .s_axi_araddr(rd_araddr),
        .s_axi_arlen(rd_arlen),
        .s_axi_arsize(rd_arsize),
        .s_axi_arburst(rd_arburst),
        .s_axi_arvalid(rd_arvalid),
        .s_axi_arready(rd_arready),
        .s_axi_rdata(rd_rdata),
        .s_axi_rresp(rd_rresp),
        .s_axi_rlast(rd_rlast),
        .s_axi_rvalid(rd_rvalid),
        .s_axi_rready(rd_rready),
        .s_axi_awaddr(mx_awaddr),
        .s_axi_awlen(mx_awlen),
        .s_axi_awsize(mx_awsize),
        .s_axi_awburst(mx_awburst),
        .s_axi_awvalid(mx_awvalid),
        .s_axi_awready(mx_awready),
        .s_axi_wdata(mx_wdata),
        .s_axi_wstrb(mx_wstrb),
        .s_axi_wlast(mx_wlast),
        .s_axi_wvalid(mx_wvalid),
        .s_axi_wready(mx_wready),
        .s_axi_bresp(mx_bresp),
        .s_axi_bvalid(mx_bvalid),
        .s_axi_bready(mx_bready),
        .bd_write(bd_write),
        .bd_index(bd_index),
        .bd_wdata(bd_wdata),
        .bd_rdata(bd_rdata)
    );

    always @(posedge clk) begin
        if (rst) begin
            prev_done <= 1'b1;
            pipeline_error_reset <= 1'b0;
            length_fail <= 1'b0;
{lane_count_clears}
        end else begin
            prev_done <= done;
            pipeline_error_reset <= done && !prev_done && error;
            if (start) begin
                length_fail <= !length_ok;
{lane_count_start_clears}
            end
{lane_count_latches}
        end
    end
endmodule

`default_nettype wire
"""
