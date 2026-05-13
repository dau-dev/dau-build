# dau build

Build tools for dau

[![Build Status](https://github.com/dau-dev/dau-build/actions/workflows/build.yaml/badge.svg?branch=main&event=push)](https://github.com/dau-dev/dau-build/actions/workflows/build.yaml)
[![codecov](https://codecov.io/gh/dau-dev/dau-build/branch/main/graph/badge.svg)](https://codecov.io/gh/dau-dev/dau-build)
[![License](https://img.shields.io/github/license/dau-dev/dau-build)](https://github.com/dau-dev/dau-build)
[![PyPI](https://img.shields.io/pypi/v/dau-build.svg)](https://pypi.python.org/pypi/dau-build)

## Overview

> [!NOTE]
> This library was generated using [copier](https://copier.readthedocs.io/en/stable/) from the [Base Python Project Template repository](https://github.com/python-project-templates/base).

## NiteFury Command Plans

`dau-nitefury-plan` owns the NiteFury hardware-session command sequence. By default it prints plans without executing privileged commands; pass `--execute` when running directly on the hardware host. Use `--nite-root` as a generated work directory and `--source-nite-root` as the read-only shell seed when a plan needs the current NiteFury Vivado shell.

Useful plans:

```bash
dau-nitefury-plan stage-nitefury-shell \
  --source-nite-root /path/to/nitefury-shell-seed \
  --nite-root outputs/nitefury
dau-nitefury-plan local-build-and-program \
  --source-nite-root /path/to/nitefury-shell-seed \
  --nite-root outputs/nitefury \
  --dau-core-root /path/to/dau-core \
  --dau-driver-root /path/to/dau-driver \
  --dau-utils-root /path/to/dau-utils
dau-nitefury-plan validate-bitstream \
  --nite-root outputs/nitefury \
  --bitstream /path/to/Top_wrapper.bit \
  --dau-core-root /path/to/dau-core \
  --dau-driver-root /path/to/dau-driver \
  --dau-utils-root /path/to/dau-utils
dau-nitefury-plan stage-vivado-overlay \
  --source-nite-root /path/to/nitefury-shell-seed \
  --nite-root outputs/nitefury \
  --dau-core-root /path/to/dau-core \
  --artifact-stem dau-nitefury \
  --backend-platform nitefury \
  --backend-shell nitefury-xdma \
  --operator identity
dau-nitefury-plan stage-nitefury-project \
  --source-nite-root /path/to/nitefury-shell-seed \
  --nite-root outputs/nitefury \
  --dau-core-root /path/to/dau-core \
  --dau-driver-root /path/to/dau-driver \
  --dau-utils-root /path/to/dau-utils \
  --artifact-stem dau-nitefury
dau-nitefury-plan validate-vivado-artifacts \
  --nite-root outputs/nitefury \
  --project-manifest-path dau-nitefury.project
dau-nitefury-plan flash \
  --nite-root outputs/nitefury \
  --dau-utils-root /path/to/dau-utils
dau-nitefury-plan recovery --nite-root outputs/nitefury
dau-nitefury-plan remote-build-and-program \
  --nite-root outputs/nitefury \
  --remote-host root@linux-hw-host \
  --remote-source-nite-root /srv/dau/projects/nite \
  --remote-nite-root /srv/dau/dau-build/outputs/nitefury \
  --remote-dau-core-root /srv/dau/dau-core \
  --remote-dau-driver-root /srv/dau/dau-driver
```

The shell staging path is `stage-nitefury-shell`. It copies a read-only NiteFury shell seed into a generated work directory with `rsync --delete --delete-excluded`, excluding Vivado run/cache/log outputs. This lets `dau-build` mutate and build `outputs/nitefury` while keeping `projects` as evidence/fixtures rather than the normal build workspace.

The hardware-session path that does not invoke Vivado is `validate-bitstream`. It detects the JTAG chain, removes any stale endpoint, programs the selected volatile bitstream, performs the ordered bridge/global PCIe rescan, retries the endpoint check with additional ordered rescans when needed, runs the dependency-free hardware identity smoke through `dau-driver`, and releases runtime PM. The XDMA kernel module must already be loaded; for the NiteFury shell checkout that module lives under `sw/xdma/xdma.ko` in either the read-only seed or generated work directory.

The backend dry-run path is `stage-vivado-overlay`. It can first stage the shell seed into the generated work directory, then writes the generated overlay Tcl, guarded build Tcl, structured backend manifest preview, and Vivado command plan there without invoking Vivado or touching hardware. The manifest is produced from a typed backend request that records the platform, shell, artifact stem, register map version, stream protocol version, operator set, DAU HDL root, final manifest/plan/bitstream paths, and Vivado command settings. Use `validate-vivado-artifacts` immediately after staging to check that the manifest, overlay Tcl, build Tcl, command plan, and final bitstream path agree without requiring Xilinx tools.

The structured project dry-run path is `stage-nitefury-project`. It stages the read-only shell seed into the generated work directory, writes `<artifact-stem>.project` to record the shell seed, work directory, DAU checkout roots, XDMA module path, backend artifacts, Vivado settings, and the high-level stage/build/validate commands, then writes the same backend overlay/build/manifest/plan artifacts as `stage-vivado-overlay`. Pass `--project-manifest-path <artifact-stem>.project` to `validate-vivado-artifacts` to include the project manifest schema, backend cross-references, and generated command contracts in the no-Xilinx validation. This is the first structured project-generation slice; it preserves the currently working Tcl surface.

The `local-build-and-program` plan uses the explicitly named `dau_build.vivado_backend` module. That backend stages a generated `scripts/dau_overlay.tcl` in the generated NiteFury work directory, imports the DAU identity register HDL and AXI-Lite wrapper from `dau-core`, replaces the scratch AXI GPIO identity endpoint, maps the DAU window at `xdma_0/M_AXI_LITE + 0x1000`, regenerates and pins `Top_wrapper` in the generated work directory, resets the DAU module-ref out-of-context synthesis run, writes `dau-nitefury.manifest`, then runs a generated `scripts/dau_build.tcl` that keeps the existing synthesis/implementation flow while guarding stale hard-coded XDMA lane cell paths from the shell seed. Treat this as the current Vivado backend for bring-up, not the final structured DAU build generator.

The `remote-*` plans are convenience wrappers for invoking the same flow over SSH from another workstation. When using `remote-build-and-program`, pass `--remote-source-nite-root` for the read-only shell seed and `--remote-nite-root` for the generated remote work directory. For `remote-xdma-load`, point `--remote-xdma-root` at the actual module directory, for example `/srv/dau/dau-build/outputs/nitefury/sw/xdma`. The primary development workflow remains `local-build-and-program --execute` directly on any Linux hardware host that has Vivado, openFPGALoader, XDMA, and the DAU checkouts.
