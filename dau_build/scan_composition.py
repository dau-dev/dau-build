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

from collections.abc import Sequence
from pathlib import Path

from ccflow import BaseModel
from pydantic import ConfigDict

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
    or expressions in terms of the top's signals) and any module-parameter
    overrides (``params``: SystemVerilog PARAMETERs — elaboration-time sizes
    like the membership core's ``KEY_SPACE`` — emitted as a ``#(...)`` override
    on the instance). A tile with no ``params`` is emitted exactly as before
    the channel existed (no ``#(...)``), so every param-less golden stays
    byte-identical."""

    model_config = ConfigDict(frozen=True)

    module: str
    config: dict[str, str] = {}  # noqa: RUF012  # pydantic deep-copies field defaults per instance
    params: dict[str, int] = {}  # noqa: RUF012  # pydantic deep-copies field defaults per instance
    # a CHAIN STAGE that emits a success close-out per batch (a terminal-shaped
    # tile fused mid-lane: the as-of join, the grouped aggregator). The lane
    # status mux allows exactly one close-out, so such a stage's status is
    # DRAINED (accepted immediately) and only its ERRORS are latched into the
    # lane status — the fused-chain protocol. False for the silent-on-success
    # stages (filters, maps, key rewrites), which keep the byte-identical
    # upstream-first mux. The private bridge sets this from the registry; this
    # public generator cannot consult it.
    closes_out: bool = False


class LaneTile(TileInstance):
    """One lane of the scan: an operator tile (with the name of its trailing
    status-counter port) behind an optional row-atomic partition filter and
    an optional ordered ``chain`` of mid-lane operator stages
    (filter -> map -> ... -> terminal tile). Chain stages speak the same
    stream+status contract but need no count port; their statuses feed the
    lane's status mux upstream-first, so a mid-chain close-out (zero rows,
    torn row, bad config) wins over the terminal tile's.

    Status contract (composer's obligation — the walker is generic and
    cannot check module semantics): exactly one success close-out per lane
    per batch. Chain stages and partition filters must be SILENT on
    success (status only when the downstream cannot see the batch end);
    only the terminal tile closes out every batch — the record writer
    consumes exactly one producer status. A close-out-on-success stage
    mid-chain would leave a second status pending after the writer is
    done; registry-aware composers (dau-core ScanCompositionSpec) reject
    that shape up front. On an error close-out the shell's
    pipeline_error_reset resets the lane, clearing any mid-stream stage."""

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
    load_phase: int = 0x084
    job_status: int = 0x054
    input_address_low: int = 0x058
    input_address_high: int = 0x05C
    input_length_low: int = 0x060
    lane_base: int = 0x100
    lane_stride: int = 0x20
    lane_output_address_low: int = 0x00
    lane_output_address_high: int = 0x14
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
    no per-lane partition.

    One optional front-stage tile may be placed between the burst reader and
    the fan-out. ``front_unpack`` widens packed fields; ``front_gearbox``
    turns wide reader beats into whole ``input_row_bytes`` records for a
    shared dispatcher. Absent (``None``), the top emits exactly as before —
    every param-less golden stays byte-identical.

    ``wide_lane`` selects the registry-legalized single-lane shape whose
    stages consume the burst reader's whole wide beat directly. The private
    bridge sets this flag; this public generator cannot consult its registry."""

    model_config = ConfigDict(frozen=True)

    name: str
    module_name: str
    lanes: tuple[LaneTile, ...]
    partitioner: TileInstance | None = None
    front_unpack: TileInstance | None = None
    front_gearbox: TileInstance | None = None
    burst_beats: int = 32
    addr_width: int = 32
    # the memory read datapath width (the width-tier catalog knob): the burst
    # reader's DATA_WIDTH and, for a dispatch-shaped scan, the row-stream
    # framing into the fan-out. 64 (default) keeps the classic single shared
    # M_AXI byte-identical; 128/256/512 split a wide read-only M_AXI_R off from
    # the narrow write M_AXI_W (the record writers stay 64-bit) so a wide DDR
    # read never forces narrow writes on a wide bus.
    data_width: int = 64
    wide_lane: bool = False
    # the byte size of ONE record the composition's first consumer reads.
    # 16 is the standard quad row; a fused chain whose head takes a wider
    # record (the as-of join's 24-byte 3-word record) must say so or the
    # length gate rejects an odd number of perfectly good records. The
    # register can only enforce power-of-two alignment, so the gate uses
    # the largest power of two dividing this — a length that is on the
    # word grid but tears a record is the head tile's ERR_STREAM job (the
    # same division of labour the packed front already uses).
    input_row_bytes: int = 16
    # capability words the identity block advertises (register map 0.2).
    # These are caller-computed data — the walker never guesses them: the
    # bitmaps default to zero ("advertise nothing") so a composition only
    # advertises what its composer declared, and the lane-count word is
    # always the composed lane count.
    operator_bitmap: int = 0x0000_0000
    host_opcode_bitmap: int = 0x0000_0000
    sort_capacity: int = 0
    # A 64-bit identity for THIS composition, advertised so a host can ask
    # "is the device running the design I think it is". The advertised
    # capability words cannot answer that: distinct designs routinely share
    # them (q19-scan and q19-packed-wide advertise identically). Caller
    # computed like the bitmaps -- the walker never guesses it -- and zero
    # means "not stamped", which is what an unstamped shell reads back as.
    build_id: int = 0
    registers: RegisterLayout = RegisterLayout()

    def model_post_init(self, _context) -> None:
        _validate_composition_shape(self)


def _largest_power_of_two_divisor_bits(row_bytes: int) -> int:
    """The alignment the length gate can enforce for a record of
    ``row_bytes``: the exponent of the largest power of two dividing it
    (16 -> 4, 24 -> 3, 8 -> 3). A 24-byte record is not power-of-two
    sized, so the gate admits every 8-byte-aligned length and leaves a
    torn record to the head tile's ERR_STREAM path."""
    if row_bytes <= 0 or row_bytes % 8:
        raise ScanCompositionError(f"input_row_bytes must be a positive multiple of 8, got {row_bytes}")
    return (row_bytes & -row_bytes).bit_length() - 1


def _validate_composition_shape(composition: ScanComposition) -> None:
    """The composition's shape and width-coherence invariants. Called from
    ``model_post_init`` AND from both public generators: ``model_copy`` skips
    pydantic validation, so an emitted top/sim must re-check — an incoherent
    copy would otherwise emit Verilog whose feed wires and fan-out port
    widths disagree."""
    if not composition.lanes:
        raise ScanCompositionError("a scan composition needs at least one lane")
    if composition.wide_lane:
        if len(composition.lanes) != 1:
            raise ScanCompositionError("a wide_lane scan composition needs exactly one lane")
        if composition.partitioner is not None:
            raise ScanCompositionError("a wide_lane scan composition cannot carry a shared partitioner")
        # a PACKED wide lane is the point of the wide unpacker: the front
        # widens SLOTS packed 64-bit rows into the SLOTS quad rows the lane
        # consumes, so the lane sees the front's OUT_WIDTH while the reader
        # moves half the bytes. The width coherence is checked below.
        if composition.front_gearbox is not None:
            raise ScanCompositionError("a wide_lane scan composition cannot carry a front_gearbox")
        if composition.lanes[0].partition is not None:
            raise ScanCompositionError("a wide_lane scan composition cannot carry a lane partition")
    if composition.partitioner is not None:
        for lane in composition.lanes:
            if lane.partition is not None:
                raise ScanCompositionError(f"lane tile {lane.module!r} carries a partition filter but the scan already has a shared partitioner")
    # Two width surfaces: DATA_WIDTH (the burst reader's DDR read/stream
    # width, the memory-bandwidth knob) and the FEED width entering the
    # fan-out (the rows/cycle framing the dispatcher accepts). A front unpacker
    # widens packed rows; a front gearbox emits one input record; without either
    # front stage the feed IS the reader stream.
    _WIDTHS = (64, 128, 256, 512)
    # both the gearbox and the record dispatcher elaboration-guard RECORD_WORDS
    _MAX_RECORD_WORDS = 16
    data_width = composition.data_width
    if data_width not in _WIDTHS:
        raise ScanCompositionError(f"data_width must be one of {_WIDTHS}, got {data_width}")
    if composition.wide_lane and data_width not in (128, 256, 512):
        raise ScanCompositionError(f"wide_lane data_width must be one of (128, 256, 512), got {data_width}")
    if composition.front_unpack is not None and composition.front_gearbox is not None:
        raise ScanCompositionError("front_unpack and front_gearbox are mutually exclusive front-stage slots")
    if composition.front_gearbox is not None:
        if data_width <= 64:
            raise ScanCompositionError("front_gearbox requires data_width > 64")
        if composition.partitioner is None:
            raise ScanCompositionError("front_gearbox requires a shared partitioner")
        if "RECORD_WORDS" in composition.front_gearbox.params:
            raise ScanCompositionError("front_gearbox RECORD_WORDS is derived from input_row_bytes; do not override it via params")
        if "IN_WIDTH" in composition.front_gearbox.params:
            raise ScanCompositionError("front_gearbox IN_WIDTH is derived from data_width; do not override it via params")
        if "cfg_input_records" in composition.front_gearbox.config:
            raise ScanCompositionError("front_gearbox cfg_input_records is derived from input_length_bytes; do not bind it via config")
        _largest_power_of_two_divisor_bits(composition.input_row_bytes)
        # the router's record framing is DERIVED too. Its IN_WIDTH in
        # whole-record mode is the record bus (RECORD_WORDS * 64), so making
        # the caller hand-set it just reintroduces a mismatch the gearbox's
        # own derivation was designed to make unrepresentable.
        for derived in ("IN_WIDTH", "RECORD_WORDS", "RECORD_INPUT"):
            if derived in composition.partitioner.params:
                raise ScanCompositionError(f"partitioner {derived} is derived from the front gearbox's record size; do not override it via params")

    front_width = composition.front_unpack.params.get("OUT_WIDTH", 64) if composition.front_unpack is not None else 64
    if composition.front_gearbox is not None:
        # derived, so the feed-framing rule below sees the record bus
        fanout_width = (composition.input_row_bytes // 8) * 64
    else:
        fanout_width = composition.partitioner.params.get("IN_WIDTH", 64) if composition.partitioner is not None else 64
    if composition.front_unpack is not None:
        if front_width not in (64, 128, 256, 512, 1024):
            raise ScanCompositionError(f"front_unpack OUT_WIDTH must be 64/128/256/512/1024, got {front_width}")
        if composition.wide_lane:
            # PACKED WIDE FEED: two different widths, and conflating them is
            # the hazard. data_width is the READ — SLOTS packed 64-bit rows
            # per beat — while the lane downstream sees SLOTS whole 128-bit
            # quad rows, which is the front's OUT_WIDTH.
            if front_width < 128:
                raise ScanCompositionError(f"a wide lane consumes whole quad rows; front_unpack OUT_WIDTH={front_width} emits at most one")
            slots = front_width // 128
            if data_width != slots * 64:
                raise ScanCompositionError(
                    f"a packed wide feed reads SLOTS packed 64-bit rows per beat; at SLOTS={slots} that is {slots * 64} bits, "
                    f"not data_width={data_width} (set data_width = SLOTS x 64)"
                )
        elif data_width != 64:
            raise ScanCompositionError(
                f"a packed front unpacker reads 64-bit packed rows; data_width={data_width} (wide packed reads) is a follow-up — "
                "drop the front_unpack to feed quad rows at data_width, or keep data_width=64"
            )
        feed_width = front_width
    elif composition.front_gearbox is not None:
        feed_width = composition.input_row_bytes * 8
    else:
        feed_width = data_width
    # behind a gearbox the router's bus carries one whole RECORD, not a row,
    # so its width is a record size (a 3-word record is 192 bits) and the
    # 64/128/256/512 row ladder does not apply. The record still has to be a
    # size the RTL can elaborate: both the gearbox and the dispatcher guard
    # RECORD_WORDS to [1, 16], and a $fatal is a far worse failure than a
    # composition error — it surfaces mid-synthesis with no attribution.
    if composition.front_gearbox is None:
        if fanout_width not in _WIDTHS:
            raise ScanCompositionError(f"the shared partitioner's IN_WIDTH must be one of {_WIDTHS}, got {fanout_width}")
    elif not (64 <= fanout_width <= _MAX_RECORD_WORDS * 64) or fanout_width % 64:
        raise ScanCompositionError(
            f"a geared record bus must be a whole number of 64-bit words, at most {_MAX_RECORD_WORDS}; got {fanout_width} bits "
            f"({composition.input_row_bytes}-byte records)"
        )
    if feed_width > 64 and not composition.wide_lane:
        if composition.partitioner is None:
            raise ScanCompositionError(
                f"a {feed_width}-bit feed stream needs a shared partitioner/dispatcher accepting IN_WIDTH={feed_width}; "
                "the stream broadcast is 64-bit only (each lane would otherwise see every row and every operator would widen)"
            )
        if fanout_width != feed_width:
            raise ScanCompositionError(
                f"the feed stream is {feed_width}-bit but the shared partitioner {composition.partitioner.module!r} accepts "
                f"IN_WIDTH={fanout_width}; the feed and fan-out widths must agree"
            )
    elif fanout_width != 64:
        raise ScanCompositionError(
            f"the shared partitioner {composition.partitioner.module!r} declares IN_WIDTH={fanout_width} but the feed stream is 64-bit; "
            "widen data_width (quad rows) or compose a front_unpack with OUT_WIDTH=128 to drive a wide fan-out"
        )

    # two-phase load shape (the walker mirror of the registry-aware dau-core
    # rules — structural only, the walker carries no registry): the reserved
    # load_phase binding is legal ONLY on cfg_load at a lane's first chain
    # stage, uniformly across lanes, with nothing between the reader and the
    # load tiles that would transform the LOAD image
    def _binds_load(tile: TileInstance | None) -> bool:
        return tile is not None and tile.config.get("cfg_load") == "load_phase"

    def _stray(tile: TileInstance | None, allowed: bool) -> None:
        if tile is None:
            return
        for key, value in tile.config.items():
            if value == "load_phase" and (key != "cfg_load" or not allowed):
                raise ScanCompositionError(
                    f"tile {tile.module!r} binds {key!r} to the reserved 'load_phase' symbol; only a lane's first chain stage may bind cfg_load"
                )

    _stray(composition.partitioner, allowed=False)
    _stray(composition.front_unpack, allowed=False)
    _stray(composition.front_gearbox, allowed=False)
    for lane in composition.lanes:
        _stray(lane, allowed=False)
        _stray(lane.partition, allowed=False)
        for index, stage in enumerate(lane.chain):
            _stray(stage, allowed=index == 0)
    load_lanes = sum(1 for lane in composition.lanes if lane.chain and _binds_load(lane.chain[0]))
    if load_lanes:
        if load_lanes != len(composition.lanes):
            raise ScanCompositionError(
                f"a two-phase composition must bind cfg_load to 'load_phase' at chain[0] of EVERY lane "
                f"({load_lanes} of {len(composition.lanes)} lanes do); a loadless lane would misread the LOAD image as rows"
            )
        if composition.partitioner is not None:
            raise ScanCompositionError("a two-phase composition cannot use a shared partitioner: the LOAD image must broadcast to every lane")
        if composition.front_unpack is not None:
            raise ScanCompositionError("a two-phase composition cannot use a front unpacker: it would re-slice the LOAD image words")
        for lane in composition.lanes:
            if lane.partition is not None:
                raise ScanCompositionError(
                    f"a two-phase composition cannot use per-lane partition filters: {lane.partition.module!r} would filter the LOAD image"
                )


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


def _m_axi_read_ports_sv(*, iface: str, prefix: str, addr_width: int, data_width: int, burst_beats: int) -> str:
    """The AR/R read-channel ports of one AXI4 memory master."""
    return f"""    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 {iface} ARADDR" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME {iface}, PROTOCOL AXI4, DATA_WIDTH {data_width}, ADDR_WIDTH {addr_width}, HAS_BURST 1, HAS_LOCK 0, HAS_PROT 0, HAS_CACHE 0, HAS_QOS 0, HAS_REGION 0, HAS_WSTRB 1, HAS_BRESP 1, HAS_RRESP 1, MAX_BURST_LENGTH {burst_beats}, SUPPORTS_NARROW_BURST 0" *)
    output wire [{addr_width - 1}:0] {prefix}araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 {iface} ARLEN" *)
    output wire [7:0] {prefix}arlen,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 {iface} ARSIZE" *)
    output wire [2:0] {prefix}arsize,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 {iface} ARBURST" *)
    output wire [1:0] {prefix}arburst,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 {iface} ARVALID" *)
    output wire {prefix}arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 {iface} ARREADY" *)
    input wire {prefix}arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 {iface} RDATA" *)
    input wire [{data_width - 1}:0] {prefix}rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 {iface} RRESP" *)
    input wire [1:0] {prefix}rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 {iface} RLAST" *)
    input wire {prefix}rlast,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 {iface} RVALID" *)
    input wire {prefix}rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 {iface} RREADY" *)
    output wire {prefix}rready"""


def _m_axi_write_ports_sv(*, iface: str, prefix: str, addr_width: int, burst_beats: int, declare_iface: bool) -> str:
    """The AW/W/B write-channel ports of one AXI4 memory master (the record
    writers are always 64-bit — records are 64-bit words). ``declare_iface``
    emits the XIL_INTERFACENAME parameter on AWADDR — set only when this is a
    distinct interface (the split ``M_AXI_W``), not the shared ``M_AXI`` whose
    read block already declared it."""
    param = (
        f'\n    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME {iface}, PROTOCOL AXI4, DATA_WIDTH 64, ADDR_WIDTH {addr_width}, HAS_BURST 1, HAS_LOCK 0, HAS_PROT 0, HAS_CACHE 0, HAS_QOS 0, HAS_REGION 0, HAS_WSTRB 1, HAS_BRESP 1, HAS_RRESP 1, MAX_BURST_LENGTH {burst_beats}, SUPPORTS_NARROW_BURST 0" *)'
        if declare_iface
        else ""
    )
    return f"""    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 {iface} AWADDR" *){param}
    output wire [{addr_width - 1}:0] {prefix}awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 {iface} AWLEN" *)
    output wire [7:0] {prefix}awlen,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 {iface} AWSIZE" *)
    output wire [2:0] {prefix}awsize,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 {iface} AWBURST" *)
    output wire [1:0] {prefix}awburst,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 {iface} AWVALID" *)
    output wire {prefix}awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 {iface} AWREADY" *)
    input wire {prefix}awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 {iface} WDATA" *)
    output wire [63:0] {prefix}wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 {iface} WSTRB" *)
    output wire [7:0] {prefix}wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 {iface} WLAST" *)
    output wire {prefix}wlast,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 {iface} WVALID" *)
    output wire {prefix}wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 {iface} WREADY" *)
    input wire {prefix}wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 {iface} BRESP" *)
    input wire [1:0] {prefix}bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 {iface} BVALID" *)
    input wire {prefix}bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 {iface} BREADY" *)
    output wire {prefix}bready"""


def _m_axi_ports_sv(*, addr_width: int, burst_beats: int, data_width: int = 64) -> str:
    """The AXI4 memory-master port block. At data_width=64 a SINGLE shared
    ``M_AXI`` carries read and write (byte-identical to the classic top). At
    128/256/512 the read master splits off: a wide read-only ``M_AXI_R`` (the
    burst reader's DATA_WIDTH) plus the narrow write-only ``M_AXI_W`` (the
    64-bit record writers), so a wide DDR read never forces narrow writes on a
    wide bus. The two connect to the MIG through the memory smartconnect."""
    if data_width == 64:
        read = _m_axi_read_ports_sv(iface="M_AXI", prefix="m_axi_", addr_width=addr_width, data_width=64, burst_beats=burst_beats)
        write = _m_axi_write_ports_sv(iface="M_AXI", prefix="m_axi_", addr_width=addr_width, burst_beats=burst_beats, declare_iface=False)
        return read + ",\n" + write
    read = _m_axi_read_ports_sv(iface="M_AXI_R", prefix="m_axi_r_", addr_width=addr_width, data_width=data_width, burst_beats=burst_beats)
    write = _m_axi_write_ports_sv(iface="M_AXI_W", prefix="m_axi_w_", addr_width=addr_width, burst_beats=burst_beats, declare_iface=True)
    return read + ",\n" + write


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
        .PLATFORM_ID(PLATFORM_ID),
        .OPERATOR_BITMAP(OPERATOR_BITMAP),
        .LANE_COUNT(LANE_COUNT),
        .HOST_OPCODE_BITMAP(HOST_OPCODE_BITMAP),
        .SORT_CAPACITY(SORT_CAPACITY),
        .BUILD_ID(BUILD_ID)
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


def _tile_param_override_sv(tile: TileInstance) -> str:
    """A SystemVerilog module-parameter override for a tile instance
    (``dau_int32_X #(.KEY_SPACE(6000000)) inst (...)``), inserted between the
    module name and the instance name. Empty string when the tile carries no
    params — a param-less tile emits byte-identically to before the
    module-parameter channel, so every existing golden stays green."""
    if not tile.params:
        return ""
    binds = ",\n".join(f"        .{name}({value})" for name, value in tile.params.items())
    return f" #(\n{binds}\n    )"


def _wide_lane_source(composition: ScanComposition) -> tuple[int, str]:
    """The stream a WIDE lane actually consumes: width, and the signal prefix.

    Straight off the reader that is ``scan_*`` at ``data_width``. Behind a
    packed front unpacker it is ``feed_*`` at the unpacker's OUT_WIDTH --
    data_width there is the PACKED read and is half as wide, so wiring the
    lane to scan_* would feed it packed rows as though they were quad rows.
    The unpacker also already drives ``scan_ready``, so a lane driving it too
    would be a second driver on the same net.
    """
    if composition.front_unpack is not None:
        return _front_stream_width(composition), "feed"
    return composition.data_width, "scan"


def _lane_front_sv(composition: ScanComposition, i: int, *, clk: str = "s_axi_aclk") -> str:
    """The lane front for lane ``i``: tap the shared partitioner's per-lane
    stream, tap the broadcast directly (filterless lane), or instantiate the
    lane's partition filter off the broadcast."""
    lane = composition.lanes[i]
    if composition.wide_lane:
        _, src = _wide_lane_source(composition)
        return f"""    assign filt_out_valid_{i} = {src}_valid;
    assign {src}_ready = filt_out_ready_{i};
    assign filt_out_data_{i} = {src}_data;
    assign filt_out_last_{i} = {src}_last;
    assign filt_status_valid_{i} = 1'b0;
    assign filt_status_ready_{i} = 1'b0;
    assign filt_status_error_{i} = 1'b0;
    assign filt_status_error_code_{i} = 8'd0;
"""
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
    return f"""    {lane.partition.module}{_tile_param_override_sv(lane.partition)} partition_{i} (
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


def _fused_chain_status_glue_sv(composition: ScanComposition, i: int, draining: list[int], *, clk: str, rst: str, start: str) -> str:
    """Status glue for a lane whose chain fuses terminal-shaped stages.

    Each draining stage's status is accepted unconditionally (``ready`` tied
    high), so its success close-out is consumed the cycle it appears and the
    stage rearms for the next job — the lane still presents exactly ONE
    close-out (the terminal's), which is what the record writer's done waits
    on. A draining stage's ERROR is latched into a per-lane register and
    overrides the lane status, so a fused-away failure is never silently
    swallowed; the latch clears on the job-start pulse the register block
    already drives (``job_start``), making the protocol multi-job safe.
    Silent stages keep the ordinary upstream-first mux beneath the latch."""
    lane = composition.lanes[i]
    front_filtered = not composition.wide_lane and (lane.partition is not None or composition.partitioner is not None)
    silent = [j for j in range(len(lane.chain)) if j not in draining]
    stems = (["filt"] if front_filtered else []) + [f"chain{j}" for j in silent]

    # a draining stage's status is consumed the cycle it appears, so an error
    # missed here is gone for good: CAPTURE OUTRANKS the per-job clear. An
    # error coinciding with job_start belongs to the job just ending and is
    # carried into the new job's status rather than silently dropped — a loud
    # failure on the next job beats a clean report over a corrupt one.
    raising = " || ".join(f"(chain{j}_status_valid_{i} && chain{j}_status_error_{i})" for j in draining)
    # THE EMPTY-BATCH CLOSE. A draining stage that produced NO output (an
    # all-miss as-of join, a filter that kept nothing) starves everything
    # downstream: no row means no output_last, so no downstream stage — and
    # not the terminal — ever closes, and the record writer would wait
    # forever. That stage's own close-out is the only end-of-batch evidence
    # in the lane, so when it closes empty the lane closes with it. Tracked
    # per stage: `saw_out` sets on the stage's first output beat and clears
    # per job. Only the most upstream empty stage can fire (a starved
    # downstream stage never closes at all), so the lane still presents one
    # close-out.
    lines = [
        f"    reg fused_err_{i};",
        f"    reg [7:0] fused_err_code_{i};",
        f"    always @(posedge {clk}) begin",
        f"        if ({rst}) begin",
        f"            fused_err_{i} <= 1'b0;",
        f"            fused_err_code_{i} <= 8'd0;",
        f"        end else if (!fused_err_{i} && ({raising})) begin",
        f"            fused_err_{i} <= 1'b1;",
    ]
    for index, j in enumerate(draining):
        keyword = "if" if index == 0 else "else if"
        lines.append(f"            {keyword} (chain{j}_status_valid_{i} && chain{j}_status_error_{i}) begin")
        lines.append(f"                fused_err_code_{i} <= chain{j}_status_error_code_{i};")
        lines.append("            end")
    lines.append(f"        end else if ({start}) begin")
    lines.append(f"            fused_err_{i} <= 1'b0;")
    lines.append(f"            fused_err_code_{i} <= 8'd0;")
    lines.append("        end")
    lines.append("    end")

    # the draining stages accept their own status immediately
    for j in draining:
        lines.append(f"    assign chain{j}_status_ready_{i} = 1'b1;")

    # per-stage output-seen tracking and the empty-close pulse
    for j in draining:
        lines.append(f"    reg saw_out_{i}_{j};")
        lines.append(f"    reg empty_close_{i}_{j};")
    lines.append(f"    always @(posedge {clk}) begin")
    lines.append(f"        if ({rst}) begin")
    for j in draining:
        lines.append(f"            saw_out_{i}_{j} <= 1'b0;")
        lines.append(f"            empty_close_{i}_{j} <= 1'b0;")
    lines.append(f"        end else if ({start}) begin")
    for j in draining:
        lines.append(f"            saw_out_{i}_{j} <= 1'b0;")
        lines.append(f"            empty_close_{i}_{j} <= 1'b0;")
    lines.append("        end else begin")
    for j in draining:
        lines.append(f"            if (chain{j}_out_valid_{i} && chain{j}_out_ready_{i}) begin")
        lines.append(f"                saw_out_{i}_{j} <= 1'b1;")
        lines.append("            end")
        lines.append(
            f"            if (chain{j}_status_valid_{i} && !chain{j}_status_error_{i} && !saw_out_{i}_{j}"
            f" && !(chain{j}_out_valid_{i} && chain{j}_out_ready_{i})) begin"
        )
        lines.append(f"                empty_close_{i}_{j} <= 1'b1;")
        lines.append("            end")
    lines.append("        end")
    lines.append("    end")
    empty_close = " || ".join(f"empty_close_{i}_{j}" for j in draining)
    lines.append(f"    wire fused_empty_close_{i} = {empty_close};")

    # beneath the latch, the silent stages and the terminal keep the
    # upstream-first mux (the pending-order rule the chain contract defines)
    valids = " || ".join(f"{stem}_status_valid_{i}" for stem in stems)
    base_valid = f"tile_status_valid_{i}" + (f" || {valids}" if stems else "")
    error_mux = f"tile_status_error_{i}"
    code_mux = f"tile_status_error_code_{i}"
    for stem in reversed(stems):
        error_mux = f"{stem}_status_valid_{i} ? {stem}_status_error_{i} : {error_mux}"
        code_mux = f"{stem}_status_valid_{i} ? {stem}_status_error_code_{i} : {code_mux}"
    lines.append(f"    assign unit_status_valid_{i} = fused_err_{i} || fused_empty_close_{i} || ({base_valid});")
    lines.append(f"    assign unit_status_error_{i} = fused_err_{i} ? 1'b1 : ({error_mux});")
    lines.append(f"    assign unit_status_error_code_{i} = fused_err_{i} ? fused_err_code_{i} : ({code_mux});")

    upstream: list[str] = []
    for stem in stems:
        gate = "".join(f" && !{name}_status_valid_{i}" for name in upstream)
        lines.append(f"    assign {stem}_status_ready_{i} = unit_status_ready_{i}{gate} && {stem}_status_valid_{i};")
        upstream.append(stem)
    gate = "".join(f" && !{name}_status_valid_{i}" for name in upstream)
    lines.append(f"    assign tile_status_ready_{i} = unit_status_ready_{i}{gate};")
    return "\n".join(lines) + "\n"


def _lane_status_glue_sv(
    composition: ScanComposition, i: int, *, clk: str = "s_axi_aclk", rst: str = "!s_axi_aresetn", start: str = "job_start"
) -> str:
    """The per-unit status mux for lane ``i``: a filterless lane forwards
    the tile status; a filtered lane muxes the partition status (which
    wins) over the tile status; a chained lane muxes every stage's status
    upstream-first (the most upstream pending status wins, and each stage's
    ready fires only when nothing upstream of it is pending)."""
    lane = composition.lanes[i]
    if lane.chain:
        front_filtered = not composition.wide_lane and (lane.partition is not None or composition.partitioner is not None)
        # the FUSED-CHAIN protocol: a chain stage that closes out on success is
        # terminal-shaped fused mid-lane (as-of join, grouped aggregator). Its
        # status is drained every batch (accepted the cycle it is raised, so it
        # rearms for the next job) and only its ERRORS latch into the lane
        # status; the terminal tile remains the lane's one close-out. Silent
        # stages keep the byte-identical upstream-first mux.
        draining = [j for j, stage in enumerate(lane.chain) if stage.closes_out]
        if draining:
            return _fused_chain_status_glue_sv(composition, i, draining, clk=clk, rst=rst, start=start)
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
    stream_width = _wide_lane_source(composition)[0] if composition.wide_lane else 64
    return "".join(
        f"""    wire chain{j}_out_valid_{i};
    wire chain{j}_out_ready_{i};
    wire [{stream_width - 1}:0] chain{j}_out_data_{i};
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
    stream_width = _wide_lane_source(composition)[0] if composition.wide_lane else 64
    return "\n".join(
        f"""    wire filt_out_valid_{i};
    wire filt_out_ready_{i};
    wire [{stream_width - 1}:0] filt_out_data_{i};
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
            f"""    {stage.module}{_tile_param_override_sv(stage)} chain_{i}_{j} (
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


def _lane_units_sv(composition: ScanComposition, *, clk: str, writer_rst: str, start: str = "job_start") -> str:
    """Every lane unit: front (partitioner tap / broadcast tap / partition
    filter), chain stages, operator tile, status glue, and record writer."""
    addr_width = composition.addr_width
    burst_beats = composition.burst_beats
    return "\n\n".join(
        f"""{_lane_front_sv(composition, i, clk=clk)}
{_lane_chain_sv(composition, i, clk=clk)}    {composition.lanes[i].module}{_tile_param_override_sv(composition.lanes[i])} tile_{i} (
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

{_lane_status_glue_sv(composition, i, clk=clk, rst=writer_rst, start=start)}
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


def _fanout_sv(composition: ScanComposition, *, clk: str, stream_prefix: str = "scan") -> tuple[str, str]:
    """The scan fan-out: wire declarations and the instance — the shared
    partitioner when the composition carries one, the stream broadcast
    otherwise. ``stream_prefix`` names the row stream driving the fan-out
    input (``scan`` from the burst reader by default, ``feed`` from the
    front unpacker, or ``geared`` from the front gearbox)."""
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
        if "NUM_PARTITIONS" in composition.partitioner.params:
            raise ScanCompositionError("the shared partitioner's NUM_PARTITIONS is derived from the lane count; do not override it via params")
        derived_params = dict(composition.partitioner.params)
        if composition.front_gearbox is not None:
            # whole-record framing, derived from the composition's record size
            record_words = composition.input_row_bytes // 8
            derived_params.update({"RECORD_WORDS": record_words, "RECORD_INPUT": 1, "IN_WIDTH": record_words * 64})
        partitioner_extra_params = "".join(f",\n        .{name}({value})" for name, value in sorted(derived_params.items()))
        instance = f"""    {composition.partitioner.module} #(
        .NUM_PARTITIONS({num_lanes}){partitioner_extra_params}
    ) partitioner (
        .clk({clk}),
        .rst(lane_rst),
{_tile_config_binds_sv(composition.partitioner.config)}        .input_valid({stream_prefix}_valid),
        .input_ready({stream_prefix}_ready),
        .input_data({stream_prefix}_data),
        .input_last({stream_prefix}_last),
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
        .input_valid({stream_prefix}_valid),
        .input_ready({stream_prefix}_ready),
        .input_data({stream_prefix}_data),
        .input_last({stream_prefix}_last),
        .output_valid(bcast_valid),
        .output_ready(bcast_ready),
        .output_data(bcast_data),
        .output_last(bcast_last)
    );"""
    return wire_decls, instance


def _front_stream_width(composition: ScanComposition) -> int:
    """The feed stream's bit width: the front unpacker's ``OUT_WIDTH``
    module parameter (the rows/cycle axis — 64 emits two beats per quad row,
    128 one whole row per beat), defaulting to the classic 64-bit framing
    when the parameter is not overridden."""
    if composition.front_unpack is None:
        return 64
    return composition.front_unpack.params.get("OUT_WIDTH", 64)


def _uses_load_phase(composition: ScanComposition) -> bool:
    """Whether any tile binds a config port to the reserved ``load_phase``
    symbol — the two-phase load: the top then declares the LOAD_PHASE
    register that drives those bindings."""
    tiles: list[TileInstance] = []
    if composition.partitioner is not None:
        tiles.append(composition.partitioner)
    if composition.front_unpack is not None:
        tiles.append(composition.front_unpack)
    if composition.front_gearbox is not None:
        tiles.append(composition.front_gearbox)
    for lane in composition.lanes:
        tiles.append(lane)
        if lane.partition is not None:
            tiles.append(lane.partition)
        tiles.extend(lane.chain)
    return any(value == "load_phase" for tile in tiles for value in tile.config.values())


def _front_unpack_wire_decls_sv(composition: ScanComposition) -> str:
    """The front unpacker's widened row stream (``feed_*``, driving the
    fan-out input at the composed ``OUT_WIDTH``), its status bundle, and the
    sticky error latch. Empty string when the composition carries no front
    unpacker, so the block stays byte-identical."""
    if composition.front_unpack is None:
        return ""
    return f"""    wire feed_valid;
    wire feed_ready;
    wire [{_front_stream_width(composition) - 1}:0] feed_data;
    wire feed_last;
    wire front_unpack_status_valid;
    wire front_unpack_status_ready;
    wire front_unpack_status_error;
    wire [7:0] front_unpack_status_error_code;
    reg front_unpack_error;
    reg [7:0] front_unpack_error_code;
"""


def _front_unpack_instance_sv(composition: ScanComposition, *, clk: str) -> str:
    """The front unpacker instance: the burst reader's row stream
    (``scan_*``) in, the widened row stream (``feed_*``) out to the fan-out,
    its config the four field descriptors + ``cfg_bypass``. Its status is a
    front-stage close-out (silent on success, ERR_CONFIG/ERR_STREAM only):
    consumed here (``status_ready`` tied high) and latched into the sticky
    ``front_unpack_error`` reg for the job error mux. Unlike the reader — which
    flushes the batch's ``last`` even on a read error, so the lanes still
    close out — the unpacker withholds ``last`` on a bad-config / torn-row
    batch, so that batch never completes the lane writers (they re-arm only
    from ``UNIT_IDLE`` and reset only on the peripheral ``aresetn``). The
    error is therefore a fatal job abort: it is held for the LAST_ERROR
    readback until the host issues a peripheral reset. A well-formed feed
    (valid config, whole rows) never raises it. Empty string when the
    composition carries no front unpacker, so the emission stays
    byte-identical."""
    tile = composition.front_unpack
    if tile is None:
        return ""
    return f"""    {tile.module}{_tile_param_override_sv(tile)} front_unpack (
        .clk({clk}),
        .rst(lane_rst),
{_tile_config_binds_sv(tile.config)}        .input_valid(scan_valid),
        .input_ready(scan_ready),
        .input_data(scan_data),
        .input_last(scan_last),
        .output_valid(feed_valid),
        .output_ready(feed_ready),
        .output_data(feed_data),
        .output_last(feed_last),
        .status_valid(front_unpack_status_valid),
        .status_ready(front_unpack_status_ready),
        .status_error(front_unpack_status_error),
        .status_error_code(front_unpack_status_error_code)
    );

    assign front_unpack_status_ready = 1'b1;

"""


def _front_unpack_error_branch_sv(composition: ScanComposition, *, error: str, error_code: str) -> str:
    """The first-error-wins branch that surfaces the latched front-unpack
    error into the job error mux, wedged between the reader's branch and the
    lane writers'. Reads the sticky ``front_unpack_error`` reg (the close-out
    is a one-cycle pulse), so LAST_ERROR holds the code until the host resets.
    Empty string when the composition carries no front unpacker, so the mux
    stays byte-identical."""
    if composition.front_unpack is None:
        return ""
    return f"""            end else if (front_unpack_error) begin
                {error} = 1'b1;
                {error_code} = front_unpack_error_code;
"""


def _front_unpack_error_reset_sv(composition: ScanComposition) -> str:
    """Reset fragment (12-space indent) clearing the latched front-unpack
    error. Empty string when no front unpacker."""
    if composition.front_unpack is None:
        return ""
    return "            front_unpack_error <= 1'b0;\n            front_unpack_error_code <= 8'd0;\n"


def _front_unpack_error_latch_sv(composition: ScanComposition) -> str:
    """Latch fragment (12-space indent): a front-unpack close-out error is
    sampled sticky (``status_ready`` is tied high, so the close-out is one
    cycle) and held until a peripheral reset. A torn/bad-config feed hangs the
    lane writers (no ``last`` arrives) and they recover only on reset, so the
    error must NOT be cleared on job start. Empty string when no front
    unpacker."""
    if composition.front_unpack is None:
        return ""
    return (
        "            if (front_unpack_status_valid && front_unpack_status_error) begin\n"
        "                front_unpack_error <= 1'b1;\n"
        "                front_unpack_error_code <= front_unpack_status_error_code;\n"
        "            end\n"
    )


def _front_gearbox_wire_decls_sv(composition: ScanComposition) -> str:
    """Whole-record stream, status bundle, and sticky gearbox error."""
    if composition.front_gearbox is None:
        return ""
    return f"""    wire geared_valid;
    wire geared_ready;
    wire [{composition.input_row_bytes * 8 - 1}:0] geared_data;
    wire geared_last;
    wire front_gearbox_status_valid;
    wire front_gearbox_status_ready;
    wire front_gearbox_status_error;
    wire [7:0] front_gearbox_status_error_code;
    reg front_gearbox_error;
    reg [7:0] front_gearbox_error_code;
"""


def _front_gearbox_instance_sv(composition: ScanComposition, *, clk: str) -> str:
    """Wide reader stream in, one whole record per beat out to dispatcher."""
    tile = composition.front_gearbox
    if tile is None:
        return ""
    extra_params = "".join(f",\n        .{name}({value})" for name, value in tile.params.items())
    return f"""    {tile.module} #(
        .IN_WIDTH({composition.data_width}),
        .RECORD_WORDS({composition.input_row_bytes // 8}){extra_params}
    ) front_gearbox (
        .clk({clk}),
        .rst(lane_rst),
        .cfg_input_records(input_length_bytes / 32'd{composition.input_row_bytes}),
{_tile_config_binds_sv(tile.config)}        .input_valid(scan_valid),
        .input_ready(scan_ready),
        .input_data(scan_data),
        .input_last(scan_last),
        .output_valid(geared_valid),
        .output_ready(geared_ready),
        .output_data(geared_data),
        .output_last(geared_last),
        .status_valid(front_gearbox_status_valid),
        .status_ready(front_gearbox_status_ready),
        .status_error(front_gearbox_status_error),
        .status_error_code(front_gearbox_status_error_code)
    );

    assign front_gearbox_status_ready = 1'b1;

"""


def _front_gearbox_error_branch_sv(composition: ScanComposition, *, error: str, error_code: str) -> str:
    """Surface the sticky gearbox error after reader errors."""
    if composition.front_gearbox is None:
        return ""
    return f"""            end else if (front_gearbox_error) begin
                {error} = 1'b1;
                {error_code} = front_gearbox_error_code;
"""


def _front_gearbox_error_reset_sv(composition: ScanComposition) -> str:
    """Reset fragment clearing the sticky gearbox error."""
    if composition.front_gearbox is None:
        return ""
    return "            front_gearbox_error <= 1'b0;\n            front_gearbox_error_code <= 8'd0;\n"


def _front_gearbox_error_latch_sv(composition: ScanComposition) -> str:
    """Latch gearbox close-out errors until peripheral reset."""
    if composition.front_gearbox is None:
        return ""
    return (
        "            if (front_gearbox_status_valid && front_gearbox_status_error) begin\n"
        "                front_gearbox_error <= 1'b1;\n"
        "                front_gearbox_error_code <= front_gearbox_status_error_code;\n"
        "            end\n"
    )


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
    if composition.front_unpack is not None:
        tiles.append((composition.front_unpack, None))
    if composition.front_gearbox is not None:
        tiles.append((composition.front_gearbox, None))
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
    platform_id: str,
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
    _validate_composition_shape(composition)  # model_copy skips model_post_init
    if sources is not None:
        _validate_against_sources(composition, sources)
    # the on-wire platform identity word, encoded little-endian into a single
    # 32-bit PLATFORM_ID parameter (inline so this public generator stays free
    # of the private core's encoder)
    platform_id_bytes = platform_id.encode("ascii")
    if not 1 <= len(platform_id_bytes) <= 4:
        raise ValueError(f"platform_id must be 1 to 4 ASCII bytes, got {platform_id!r}")
    platform_id_u32 = int.from_bytes(platform_id_bytes.ljust(4, b"\x00"), "little")
    regs = composition.registers
    addr_width = composition.addr_width
    burst_beats = composition.burst_beats
    data_width = composition.data_width
    # single shared M_AXI at 64-bit; a split wide read (M_AXI_R) + narrow
    # write (M_AXI_W) at wider tiers — the reader and write mux bind the top
    # signals through these prefixes (both "m_axi_" at 64, byte-identical)
    read_bus = "m_axi_" if data_width == 64 else "m_axi_r_"
    write_bus = "m_axi_" if data_width == 64 else "m_axi_w_"
    # emit the reader DATA_WIDTH param only when widened, so the 64-bit top
    # stays byte-identical to the pre-width goldens (the RTL default is 64)
    reader_data_width_param = "" if data_width == 64 else f"        .DATA_WIDTH({data_width}),\n"
    # the S_AXI clock associates with the memory master(s): the shared M_AXI at
    # 64, or the split M_AXI_R + M_AXI_W when widened
    associated_busif = "S_AXI:M_AXI" if data_width == 64 else "S_AXI:M_AXI_R:M_AXI_W"
    module_name = composition.module_name
    num_lanes = len(composition.lanes)
    lanes = range(num_lanes)
    lane_localparams = "\n".join(
        f"    localparam [11:0] ADDR_LANE{i}_OUTPUT_ADDRESS = 12'h{regs.lane_register(i, regs.lane_output_address_low):03X};\n"
        f"    localparam [11:0] ADDR_LANE{i}_RESULT_LENGTH = 12'h{regs.lane_register(i, regs.lane_result_length_low):03X};\n"
        f"    localparam [11:0] ADDR_LANE{i}_RECORD_COUNT_LOW = 12'h{regs.lane_register(i, regs.lane_record_count_low):03X};\n"
        f"    localparam [11:0] ADDR_LANE{i}_RECORD_COUNT_HIGH = 12'h{regs.lane_register(i, regs.lane_record_count_high):03X};\n"
        f"    localparam [11:0] ADDR_LANE{i}_ERROR = 12'h{regs.lane_register(i, regs.lane_error):03X};"
        + (
            f"\n    localparam [11:0] ADDR_LANE{i}_OUTPUT_ADDRESS_HIGH = 12'h{regs.lane_register(i, regs.lane_output_address_high):03X};"
            if addr_width > 32
            else ""
        )
        for i in lanes
    )
    uses_load_phase = _uses_load_phase(composition)
    lane_reg_decls = "\n".join(f"    reg [{addr_width - 1}:0] lane_output_address_{i};" for i in lanes)
    load_phase_decl = "    reg load_phase;\n" if uses_load_phase else ""
    lane_wire_decls = _lane_wire_decls_sv(composition)
    lane_flat_assigns = _lane_flat_assigns_sv(composition)
    lane_instances = _lane_units_sv(composition, clk="s_axi_aclk", writer_rst="!s_axi_aresetn")
    stream_prefix = "scan"
    if composition.front_unpack is not None:
        stream_prefix = "feed"
    elif composition.front_gearbox is not None:
        stream_prefix = "geared"
    fanout_wire_decls, fanout_instance = ("", "") if composition.wide_lane else _fanout_sv(composition, clk="s_axi_aclk", stream_prefix=stream_prefix)
    front_unpack_wire_decls = _front_unpack_wire_decls_sv(composition)
    front_unpack_instance = _front_unpack_instance_sv(composition, clk="s_axi_aclk")
    front_gearbox_wire_decls = _front_gearbox_wire_decls_sv(composition)
    front_gearbox_instance = _front_gearbox_instance_sv(composition, clk="s_axi_aclk")
    front_stage_error_branch = _front_unpack_error_branch_sv(
        composition, error="job_error", error_code="job_error_code"
    ) + _front_gearbox_error_branch_sv(composition, error="job_error", error_code="job_error_code")
    front_stage_error_reset = _front_unpack_error_reset_sv(composition) + _front_gearbox_error_reset_sv(composition)
    front_stage_error_latch = _front_unpack_error_latch_sv(composition) + _front_gearbox_error_latch_sv(composition)
    # packed rows land one per 8-byte word (unpack mode); pre-widened quad
    # rows are two 8-byte words. Both sit on an 8-byte grid, so a front-unpack
    # composition relaxes the reader/length gate from the 16-byte quad grid to
    # 8 bytes (an odd packed-row count would round to a non-16-multiple length
    # and be rejected otherwise); bypass-mode odd-word framing stays the
    # unpacker's ERR_STREAM job.
    # the input length must be a whole number of rows AND of reader beats: at
    # a wide bus (data_width > 64) a beat spans several rows, so beat
    # alignment (log2(data_width/8)) dominates the row grid (3 packed / 4 quad)
    _row_align = 3 if composition.front_unpack is not None else _largest_power_of_two_divisor_bits(composition.input_row_bytes)
    _beat_align = (data_width // 8).bit_length() - 1
    length_align_bits = max(_row_align, _beat_align)
    grid_bytes = 1 << length_align_bits
    length_ok_check = f"input_length_bytes[{length_align_bits - 1}:0] == {length_align_bits}'d0"

    all_writers_done = " && ".join(f"writer_done_{i}" for i in lanes)
    any_writer_busy = " || ".join(f"writer_busy_{i}" for i in lanes)
    error_priority = _writer_error_priority_sv(num_lanes, error="job_error", error_code="job_error_code")

    def lane_high_read(i: int) -> str:
        if addr_width <= 32:
            return ""
        return f"\n                    ADDR_LANE{i}_OUTPUT_ADDRESS_HIGH: s_axi_rdata <= lane_output_address_{i}[{addr_width - 1}:32];"

    # the lane write address takes the same low/high treatment as the job read
    # address: a bare 32-bit write into a wider register leaves the top bits
    # unreachable, so a lane could only ever write below 4 GiB
    if addr_width > 32:
        write_case_items = "\n".join(
            f"                    ADDR_LANE{i}_OUTPUT_ADDRESS: lane_output_address_{i}[31:0] <= s_axi_wdata;\n"
            f"                    ADDR_LANE{i}_OUTPUT_ADDRESS_HIGH: lane_output_address_{i}[{addr_width - 1}:32] <= s_axi_wdata[{addr_width - 33}:0];"
            for i in lanes
        )
    else:
        write_case_items = "\n".join(f"                    ADDR_LANE{i}_OUTPUT_ADDRESS: lane_output_address_{i} <= s_axi_wdata;" for i in lanes)
    read_case_items = "\n".join(
        f"""                    ADDR_LANE{i}_OUTPUT_ADDRESS: s_axi_rdata <= lane_output_address_{i}[31:0];
                    ADDR_LANE{i}_RESULT_LENGTH: s_axi_rdata <= lane_result_length_{i};
                    ADDR_LANE{i}_RECORD_COUNT_LOW: s_axi_rdata <= lane_bar_count_{i}[31:0];
                    ADDR_LANE{i}_RECORD_COUNT_HIGH: s_axi_rdata <= lane_bar_count_{i}[63:32];
                    ADDR_LANE{i}_ERROR: s_axi_rdata <= {{24'd0, writer_error_code_{i}}};{lane_high_read(i)}"""
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
    wide_address = addr_width > 32
    input_address_low_write = (
        "                    ADDR_INPUT_ADDRESS_LOW: input_address[31:0] <= s_axi_wdata;\n"
        if wide_address
        else f"                    ADDR_INPUT_ADDRESS_LOW: input_address <= s_axi_wdata[{addr_width - 1}:0];\n"
    )
    input_address_high_write = (
        f"                    ADDR_INPUT_ADDRESS_HIGH: input_address[{addr_width - 1}:32] <= s_axi_wdata[{addr_width - 33}:0];\n"
        if wide_address
        else ""
    )
    # a narrower-than-32 slice zero-extends on assignment to the 32-bit read
    # data, so no concatenation is needed (and a 64-bit master's high half is
    # exactly 32 bits, where a pad would be zero-width and illegal)
    input_address_high_read = (
        f"\n                    ADDR_INPUT_ADDRESS_HIGH: s_axi_rdata <= input_address[{addr_width - 1}:32];" if wide_address else ""
    )
    register_names: tuple[tuple[str, int], ...] = (
        ("LAST_ERROR", regs.last_error),
        ("JOB_CONTROL", regs.job_control),
        ("JOB_STATUS", regs.job_status),
        ("INPUT_ADDRESS_LOW", regs.input_address_low),
        ("INPUT_LENGTH_LOW", regs.input_length_low),
    )
    if wide_address:
        register_names = register_names + (("INPUT_ADDRESS_HIGH", regs.input_address_high),)
    if uses_load_phase:
        register_names = register_names + (("LOAD_PHASE", regs.load_phase),)
    localparams = _register_localparams_sv(register_names)
    load_phase_reset = "            load_phase <= 1'b0;\n" if uses_load_phase else ""
    load_phase_write = "                    ADDR_LOAD_PHASE: load_phase <= s_axi_wdata[0];\n" if uses_load_phase else ""
    # leading newline so an unused register leaves the read block byte-identical
    load_phase_read = "\n                    ADDR_LOAD_PHASE: s_axi_rdata <= {31'd0, load_phase};" if uses_load_phase else ""
    # THE HIGH HALF OF THE JOB ADDRESS. AXI-Lite carries 32 bits per access,
    # so a job master wider than 32 bits needs a second register or its top
    # bits are unreachable — which is exactly why a build could only ever
    # map the low 4 GiB. Emitted ONLY when the master is actually wider, so
    # a 32-bit design's register block stays byte-identical.
    register_process = _axi_lite_register_process_sv(
        write_default_comment="other job fields accepted and ignored",
        reset_extra=f"""            input_address <= {addr_width}'d0;
            input_length_bytes <= 32'd0;
            job_start <= 1'b0;
            length_fail <= 1'b0;
{load_phase_reset}{front_stage_error_reset}            prev_done <= 1'b1;
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
{front_stage_error_latch}{lane_count_latch_items}
""",
        write_cases_extra=f"""{input_address_low_write}{input_address_high_write}                    ADDR_INPUT_LENGTH_LOW: input_length_bytes <= s_axi_wdata;
{load_phase_write}{write_case_items}
""",
        read_cases_extra=f"""                    ADDR_INPUT_ADDRESS_LOW: s_axi_rdata <= input_address[31:0];{input_address_high_read}
                    ADDR_INPUT_LENGTH_LOW: s_axi_rdata <= input_length_bytes;{load_phase_read}
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
    parameter [31:0] PLATFORM_ID = 32'h{platform_id_u32:08X},
    parameter [31:0] OPERATOR_BITMAP = 32'h{composition.operator_bitmap:08X},
    parameter [31:0] LANE_COUNT = 32'd{num_lanes},
    parameter [31:0] HOST_OPCODE_BITMAP = 32'h{composition.host_opcode_bitmap:08X},
    parameter [31:0] SORT_CAPACITY = 32'd{composition.sort_capacity},
    parameter [63:0] BUILD_ID = 64'h{composition.build_id:016X}
) (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 s_axi_aclk CLK" *)
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF {associated_busif}, ASSOCIATED_RESET s_axi_aresetn" *)
    input wire s_axi_aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 s_axi_aresetn RST" *)
    (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    input wire s_axi_aresetn,

{_s_axi_lite_ports_sv()}

{_m_axi_ports_sv(addr_width=addr_width, burst_beats=burst_beats, data_width=data_width)}
);
    // window-relative decode (the AXI address carries the BAR offset)
{localparams}
{lane_localparams}

{_axi_lite_decode_wires_sv()}

    reg [{addr_width - 1}:0] input_address;
    reg [31:0] input_length_bytes;
    reg job_start;
{load_phase_decl}{lane_reg_decls}

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
    wire [{data_width - 1}:0] scan_data;
    wire scan_last;
{front_unpack_wire_decls}{front_gearbox_wire_decls}{fanout_wire_decls}
{_wr_flat_decls_sv(composition)}
{lane_wire_decls}

    // the {grid_bytes}-byte row grid is enforced before any unit starts: a rejected
    // length must not leave the writers waiting on a status
    reg length_fail;
    wire length_ok = (input_length_bytes != 32'd0) && ({length_ok_check});
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
{front_stage_error_branch}{error_priority}
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
{reader_data_width_param}        .LENGTH_ALIGN_BITS({length_align_bits})
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
        .m_axi_araddr({read_bus}araddr),
        .m_axi_arlen({read_bus}arlen),
        .m_axi_arsize({read_bus}arsize),
        .m_axi_arburst({read_bus}arburst),
        .m_axi_arvalid({read_bus}arvalid),
        .m_axi_arready({read_bus}arready),
        .m_axi_rdata({read_bus}rdata),
        .m_axi_rresp({read_bus}rresp),
        .m_axi_rlast({read_bus}rlast),
        .m_axi_rvalid({read_bus}rvalid),
        .m_axi_rready({read_bus}rready),
        .stream_valid(scan_valid),
        .stream_ready(scan_ready),
        .stream_data(scan_data),
        .stream_last(scan_last),
        .dbg_first_stream_word(dbg_first_stream_word),
        .dbg_first_araddr(dbg_first_araddr),
        .dbg_beats_while_idle(dbg_beats_while_idle),
        .dbg_final_fifo_count(dbg_final_fifo_count)
    );

{front_unpack_instance}{front_gearbox_instance}{fanout_instance}

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
        .m_axi_awaddr({write_bus}awaddr),
        .m_axi_awlen({write_bus}awlen),
        .m_axi_awsize({write_bus}awsize),
        .m_axi_awburst({write_bus}awburst),
        .m_axi_awvalid({write_bus}awvalid),
        .m_axi_awready({write_bus}awready),
        .m_axi_wdata({write_bus}wdata),
        .m_axi_wstrb({write_bus}wstrb),
        .m_axi_wlast({write_bus}wlast),
        .m_axi_wvalid({write_bus}wvalid),
        .m_axi_wready({write_bus}wready),
        .m_axi_bresp({write_bus}bresp),
        .m_axi_bvalid({write_bus}bvalid),
        .m_axi_bready({write_bus}bready)
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
    _validate_composition_shape(composition)  # model_copy skips model_post_init
    if sources is not None:
        _validate_against_sources(composition, sources)
    addr_width = composition.addr_width
    burst_beats = composition.burst_beats
    data_width = composition.data_width
    if data_width != 64:
        # the JOB-level sim harness backdoor-loads a 64-bit dau_axi_ram_sim;
        # wide read tiers are validated by the shell-top generation + silicon
        # (the reader/dispatcher RTL are cocotb-proven at all tiers in dau-core)
        raise ScanCompositionError(f"the composition sim harness is 64-bit; data_width={data_width} is a shell-top/silicon path only")
    num_lanes = len(composition.lanes)
    lanes = range(num_lanes)
    name = module_name if module_name is not None else f"{composition.module_name}_sim"
    merged_config_inputs = dict(config_inputs or {})
    if _uses_load_phase(composition):
        # the harness has no register aperture, so the LOAD_PHASE register
        # surfaces as a testbench-driven input port
        merged_config_inputs.setdefault("load_phase", 1)
    config_input_ports = "".join(f"    input wire [{width - 1}:0] {port},\n" for port, width in merged_config_inputs.items())
    lane_port_taps = "\n".join(
        f"""    wire [{addr_width - 1}:0] lane_output_address_{i} = lane_output_address[{addr_width * (i + 1) - 1}:{addr_width * i}];
    assign lane_result_length_bytes[{32 * (i + 1) - 1}:{32 * i}] = lane_result_length_{i};
    assign lane_count[{64 * (i + 1) - 1}:{64 * i}] = lane_bar_count_{i};"""
        for i in lanes
    )
    stream_prefix = "scan"
    if composition.front_unpack is not None:
        stream_prefix = "feed"
    elif composition.front_gearbox is not None:
        stream_prefix = "geared"
    fanout_wire_decls, fanout_instance = ("", "") if composition.wide_lane else _fanout_sv(composition, clk="clk", stream_prefix=stream_prefix)
    front_unpack_wire_decls = _front_unpack_wire_decls_sv(composition)
    front_unpack_instance = _front_unpack_instance_sv(composition, clk="clk")
    front_gearbox_wire_decls = _front_gearbox_wire_decls_sv(composition)
    front_gearbox_instance = _front_gearbox_instance_sv(composition, clk="clk")
    front_stage_error_branch = _front_unpack_error_branch_sv(composition, error="error", error_code="error_code") + _front_gearbox_error_branch_sv(
        composition, error="error", error_code="error_code"
    )
    front_stage_error_reset = _front_unpack_error_reset_sv(composition) + _front_gearbox_error_reset_sv(composition)
    front_stage_error_latch = _front_unpack_error_latch_sv(composition) + _front_gearbox_error_latch_sv(composition)
    length_align_bits = 3 if composition.front_unpack is not None else 4
    grid_bytes = 1 << length_align_bits
    length_ok_check = f"input_length_bytes[{length_align_bits - 1}:0] == {length_align_bits}'d0"
    lane_instances = _lane_units_sv(composition, clk="clk", writer_rst="rst", start="start")
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
    wire [{data_width - 1}:0] scan_data;
    wire scan_last;
{front_unpack_wire_decls}{front_gearbox_wire_decls}{fanout_wire_decls}
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

    // the {grid_bytes}-byte row grid is enforced before any unit starts: a rejected
    // length must not leave the writers waiting on a status
    reg length_fail;
    wire length_ok = (input_length_bytes != 32'd0) && ({length_ok_check});
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
{front_stage_error_branch}{error_priority}
            end else begin
                error = 1'b0;
                error_code = 8'd0;
            end
        end
    end

    dau_axi_burst_reader #(
        .ADDR_WIDTH({addr_width}),
        .BURST_BEATS({burst_beats}),
        .LENGTH_ALIGN_BITS({length_align_bits})
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

{front_unpack_instance}{front_gearbox_instance}{fanout_instance}

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
{front_stage_error_reset}{lane_count_clears}
        end else begin
            prev_done <= done;
            pipeline_error_reset <= done && !prev_done && error;
            if (start) begin
                length_fail <= !length_ok;
{lane_count_start_clears}
            end
{front_stage_error_latch}{lane_count_latches}
        end
    end
endmodule

`default_nettype wire
"""
