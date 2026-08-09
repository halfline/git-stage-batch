"""Tests for bounded history-plan tree replay."""

from __future__ import annotations

import gc
import tracemalloc
from contextlib import contextmanager
from dataclasses import replace

import pytest

from git_stage_batch.exceptions import CommandError
from git_stage_batch.history import replay as history_replay
from git_stage_batch.history.replay import (
    materialize_history_output_trees,
    validate_history_plan_materialization,
)
from git_stage_batch.history.scan import acquire_history_plan_document

from .conftest import git


def test_history_replay_delegates_only_resolved_output_materialization(
    linear_history_repo,
):
    document = acquire_history_plan_document(linear_history_repo.base)
    first_output = replace(document.plan.outputs[0], materialization="RESOLVED")
    document = replace(
        document,
        plan=replace(
            document.plan,
            outputs=(first_output, *document.plan.outputs[1:]),
        ),
    )
    calls: list[tuple[int, str]] = []

    def resolve_output(
        callback_document,
        output_index,
        output,
        parent_tree,
        *,
        env,
    ):
        assert callback_document is document
        assert output is first_output
        assert env is None
        calls.append((output_index, parent_tree))
        return document.snapshot.commits[0].tree

    replay = materialize_history_output_trees(
        document,
        resolved_output_materializer=resolve_output,
    )

    assert calls == [(0, document.snapshot.base_tree)]
    assert replay.final_tree == document.snapshot.final_tree


def test_history_replay_rejects_empty_resolved_output(linear_history_repo):
    document = acquire_history_plan_document(linear_history_repo.base)
    first_output = replace(document.plan.outputs[0], materialization="RESOLVED")
    document = replace(
        document,
        plan=replace(
            document.plan,
            outputs=(first_output, *document.plan.outputs[1:]),
        ),
    )

    with pytest.raises(CommandError, match="resolves non-empty units.*empty"):
        materialize_history_output_trees(
            document,
            resolved_output_materializer=(
                lambda _document, _index, _output, parent_tree, **_kwargs: (
                    parent_tree
                )
            ),
        )


@pytest.mark.parametrize(
    ("candidate", "match"),
    [
        ("--not-an-object", "full tree object ID"),
        ("0" * 40, "accessible tree object"),
    ],
)
def test_history_replay_rejects_unvalidated_resolved_tree(
    linear_history_repo,
    candidate,
    match,
):
    document = acquire_history_plan_document(linear_history_repo.base)
    first_output = replace(document.plan.outputs[0], materialization="RESOLVED")
    document = replace(
        document,
        plan=replace(
            document.plan,
            outputs=(first_output, *document.plan.outputs[1:]),
        ),
    )

    with pytest.raises(CommandError, match=match):
        materialize_history_output_trees(
            document,
            resolved_output_materializer=(
                lambda _document, _index, _output, _parent, **_kwargs: candidate
            ),
        )


def test_plan_materialization_ignores_replace_refs_installed_after_validation(
    linear_history_repo,
    monkeypatch,
):
    repo = linear_history_repo
    document = acquire_history_plan_document(repo.base)
    first, second = document.plan.outputs
    reordered = replace(
        document,
        plan=replace(
            document.plan,
            outputs=(
                replace(second, operation="REORDER"),
                replace(first, operation="REORDER"),
            ),
        ),
    )
    source_tree = document.snapshot.commits[0].tree
    replacement_tree = document.snapshot.final_tree
    create_quarantine = history_replay.temporary_git_object_environment

    @contextmanager
    def replace_racing_quarantine(*, disable_replace_objects=False):
        git("replace", source_tree, replacement_tree)
        try:
            with create_quarantine(
                disable_replace_objects=disable_replace_objects
            ) as quarantine:
                assert quarantine.environment()["GIT_NO_REPLACE_OBJECTS"] == "1"
                yield quarantine
        finally:
            git("replace", "-d", source_tree)

    monkeypatch.setattr(
        history_replay,
        "temporary_git_object_environment",
        replace_racing_quarantine,
    )

    result = validate_history_plan_materialization(reordered)

    assert result.final_tree == document.snapshot.final_tree


def test_history_replay_avoids_line_scale_python_heap(tmp_path, monkeypatch):
    """Full source patches remain spill-backed while output trees are built."""
    line = b"history payload " + b"x" * 496 + b"\n"
    heap_peaks: list[int] = []

    for line_count in (4096, 32768):
        repository = tmp_path / f"repo-{line_count}"
        repository.mkdir()
        monkeypatch.chdir(repository)
        git("init", "-q", "-b", "topic")
        git("config", "user.name", "Test User")
        git("config", "user.email", "test@example.com")
        (repository / "anchor.txt").write_text("anchor\n", encoding="utf-8")
        git("add", "anchor.txt")
        git("commit", "-m", "Base")
        base = git("rev-parse", "HEAD")
        large_path = repository / "large.txt"
        large_path.write_bytes(line * line_count)
        git("add", "large.txt")
        git("commit", "-m", "Add large history payload")
        document = acquire_history_plan_document(base)

        gc.collect()
        tracemalloc.start()
        try:
            replay = materialize_history_output_trees(document)
            _current_heap, peak_heap = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        heap_peaks.append(peak_heap)
        assert replay.final_tree == document.snapshot.final_tree

    small_peak, large_peak = heap_peaks
    assert large_peak < small_peak + 64 * 1024
