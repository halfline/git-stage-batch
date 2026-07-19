"""Tests for non-mutating batch metadata diagnostics."""

from __future__ import annotations

import json
import subprocess

import pytest
import git_stage_batch.utils.git_command as git_command_module

from git_stage_batch.commands.validate import command_validate_batches
from git_stage_batch.commands.new import command_new_batch
from git_stage_batch.utils.file_io import write_text_file_contents
from git_stage_batch.utils.paths import get_batch_metadata_file_path
from git_stage_batch.exceptions import CommandError


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
    (tmp_path / "README").write_text("initial\n")
    subprocess.run(["git", "add", "README"], check=True)
    subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)
    return tmp_path


def _store_git_blob(content: bytes) -> str:
    return subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        input=content,
        check=True,
        capture_output=True,
    ).stdout.decode("ascii").strip()


def _store_git_tree_entry(
    *,
    mode: str,
    object_type: str,
    object_id: str,
    path: str,
) -> str:
    return subprocess.run(
        ["git", "mktree"],
        input=f"{mode} {object_type} {object_id}\t{path}\n",
        text=True,
        check=True,
        capture_output=True,
    ).stdout.strip()


def _replace_batch_state_entry(
    batch_name: str,
    *,
    mode: str,
    object_type: str,
    object_id: str,
    path: str = "batch.json",
) -> None:
    tree = _store_git_tree_entry(
        mode=mode,
        object_type=object_type,
        object_id=object_id,
        path=path,
    )
    commit = subprocess.run(
        ["git", "commit-tree", tree, "-m", "Malformed test batch state"],
        text=True,
        check=True,
        capture_output=True,
    ).stdout.strip()
    subprocess.run(
        [
            "git",
            "update-ref",
            f"refs/git-stage-batch/state/{batch_name}",
            commit,
        ],
        check=True,
    )


def test_validate_reports_current_metadata_without_mutation(temp_git_repo, capsys):
    command_new_batch("current")
    before = subprocess.run(
        ["git", "rev-parse", "refs/git-stage-batch/state/current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    command_validate_batches(porcelain=True)

    report = json.loads(capsys.readouterr().out)
    assert report["batches"][0]["status"] == "ok"
    assert report["batches"][0]["schema_version"] == 1
    after = subprocess.run(
        ["git", "rev-parse", "refs/git-stage-batch/state/current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert after == before


def test_validate_previews_legacy_migration(temp_git_repo, capsys):
    subprocess.run(["git", "update-ref", "refs/batches/legacy", "HEAD"], check=True)
    metadata_path = get_batch_metadata_file_path("legacy")
    metadata_path.parent.mkdir(parents=True)
    write_text_file_contents(
        metadata_path,
        json.dumps({
            "note": "Legacy",
            "created_at": "",
            "baseline": subprocess.run(
                ["git", "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            "files": {},
        }),
    )

    command_validate_batches(porcelain=True)

    report = json.loads(capsys.readouterr().out)["batches"][0]
    assert report["status"] == "ok"
    assert report["migration_required"] is True
    assert json.loads(metadata_path.read_text()).get("schema_version") is None


def test_validate_reports_invalid_orphaned_legacy_name(temp_git_repo, capsys):
    metadata_path = get_batch_metadata_file_path("invalid^name")
    metadata_path.parent.mkdir(parents=True)
    write_text_file_contents(metadata_path, "{}")

    with pytest.raises(CommandError):
        command_validate_batches(porcelain=True)

    report = json.loads(capsys.readouterr().out)["batches"][0]
    assert report["status"] == "error"
    assert "Git ref naming rules" in report["errors"][0]


def test_validate_rejects_non_blob_state_with_compatibility_residue(
    temp_git_repo,
    capsys,
):
    """A compatibility file must not mask malformed authoritative state."""
    command_new_batch("malformed")
    state_payload = subprocess.run(
        [
            "git",
            "show",
            "refs/git-stage-batch/state/malformed:batch.json",
        ],
        text=True,
        check=True,
        capture_output=True,
    ).stdout
    metadata_path = get_batch_metadata_file_path("malformed")
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_file_contents(metadata_path, state_payload)

    nested_blob = _store_git_blob(b"not metadata\n")
    nested_tree = _store_git_tree_entry(
        mode="100644",
        object_type="blob",
        object_id=nested_blob,
        path="value",
    )
    _replace_batch_state_entry(
        "malformed",
        mode="040000",
        object_type="tree",
        object_id=nested_tree,
    )
    capsys.readouterr()

    with pytest.raises(CommandError):
        command_validate_batches(porcelain=True)

    report = json.loads(capsys.readouterr().out)["batches"][0]
    assert report["source"] == "state-ref"
    assert report["status"] == "error"
    assert report["residue"]["class"] == "unverifiable_residue"
    assert "tree, not a blob" in report["errors"][0]


def test_validate_rejects_missing_state_path_with_compatibility_residue(
    temp_git_repo,
    capsys,
):
    """A compatibility file must not mask a missing authoritative payload."""
    command_new_batch("missing-state")
    state_payload = subprocess.run(
        [
            "git",
            "show",
            "refs/git-stage-batch/state/missing-state:batch.json",
        ],
        text=True,
        check=True,
        capture_output=True,
    ).stdout
    metadata_path = get_batch_metadata_file_path("missing-state")
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_file_contents(metadata_path, state_payload)

    other_blob = _store_git_blob(b"not the authoritative payload\n")
    _replace_batch_state_entry(
        "missing-state",
        mode="100644",
        object_type="blob",
        object_id=other_blob,
        path="other.json",
    )
    capsys.readouterr()

    with pytest.raises(CommandError):
        command_validate_batches(porcelain=True)

    report = json.loads(capsys.readouterr().out)["batches"][0]
    assert report["source"] == "state-ref"
    assert report["status"] == "error"
    assert report["residue"]["class"] == "unverifiable_residue"
    assert "missing path 'batch.json'" in report["errors"][0]


def test_validate_reports_non_utf8_state_payload(temp_git_repo, capsys):
    """Invalid state bytes should become a diagnostic instead of escaping."""
    command_new_batch("invalid-bytes")
    state_payload = subprocess.run(
        [
            "git",
            "show",
            "refs/git-stage-batch/state/invalid-bytes:batch.json",
        ],
        text=True,
        check=True,
        capture_output=True,
    ).stdout
    metadata_path = get_batch_metadata_file_path("invalid-bytes")
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_file_contents(metadata_path, state_payload)
    invalid_blob = _store_git_blob(b"\x80")
    _replace_batch_state_entry(
        "invalid-bytes",
        mode="100644",
        object_type="blob",
        object_id=invalid_blob,
    )
    capsys.readouterr()

    with pytest.raises(CommandError):
        command_validate_batches(porcelain=True)

    report = json.loads(capsys.readouterr().out)["batches"][0]
    assert report["source"] == "state-ref"
    assert report["status"] == "error"
    assert report["residue"]["class"] == "unverifiable_residue"
    assert "not valid JSON" in report["errors"][0]


def test_validate_batches_bounds_git_object_subprocesses(
    temp_git_repo,
    capsys,
    monkeypatch,
):
    """Validation should inspect many batches through fixed bulk Git calls."""
    for index in range(16):
        command_new_batch(f"batch-{index}")
    capsys.readouterr()

    original_run = git_command_module.run_command
    original_stream = git_command_module.stream_command
    cat_file_commands = []
    check_ref_commands = []

    def counting_run(arguments, *args, **kwargs):
        if arguments[:2] == ["git", "cat-file"]:
            cat_file_commands.append(tuple(arguments[1:]))
        if arguments[:2] == ["git", "check-ref-format"]:
            check_ref_commands.append(tuple(arguments[1:]))
        return original_run(arguments, *args, **kwargs)

    def counting_stream(arguments, *args, **kwargs):
        if arguments[:2] == ["git", "cat-file"]:
            cat_file_commands.append(tuple(arguments[1:]))
        yield from original_stream(arguments, *args, **kwargs)

    monkeypatch.setattr(git_command_module, "run_command", counting_run)
    monkeypatch.setattr(git_command_module, "stream_command", counting_stream)

    command_validate_batches(porcelain=True)

    assert len(json.loads(capsys.readouterr().out)["batches"]) == 16
    assert check_ref_commands == []
    assert cat_file_commands == [
        ("cat-file", "--batch-check"),
        ("cat-file", "--batch"),
        ("cat-file", "--batch-check"),
    ]
