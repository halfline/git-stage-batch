"""Tests for stable mutation-target identities."""

import pytest

from git_stage_batch.data.file_target_identity import (
    IndexIdentity,
    WorktreeIdentity,
    capture_worktree_identity,
    index_identity_from_entry,
)
from git_stage_batch.data.index_entries import IndexEntry
from git_stage_batch.exceptions import RepositoryDataInvalid
import git_stage_batch.data.file_target_identity as identities


def test_regular_identity_spools_the_hashed_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        identities,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )
    target = tmp_path / "file.txt"
    target.write_bytes(b"alpha\nbeta\n")
    artifact = tmp_path / "artifact"

    identity = capture_worktree_identity(
        "file.txt",
        content_artifact_path=artifact,
    )

    assert identity == WorktreeIdentity(
        exists=True,
        kind="regular",
        mode=0o644,
        size=11,
        digest="e49c81e2d2f84e259d40e2fb8192f3bcd198b355184845d76d8f58807d0d78ee",
    )
    assert artifact.read_bytes() == b"alpha\nbeta\n"


def test_symlink_identity_hashes_the_link_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(
        identities,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )
    (tmp_path / "link").symlink_to("target")
    artifact = tmp_path / "artifact"

    identity = capture_worktree_identity(
        "link",
        content_artifact_path=artifact,
    )

    assert identity.exists is True
    assert identity.kind == "symlink"
    assert identity.size == len(b"target")
    assert artifact.read_bytes() == b"target"


def test_missing_identity_writes_an_empty_text_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(
        identities,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )
    artifact = tmp_path / "artifact"

    identity = capture_worktree_identity(
        "missing.txt",
        content_artifact_path=artifact,
    )

    assert identity == WorktreeIdentity(False, "missing", None, None, None)
    assert artifact.read_bytes() == b""


def test_obstructed_identity_distinguishes_a_non_directory_parent(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        identities,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )
    (tmp_path / "parent").write_text("not a directory")
    artifact = tmp_path / "artifact"

    identity = capture_worktree_identity(
        "parent/file.txt",
        content_artifact_path=artifact,
    )

    assert identity == WorktreeIdentity(
        False,
        "obstructed",
        None,
        None,
        None,
    )
    assert artifact.read_bytes() == b""


def test_directory_text_capture_preserves_the_worktree_kind_diagnostic(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        identities,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )
    (tmp_path / "directory").mkdir()

    with pytest.raises(
        RepositoryDataInvalid,
        match="Unsupported working-tree path kind: directory",
    ):
        capture_worktree_identity(
            "directory",
            content_artifact_path=tmp_path / "artifact",
        )


def test_directory_identity_uses_the_git_command_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(
        identities,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )
    submodule = tmp_path / "sub"
    submodule.mkdir()
    calls = []
    monkeypatch.setattr(
        identities,
        "is_git_repository_root_path",
        lambda path: path == submodule,
    )

    def stream_git(arguments, **kwargs):
        calls.append((arguments, kwargs))
        yield b"state\0"

    monkeypatch.setattr(identities, "stream_git_command_bytes", stream_git)

    identity = capture_worktree_identity("sub")

    assert identity.exists is True
    assert identity.kind == "directory"
    assert identity.digest is not None
    assert [call[0] for call in calls] == [
        ["rev-parse", "--verify", "HEAD^{commit}"],
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
    ]
    assert {call[1]["cwd"] for call in calls} == {str(submodule)}
    assert all(call[1]["requires_index_lock"] is False for call in calls)


def test_ordinary_directory_identity_does_not_digest_parent_repo_state(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        identities,
        "get_git_repository_root_path",
        lambda: tmp_path,
    )
    directory = tmp_path / "directory"
    directory.mkdir()
    monkeypatch.setattr(
        identities,
        "is_git_repository_root_path",
        lambda path: False,
    )
    monkeypatch.setattr(
        identities,
        "stream_git_command_bytes",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("ordinary directory invoked Git")
        ),
    )

    identity = capture_worktree_identity("directory")

    assert identity.exists is True
    assert identity.kind == "directory"
    assert identity.digest is None


def test_index_identity_converts_stage_zero_entries():
    assert index_identity_from_entry(None) == IndexIdentity(None, None)
    assert index_identity_from_entry(
        IndexEntry("100755", "a" * 40)
    ) == IndexIdentity("100755", "a" * 40)
