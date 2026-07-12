# How to run a build end to end

This guide takes a design from a build spec to a validated, ready-to-run Vivado
build. It follows the plan-first flow: everything up to the actual synthesis is
composed and checked on your development machine, and only the `execute=true`
step needs a Vivado host.

The stages are **synthesize → stage → build → validate**, then hand off to
[programming the board](program-hardware.md). Vivado is the only backend today,
so every backend-specific command below is a `vivado` command.

Field lists for each task are in the [task catalog](../reference/tasks-and-steps.md).
To see the full resolved config for any command before running it, prefix it with
`dau-build-cfg-explain`.

## Synthesize the handoff

Generate the DAU top, artifact bundle, and the `vivado/<stem>.manifest` backend
handoff. This writes a manifest at `build_status=planned` and does **not** invoke
Vivado:

```bash
dau-build-cfg task=tasks/build/synthesize \
  spec=specs/identity \
  model.module=dau_identity_top \
  model.output_root=outputs/identity
```

If you keep your spec in a file rather than a config group, drop `spec=` and pass
`model.spec_path=<file>` instead. To attach a specific board and backend to the
resolved config, add `board=boards/dau/dpv1 backend=backends/vivado`.

## Stage the Vivado work directory

Staging copies a read-only Vivado shell seed into a writable work directory and
writes the overlay Tcl, guarded build Tcl, backend manifest, and command plan —
still without running Vivado.

For a full project dry-run that also records checkout roots and the
stage/build/validate command contract, use `stage-vivado-project`:

```bash
dau-build task=tasks/stage/stage-vivado-project \
  source_shell_root=/path/to/vivado-shell-seed \
  work_root=outputs/vivado \
  dau_core_root=/path/to/dau-core \
  dau_driver_root=/path/to/dau-driver \
  artifact_stem=dau-vivado
```

If you only need the overlay artifacts, use `tasks/stage/stage-vivado-overlay`
instead (it requires `work_root` and `dau_core_root`). To fold a DAU artifact
bundle into the overlay, add `dau_artifact_bundle=<bundle.yaml>`. If your Vivado
runs through a wrapper that only accepts a Tcl source path, add
`vivado_invocation=source-only`, and if that wrapper mounts the current directory
in a container, also add `vivado_mount_root=/path/to/dau`.

## Validate before you synthesize

Check that the staged plan is internally consistent — manifest, overlay Tcl,
build Tcl, command plan, and planned output paths all agree — without needing
Xilinx tools:

```bash
dau-build task=tasks/validate/validate-vivado-artifacts \
  work_root=outputs/vivado \
  project_manifest_path=dau-vivado.project
```

Do this on your development machine before moving to the Vivado host. A failure
here is cheap; a failure an hour into synthesis is not.

## Build on the Vivado host

On the machine with Vivado, run the generated overlay/build command and let it
validate the artifact bundle. This is the step that spends real synthesis time,
so it is gated behind `execute=true`:

```bash
dau-build task=tasks/build/build-vivado-artifacts \
  work_root=outputs/vivado \
  artifact_stem=dau-vivado \
  execute=true
```

Once the bitstream, resource report, timing report, and Vivado log exist, this
moves the backend manifest from `build_status=planned` to `built`. Downstream
flashing and smoke-testing require `built`.

Treat the Vivado machine as an ordinary Linux host: SSH in, `rsync` the checkouts
or generated work directory you need, `pip install` the DAU packages there, and
run the same CLIs. dau-build does not wrap host orchestration.

## Next

With a `built` bitstream, continue to
[Program a bitstream on dpv1](program-hardware.md) to program and validate it on
hardware.
