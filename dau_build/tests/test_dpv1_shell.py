from __future__ import annotations

from pathlib import Path

from dau_build.dpv1_shell import (
    DPV1_XDMA_PERSONALITY,
    GT_LANE_SWIZZLE,
    MmJobShellRequest,
    dpv1_constraints_xdc,
    gt_lane_swizzle_hook_tcl,
    mm_job_shell_project_tcl,
    write_mm_job_shell_artifacts,
)


def _request(tmp_path: Path) -> MmJobShellRequest:
    # dau-build is generic: the caller supplies HDL sources and generated
    # binding tops; this test uses its own stand-ins
    tile = tmp_path / "stream_tile.sv"
    tile.write_text("module stream_tile; endmodule\n")
    return MmJobShellRequest(
        output_root=tmp_path / "shell",
        hdl_sources=(tile,),
        generated_sources=(("my_mm_top.v", "module my_mm_top; endmodule\n"),),
        top_module="my_mm_top",
    )


def test_personality_mirrors_the_proven_shell() -> None:
    # the hardware rules: 64-bit prefetchable BARs, 128K KB-scale AXI-Lite, QPLL1
    assert DPV1_XDMA_PERSONALITY["axil_master_64bit_en"] == "true"
    assert DPV1_XDMA_PERSONALITY["axil_master_prefetchable"] == "true"
    assert DPV1_XDMA_PERSONALITY["xdma_pcie_prefetchable"] == "true"
    assert DPV1_XDMA_PERSONALITY["axilite_master_scale"] == "Kilobytes"
    assert DPV1_XDMA_PERSONALITY["axilite_master_size"] == "128"
    assert DPV1_XDMA_PERSONALITY["plltype"] == "QPLL1"
    assert DPV1_XDMA_PERSONALITY["pf0_device_id"] == "7011"


def test_project_tcl_embeds_personality_staging_and_hook(tmp_path: Path) -> None:
    text = mm_job_shell_project_tcl(_request(tmp_path))
    for key, value in DPV1_XDMA_PERSONALITY.items():
        assert f"CONFIG.{key} {{{value}}}" in text
    # staging layout from the register contract
    assert "-offset 0x00000000 -range 0x00020000" in text  # input BRAM
    assert "-offset 0x00100000 -range 0x00001000" in text  # output BRAM
    assert "-offset 0x00001000 -range 0x00001000" in text  # register window
    # swizzle applied as an implementation hook, never XDC
    assert 'STEPS.OPT_DESIGN.TCL.PRE "$origin_dir/gt_lane_swizzle.tcl"' in text
    assert "lane swizzle verified" in text
    assert "DAU_MM_JOB_BUILD_OK" in text


def test_swizzle_hook_covers_all_lanes() -> None:
    text = gt_lane_swizzle_hook_tcl()
    for lane, channel in GT_LANE_SWIZZLE:
        assert channel in text
    assert "expected 4 GTPE2_CHANNEL lane cells" in text
    assert "reset_property" in text


def test_constraints_have_no_gt_locs() -> None:
    text = dpv1_constraints_xdc()
    assert "GTPE2_CHANNEL" not in text  # swizzle lives in the hook
    assert "PACKAGE_PIN A10" in text
    assert "CFGBVS VCCO" in text
    assert "BITSTREAM.GENERAL.COMPRESS TRUE" in text


def test_write_artifacts_emits_generated_sources_and_scripts(tmp_path: Path) -> None:
    request = _request(tmp_path)
    written = write_mm_job_shell_artifacts(request)
    names = sorted(path.name for path in written)
    assert names == ["build_mm_job.tcl", "constraints.xdc", "gt_lane_swizzle.tcl", "my_mm_top.v"]
    assert (request.output_root / "my_mm_top.v").read_text() == "module my_mm_top; endmodule\n"
    tcl = (request.output_root / "build_mm_job.tcl").read_text()
    assert "my_mm_top.v" in tcl
    assert "stream_tile.sv" in tcl
