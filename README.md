# dau build

Build tools for dau

[![Build Status](https://github.com/dau-dev/dau-build/actions/workflows/build.yaml/badge.svg?branch=main&event=push)](https://github.com/dau-dev/dau-build/actions/workflows/build.yaml)
[![codecov](https://codecov.io/gh/dau-dev/dau-build/branch/main/graph/badge.svg)](https://codecov.io/gh/dau-dev/dau-build)
[![License](https://img.shields.io/github/license/dau-dev/dau-build)](https://github.com/dau-dev/dau-build)
[![PyPI](https://img.shields.io/pypi/v/dau-build.svg)](https://pypi.python.org/pypi/dau-build)

## Overview

`dau-build` consumes declarative YAML build specs and YAML artifact bundles. Artifact bundles are provided by `artlink` and use the `artlink.manifest/v0` schema: they can carry HDL/SystemVerilog sources, Python reference sources, constraints, bitstreams, and other metadata/binary assets. HDL module ownership is expressed with `provides: {kind: hdl-module, name: ...}` capabilities. The reusable bundle layer validates required roles, source languages, duplicate module providers, missing files, and HDL availability before a build spec reaches backend tooling.

Use `dau-build inspect --spec <spec.yaml>` to see the resolved package inputs. When a spec references `artifact_manifests`, inspect prints each source, metadata file, and binary asset with its originating manifest so backend handoff issues can be debugged before invoking a toolchain.

The checked-in identity example shows the current portable handoff shape:

```bash
dau-build inspect --spec examples/identity/dau-build.yaml
dau-build build --spec examples/identity/dau-build.yaml --out outputs/identity
dau-build validate --manifest outputs/identity/dau-identity.manifest --root outputs/identity
```

The public callable task surface is available directly through `dau-build`. It accepts Hydra-style `key=value` overrides and dispatches typed `ccflow.CallableModel` requests for simulation, synthesis handoff, flash planning, and smoke-test planning:

```bash
dau-build task=simulate simulator=cocotb module=dau_identity_top spec_path=examples/identity/dau-build.yaml
dau-build task=simulate simulator=verilator module=dau_int32_aggregation_tile spec_path=examples/identity/dau-build.yaml profile=dau-int32-aggregation-tile output_root=outputs/sim
dau-build task=synthesize engine=vivado module=dau_identity_top spec_path=examples/identity/dau-build.yaml output_root=outputs/identity
dau-build task=flash manifest_path=outputs/identity/vivado/dau-identity.manifest
dau-build task=smoke-test test=aggregation manifest_path=outputs/identity/vivado/dau-identity.manifest
dau-build task=flash tool=openFPGAloader bitstream=outputs/vivado/project.runs/impl_1/Top_wrapper.bit
dau-build task=smoke-test test=identity
```

The `simulate` task validates the selected module against the DAU build spec for `simulator=cocotb` and `simulator=svparser`. Pass `simulator=verilator` with either a named DAU-owned `profile` or raw `testbench_path=...` and `top_module=...`, plus an optional `expect_stdout=...` marker, to compile and run a Verilator testbench through the generic `dau-sim` Verilator adapter.

The `synthesize` task writes the local DAU generated top, DAU manifest, `artlink.manifest/v0` artifact bundle, and `vivado/<artifact-stem>.manifest` backend handoff. The backend manifest records the selected module, generated top, HDL source set, command plan, expected bitstream path, register/status contract, staging buffers, and operator metadata. It still does not invoke Vivado directly. `task=flash` can consume that manifest after the bitstream exists, and `task=smoke-test test=aggregation` can consume the same manifest to plan the register/DMA-facing aggregation smoke. The `flash` and `smoke-test` tasks currently produce safe plans and validation output rather than touching hardware by default.

For lower-level development, `dau-build-steps step=...` exposes artifact operations such as `inspect`, `validate`, `generate`, `write`, `resolved-config`, and `explain`. Those operations are also `ccflow.CallableModel`s; user-facing workflows should use `dau-build task=...`.

Currently available DAU Verilator profiles are owned by `dau-build` and reference DAU HDL benches from `dau-core`:

- `dau-int32-aggregation-tile`
- `dau-int32-arrow-lite-stream-aggregation`
- `dau-int32-stream-aggregation`

`examples/identity/package.artifacts.yaml` is the reusable input package. `examples/identity/generated/dau-identity.artifacts.yaml` is a portable example of the generated artifact bundle; its paths are relative to the example directory rather than to a developer workstation.

## Vivado Command Plans

`dau-build task=hardware-plan` owns the Vivado hardware-session command sequence. By default it prints plans without executing privileged commands; pass `execute=true` when running directly on the hardware host. Use `work_root=...` as a generated work directory and `source_shell_root=...` as the read-only shell seed when a plan needs the current Vivado shell.

Useful plans:

```bash
dau-build task=hardware-plan plan=stage-shell \
  source_shell_root=/path/to/vivado-shell-seed \
  work_root=outputs/vivado
dau-build task=hardware-plan plan=local-build-and-program \
  source_shell_root=/path/to/vivado-shell-seed \
  work_root=outputs/vivado \
  dau_core_root=/path/to/dau-core \
  dau_driver_root=/path/to/dau-driver \
  dau_utils_root=/path/to/dau-utils
dau-build task=hardware-plan plan=validate-bitstream \
  work_root=outputs/vivado \
  bitstream=/path/to/Top_wrapper.bit \
  dau_core_root=/path/to/dau-core \
  dau_driver_root=/path/to/dau-driver \
  dau_utils_root=/path/to/dau-utils
dau-build task=hardware-plan plan=stage-vivado-overlay \
  source_shell_root=/path/to/vivado-shell-seed \
  work_root=outputs/vivado \
  dau_core_root=/path/to/dau-core \
  dau_artifact_bundle=outputs/dau-identity/dau-identity.artifacts.yaml \
  artifact_stem=dau-vivado \
  backend_platform=vivado-xdma \
  backend_shell=xdma-shell \
  operator=identity
dau-build task=hardware-plan plan=stage-vivado-project \
  source_shell_root=/path/to/vivado-shell-seed \
  work_root=outputs/vivado \
  dau_core_root=/path/to/dau-core \
  dau_driver_root=/path/to/dau-driver \
  dau_utils_root=/path/to/dau-utils \
  artifact_stem=dau-vivado
dau-build task=hardware-plan plan=validate-vivado-artifacts \
  work_root=outputs/vivado \
  project_manifest_path=dau-vivado.project
dau-build task=hardware-plan plan=flash \
  work_root=outputs/vivado \
  dau_utils_root=/path/to/dau-utils
dau-build task=hardware-plan plan=recovery work_root=outputs/vivado
```

## Hardware Host Workflow

Treat Vivado-capable machines as normal Linux hardware hosts. From your development machine, SSH to the host, rsync the package checkouts or generated artifacts you need, install the DAU packages with pip on that host, and run the same local CLIs there. The `dau-build` CLI roadmap is focused on typed build/config steps, not host orchestration wrappers.

```bash
ssh root@primary-linux-host "mkdir -p /srv/dau"
rsync -a --delete /path/to/dau-core/ root@primary-linux-host:/srv/dau/dau-core/
rsync -a --delete /path/to/dau-driver/ root@primary-linux-host:/srv/dau/dau-driver/
rsync -a --delete /path/to/dau-build/ root@primary-linux-host:/srv/dau/dau-build/
ssh root@primary-linux-host "cd /srv/dau/dau-build && python -m pip install -e ../dau-core -e ../dau-driver -e ."
ssh root@primary-linux-host "cd /srv/dau/dau-build && dau-build inspect --spec examples/identity/dau-build.yaml"
```

The shell staging path is `stage-shell`. It copies a read-only Vivado shell seed into a generated work directory with `rsync --delete --delete-excluded`, excluding Vivado run/cache/log outputs. This lets `dau-build` mutate and build `outputs/vivado` while keeping the seed checkout as evidence/fixtures rather than the normal build workspace.

The hardware-session path that does not invoke Vivado is `validate-bitstream`. It detects the JTAG chain, removes any stale endpoint, programs the selected volatile bitstream, performs the ordered bridge/global PCIe rescan, retries the endpoint check with additional ordered rescans when needed, runs the dependency-free hardware identity smoke through `dau-driver`, and releases runtime PM. The XDMA kernel module must already be loaded; for the Vivado shell checkout that module lives under `sw/xdma/xdma.ko` in either the read-only seed or generated work directory.

The backend dry-run path is `stage-vivado-overlay`. It can first stage the shell seed into the generated work directory, then writes the generated overlay Tcl, guarded build Tcl, structured backend manifest preview, and Vivado command plan there without invoking Vivado or touching hardware. The manifest is produced from a typed backend request that records the platform, shell, artifact stem, register map version, stream protocol version, operator set, DAU HDL root, final manifest/plan/bitstream paths, and Vivado command settings. Use `validate-vivado-artifacts` immediately after staging to check that the manifest, overlay Tcl, build Tcl, command plan, and final bitstream path agree without requiring Xilinx tools.

For DAU-native backend handoff, pass `dau_artifact_bundle=...` to `plan=stage-vivado-overlay` or `plan=stage-vivado-project`. The Vivado backend loads and validates the YAML bundle, records the generated top and HDL source set in the backend manifest, and adds those HDL sources to the generated overlay Tcl before the shell-specific bridge is applied. This is the first handoff step from DAU build artifacts into the Vivado/XDMA adapter.

For a local wrapper that already runs `vivado -mode batch -source`, pass `vivado_invocation=source-only` so generated command plans invoke the wrapper with only a Tcl source path. If that wrapper launches Vivado in a container that mounts the current directory, also pass `vivado_mount_root=/path/to/dau`; the backend will emit small driver Tcl files, launch the wrapper from the mounted root, and render DAU HDL and bundle source paths relative to the generated work directory.

The structured project dry-run path is `plan=stage-vivado-project`. It stages the read-only shell seed into the generated work directory, writes `<artifact-stem>.project` to record the shell seed, work directory, DAU checkout roots, XDMA module path, backend artifacts, Vivado settings, and the high-level stage/build/validate commands, then writes the same backend overlay/build/manifest/plan artifacts as `plan=stage-vivado-overlay`. Pass `project_manifest_path=<artifact-stem>.project` to `plan=validate-vivado-artifacts` to include the project manifest schema, backend cross-references, and generated command contracts in the no-Xilinx validation. This is the first structured project-generation slice; it preserves the currently working Tcl surface.

The `local-build-and-program` plan uses the explicitly named `dau_build.vivado_backend` module. That backend stages a generated `scripts/dau_overlay.tcl` in the generated Vivado work directory, imports the DAU identity register HDL and AXI-Lite wrapper from `dau-core`, replaces the scratch AXI GPIO identity endpoint, maps the DAU window at `xdma_0/M_AXI_LITE + 0x1000`, regenerates and pins `Top_wrapper` in the generated work directory, resets the DAU module-ref out-of-context synthesis run, writes `dau-vivado.manifest`, then runs a generated `scripts/dau_build.tcl` that keeps the existing synthesis/implementation flow while guarding stale hard-coded XDMA lane cell paths from the shell seed. Treat this as the current Vivado backend for bring-up, not the final structured DAU build generator.

> [!NOTE]
> This library was generated using [copier](https://copier.readthedocs.io/en/stable/) from the [Base Python Project Template repository](https://github.com/python-project-templates/base).
