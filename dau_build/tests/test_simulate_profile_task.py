import pytest

from dau_build.build_steps import BuildStepError, execute_override_task


def test_profile_only_simulate_runs(tmp_path):
    result = execute_override_task(
        [
            "task=simulate",
            "simulator=verilator",
            "profile=dau-int32-record-batch-aggregation",
            f"output_root={tmp_path}",
        ]
    )
    assert "status=passed" in result.message
    assert "profile=dau-int32-record-batch-aggregation" in result.message


def test_simulate_without_spec_or_profile_fails_typed(tmp_path):
    with pytest.raises(BuildStepError, match="requires spec_path and module"):
        execute_override_task(["task=simulate", "simulator=verilator"])
