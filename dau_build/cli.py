"""ccflow-registry CLI surface for dau-build (the ccflow-etl-style path).

The task dispatch has always loaded through the hydra config tree in
``dau_build/config`` (``request_config``/``run_request_config``); this module
exposes that machinery directly on the command line:

    dau-build-cfg task=tasks/sim/simulate model.simulator=verilator model.profile=<name>
    dau-build-cfg task=tasks/build/synthesize spec=specs/identity model.module=m \\
        model.engine=vivado model.output_root=out
    dau-build-cfg --config-dir ./my-configs task=my-custom-task
    dau-build-cfg-explain task=synthesize ...   # resolved config, no execution

Open registration: a ``--config-dir`` overlay can add new task configs (and
new ``_target_`` models from any importable package) without modifying
dau-build. The legacy ``dau-build task=... key=value`` front-end remains for
the flat roadmap syntax; both converge on the same registry execution.
"""

from __future__ import annotations

import argparse
import sys

from omegaconf import OmegaConf

from dau_build.build_steps import BuildStepError, BuildStepResult


def _parse(argv: list[str] | None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        prog="dau-build-cfg",
        description="Run a dau-build task through the ccflow model registry with hydra composition.",
    )
    parser.add_argument("--config-dir", default=None, help="user config overlay directory (open registration)")
    parser.add_argument("overrides", nargs="*", help="hydra overrides, e.g. task=simulate model.profile=...")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    return args, list(args.overrides)


def main(argv: list[str] | None = None) -> int:
    from ccflow.utils.hydra import cfg_run

    from dau_build.config import compose_config

    args, overrides = _parse(argv)
    result = compose_config(overrides, config_dir=args.config_dir)
    if "model" not in result.cfg:
        raise BuildStepError("no task selected; pass task=<name> (see dau_build/config/task) or a --config-dir overlay")
    outcome = cfg_run(result.cfg)
    if isinstance(outcome, BuildStepResult):
        print(outcome.message)
    elif outcome is not None:
        print(outcome)
    return 0


def explain(argv: list[str] | None = None) -> int:
    from dau_build.config import compose_config

    args, overrides = _parse(argv)
    result = compose_config(overrides, config_dir=args.config_dir)
    print(OmegaConf.to_yaml(result.cfg, resolve=True))
    return 0


def _hydra_run(cfg):
    from ccflow.utils.hydra import cfg_run

    outcome = cfg_run(cfg)
    if isinstance(outcome, BuildStepResult):
        print(outcome.message)
    elif outcome is not None:
        print(outcome)
    return outcome


def run(argv: list[str] | None = None):
    """The single ccflow-etl-style CLI: a Hydra app over ``dau_build/config``
    with the search path active, so packaged and search-path-registered
    config groups compose uniformly:

        dau-build-run task=tasks/... board=boards/dau/dpv1 backend=backends/vivado

    Search-path packages (their own ``hydra.lernaplugins`` entry point) add
    boards/backends/designs/tasks without editing dau-build."""
    import hydra

    return hydra.main(config_path="config", config_name="base", version_base=None)(_hydra_run)()


if __name__ == "__main__":
    raise SystemExit(main())
