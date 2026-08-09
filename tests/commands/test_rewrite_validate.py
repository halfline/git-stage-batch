"""Adversarial command tests for completed rewrite-resolution validation."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from git_stage_batch.commands import rewrite_validate
from git_stage_batch.exceptions import CommandError
from git_stage_batch.history.records import history_plan_document_record
from git_stage_batch.history.resolution_workspace import resolve_history_plan
from git_stage_batch.history.scan import acquire_history_plan_document


_INTERMEDIATE_PAYLOAD = b"alpha intermediate\nbeta\ngamma\n"


def _git(
    *arguments: str,
    check: bool = True,
    input_bytes: bytes | None = None,
) -> str:
    result = subprocess.run(
        ["git", *arguments],
        check=check,
        input=input_bytes,
        capture_output=True,
        text=input_bytes is None,
    )
    if isinstance(result.stdout, bytes):
        return result.stdout.decode("ascii").strip()
    return result.stdout.strip()


def _object_exists(object_id: str) -> bool:
    return (
        subprocess.run(
            ["git", "cat-file", "-e", object_id],
            check=False,
            capture_output=True,
        ).returncode
        == 0
    )


def _write_json(path: Path, record: object) -> None:
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def _metadata(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _replace_result(
    output_path: Path,
    *,
    role: str,
    payload: bytes | None = None,
) -> None:
    request = _metadata(output_path / "request.json")
    result = _metadata(output_path / "result.json")
    request_path = request["authorized_paths"][0]
    result_path = result["paths"][0]
    artifact = output_path / "results" / result_path["artifact"]
    reference = next(
        item for item in request_path["references"] if item["role"] == role
    )
    if payload is None:
        shutil.copyfile(
            output_path / "references" / reference["artifact"],
            artifact,
        )
    else:
        artifact.write_bytes(payload)
    artifact.chmod(0o600)
    result_path["state"] = reference["state"]
    result_path["mode"] = reference["mode"]
    _write_json(output_path / "result.json", result)


def _complete_workspace(plan: Path, workspace: Path) -> SimpleNamespace:
    first = resolve_history_plan(str(plan), str(workspace))
    assert first.output_key is not None
    first_output = workspace / "outputs" / first.output_key
    _replace_result(
        first_output,
        role="SOURCE_AFTER",
        payload=_INTERMEDIATE_PAYLOAD,
    )
    second = resolve_history_plan(
        str(plan),
        str(workspace),
        accept_result=True,
    )
    assert second.output_key is not None
    second_output = workspace / "outputs" / second.output_key
    _replace_result(second_output, role="SOURCE_AFTER")
    completed = resolve_history_plan(
        str(plan),
        str(workspace),
        accept_result=True,
    )
    assert completed.status == "COMPLETE"
    first_receipt = _metadata(first_output / "receipt.json")
    candidate_tree = first_receipt["output_tree"]
    assert isinstance(candidate_tree, str)
    candidate_blob = _git("hash-object", "--stdin", input_bytes=_INTERMEDIATE_PAYLOAD)
    return SimpleNamespace(
        first_output=first_output,
        second_output=second_output,
        candidate_tree=candidate_tree,
        candidate_blob=candidate_blob,
    )


@pytest.fixture
def resolved_repository(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _git("init", "-b", "topic")
    _git("config", "user.name", "Test User")
    _git("config", "user.email", "test@example.com")
    source = tmp_path / "example.txt"
    source.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    _git("add", "example.txt")
    _git("commit", "-m", "Base")
    base = _git("rev-parse", "HEAD")
    source.write_text("alpha topic\nbeta\ngamma\n", encoding="utf-8")
    _git("commit", "-am", "Change alpha")
    source.write_text("alpha topic\nbeta\ngamma topic\n", encoding="utf-8")
    _git("commit", "-am", "Change gamma")
    record = history_plan_document_record(acquire_history_plan_document(base))
    for output in record["plan"]["outputs"]:
        output["materialization"] = "RESOLVED"
    plan = tmp_path / "plan.json"
    _write_json(plan, record)
    workspace = tmp_path / "resolution"
    completion = _complete_workspace(plan, workspace)
    return SimpleNamespace(
        root=tmp_path,
        plan=plan,
        plan_record=record,
        source=source,
        workspace=workspace,
        completion=completion,
        final_tree=_git("rev-parse", "HEAD^{tree}"),
    )


def _assert_candidate_objects_absent(repository) -> None:
    assert not _object_exists(repository.completion.candidate_blob)
    assert not _object_exists(repository.completion.candidate_tree)


def test_completed_workspace_authenticates_in_fresh_quarantine(
    resolved_repository,
    monkeypatch,
    capsys,
):
    repository = resolved_repository
    _assert_candidate_objects_absent(repository)
    refs_before = _git("for-each-ref", "--format=%(refname) %(objectname)")
    quarantine_paths: list[Path] = []
    create_quarantine = rewrite_validate.temporary_git_object_environment

    @contextmanager
    def tracked_quarantine(*, disable_replace_objects=False):
        with create_quarantine(
            disable_replace_objects=disable_replace_objects
        ) as quarantine:
            assert quarantine.environment()["GIT_NO_REPLACE_OBJECTS"] == "1"
            quarantine_path = Path(quarantine.environment()["GIT_OBJECT_DIRECTORY"])
            assert quarantine_path.is_dir()
            quarantine_paths.append(quarantine_path)
            yield quarantine

    monkeypatch.setattr(
        rewrite_validate,
        "temporary_git_object_environment",
        tracked_quarantine,
    )

    rewrite_validate.command_rewrite_validate(
        str(repository.plan),
        resolutions_path=str(repository.workspace),
        porcelain=True,
    )

    report = json.loads(capsys.readouterr().out)
    complete_sha256 = hashlib.sha256(
        (repository.workspace / "complete.json").read_bytes()
    ).hexdigest()
    assert report["range"]["final_tree"] == repository.final_tree
    assert report["summary"]["resolved_outputs"] == 2
    assert report["resolution"] == {
        "workspace": str(repository.workspace),
        "complete_sha256": complete_sha256,
        "resolved_outputs": 2,
    }
    assert len(quarantine_paths) == 1
    assert not quarantine_paths[0].exists()
    assert _git("for-each-ref", "--format=%(refname) %(objectname)") == refs_before
    _assert_candidate_objects_absent(repository)


def test_completed_workspace_ignores_replace_refs_installed_after_plan_validation(
    resolved_repository,
    monkeypatch,
    capsys,
):
    repository = resolved_repository
    first_commit = repository.plan_record["snapshot"]["range"]["commits_oldest_first"][
        0
    ]
    assert isinstance(first_commit, str)
    source_tree = _git("rev-parse", f"{first_commit}^{{tree}}")
    replacement_tree = repository.final_tree
    create_quarantine = rewrite_validate.temporary_git_object_environment

    @contextmanager
    def replace_racing_quarantine(*, disable_replace_objects=False):
        _git("replace", source_tree, replacement_tree)
        try:
            with create_quarantine(
                disable_replace_objects=disable_replace_objects
            ) as quarantine:
                assert quarantine.environment()["GIT_NO_REPLACE_OBJECTS"] == "1"
                yield quarantine
        finally:
            _git("replace", "-d", source_tree)

    monkeypatch.setattr(
        rewrite_validate,
        "temporary_git_object_environment",
        replace_racing_quarantine,
    )

    rewrite_validate.command_rewrite_validate(
        str(repository.plan),
        resolutions_path=str(repository.workspace),
        porcelain=True,
    )

    report = json.loads(capsys.readouterr().out)
    assert report["valid"] is True
    assert report["range"]["final_tree"] == repository.final_tree
    assert _git("replace", "--list") == ""
    _assert_candidate_objects_absent(repository)


def test_raw_plan_byte_change_breaks_workspace_digest_binding(resolved_repository):
    repository = resolved_repository
    repository.plan.write_bytes(repository.plan.read_bytes() + b"\n")

    with pytest.raises(CommandError, match="workspace.json.*immutable binding"):
        rewrite_validate.command_rewrite_validate(
            str(repository.plan),
            resolutions_path=str(repository.workspace),
        )

    _assert_candidate_objects_absent(repository)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing", "missing complete.json"),
        ("incomplete", "output 1 has not been accepted"),
        ("changed", "complete.json does not match its immutable binding"),
    ],
)
def test_missing_incomplete_or_changed_completion_is_rejected(
    resolved_repository,
    mutation,
    message,
):
    repository = resolved_repository
    complete = repository.workspace / "complete.json"
    if mutation == "missing":
        complete.unlink()
    elif mutation == "incomplete":
        (repository.completion.first_output / "receipt.json").unlink()
    else:
        complete.write_bytes(complete.read_bytes() + b"\n")

    with pytest.raises(CommandError, match=message):
        rewrite_validate.command_rewrite_validate(
            str(repository.plan),
            resolutions_path=str(repository.workspace),
        )

    _assert_candidate_objects_absent(repository)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("result", "result.json changed after acceptance"),
        ("receipt", "receipt.json is not authentic"),
        ("artifact", "expected size and SHA-256"),
    ],
)
def test_tampered_result_receipt_or_artifact_is_rejected_without_leakage(
    resolved_repository,
    mutation,
    message,
):
    repository = resolved_repository
    output = repository.completion.first_output
    if mutation == "result":
        result = output / "result.json"
        result.write_bytes(result.read_bytes() + b"\n")
    elif mutation == "receipt":
        receipt_path = output / "receipt.json"
        receipt = _metadata(receipt_path)
        receipt["output_tree"] = "0" * 40
        _write_json(receipt_path, receipt)
    else:
        result = _metadata(output / "result.json")
        artifact = output / "results" / result["paths"][0]["artifact"]
        artifact.write_bytes(b"tampered\n")
        artifact.chmod(0o600)

    with pytest.raises(CommandError, match=message):
        rewrite_validate.command_rewrite_validate(
            str(repository.plan),
            resolutions_path=str(repository.workspace),
        )

    _assert_candidate_objects_absent(repository)


@pytest.mark.parametrize("field", ["parent_tree", "output_key"])
def test_stale_result_parent_or_output_key_is_rejected(
    resolved_repository,
    field,
):
    repository = resolved_repository
    result_path = repository.completion.first_output / "result.json"
    result = _metadata(result_path)
    result[field] = "0" * 40
    _write_json(result_path, result)

    with pytest.raises(CommandError, match="binding fields do not match request"):
        rewrite_validate.command_rewrite_validate(
            str(repository.plan),
            resolutions_path=str(repository.workspace),
        )

    _assert_candidate_objects_absent(repository)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("extra", "workspace root.*unexpected"),
        ("symlink", "must not contain.*symlinks"),
        ("directory-mode", "permissions must be 0700"),
        ("file-mode", "permissions must be 0600"),
    ],
)
def test_extra_symlinked_or_nonprivate_workspace_state_is_rejected(
    resolved_repository,
    mutation,
    message,
):
    repository = resolved_repository
    if mutation == "extra":
        unexpected = repository.workspace / "unexpected"
        unexpected.write_bytes(b"extra\n")
        unexpected.chmod(0o600)
    elif mutation == "symlink":
        complete = repository.workspace / "complete.json"
        target = repository.root / "complete-target.json"
        shutil.copyfile(complete, target)
        target.chmod(0o600)
        complete.unlink()
        complete.symlink_to(target)
    elif mutation == "directory-mode":
        repository.workspace.chmod(0o755)
    else:
        (repository.workspace / "complete.json").chmod(0o644)

    with pytest.raises(CommandError, match=message):
        rewrite_validate.command_rewrite_validate(
            str(repository.plan),
            resolutions_path=str(repository.workspace),
        )

    _assert_candidate_objects_absent(repository)


def test_exact_plan_rejects_workspace_before_inspecting_its_path(
    resolved_repository,
    monkeypatch,
):
    repository = resolved_repository
    exact_plan = repository.root / "exact-plan.json"
    exact_record = json.loads(json.dumps(repository.plan_record))
    for output in exact_record["plan"]["outputs"]:
        output["materialization"] = "EXACT"
    _write_json(exact_plan, exact_record)

    def reject_path_inspection(_workspace_path: str) -> Path:
        raise AssertionError("gratuitous workspace path was inspected")

    monkeypatch.setattr(
        "git_stage_batch.history.resolution_workspace._absolute_workspace_path",
        reject_path_inspection,
    )

    with pytest.raises(CommandError, match="does not contain any RESOLVED outputs"):
        rewrite_validate.command_rewrite_validate(
            str(exact_plan),
            resolutions_path=str(repository.root / "hostile\n\x1bpath"),
        )


def test_resolved_plan_without_workspace_retains_actionable_error(
    resolved_repository,
):
    with pytest.raises(
        CommandError,
        match="Rewrite output 1 requires an explicit resolution workspace",
    ):
        rewrite_validate.command_rewrite_validate(str(resolved_repository.plan))


def test_human_resolution_path_and_digest_are_terminal_safe(
    resolved_repository,
    capsys,
):
    repository = resolved_repository
    unsafe_component = "resolution\n\x1b[31m"
    workspace = repository.root / unsafe_component
    _complete_workspace(repository.plan, workspace)

    rewrite_validate.command_rewrite_validate(
        str(repository.plan),
        resolutions_path=str(workspace),
    )

    output = capsys.readouterr().out
    complete_sha256 = hashlib.sha256(
        (workspace / "complete.json").read_bytes()
    ).hexdigest()
    assert str(workspace) not in output
    assert "resolution\\n\\u001b[31m" in output
    assert f"Resolution completion SHA-256: {complete_sha256}" in output


def test_completed_workspace_authenticates_in_sha256_repository(
    tmp_path,
    monkeypatch,
    capsys,
):
    monkeypatch.chdir(tmp_path)
    initialized = subprocess.run(
        ["git", "init", "-b", "topic", "--object-format=sha256"],
        check=False,
        capture_output=True,
        text=True,
    )
    if initialized.returncode != 0:
        pytest.skip("installed Git does not support SHA-256 repositories")
    _git("config", "user.name", "Test User")
    _git("config", "user.email", "test@example.com")
    source = tmp_path / "example.txt"
    source.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    _git("add", "example.txt")
    _git("commit", "-m", "Base")
    base = _git("rev-parse", "HEAD")
    source.write_text("alpha topic\nbeta\ngamma\n", encoding="utf-8")
    _git("commit", "-am", "Change alpha")
    source.write_text("alpha topic\nbeta\ngamma topic\n", encoding="utf-8")
    _git("commit", "-am", "Change gamma")
    tip = _git("rev-parse", "HEAD")
    final_tree = _git("rev-parse", "HEAD^{tree}")
    assert _git("rev-parse", "--show-object-format") == "sha256"
    assert all(len(object_id) == 64 for object_id in (base, tip, final_tree))

    record = history_plan_document_record(acquire_history_plan_document(base))
    for output in record["plan"]["outputs"]:
        output["materialization"] = "RESOLVED"
    plan = tmp_path / "plan.json"
    _write_json(plan, record)
    workspace = tmp_path / "resolution"
    completion = _complete_workspace(plan, workspace)
    repository = SimpleNamespace(completion=completion)
    assert len(completion.candidate_blob) == 64
    assert len(completion.candidate_tree) == 64
    _assert_candidate_objects_absent(repository)

    rewrite_validate.command_rewrite_validate(
        str(plan),
        resolutions_path=str(workspace),
        porcelain=True,
    )

    report = json.loads(capsys.readouterr().out)
    complete_sha256 = hashlib.sha256(
        (workspace / "complete.json").read_bytes()
    ).hexdigest()
    assert report["valid"] is True
    assert report["range"] == {
        "base": base,
        "tip": tip,
        "final_tree": final_tree,
    }
    assert report["resolution"] == {
        "workspace": str(workspace),
        "complete_sha256": complete_sha256,
        "resolved_outputs": 2,
    }
    _assert_candidate_objects_absent(repository)
