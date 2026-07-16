"""Tests for the maintained matching and attribution benchmark."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


SCRIPT_PATH = Path(__file__).parents[3] / "scripts" / "benchmark_matching.py"
SPEC = importlib.util.spec_from_file_location("benchmark_matching", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
benchmark_matching = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark_matching
SPEC.loader.exec_module(benchmark_matching)


def test_seeded_fixtures_are_deterministic_and_cover_edge_cases():
    """Generated fixtures should be repeatable and retain adversarial shapes."""
    repeated = benchmark_matching.build_matching_fixture(
        "repeated-lines", 100, 42
    )
    repeated_again = benchmark_matching.build_matching_fixture(
        "repeated-lines", 100, 42
    )
    low_similarity = benchmark_matching.build_matching_fixture(
        "low-similarity", 100, 42
    )
    binary = benchmark_matching.build_matching_fixture("binary-exclusion", 2, 42)
    large = benchmark_matching.build_matching_fixture("large-file", 5_000, 42)
    seeded_edit = benchmark_matching.build_matching_fixture(
        "small-interactive", 80, 42
    )
    differently_seeded_edit = benchmark_matching.build_matching_fixture(
        "small-interactive", 80, 43
    )
    repeated_source_lines = b"".join(repeated.source_chunks).splitlines(
        keepends=True
    )
    low_source_lines = b"".join(low_similarity.source_chunks).splitlines(
        keepends=True
    )
    low_target_lines = b"".join(low_similarity.target_chunks).splitlines(
        keepends=True
    )

    assert repeated == repeated_again
    assert repeated_source_lines.count(b"same\n") > 90
    assert set(low_source_lines) & set(low_target_lines) == {low_source_lines[50]}
    assert b"\0" in b"".join(binary.source_chunks)
    assert large.source_line_count == 5_000
    assert len(large.source_chunks) > 1
    assert max(map(len, large.source_chunks)) <= (
        benchmark_matching.FIXTURE_CHUNK_SIZE
    )
    assert seeded_edit.target_chunks != differently_seeded_edit.target_chunks


def test_smallest_case_smokes_public_apis_and_stable_schema():
    """The benchmark should remain importable and runnable after API changes."""
    report = benchmark_matching.run_suite(
        "quick",
        case_names=["small-interactive"],
        seed=42,
        warmups=0,
        repeats=1,
    )

    assert report["schema_version"] == 1
    assert report["suite"] == "matching-and-attribution"
    assert report["metadata"]["seed"] == 42
    assert isinstance(report["metadata"]["working_tree_dirty"], bool)
    case = report["cases"][0]
    assert case["name"] == "small-interactive"
    assert case["setup"]["measured"] is False
    assert set(case["phases"]) == {
        "buffer_loading",
        "mapping",
        "per_hunk_mapping",
        "reused_file_mapping",
        "unit_attribution",
    }
    for phase in case["phases"].values():
        assert set(phase) == {
            "seconds",
            "tracemalloc_peak_bytes",
            "git_subprocesses",
            "parent_peak_rss_bytes",
            "child_peak_rss_bytes",
            "samples",
            "result",
        }
        assert len(phase["samples"]["seconds"]) == 1
        assert len(phase["samples"]["tracemalloc_peak_bytes"]) == 1
        assert len(phase["samples"]["git_subprocesses"]) == 1
        assert len(phase["samples"]["parent_peak_rss_bytes"]) == 1
        assert phase["samples"]["child_peak_rss_bytes"] == [0]


@pytest.mark.parametrize(
    ("case_name", "expected_mapped_lines"),
    (
        ("repeated-lines", 99),
        ("low-similarity", 1),
        ("reversed-unique", 1),
    ),
)
def test_adversarial_matching_cases_run(case_name, expected_mapped_lines):
    """Ambiguous and low-similarity inputs should traverse the real matcher."""
    case = benchmark_matching.CASE_BY_NAME[case_name]
    result = benchmark_matching.run_matching_case(
        case,
        100,
        42,
        warmups=0,
        repeats=1,
    )

    assert result["phases"]["mapping"]["result"]["mapped_lines"] == (
        expected_mapped_lines
    )


def test_attribution_case_exercises_many_claims_and_deduplicates_work():
    """The end-to-end case should cover Git IO, matching, and claim ownership."""
    report = benchmark_matching.run_suite(
        "quick",
        case_names=["many-batches"],
        warmups=0,
        repeats=1,
    )

    case = report["cases"][0]
    phases = case["phases"]
    claim_result = phases["claim_attribution"]["result"]
    metrics = claim_result["metrics"]
    assert case["setup"]["measured"] is False
    assert set(phases) == {
        "git_object_resolution",
        "blob_loading",
        "mapping",
        "unit_attribution",
        "claim_attribution",
    }
    assert metrics["claimed_batches"] == 50
    assert metrics["object_requests"] == 1
    assert metrics["mapping_computations"] == 1
    assert claim_result["owner_links"] == 100
    assert phases["unit_attribution"]["result"]["units"] == 2
    assert phases["git_object_resolution"]["result"] == {
        "requests": 51,
        "resolved": 51,
        "unique_object_ids": 1,
    }


def test_file_workload_covers_supported_file_and_hunk_dimensions():
    report = benchmark_matching.run_suite(
        "quick",
        case_names=["small-interactive"],
        warmups=0,
        repeats=1,
        file_count=2,
        hunks_per_file=8,
        matching_size=20,
    )

    phases = report["cases"][0]["phases"]
    assert phases["per_hunk_mapping"]["result"]["mapping_computations"] == 16
    assert phases["reused_file_mapping"]["result"]["mapping_computations"] == 2
    assert report["cases"][0]["dimensions"]["files"] == 2
    assert report["cases"][0]["dimensions"]["hunks_per_file"] == 8


def test_attribution_workload_accepts_zero_batches():
    report = benchmark_matching.run_suite(
        "quick",
        case_names=["many-batches"],
        warmups=0,
        repeats=1,
        batch_count=0,
    )

    case = report["cases"][0]
    assert case["dimensions"]["batches"] == 0
    assert case["phases"]["claim_attribution"]["result"]["owner_links"] == 0
    assert (
        case["phases"]["claim_attribution"]["result"]["metrics"][
            "candidate_batches"
        ]
        == 0
    )


def test_binary_case_is_explicitly_excluded_from_text_matching():
    """Binary fixtures should document exclusion and run no matching phases."""
    report = benchmark_matching.run_suite(
        "quick",
        case_names=["binary-exclusion"],
        warmups=0,
        repeats=1,
    )

    case = report["cases"][0]
    assert case["status"] == "excluded"
    assert case["dimensions"]["contains_nul"] is True
    assert case["phases"] == {}


def test_phase_preparation_and_cleanup_are_outside_the_timer(monkeypatch):
    """Setup stays untimed and allocation tracing uses a separate run."""
    events = []
    ticks = iter((10.0, 10.25))
    tracing = False

    def prepare():
        events.append("prepare")
        return "state"

    def operation(state):
        assert state == "state"
        events.append("operation")
        return {"answer": 42}

    def cleanup(state):
        assert state == "state"
        events.append("cleanup")

    def perf_counter():
        events.append("clock")
        return next(ticks)

    def start_tracing():
        nonlocal tracing
        assert not tracing
        tracing = True
        events.append("trace-start")

    def read_tracing():
        assert tracing
        events.append("trace-read")
        return 0, 123

    def stop_tracing():
        nonlocal tracing
        assert tracing
        tracing = False
        events.append("trace-stop")

    monkeypatch.setattr(benchmark_matching.time, "perf_counter", perf_counter)
    monkeypatch.setattr(benchmark_matching.tracemalloc, "start", start_tracing)
    monkeypatch.setattr(
        benchmark_matching.tracemalloc,
        "get_traced_memory",
        read_tracing,
    )
    monkeypatch.setattr(benchmark_matching.tracemalloc, "stop", stop_tracing)
    monkeypatch.setattr(
        benchmark_matching.tracemalloc,
        "is_tracing",
        lambda: tracing,
    )
    phase = benchmark_matching._measure_phase(
        prepare,
        operation,
        cleanup,
        warmups=0,
        repeats=1,
    )

    assert events == [
        "prepare",
        "clock",
        "operation",
        "clock",
        "cleanup",
        "prepare",
        "trace-start",
        "operation",
        "trace-read",
        "trace-stop",
        "cleanup",
    ]
    assert phase["seconds"]["median"] == pytest.approx(0.25)
    assert phase["samples"]["seconds"] == [pytest.approx(0.25)]
    assert phase["samples"]["tracemalloc_peak_bytes"] == [123]


def test_report_comparison_flags_time_and_memory_regressions():
    """Comparison should align phase measurements rather than sample ordering."""
    def report(seconds, memory, *, dimensions=None, python_version="3.10.0"):
        return {
            "schema_version": 1,
            "suite": "matching-and-attribution",
            "mode": "quick",
            "metadata": {
                "seed": 42,
                "warmups": 1,
                "repeats": 3,
                "memory_repeats": 3,
                "clock": "time.perf_counter",
                "timing_metric": "perf_counter without tracemalloc",
                "memory_metric": "tracemalloc peak Python allocations",
                "python_version": python_version,
                "python_implementation": "CPython",
                "git_version": "git version 2.0",
                "platform": "test-platform",
                "working_tree_dirty": False,
            },
            "cases": [
                {
                    "name": "case",
                    "dimensions": dimensions or {"source_lines": 10},
                    "phases": {
                        "mapping": {
                            "seconds": {"median": seconds},
                            "tracemalloc_peak_bytes": {"median": memory},
                        }
                    },
                }
            ],
        }

    comparison = benchmark_matching.compare_reports(
        report(1.0, 1_000),
        report(1.25, 1_250),
        threshold_percent=20.0,
    )

    assert comparison["regressions"] == 2
    assert {item["metric"] for item in comparison["measurements"]} == {
        "seconds",
        "tracemalloc_peak_bytes",
    }
    assert all(
        item["change_percent"] == pytest.approx(25.0)
        for item in comparison["measurements"]
    )
    assert comparison["environment_warnings"] == []

    environment_mismatch = benchmark_matching.compare_reports(
        report(1.0, 1_000),
        report(1.0, 1_000, python_version="3.13.0"),
    )
    assert environment_mismatch["environment_warnings"] == [
        {
            "field": "python_version",
            "before": "3.10.0",
            "after": "3.13.0",
        }
    ]

    with pytest.raises(ValueError, match="different input dimensions"):
        benchmark_matching.compare_reports(
            report(1.0, 1_000),
            report(1.0, 1_000, dimensions={"source_lines": 20}),
        )

    with pytest.raises(ValueError, match="finite and non-negative"):
        benchmark_matching.compare_reports(
            report(1.0, 1_000),
            report(1.0, 1_000),
            threshold_percent=float("nan"),
        )
