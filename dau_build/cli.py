"""The dau-build CLI: run a task through the ccflow model registry.

Task dispatch loads through the hydra config tree in ``dau_build/config``
(``request_config``/``run_request_config``); this module exposes that
machinery on the command line as the single ``dau-build`` entry point:

    dau-build task=tasks/sim/simulate simulator=simulators/verilator model.profile=<name>
    dau-build task=tasks/build/synthesize spec=specs/identity model.module=m \\
        backend=backends/vivado model.output_root=out
    dau-build --config-dir ./my-configs task=my-custom-task
    dau-build --explain task=tasks/build/synthesize ...   # resolved config, no run

Overrides are hydra overrides: ``group=option`` selects a config group
(``backend=``, ``simulator=``, ``design=``, ``plan=``, ``board=``, ``spec=``,
``step=``) and ``model.field=value`` sets a task field.

Open registration: a ``--config-dir`` overlay (or a package's own
``hydra.lernaplugins`` entry point) can add new task configs and new
``_target_`` models without modifying dau-build.
"""

from __future__ import annotations

import argparse
import sys

from omegaconf import OmegaConf

from dau_build.build_steps import BuildStepError, BuildStepResult


def _parse(argv: list[str] | None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        prog="dau-build",
        description="Run a dau-build task through the ccflow model registry with hydra composition.",
    )
    parser.add_argument("--config-dir", default=None, help="user config overlay directory (open registration)")
    parser.add_argument("--explain", action="store_true", help="print the resolved config instead of running the task")
    parser.add_argument("overrides", nargs="*", help="hydra overrides, e.g. task=... backend=backends/yosys model.output_root=...")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    return args, list(args.overrides)


def main(argv: list[str] | None = None) -> int:
    from ccflow.utils.hydra import cfg_run

    from dau_build.config import compose_config

    args, overrides = _parse(argv)
    result = compose_config(overrides, config_dir=args.config_dir)
    if args.explain:
        print(OmegaConf.to_yaml(result.cfg, resolve=True))
        return 0
    if "model" not in result.cfg:
        raise BuildStepError("no task selected; pass task=<name> (see dau_build/config/task) or a --config-dir overlay")
    outcome = cfg_run(result.cfg)
    if isinstance(outcome, BuildStepResult):
        print(outcome.message)
    elif outcome is not None:
        print(outcome)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
