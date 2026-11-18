# How to program a bitstream on a board

This guide programs and validates a bitstream on an attached board with the
`hardware-plan` task. The plans run on any board whose platform definition
records its host-access facts; the examples use a PCIe card behind a bridge,
the topology the plans were developed on. The guide covers the three
situations you hit most: programming a fresh build, validating an
already-built bitstream, and recovering a device that a bad image has hung.

`hardware-plan` composes an ordered sequence of hardware-session steps (JTAG
detect, endpoint remove, program, PCIe rescan, endpoint check, an optional
injected smoke command) and runs them in the order the board needs. Like the
build tasks it is plan-first: without `execute=true` it prints the plan; with
it, it runs on the host. Run these on the machine physically attached to the
board.

The named plans are `local-build-and-program`, `build-and-program`,
`validate-bitstream`, `recovery`, `sram-program`, `spi-flash`, `flash`,
`thunderbolt-hold` and `thunderbolt-release`. Full field lists are in the
[task catalog](../reference/tasks-and-steps.md).

Host access (the endpoint PCI identity, bridge BDFs, runtime-PM patterns and
JTAG cable) is host configuration, not a code default and not a board fact. A
board option describes the card; the host that holds the card gets its own
platform option that extends the board option with `host_access` and the
trained link (`host_link.expected_link_width`, `expected_link_speed_gts`), kept
wherever that host's facts live and composed with `--config-dir`. The packaged
example board (`platforms/example/probe`) is a fiction whose every hardware
value is a placeholder, so it previews plans and refuses `execute=true`; the
commands below name your own board. Compose
`platform=platforms/<vendor>/<board>-<host>` so the plan takes that host's
`host_access` facts; override one for a single run with
`platform.host_access.<field>=`. A step that needs an unset fact refuses to
render.

> **Programming can hang the PCIe link.** Removing and reprogramming an
> endpoint while the host holds it can hang a rescan hard enough to need a
> power cycle. `sram-program` arms the forced-reboot deadman over that window
> and disarms it only on success; prefer it for any reprogram, and on a host
> where a hang is a risk arm the deadman yourself before any other
> `execute=true` plan.

## Preview a plan before running it

Look at the sequence first. Without `execute=true` the task prints the
ordered steps and does not touch the board.

The plan is a config group (`plan=plans/<name>`); its own fields are
`plan.<field>=` overrides and the shared toolchain fields are `model.<field>=`:

```bash
dau-build task=tasks/hardware/hardware-plan \
  platform=platforms/<vendor>/<board> \
  plan=plans/local-build-and-program \
  plan.source_shell_root=/path/to/vivado-shell-seed \
  plan.dau_core_root=/path/to/dau-core \
  plan.dau_utils_root=/path/to/dau-utils \
  model.work_root=outputs/vivado
```

Read the printed steps. When they look right, run the same command with
`model.execute=true` appended.

## Program a fresh build

`local-build-and-program` stages the shell, runs the Vivado overlay build,
then programs and verifies the device. Add `execute=true` to run it on the
host:

```bash
dau-build task=tasks/hardware/hardware-plan \
  platform=platforms/<vendor>/<board> \
  plan=plans/local-build-and-program \
  plan.source_shell_root=/path/to/vivado-shell-seed \
  plan.dau_core_root=/path/to/dau-core \
  plan.dau_utils_root=/path/to/dau-utils \
  model.work_root=outputs/vivado \
  model.execute=true
```

The plan holds runtime power management, writes the overlay and build Tcl,
runs the Vivado build, detects the JTAG chain, removes the stale endpoint,
programs the volatile bitstream, rescans PCIe (bridge first, then global),
retries the endpoint check, runs the injected smoke command if one is
configured, and releases power management. If a step fails, the release
step still runs.

The smoke step is injectable: `plan.smoke_command=<command>` runs after the
endpoint check, and the plan omits the step when no command is configured.
dau-build ships no smoke payload of its own; the DAU driver smoke, which
asserts the DAU magic register and prints `DAU_SMOKE_OK`, comes from the
`dau` package's config overlay.

If the shell is already built and you only want to program it, use
`validate-bitstream` with the build's manifest (below). A plan that programs
an existing image takes it from a built manifest, never from a bare path:
the task checks the manifest records `build_status=built`, verifies the
bitstream's digest and programs a snapshot of the verified bytes under
`<work_root>/flash/`. Only `plan=plans/recovery` accepts `model.bitstream=<path>`,
because the image it loads is the board's fallback, not a build under test.

## Validate an already-built bitstream

To program a specific bitstream and run the endpoint check without building
anything, use `validate-bitstream`. The driver smoke is injected, not built
in: pass `plan.smoke_command=<cmd>`, or use a package-provided plan that
injects one (the `dau` package's `plans/dpv1-validate-bitstream`, for
example). With no command the plan ends at the endpoint check:

```bash
dau-build task=tasks/hardware/hardware-plan \
  platform=platforms/<vendor>/<board> \
  plan=plans/validate-bitstream \
  plan.dau_utils_root=/path/to/dau-utils \
  model.work_root=outputs/vivado \
  model.manifest=outputs/shell/shell-build.artifacts.yaml \
  model.execute=true
```

The XDMA kernel module must already be loaded. For a Vivado shell checkout
it is at `sw/xdma/xdma.ko` in the seed or work directory.

## Flash a board that boots from SPI flash

A board whose platform sets `spi_boot_buswidth` loads its design from SPI
flash at power-on. Its shell build writes a configuration-memory image
(`dau_mm_job.mcs`) beside the bitstream and records it in the manifest with
role `cfgmem`. The `spi-flash` plan writes that image and power-cycles the
host so the board loads it:

```bash
dau-build task=tasks/hardware/hardware-plan \
  platform=platforms/<vendor>/<board>-<host> \
  plan=plans/spi-flash \
  model.work_root=outputs/flash \
  model.manifest=outputs/shell/shell-build.artifacts.yaml \
  model.execute=true
```

The plan arms the deadman, removes the endpoint, writes the flash over JTAG
through openFPGALoader's SPI bridge, then sets an RTC alarm
(`plan.wake_after_s`, 60 by default) and powers the host off. The host option
names the bridge bitstream for the board's part as `host_access.spi_bridge_bitstream`
(openFPGALoader installs them as `spiOverJtag_<part>.bit.gz`). The host must
wake from an RTC alarm, and the steps that write sysfs or power the host off
run behind `host_access.privilege_prefix`.

The plan cannot check the board, because the host is off when the plan ends.
Once the host is back, check that the board reports the BUILD_ID of the
manifest you flashed. If a step fails, the deadman stays armed and reboots
the host; the flash may be partly written, and running the plan again
rewrites it.

## Recover a hung device

If a bad image has left the endpoint dead, do not rescan first: a rescan
against a resident dead image is what hangs. The `recovery` plan does the
steps in the safe order: hold power management, remove the endpoint through
sysfs, program a known-good volatile bitstream, then rescan and re-check:

```bash
dau-build task=tasks/hardware/hardware-plan \
  platform=platforms/<vendor>/<board> \
  plan=plans/recovery \
  model.work_root=outputs/vivado \
  model.execute=true
```

This recovers the link without a reboot in most cases. If the endpoint still
does not reappear, the device needs a power cycle.
