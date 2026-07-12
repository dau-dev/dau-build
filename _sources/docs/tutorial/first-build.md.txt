# Build the identity example

In this tutorial we will take a checked-in example design through dau-build:
inspect it, generate its artifacts, validate the result, and run a simulation
check — all on your machine, with no FPGA and no Xilinx tools installed. By the
end you will have run every stage of the plan-first build flow and seen what each
one produces.

Work from the root of a `dau-build` checkout. Every command here uses the
`examples/identity` design that ships with the package.

## Step 1 — Inspect the spec

First, look at what the example declares. Run:

```bash
dau-build inspect --spec examples/identity/dau-build.yaml
```

You should see a summary line followed by the resolved inputs:

```text
dau-build-spec	name=identity-pipeline platform=vivado-xdma shell=xdma-ddr modules=identity sources=1 clock=clk reset=reset backend=vivado
manifest	index=0 path=.../examples/identity/package.artifacts.yaml
source	index=0 path=.../examples/identity/rtl/identity.sv role=hdl-source language=systemverilog origin=...
source	index=1 path=.../examples/identity/python/model.py role=python-source language=python origin=...
metadata	index=0 path=.../examples/identity/constraints/identity.xdc role=constraints format=xdc origin=...
binary	index=0 path=.../examples/identity/bitstreams/seed.bit role=bitstream format=xilinx-bitstream origin=...
```

Notice that each source, constraint, and binary is listed with its role and the
artifact bundle it came from. This is the resolved view dau-build hands to a
backend — nothing has been generated yet.

## Step 2 — Generate the artifacts

Now generate the build outputs into a fresh directory:

```bash
dau-build build --spec examples/identity/dau-build.yaml --out outputs/identity
```

The command prints the two headline artifacts it wrote:

```text
dau-build-artifacts	manifest=outputs/identity/dau-identity.manifest top_sv=outputs/identity/generated/dau_identity_top.sv
```

Look at what landed in the output directory:

```bash
ls outputs/identity
```

```text
dau-identity.artifacts.yaml   dau-identity.manifest   generated
```

You have generated the top-level SystemVerilog (`generated/dau_identity_top.sv`),
the DAU manifest, and an `artlink.manifest/v0` artifact bundle. These are the
portable inputs a synthesis backend consumes.

## Step 3 — Validate the bundle

Check that the generated bundle is internally consistent — that every file the
manifest references exists and every required role is present:

```bash
dau-build validate --manifest outputs/identity/dau-identity.manifest --root outputs/identity
```

```text
dau-build-artifacts-valid	manifest=outputs/identity/dau-identity.manifest top_sv=outputs/identity/generated/dau_identity_top.sv
```

The `-valid` prefix confirms the bundle passed. If a referenced file were missing,
validation would fail here rather than deep inside a Vivado run later.

## Step 4 — Run a simulation check

Finally, validate the generated top against the spec through the simulation task.
The default simulator, `svparser`, parses and checks the module without needing
any external simulator:

```bash
dau-build task=tasks/sim/simulate module=dau_identity_top spec_path=examples/identity/dau-build.yaml
```

```text
dau-build-simulate	task=simulate simulator=svparser module=dau_identity_top spec=examples/identity/dau-build.yaml status=validated
```

`status=validated` means the module checked out against the build spec. This is
your first task run — `task=tasks/sim/simulate` selected the simulate task from
the config tree, and the `module=` and `spec_path=` overrides supplied its fields.

## What you have done

You have run the identity design through the full plan-first flow: **inspect →
build → validate → simulate**, and seen the artifacts each stage produces — all
without a board or a vendor toolchain. Every dau-build task and step works this
same way: select it from the config tree and override its fields.

From here:

- To drive a real synthesis-and-program sequence, see
  [Run a build end to end](../how-to/run-a-build.md) and
  [Program a bitstream on dpv1](../how-to/program-hardware.md).
- To understand how the config composition works underneath these commands, read
  [the architecture explanation](../explanation/architecture.md).
- For the full set of commands, tasks, and config groups, see the
  [reference](../reference/commands.md).
