# Build the identity example

This tutorial takes a checked-in example design through dau-build: inspect
it, generate its artifacts, validate the result, and run a simulation check.
Everything runs on your machine; no FPGA and no Xilinx tools are involved.
By the end you will have run every stage of the plan-first build flow and
seen what each stage produces.

Work from the root of a `dau-build` checkout. Every command uses the
`examples/identity` design that ships with the package. Each step shows the
command, then an **Output** block with what it prints. The output lines are
tab-separated `label⇥key=value` status lines, so a leading word such as
`dau-build-spec` is a label the task emits, not a command.

## Step 1: inspect the spec

Start by looking at what the example declares:

```bash
dau-build task=tasks/spec/inspect model.spec_path=examples/identity/dau-build.yaml
```

**Output**, a summary line and then the resolved inputs:

```text
dau-build-spec	name=identity-pipeline platform=vivado-xdma shell=xdma-ddr modules=identity sources=1 clock=clk reset=reset backend=vivado
manifest	index=0 path=.../examples/identity/package.artifacts.yaml
source	index=0 path=.../examples/identity/rtl/identity.sv role=hdl-source language=systemverilog origin=...
source	index=1 path=.../examples/identity/python/model.py role=python-source language=python origin=...
metadata	index=0 path=.../examples/identity/constraints/identity.xdc role=constraints format=xdc origin=...
binary	index=0 path=.../examples/identity/bitstreams/seed.bit role=bitstream format=xilinx-bitstream origin=...
```

Each source, constraint and binary is listed with its role and the artifact
bundle it came from. This is the resolved view dau-build hands to a backend.
Nothing has been generated yet.

## Step 2: generate the artifacts

Generate the build outputs into a fresh directory:

```bash
dau-build task=tasks/spec/build model.spec_path=examples/identity/dau-build.yaml model.output_root=outputs/identity
```

**Output**, the two headline artifacts it wrote:

```text
dau-build-artifacts	manifest=outputs/identity/dau-identity.manifest top_sv=outputs/identity/generated/dau_identity_top.sv
```

Look at what landed in the output directory:

```bash
ls outputs/identity
```

**Output:**

```text
dau-identity.artifacts.yaml   dau-identity.manifest   generated
```

You now have the top-level SystemVerilog (`generated/dau_identity_top.sv`),
the DAU manifest, and an `artlink.manifest/v0` artifact bundle. These are
the portable inputs a synthesis backend consumes.

## Step 3: validate the bundle

Check that the generated bundle is consistent: every file the manifest
references exists and every required role is present.

```bash
dau-build task=tasks/spec/validate model.manifest_path=outputs/identity/dau-identity.manifest model.root=outputs/identity
```

**Output:**

```text
dau-build-artifacts-valid	manifest=outputs/identity/dau-identity.manifest top_sv=outputs/identity/generated/dau_identity_top.sv
```

The `-valid` label means the bundle passed. A missing file fails here rather
than partway through a Vivado run.

## Step 4: run a simulation check

Validate the generated top against the spec through the simulation task. The
default simulator, `svparser`, parses and checks the module without an
external simulator:

```bash
dau-build task=tasks/sim/simulate model.module=dau_identity_top model.spec_path=examples/identity/dau-build.yaml
```

**Output:**

```text
dau-build-simulate	task=simulate simulator=svparser module=dau_identity_top spec=examples/identity/dau-build.yaml status=validated
```

`status=validated` means the module checked out against the build spec. As
in the earlier steps, `task=tasks/sim/simulate` selected a task from the
config tree and the `model.module=` and `model.spec_path=` overrides
supplied its fields.

## What you have done

You ran the identity design through the full plan-first flow (inspect,
build, validate, simulate) and saw the artifacts each stage produces,
without a board or a vendor toolchain. Every step had the same shape,
`dau-build task=<path> model.field=value`, because every dau-build operation
is a task selected from the config tree and configured by overrides.

From here:

- To run a real synthesis-and-program sequence, see
  [Run a build end to end](../how-to/run-a-build.md) and
  [Program a bitstream on a board](../how-to/program-hardware.md).
- To see how the config composition works underneath these commands, read
  [the architecture explanation](../explanation/architecture.md).
- For the full set of commands, tasks and config groups, see the
  [reference](../reference/commands.md).
