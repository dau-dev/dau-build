# How to program a bitstream on dpv1

This guide programs and validates a bitstream on a dpv1 board (a NiteFury
XC7A200T reached over a PCIe/Thunderbolt link) using the `hardware-plan` task. It
covers the three situations you hit most: programming a fresh build, validating
an already-built bitstream, and recovering a device that a bad image has wedged.

`hardware-plan` composes an ordered sequence of hardware-session steps — JTAG
detect, endpoint remove, program, PCIe rescan, endpoint check, driver smoke — and
runs them in the order the board actually needs. Like the build tasks it is
plan-first: without `execute=true` it prints the plan; with it, it runs on the
host. Run these on the machine physically attached to the board.

The named plans are `local-build-and-program`, `build-and-program`,
`validate-bitstream`, `recovery`, `flash`, `thunderbolt-hold`, and
`thunderbolt-release`. Full field lists are in the
[task catalog](../reference/tasks-and-steps.md#tasks).

> **Programming can wedge the PCIe link.** Removing and reprogramming an endpoint
> while the host holds it can hang a rescan hard enough to need a power cycle. On
> a host where that is a risk, arm a watchdog / auto-reboot before you run any
> `execute=true` plan, so a wedge recovers itself.

## Preview a plan before running it

Always look at the sequence first. Omit `execute=true` and the task prints the
ordered steps without touching the board:

```bash
dau-build task=tasks/hardware/hardware-plan \
  plan=local-build-and-program \
  source_shell_root=/path/to/vivado-shell-seed \
  work_root=outputs/vivado \
  dau_core_root=/path/to/dau-core \
  dau_driver_root=/path/to/dau-driver \
  dau_utils_root=/path/to/dau-utils
```

Read the printed steps. When they look right, re-run the same command with
`execute=true` appended.

## Program a fresh build

`local-build-and-program` stages the shell, runs the Vivado overlay build, then
programs and verifies the device. Add `execute=true` to run it on the host:

```bash
dau-build task=tasks/hardware/hardware-plan \
  plan=local-build-and-program \
  source_shell_root=/path/to/vivado-shell-seed \
  work_root=outputs/vivado \
  dau_core_root=/path/to/dau-core \
  dau_driver_root=/path/to/dau-driver \
  dau_utils_root=/path/to/dau-utils \
  execute=true
```

The plan holds runtime power management, writes the overlay/build Tcl, runs the
Vivado build, detects the JTAG chain, removes the stale endpoint, programs the
volatile bitstream, performs the ordered bridge-then-global PCIe rescan, retries
the endpoint check, runs the dependency-free driver smoke (which asserts the DAU
magic register and prints `DAU_SMOKE_OK`), and finally releases power management.
If any step fails, the release step still runs.

If you already have a bitstream and only want to program it, use
`build-and-program` with `bitstream=<path>` instead — it skips staging and the
Vivado build.

## Validate an already-built bitstream

To program a specific bitstream and run the full endpoint-and-smoke check without
building anything, use `validate-bitstream`:

```bash
dau-build task=tasks/hardware/hardware-plan \
  plan=validate-bitstream \
  work_root=outputs/vivado \
  bitstream=/path/to/Top_wrapper.bit \
  dau_core_root=/path/to/dau-core \
  dau_driver_root=/path/to/dau-driver \
  dau_utils_root=/path/to/dau-utils \
  execute=true
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
  plan=recovery \
  work_root=outputs/vivado \
  execute=true
```

This recovers the link without a reboot in most cases. If the endpoint still does
not reappear, the device needs a power cycle.

## Flash and smoke-test from a manifest

To flash and to run a smoke test against a `built` shell-build manifest, use the
`flash` and `smoke-test` tasks (these consume the manifest and verify the
bitstream digest before touching the board):

```bash
dau-build task=tasks/flash/flash manifest_path=outputs/vivado/shell-build.artifacts.yaml
dau-build task=tasks/flash/smoke-test test=identity
```

Both require `build_status=built` when given a manifest, and both emit a safe plan
unless run against real hardware. The smoke `test` is one of `identity`,
`dma-loopback`, or `aggregation`.
