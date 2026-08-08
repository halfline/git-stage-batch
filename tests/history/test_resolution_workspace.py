"""Tests for resumable explicit rewrite-resolution workspaces."""

from __future__ import annotations

import copy
import gc
import json
from pathlib import Path
import shutil
import tracemalloc

import pytest

from git_stage_batch.exceptions import CommandError
from git_stage_batch.history import resolution_files, resolution_workspace
from git_stage_batch.history.plan_files import (
    read_and_validate_history_plan_semantics,
)
from git_stage_batch.history.records import history_plan_document_record
from git_stage_batch.history.resolution_files import lock_resolution_directory
from git_stage_batch.history.resolution_workspace import (
    materialize_completed_history_resolution,
    resolve_history_plan,
)
from git_stage_batch.history.scan import acquire_history_plan_document
from git_stage_batch.utils.git_object_io import temporary_git_object_environment

from .conftest import git


def _write_plan(path: Path, record: dict[str, object]) -> None:
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def _resolved_plan(
    linear_history_repo,
    *,
    resolved_indexes: tuple[int, ...] = (0,),
) -> Path:
    path = linear_history_repo.root / "plan.json"
    record = history_plan_document_record(
        acquire_history_plan_document(linear_history_repo.base)
    )
    outputs = record["plan"]["outputs"]
    for index in resolved_indexes:
        outputs[index]["materialization"] = "RESOLVED"
    _write_plan(path, record)
    return path


def _output_path(workspace: Path, output_key: str) -> Path:
    return workspace / "outputs" / output_key


def _metadata(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _leave_interrupted_write(destination: Path) -> Path:
    temporary = destination.with_name(
        resolution_files._temporary_resolution_artifact_name(destination)
    )
    temporary.write_bytes(b"interrupted publication\n")
    temporary.chmod(0o600)
    return temporary


def _replace_result_with_reference(
    output_path: Path,
    *,
    role: str,
    payload: bytes | None = None,
) -> None:
    request = _metadata(output_path / "request.json")
    result = _metadata(output_path / "result.json")
    request_path = request["authorized_paths"][0]
    result_path = result["paths"][0]
    destination = output_path / "results" / result_path["artifact"]
    if payload is not None:
        destination.write_bytes(payload)
        destination.chmod(0o600)
        return
    reference = next(
        candidate
        for candidate in request_path["references"]
        if candidate["role"] == role
    )
    shutil.copyfile(
        output_path / "references" / reference["artifact"],
        destination,
    )
    destination.chmod(0o600)
    result_path["state"] = reference["state"]
    result_path["mode"] = reference["mode"]
    _write_plan(output_path / "result.json", result)


def test_resolution_exports_source_context_and_completes_exact_replay(
    linear_history_repo,
):
    plan = _resolved_plan(linear_history_repo)
    workspace = linear_history_repo.root / "resolution"

    pending = resolve_history_plan(str(plan), str(workspace))

    assert pending.status == "NEEDS_RESOLUTION"
    assert pending.output_index == 0
    assert pending.completed_resolved_outputs == 0
    assert pending.total_resolved_outputs == 1
    assert pending.authorized_paths == ("example.txt",)
    assert pending.plan_path == str(plan)
    assert pending.request_path is not None
    assert pending.result_path is not None
    assert pending.results_path is not None
    assert pending.output_key is not None
    output_path = _output_path(workspace, pending.output_key)
    request = _metadata(output_path / "request.json")
    references = request["authorized_paths"][0]["references"]
    assert [reference["role"] for reference in references] == [
        "CURRENT_PARENT",
        "SOURCE_BEFORE",
        "SOURCE_AFTER",
    ]
    _replace_result_with_reference(output_path, role="SOURCE_AFTER")

    complete = resolve_history_plan(
        str(plan),
        str(workspace),
        accept_result=True,
    )

    assert complete.status == "COMPLETE"
    assert complete.completed_resolved_outputs == 1
    assert complete.output_index is None
    assert complete.request_path is None
    completion = _metadata(workspace / "complete.json")
    assert completion["final_tree"] == git("rev-parse", "HEAD^{tree}")


def test_resolution_reentry_does_not_accept_seeded_result(linear_history_repo):
    plan = _resolved_plan(linear_history_repo)
    workspace = linear_history_repo.root / "resolution"
    first = resolve_history_plan(str(plan), str(workspace))

    second = resolve_history_plan(str(plan), str(workspace))

    assert second == first
    assert not (_output_path(workspace, first.output_key) / "receipt.json").exists()


def test_resolution_rejects_all_exact_plan_without_creating_workspace(
    linear_history_repo,
):
    plan = _resolved_plan(linear_history_repo, resolved_indexes=())
    workspace = linear_history_repo.root / "resolution"

    with pytest.raises(CommandError, match="does not contain any RESOLVED outputs"):
        resolve_history_plan(str(plan), str(workspace))

    assert not workspace.exists()


def test_continue_accepts_only_one_of_two_resolved_outputs_per_call(
    linear_history_repo,
):
    plan = _resolved_plan(linear_history_repo, resolved_indexes=(0, 1))
    workspace = linear_history_repo.root / "resolution"
    first = resolve_history_plan(str(plan), str(workspace))
    assert first.output_key is not None
    first_output = _output_path(workspace, first.output_key)
    _replace_result_with_reference(
        first_output,
        role="SOURCE_AFTER",
        payload=b"alpha intermediate\nbeta\ngamma\n",
    )

    second = resolve_history_plan(
        str(plan),
        str(workspace),
        accept_result=True,
    )

    assert second.status == "NEEDS_RESOLUTION"
    assert second.output_index == 1
    assert second.completed_resolved_outputs == 1
    assert second.output_key is not None
    first_receipt = _metadata(first_output / "receipt.json")
    assert (
        git(
            "cat-file",
            "-t",
            first_receipt["output_tree"],
            check=False,
        )
        == ""
    )
    second_output = _output_path(workspace, second.output_key)
    _replace_result_with_reference(second_output, role="SOURCE_AFTER")

    complete = resolve_history_plan(
        str(plan),
        str(workspace),
        accept_result=True,
    )

    assert complete.status == "COMPLETE"
    assert complete.completed_resolved_outputs == 2


def test_partitioned_unit_can_materialize_distinct_resolved_snapshots(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    git("init", "-b", "topic")
    git("config", "user.name", "Test User")
    git("config", "user.email", "test@example.com")
    source = tmp_path / "example.txt"
    source.write_text("base\n", encoding="utf-8")
    git("add", "example.txt")
    git("commit", "-m", "Base")
    base = git("rev-parse", "HEAD")
    source.write_text("final\n", encoding="utf-8")
    git("commit", "-am", "Replace value")
    record = history_plan_document_record(acquire_history_plan_document(base))
    original = record["plan"]["outputs"][0]
    unit_id = original["source_unit_ids"][0]
    outputs = []
    for message in ("Introduce intermediate value\n", "Finish value\n"):
        output = copy.deepcopy(original)
        output["operation"] = "SPLIT"
        output["materialization"] = "RESOLVED"
        output["message"] = message
        outputs.append(output)
    record["plan"]["partitioned_units"] = [
        {"unit_id": unit_id, "output_indexes": [0, 1]}
    ]
    record["plan"]["outputs"] = outputs
    plan = tmp_path / "plan.json"
    _write_plan(plan, record)
    workspace = tmp_path / "resolution"

    first = resolve_history_plan(str(plan), str(workspace))
    assert first.output_key is not None
    _replace_result_with_reference(
        _output_path(workspace, first.output_key),
        role="SOURCE_AFTER",
        payload=b"intermediate\n",
    )
    second = resolve_history_plan(
        str(plan),
        str(workspace),
        accept_result=True,
    )
    assert second.output_key is not None
    _replace_result_with_reference(
        _output_path(workspace, second.output_key),
        role="SOURCE_AFTER",
    )

    complete = resolve_history_plan(
        str(plan),
        str(workspace),
        accept_result=True,
    )

    assert complete.status == "COMPLETE"
    assert _metadata(workspace / "complete.json")["final_tree"] == git(
        "rev-parse", "HEAD^{tree}"
    )


def test_resolution_rejects_stale_plan_and_extra_result_file(
    linear_history_repo,
):
    plan = _resolved_plan(linear_history_repo)
    workspace = linear_history_repo.root / "resolution"
    pending = resolve_history_plan(str(plan), str(workspace))
    assert pending.output_key is not None
    output_path = _output_path(workspace, pending.output_key)
    (output_path / "results" / "unexpected").write_text(
        "extra\n",
        encoding="utf-8",
    )

    with pytest.raises(CommandError, match="unexpected unexpected"):
        resolve_history_plan(
            str(plan),
            str(workspace),
            accept_result=True,
        )

    (output_path / "results" / "unexpected").unlink()
    plan.write_text(plan.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(CommandError, match="workspace.json.*immutable binding"):
        resolve_history_plan(str(plan), str(workspace))


def test_extra_output_directory_prevents_receipt_publication(linear_history_repo):
    plan = _resolved_plan(linear_history_repo)
    workspace = linear_history_repo.root / "resolution"
    pending = resolve_history_plan(str(plan), str(workspace))
    assert pending.output_key is not None
    output_path = _output_path(workspace, pending.output_key)
    _replace_result_with_reference(output_path, role="SOURCE_AFTER")
    (workspace / "outputs" / "unexpected").mkdir(mode=0o700)

    with pytest.raises(CommandError, match="unexpected entries: unexpected"):
        resolve_history_plan(str(plan), str(workspace), accept_result=True)

    assert not (output_path / "receipt.json").exists()


def test_unexpected_workspace_names_are_terminal_safe(linear_history_repo):
    plan = _resolved_plan(linear_history_repo)
    workspace = linear_history_repo.root / "resolution"
    pending = resolve_history_plan(str(plan), str(workspace))
    assert pending.output_key is not None
    unexpected_name = "unexpected\n\x1b[31m"
    (workspace / "outputs" / unexpected_name).mkdir(mode=0o700)

    with pytest.raises(CommandError) as raised:
        resolve_history_plan(str(plan), str(workspace), accept_result=True)

    diagnostic = str(raised.value)
    assert unexpected_name not in diagnostic
    assert "\\n" in diagnostic
    assert "\\u001b" in diagnostic


def test_resolution_rejects_empty_output_and_final_tree_mismatch(
    linear_history_repo,
):
    plan = _resolved_plan(linear_history_repo)
    workspace = linear_history_repo.root / "resolution-empty"
    resolve_history_plan(str(plan), str(workspace))
    with pytest.raises(CommandError, match="did not change"):
        resolve_history_plan(
            str(plan),
            str(workspace),
            accept_result=True,
        )

    mismatch_workspace = linear_history_repo.root / "resolution-mismatch"
    pending = resolve_history_plan(str(plan), str(mismatch_workspace))
    assert pending.output_key is not None
    _replace_result_with_reference(
        _output_path(mismatch_workspace, pending.output_key),
        role="SOURCE_AFTER",
        payload=b"alpha unexpected\nbeta\ngamma\n",
    )
    with pytest.raises(CommandError, match="frozen final tree"):
        resolve_history_plan(
            str(plan),
            str(mismatch_workspace),
            accept_result=True,
        )
    mismatch_output = _output_path(mismatch_workspace, pending.output_key)
    assert not (mismatch_output / "receipt.json").exists()
    _replace_result_with_reference(mismatch_output, role="SOURCE_AFTER")
    assert (
        resolve_history_plan(
            str(plan),
            str(mismatch_workspace),
            accept_result=True,
        ).status
        == "COMPLETE"
    )


def test_user_result_json_member_order_is_not_significant(linear_history_repo):
    plan = _resolved_plan(linear_history_repo)
    workspace = linear_history_repo.root / "resolution"
    pending = resolve_history_plan(str(plan), str(workspace))
    assert pending.output_key is not None
    output_path = _output_path(workspace, pending.output_key)
    _replace_result_with_reference(output_path, role="SOURCE_AFTER")
    result_path = output_path / "result.json"
    result = _metadata(result_path)
    result["paths"] = [
        {key: path[key] for key in reversed(tuple(path))} for path in result["paths"]
    ]
    reordered = {key: result[key] for key in reversed(tuple(result))}
    _write_plan(result_path, reordered)

    assert (
        resolve_history_plan(
            str(plan),
            str(workspace),
            accept_result=True,
        ).status
        == "COMPLETE"
    )


def test_accepted_resolution_rejects_modified_result_artifact(
    linear_history_repo,
):
    plan = _resolved_plan(linear_history_repo, resolved_indexes=(0, 1))
    workspace = linear_history_repo.root / "resolution"
    first = resolve_history_plan(str(plan), str(workspace))
    assert first.output_key is not None
    first_output = _output_path(workspace, first.output_key)
    _replace_result_with_reference(
        first_output,
        role="SOURCE_AFTER",
        payload=b"alpha intermediate\nbeta\ngamma\n",
    )
    second = resolve_history_plan(
        str(plan),
        str(workspace),
        accept_result=True,
    )
    result = _metadata(first_output / "result.json")
    artifact = first_output / "results" / result["paths"][0]["artifact"]
    artifact.write_bytes(b"modified after acceptance\n")

    with pytest.raises(CommandError, match="expected size and SHA-256"):
        resolve_history_plan(str(plan), str(workspace))

    artifact.write_bytes(b"alpha intermediate\nbeta\ngamma\n")
    result_path = first_output / "result.json"
    original_result = result_path.read_text(encoding="utf-8")
    result_path.write_text(original_result + "\n", encoding="utf-8")
    with pytest.raises(CommandError, match="result.json changed after acceptance"):
        resolve_history_plan(str(plan), str(workspace))

    result_path.write_text(original_result, encoding="utf-8")
    receipt_path = first_output / "receipt.json"
    receipt = _metadata(receipt_path)
    receipt["output_tree"] = "0" * 40
    _write_plan(receipt_path, receipt)
    with pytest.raises(CommandError, match="receipt.json is not authentic"):
        resolve_history_plan(str(plan), str(workspace))

    assert second.status == "NEEDS_RESOLUTION"


def test_resolution_rejects_symlinked_result_artifact(linear_history_repo):
    plan = _resolved_plan(linear_history_repo)
    workspace = linear_history_repo.root / "resolution"
    pending = resolve_history_plan(str(plan), str(workspace))
    assert pending.output_key is not None
    output_path = _output_path(workspace, pending.output_key)
    request = _metadata(output_path / "request.json")
    result = _metadata(output_path / "result.json")
    result_artifact = output_path / "results" / result["paths"][0]["artifact"]
    result_artifact.unlink()
    source_after = next(
        reference
        for reference in request["authorized_paths"][0]["references"]
        if reference["role"] == "SOURCE_AFTER"
    )
    result_artifact.symlink_to(output_path / "references" / source_after["artifact"])

    with pytest.raises(CommandError, match="must not contain.*symlinks"):
        resolve_history_plan(str(plan), str(workspace), accept_result=True)


def test_arbitrary_git_path_uses_only_opaque_artifact_names(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    git("init", "-b", "topic")
    git("config", "user.name", "Test User")
    git("config", "user.email", "test@example.com")
    anchor = tmp_path / "anchor.txt"
    anchor.write_text("anchor\n", encoding="utf-8")
    git("add", "anchor.txt")
    git("commit", "-m", "Base")
    base = git("rev-parse", "HEAD")
    unusual_name = "line\nwith-unusual-name.txt"
    (tmp_path / unusual_name).write_text("resolved\n", encoding="utf-8")
    git("add", unusual_name)
    git("commit", "-m", "Add unusual path")
    record = history_plan_document_record(acquire_history_plan_document(base))
    record["plan"]["outputs"][0]["materialization"] = "RESOLVED"
    plan = tmp_path / "plan.json"
    _write_plan(plan, record)
    workspace = tmp_path / "resolution"

    pending = resolve_history_plan(str(plan), str(workspace))

    assert pending.authorized_paths == (unusual_name,)
    assert pending.output_key is not None
    output_path = _output_path(workspace, pending.output_key)
    request = _metadata(output_path / "request.json")
    result = _metadata(output_path / "result.json")
    artifact_names = {
        path.name
        for directory in ("references", "results")
        for path in (output_path / directory).iterdir()
    }
    artifact_names.add(result["paths"][0]["artifact"])
    assert all(name.startswith("artifact-") for name in artifact_names)
    assert all("line" not in name and "\n" not in name for name in artifact_names)
    assert request["authorized_paths"][0]["path"] == unusual_name
    _replace_result_with_reference(output_path, role="SOURCE_AFTER")
    assert (
        resolve_history_plan(
            str(plan),
            str(workspace),
            accept_result=True,
        ).status
        == "COMPLETE"
    )


def test_resolution_rejects_file_type_source_unit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    git("init", "-b", "topic")
    git("config", "user.name", "Test User")
    git("config", "user.email", "test@example.com")
    source = tmp_path / "example.txt"
    source.write_text("regular\n", encoding="utf-8")
    git("add", "example.txt")
    git("commit", "-m", "Base")
    base = git("rev-parse", "HEAD")
    source.unlink()
    source.symlink_to("target")
    git("add", "example.txt")
    git("commit", "-m", "Replace file with symlink")
    record = history_plan_document_record(acquire_history_plan_document(base))
    record["plan"]["outputs"][0]["materialization"] = "RESOLVED"
    plan = tmp_path / "plan.json"
    _write_plan(plan, record)

    with pytest.raises(CommandError, match="unsupported.*file-type"):
        resolve_history_plan(str(plan), str(tmp_path / "resolution"))


def test_resolved_outputs_authorize_file_addition_and_deletion(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    git("init", "-b", "topic")
    git("config", "user.name", "Test User")
    git("config", "user.email", "test@example.com")
    deleted = tmp_path / "deleted.txt"
    deleted.write_text("remove me\n", encoding="utf-8")
    git("add", "deleted.txt")
    git("commit", "-m", "Base")
    base = git("rev-parse", "HEAD")
    added = tmp_path / "added.txt"
    added.write_text("added\n", encoding="utf-8")
    git("add", "added.txt")
    git("commit", "-m", "Add file")
    deleted.unlink()
    git("add", "deleted.txt")
    git("commit", "-m", "Delete file")
    record = history_plan_document_record(acquire_history_plan_document(base))
    for output in record["plan"]["outputs"]:
        output["materialization"] = "RESOLVED"
    plan = tmp_path / "plan.json"
    _write_plan(plan, record)
    workspace = tmp_path / "resolution"

    addition = resolve_history_plan(str(plan), str(workspace))
    assert addition.output_key is not None
    addition_path = _output_path(workspace, addition.output_key)
    _replace_result_with_reference(addition_path, role="SOURCE_AFTER")
    deletion = resolve_history_plan(
        str(plan),
        str(workspace),
        accept_result=True,
    )
    assert deletion.output_key is not None
    deletion_path = _output_path(workspace, deletion.output_key)
    result_path = deletion_path / "result.json"
    result = _metadata(result_path)
    result_artifact = deletion_path / "results" / result["paths"][0]["artifact"]
    result_artifact.unlink()
    result["paths"][0]["state"] = "ABSENT"
    result["paths"][0]["mode"] = None
    _write_plan(result_path, result)

    assert (
        resolve_history_plan(
            str(plan),
            str(workspace),
            accept_result=True,
        ).status
        == "COMPLETE"
    )


def test_completed_workspace_replays_read_only_in_a_fresh_quarantine(
    linear_history_repo,
):
    plan = _resolved_plan(linear_history_repo)
    workspace = linear_history_repo.root / "resolution"
    pending = resolve_history_plan(str(plan), str(workspace))
    assert pending.output_key is not None
    _replace_result_with_reference(
        _output_path(workspace, pending.output_key),
        role="SOURCE_AFTER",
    )
    resolve_history_plan(str(plan), str(workspace), accept_result=True)
    document, plan_sha256 = read_and_validate_history_plan_semantics(str(plan))

    with temporary_git_object_environment() as quarantine:
        replay = materialize_completed_history_resolution(
            document,
            plan_sha256,
            str(workspace),
            quarantine=quarantine,
        )

    assert replay.final_tree == document.snapshot.final_tree


def test_completed_workspace_reader_requires_manifest_without_mutation(
    linear_history_repo,
):
    plan = _resolved_plan(linear_history_repo)
    workspace = linear_history_repo.root / "resolution"
    resolve_history_plan(str(plan), str(workspace))
    document, plan_sha256 = read_and_validate_history_plan_semantics(str(plan))
    before = tuple(sorted(path.name for path in workspace.iterdir()))

    with temporary_git_object_environment() as quarantine:
        with pytest.raises(CommandError, match="missing complete.json"):
            materialize_completed_history_resolution(
                document,
                plan_sha256,
                str(workspace),
                quarantine=quarantine,
            )

    assert tuple(sorted(path.name for path in workspace.iterdir())) == before


def test_resolve_does_not_mutate_an_unbound_existing_directory(
    linear_history_repo,
):
    plan = _resolved_plan(linear_history_repo)
    workspace = linear_history_repo.root / "unrelated"
    workspace.mkdir(mode=0o700)

    with pytest.raises(CommandError, match="cannot open workspace lock"):
        resolve_history_plan(str(plan), str(workspace))

    assert list(workspace.iterdir()) == []


def test_concurrent_workspace_use_fails_without_waiting(linear_history_repo):
    plan = _resolved_plan(linear_history_repo)
    workspace = linear_history_repo.root / "resolution"
    resolve_history_plan(str(plan), str(workspace))

    with lock_resolution_directory(workspace, create=False):
        with pytest.raises(CommandError, match="already in use"):
            resolve_history_plan(str(plan), str(workspace))


def test_interrupted_request_scaffold_recovers_before_publication(
    linear_history_repo,
    monkeypatch,
):
    plan = _resolved_plan(linear_history_repo)
    workspace = linear_history_repo.root / "resolution"
    write_json = resolution_workspace._write_json
    interrupted = False

    def interrupt_request(path, record):
        nonlocal interrupted
        if path.name == "request.json" and not interrupted:
            interrupted = True
            raise CommandError("interrupted request publication")
        return write_json(path, record)

    monkeypatch.setattr(resolution_workspace, "_write_json", interrupt_request)
    with pytest.raises(CommandError, match="interrupted request publication"):
        resolve_history_plan(str(plan), str(workspace))
    output_names = [path.name for path in (workspace / "outputs").iterdir()]
    assert len(output_names) == 1
    assert output_names[0].startswith(".staging-")
    monkeypatch.setattr(resolution_workspace, "_write_json", write_json)

    pending = resolve_history_plan(str(plan), str(workspace))

    assert pending.status == "NEEDS_RESOLUTION"
    assert [path.name for path in (workspace / "outputs").iterdir()] == [
        pending.output_key
    ]


def test_interrupted_receipt_and_completion_writes_resume(
    linear_history_repo,
    monkeypatch,
):
    plan = _resolved_plan(linear_history_repo)
    receipt_workspace = linear_history_repo.root / "receipt-resolution"
    pending = resolve_history_plan(str(plan), str(receipt_workspace))
    assert pending.output_key is not None
    output_path = _output_path(receipt_workspace, pending.output_key)
    _replace_result_with_reference(output_path, role="SOURCE_AFTER")
    receipt_temporary = _leave_interrupted_write(output_path / "receipt.json")

    assert (
        resolve_history_plan(
            str(plan),
            str(receipt_workspace),
            accept_result=True,
        ).status
        == "COMPLETE"
    )
    assert not receipt_temporary.exists()

    completion_workspace = linear_history_repo.root / "completion-resolution"
    pending = resolve_history_plan(str(plan), str(completion_workspace))
    assert pending.output_key is not None
    _replace_result_with_reference(
        _output_path(completion_workspace, pending.output_key),
        role="SOURCE_AFTER",
    )
    write_json = resolution_workspace._write_json

    def interrupt_completion(path, record):
        if path.name == "complete.json":
            raise CommandError("interrupted completion publication")
        return write_json(path, record)

    monkeypatch.setattr(resolution_workspace, "_write_json", interrupt_completion)
    with pytest.raises(CommandError, match="interrupted completion publication"):
        resolve_history_plan(
            str(plan),
            str(completion_workspace),
            accept_result=True,
        )
    monkeypatch.setattr(resolution_workspace, "_write_json", write_json)
    completion_temporary = _leave_interrupted_write(
        completion_workspace / "complete.json"
    )

    assert (
        resolve_history_plan(
            str(plan),
            str(completion_workspace),
        ).status
        == "COMPLETE"
    )
    assert not completion_temporary.exists()


def test_selected_content_and_mode_units_do_not_launder_each_other(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    git("init", "-b", "topic")
    git("config", "user.name", "Test User")
    git("config", "user.email", "test@example.com")
    source = tmp_path / "example.txt"
    source.write_text("base\n", encoding="utf-8")
    git("add", "example.txt")
    git("commit", "-m", "Base")
    base = git("rev-parse", "HEAD")
    source.write_text("changed\n", encoding="utf-8")
    source.chmod(0o755)
    git("add", "example.txt")
    git("commit", "-m", "Change content and mode")
    record = history_plan_document_record(acquire_history_plan_document(base))
    original = record["plan"]["outputs"][0]
    content_unit, mode_unit = original["source_unit_ids"]
    content_output = copy.deepcopy(original)
    mode_output = copy.deepcopy(original)
    for output, unit in (
        (content_output, content_unit),
        (mode_output, mode_unit),
    ):
        output["operation"] = "SPLIT"
        output["materialization"] = "RESOLVED"
        output["source_unit_ids"] = [unit]
    record["plan"]["outputs"] = [content_output, mode_output]
    plan = tmp_path / "plan.json"
    _write_plan(plan, record)
    workspace = tmp_path / "resolution"
    content_pending = resolve_history_plan(str(plan), str(workspace))
    assert content_pending.output_key is not None
    content_path = _output_path(workspace, content_pending.output_key)
    _replace_result_with_reference(content_path, role="SOURCE_AFTER")
    content_result = _metadata(content_path / "result.json")
    content_result["paths"][0]["mode"] = "100755"
    _write_plan(content_path / "result.json", content_result)

    with pytest.raises(CommandError, match="not authorized to change mode"):
        resolve_history_plan(str(plan), str(workspace), accept_result=True)

    content_result["paths"][0]["mode"] = "100644"
    _write_plan(content_path / "result.json", content_result)
    mode_pending = resolve_history_plan(
        str(plan),
        str(workspace),
        accept_result=True,
    )
    assert mode_pending.output_key is not None
    mode_path = _output_path(workspace, mode_pending.output_key)
    mode_result = _metadata(mode_path / "result.json")
    mode_result["paths"][0]["mode"] = "100755"
    _write_plan(mode_path / "result.json", mode_result)
    _replace_result_with_reference(
        mode_path,
        role="CURRENT_PARENT",
        payload=b"unauthorized content\n",
    )

    with pytest.raises(CommandError, match="not authorized to change content"):
        resolve_history_plan(str(plan), str(workspace), accept_result=True)

    _replace_result_with_reference(mode_path, role="CURRENT_PARENT")
    mode_result = _metadata(mode_path / "result.json")
    mode_result["paths"][0]["mode"] = "100755"
    _write_plan(mode_path / "result.json", mode_result)
    assert (
        resolve_history_plan(
            str(plan),
            str(workspace),
            accept_result=True,
        ).status
        == "COMPLETE"
    )


def test_large_resolution_replay_has_bounded_python_heap(tmp_path, monkeypatch):
    line = b"resolved history payload " + b"x" * 488 + b"\n"
    heap_peaks: list[int] = []

    for line_count in (2048, 16384):
        repository = tmp_path / f"repository-{line_count}"
        repository.mkdir()
        monkeypatch.chdir(repository)
        git("init", "-b", "topic")
        git("config", "user.name", "Test User")
        git("config", "user.email", "test@example.com")
        (repository / "anchor.txt").write_text("anchor\n", encoding="utf-8")
        git("add", "anchor.txt")
        git("commit", "-m", "Base")
        base = git("rev-parse", "HEAD")
        (repository / "large.txt").write_bytes(line * line_count)
        git("add", "large.txt")
        git("commit", "-m", "Add large file")
        record = history_plan_document_record(acquire_history_plan_document(base))
        record["plan"]["outputs"][0]["materialization"] = "RESOLVED"
        plan = repository / "plan.json"
        _write_plan(plan, record)
        workspace = repository / "resolution"
        pending = resolve_history_plan(str(plan), str(workspace))
        assert pending.output_key is not None
        _replace_result_with_reference(
            _output_path(workspace, pending.output_key),
            role="SOURCE_AFTER",
        )
        assert (
            resolve_history_plan(
                str(plan),
                str(workspace),
                accept_result=True,
            ).status
            == "COMPLETE"
        )
        document, plan_sha256 = read_and_validate_history_plan_semantics(str(plan))

        gc.collect()
        tracemalloc.start()
        try:
            with temporary_git_object_environment() as quarantine:
                replay = materialize_completed_history_resolution(
                    document,
                    plan_sha256,
                    str(workspace),
                    quarantine=quarantine,
                )
            _current_heap, peak_heap = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        heap_peaks.append(peak_heap)
        assert replay.final_tree == document.snapshot.final_tree

    small_peak, large_peak = heap_peaks
    assert large_peak < small_peak + 128 * 1024
