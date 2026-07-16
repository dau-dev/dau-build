from __future__ import annotations

from pathlib import Path

from dau_build.dpv1_shell import (
    GT_LANE_SWIZZLE,
    MmDdrJobShellRequest,
    MmJobShellRequest,
    _lane_swizzle_verify_tcl,
    dpv1_constraints_xdc,
    dpv1_ddr_constraints_xdc,
    dpv1_xdma_personality,
    gt_lane_swizzle_hook_tcl,
    mm_ddr_job_shell_project_tcl,
    mm_job_shell_project_tcl,
    write_mm_ddr_job_shell_artifacts,
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
    assert dpv1_xdma_personality().params["axil_master_64bit_en"] == "true"
    assert dpv1_xdma_personality().params["axil_master_prefetchable"] == "true"
    assert dpv1_xdma_personality().params["xdma_pcie_prefetchable"] == "true"
    assert dpv1_xdma_personality().params["axilite_master_scale"] == "Kilobytes"
    assert dpv1_xdma_personality().params["axilite_master_size"] == "128"
    assert dpv1_xdma_personality().params["plltype"] == "QPLL1"
    assert dpv1_xdma_personality().params["pf0_device_id"] == "7011"


def test_project_tcl_embeds_personality_staging_and_hook(tmp_path: Path) -> None:
    text = mm_job_shell_project_tcl(_request(tmp_path))
    for key, value in dpv1_xdma_personality().params.items():
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


def test_swizzle_hook_follows_the_placement_gt_family() -> None:
    """The hook targets the GT channel family named by the placement sites
    (GTX on Kintex-7 boards), so a second platform is data, not a code path."""
    placements = tuple((lane, f"GTXE2_CHANNEL_X0Y{7 - lane}") for lane in range(8))
    text = gt_lane_swizzle_hook_tcl(placements)
    assert "REF_NAME == GTXE2_CHANNEL || ORIG_REF_NAME == GTXE2_CHANNEL" in text
    assert "expected 8 GTXE2_CHANNEL lane cells" in text
    assert "GTPE2" not in text
    verify = _lane_swizzle_verify_tcl(placements)
    assert "REF_NAME == GTXE2_CHANNEL || ORIG_REF_NAME == GTXE2_CHANNEL" in verify
    assert "GTXE2_CHANNEL_X0Y0" in verify


def test_constraints_have_no_gt_locs() -> None:
    text = dpv1_constraints_xdc()
    assert "GTPE2_CHANNEL" not in text  # swizzle lives in the hook
    assert "PACKAGE_PIN A10" in text
    assert "CFGBVS VCCO" in text
    assert "BITSTREAM.GENERAL.COMPRESS TRUE" in text


def _ddr_request(tmp_path: Path) -> MmDdrJobShellRequest:
    tile = tmp_path / "stream_tile.sv"
    tile.write_text("module stream_tile; endmodule\n")
    prj = tmp_path / "mig.prj"
    prj.write_text('<Project NoOfControllers="1"></Project>\n')
    return MmDdrJobShellRequest(
        output_root=tmp_path / "shell",
        hdl_sources=(tile,),
        generated_sources=(("my_ddr_top.v", "module my_ddr_top; endmodule\n"),),
        top_module="my_ddr_top",
        mig_prj=prj,
    )


def test_ddr_project_tcl_embeds_mig_and_shared_memory_path(tmp_path: Path) -> None:
    text = mm_ddr_job_shell_project_tcl(_ddr_request(tmp_path))
    for key, value in dpv1_xdma_personality().params.items():
        assert f"CONFIG.{key} {{{value}}}" in text
    # the memory controller comes from the vendored proven configuration
    assert "xilinx.com:ip:mig_7series" in text
    assert 'CONFIG.XML_INPUT_FILE "$origin_dir/dpv1_mig.prj"' in text
    assert "CONFIG.MIG_DONT_TOUCH_PARAM {Custom}" in text
    # proven wiring: tied-high resets, calibration LED, XADC temperature
    assert "[get_bd_pins mig_0/sys_rst] [get_bd_pins mig_0/aresetn]" in text
    assert "mig_0/init_calib_complete" in text and "LED_A4" in text
    assert "[get_bd_pins xadc_0/temp_out] [get_bd_pins mig_0/device_temp_i]" in text
    # XDMA and the job master share the controller; CDC at ui_clk
    assert "CONFIG.NUM_SI {2}" in text
    assert "[get_bd_pins mig_0/ui_clk] [get_bd_pins smc/aclk1]" in text
    # both masters see the whole DDR at 0x0
    assert text.count("-offset 0x00000000 -range 0x40000000") == 2
    assert "-offset 0x00001000 -range 0x00001000" in text  # register window
    assert "-offset 0x00010000 -range 0x00010000" in text  # XADC window
    assert 'STEPS.OPT_DESIGN.TCL.PRE "$origin_dir/gt_lane_swizzle.tcl"' in text
    assert "DAU_MM_JOB_BUILD_OK" in text
    # no BRAM staging in the DDR shell
    assert "axi_bram_ctrl" not in text


def test_ddr_constraints_add_sys_clk_and_calib_led_only() -> None:
    base = dpv1_constraints_xdc()
    text = dpv1_ddr_constraints_xdc()
    assert text.startswith(base)
    assert "LVDS_25" in text and "sys_clk_clk_p" in text
    assert "PACKAGE_PIN H4" in text and "LED_A4" in text
    # sys_clk placement belongs to the MIG .prj, never the XDC
    assert "PACKAGE_PIN J19" not in text and "PACKAGE_PIN H19" not in text


def test_write_ddr_artifacts_vendor_the_mig_prj(tmp_path: Path) -> None:
    request = _ddr_request(tmp_path)
    written = write_mm_ddr_job_shell_artifacts(request)
    names = sorted(path.name for path in written)
    assert names == ["build_mm_job.tcl", "constraints.xdc", "dpv1_mig.prj", "gt_lane_swizzle.tcl", "my_ddr_top.v"]
    assert (request.output_root / "dpv1_mig.prj").read_text() == request.mig_prj.read_text()
    tcl = (request.output_root / "build_mm_job.tcl").read_text()
    assert "my_ddr_top.v" in tcl
    assert "stream_tile.sv" in tcl


def test_write_artifacts_emits_generated_sources_and_scripts(tmp_path: Path) -> None:
    request = _request(tmp_path)
    written = write_mm_job_shell_artifacts(request)
    names = sorted(path.name for path in written)
    assert names == ["build_mm_job.tcl", "constraints.xdc", "gt_lane_swizzle.tcl", "my_mm_top.v"]
    assert (request.output_root / "my_mm_top.v").read_text() == "module my_mm_top; endmodule\n"
    tcl = (request.output_root / "build_mm_job.tcl").read_text()
    assert "my_mm_top.v" in tcl
    assert "stream_tile.sv" in tcl


def _probe_platform(**overrides):
    from dau_build.platforms import HostLink, PlatformDefinition, PlatformMemory, ResourceBudget, XdmaPersonality

    base = dict(
        name="probe-k7",
        part="xc7k325tffg900-2",
        budget=ResourceBudget(lut=178800, ff=382600, bram36=415, dsp=810),
        host_link=HostLink(
            interface="pcie-xdma",
            pcie_lanes=8,
            xdma_personality=XdmaPersonality(params={"pl_link_cap_max_link_width": "X8", "axisten_freq": "125"}),
        ),
        memory=PlatformMemory(kind="ddr3", size_bytes=8 << 30),
        constraints_xdc="set_property CFGBVS GND [current_design]\n",
    )
    base.update(overrides)
    return PlatformDefinition(**base)


def test_constraints_match_committed_goldens() -> None:
    """Moving the pin constraints from code into the dpv1 platform config
    must not change a byte of the emitted XDC."""
    fixtures = Path(__file__).parent / "fixtures" / "dpv1_shell"
    assert dpv1_constraints_xdc() == (fixtures / "constraints.xdc").read_text()
    assert dpv1_ddr_constraints_xdc() == (fixtures / "constraints_ddr.xdc").read_text()


def test_platform_threads_through_the_ddr_project(tmp_path: Path) -> None:
    """A registered non-dpv1 board changes the generated project through
    config data alone: part, personality, constraints, and (no) swizzle all
    come from the platform."""
    platform = _probe_platform()
    request = _ddr_request(tmp_path).model_copy(update={"platform": platform})
    text = mm_ddr_job_shell_project_tcl(request)
    assert "-part xc7k325tffg900-2" in text
    assert "CONFIG.pl_link_cap_max_link_width {X8}" in text
    assert "CONFIG.plltype" not in text  # the dpv1 personality is not inherited
    # no lane placements: no swizzle hook, no post-route verification
    assert "gt_lane_swizzle" not in text
    assert "lane swizzle verified" not in text
    # the staged MIG prj falls back to the caller's filename
    assert 'CONFIG.XML_INPUT_FILE "$origin_dir/mig.prj"' in text
    written = write_mm_ddr_job_shell_artifacts(request)
    names = sorted(path.name for path in written)
    assert names == ["build_mm_job.tcl", "constraints.xdc", "mig.prj", "my_ddr_top.v"]
    constraints = (request.output_root / "constraints.xdc").read_text()
    assert constraints == "# GENERATED by dau_build.dpv1_shell — do not edit.\n" + platform.constraints_xdc


def test_request_part_resolves_from_the_platform(tmp_path: Path) -> None:
    tile = tmp_path / "stream_tile.sv"
    tile.write_text("module stream_tile; endmodule\n")
    common = dict(
        output_root=tmp_path / "shell",
        hdl_sources=(tile,),
        generated_sources=(("my_mm_top.v", "module my_mm_top; endmodule\n"),),
        top_module="my_mm_top",
    )
    assert MmJobShellRequest(**common).resolved_part == "xc7a200tfbg484-2"  # dpv1 default
    assert MmJobShellRequest(platform=_probe_platform(), **common).resolved_part == "xc7k325tffg900-2"
    # an explicit part override wins over the platform
    assert MmJobShellRequest(platform=_probe_platform(), part="xc7k325tffg676-1", **common).resolved_part == "xc7k325tffg676-1"
    # resolution is live: swapping the platform never leaves a stale part
    swapped = MmJobShellRequest(**common).model_copy(update={"platform": _probe_platform()})
    assert swapped.resolved_part == "xc7k325tffg900-2"


def test_default_platform_instances_are_not_shared(tmp_path: Path) -> None:
    tile = tmp_path / "stream_tile.sv"
    tile.write_text("module stream_tile; endmodule\n")
    common = dict(
        output_root=tmp_path / "shell",
        hdl_sources=(tile,),
        generated_sources=(("my_mm_top.v", "module my_mm_top; endmodule\n"),),
        top_module="my_mm_top",
    )
    first = MmJobShellRequest(**common)
    first.platform.host_link.xdma_personality.params["plltype"] = "TAMPERED"
    second = MmJobShellRequest(**common)
    assert second.platform.host_link.xdma_personality.params["plltype"] == "QPLL1"


def test_placeholder_platform_projects_refuse_to_build(tmp_path: Path) -> None:
    """A placeholder board generates a project, but the generated script
    itself refuses to build — even a bare Vivado run fails fast."""
    platform = _probe_platform(placeholders=("host_link.xdma_personality",))
    request = _ddr_request(tmp_path).model_copy(update={"platform": platform})
    text = mm_ddr_job_shell_project_tcl(request)
    assert 'puts "DAU_MM_JOB_BUILD_FAILED placeholder-platform probe-k7: host_link.xdma_personality"' in text
    assert text.index("DAU_MM_JOB_BUILD_FAILED placeholder-platform") < text.index("create_project")
    # a measured board emits no guard
    assert "placeholder-platform" not in mm_ddr_job_shell_project_tcl(_ddr_request(tmp_path))


def test_project_tcl_matches_pre_fragment_goldens() -> None:
    """The fragment refactor (shared preamble/postamble emitters) must not
    change a byte of the generated scripts — these fixtures were captured
    from the pre-refactor monolithic templates."""
    fixtures = Path(__file__).parent / "fixtures" / "dpv1_shell"
    mm = MmJobShellRequest(
        output_root=Path("/work/shell"),
        hdl_sources=(Path("/src/tile.sv"), Path("/src/identity.v")),
        generated_sources=(("my_top.v", "module my_top; endmodule\n"),),
        top_module="my_top",
    )
    ddr = MmDdrJobShellRequest(
        output_root=Path("/work/shell"),
        hdl_sources=(Path("/src/tile.sv"),),
        generated_sources=(("my_ddr_top.v", "module my_ddr_top; endmodule\n"),),
        top_module="my_ddr_top",
        mig_prj=Path("/src/mig.prj"),
    )
    assert mm_job_shell_project_tcl(mm) == (fixtures / "mm_job_project.tcl").read_text()
    assert mm_ddr_job_shell_project_tcl(ddr) == (fixtures / "mm_ddr_job_project.tcl").read_text()
