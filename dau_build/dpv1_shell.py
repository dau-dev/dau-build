"""DAU platform variant 1 (dpv1) MM job shell project generation.

Productization of the proven bring-up flow: emits the
Vivado project Tcl, constraints, and the lane-swizzle implementation hook for
the memory-mapped DAU job shell on the DAU platform variant 1 (dpv1) (XC7A200T over Thunderbolt
XDMA). Hardware rules baked in (see dau-docs GUIDELINES):

- the PCIe personality must mirror the proven shell exactly: 64-bit
  prefetchable BARs (the Thunderbolt bridge only forwards the 64-bit
  prefetchable window; 32-bit non-prefetch BARs enumerate but all memory
  reads return all-ones), 128 KB AXI-Lite BAR, QPLL1 GTP clocking;
- the reversed lane-to-GT-channel mapping is applied as a pre-opt_design
  implementation hook (XDC-time LOCs conflict with the IP's internal BEL/LOC
  constraints) and verified post-route;
- BRAM staging addresses follow DEFAULT_STREAM_JOB_REGISTER_CONTRACT.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from ccflow import BaseModel
from pydantic import Field

from dau_build.platforms import PlatformDefinition

DPV1_PART = "xc7a200tfbg484-2"

# lane index -> GTPE2 channel site (reversed lane order on the DAU platform variant 1 (dpv1))
GT_LANE_SWIZZLE = ((3, "GTPE2_CHANNEL_X0Y7"), (0, "GTPE2_CHANNEL_X0Y6"), (2, "GTPE2_CHANNEL_X0Y5"), (1, "GTPE2_CHANNEL_X0Y4"))


# The platform (part, the 47 value_src=user XCI personality parameters
# applied verbatim — a hand-picked subset is memory-dead on hardware —
# pin constraints, lane swizzle) lives in config/platform/platforms/dau/dpv1.yaml,
# the single source. Resolve it once per process.
@lru_cache(maxsize=1)
def _dpv1_platform() -> PlatformDefinition:
    from dau_build.config import resolve_platform

    return resolve_platform("platforms/dau/dpv1")


def _default_platform() -> PlatformDefinition:
    """A fresh dpv1 platform per request (the resolved config is cached, the
    instance is copied so no two requests share a mutable default)."""
    return _dpv1_platform().model_copy(deep=True)


def dpv1_xdma_personality():
    """The dpv1 XDMA personality, resolved from the platform config."""
    return _dpv1_platform().host_link.xdma_personality


class MmJobShellRequest(BaseModel):
    """Inputs for generating the MM job shell project. The caller supplies
    the generated binding sources as (filename, text) pairs — dau-build is
    generic platform integration and generates the *project*, never the
    domain HDL. The target board is the composed ``platform``
    (``PlatformDefinition``, default dpv1): part, XDMA personality, pin
    constraints, and lane swizzle all come from it (``part`` is an explicit
    override; ``resolved_part`` is what the project builds with). Staging
    addresses are plain values; DAU flows pass their register-contract
    numbers."""

    output_root: Path
    hdl_sources: tuple[Path, ...]
    generated_sources: tuple[tuple[str, str], ...]
    top_module: str
    platform: PlatformDefinition = Field(default_factory=_default_platform)
    part: str | None = None
    input_buffer_address: int = 0x0000_0000
    output_buffer_address: int = 0x0010_0000
    register_window_offset: int = 0x0000_1000
    input_bram_bytes: int = 0x0002_0000  # 128 KiB
    output_bram_bytes: int = 0x0000_1000  # 4 KiB
    jobs: int = 12

    @property
    def resolved_part(self) -> str:
        """The FPGA part the project builds with: the explicit ``part``
        override when given, else the platform's part."""
        return self.part or self.platform.part


class MmDdrJobShellRequest(BaseModel):
    """Inputs for generating the DDR-staged MM job shell project: the MM
    job shell with the block RAM staging replaced by the memory controller
    (MIG, configured from a caller-supplied .prj) shared between the XDMA
    memory path and the job top's AXI4 master. The target board is the
    composed ``platform`` (default dpv1). The register aperture is
    unchanged; the XADC feeds the controller's temperature compensation and
    is host-readable in the upper half of the AXI-Lite BAR."""

    output_root: Path
    hdl_sources: tuple[Path, ...]
    generated_sources: tuple[tuple[str, str], ...]
    top_module: str
    mig_prj: Path
    platform: PlatformDefinition = Field(default_factory=_default_platform)
    part: str | None = None
    register_window_offset: int = 0x0000_1000
    xadc_window_offset: int = 0x0001_0000
    ddr_bytes: int = 0x4000_0000  # 1 GiB
    jobs: int = 12

    @property
    def resolved_part(self) -> str:
        """The FPGA part the project builds with: the explicit ``part``
        override when given, else the platform's part."""
        return self.part or self.platform.part

    @property
    def staged_mig_prj_name(self) -> str:
        """The name the MIG ``.prj`` is vendored under inside the project —
        the platform's registered name when it has one, else the caller's
        filename."""
        return self.platform.memory.mig_prj or self.mig_prj.name


def _gt_channel_ref_name(lane_placements: tuple[tuple[int, str], ...]) -> str:
    """The GT channel cell REF_NAME the swizzle targets, derived from the
    placement site names (``GTPE2_CHANNEL_X0Y7`` -> ``GTPE2_CHANNEL`` on the
    dpv1 GTP part, ``GTXE2_CHANNEL_X0Y7`` -> ``GTXE2_CHANNEL`` on Kintex-7
    GTX boards) — one source, so the hook follows the platform's transceiver
    family without a code path per board."""
    return lane_placements[0][1].rsplit("_X", 1)[0]


def gt_lane_swizzle_hook_tcl(lane_placements: tuple[tuple[int, str], ...] = GT_LANE_SWIZZLE) -> str:
    """Pre-opt_design hook applying the platform's lane swizzle (default: the
    DAU platform variant 1 (dpv1) mapping), version-robust across XDMA
    internal hierarchy renames. The GT channel family (GTP/GTX) is derived
    from the placement site names."""
    swizzle_pairs = " ".join(f"{lane} {channel}" for lane, channel in lane_placements)
    lane_count = len(lane_placements)
    channel_ref = _gt_channel_ref_name(lane_placements)
    return f"""# GENERATED by dau_build.dpv1_shell — do not edit.
# Pre-opt_design implementation hook: apply the DAU platform variant 1 (dpv1) PCIe lane swizzle
# after the IP's own XDC constraints, per CRITICAL WARNING 18-4427 guidance.
set swizzle {{{swizzle_pairs}}}
set lane_cells [dict create]
set gts [get_cells -hierarchical -quiet -filter {{REF_NAME == {channel_ref} || ORIG_REF_NAME == {channel_ref}}}]
foreach cell $gts {{
    if {{[regexp {{pipe_lane\\[(\\d+)\\]}} $cell -> lane]}} {{
        dict set lane_cells $lane $cell
    }}
}}
if {{[dict size $lane_cells] != {lane_count}}} {{
    error "gt_lane_swizzle: expected {lane_count} {channel_ref} lane cells, found [dict size $lane_cells]"
}}
# two-phase: release every lane's LOC first, then place — interleaved
# reset/set hits "bel is occupied" when a target site is still held by a
# not-yet-processed lane (Vivado 12-2285, a CRITICAL WARNING that silently
# leaves lanes crossed)
foreach {{lane channel}} $swizzle {{
    reset_property -quiet LOC [dict get $lane_cells $lane]
}}
foreach {{lane channel}} $swizzle {{
    set cell [dict get $lane_cells $lane]
    set_property LOC $channel $cell
    set got [get_property LOC $cell]
    if {{$got ne $channel}} {{
        error "gt_lane_swizzle: pipe_lane\\[$lane\\] LOC is '$got', wanted $channel"
    }}
    puts "gt_lane_swizzle: pipe_lane\\[$lane\\] ($cell) -> $channel"
}}
"""


_CONSTRAINTS_BANNER = "# GENERATED by dau_build.dpv1_shell — do not edit.\n"


def shell_constraints_xdc(platform: PlatformDefinition) -> str:
    """Pin constraints for the MM job shell of a registered platform (no GT
    LOCs — a lane swizzle, when the board has one, is applied by the
    implementation hook). The text is the platform's ``constraints_xdc``
    config data behind the generator banner."""
    return _CONSTRAINTS_BANNER + platform.constraints_xdc


def ddr_shell_constraints_xdc(platform: PlatformDefinition) -> str:
    """MM shell pin constraints extended with the platform memory system's
    additions (``memory.constraints_xdc``)."""
    base = shell_constraints_xdc(platform)
    if not platform.memory.constraints_xdc:
        return base
    return base + "\n" + platform.memory.constraints_xdc


def dpv1_constraints_xdc() -> str:
    """Pin constraints for the dpv1 MM job shell, resolved from the platform
    config (the single source; no GT LOCs here — the swizzle is applied by
    the implementation hook)."""
    return shell_constraints_xdc(_dpv1_platform())


def _placeholder_guard_tcl(platform: PlatformDefinition) -> str:
    """A hard refusal at the top of a placeholder board's project script: the
    project may be generated and inspected, but any build attempt — through
    the task or a bare Vivado invocation — fails fast until the placeholder
    values are measured on hardware and cleared."""
    if not platform.placeholders:
        return ""
    placeholder_list = ", ".join(platform.placeholders)
    return (
        f"# PLACEHOLDER BOARD: {platform.name} carries unmeasured hardware-derived\n"
        f"# values ({placeholder_list}); refuse to build until they are measured\n"
        f"# and cleared from the platform's `placeholders` config.\n"
        f'puts "DAU_MM_JOB_BUILD_FAILED placeholder-platform {platform.name}: {placeholder_list}"\n'
        "exit 1\n"
    )


def _project_preamble_tcl(request, *, banner: str) -> str:
    """Shared create_project/add_files/constraints/XDMA-BD preamble: every
    shell starts from the platform's PCIe front end (its complete XDMA
    personality — dpv1's proven 47 parameters by default)."""
    xdma_config = request.platform.host_link.xdma_personality.to_tcl_config()
    generated_paths = tuple(request.output_root / name for name, _ in request.generated_sources)
    sources = " \\\n".join(f'    "{path.as_posix()}"' for path in (*request.hdl_sources, *generated_paths))
    sv_typing = "\n".join(
        f'set_property file_type SystemVerilog [get_files "{path.as_posix()}"]' for path in request.hdl_sources if path.suffix == ".sv"
    )
    return f"""# GENERATED by dau_build.dpv1_shell — do not edit.
{banner}
{_placeholder_guard_tcl(request.platform)}set origin_dir [file dirname [file normalize [info script]]]

create_project -force project_mm "$origin_dir/project_mm" -part {request.resolved_part}

add_files [list \\
{sources} \\
]
{sv_typing}

add_files -fileset constrs_1 "$origin_dir/constraints.xdc"
set_property PROCESSING_ORDER EARLY [get_files "$origin_dir/constraints.xdc"]

create_bd_design Top

set xdma_0 [create_bd_cell -type ip -vlnv xilinx.com:ip:xdma xdma_0]
set_property -dict [list \\
{xdma_config} \\
] $xdma_0

set refclk_buf [create_bd_cell -type ip -vlnv xilinx.com:ip:util_ds_buf refclk_buf]
set_property CONFIG.C_BUF_TYPE {{IBUFDSGTE}} $refclk_buf
set clkreq_low [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant clkreq_low]
set_property -dict [list CONFIG.CONST_VAL {{0}} CONFIG.CONST_WIDTH {{1}}] $clkreq_low

make_bd_intf_pins_external [get_bd_intf_pins xdma_0/pcie_mgt]
set_property name pcie_mgt [get_bd_intf_ports pcie_mgt_0]
set pcie_clkin [create_bd_intf_port -mode Slave -vlnv xilinx.com:interface:diff_clock_rtl:1.0 pcie_clkin]
set_property CONFIG.FREQ_HZ {{100000000}} $pcie_clkin
connect_bd_intf_net $pcie_clkin [get_bd_intf_pins refclk_buf/CLK_IN_D]
set pci_reset [create_bd_port -dir I -type rst pci_reset]
set_property CONFIG.POLARITY {{ACTIVE_LOW}} $pci_reset
connect_bd_net $pci_reset [get_bd_pins xdma_0/sys_rst_n]
set clkreq_port [create_bd_port -dir O -from 0 -to 0 pcie_clkreq_l]
connect_bd_net [get_bd_pins clkreq_low/dout] $clkreq_port
set led_port [create_bd_port -dir O -from 0 -to 0 LED_M2]
connect_bd_net [get_bd_pins xdma_0/user_lnk_up] $led_port
connect_bd_net [get_bd_pins refclk_buf/IBUF_OUT] [get_bd_pins xdma_0/sys_clk]

set dau_job [create_bd_cell -type module -reference {request.top_module} {request.top_module}_0]
"""


def _lane_swizzle_verify_tcl(lane_placements: tuple[tuple[int, str], ...]) -> str:
    """Post-route verification that every swizzled lane landed on its site."""
    swizzle_pairs = " ".join(f"{lane} {channel}" for lane, channel in lane_placements)
    channel_ref = _gt_channel_ref_name(lane_placements)
    return f"""set routed_lanes [dict create]
foreach cell [get_cells -hierarchical -quiet -filter {{REF_NAME == {channel_ref} || ORIG_REF_NAME == {channel_ref}}}] {{
    if {{[regexp {{pipe_lane\\[(\\d+)\\]}} $cell -> lane]}} {{
        dict set routed_lanes $lane [get_property LOC $cell]
    }}
}}
foreach {{lane channel}} {{{swizzle_pairs}}} {{
    if {{![dict exists $routed_lanes $lane] || [dict get $routed_lanes $lane] != $channel}} {{
        puts "DAU_MM_JOB_BUILD_FAILED lane $lane misplaced (expected $channel)"
        exit 1
    }}
}}
puts "lane swizzle verified"

"""


def _build_postamble_tcl(request) -> str:
    """Shared validate/synthesize/implement/verify-swizzle/bitstream tail.
    The lane-swizzle hook and its post-route verification are emitted only
    when the platform declares lane placements."""
    placements = request.platform.lane_placements
    hook_line = 'set_property STEPS.OPT_DESIGN.TCL.PRE "$origin_dir/gt_lane_swizzle.tcl" [get_runs impl_1]\n' if placements else ""
    verify_block = _lane_swizzle_verify_tcl(placements) if placements else ""
    return f"""validate_bd_design
save_bd_design

make_wrapper -files [get_files [get_property FILE_NAME [get_bd_designs Top]]] -top
add_files -norecurse "$origin_dir/project_mm/project_mm.gen/sources_1/bd/Top/hdl/Top_wrapper.v"
set_property top Top_wrapper [current_fileset]
update_compile_order -fileset sources_1

launch_runs synth_1 -jobs {request.jobs}
wait_on_run synth_1
if {{[get_property PROGRESS [get_runs synth_1]] != "100%"}} {{
    puts "DAU_MM_JOB_BUILD_FAILED synthesis"
    exit 1
}}

{hook_line}launch_runs impl_1 -to_step write_bitstream -jobs {request.jobs}
wait_on_run impl_1
if {{[get_property PROGRESS [get_runs impl_1]] != "100%"}} {{
    puts "DAU_MM_JOB_BUILD_FAILED implementation"
    exit 1
}}
open_run impl_1
report_utilization -file "$origin_dir/utilization_mm.rpt"
report_timing_summary -file "$origin_dir/timing_mm.rpt"

{verify_block}set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]]
file copy -force "$origin_dir/project_mm/project_mm.runs/impl_1/Top_wrapper.bit" "$origin_dir/dau_mm_job.bit"
puts "DAU_MM_JOB_BUILD_OK wns=$wns"
exit 0
"""


def dpv1_ddr_constraints_xdc() -> str:
    """dpv1 MM shell pin constraints extended for the DDR shell, resolved
    from the platform config: the memory controller's 200 MHz reference
    enters on J19/H19 (bank 15, LVDS_25 — pin placement lives in the MIG
    .prj, only the IOSTANDARD belongs here) and calibration-complete drives
    LED_A4."""
    return ddr_shell_constraints_xdc(_dpv1_platform())


def mm_ddr_job_shell_project_tcl(request: MmDdrJobShellRequest) -> str:
    """Full batch-mode project script for the DDR-staged job shell: the MM
    shell's XDMA personality with the staging BRAMs replaced by the memory
    controller (configured from the vendored .prj — the proven bring-up
    configuration, which also owns the sys_clk/DDR3 pin placement). The
    XDMA memory path and the job top's AXI4 master share the controller
    through one smartconnect that also bridges into the controller's
    ui_clk domain; resets follow the proven wiring (sys_rst and aresetn
    tied high, calibration status on LED_A4, XADC temperature into
    device_temp_i)."""
    preamble = _project_preamble_tcl(
        request,
        banner=(
            "# DAU MM DDR job shell: XDMA (memory-mapped, proven DAU platform variant 1\n"
            "# (dpv1) PCIe personality) with DDR staging behind the memory controller."
        ),
    )
    staging = f"""
# memory controller from the vendored proven configuration; the .prj owns
# the DDR3 and sys_clk pin placement
set mig_0 [create_bd_cell -type ip -vlnv xilinx.com:ip:mig_7series mig_0]
set_property -dict [list \\
    CONFIG.XML_INPUT_FILE "$origin_dir/{request.staged_mig_prj_name}" \\
    CONFIG.RESET_BOARD_INTERFACE {{Custom}} \\
    CONFIG.MIG_DONT_TOUCH_PARAM {{Custom}} \\
    CONFIG.BOARD_MIG_PARAM {{Custom}} \\
] $mig_0

set sys_clk [create_bd_intf_port -mode Slave -vlnv xilinx.com:interface:diff_clock_rtl:1.0 sys_clk]
set_property CONFIG.FREQ_HZ {{200000000}} $sys_clk
connect_bd_intf_net $sys_clk [get_bd_intf_pins mig_0/SYS_CLK]
make_bd_intf_pins_external [get_bd_intf_pins mig_0/DDR3]
set_property name DDR3 [get_bd_intf_ports DDR3_0]

set mig_high [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant mig_high]
set_property -dict [list CONFIG.CONST_VAL {{1}} CONFIG.CONST_WIDTH {{1}}] $mig_high
connect_bd_net [get_bd_pins mig_high/dout] [get_bd_pins mig_0/sys_rst] [get_bd_pins mig_0/aresetn]
set calib_led_port [create_bd_port -dir O LED_A4]
connect_bd_net [get_bd_pins mig_0/init_calib_complete] $calib_led_port

set xadc_0 [create_bd_cell -type ip -vlnv xilinx.com:ip:xadc_wiz xadc_0]
set_property -dict [list \\
    CONFIG.INTERFACE_SELECTION {{Enable_AXI}} \\
    CONFIG.ENABLE_TEMP_BUS {{true}} \\
    CONFIG.DCLK_FREQUENCY {{125}} \\
    CONFIG.ADC_CONVERSION_RATE {{1000}} \\
    CONFIG.ENABLE_RESET {{false}} \\
] $xadc_0
connect_bd_net [get_bd_pins xadc_0/temp_out] [get_bd_pins mig_0/device_temp_i]

# one memory-side smartconnect: XDMA (128-bit) and the job master (64-bit)
# into the controller's 128-bit S_AXI, bridging 125 MHz -> ui_clk
set smc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect smc]
set_property -dict [list CONFIG.NUM_SI {{2}} CONFIG.NUM_MI {{1}} CONFIG.NUM_CLKS {{2}}] $smc
connect_bd_intf_net [get_bd_intf_pins xdma_0/M_AXI] [get_bd_intf_pins smc/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins {request.top_module}_0/M_AXI] [get_bd_intf_pins smc/S01_AXI]
connect_bd_intf_net [get_bd_intf_pins smc/M00_AXI] [get_bd_intf_pins mig_0/S_AXI]

# the proven shell routes M_AXI_LITE through an interconnect, never directly
# into a register block; smc_lite mirrors that (pipelined, spec-strict
# handshakes between the XDMA AXI-Lite master and the module reference)
set smc_lite [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect smc_lite]
set_property -dict [list CONFIG.NUM_SI {{1}} CONFIG.NUM_MI {{2}}] $smc_lite
connect_bd_intf_net [get_bd_intf_pins xdma_0/M_AXI_LITE] [get_bd_intf_pins smc_lite/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins smc_lite/M00_AXI] [get_bd_intf_pins {request.top_module}_0/S_AXI]
connect_bd_intf_net [get_bd_intf_pins smc_lite/M01_AXI] [get_bd_intf_pins xadc_0/s_axi_lite]

connect_bd_net [get_bd_pins xdma_0/axi_aclk] \\
    [get_bd_pins smc/aclk] [get_bd_pins smc_lite/aclk] [get_bd_pins xadc_0/s_axi_aclk] \\
    [get_bd_pins {request.top_module}_0/s_axi_aclk]
connect_bd_net [get_bd_pins mig_0/ui_clk] [get_bd_pins smc/aclk1]
connect_bd_net [get_bd_pins xdma_0/axi_aresetn] \\
    [get_bd_pins smc/aresetn] [get_bd_pins smc_lite/aresetn] [get_bd_pins xadc_0/s_axi_aresetn] \\
    [get_bd_pins {request.top_module}_0/s_axi_aresetn]

assign_bd_address -offset 0x00000000 -range 0x{request.ddr_bytes:08X} -target_address_space [get_bd_addr_spaces xdma_0/M_AXI] [get_bd_addr_segs mig_0/memmap/memaddr] -force
assign_bd_address -offset 0x00000000 -range 0x{request.ddr_bytes:08X} -target_address_space [get_bd_addr_spaces {request.top_module}_0/M_AXI] [get_bd_addr_segs mig_0/memmap/memaddr] -force
assign_bd_address -offset 0x{request.register_window_offset:08X} -range 0x00001000 -target_address_space [get_bd_addr_spaces xdma_0/M_AXI_LITE] [get_bd_addr_segs {request.top_module}_0/S_AXI/*] -force
assign_bd_address -offset 0x{request.xadc_window_offset:08X} -range 0x00010000 -target_address_space [get_bd_addr_spaces xdma_0/M_AXI_LITE] [get_bd_addr_segs xadc_0/s_axi_lite/*] -force

"""
    return preamble + staging + _build_postamble_tcl(request)


def write_mm_ddr_job_shell_artifacts(request: MmDdrJobShellRequest) -> tuple[Path, ...]:
    """Write the caller-generated sources, the vendored MIG .prj, project
    Tcl, constraints, and swizzle hook into ``request.output_root``;
    returns the written paths."""
    request.output_root.mkdir(parents=True, exist_ok=True)
    written = []

    for name, text in request.generated_sources:
        path = request.output_root / name
        path.write_text(text)
        written.append(path)

    prj_path = request.output_root / request.staged_mig_prj_name
    prj_path.write_text(request.mig_prj.read_text())
    written.append(prj_path)

    for name, text in _shell_script_files(request, constraints=ddr_shell_constraints_xdc(request.platform)):
        path = request.output_root / name
        path.write_text(text)
        written.append(path)
    return tuple(written)


def _shell_script_files(request, *, constraints: str) -> tuple[tuple[str, str], ...]:
    """The project script, constraints, and (when the platform swizzles
    lanes) the swizzle hook, as (name, text) pairs."""
    project_tcl = mm_ddr_job_shell_project_tcl(request) if isinstance(request, MmDdrJobShellRequest) else mm_job_shell_project_tcl(request)
    files = [("build_mm_job.tcl", project_tcl), ("constraints.xdc", constraints)]
    if request.platform.lane_placements:
        files.append(("gt_lane_swizzle.tcl", gt_lane_swizzle_hook_tcl(request.platform.lane_placements)))
    return tuple(files)


def mm_job_shell_project_tcl(request: MmJobShellRequest) -> str:
    """Full batch-mode project script: create project, build the XDMA MM BD
    with BRAM staging at the register-contract addresses, synthesize and
    implement with the swizzle hook, verify post-route, emit the bitstream."""
    preamble = _project_preamble_tcl(
        request,
        banner=(
            "# DAU MM job shell: XDMA (memory-mapped, proven DAU platform variant 1 (dpv1) PCIe personality)\n"
            "# with BRAM staging at the stream-job contract addresses."
        ),
    )
    staging = f"""
set bram_ctrl_in [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl bram_ctrl_in]
set_property -dict [list CONFIG.DATA_WIDTH {{64}} CONFIG.SINGLE_PORT_BRAM {{1}}] $bram_ctrl_in
set bram_ctrl_out [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl bram_ctrl_out]
set_property -dict [list CONFIG.DATA_WIDTH {{64}} CONFIG.SINGLE_PORT_BRAM {{1}}] $bram_ctrl_out
set mem_in [create_bd_cell -type ip -vlnv xilinx.com:ip:blk_mem_gen mem_in]
set_property -dict [list CONFIG.Memory_Type {{True_Dual_Port_RAM}} CONFIG.use_bram_block {{BRAM_Controller}} CONFIG.Enable_B {{Use_ENB_Pin}}] $mem_in
set mem_out [create_bd_cell -type ip -vlnv xilinx.com:ip:blk_mem_gen mem_out]
set_property -dict [list CONFIG.Memory_Type {{True_Dual_Port_RAM}} CONFIG.use_bram_block {{BRAM_Controller}} CONFIG.Enable_B {{Use_ENB_Pin}}] $mem_out

connect_bd_intf_net [get_bd_intf_pins bram_ctrl_in/BRAM_PORTA] [get_bd_intf_pins mem_in/BRAM_PORTA]
connect_bd_intf_net [get_bd_intf_pins {request.top_module}_0/BRAM_IN] [get_bd_intf_pins mem_in/BRAM_PORTB]
connect_bd_intf_net [get_bd_intf_pins bram_ctrl_out/BRAM_PORTA] [get_bd_intf_pins mem_out/BRAM_PORTA]
connect_bd_intf_net [get_bd_intf_pins {request.top_module}_0/BRAM_OUT] [get_bd_intf_pins mem_out/BRAM_PORTB]

set smc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect smc]
set_property -dict [list CONFIG.NUM_SI {{1}} CONFIG.NUM_MI {{2}}] $smc
set dw_in [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dwidth_converter dw_in]
set_property -dict [list CONFIG.SI_DATA_WIDTH {{128}} CONFIG.MI_DATA_WIDTH {{64}}] $dw_in
set dw_out [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dwidth_converter dw_out]
set_property -dict [list CONFIG.SI_DATA_WIDTH {{128}} CONFIG.MI_DATA_WIDTH {{64}}] $dw_out
connect_bd_intf_net [get_bd_intf_pins xdma_0/M_AXI] [get_bd_intf_pins smc/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins smc/M00_AXI] [get_bd_intf_pins dw_in/S_AXI]
connect_bd_intf_net [get_bd_intf_pins dw_in/M_AXI] [get_bd_intf_pins bram_ctrl_in/S_AXI]
connect_bd_intf_net [get_bd_intf_pins smc/M01_AXI] [get_bd_intf_pins dw_out/S_AXI]
connect_bd_intf_net [get_bd_intf_pins dw_out/M_AXI] [get_bd_intf_pins bram_ctrl_out/S_AXI]

# the proven shell routes M_AXI_LITE through an interconnect, never directly
# into a register block; smc_lite mirrors that (pipelined, spec-strict
# handshakes between the XDMA AXI-Lite master and the module reference)
set smc_lite [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect smc_lite]
set_property -dict [list CONFIG.NUM_SI {{1}} CONFIG.NUM_MI {{1}}] $smc_lite
connect_bd_intf_net [get_bd_intf_pins xdma_0/M_AXI_LITE] [get_bd_intf_pins smc_lite/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins smc_lite/M00_AXI] [get_bd_intf_pins {request.top_module}_0/S_AXI]

connect_bd_net [get_bd_pins xdma_0/axi_aclk] \\
    [get_bd_pins smc/aclk] [get_bd_pins smc_lite/aclk] [get_bd_pins bram_ctrl_in/s_axi_aclk] [get_bd_pins bram_ctrl_out/s_axi_aclk] \\
    [get_bd_pins dw_in/s_axi_aclk] [get_bd_pins dw_out/s_axi_aclk] \\
    [get_bd_pins {request.top_module}_0/s_axi_aclk]
connect_bd_net [get_bd_pins xdma_0/axi_aresetn] \\
    [get_bd_pins smc/aresetn] [get_bd_pins smc_lite/aresetn] [get_bd_pins bram_ctrl_in/s_axi_aresetn] [get_bd_pins bram_ctrl_out/s_axi_aresetn] \\
    [get_bd_pins dw_in/s_axi_aresetn] [get_bd_pins dw_out/s_axi_aresetn] \\
    [get_bd_pins {request.top_module}_0/s_axi_aresetn]

assign_bd_address -offset 0x{request.input_buffer_address:08X} -range 0x{request.input_bram_bytes:08X} -target_address_space [get_bd_addr_spaces xdma_0/M_AXI] [get_bd_addr_segs bram_ctrl_in/S_AXI/*] -force
assign_bd_address -offset 0x{request.output_buffer_address:08X} -range 0x{request.output_bram_bytes:08X} -target_address_space [get_bd_addr_spaces xdma_0/M_AXI] [get_bd_addr_segs bram_ctrl_out/S_AXI/*] -force
assign_bd_address -offset 0x{request.register_window_offset:08X} -range 0x00001000 -target_address_space [get_bd_addr_spaces xdma_0/M_AXI_LITE] [get_bd_addr_segs {request.top_module}_0/S_AXI/*] -force

"""
    return preamble + staging + _build_postamble_tcl(request)


def write_mm_job_shell_artifacts(request: MmJobShellRequest) -> tuple[Path, ...]:
    """Write the caller-generated sources, project Tcl, constraints, and
    swizzle hook into ``request.output_root``; returns the written paths."""
    request.output_root.mkdir(parents=True, exist_ok=True)
    written = []

    for name, text in request.generated_sources:
        path = request.output_root / name
        path.write_text(text)
        written.append(path)

    for name, text in _shell_script_files(request, constraints=shell_constraints_xdc(request.platform)):
        path = request.output_root / name
        path.write_text(text)
        written.append(path)
    return tuple(written)
