# dau build

Build tools for dau

[![Build Status](https://github.com/dau-dev/dau-build/actions/workflows/build.yaml/badge.svg?branch=main&event=push)](https://github.com/dau-dev/dau-build/actions/workflows/build.yaml)
[![codecov](https://codecov.io/gh/dau-dev/dau-build/branch/main/graph/badge.svg)](https://codecov.io/gh/dau-dev/dau-build)
[![License](https://img.shields.io/github/license/dau-dev/dau-build)](https://github.com/dau-dev/dau-build)
[![PyPI](https://img.shields.io/pypi/v/dau-build.svg)](https://pypi.python.org/pypi/dau-build)

## Overview

`dau-build` turns declarative FPGA build specs into concrete artifacts: generated SystemVerilog, `artlink.manifest/v0` artifact bundles, backend handoff manifests, Vivado Tcl, and ordered hardware command plans. Every operation is a typed `ccflow.CallableModel` composed from a Hydra config tree in `dau_build/config`, so the whole pipeline composes, inspects, and tests on a development machine, with the privileged steps (Vivado, JTAG, PCIe) gated behind an explicit `execute=true`.

Vivado is the only synthesis backend today; the config plumbing is backend-agnostic so others (e.g. yosys/nextpnr) can be added later.

## Quickstart

Build the checked-in identity example — no board or vendor tools required:

```bash
dau-build inspect  --spec examples/identity/dau-build.yaml
dau-build build    --spec examples/identity/dau-build.yaml --out outputs/identity
dau-build validate --manifest outputs/identity/dau-identity.manifest --root outputs/identity
dau-build task=tasks/sim/simulate module=dau_identity_top spec_path=examples/identity/dau-build.yaml
```

Task and step names are path-style, mirroring the config tree (`task=tasks/sim/simulate`, `step=steps/inspect`).

## Documentation

The documentation is organized into four sections:

- **Tutorial** — [Build the identity example](docs/tutorial/first-build.md).
- **How-to** — [Run a build end to end](docs/how-to/run-a-build.md) · [Program a bitstream on dpv1](docs/how-to/program-hardware.md) · [Extend dau-build](docs/how-to/extend-dau-build.md).
- **Reference** — [Commands](docs/reference/commands.md) · [Config groups](docs/reference/config-groups.md) · [Task and step catalog](docs/reference/tasks-and-steps.md).
- **Explanation** — [Architecture](docs/explanation/architecture.md): how Hydra composition, ccflow evaluation, and the search-path extension model work, and the current state of backend support.

> [!NOTE]
> This library was generated using [copier](https://copier.readthedocs.io/en/stable/) from the [Base Python Project Template repository](https://github.com/python-project-templates/base).
