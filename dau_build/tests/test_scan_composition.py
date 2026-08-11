from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from dau_build.scan_composition import (
    LaneTile,
    RegisterLayout,
    ScanComposition,
    ScanCompositionError,
    TileInstance,
    generate_scan_composition_sim_sv,
    generate_scan_composition_top_sv,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "scan_composition"


def _bar_noc_composition() -> ScanComposition:
    """The broadcast-NoC shape: four lanes of key partition filter
    (key & 3 == lane) feeding a fused bar tile in last-driven mode."""
    return ScanComposition(
        name="bar-noc-4",
        module_name="dau_mm_bar_noc_job",
        lanes=tuple(
            LaneTile(
                module="dau_int32_bar_aggregation",
                config={"cfg_mode": "2'd0", "cfg_row_count": "64'd0"},
                count_port="bar_count",
                partition=TileInstance(
                    module="dau_int32_pair_key_filter",
                    config={"cfg_key_mask": "32'd3", "cfg_key_match": f"32'd{lane}"},
                ),
            )
            for lane in range(4)
        ),
    )


def _sorted_scan_composition() -> ScanComposition:
    """The range-partitioned shape: a shared range partitioner routing each
    row to one of four filterless sorter lanes."""
    return ScanComposition(
        name="sorted-scan",
        module_name="dau_mm_sorted_scan_job",
        lanes=tuple(LaneTile(module="dau_int32_bitonic_sorter", count_port="sorted_count") for _ in range(4)),
        partitioner=TileInstance(
            module="dau_int32_range_partitioner",
            config={"cfg_splitters": "{32'h0000001E, 32'h00000014, 32'h0000000A}"},
        ),
        sort_capacity=16,  # caller-computed capability data (the sorter's batch row capacity)
    )


def test_bar_noc_top_matches_golden() -> None:
    """Byte-identity golden: the walker's output is pinned so no refactor
    can change a byte of the generated top unnoticed."""
    assert generate_scan_composition_top_sv(_bar_noc_composition(), platform_id="DPV1") == (_FIXTURES / "bar_noc_4.v").read_text()


def test_sorted_scan_top_matches_golden() -> None:
    assert generate_scan_composition_top_sv(_sorted_scan_composition(), platform_id="DPV1") == (_FIXTURES / "sorted_scan.v").read_text()


def test_platform_id_threads_into_the_identity_parameter() -> None:
    """The platform's wire id encodes little-endian into the top's PLATFORM_ID
    parameter and overrides the identity block; the default is DPV1."""
    default = generate_scan_composition_top_sv(_bar_noc_composition(), platform_id="DPV1")
    assert "parameter [31:0] PLATFORM_ID = 32'h31565044," in default  # "DPV1"
    assert ".PLATFORM_ID(PLATFORM_ID)," in default

    dpv2 = generate_scan_composition_top_sv(_bar_noc_composition(), platform_id="DPV2")
    assert "parameter [31:0] PLATFORM_ID = 32'h32565044," in dpv2  # "DPV2"

    with pytest.raises(ValueError, match="1 to 4 ASCII bytes"):
        generate_scan_composition_top_sv(_bar_noc_composition(), platform_id="TOOLONG")


def test_capability_words_are_caller_data_and_default_to_unadvertised() -> None:
    """The identity block advertises exactly what the composer computed:
    the bitmaps default to zero (never a static advertisement), the lane
    count is always the composed lane count, and non-default words flow
    into the top parameters and the identity instance."""
    default = generate_scan_composition_top_sv(_bar_noc_composition(), platform_id="DPV1")
    assert "    parameter [31:0] OPERATOR_BITMAP = 32'h00000000,\n" in default
    assert "    parameter [31:0] LANE_COUNT = 32'd4,\n" in default
    assert "    parameter [31:0] HOST_OPCODE_BITMAP = 32'h00000000,\n" in default
    assert "    parameter [31:0] SORT_CAPACITY = 32'd0,\n" in default
    # unstamped by default: a composition only advertises an identity when
    # its composer computed one, exactly like the bitmaps
    assert "    parameter [63:0] BUILD_ID = 64'h0000000000000000\n" in default

    composed = generate_scan_composition_top_sv(
        _bar_noc_composition().model_copy(update={"operator_bitmap": 0x1E, "host_opcode_bitmap": 0x20, "sort_capacity": 16, "build_id": 0xDEADBEEF}),
        platform_id="DPV1",
    )
    assert "    parameter [31:0] OPERATOR_BITMAP = 32'h0000001E,\n" in composed
    assert "    parameter [31:0] HOST_OPCODE_BITMAP = 32'h00000020,\n" in composed
    assert "    parameter [31:0] SORT_CAPACITY = 32'd16,\n" in composed
    assert "    parameter [63:0] BUILD_ID = 64'h00000000DEADBEEF\n" in composed
    for parameter in ("OPERATOR_BITMAP", "LANE_COUNT", "HOST_OPCODE_BITMAP", "SORT_CAPACITY"):
        assert f"        .{parameter}({parameter}),\n" in composed or f"        .{parameter}({parameter})\n" in composed


def test_generated_by_names_the_banner_only() -> None:
    """``generated_by`` swaps the generator name in the banner and changes
    nothing else — callers re-hosting the walker keep byte-identity."""
    default = generate_scan_composition_top_sv(_bar_noc_composition(), platform_id="DPV1")
    renamed = generate_scan_composition_top_sv(_bar_noc_composition(), generated_by="other.walker", platform_id="DPV1")
    differing = [(a, b) for a, b in zip(default.splitlines(), renamed.splitlines()) if a != b]
    assert differing == [
        (
            "// GENERATED by dau_build.scan_composition.generate_scan_composition_top_sv — do not",
            "// GENERATED by other.walker — do not",
        )
    ]


def test_bar_noc_sim_matches_golden() -> None:
    """Byte-identity golden for the sim harness walked from the same
    composition data as the shell top."""
    assert generate_scan_composition_sim_sv(_bar_noc_composition()) == (_FIXTURES / "bar_noc_4_sim.v").read_text()


def test_sorted_scan_sim_matches_golden() -> None:
    """The partitioned shape's harness surfaces the splitters as a
    testbench-driven config input instead of a literal binding."""
    composition = _sorted_scan_composition().model_copy(
        update={"partitioner": TileInstance(module="dau_int32_range_partitioner", config={"cfg_splitters": "cfg_splitters"})}
    )
    text = generate_scan_composition_sim_sv(composition, config_inputs={"cfg_splitters": 96})
    assert text == (_FIXTURES / "sorted_scan_sim.v").read_text()
    assert "    input wire [95:0] cfg_splitters,\n" in text
    assert ".cfg_splitters(cfg_splitters)," in text


def test_sim_module_name_defaults_to_shell_name_with_sim_suffix() -> None:
    text = generate_scan_composition_sim_sv(_bar_noc_composition())
    assert "module dau_mm_bar_noc_job_sim (" in text
    renamed = generate_scan_composition_sim_sv(_bar_noc_composition(), module_name="dau_bar_bench")
    assert "module dau_bar_bench (" in renamed


def test_sim_harness_carries_the_job_control_surface() -> None:
    """The bar-noc-sim control surface: length-grid gate, unit_start,
    lane_rst error recovery, first-error-wins, backdoor RAM."""
    text = generate_scan_composition_sim_sv(_bar_noc_composition(), mem_words=4096, read_latency=2)
    assert "wire length_ok = (input_length_bytes != 32'd0) && (input_length_bytes[3:0] == 4'd0);" in text
    assert "wire unit_start = start && length_ok;" in text
    assert "wire lane_rst = rst || pipeline_error_reset;" in text
    assert "pipeline_error_reset <= done && !prev_done && error;" in text
    assert "error_code = 8'hFE;" in text
    assert ".MEM_WORDS(4096)" in text
    assert ".READ_LATENCY(2)" in text
    # no register aperture in the harness: the job surface is direct ports
    assert "s_axi_aclk" not in text
    assert "ADDR_JOB_CONTROL" not in text
    assert "dau_identity_registers" not in text


def test_register_layout_defaults_place_the_lane_block() -> None:
    layout = RegisterLayout()
    assert layout.lane_register(0, layout.lane_output_address_low) == 0x100
    assert layout.lane_register(3, layout.lane_error) == 0x170


def test_register_layout_drives_the_emitted_localparams() -> None:
    composition = _bar_noc_composition().model_copy(update={"registers": RegisterLayout(lane_base=0x200, lane_stride=0x40)})
    text = generate_scan_composition_top_sv(composition, platform_id="DPV1")
    assert "localparam [11:0] ADDR_LANE0_OUTPUT_ADDRESS = 12'h200;" in text
    assert "localparam [11:0] ADDR_LANE1_OUTPUT_ADDRESS = 12'h240;" in text


def test_composition_needs_a_lane() -> None:
    # pydantic surfaces model_post_init errors as ValidationError
    with pytest.raises(ValidationError, match="at least one lane"):
        ScanComposition(name="empty", module_name="dau_empty", lanes=())


def test_shared_partitioner_excludes_lane_partitions() -> None:
    with pytest.raises(ValidationError, match="shared partitioner"):
        ScanComposition(
            name="conflict",
            module_name="dau_conflict",
            lanes=(
                LaneTile(
                    module="dau_tile",
                    count_port="row_count",
                    partition=TileInstance(module="dau_filter"),
                ),
            ),
            partitioner=TileInstance(module="dau_partitioner"),
        )


def _chained_composition() -> ScanComposition:
    """A lane with a two-stage mid-lane chain (filter -> map) ahead of the
    terminal tile, plus a chainless lane to pin cross-lane independence."""
    return ScanComposition(
        name="chained",
        module_name="dau_chained_job",
        lanes=(
            LaneTile(
                module="dau_terminal_tile",
                config={"cfg_mode": "2'd0"},
                count_port="row_count",
                partition=TileInstance(module="dau_key_filter", config={"cfg_key_match": "32'd0"}),
                chain=(
                    TileInstance(module="dau_stage_filter", config={"cfg_op": "3'd1"}),
                    TileInstance(module="dau_stage_map", config={"cfg_shift": "5'd15"}),
                ),
            ),
            LaneTile(module="dau_terminal_tile", count_port="row_count"),
        ),
    )


def test_chain_stages_wire_front_to_terminal_in_order() -> None:
    text = generate_scan_composition_top_sv(_chained_composition(), platform_id="DPV1")
    # stage 0 consumes the lane front, stage 1 consumes stage 0, the
    # terminal tile consumes stage 1
    assert "    dau_stage_filter chain_0_0 (" in text
    assert ".input_valid(filt_out_valid_0)," in text
    assert "    dau_stage_map chain_0_1 (" in text
    assert ".input_valid(chain0_out_valid_0)," in text
    assert ".output_valid(chain1_out_valid_0)," in text
    tile_block = text.split("dau_terminal_tile tile_0 (")[1].split(");")[0]
    assert ".input_valid(chain1_out_valid_0)," in tile_block
    # config bindings land on their stages
    assert ".cfg_op(3'd1)," in text
    assert ".cfg_shift(5'd15)," in text
    # the chainless lane still consumes its front directly
    tile1_block = text.split("dau_terminal_tile tile_1 (")[1].split(");")[0]
    assert ".input_valid(filt_out_valid_1)," in tile1_block
    assert "chain0_out_valid_1" not in text


def test_chain_status_mux_is_upstream_first() -> None:
    text = generate_scan_composition_top_sv(_chained_composition(), platform_id="DPV1")
    assert "assign unit_status_valid_0 = tile_status_valid_0 || filt_status_valid_0 || chain0_status_valid_0 || chain1_status_valid_0;" in text
    assert (
        "assign unit_status_error_0 = filt_status_valid_0 ? filt_status_error_0 : "
        "chain0_status_valid_0 ? chain0_status_error_0 : "
        "chain1_status_valid_0 ? chain1_status_error_0 : tile_status_error_0;"
    ) in text
    assert "assign filt_status_ready_0 = unit_status_ready_0 && filt_status_valid_0;" in text
    assert "assign chain0_status_ready_0 = unit_status_ready_0 && !filt_status_valid_0 && chain0_status_valid_0;" in text
    assert "assign chain1_status_ready_0 = unit_status_ready_0 && !filt_status_valid_0 && !chain0_status_valid_0 && chain1_status_valid_0;" in text
    assert "assign tile_status_ready_0 = unit_status_ready_0 && !filt_status_valid_0 && !chain0_status_valid_0 && !chain1_status_valid_0;" in text
    # the chainless lane keeps the plain forwarding glue
    assert "assign unit_status_valid_1 = tile_status_valid_1;" in text


def test_filterless_chained_lane_muxes_chain_before_tile() -> None:
    composition = ScanComposition(
        name="chained-filterless",
        module_name="dau_chained_filterless_job",
        lanes=(
            LaneTile(
                module="dau_terminal_tile",
                count_port="row_count",
                chain=(TileInstance(module="dau_stage_filter"),),
            ),
        ),
    )
    text = generate_scan_composition_top_sv(composition, platform_id="DPV1")
    assert "assign unit_status_valid_0 = tile_status_valid_0 || chain0_status_valid_0;" in text
    assert "assign chain0_status_ready_0 = unit_status_ready_0 && chain0_status_valid_0;" in text
    assert "assign tile_status_ready_0 = unit_status_ready_0 && !chain0_status_valid_0;" in text


def test_sim_harness_carries_the_same_chain() -> None:
    text = generate_scan_composition_sim_sv(_chained_composition())
    assert "    dau_stage_filter chain_0_0 (" in text
    assert "    dau_stage_map chain_0_1 (" in text
    tile_block = text.split("dau_terminal_tile tile_0 (")[1].split(");")[0]
    assert ".input_valid(chain1_out_valid_0)," in tile_block


def test_param_override_emits_on_the_tile_and_param_less_stays_byte_identical() -> None:
    """A tile carrying params emits a ``#(.KEY_SPACE(N))`` override between
    the module name and instance; a param-less composition emits exactly as
    before (the byte-identity goldens above pin that)."""
    # the exact "module tile_0 (" substring proves there is no #(...) wedged
    # between the module name and the instance name for a param-less tile
    param_free = generate_scan_composition_top_sv(_bar_noc_composition(), platform_id="DPV1")
    assert "    dau_int32_bar_aggregation tile_0 (" in param_free

    membership = ScanComposition(
        name="membership",
        module_name="dau_mm_membership_job",
        lanes=(
            LaneTile(
                module="dau_int32_field_sum_aggregation",
                config={"cfg_field_mask": "4'b0010"},
                count_port="aggregated_count",
                chain=(TileInstance(module="dau_int32_key_membership_filter", params={"KEY_SPACE": 6_000_000}),),
            ),
        ),
    )
    text = generate_scan_composition_top_sv(membership, platform_id="DPV1")
    assert "    dau_int32_key_membership_filter #(\n        .KEY_SPACE(6000000)\n    ) chain_0_0 (" in text
    # the param-less terminal tile carries no override
    assert "    dau_int32_field_sum_aggregation tile_0 (" in text


def test_param_override_emits_on_partition_and_terminal_tile_slots() -> None:
    composition = ScanComposition(
        name="param-slots",
        module_name="dau_mm_param_slots_job",
        lanes=(
            LaneTile(
                module="dau_terminal_tile",
                count_port="row_count",
                params={"KEY_SPACE": 2048},
                partition=TileInstance(module="dau_key_filter", params={"KEY_SPACE": 128}),
            ),
        ),
    )
    text = generate_scan_composition_top_sv(composition, platform_id="DPV1")
    assert "    dau_key_filter #(\n        .KEY_SPACE(128)\n    ) partition_0 (" in text
    assert "    dau_terminal_tile #(\n        .KEY_SPACE(2048)\n    ) tile_0 (" in text


def test_param_override_emits_on_the_shared_partitioner() -> None:
    # a param-less shared partitioner emits only the derived NUM_PARTITIONS (byte-identical)
    plain = generate_scan_composition_top_sv(_sorted_scan_composition(), platform_id="DPV1")
    assert "    dau_int32_range_partitioner #(\n        .NUM_PARTITIONS(4)\n    ) partitioner (" in plain
    # custom params append after NUM_PARTITIONS
    with_params = _sorted_scan_composition().model_copy(
        update={"partitioner": TileInstance(module="dau_int32_range_partitioner", config={"cfg_splitters": "s"}, params={"KEY_SPACE": 4096})}
    )
    text = generate_scan_composition_top_sv(with_params, platform_id="DPV1")
    assert "    dau_int32_range_partitioner #(\n        .NUM_PARTITIONS(4),\n        .KEY_SPACE(4096)\n    ) partitioner (" in text


def test_shared_partitioner_rejects_a_num_partitions_param_override() -> None:
    composition = _sorted_scan_composition().model_copy(
        update={"partitioner": TileInstance(module="dau_int32_range_partitioner", config={"cfg_splitters": "s"}, params={"NUM_PARTITIONS": 8})}
    )
    with pytest.raises(ScanCompositionError, match="NUM_PARTITIONS is derived"):
        generate_scan_composition_top_sv(composition, platform_id="DPV1")


def test_param_less_goldens_are_untouched_by_the_param_channel() -> None:
    """The param channel adds a field that defaults empty: every existing
    golden must still match byte-for-byte (this repeats the golden asserts to
    pin them against the param-channel change explicitly)."""
    assert generate_scan_composition_top_sv(_bar_noc_composition(), platform_id="DPV1") == (_FIXTURES / "bar_noc_4.v").read_text()
    assert generate_scan_composition_top_sv(_sorted_scan_composition(), platform_id="DPV1") == (_FIXTURES / "sorted_scan.v").read_text()
    assert generate_scan_composition_sim_sv(_bar_noc_composition()) == (_FIXTURES / "bar_noc_4_sim.v").read_text()


_FRONT_UNPACK_CONFIG = {
    "cfg_field0_offset": "6'd0",
    "cfg_field0_width": "6'd32",
    "cfg_field0_signed": "1'b0",
    "cfg_field1_offset": "6'd32",
    "cfg_field1_width": "6'd27",
    "cfg_field1_signed": "1'b0",
    "cfg_field2_offset": "6'd0",
    "cfg_field2_width": "6'd1",
    "cfg_field2_signed": "1'b0",
    "cfg_field3_offset": "6'd0",
    "cfg_field3_width": "6'd1",
    "cfg_field3_signed": "1'b0",
    "cfg_bypass": "1'b0",
}


def _front_unpacked_composition() -> ScanComposition:
    """The bar-noc shape with a packed-row front unpacker between the burst
    reader and the broadcast fan-out."""
    return _bar_noc_composition().model_copy(update={"front_unpack": TileInstance(module="dau_int32_row_unpack", config=dict(_FRONT_UNPACK_CONFIG))})


def test_front_unpack_wires_reader_through_unpacker_to_the_fanout() -> None:
    text = generate_scan_composition_top_sv(_front_unpacked_composition(), platform_id="DPV1")
    # the reader is unchanged: it still drives the scan_* stream
    assert ".stream_valid(scan_valid)," in text
    # the unpacker consumes the reader's scan_* stream ...
    unpack_block = text.split("dau_int32_row_unpack front_unpack (")[1].split(");")[0]
    assert ".input_valid(scan_valid)," in unpack_block
    assert ".input_ready(scan_ready)," in unpack_block
    assert ".input_data(scan_data)," in unpack_block
    assert ".input_last(scan_last)," in unpack_block
    # ... and emits the widened feed_* stream
    assert ".output_valid(feed_valid)," in unpack_block
    assert ".output_ready(feed_ready)," in unpack_block
    assert ".output_data(feed_data)," in unpack_block
    assert ".output_last(feed_last)," in unpack_block
    # its config carries the four field descriptors + cfg_bypass
    assert ".cfg_field1_offset(6'd32)," in unpack_block
    assert ".cfg_bypass(1'b0)," in unpack_block
    # the broadcast fan-out now consumes the unpacker's output, not the reader's
    bcast_block = text.split("dau_stream_broadcast #(")[1].split(");")[0]
    assert ".input_valid(feed_valid)," in bcast_block
    assert ".input_ready(feed_ready)," in bcast_block
    assert ".input_data(feed_data)," in bcast_block
    assert ".input_last(feed_last)," in bcast_block
    # the unpacker sits between the reader and the fan-out in the emission
    assert text.index("dau_axi_burst_reader") < text.index("dau_int32_row_unpack front_unpack (") < text.index("dau_stream_broadcast #(")
    # the feed_* stream wires are declared
    assert "    wire feed_valid;\n    wire feed_ready;\n    wire [63:0] feed_data;\n    wire feed_last;\n" in text


def _wide_front_composition() -> ScanComposition:
    """The rows/cycle-axis shape: a 128-bit front unpacker (OUT_WIDTH=128, one
    whole quad row per beat) feeding a key-mask dispatcher (IN_WIDTH=128) that
    routes each row to one of four filterless lanes."""
    return ScanComposition(
        name="bar-noc-wide",
        module_name="dau_mm_bar_noc_wide_job",
        lanes=tuple(
            LaneTile(module="dau_int32_bar_aggregation", config={"cfg_mode": "2'd0", "cfg_row_count": "64'd0"}, count_port="bar_count")
            for _ in range(4)
        ),
        partitioner=TileInstance(module="dau_int32_key_mask_dispatcher", params={"IN_WIDTH": 128}),
        front_unpack=TileInstance(module="dau_int32_row_unpack", config=dict(_FRONT_UNPACK_CONFIG), params={"OUT_WIDTH": 128}),
    )


def test_wide_front_widens_the_feed_and_threads_the_width_params() -> None:
    # the feed stream is declared at the front unpacker's OUT_WIDTH and both
    # width params land on their instances via the module-parameter channel
    text = generate_scan_composition_top_sv(_wide_front_composition(), platform_id="DPV1")
    assert "    wire feed_valid;\n    wire feed_ready;\n    wire [127:0] feed_data;\n    wire feed_last;\n" in text
    assert "dau_int32_row_unpack #(\n        .OUT_WIDTH(128)\n    )" in text
    dispatcher_block = text.split("dau_int32_key_mask_dispatcher #(")[1].split(");")[0]
    assert ".NUM_PARTITIONS(4)" in dispatcher_block
    assert ".IN_WIDTH(128)" in dispatcher_block
    assert ".input_data(feed_data)," in dispatcher_block
    # per-lane taps stay the 64-bit two-beat framing (no operator widens)
    assert "assign filt_out_data_0 = part_out_data[63:0];" in text
    # the sim harness mirrors the same width
    sim = generate_scan_composition_sim_sv(_wide_front_composition())
    assert "wire [127:0] feed_data;" in sim


def test_wide_front_requires_a_matching_wide_fanout() -> None:
    # broadcast is 64-bit only: a wide front with no shared partitioner is
    # rejected, as is any feed/fan-out width mismatch (either direction).
    # Constructed directly — model_copy skips model_post_init validation.
    wide_unpack = TileInstance(module="dau_int32_row_unpack", config=dict(_FRONT_UNPACK_CONFIG), params={"OUT_WIDTH": 128})
    plain_lanes = tuple(
        LaneTile(module="dau_int32_bar_aggregation", config={"cfg_mode": "2'd0", "cfg_row_count": "64'd0"}, count_port="bar_count") for _ in range(4)
    )
    with pytest.raises(ValidationError, match="needs a shared partitioner"):
        ScanComposition(name="bad", module_name="dau_bad", lanes=plain_lanes, front_unpack=wide_unpack)
    with pytest.raises(ValidationError, match="widths must agree"):
        ScanComposition(
            name="bad",
            module_name="dau_bad",
            lanes=plain_lanes,
            partitioner=TileInstance(module="dau_int32_key_mask_dispatcher"),
            front_unpack=wide_unpack,
        )
    with pytest.raises(ValidationError, match="feed stream is 64-bit"):
        ScanComposition(
            name="bad",
            module_name="dau_bad",
            lanes=plain_lanes,
            partitioner=TileInstance(module="dau_int32_key_mask_dispatcher", params={"IN_WIDTH": 128}),
        )
    with pytest.raises(ValidationError, match="OUT_WIDTH must be 64/128/256/512/1024"):
        ScanComposition(
            name="bad",
            module_name="dau_bad",
            lanes=plain_lanes,
            partitioner=TileInstance(module="dau_int32_key_mask_dispatcher", params={"IN_WIDTH": 128}),
            front_unpack=TileInstance(module="dau_int32_row_unpack", config=dict(_FRONT_UNPACK_CONFIG), params={"OUT_WIDTH": 96}),
        )


def _load_phase_composition() -> ScanComposition:
    """The q3 shape: one lane, a phase-bound membership filter at chain[0]
    ahead of the terminal tile — the LOAD_PHASE register drives cfg_load."""
    return ScanComposition(
        name="membership-scan",
        module_name="dau_mm_membership_job",
        lanes=(
            LaneTile(
                module="dau_int32_bar_aggregation",
                config={"cfg_mode": "2'd0", "cfg_row_count": "64'd0"},
                count_port="bar_count",
                chain=(
                    TileInstance(
                        module="dau_int32_key_membership_filter",
                        config={"cfg_load": "load_phase", "cfg_mode": "1'b0", "cfg_kind": "2'd1"},
                    ),
                ),
            ),
        ),
    )


def test_load_phase_binding_emits_the_register_and_drives_cfg_load() -> None:
    text = generate_scan_composition_top_sv(_load_phase_composition(), platform_id="DPV1")
    # the register: declared, decoded at 0x084, reset to probe, readable
    assert "    reg load_phase;\n" in text
    assert "    localparam [11:0] ADDR_LOAD_PHASE = 12'h084;" in text
    assert "                    ADDR_LOAD_PHASE: load_phase <= s_axi_wdata[0];" in text
    assert "                    ADDR_LOAD_PHASE: s_axi_rdata <= {31'd0, load_phase};" in text
    assert "            load_phase <= 1'b0;" in text
    # the binding: the membership tile's cfg_load is driven by the register
    membership_block = text.split("dau_int32_key_membership_filter")[1].split(");")[0]
    assert ".cfg_load(load_phase)," in membership_block


def test_load_phase_sim_harness_surfaces_the_register_as_a_port() -> None:
    # the harness has no register aperture: LOAD_PHASE surfaces as a
    # testbench-driven 1-bit input port bound to the same cfg_load
    sim = generate_scan_composition_sim_sv(_load_phase_composition())
    assert "    input wire [0:0] load_phase,\n" in sim
    assert ".cfg_load(load_phase)," in sim


def test_load_phase_free_composition_stays_byte_identical() -> None:
    # no load binding -> no register, no decl, no decode (the pinned goldens
    # already prove byte-identity; this pins the register's absence)
    text = generate_scan_composition_top_sv(_bar_noc_composition(), platform_id="DPV1")
    assert "load_phase" not in text
    assert "ADDR_LOAD_PHASE" not in text


def test_load_phase_shape_rules_mirror_the_walker() -> None:
    membership = TileInstance(module="dau_int32_key_membership_filter", config={"cfg_load": "load_phase", "cfg_mode": "1'b0", "cfg_kind": "2'd1"})
    plain = LaneTile(module="dau_int32_bar_aggregation", config={"cfg_mode": "2'd0", "cfg_row_count": "64'd0"}, count_port="bar_count")
    loaded = plain.model_copy(update={"chain": (membership,)})
    # the reserved symbol anywhere but chain[0].cfg_load is rejected
    with pytest.raises(ValidationError, match="only a lane's first chain stage"):
        ScanComposition(
            name="bad",
            module_name="dau_bad",
            lanes=(plain.model_copy(update={"partition": TileInstance(module="dau_int32_pair_key_filter", config={"cfg_key_mask": "load_phase"})}),),
        )
    # non-uniform lanes rejected
    with pytest.raises(ValidationError, match="EVERY lane"):
        ScanComposition(name="bad", module_name="dau_bad", lanes=(loaded, plain))
    # a partition filter would filter the image
    with pytest.raises(ValidationError, match="partition filters"):
        ScanComposition(
            name="bad",
            module_name="dau_bad",
            lanes=(
                loaded.model_copy(
                    update={"partition": TileInstance(module="dau_int32_pair_key_filter", config={"cfg_key_mask": "32'd3", "cfg_key_match": "32'd0"})}
                ),
            ),
        )
    # generators re-check a model-copied composition
    broken = _load_phase_composition().model_copy(
        update={"front_unpack": TileInstance(module="dau_int32_row_unpack", config=dict(_FRONT_UNPACK_CONFIG))}
    )
    with pytest.raises(ScanCompositionError, match="front unpacker"):
        generate_scan_composition_top_sv(broken, platform_id="DPV1")


def test_generators_revalidate_a_model_copied_composition() -> None:
    # model_copy(update=...) skips model_post_init, so BOTH generators must
    # re-check shape/width coherence — an incoherent copy must never emit
    # width-broken Verilog
    wide_unpack = TileInstance(module="dau_int32_row_unpack", config=dict(_FRONT_UNPACK_CONFIG), params={"OUT_WIDTH": 128})
    broadcast_mismatch = _bar_noc_composition().model_copy(update={"front_unpack": wide_unpack})
    with pytest.raises(ScanCompositionError, match="needs a shared partitioner"):
        generate_scan_composition_top_sv(broadcast_mismatch, platform_id="DPV1")
    with pytest.raises(ScanCompositionError, match="needs a shared partitioner"):
        generate_scan_composition_sim_sv(broadcast_mismatch)
    narrow_fanout = _wide_front_composition().model_copy(update={"partitioner": TileInstance(module="dau_int32_key_mask_dispatcher")})
    with pytest.raises(ScanCompositionError, match="widths must agree"):
        generate_scan_composition_top_sv(narrow_fanout, platform_id="DPV1")
    with pytest.raises(ScanCompositionError, match="widths must agree"):
        generate_scan_composition_sim_sv(narrow_fanout)
    bad_width = _wide_front_composition().model_copy(
        update={"partitioner": TileInstance(module="dau_int32_key_mask_dispatcher", params={"IN_WIDTH": 96})}
    )
    with pytest.raises(ScanCompositionError, match="IN_WIDTH must be one of"):
        generate_scan_composition_top_sv(bad_width, platform_id="DPV1")


def test_front_unpack_status_is_latched_and_folded_into_job_error() -> None:
    text = generate_scan_composition_top_sv(_front_unpacked_composition(), platform_id="DPV1")
    # the front-stage status close-out is consumed (ready tied high) ...
    assert "assign front_unpack_status_ready = 1'b1;" in text
    # ... latched sticky (the close-out is a one-cycle pulse) ...
    assert "    reg front_unpack_error;\n    reg [7:0] front_unpack_error_code;\n" in text
    assert (
        "            if (front_unpack_status_valid && front_unpack_status_error) begin\n"
        "                front_unpack_error <= 1'b1;\n"
        "                front_unpack_error_code <= front_unpack_status_error_code;\n"
        "            end\n"
    ) in text
    # cleared ONLY at peripheral reset, never on job start: a torn/bad-config feed
    # hangs the lane writers (no last arrives) and they recover only on reset, so the
    # latched error must persist until reset (regression: no job-start clear)
    assert "                front_unpack_error <= 1'b0;\n" not in text  # no 16-space job-start clear
    assert "            front_unpack_error <= 1'b0;\n" in text  # the 12-space reset clear remains
    # ... and surfaced into the job error mux after the reader, before the
    # lane writers (first-error-wins), reading the sticky reg
    assert (
        "            if (reader_error) begin\n"
        "                job_error = 1'b1;\n"
        "                job_error_code = reader_error_code;\n"
        "            end else if (front_unpack_error) begin\n"
        "                job_error = 1'b1;\n"
        "                job_error_code = front_unpack_error_code;\n"
        "            end else if (writer_error_0) begin\n"
    ) in text


def test_front_unpack_relaxes_the_length_grid_to_eight_bytes() -> None:
    # packed rows are one 8-byte word each, so a front-unpack composition
    # accepts an 8-byte length grid (an odd packed-row count would round to a
    # non-16-multiple length and be rejected on the 16-byte quad grid)
    packed = generate_scan_composition_top_sv(_front_unpacked_composition(), platform_id="DPV1")
    assert "wire length_ok = (input_length_bytes != 32'd0) && (input_length_bytes[2:0] == 3'd0);" in packed
    assert ".LENGTH_ALIGN_BITS(3)" in packed
    assert "// the 8-byte row grid is enforced" in packed
    # the sim harness matches
    packed_sim = generate_scan_composition_sim_sv(_front_unpacked_composition())
    assert "wire length_ok = (input_length_bytes != 32'd0) && (input_length_bytes[2:0] == 3'd0);" in packed_sim
    assert ".LENGTH_ALIGN_BITS(3)" in packed_sim
    # a front-unpack-less composition keeps the 16-byte quad grid
    plain = generate_scan_composition_top_sv(_bar_noc_composition(), platform_id="DPV1")
    assert "wire length_ok = (input_length_bytes != 32'd0) && (input_length_bytes[3:0] == 4'd0);" in plain
    assert ".LENGTH_ALIGN_BITS(4)" in plain
    assert "// the 16-byte row grid is enforced" in plain


def test_front_unpack_with_a_shared_partitioner_feeds_the_partitioner() -> None:
    composition = _sorted_scan_composition().model_copy(
        update={"front_unpack": TileInstance(module="dau_int32_row_unpack", config=dict(_FRONT_UNPACK_CONFIG))}
    )
    text = generate_scan_composition_top_sv(composition, platform_id="DPV1")
    part_block = text.split(") partitioner (")[1].split(");")[0]
    assert ".input_valid(feed_valid)," in part_block
    assert ".input_last(feed_last)," in part_block
    assert text.index("dau_int32_row_unpack front_unpack (") < text.index(") partitioner (")


def test_front_unpack_sim_harness_carries_the_unpacker() -> None:
    text = generate_scan_composition_sim_sv(_front_unpacked_composition())
    unpack_block = text.split("dau_int32_row_unpack front_unpack (")[1].split(");")[0]
    assert ".input_valid(scan_valid)," in unpack_block
    assert ".output_valid(feed_valid)," in unpack_block
    bcast_block = text.split("dau_stream_broadcast #(")[1].split(");")[0]
    assert ".input_valid(feed_valid)," in bcast_block
    assert "assign front_unpack_status_ready = 1'b1;" in text


def test_front_unpack_absent_stays_byte_identical() -> None:
    """The front-unpack slot defaults to ``None``; every existing golden must
    still match byte-for-byte (the additive change never touches the
    front-unpack-less emission)."""
    assert generate_scan_composition_top_sv(_bar_noc_composition(), platform_id="DPV1") == (_FIXTURES / "bar_noc_4.v").read_text()
    assert generate_scan_composition_top_sv(_sorted_scan_composition(), platform_id="DPV1") == (_FIXTURES / "sorted_scan.v").read_text()
    assert generate_scan_composition_sim_sv(_bar_noc_composition()) == (_FIXTURES / "bar_noc_4_sim.v").read_text()


def _front_gearbox_composition() -> ScanComposition:
    """A wide reader geared into whole three-word records before dispatch."""
    return ScanComposition(
        name="geared",
        module_name="dau_mm_geared_job",
        lanes=tuple(LaneTile(module="dau_int32_temporal_hero", count_port="record_count") for _ in range(8)),
        partitioner=TileInstance(module="dau_int32_key_mask_dispatcher"),
        front_gearbox=TileInstance(module="dau_record_gearbox"),
        data_width=512,
        input_row_bytes=24,
    )


def test_front_gearbox_wires_wide_reader_to_whole_record_dispatcher() -> None:
    text = generate_scan_composition_top_sv(_front_gearbox_composition(), platform_id="DPV1")

    assert "    wire geared_valid;\n    wire geared_ready;\n    wire [191:0] geared_data;\n    wire geared_last;\n" in text
    gearbox_block = text.split("dau_record_gearbox #(")[1].split(");")[0]
    assert ".IN_WIDTH(512)" in gearbox_block
    assert ".RECORD_WORDS(3)" in gearbox_block
    assert ".cfg_input_records(input_length_bytes / 32'd24)," in gearbox_block
    assert ".input_valid(scan_valid)," in gearbox_block
    assert ".input_ready(scan_ready)," in gearbox_block
    assert ".input_data(scan_data)," in gearbox_block
    assert ".input_last(scan_last)," in gearbox_block
    assert ".output_valid(geared_valid)," in gearbox_block
    assert ".output_ready(geared_ready)," in gearbox_block
    assert ".output_data(geared_data)," in gearbox_block
    assert ".output_last(geared_last)," in gearbox_block

    dispatcher_block = text.split(") partitioner (")[1].split(");")[0]
    assert ".input_valid(geared_valid)," in dispatcher_block
    assert ".input_ready(geared_ready)," in dispatcher_block
    assert ".input_data(geared_data)," in dispatcher_block
    assert ".input_last(geared_last)," in dispatcher_block
    assert text.index("dau_axi_burst_reader") < text.index("dau_record_gearbox #(") < text.index(") partitioner (")


def test_front_gearbox_status_is_latched_and_folded_into_job_error() -> None:
    text = generate_scan_composition_top_sv(_front_gearbox_composition(), platform_id="DPV1")

    assert "assign front_gearbox_status_ready = 1'b1;" in text
    assert "    reg front_gearbox_error;\n    reg [7:0] front_gearbox_error_code;\n" in text
    assert (
        "            if (front_gearbox_status_valid && front_gearbox_status_error) begin\n"
        "                front_gearbox_error <= 1'b1;\n"
        "                front_gearbox_error_code <= front_gearbox_status_error_code;\n"
        "            end\n"
    ) in text
    assert "                front_gearbox_error <= 1'b0;\n" not in text
    assert "            front_gearbox_error <= 1'b0;\n" in text
    assert (
        "            if (reader_error) begin\n"
        "                job_error = 1'b1;\n"
        "                job_error_code = reader_error_code;\n"
        "            end else if (front_gearbox_error) begin\n"
        "                job_error = 1'b1;\n"
        "                job_error_code = front_gearbox_error_code;\n"
        "            end else if (writer_error_0) begin\n"
    ) in text


def test_front_gearbox_shape_refusals() -> None:
    lanes = tuple(LaneTile(module="dau_int32_temporal_hero", count_port="record_count") for _ in range(8))
    gearbox = TileInstance(module="dau_record_gearbox")
    dispatcher = TileInstance(module="dau_int32_key_mask_dispatcher")

    with pytest.raises(ValidationError, match="front_gearbox requires data_width > 64"):
        ScanComposition(
            name="bad",
            module_name="dau_bad",
            lanes=lanes,
            partitioner=dispatcher,
            front_gearbox=gearbox,
            input_row_bytes=24,
        )
    with pytest.raises(ValidationError, match="front_gearbox requires a shared partitioner"):
        ScanComposition(
            name="bad",
            module_name="dau_bad",
            lanes=lanes,
            front_gearbox=gearbox,
            data_width=512,
            input_row_bytes=24,
        )
    with pytest.raises(ValidationError, match="mutually exclusive"):
        ScanComposition(
            name="bad",
            module_name="dau_bad",
            lanes=lanes,
            partitioner=dispatcher,
            front_unpack=TileInstance(module="dau_int32_row_unpack"),
            front_gearbox=gearbox,
            data_width=512,
            input_row_bytes=24,
        )


_CONFORMING_TILE = """
module lane_tile (
    input  wire logic        clk,
    input  wire logic        rst,
    input  wire logic [31:0] cfg_thing,
    input  wire logic        input_valid,
    output logic             input_ready,
    input  wire logic [63:0] input_data,
    input  wire logic        input_last,
    output logic             output_valid,
    input  wire logic        output_ready,
    output logic [63:0]      output_data,
    output logic             output_last,
    output logic             status_valid,
    input  wire logic        status_ready,
    output logic             status_error,
    output logic [7:0]       status_error_code,
    output logic [63:0]      row_count
);
endmodule
"""


def _lane_tile_composition(config: dict[str, str]) -> ScanComposition:
    return ScanComposition(
        name="introspected",
        module_name="dau_introspected_job",
        lanes=(LaneTile(module="lane_tile", config=config, count_port="row_count"),),
    )


def test_sources_arm_interface_validation(tmp_path: Path) -> None:
    source = tmp_path / "lane_tile.sv"
    source.write_text(_CONFORMING_TILE)
    text = generate_scan_composition_top_sv(_lane_tile_composition({"cfg_thing": "32'd7"}), sources=(source,), platform_id="DPV1")
    assert "lane_tile tile_0 (" in text
    assert ".cfg_thing(32'd7)," in text


def test_sources_reject_a_config_binding_typo(tmp_path: Path) -> None:
    source = tmp_path / "lane_tile.sv"
    source.write_text(_CONFORMING_TILE)
    with pytest.raises(ScanCompositionError, match=r"config binding 'cfg_thingz' is not an input port \(cfg ports: cfg_thing\)"):
        generate_scan_composition_top_sv(_lane_tile_composition({"cfg_thingz": "32'd7"}), sources=(source,), platform_id="DPV1")


def test_sources_reject_a_missing_module(tmp_path: Path) -> None:
    source = tmp_path / "lane_tile.sv"
    source.write_text(_CONFORMING_TILE)
    composition = _lane_tile_composition({}).model_copy(update={"partitioner": TileInstance(module="dau_absent_partitioner")})
    with pytest.raises(ScanCompositionError, match="dau_absent_partitioner.*not found"):
        generate_scan_composition_top_sv(composition, sources=(source,), platform_id="DPV1")


def test_sim_generator_arms_the_same_interface_validation(tmp_path: Path) -> None:
    source = tmp_path / "lane_tile.sv"
    source.write_text(_CONFORMING_TILE)
    text = generate_scan_composition_sim_sv(_lane_tile_composition({"cfg_thing": "32'd7"}), sources=(source,))
    assert "lane_tile tile_0 (" in text
    with pytest.raises(ScanCompositionError, match=r"config binding 'cfg_thingz' is not an input port"):
        generate_scan_composition_sim_sv(_lane_tile_composition({"cfg_thingz": "32'd7"}), sources=(source,))


def test_sources_validate_chain_stages(tmp_path: Path) -> None:
    """Chain stages arm the same slang interface validation as terminal
    tiles (contract conformance, config-binding names) but need no count
    port."""
    source = tmp_path / "lane_tile.sv"
    source.write_text(_CONFORMING_TILE)
    chained = _lane_tile_composition({}).model_copy(
        update={
            "lanes": (LaneTile(module="lane_tile", count_port="row_count", chain=(TileInstance(module="lane_tile", config={"cfg_thing": "32'd1"}),)),)
        }
    )
    text = generate_scan_composition_top_sv(chained, sources=(source,), platform_id="DPV1")
    assert "lane_tile chain_0_0 (" in text

    with pytest.raises(ScanCompositionError, match=r"config binding 'cfg_typo' is not an input port"):
        generate_scan_composition_top_sv(
            chained.model_copy(
                update={
                    "lanes": (
                        LaneTile(module="lane_tile", count_port="row_count", chain=(TileInstance(module="lane_tile", config={"cfg_typo": "32'd1"}),)),
                    )
                }
            ),
            sources=(source,),
            platform_id="DPV1",
        )

    with pytest.raises(ScanCompositionError, match="dau_absent_stage.*not found"):
        generate_scan_composition_top_sv(
            chained.model_copy(
                update={"lanes": (LaneTile(module="lane_tile", count_port="row_count", chain=(TileInstance(module="dau_absent_stage"),)),)}
            ),
            sources=(source,),
            platform_id="DPV1",
        )


def test_sources_reject_a_missing_count_port(tmp_path: Path) -> None:
    source = tmp_path / "lane_tile.sv"
    source.write_text(_CONFORMING_TILE)
    composition = _lane_tile_composition({}).model_copy(update={"lanes": (LaneTile(module="lane_tile", count_port="bar_count"),)})
    with pytest.raises(ScanCompositionError, match="missing declared count port 'bar_count'"):
        generate_scan_composition_top_sv(composition, sources=(source,), platform_id="DPV1")


def test_sources_validate_the_front_unpacker(tmp_path: Path) -> None:
    """The front unpacker arms the same slang interface validation as the
    other stream tiles (contract conformance, config-binding names) but,
    like a partition filter, needs no count port."""
    source = tmp_path / "lane_tile.sv"
    source.write_text(_CONFORMING_TILE)
    composition = _lane_tile_composition({}).model_copy(update={"front_unpack": TileInstance(module="lane_tile", config={"cfg_thing": "32'd1"})})
    text = generate_scan_composition_top_sv(composition, sources=(source,), platform_id="DPV1")
    assert "lane_tile front_unpack (" in text

    with pytest.raises(ScanCompositionError, match=r"config binding 'cfg_typo' is not an input port"):
        generate_scan_composition_top_sv(
            composition.model_copy(update={"front_unpack": TileInstance(module="lane_tile", config={"cfg_typo": "32'd1"})}),
            sources=(source,),
            platform_id="DPV1",
        )
    with pytest.raises(ScanCompositionError, match="dau_absent_unpacker.*not found"):
        generate_scan_composition_top_sv(
            composition.model_copy(update={"front_unpack": TileInstance(module="dau_absent_unpacker")}), sources=(source,), platform_id="DPV1"
        )


def test_wide_data_width_splits_the_m_axi_into_wide_read_and_narrow_write() -> None:
    """A dispatch-shaped quad scan at data_width=512 emits a wide read-only
    M_AXI_R (the burst reader) + the narrow write-only M_AXI_W (64-bit record
    writers), the reader/dispatcher widened, the write path untouched."""
    comp = ScanComposition(
        name="wide",
        module_name="dau_mm_wide_job",
        lanes=tuple(LaneTile(module="dau_int32_field_sum_aggregation", count_port="agg_count") for _ in range(4)),
        partitioner=TileInstance(module="dau_int32_key_mask_dispatcher", params={"IN_WIDTH": 512}),
        data_width=512,
    )
    sv = generate_scan_composition_top_sv(comp, platform_id="DPV1")
    assert "M_AXI_R ARADDR" in sv and "M_AXI_W AWADDR" in sv
    assert "input wire [511:0] m_axi_r_rdata" in sv  # wide read
    assert "output wire [63:0] m_axi_w_wdata" in sv  # narrow write (records stay 64-bit)
    assert ".DATA_WIDTH(512)" in sv and ".IN_WIDTH(512)" in sv
    assert ".m_axi_araddr(m_axi_r_araddr)" in sv and ".m_axi_awaddr(m_axi_w_awaddr)" in sv
    assert "wire [511:0] scan_data;" in sv
    # the single shared M_AXI is gone
    assert "input wire [63:0] m_axi_rdata," not in sv


def _wide_lane_composition() -> ScanComposition:
    return ScanComposition(
        name="wide-lane",
        module_name="dau_mm_wide_lane_job",
        lanes=(
            LaneTile(
                module="dau_int32_wide_row_projection",
                params={"SLOTS": 4},
                count_port="record_count",
                chain=(
                    TileInstance(module="dau_int32_wide_row_predicate_filter", params={"SLOTS": 4}),
                    TileInstance(module="dau_int32_wide_row_transform", params={"SLOTS": 4}),
                ),
            ),
        ),
        data_width=512,
        wide_lane=True,
    )


def test_wide_lane_taps_the_wide_reader_directly() -> None:
    """A wide lane consumes whole reader beats through every chain stage,
    then emits standard 64-bit records to the unchanged writer."""
    sv = generate_scan_composition_top_sv(_wide_lane_composition(), platform_id="DPV1")

    assert "    assign filt_out_valid_0 = scan_valid;\n" in sv
    assert "    assign scan_ready = filt_out_ready_0;\n" in sv
    assert "    assign filt_out_data_0 = scan_data;\n" in sv
    assert "    assign filt_out_last_0 = scan_last;\n" in sv
    assert "    assign filt_status_valid_0 = 1'b0;\n" in sv
    assert "    assign filt_status_ready_0 = 1'b0;\n" in sv
    assert "    assign filt_status_error_0 = 1'b0;\n" in sv
    assert "    assign filt_status_error_code_0 = 8'd0;\n" in sv
    assert "dau_stream_broadcast" not in sv
    assert " partitioner (" not in sv
    assert "bcast_" not in sv
    assert "part_out_" not in sv

    assert "wire [511:0] filt_out_data_0;" in sv
    assert "wire [511:0] chain0_out_data_0;" in sv
    assert "wire [511:0] chain1_out_data_0;" in sv
    assert "wire [63:0] tile_out_data_0;" in sv
    assert "wire [63:0] wr_wdata_0;" in sv
    assert sv.count(".SLOTS(4)") == 3

    assert "M_AXI_R ARADDR" in sv and "M_AXI_W AWADDR" in sv
    assert "input wire [511:0] m_axi_r_rdata" in sv
    assert "output wire [63:0] m_axi_w_wdata" in sv
    assert ".DATA_WIDTH(512)" in sv
    assert "ASSOCIATED_BUSIF S_AXI:M_AXI_R:M_AXI_W" in sv
    assert "wire length_ok = (input_length_bytes != 32'd0) && (input_length_bytes[5:0] == 6'd0);" in sv


def test_wide_lane_requires_exactly_one_lane() -> None:
    lane = LaneTile(module="dau_int32_wide_row_projection", count_port="record_count")
    with pytest.raises(ValidationError, match="wide_lane.*exactly one lane"):
        ScanComposition(name="bad", module_name="dau_bad", lanes=(lane, lane), data_width=512, wide_lane=True)


def test_wide_lane_rejects_a_shared_partitioner() -> None:
    composition = _wide_lane_composition().model_dump()
    composition["partitioner"] = TileInstance(module="dau_int32_key_mask_dispatcher")
    with pytest.raises(ValidationError, match="wide_lane.*shared partitioner"):
        ScanComposition.model_validate(composition)


def test_a_packed_front_feeds_a_wide_lane_only_at_matching_widths() -> None:
    """The packed feed and the wide lane compose, but only coherently.

    This pair was mutually exclusive until the unpacker could emit more than
    one quad row per beat: a wide lane read whole quad rows straight from the
    reader and so burned 16 DDR bytes per row. Packed it moves 8, which is
    what puts the next SLOTS doubling under the DDR ceiling.

    Two widths are in play and conflating them is the hazard. ``data_width``
    is the READ -- SLOTS packed 64-bit rows per beat -- while the lane sees
    the front's OUT_WIDTH, SLOTS whole 128-bit quad rows.
    """
    base = _wide_lane_composition().model_dump()

    # a 64-bit front emits at most one quad row and cannot feed a wide lane
    narrow = dict(base, front_unpack=TileInstance(module="dau_int32_row_unpack", params={"OUT_WIDTH": 64}))
    with pytest.raises(ValidationError, match="emits at most one"):
        ScanComposition.model_validate(narrow)

    # and the read width must be the PACKED one, not the quad-row one
    slots = base["data_width"] // 128
    mismatched = dict(base, front_unpack=TileInstance(module="dau_int32_row_unpack", params={"OUT_WIDTH": slots * 128}))
    with pytest.raises(ValidationError, match="reads SLOTS packed 64-bit rows"):
        ScanComposition.model_validate(mismatched)

    coherent = dict(mismatched, data_width=slots * 64)
    assert ScanComposition.model_validate(coherent).front_unpack is not None


def test_wide_lane_rejects_a_narrow_data_width() -> None:
    with pytest.raises(ValidationError, match="wide_lane data_width.*128, 256, 512"):
        ScanComposition(
            name="bad",
            module_name="dau_bad",
            lanes=(LaneTile(module="dau_int32_wide_row_projection", count_port="record_count"),),
            data_width=64,
            wide_lane=True,
        )


def test_wide_data_width_needs_a_dispatcher_not_a_broadcast() -> None:
    """The broadcast shape is 64-bit only — a wide data_width with no shared
    partitioner is rejected (every lane would see every row)."""
    with pytest.raises(ValidationError, match="broadcast is 64-bit only"):
        ScanComposition(
            name="bad",
            module_name="dau_bad",
            lanes=tuple(LaneTile(module="dau_int32_field_sum_aggregation", count_port="agg_count") for _ in range(4)),
            data_width=256,
        )


def test_wide_packed_reader_is_a_named_followup() -> None:
    """A front unpacker (packed reader) with data_width > 64 is refused with
    the follow-up note (wide packed reads need the row-unpack widening)."""
    with pytest.raises(ValidationError, match="wide packed reads.*is a follow-up"):
        ScanComposition(
            name="bad",
            module_name="dau_bad",
            lanes=(LaneTile(module="dau_int32_field_sum_aggregation", count_port="agg_count"),),
            front_unpack=TileInstance(module="dau_int32_row_unpack", params={"OUT_WIDTH": 128}),
            partitioner=TileInstance(module="dau_int32_key_mask_dispatcher", params={"IN_WIDTH": 128}),
            data_width=256,
        )


def test_fused_chain_drains_mid_stage_close_outs_and_latches_their_errors() -> None:
    """The fused-chain protocol: a chain stage marked ``closes_out`` is a
    terminal-shaped tile fused mid-lane (as-of join, grouped aggregator).
    Its status is accepted unconditionally so the lane still presents ONE
    close-out (the terminal's), while its errors latch into a per-lane
    register that overrides the lane status and clears per job."""
    composition = ScanComposition(
        name="fused",
        module_name="dau_mm_fused_job",
        lanes=(
            LaneTile(
                module="dau_int32_rolling_moments",
                count_port="moment_count",
                chain=(
                    TileInstance(module="dau_int32_asof_backward", closes_out=True),
                    TileInstance(module="dau_int32_time_bucket_key"),
                    TileInstance(module="dau_int32_grouped_field_aggregation", closes_out=True),
                ),
            ),
        ),
    )
    sv = generate_scan_composition_top_sv(composition, platform_id="DPV1")

    # the closing stages accept their own status the cycle it appears
    assert "assign chain0_status_ready_0 = 1'b1;" in sv
    assert "assign chain2_status_ready_0 = 1'b1;" in sv
    # the silent stage keeps the ordinary upstream-first gating
    assert "assign chain1_status_ready_0 = unit_status_ready_0 && chain1_status_valid_0;" in sv
    # errors latch, override the lane status, and clear per job (multi-job safe)
    assert "reg fused_err_0;" in sv and "reg [7:0] fused_err_code_0;" in sv
    # a drained status lives for ONE cycle, so capture must outrank the
    # per-job clear: an error coinciding with job_start would otherwise be
    # lost and the corrupt job would report clean
    capture = (
        "end else if (!fused_err_0 && ((chain0_status_valid_0 && chain0_status_error_0) || (chain2_status_valid_0 && chain2_status_error_0))) begin"
    )
    assert capture in sv
    assert sv.index(capture) < sv.index("end else if (job_start) begin"), "the per-job clear must not outrank error capture"
    assert "if (chain0_status_valid_0 && chain0_status_error_0) begin" in sv
    assert "else if (chain2_status_valid_0 && chain2_status_error_0) begin" in sv
    assert "assign unit_status_valid_0 = fused_err_0 || fused_empty_close_0 || (tile_status_valid_0 || chain1_status_valid_0);" in sv
    # an all-miss upstream stage produces no rows, so nothing downstream ever
    # sees output_last: its own close-out is the lane's only end-of-batch
    # evidence and must reach the writer instead of being drained away
    assert "reg saw_out_0_0;" in sv and "reg empty_close_0_0;" in sv
    assert "wire fused_empty_close_0 = empty_close_0_0 || empty_close_0_2;" in sv
    assert "assign unit_status_error_0 = fused_err_0 ? 1'b1 : (chain1_status_valid_0 ? chain1_status_error_0 : tile_status_error_0);" in sv
    # the terminal still closes the lane out
    assert "assign tile_status_ready_0 = unit_status_ready_0 && !chain1_status_valid_0;" in sv


def test_a_chain_without_closing_stages_is_byte_identical() -> None:
    """The protocol is opt-in per stage: a chain of silent stages emits
    exactly what it did before the field existed (the goldens prove the
    same thing; this pins the intent)."""
    silent = ScanComposition(
        name="plain",
        module_name="dau_mm_plain_job",
        lanes=(
            LaneTile(
                module="dau_int32_field_sum_aggregation",
                count_port="aggregated_count",
                chain=(TileInstance(module="dau_int32_row_predicate_filter"), TileInstance(module="dau_int32_row_map_alu")),
            ),
        ),
    )
    sv = generate_scan_composition_top_sv(silent, platform_id="DPV1")
    assert "fused_err_0" not in sv
    assert "assign chain0_status_ready_0 = unit_status_ready_0 && chain0_status_valid_0;" in sv


def test_the_length_gate_follows_the_head_record_size() -> None:
    """A fused chain whose head reads a 24-byte record was rejecting an
    ODD number of perfectly good records, because the gate assumed the
    16-byte quad row. The gate now takes the largest power of two
    dividing the declared record size; a length on that grid that still
    tears a record is the head tile's ERR_STREAM job."""
    for row_bytes, grid in ((16, 16), (24, 8), (8, 8), (32, 32)):
        composition = ScanComposition(
            name="gate",
            module_name="dau_mm_gate_job",
            input_row_bytes=row_bytes,
            lanes=(LaneTile(module="dau_int32_field_sum_aggregation", count_port="aggregated_count"),),
        )
        sv = generate_scan_composition_top_sv(composition, platform_id="DPV1")
        bits = grid.bit_length() - 1
        assert f"input_length_bytes[{bits - 1}:0] == {bits}'d0" in sv, f"{row_bytes}B record wants a {grid}B grid"

    with pytest.raises(ScanCompositionError, match="positive multiple of 8"):
        generate_scan_composition_top_sv(
            ScanComposition(
                name="bad",
                module_name="m",
                input_row_bytes=12,
                lanes=(LaneTile(module="t", count_port="c"),),
            ),
            platform_id="DPV1",
        )


def test_a_geared_record_bus_must_be_a_size_the_rtl_can_elaborate() -> None:
    """Skipping the row-width ladder for geared compositions must not admit
    record sizes the gearbox and dispatcher elaboration-guard to [1, 16]
    words: a $fatal surfaces mid-synthesis with no attribution, so the
    composition has to refuse first."""
    base = _front_gearbox_composition()
    with pytest.raises(ScanCompositionError, match="geared record bus"):
        generate_scan_composition_top_sv(base.model_copy(update={"input_row_bytes": 136}), platform_id="DPV1")  # 17 words
    # a size that is not whole words is already refused upstream, by the rule
    # that owns the invariant rather than by the record-range check
    with pytest.raises(ScanCompositionError, match="positive multiple of 8"):
        generate_scan_composition_top_sv(base.model_copy(update={"input_row_bytes": 20}), platform_id="DPV1")
    # the legal end of the range still composes
    generate_scan_composition_top_sv(base.model_copy(update={"input_row_bytes": 128}), platform_id="DPV1")


def test_the_build_specs_are_frozen() -> None:
    """A spec that can mutate after validation is a spec whose identity, and
    therefore whose cache key and staged artifacts, depend on call order.
    Freezing is what makes the hash mean something."""
    import pytest as _pytest
    from pydantic import ValidationError as _ValidationError

    from dau_build.build_spec import DauBuildSpec
    from dau_build.tests.platform_fixtures import probe_platform

    frozen = (
        TileInstance(module="dau_tile"),
        _bar_noc_composition(),
        probe_platform(),
    )
    for model in frozen:
        field = next(iter(type(model).model_fields))
        with _pytest.raises(_ValidationError, match="frozen"):
            setattr(model, field, getattr(model, field))
    assert DauBuildSpec.model_config.get("frozen") is True


def test_a_wide_job_master_can_actually_reach_its_high_address_bits() -> None:
    """AXI-Lite carries 32 bits per access, so a job master wider than 32
    bits needs a SECOND register or its top bits are unreachable — which is
    exactly why a build could only ever map the low 4 GiB of an 8 GB module.

    The narrow case must stay byte-identical: a 32-bit design's register
    block is proven silicon and must not move.
    """
    narrow = _bar_noc_composition()
    wide = narrow.model_copy(update={"addr_width": 64})

    narrow_sv = generate_scan_composition_top_sv(narrow, platform_id="DPV1")
    wide_sv = generate_scan_composition_top_sv(wide, platform_id="DPV1")

    # the narrow design decodes only the low register, exactly as before
    assert "ADDR_INPUT_ADDRESS_LOW: input_address <= s_axi_wdata[31:0];" in narrow_sv
    assert "ADDR_INPUT_ADDRESS_HIGH" not in narrow_sv
    assert "OUTPUT_ADDRESS_HIGH" not in narrow_sv

    # the wide design splits the job read address across both halves
    assert "ADDR_INPUT_ADDRESS_LOW: input_address[31:0] <= s_axi_wdata;" in wide_sv
    assert "ADDR_INPUT_ADDRESS_HIGH: input_address[63:32] <= s_axi_wdata[31:0];" in wide_sv
    assert "ADDR_INPUT_ADDRESS_HIGH: s_axi_rdata <= input_address[63:32];" in wide_sv

    # ...and every lane's WRITE address too, or a lane could only write low
    for lane in range(len(narrow.lanes)):
        assert f"ADDR_LANE{lane}_OUTPUT_ADDRESS: lane_output_address_{lane}[31:0] <= s_axi_wdata;" in wide_sv
        assert f"ADDR_LANE{lane}_OUTPUT_ADDRESS_HIGH: lane_output_address_{lane}[63:32] <= s_axi_wdata[31:0];" in wide_sv
        assert f"ADDR_LANE{lane}_OUTPUT_ADDRESS_HIGH = 12'h" in wide_sv


def test_the_wide_address_registers_sit_where_dau_core_says() -> None:
    """The walker's offsets mirror `dau_core.registers`; a drift here means
    the host writes one address and the design decodes another."""
    registers = pytest.importorskip("dau_core.registers")

    from dau_build.scan_composition import RegisterLayout

    regs = RegisterLayout()
    assert regs.input_address_high == int(registers.RegisterOffset.INPUT_ADDRESS_HIGH)
    assert regs.lane_output_address_high == int(registers.NocLaneRegisterOffset.OUTPUT_ADDRESS_HIGH)
    # and the lane high register stays inside the lane stride
    assert regs.lane_output_address_high < regs.lane_stride


def test_a_wide_lane_behind_a_packed_front_reads_the_UNPACKED_stream() -> None:
    """The lane must consume the unpacker's output, not the reader's.

    Validation allowing the pair is not the same as wiring it. The generator
    fed every wide lane from ``scan_*`` at ``data_width``, which behind a
    packed front is the PACKED read -- half the width, and packed rows where
    the lane expects quad rows. The emitted Verilog looked entirely
    reasonable; the tiles carried the right SLOTS and the nets were simply
    the wrong width and the wrong source. The unpacker also drives
    ``scan_ready``, so the lane driving it as well was a second driver.
    """
    base = _wide_lane_composition().model_dump()
    slots = base["data_width"] // 128
    composition = ScanComposition.model_validate(
        dict(
            base,
            data_width=slots * 64,
            front_unpack=TileInstance(
                module="dau_int32_row_unpack",
                config=dict(_FRONT_UNPACK_CONFIG),
                params={"OUT_WIDTH": slots * 128},
            ),
        )
    )
    sv = generate_scan_composition_top_sv(composition, platform_id="DPV2")
    assert f"wire [{slots * 128 - 1}:0] feed_data;" in sv
    assert f"wire [{slots * 128 - 1}:0] filt_out_data_0;" in sv, "the lane net must be the UNPACKED width"
    assert "assign filt_out_data_0 = feed_data;" in sv, "the lane must read the unpacker, not the reader"
    assert "assign feed_ready = filt_out_ready_0;" in sv
    assert "assign scan_ready = filt_out_ready_0;" not in sv, "the unpacker already drives scan_ready"
