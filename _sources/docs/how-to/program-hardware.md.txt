# How to program a bitstream on dpv1

This guide programs and validates a bitstream on a dpv1 board (a NiteFury
XC7A200T reached over a PCIe/Thunderbolt link) using the `hardware-plan` task. It
covers the three situations you hit most: programming a fresh build, validating
an already-built bitstream, and recovering a device that a bad image has wedged.

`hardware-plan` composes an ordered sequence of hardware-session steps — JTAG
detect, endpoint remove, program, PCIe rescan, endpoint check, optional injected
smoke command — and runs them in the order the board actually needs. Like the build tasks it is
plan-first: without `execute=true` it prints the plan; with it, it runs on the
host. Run these on the machine physically attached to the board.

The named plans are `local-build-and-program`, `build-and-program`,
`validate-bitstream`, `recovery`, `flash`, `thunderbolt-hold`, and
`thunderbolt-release`. Full field lists are in the
[task catalog](../reference/tasks-and-steps.md).

Host access (the endpoint PCI identity, bridge BDFs, runtime-PM patterns, and
JTAG cable) is board/host configuration, not code defaults: compose
`platform=platforms/<vendor>/<board>` so the plan takes the board's
`host_access` facts (or set the `model.<field>=` overrides explicitly). Steps
that need an unset fact refuse to render.

> **Programming can wedge the PCIe link.** Removing and reprogramming an endpoint
> while the host holds it can hang a rescan hard enough to need a power cycle. On
> a host where that is a risk, arm a watchdog / auto-reboot before you run any
> `execute=true` plan, so a wedge recovers itself.

## Preview a plan before running it

Always look at the sequence first. Omit `execute=true` and the task prints the
ordered steps without touching the board:

The plan is a config group (`plan=plans/<name>`); its own fields are
`plan.<field>=` overrides and the shared toolchain fields are `model.<field>=`:

```bash
dau-build task=tasks/hardware/hardware-plan \
  platform=platforms/dau/dpv1 \
  plan=plans/local-build-and-program \
  plan.source_shell_root=/path/to/vivado-shell-seed \
  plan.dau_core_root=/path/to/dau-core \
  plan.dau_utils_root=/path/to/dau-utils \
  model.work_root=outputs/vivado
```

Read the printed steps. When they look right, re-run the same command with
`model.execute=true` appended.

## Program a fresh build

`local-build-and-program` stages the shell, runs the Vivado overlay build, then
programs and verifies the device. Add `execute=true` to run it on the host:

```bash
dau-build task=tasks/hardware/hardware-plan \
  platform=platforms/dau/dpv1 \
  plan=plans/local-build-and-program \
  plan.source_shell_root=/path/to/vivado-shell-seed \
  plan.dau_core_root=/path/to/dau-core \
  plan.dau_utils_root=/path/to/dau-utils \
  model.work_root=outputs/vivado \
  model.execute=true
```

The plan holds runtime power management, writes the overlay/build Tcl, runs the
Vivado build, detects the JTAG chain, removes the stale endpoint, programs the
volatile bitstream, performs the ordered bridge-then-global PCIe rescan, retries
the endpoint check, runs the injected smoke command (if one is configured), and
finally releases power management. If any step fails, the release step still
runs.

The smoke step is injectable: `plan.smoke_command=<command>` runs after the
endpoint check, and the plan omits the step when no command is configured.
dau-build itself ships no smoke payload — the DAU driver smoke (which asserts
the DAU magic register and prints `DAU_SMOKE_OK`) is injected by the private
`dau` package's config overlay.

If you already have a bitstream and only want to program it, use
`plan=plans/build-and-program` with `model.bitstream=<path>` instead — it skips
staging and the Vivado build.

## Validate an already-built bitstream

To program a specific bitstream and run the endpoint check without building
anything, use `validate-bitstream`. The driver smoke is injected, not built in:
pass `plan.smoke_command=<cmd>` (or use a package-provided plan that injects
one, e.g. the private dau package's `plans/dpv1-validate-bitstream`); with no
command the plan ends at the endpoint check:

```bash
dau-build task=tasks/hardware/hardware-plan \
  platform=platforms/dau/dpv1 \
  plan=plans/validate-bitstream \
  plan.dau_utils_root=/path/to/dau-utils \
  model.work_root=outputs/vivado \
  model.bitstream=/path/to/Top_wrapper.bit \
  model.execute=true
```

The XDMA kernel module must already be loaded; for a Vivado shell checkout it
lives at `sw/xdma/xdma.ko` in the seed or work directory.

## Recover a wedged device

If a bad image has left the endpoint dead, do **not** rescan first — a rescan
against a resident dead image is what hangs. The `recovery` plan does the steps in
the safe order: hold power management, remove the endpoint via sysfs, program a
known-good volatile bitstream, then rescan and re-check:

```bash
dau-build task=tasks/hardware/hardware-plan \
  platform=platforms/dau/dpv1 \
  plan=plans/recovery \
  model.work_root=outputs/vivado \
  model.execute=true
```

This recovers the link without a reboot in most cases. If the endpoint still does
not reappear, the device needs a power cycle.

## Flash and smoke-test from a manifest

To flash and to run a smoke test against a `built` shell-build manifest, use the
`flash` and `smoke-test` tasks (these consume the manifest and verify the
bitstream digest before touching the board):

```bash
dau-build task=tasks/flash/flash model.manifest_path=outputs/vivado/shell-build.artifacts.yaml
dau-build task=tasks/flash/smoke-test model.test=identity
```

Both require `build_status=built` when given a manifest, and both emit a safe plan
unless run against real hardware. The smoke `test` is one of `identity`,
`dma-loopback`, or `aggregation`.
