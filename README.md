# dau build

Build tools for dau

[![Build Status](https://github.com/dau-dev/dau-build/actions/workflows/build.yaml/badge.svg?branch=main&event=push)](https://github.com/dau-dev/dau-build/actions/workflows/build.yaml)
[![codecov](https://codecov.io/gh/dau-dev/dau-build/branch/main/graph/badge.svg)](https://codecov.io/gh/dau-dev/dau-build)
[![License](https://img.shields.io/github/license/dau-dev/dau-build)](https://github.com/dau-dev/dau-build)

## Overview

`dau-build` turns a declarative FPGA build spec into the files a build needs: generated SystemVerilog, `artlink.manifest/v0` artifact bundles, backend handoff manifests, Vivado Tcl, and ordered hardware command plans. The operations are typed `ccflow.CallableModel`s composed from the Hydra config tree in `dau_build/config`. The whole pipeline composes, inspects and tests on a development machine; the steps that need Vivado, a JTAG cable or a PCIe bus run only when you pass `execute=true`.

Two synthesis engines are supported: Vivado for the FPGA bitstream flow, and yosys for open-source synthesis that runs in CI. The config plumbing does not depend on either, so more can be added.

Other packages extend dau-build by composition rather than by editing it. A package registers its own config tree on the shared Hydra search path through a `hydra.lernaplugins` entry point, and its task, design and board groups then compose through this CLI without dau-build importing the package. The core registry arrives the same way: `dau-core` publishes each hardware building block as a `CoreDefinition` at `/dau-core/<name>`, and the `synthesize-cores` task resolves cores from that registry to characterize them one at a time, out of context. Without a synthesis host it stages the Tcl and a command plan; with `model.execute=true` on a machine that has Vivado it runs the synthesis and reports any drift from the resource and timing envelope the registry records:

```bash
dau-build task=tasks/build/synthesize-cores   'model.cores=[/dau-core/int32-predicate-filter]'   model.output_root=outputs/ooc model.part=xc7a200tfbg484-2
```

## Installation

Python 3.11 or later. There is no package index release yet; install from a clone or straight from GitHub:

```bash
pip install git+https://github.com/dau-dev/dau-build.git
```

For development, clone and run `make develop`. Vivado and yosys are needed only for the steps that run them; everything else composes, generates and tests without them.

## Quickstart

Build the checked-in identity example. No board and no vendor tools are needed:

```bash
dau-build task=tasks/spec/inspect  model.spec_path=examples/identity/dau-build.yaml
dau-build task=tasks/spec/build     model.spec_path=examples/identity/dau-build.yaml model.output_root=outputs/identity
dau-build task=tasks/spec/validate  model.manifest_path=outputs/identity/dau-identity.manifest model.root=outputs/identity
dau-build task=tasks/sim/simulate   model.module=dau_identity_top model.spec_path=examples/identity/dau-build.yaml
```

Task names are paths into the config tree (`task=tasks/spec/inspect`).

## Documentation

- **Tutorial**: [Build the identity example](docs/tutorial/first-build.md).
- **How-to**: [Run a build end to end](docs/how-to/run-a-build.md) · [Program a bitstream on a board](docs/how-to/program-hardware.md) · [Extend dau-build](docs/how-to/extend-dau-build.md).
- **Reference**: [Commands](docs/reference/commands.md) · [Config groups](docs/reference/config-groups.md) · [Task and step catalog](docs/reference/tasks-and-steps.md).
- **Explanation**: [Architecture](docs/explanation/architecture.md), on Hydra composition, ccflow evaluation, the search-path extension model, and the state of backend support.
