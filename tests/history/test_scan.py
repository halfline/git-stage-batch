"""Tests for immutable history snapshots."""

from __future__ import annotations

import hashlib
import gc
import tracemalloc
from contextlib import contextmanager

import pytest

from git_stage_batch.exceptions import CommandError
from git_stage_batch.history import dependencies, ranges, safety
from git_stage_batch.history.records import history_plan_document_record
from git_stage_batch.history.scan import acquire_history_plan_document

from .conftest import git


def test_scan_captures_exact_commit_chain_and_keep_template(linear_history_repo):
    repo = linear_history_repo

    document = acquire_history_plan_document(repo.base)

    snapshot = document.snapshot
    assert document.schema_version == 4
    assert snapshot.base_commit == repo.base
    assert snapshot.tip_commit == repo.tip
    assert snapshot.branch_ref == "refs/heads/topic"
    assert [commit.commit_id for commit in snapshot.commits] == [
        repo.first,
        repo.tip,
    ]
    assert [commit.parent for commit in snapshot.commits] == [
        repo.base,
        repo.first,
    ]
    assert snapshot.commits[0].tree == snapshot.commits[1].parent_tree
    assert snapshot.final_tree == snapshot.commits[-1].tree
    assert [output.operation for output in document.plan.outputs] == [
        "KEEP",
        "KEEP",
    ]
    assert [output.materialization for output in document.plan.outputs] == [
        "EXACT",
        "EXACT",
    ]
    assert document.plan.partitioned_units == ()
    assert [output.source_commits for output in document.plan.outputs] == [
        (repo.first,),
        (repo.tip,),
    ]
    assert all(
        output.source_unit_ids == tuple(unit.unit_id for unit in commit.units)
        for output, commit in zip(
            document.plan.outputs,
            snapshot.commits,
            strict=True,
        )
    )
    safety_record = history_plan_document_record(document)["safety"]
    assert safety_record["active_rewrite_operation"] is None
    assert "active_history_operation" not in safety_record
    assert document.safety.mutation_ready is True


def test_scan_preserves_an_empty_commit_as_a_unitless_output(linear_history_repo):
    repo = linear_history_repo
    git("commit", "--allow-empty", "-m", "Empty marker")
    empty_commit = git("rev-parse", "HEAD")

    document = acquire_history_plan_document(repo.base)

    commit = document.snapshot.commits[-1]
    output = document.plan.outputs[-1]
    assert commit.commit_id == empty_commit
    assert commit.parent == repo.tip
    assert commit.parent_tree == commit.tree
    assert commit.units == ()
    assert output.operation == "KEEP"
    assert output.materialization == "EXACT"
    assert output.source_commits == (empty_commit,)
    assert output.source_unit_ids == ()


def test_scan_reports_detached_head_as_a_mutation_blocker(linear_history_repo):
    repo = linear_history_repo
    git("checkout", "--detach", repo.tip)

    document = acquire_history_plan_document(repo.base)

    assert document.snapshot.tip_commit == repo.tip
    assert document.snapshot.branch_ref is None
    assert "detached-head" in document.safety.blockers
    assert document.safety.mutation_ready is False


def test_scan_records_messages_identities_and_tree_pairs(linear_history_repo):
    document = acquire_history_plan_document(linear_history_repo.base)
    commit = document.snapshot.commits[0]

    assert commit.message == "Change alpha\n"
    assert commit.message_sha256 == hashlib.sha256(
        commit.message.encode("utf-8")
    ).hexdigest()
    assert commit.author.name == "Test User"
    assert commit.author.email == "test@example.com"
    assert commit.author.raw.endswith(commit.author.timezone)
    assert commit.parent_tree != commit.tree
    assert commit.units[0].source_commit == commit.commit_id
    assert len(commit.units[0].unit_id) == 64
    assert len(commit.units[0].patch_id) == 64


def test_scan_records_compact_per_unit_dependency_evidence(linear_history_repo):
    document = acquire_history_plan_document(linear_history_repo.base)
    dependencies = document.snapshot.dependencies
    record = history_plan_document_record(document)

    assert [dependency.original_position for dependency in dependencies] == [0, 1]
    assert [dependency.earliest_position for dependency in dependencies] == [0, 0]
    assert all(dependency.barrier is None for dependency in dependencies)
    assert record["snapshot"]["dependency_graph"] == {
        "algorithm_version": 1,
        "units": [
            {
                "unit_id": dependency.unit_id,
                "original_position": dependency.original_position,
                "earliest_position": dependency.earliest_position,
                "barrier_unit_id": dependency.barrier_unit_id,
                "barrier": dependency.barrier,
                "detail": dependency.detail,
            }
            for dependency in dependencies
        ],
    }


def test_scan_decodes_a_declared_commit_message_encoding(linear_history_repo):
    repo = linear_history_repo
    tree = git("rev-parse", "HEAD^{tree}")
    payload = (
        f"tree {tree}\n"
        f"parent {repo.tip}\n"
        "author Test User <test@example.com> 1700000000 +0000\n"
        "committer Test User <test@example.com> 1700000000 +0000\n"
        "encoding ISO-8859-1\n\n"
    ).encode("ascii") + b"caf\xe9\n"
    encoded_commit = git(
        "hash-object",
        "-t",
        "commit",
        "-w",
        "--stdin",
        input_bytes=payload,
    )
    git("update-ref", "refs/heads/topic", encoded_commit, repo.tip)

    document = acquire_history_plan_document(repo.tip)

    commit = document.snapshot.commits[0]
    output = document.plan.outputs[0]
    assert commit.message == "café\n"
    assert commit.encoding == "ISO-8859-1"
    assert output.message == commit.message
    assert output.encoding == commit.encoding


def test_scan_snapshot_contains_metadata_not_patch_lines(linear_history_repo):
    record = history_plan_document_record(
        acquire_history_plan_document(linear_history_repo.base)
    )
    serialized = repr(record)

    assert "alpha topic" not in serialized
    assert record["snapshot"]["commits"][0]["patch"]["old_tree"]
    assert record["snapshot"]["commits"][0]["patch"]["new_tree"]


def test_scan_keeps_siblings_when_a_file_changes_type(linear_history_repo):
    repo = linear_history_repo
    sibling = repo.root / "sibling.txt"
    sibling.write_text("before\n", encoding="utf-8")
    git("add", "sibling.txt")
    git("commit", "-m", "Add transition inputs")
    transition_base = git("rev-parse", "HEAD")

    repo.source.unlink()
    repo.source.symlink_to("sibling.txt")
    sibling.write_text("after\n", encoding="utf-8")
    git("add", "example.txt", "sibling.txt")
    git("commit", "-m", "Change type beside text")

    document = acquire_history_plan_document(transition_base)
    units = document.snapshot.commits[0].units

    assert any(unit.kind == "file-type" for unit in units)
    assert any(
        unit.path == "example.txt"
        and unit.unsupported_reason == "file-type-with-content"
        for unit in units
    )
    assert any(
        unit.path == "sibling.txt" and unit.kind == "text-replacement"
        for unit in units
    )


def test_scan_records_signature_identity_without_embedding_payload(
    linear_history_repo,
):
    repo = linear_history_repo
    tree = git("rev-parse", "HEAD^{tree}")
    signature = b"-----BEGIN PGP SIGNATURE-----\nfake\n-----END PGP SIGNATURE-----"
    payload = (
        f"tree {tree}\n"
        f"parent {repo.tip}\n"
        "author Test User <test@example.com> 1700000000 +0000\n"
        "committer Test User <test@example.com> 1700000000 +0000\n"
        "gpgsig "
    ).encode("ascii") + signature.replace(b"\n", b"\n ") + b"\n\nSigned\n"
    signed_commit = git(
        "hash-object",
        "-t",
        "commit",
        "-w",
        "--stdin",
        input_bytes=payload,
    )
    git("update-ref", "refs/heads/topic", signed_commit, repo.tip)

    document = acquire_history_plan_document(repo.tip)
    commit = document.snapshot.commits[0]
    record = history_plan_document_record(document)

    assert commit.signatures[0].header == "gpgsig"
    assert len(commit.signatures[0].sha256) == 64
    assert record["snapshot"]["rewritten_signatures_preserved"] is False
    assert "BEGIN PGP SIGNATURE" not in repr(record)


def test_scan_unit_ids_ignore_repository_diff_display_configuration(
    linear_history_repo,
):
    repo = linear_history_repo
    unicode_path = repo.root / "café.txt"
    unicode_path.write_text("one\n", encoding="utf-8")
    git("add", "café.txt")
    git("commit", "-m", "Add unicode path")
    base = git("rev-parse", "HEAD")
    unicode_path.write_text("two\n", encoding="utf-8")
    git("commit", "-am", "Change unicode path")

    git("config", "core.quotePath", "false")
    first_ids = [
        unit.unit_id
        for unit in acquire_history_plan_document(base).snapshot.commits[0].units
    ]
    order_file = repo.root / "order"
    order_file.write_text("example.txt\n", encoding="utf-8")
    git("config", "diff.orderFile", str(order_file))
    git("config", "diff.algorithm", "histogram")
    git("config", "diff.indentHeuristic", "true")
    git("config", "diff.interHunkContext", "99")
    git("config", "core.quotePath", "true")
    second_ids = [
        unit.unit_id
        for unit in acquire_history_plan_document(base).snapshot.commits[0].units
    ]

    assert second_ids == first_ids


def test_scan_does_not_write_the_real_index_tree(linear_history_repo, monkeypatch):
    original_run_git_command = safety.run_git_command

    def reject_write_tree(arguments, *args, **kwargs):
        assert arguments[0] != "write-tree"
        return original_run_git_command(arguments, *args, **kwargs)

    monkeypatch.setattr(safety, "run_git_command", reject_write_tree)
    linear_history_repo.source.write_text("staged audit\n", encoding="utf-8")
    git("add", "example.txt")

    document = acquire_history_plan_document(linear_history_repo.base)

    assert document.safety.index_tree is None
    assert document.safety.index_clean is False
    assert "staged-index" in document.safety.blockers


def test_scan_does_not_retain_dependency_candidate_trees(linear_history_repo):
    before = git("count-objects", "-v")

    acquire_history_plan_document(linear_history_repo.base)

    assert git("count-objects", "-v") == before


def test_dependency_analysis_ignores_replace_refs_installed_after_snapshot(
    linear_history_repo,
    monkeypatch,
):
    repo = linear_history_repo
    baseline = acquire_history_plan_document(repo.base)
    source_tree = git("rev-parse", f"{repo.first}^{{tree}}")
    replacement_tree = git("rev-parse", f"{repo.tip}^{{tree}}")
    create_quarantine = dependencies.temporary_git_object_environment

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
        dependencies,
        "temporary_git_object_environment",
        replace_racing_quarantine,
    )

    raced = acquire_history_plan_document(repo.base)

    assert raced.snapshot.commits == baseline.snapshot.commits
    assert raced.snapshot.dependencies == baseline.snapshot.dependencies


def test_scan_ignores_replace_ref_installed_after_upfront_check(
    linear_history_repo,
    monkeypatch,
):
    repo = linear_history_repo
    baseline = acquire_history_plan_document(repo.base)
    require_unmodified_object_graph = ranges._require_unmodified_object_graph
    installed = False

    def install_replace_after_check():
        nonlocal installed
        require_unmodified_object_graph()
        git("replace", repo.first, repo.tip)
        installed = True

    monkeypatch.setattr(
        ranges,
        "_require_unmodified_object_graph",
        install_replace_after_check,
    )
    try:
        raced = acquire_history_plan_document(repo.base)
    finally:
        if installed:
            git("replace", "-d", repo.first)

    assert installed
    assert raced == baseline


def test_scan_ignores_legacy_graft_changed_after_upfront_check(
    linear_history_repo,
    monkeypatch,
):
    repo = linear_history_repo
    grafts = repo.root / git("rev-parse", "--git-path", "info/grafts")
    grafts.parent.mkdir(parents=True, exist_ok=True)
    grafts.touch()
    baseline = acquire_history_plan_document(repo.base)
    require_unmodified_object_graph = ranges._require_unmodified_object_graph
    changed = False

    def change_graft_after_check():
        nonlocal changed
        require_unmodified_object_graph()
        grafts.write_text(f"{repo.tip} {repo.base}\n", encoding="ascii")
        changed = True

    monkeypatch.setattr(
        ranges,
        "_require_unmodified_object_graph",
        change_graft_after_check,
    )
    try:
        raced = acquire_history_plan_document(repo.base)
    finally:
        grafts.unlink(missing_ok=True)

    assert changed
    assert raced == baseline


def test_scan_dependency_analysis_avoids_line_scale_python_heap(
    tmp_path,
    monkeypatch,
):
    line = b"history dependency payload " + b"x" * 480 + b"\n"
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
        (repository / "large.txt").write_bytes(line * line_count)
        git("add", "large.txt")
        git("commit", "-m", "Add large dependency payload")

        gc.collect()
        tracemalloc.start()
        try:
            document = acquire_history_plan_document(base)
            _current_heap, peak_heap = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        heap_peaks.append(peak_heap)
        assert len(document.snapshot.dependencies) == 1

    small_peak, large_peak = heap_peaks
    assert large_peak < small_peak + 64 * 1024


def test_scan_groups_different_path_dependency_replay(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    git("init", "-q", "-b", "topic")
    git("config", "user.name", "Test User")
    git("config", "user.email", "test@example.com")
    path_count = 12
    for index in range(path_count):
        (tmp_path / f"path-{index}.txt").write_text("", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "Base")
    base = git("rev-parse", "HEAD")
    for index in range(path_count):
        (tmp_path / f"path-{index}.txt").write_text(
            f"value {index}\n",
            encoding="utf-8",
        )
        git("commit", "-am", f"Change path {index}")

    real_apply = dependencies.apply_history_replay_unit
    application_count = 0

    def count_application(*args, **kwargs):
        nonlocal application_count
        application_count += 1
        return real_apply(*args, **kwargs)

    monkeypatch.setattr(
        dependencies,
        "apply_history_replay_unit",
        count_application,
    )

    document = acquire_history_plan_document(base)

    assert all(
        dependency.earliest_position == 0
        for dependency in document.snapshot.dependencies
    )
    assert application_count < path_count * 3


def test_scan_reports_publication_of_an_older_range_commit(
    linear_history_repo,
):
    repo = linear_history_repo
    git("update-ref", "refs/remotes/origin/review", repo.first)

    document = acquire_history_plan_document(repo.base)

    first, tip = document.safety.remote_containment
    assert first.commit_id == repo.first
    assert first.remote_refs == ("refs/remotes/origin/review",)
    assert tip.commit_id == repo.tip
    assert tip.remote_refs == ()
    assert document.safety.remote_refs_containing_tip == ()
    assert "published-range" in document.safety.blockers


def test_scan_rejects_merge_topology(linear_history_repo):
    repo = linear_history_repo
    git("checkout", "-b", "side", repo.first)
    (repo.root / "side.txt").write_text("side\n", encoding="utf-8")
    git("add", "side.txt")
    git("commit", "-m", "Side")
    git("checkout", "topic")
    git("merge", "--no-ff", "side", "-m", "Merge side")

    with pytest.raises(CommandError, match="requires a linear range"):
        acquire_history_plan_document(repo.base)


def test_scan_rejects_active_replace_objects(linear_history_repo):
    repo = linear_history_repo
    git("replace", repo.first, repo.tip)

    with pytest.raises(CommandError, match="refuses replace objects"):
        acquire_history_plan_document(repo.base)


def test_scan_rejects_legacy_grafts(linear_history_repo):
    repo = linear_history_repo
    grafts = repo.root / git("rev-parse", "--git-path", "info/grafts")
    grafts.parent.mkdir(parents=True, exist_ok=True)
    grafts.write_text(f"{repo.tip} {repo.base}\n", encoding="ascii")

    with pytest.raises(CommandError, match="legacy grafts"):
        acquire_history_plan_document(repo.base)


def test_scan_rejects_configured_legacy_graft_file(
    linear_history_repo,
    monkeypatch,
):
    repo = linear_history_repo
    grafts = repo.root / "custom-grafts"
    grafts.write_text(f"{repo.tip} {repo.base}\n", encoding="ascii")
    monkeypatch.setenv("GIT_GRAFT_FILE", str(grafts))

    with pytest.raises(CommandError, match="legacy grafts"):
        acquire_history_plan_document(repo.base)
