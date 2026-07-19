"""Tests for the maintained status transport benchmark."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


SCRIPT_PATH = (
    Path(__file__).parents[2] / "scripts" / "benchmark_status_transport.py"
)
SPEC = importlib.util.spec_from_file_location(
    "benchmark_status_transport",
    SCRIPT_PATH,
)
assert SPEC is not None
assert SPEC.loader is not None
benchmark_status_transport = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark_status_transport
SPEC.loader.exec_module(benchmark_status_transport)


def test_status_transport_benchmark_smoke_profile():
    report = benchmark_status_transport.run_suite(
        case_names=("small-one",),
        requested_jobs=("1",),
        warmups=0,
        repeats=1,
        memory_repeats=1,
    )

    assert report["schema_version"] == 1
    assert report["benchmark"] == "status-file-job-transport"
    assert len(report["cases"]) == 1
    execution = report["cases"][0]["executions"][0]
    assert execution["transport"] == "inline"
    assert execution["worker_count"] == 1
    assert execution["job_count"] == 1
    assert execution["workspace_cleaned"] is True
    assert execution["leaked_processes"] == []
    assert execution["git_subprocess_count"] > 0
    assert execution["input_artifact_bytes"] > 0
    assert execution["peak_artifact_bytes"] >= execution[
        "input_artifact_bytes"
    ]
    assert execution["summary"]["wall_seconds"]["median"] > 0
    assert execution["summary"]["tracemalloc_peak_bytes"]["maximum"] > 0
