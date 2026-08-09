"""Tests for recoverable object-based history execution."""

from __future__ import annotations

import copy
import json
import os
import shutil
import stat
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from git_stage_batch.exceptions import CommandError
from git_stage_batch.history import (
    execution,
    plan_files,
    resolution_files,
    state as history_state,
)
from git_stage_batch.history.commit_writer import (
    create_history_commit,
    require_history_commit_matches,
)
from git_stage_batch.history.execution import (
    abort_history_operation,
    continue_history_operation,
    start_history_operation,
    verify_history_operation,
)
from git_stage_batch.history.models import HistoryPhase
from git_stage_batch.history.commit_objects import parse_commit_object
from git_stage_batch.history.json_files import (
    history_json_sha256,
    write_history_json_file,
)
from git_stage_batch.history.records import history_plan_document_record
from git_stage_batch.history.resolution_files import (
    PrivateFilePublicationError,
    PrivateFilePublicationOutcome,
)
from git_stage_batch.history.scan import acquire_history_plan_document
from git_stage_batch.history.state import (
    active_history_operation_id,
    history_operation_directory,
    history_operation_plan_path,
    history_operation_preparation_directory,
    history_recovery_ref,
    latest_history_operation_id,
    load_active_history_operation,
    load_latest_history_operation,
    write_history_verification_record,
)
from git_stage_batch.output.rewrite_operation import print_rewrite_operation

from .conftest import git


def _write_plan(repo, mutation=None):
    plan = history_plan_document_record(acquire_history_plan_document(repo.base))
    if mutation is not None:
        mutation(plan)
    path = repo.root / "history-plan.json"
    path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    return path, plan


def _reword_first(plan):
    output = plan["plan"]["outputs"][0]
    output["operation"] = "REWORD"
    output["message"] = "Explain alpha precisely\n"


def _integrate_all(plan):
    first, second = plan["plan"]["outputs"]
    first["operation"] = "INTEGRATE"
    first["source_commits"].extend(second["source_commits"])
    first["source_unit_ids"].extend(second["source_unit_ids"])
    plan["plan"]["outputs"] = [first]


def _integrate_first_and_last(plan):
    first, middle, repair = plan["plan"]["outputs"]
    first["operation"] = "INTEGRATE"
    first["source_commits"].extend(repair["source_commits"])
    first["source_unit_ids"].extend(repair["source_unit_ids"])
    plan["plan"]["outputs"] = [first, middle]


def _legacy_v3_plan_record(plan):
    legacy = copy.deepcopy(plan)
    legacy["schema_version"] = 3
    legacy["plan"].pop("partitioned_units")
    for output in legacy["plan"]["outputs"]:
        output.pop("materialization")
        output["unit_ids"] = output.pop("source_unit_ids")
    return legacy


def _use_operation_id(monkeypatch, operation_id):
    monkeypatch.setattr(
        execution.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex=operation_id),
    )


def _resolved_ref(refname):
    return git("rev-parse", "--verify", "--quiet", refname, check=False)


def _add_text_equivalent_encoded_commits(repo):
    prefix = (
        f"tree {git('rev-parse', 'HEAD^{tree}')}\n"
        f"parent {repo.tip}\n"
        "author Test User <test@example.com> 1700000000 +0000\n"
        "committer Test User <test@example.com> 1700000000 +0000\n"
        "encoding ISO-2022-JP\n\n"
    ).encode("ascii")
    source_commit = git(
        "hash-object",
        "-t",
        "commit",
        "-w",
        "--stdin",
        input_bytes=prefix + b"hello\n",
    )
    replacement_commit = git(
        "hash-object",
        "-t",
        "commit",
        "-w",
        "--stdin",
        input_bytes=prefix + b"\x1b(Bhello\n",
    )
    git("update-ref", "refs/heads/topic", source_commit, repo.tip)
    repo.base = repo.tip
    return source_commit, replacement_commit


@pytest.mark.parametrize(
    "failed_artifact",
    ["preparation", "plan.json", "state.json"],
)
def test_start_does_not_expose_an_incomplete_preparation(
    linear_history_repo,
    monkeypatch,
    failed_artifact,
):
    repo = linear_history_repo
    path, _plan = _write_plan(repo)
    operation_id = "1" * 32
    _use_operation_id(monkeypatch, operation_id)

    if failed_artifact == "preparation":

        def fail_preparation(_path):
            raise RuntimeError("injected preparation failure")

        monkeypatch.setattr(
            history_state,
            "create_private_resolution_directory",
            fail_preparation,
        )
    else:
        original_write = history_state.write_history_json_file

        def fail_selected_write(destination, value, **kwargs):
            if destination.name == failed_artifact:
                raise RuntimeError(f"injected {failed_artifact} failure")
            return original_write(destination, value, **kwargs)

        monkeypatch.setattr(
            history_state,
            "write_history_json_file",
            fail_selected_write,
        )

    with pytest.raises(RuntimeError, match="injected"):
        start_history_operation(str(path))

    assert active_history_operation_id() is None
    assert _resolved_ref(history_recovery_ref(operation_id)) == ""
    assert not history_operation_directory(operation_id).exists()


def test_start_does_not_replace_an_operation_directory_collision(
    linear_history_repo,
    monkeypatch,
):
    repo = linear_history_repo
    path, _plan = _write_plan(repo)
    operation_id = "2" * 32
    _use_operation_id(monkeypatch, operation_id)
    history_directory = history_operation_directory(operation_id).parent
    history_directory.mkdir(parents=True, mode=0o700)
    operation_directory = history_operation_directory(operation_id)
    operation_directory.mkdir(mode=0o700)
    sentinel = operation_directory / "foreign"
    sentinel.write_text("foreign operation\n", encoding="utf-8")
    sentinel.chmod(0o600)

    with pytest.raises(CommandError, match="operation directory already exists"):
        start_history_operation(str(path))

    assert sentinel.read_text(encoding="utf-8") == "foreign operation\n"
    assert active_history_operation_id() is None
    assert _resolved_ref(history_recovery_ref(operation_id)) == ""
    assert not history_operation_preparation_directory(operation_id).exists()


def test_start_does_not_replace_active_appearing_at_publication(
    linear_history_repo,
    monkeypatch,
):
    repo = linear_history_repo
    path, _plan = _write_plan(repo)
    operation_id = "3" * 32
    foreign_operation_id = "f" * 32
    _use_operation_id(monkeypatch, operation_id)
    original_rename_noreplace = resolution_files._rename_noreplace

    def publish_foreign_active(parent, source_name, destination_name):
        if destination_name == "active":
            descriptor = os.open(
                destination_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=parent,
            )
            try:
                os.fchmod(descriptor, 0o600)
                os.write(descriptor, f"{foreign_operation_id}\n".encode("ascii"))
            finally:
                os.close(descriptor)
        original_rename_noreplace(parent, source_name, destination_name)

    monkeypatch.setattr(
        resolution_files,
        "_rename_noreplace",
        publish_foreign_active,
    )

    with pytest.raises(PrivateFilePublicationError) as caught:
        start_history_operation(str(path))

    assert caught.value.outcome is PrivateFilePublicationOutcome.NOT_COMMITTED
    assert active_history_operation_id() == foreign_operation_id
    assert history_operation_directory(operation_id).is_dir()
    assert _resolved_ref(history_recovery_ref(operation_id)) == repo.tip


def test_start_preserves_recovery_after_operation_directory_publication_failure(
    linear_history_repo,
    monkeypatch,
):
    repo = linear_history_repo
    path, _plan = _write_plan(repo)
    operation_id = "4" * 32
    _use_operation_id(monkeypatch, operation_id)
    operation_directory = history_operation_directory(operation_id)
    active_path = operation_directory.parent / "active"
    original_fsync = resolution_files.os.fsync

    def fail_parent_fsync_after_directory_rename(descriptor):
        if (
            operation_directory.is_dir()
            and not active_path.exists()
            and stat.S_ISDIR(os.fstat(descriptor).st_mode)
        ):
            raise OSError("injected operation-directory parent fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(
        resolution_files.os,
        "fsync",
        fail_parent_fsync_after_directory_rename,
    )

    with pytest.raises(OSError, match="operation-directory parent fsync"):
        start_history_operation(str(path))

    assert active_history_operation_id() is None
    assert operation_directory.is_dir()
    assert _resolved_ref(history_recovery_ref(operation_id)) == repo.tip


def test_start_preserves_committed_active_after_parent_fsync_failure(
    linear_history_repo,
    monkeypatch,
):
    repo = linear_history_repo
    path, _plan = _write_plan(repo)
    operation_id = "5" * 32
    _use_operation_id(monkeypatch, operation_id)
    operation_directory = history_operation_directory(operation_id)
    active_path = operation_directory.parent / "active"
    original_fsync = resolution_files.os.fsync

    def fail_parent_fsync_after_active_rename(descriptor):
        if active_path.is_file() and stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("injected active parent fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(
        resolution_files.os,
        "fsync",
        fail_parent_fsync_after_active_rename,
    )

    with pytest.raises(PrivateFilePublicationError) as caught:
        start_history_operation(str(path))

    assert caught.value.outcome is PrivateFilePublicationOutcome.COMMITTED
    assert active_history_operation_id() == operation_id
    assert operation_directory.is_dir()
    assert _resolved_ref(history_recovery_ref(operation_id)) == repo.tip


def test_start_recovery_cas_rejects_a_raced_dangling_symbolic_ref(
    linear_history_repo,
    monkeypatch,
):
    repo = linear_history_repo
    path, _plan = _write_plan(repo)
    operation_id = "6" * 32
    recovery_ref = history_recovery_ref(operation_id)
    victim_ref = "refs/heads/unrelated-recovery-target"
    _use_operation_id(monkeypatch, operation_id)
    real_update_refs = execution.update_git_refs

    def race_recovery_ref(*, updates=(), **kwargs):
        if updates and updates[0][0] == recovery_ref:
            git("symbolic-ref", recovery_ref, victim_ref)
        return real_update_refs(updates=updates, **kwargs)

    monkeypatch.setattr(execution, "update_git_refs", race_recovery_ref)

    with pytest.raises(subprocess.CalledProcessError):
        start_history_operation(str(path))

    assert git("symbolic-ref", recovery_ref) == victim_ref
    assert _resolved_ref(victim_ref) == ""
    assert active_history_operation_id() is None


def test_output_cas_rejects_a_raced_dangling_symbolic_ref(
    linear_history_repo,
    monkeypatch,
):
    repo = linear_history_repo
    path, _plan = _write_plan(repo, _reword_first)
    operation_id = "7" * 32
    output_ref = f"refs/git-stage-batch/rewrite/{operation_id}/output"
    victim_ref = "refs/heads/unrelated-output-target"
    _use_operation_id(monkeypatch, operation_id)
    real_update_refs = execution.update_git_refs

    def race_output_ref(*, updates=(), **kwargs):
        if updates and updates[0][0] == output_ref:
            git("symbolic-ref", output_ref, victim_ref)
        return real_update_refs(updates=updates, **kwargs)

    monkeypatch.setattr(execution, "update_git_refs", race_output_ref)

    with pytest.raises(CommandError, match="output ref changed"):
        start_history_operation(str(path))

    assert git("symbolic-ref", output_ref) == victim_ref
    assert _resolved_ref(victim_ref) == ""
    assert git("rev-parse", "HEAD") == repo.tip


def test_branch_cas_rejects_a_raced_dangling_symbolic_ref(
    linear_history_repo,
    monkeypatch,
):
    repo = linear_history_repo
    path, _plan = _write_plan(repo, _reword_first)
    branch_ref = "refs/heads/topic"
    victim_ref = "refs/heads/unrelated-branch-target"
    real_update_refs = execution.update_git_refs

    def race_branch_ref(*, updates=(), **kwargs):
        if updates and updates[0][0] == branch_ref:
            git("symbolic-ref", branch_ref, victim_ref)
        return real_update_refs(updates=updates, **kwargs)

    monkeypatch.setattr(execution, "update_git_refs", race_branch_ref)

    with pytest.raises(CommandError, match="branch changed before"):
        start_history_operation(str(path))

    assert git("symbolic-ref", branch_ref) == victim_ref
    assert _resolved_ref(victim_ref) == ""
    assert _resolved_ref("HEAD") == ""
    assert _resolved_ref(load_active_history_operation().recovery_ref) == repo.tip


def test_apply_rewords_without_changing_the_final_tree(linear_history_repo):
    repo = linear_history_repo
    original_tree = git("rev-parse", "HEAD^{tree}")
    original_author = git("show", "-s", "--format=%an <%ae> %at %ai", repo.first)
    original_committer = parse_commit_object(repo.first).committer
    path, _plan = _write_plan(repo, _reword_first)

    state = start_history_operation(str(path))

    assert state.phase is HistoryPhase.COMPLETE
    assert active_history_operation_id() is None
    assert latest_history_operation_id() == state.operation_id
    assert git("rev-parse", "HEAD") == state.output_commits[-1]
    assert git("rev-parse", "HEAD^{tree}") == original_tree
    rewritten_first = git("rev-list", "--reverse", f"{repo.base}..HEAD").splitlines()[0]
    assert git("show", "-s", "--format=%B", rewritten_first) == (
        "Explain alpha precisely"
    )
    assert git("show", "-s", "--format=%an <%ae> %at %ai", rewritten_first) == (
        original_author
    )
    assert parse_commit_object(rewritten_first).committer == original_committer
    assert git("diff", "--exit-code") == ""
    assert git("diff", "--cached", "--exit-code") == ""

    verified_state, verification = verify_history_operation()
    assert verified_state == state
    assert verification.final_tree == original_tree


def test_operation_document_rejects_a_plan_swapped_between_read_and_digest(
    linear_history_repo,
    monkeypatch,
):
    repo = linear_history_repo
    path, plan = _write_plan(repo)
    operation_id = "9" * 32
    _use_operation_id(monkeypatch, operation_id)
    monkeypatch.setattr(
        execution,
        "continue_history_operation",
        load_active_history_operation,
    )
    state = start_history_operation(str(path))
    assert state is not None
    persisted_path = history_operation_plan_path(operation_id)
    trusted_payload = persisted_path.read_text(encoding="utf-8")
    swapped_plan = copy.deepcopy(plan)
    swapped_output = swapped_plan["plan"]["outputs"][0]
    swapped_output["operation"] = "REWORD"
    swapped_output["message"] = "Untrusted swapped message\n"
    persisted_path.write_text(
        json.dumps(swapped_plan, indent=2) + "\n",
        encoding="utf-8",
    )
    _swapped_payload, swapped_sha256 = (
        plan_files.read_required_text_file_contents_and_sha256(persisted_path)
    )
    assert swapped_sha256 != state.plan_sha256
    original_read = plan_files.read_required_text_file_contents_and_sha256
    reads = 0

    def read_then_restore_trusted_plan(plan_path):
        nonlocal reads
        reads += 1
        captured = original_read(plan_path)
        plan_path.write_text(trusted_payload, encoding="utf-8")
        return captured

    monkeypatch.setattr(
        plan_files,
        "read_required_text_file_contents_and_sha256",
        read_then_restore_trusted_plan,
    )

    with pytest.raises(CommandError, match="no longer matches its checkpoint"):
        execution._operation_document(state)

    assert reads == 1
    assert persisted_path.read_text(encoding="utf-8") == trusted_payload


def test_apply_integrates_adjacent_sources_into_one_commit(linear_history_repo):
    repo = linear_history_repo
    original_tree = git("rev-parse", "HEAD^{tree}")
    path, _plan = _write_plan(repo, _integrate_all)

    state = start_history_operation(str(path))

    assert state.phase is HistoryPhase.COMPLETE
    assert len(state.output_commits) == 1
    assert git("rev-list", "--count", f"{repo.base}..HEAD") == "1"
    assert git("rev-parse", "HEAD^{tree}") == original_tree


def test_apply_preserves_an_empty_source_commit(linear_history_repo):
    repo = linear_history_repo
    git("commit", "--allow-empty", "-m", "Empty marker")
    empty_commit = git("rev-parse", "HEAD")
    path, _plan = _write_plan(repo)

    state = start_history_operation(str(path))

    assert state.phase is HistoryPhase.COMPLETE
    assert state.output_commits[-1] == empty_commit
    assert git("rev-list", "--count", f"{repo.base}..HEAD") == "3"
    assert git("show", "-s", "--format=%B", "HEAD") == "Empty marker"


def test_commit_writer_binds_keep_to_the_frozen_raw_message(
    linear_history_repo,
):
    repo = linear_history_repo
    source_commit, replacement_commit = _add_text_equivalent_encoded_commits(repo)
    document = acquire_history_plan_document(repo.base)
    target = document.snapshot.commits[0]
    output = document.plan.outputs[0]
    source = parse_commit_object(source_commit)
    replacement = parse_commit_object(replacement_commit)
    assert source.message == replacement.message == "hello\n"
    assert source.message_sha256 != replacement.message_sha256

    with pytest.raises(CommandError, match="unexpected message metadata"):
        require_history_commit_matches(
            replacement_commit,
            tree=target.tree,
            parent=target.parent,
            output=output,
            target=target,
        )

    git("replace", source_commit, replacement_commit)
    with pytest.raises(CommandError, match="frozen digest"):
        create_history_commit(
            tree=target.tree,
            parent=target.parent,
            output=output,
            target=target,
            write=False,
        )


def test_apply_and_verify_ignore_replace_refs_for_source_messages(
    linear_history_repo,
    monkeypatch,
):
    repo = linear_history_repo
    source_commit, replacement_commit = _add_text_equivalent_encoded_commits(repo)
    path, _plan = _write_plan(repo)
    real_build_outputs = execution._build_outputs

    def install_replace_ref_and_build(state, document):
        git("replace", source_commit, replacement_commit)
        return real_build_outputs(state, document)

    monkeypatch.setattr(execution, "_build_outputs", install_replace_ref_and_build)

    state = start_history_operation(str(path))
    assert git("replace", "--list") == source_commit
    assert state.output_commits == (source_commit,)
    git("replace", "-d", source_commit)
    verified_state, verification = verify_history_operation()

    assert verified_state == state
    assert verification.output_tip == source_commit
    raw_output = subprocess.run(
        ["git", "--no-replace-objects", "cat-file", "commit", source_commit],
        check=True,
        capture_output=True,
    ).stdout
    assert raw_output.endswith(b"\n\nhello\n")


def test_apply_integrates_repair_through_commuting_intermediate(
    linear_history_repo,
):
    repo = linear_history_repo
    repo.source.write_text(
        "alpha repaired\nbeta\ngamma topic\n",
        encoding="utf-8",
    )
    git("commit", "-am", "Repair alpha")
    original_tree = git("rev-parse", "HEAD^{tree}")
    path, _plan = _write_plan(repo, _integrate_first_and_last)

    state = start_history_operation(str(path))

    assert state.phase is HistoryPhase.COMPLETE
    assert len(state.output_commits) == 2
    assert git("rev-list", "--count", f"{repo.base}..HEAD") == "2"
    assert git("rev-parse", "HEAD^{tree}") == original_tree


def test_verify_rejects_a_missing_persistent_intermediate_tree(
    linear_history_repo,
):
    repo = linear_history_repo
    repo.source.write_text(
        "alpha repaired\nbeta\ngamma topic\n",
        encoding="utf-8",
    )
    git("commit", "-am", "Repair alpha")
    path, _plan = _write_plan(repo, _integrate_first_and_last)
    pack_directory = Path(
        git(
            "rev-parse",
            "--path-format=absolute",
            "--git-path",
            "objects/pack",
        )
    )
    packs_before = set(pack_directory.glob("pack-*.pack"))
    state = start_history_operation(str(path))
    intermediate_tree = git("rev-parse", f"{state.output_commits[0]}^{{tree}}")
    promoted_packs = set(pack_directory.glob("pack-*.pack")) - packs_before
    assert len(promoted_packs) == 1
    pack_path = promoted_packs.pop()
    index_path = pack_path.with_suffix(".idx")
    unpack_source = repo.root / "promoted-history.pack"
    shutil.copyfile(pack_path, unpack_source)
    pack_path.unlink()
    index_path.unlink()
    with unpack_source.open("rb") as stream:
        subprocess.run(
            ["git", "unpack-objects", "-r"],
            stdin=stream,
            check=True,
            capture_output=True,
        )
    unpack_source.unlink()
    object_directory = Path(
        git(
            "rev-parse",
            "--path-format=absolute",
            "--git-path",
            "objects",
        )
    )
    loose_tree = object_directory / intermediate_tree[:2] / intermediate_tree[2:]
    assert loose_tree.is_file()
    loose_tree.unlink()
    assert git("cat-file", "-t", state.output_commits[0]) == "commit"
    assert git("cat-file", "-t", intermediate_tree, check=False) == ""

    with pytest.raises(CommandError, match="object closure is incomplete"):
        verify_history_operation()


def test_validate_rejects_integration_across_a_blocking_intermediate(
    linear_history_repo,
):
    repo = linear_history_repo
    repo.source.write_text(
        "alpha topic\nbeta\ngamma repaired\n",
        encoding="utf-8",
    )
    git("commit", "-am", "Repair gamma")
    original_tip = git("rev-parse", "HEAD")
    path, _plan = _write_plan(repo, _integrate_first_and_last)

    with pytest.raises(
        CommandError,
        match="BLOCKED dependency|cannot replay source commit|frozen final tree",
    ):
        start_history_operation(str(path))

    assert git("rev-parse", "HEAD") == original_tip
    assert active_history_operation_id() is None


def test_apply_requires_an_exact_published_ref_exception(linear_history_repo):
    repo = linear_history_repo
    git("update-ref", "refs/remotes/origin/topic", repo.first)
    path, _plan = _write_plan(repo, _reword_first)

    with pytest.raises(CommandError, match="published-range"):
        start_history_operation(str(path))

    state = start_history_operation(
        str(path),
        allowed_remote_refs=("refs/remotes/origin/topic",),
    )

    assert state.phase is HistoryPhase.COMPLETE


def test_apply_strips_source_signatures_but_audits_their_digest(
    linear_history_repo,
):
    repo = linear_history_repo
    tree = git("rev-parse", "HEAD^{tree}")
    signature = b"-----BEGIN PGP SIGNATURE-----\nfake\n-----END PGP SIGNATURE-----"
    payload = (
        (
            f"tree {tree}\n"
            f"parent {repo.tip}\n"
            "author Test User <test@example.com> 1700000000 +0000\n"
            "committer Test User <test@example.com> 1700000000 +0000\n"
            "gpgsig "
        ).encode("ascii")
        + signature.replace(b"\n", b"\n ")
        + b"\n\nSigned\n"
    )
    signed_commit = git(
        "hash-object",
        "-t",
        "commit",
        "-w",
        "--stdin",
        input_bytes=payload,
    )
    git("update-ref", "refs/heads/topic", signed_commit, repo.tip)
    repo.base = repo.tip
    path, _plan = _write_plan(repo)

    state = start_history_operation(str(path))
    _verified_state, verification = verify_history_operation()

    output = parse_commit_object(state.output_commits[-1])
    assert state.output_commits[-1] != signed_commit
    assert output.signatures == ()
    assert len(verification.removed_signatures) == 1
    assert verification.removed_signatures[0][0] == signed_commit
    assert len(verification.removed_signatures[0][1].sha256) == 64


def test_finalize_refuses_external_branch_movement(
    linear_history_repo,
    monkeypatch,
):
    repo = linear_history_repo
    path, _plan = _write_plan(repo, _reword_first)
    real_finalize = execution._finalize_branch
    foreign_commit = git(
        "commit-tree",
        git("rev-parse", "HEAD^{tree}"),
        "-p",
        repo.tip,
        "-m",
        "Foreign",
    )

    def move_branch_before_finalize(state):
        git("update-ref", state.branch_ref, foreign_commit, state.original_tip)
        return real_finalize(state)

    monkeypatch.setattr(
        execution,
        "_finalize_branch",
        move_branch_before_finalize,
    )
    with pytest.raises(CommandError, match="branch-tip-changed|no longer names"):
        start_history_operation(str(path))

    paused = load_active_history_operation()
    assert paused.phase is HistoryPhase.PAUSED
    assert git("rev-parse", "HEAD") == foreign_commit
    assert git("rev-parse", paused.recovery_ref) == repo.tip
    with pytest.raises(CommandError, match="does not name an operation-owned tip"):
        abort_history_operation()
    assert git("rev-parse", "HEAD") == foreign_commit


def test_abort_before_build_restores_terminal_latest_state(
    linear_history_repo,
    monkeypatch,
):
    repo = linear_history_repo
    path, _plan = _write_plan(repo, _reword_first)
    original_continue = execution.continue_history_operation

    def interrupt_before_build():
        raise RuntimeError("stop before build")

    monkeypatch.setattr(
        execution,
        "continue_history_operation",
        interrupt_before_build,
    )
    with pytest.raises(RuntimeError, match="stop before build"):
        start_history_operation(str(path))
    monkeypatch.setattr(
        execution,
        "continue_history_operation",
        original_continue,
    )

    assert load_active_history_operation().phase is HistoryPhase.PREPARED
    aborted = abort_history_operation()

    assert aborted.phase is HistoryPhase.ABORTED
    assert git("rev-parse", "HEAD") == repo.tip
    assert active_history_operation_id() is None
    assert load_latest_history_operation() == aborted


def test_continue_adopts_a_pending_output_ref_after_interruption(
    linear_history_repo,
    monkeypatch,
):
    repo = linear_history_repo
    path, _plan = _write_plan(repo, _reword_first)
    real_update = execution.update_history_operation
    interrupted = False

    def interrupt_before_completed_checkpoint(state):
        nonlocal interrupted
        if (
            not interrupted
            and state.completed_output_count == 1
            and state.pending_output_commit is None
        ):
            interrupted = True
            raise RuntimeError("stop after output ref")
        real_update(state)

    monkeypatch.setattr(
        execution,
        "update_history_operation",
        interrupt_before_completed_checkpoint,
    )
    with pytest.raises(RuntimeError, match="stop after output ref"):
        start_history_operation(str(path))
    paused = load_active_history_operation()
    assert paused.phase is HistoryPhase.PAUSED
    assert paused.pending_output_commit is not None
    assert git("rev-parse", paused.output_ref) == paused.pending_output_commit
    assert git("rev-parse", "HEAD") == repo.tip

    monkeypatch.setattr(execution, "update_history_operation", real_update)
    complete = continue_history_operation()

    assert complete.phase is HistoryPhase.COMPLETE
    assert git("rev-parse", "HEAD") == complete.output_commits[-1]


def test_continue_survives_removing_originating_linked_worktree(
    linear_history_repo,
    monkeypatch,
):
    """Durable state must outlive the linked worktree that started a rewrite."""
    repo = linear_history_repo
    linked_worktree = repo.root.parent / "linked"
    git("checkout", "--detach", repo.base)
    git("worktree", "add", str(linked_worktree), "topic")
    monkeypatch.chdir(linked_worktree)

    path, _plan = _write_plan(repo, _reword_first)
    original_continue = execution.continue_history_operation

    def interrupt_before_build():
        raise RuntimeError("stop before linked-worktree build")

    monkeypatch.setattr(
        execution,
        "continue_history_operation",
        interrupt_before_build,
    )
    with pytest.raises(RuntimeError, match="stop before linked-worktree build"):
        start_history_operation(str(path))

    monkeypatch.chdir(repo.root)
    git("worktree", "remove", "--force", str(linked_worktree))
    git("checkout", "topic")
    monkeypatch.setattr(
        execution,
        "continue_history_operation",
        original_continue,
    )

    complete = continue_history_operation()

    assert complete.phase is HistoryPhase.COMPLETE
    assert git("rev-parse", "HEAD") == complete.output_commits[-1]


def test_continue_reconciles_final_branch_cas_after_interruption(
    linear_history_repo,
    monkeypatch,
):
    repo = linear_history_repo
    path, _plan = _write_plan(repo, _reword_first)
    real_update = execution.update_history_operation
    interrupted = False

    def interrupt_before_complete_checkpoint(state):
        nonlocal interrupted
        if not interrupted and state.phase is HistoryPhase.COMPLETE:
            interrupted = True
            raise RuntimeError("stop after branch ref")
        real_update(state)

    monkeypatch.setattr(
        execution,
        "update_history_operation",
        interrupt_before_complete_checkpoint,
    )
    with pytest.raises(RuntimeError, match="stop after branch ref"):
        start_history_operation(str(path))
    paused = load_active_history_operation()
    assert paused.phase is HistoryPhase.PAUSED
    assert git("rev-parse", "HEAD") == paused.output_commits[-1]

    monkeypatch.setattr(execution, "update_history_operation", real_update)
    complete = continue_history_operation()

    assert complete.phase is HistoryPhase.COMPLETE
    assert active_history_operation_id() is None


def test_abort_restores_original_after_final_branch_cas_interruption(
    linear_history_repo,
    monkeypatch,
):
    repo = linear_history_repo
    path, _plan = _write_plan(repo, _reword_first)
    real_update = execution.update_history_operation
    interrupted = False

    def interrupt_before_complete_checkpoint(state):
        nonlocal interrupted
        if not interrupted and state.phase is HistoryPhase.COMPLETE:
            interrupted = True
            raise RuntimeError("stop after branch ref")
        real_update(state)

    monkeypatch.setattr(
        execution,
        "update_history_operation",
        interrupt_before_complete_checkpoint,
    )
    with pytest.raises(RuntimeError, match="stop after branch ref"):
        start_history_operation(str(path))
    monkeypatch.setattr(execution, "update_history_operation", real_update)

    aborted = abort_history_operation()

    assert aborted.phase is HistoryPhase.ABORTED
    assert git("rev-parse", "HEAD") == repo.tip


def test_continue_finishes_an_interrupted_abort_after_final_branch_cas(
    linear_history_repo,
    monkeypatch,
):
    repo = linear_history_repo
    path, _plan = _write_plan(repo, _reword_first)
    real_update_state = execution.update_history_operation
    interrupted = False

    def interrupt_before_complete_checkpoint(state):
        nonlocal interrupted
        if not interrupted and state.phase is HistoryPhase.COMPLETE:
            interrupted = True
            raise RuntimeError("stop after branch ref")
        real_update_state(state)

    monkeypatch.setattr(
        execution,
        "update_history_operation",
        interrupt_before_complete_checkpoint,
    )
    with pytest.raises(RuntimeError, match="stop after branch ref"):
        start_history_operation(str(path))
    monkeypatch.setattr(execution, "update_history_operation", real_update_state)

    real_update_refs = execution.update_git_refs

    def interrupt_before_restore(*, updates=(), **kwargs):
        if updates and updates[0][0] == "refs/heads/topic":
            raise RuntimeError("stop before branch restore")
        return real_update_refs(updates=updates, **kwargs)

    monkeypatch.setattr(execution, "update_git_refs", interrupt_before_restore)
    with pytest.raises(RuntimeError, match="stop before branch restore"):
        abort_history_operation()

    restoring = load_active_history_operation()
    assert restoring.phase is HistoryPhase.PAUSED
    assert restoring.next_action.value == "RESTORE_ORIGINAL"
    assert git("rev-parse", "HEAD") == restoring.output_commits[-1]

    monkeypatch.setattr(execution, "update_git_refs", real_update_refs)
    aborted = continue_history_operation()

    assert aborted.phase is HistoryPhase.ABORTED
    assert git("rev-parse", "HEAD") == repo.tip


def test_finalize_rechecks_worktree_immediately_before_branch_update(
    linear_history_repo,
    monkeypatch,
):
    repo = linear_history_repo
    path, _plan = _write_plan(repo, _reword_first)
    real_finalize = execution._finalize_branch

    def dirty_worktree_before_finalize(state):
        repo.source.write_text("external worktree edit\n", encoding="utf-8")
        return real_finalize(state)

    monkeypatch.setattr(
        execution,
        "_finalize_branch",
        dirty_worktree_before_finalize,
    )
    with pytest.raises(CommandError, match="tracked-worktree"):
        start_history_operation(str(path))

    paused = load_active_history_operation()
    assert paused.phase is HistoryPhase.PAUSED
    assert paused.next_action.value == "UPDATE_BRANCH"
    assert git("rev-parse", "HEAD") == repo.tip


def test_latest_verification_ignores_later_publication_policy(
    linear_history_repo,
):
    repo = linear_history_repo
    path, _plan = _write_plan(repo, _reword_first)
    state = start_history_operation(str(path))
    git("update-ref", "refs/remotes/origin/topic", repo.first)

    verified_state, verification = verify_history_operation()

    assert verified_state == state
    assert verification.output_tip == state.output_commits[-1]


def test_latest_verification_reads_a_persisted_schema_three_plan(
    linear_history_repo,
):
    repo = linear_history_repo
    path, plan = _write_plan(repo, _reword_first)
    state = start_history_operation(str(path))
    _current_state, verification = verify_history_operation()
    legacy_plan = _legacy_v3_plan_record(plan)
    legacy_plan_sha256 = history_json_sha256(legacy_plan)
    write_history_json_file(
        history_operation_plan_path(state.operation_id),
        legacy_plan,
    )
    legacy_verification = execution._verification_record(
        verification,
        plan_sha256=legacy_plan_sha256,
    )
    legacy_verification_sha256 = write_history_verification_record(
        state.operation_id,
        legacy_verification,
    )
    legacy_state = replace(
        state,
        schema_version=2,
        plan_sha256=legacy_plan_sha256,
        verification_sha256=legacy_verification_sha256,
    )
    write_history_json_file(
        history_operation_directory(state.operation_id) / "state.json",
        history_state._state_record(legacy_state),
    )

    verified_state, regenerated = verify_history_operation()

    assert verified_state == legacy_state
    assert regenerated == verification


def test_independent_verify_reports_success_before_checkpoint_verification(
    linear_history_repo,
    monkeypatch,
    capsys,
):
    repo = linear_history_repo
    path, _plan = _write_plan(repo, _reword_first)

    def interrupt_before_checkpoint_verification(_state, _document):
        raise RuntimeError("stop before checkpoint verification")

    monkeypatch.setattr(
        execution,
        "_verify_outputs",
        interrupt_before_checkpoint_verification,
    )
    with pytest.raises(RuntimeError, match="stop before checkpoint verification"):
        start_history_operation(str(path))

    state, verification = verify_history_operation()
    assert state.verification_sha256 is None
    print_rewrite_operation(
        "verify",
        state,
        verification=verification,
        porcelain=True,
    )

    output = json.loads(capsys.readouterr().out)
    assert output["operation"] == "rewrite-verify"
    assert output["verified"] is True


def test_validate_does_not_retain_candidate_tree_objects(
    linear_history_repo,
):
    repo = linear_history_repo
    repo.source.write_text(
        "alpha repaired\nbeta\ngamma topic\n",
        encoding="utf-8",
    )
    git("commit", "-am", "Repair alpha")
    path, _plan = _write_plan(repo, _integrate_first_and_last)
    before = git("count-objects", "-v")

    execution.read_and_validate_history_plan(str(path))

    assert git("count-objects", "-v") == before


def test_integration_rejects_unsupported_headers_on_repair_source(
    linear_history_repo,
):
    repo = linear_history_repo
    source_payload = subprocess.run(
        ["git", "cat-file", "commit", repo.tip],
        check=True,
        capture_output=True,
    ).stdout
    headers, message = source_payload.split(b"\n\n", 1)
    custom_payload = headers + b"\nx-review-metadata retained\n\n" + message
    custom_tip = git(
        "hash-object",
        "-t",
        "commit",
        "-w",
        "--stdin",
        input_bytes=custom_payload,
    )
    git("update-ref", "refs/heads/topic", custom_tip, repo.tip)
    path, _plan = _write_plan(repo, _integrate_all)

    with pytest.raises(CommandError, match="unsupported header.*x-review-metadata"):
        start_history_operation(str(path))
