"""Tests for undo checkpoint restoration helpers."""

from __future__ import annotations

import os
import pytest
import subprocess

from git_stage_batch.data import undo_restore, undo_worktree
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.git_object_io import create_git_blob


def test_restore_worktree_rejects_missing_saved_blob(tmp_path, monkeypatch):
    """An incomplete checkpoint must not silently preserve post-operation bytes."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    monkeypatch.setattr(undo_restore, "_tree_entries", lambda *_args: [])

    with pytest.raises(CommandError, match="missing worktree content for file.txt"):
        undo_restore.restore_worktree(
            "checkpoint",
            {
                "worktree_paths": [
                    {
                        "path": "file.txt",
                        "exists": True,
                        "mode": "100644",
                    }
                ]
            },
        )


@pytest.mark.parametrize(
    ("saved_mode", "saved_content", "current_kind"),
    [
        ("100644", b"regular bytes\n", "symlink"),
        ("120000", b"saved-target", "regular"),
    ],
)
def test_restore_tree_paths_uses_saved_git_mode(
    tmp_path,
    monkeypatch,
    saved_mode,
    saved_content,
    current_kind,
):
    """Restore replaces the current path type instead of following it."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    blob_sha = create_git_blob([saved_content])
    monkeypatch.setattr(
        undo_restore,
        "_tree_entries",
        lambda *_args: [(saved_mode, blob_sha, "state/entry")],
    )
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    target = target_dir / "entry"
    referent = tmp_path / "referent"
    referent.write_text("untouched\n")
    if current_kind == "symlink":
        target.symlink_to(referent)
    else:
        target.write_text("current\n")

    undo_restore.restore_tree_paths(
        "checkpoint",
        prefix="state",
        target_dir=target_dir,
        tracked_paths=["entry"],
    )

    if saved_mode == "120000":
        assert target.is_symlink()
        assert os.readlink(target) == "saved-target"
    else:
        assert not target.is_symlink()
        assert target.read_bytes() == saved_content
        assert referent.read_text() == "untouched\n"


def test_restore_gitlink_recorded_absent_removes_post_operation_directory(
    tmp_path,
    monkeypatch,
):
    """Undo removes a gitlink worktree that was absent in the before-image."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    target = tmp_path / "sub"
    target.mkdir()
    (target / "leftover").write_text("post-operation\n")
    monkeypatch.setattr(undo_restore, "_tree_entries", lambda *_args: [])

    undo_restore.restore_worktree(
        "checkpoint",
        {
            "worktree_paths": [
                {
                    "path": "sub",
                    "kind": "gitlink",
                    "exists": False,
                    "worktree_oid": None,
                }
            ]
        },
    )

    assert not target.exists()


def test_restore_legacy_gitlink_without_worktree_removes_directory(
    tmp_path,
    monkeypatch,
):
    """Legacy index-based existence does not preserve a later worktree."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    target = tmp_path / "sub"
    target.mkdir()
    (target / "leftover").write_text("post-operation\n")
    monkeypatch.setattr(undo_restore, "_tree_entries", lambda *_args: [])

    undo_restore.restore_worktree(
        "checkpoint",
        {
            "worktree_paths": [
                {
                    "path": "sub",
                    "kind": "gitlink",
                    "exists": True,
                    "worktree_oid": None,
                    "archive": False,
                }
            ]
        },
    )

    assert not target.exists()


def test_restore_gitlink_without_head_uses_saved_archive(tmp_path, monkeypatch):
    """A present gitlink directory without a commit is restored from its archive."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    before = tmp_path / "before"
    before.mkdir()
    (before / "private.txt").write_text("before\n")
    archive_blob = undo_worktree._create_directory_archive_blob(before)
    target = tmp_path / "sub"
    target.mkdir()
    (target / "private.txt").write_text("after\n")
    monkeypatch.setattr(
        undo_restore,
        "_tree_entries",
        lambda *_args: [("100644", archive_blob, "worktree/sub")],
    )

    undo_restore.restore_worktree(
        "checkpoint",
        {
            "worktree_paths": [
                {
                    "path": "sub",
                    "kind": "gitlink",
                    "exists": True,
                    "worktree_oid": None,
                    "archive": True,
                }
            ]
        },
    )

    assert (target / "private.txt").read_text() == "before\n"


def test_restore_archived_gitlink_reapplies_dirty_worktree(tmp_path, monkeypatch):
    """An archive restores dirty bytes when a submodule's Git dir is external."""
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init"], cwd=source, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=source,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=source,
        check=True,
    )
    source_file = source / "file.txt"
    source_file.write_text("base\n")
    subprocess.run(["git", "add", "file.txt"], cwd=source, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial nested commit"],
        cwd=source,
        check=True,
        capture_output=True,
    )
    worktree_oid = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init"], cwd=repository, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            str(source),
            "sub",
        ],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-am", "Add submodule"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    monkeypatch.chdir(repository)
    target = repository / "sub"
    nested_file = target / "file.txt"
    nested_file.write_text("saved dirty bytes\n")
    archive_blob = undo_worktree._create_directory_archive_blob(target)

    source_file.write_text("second commit\n")
    subprocess.run(
        ["git", "commit", "-am", "Update nested file"],
        cwd=source,
        check=True,
        capture_output=True,
    )
    post_operation_oid = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "fetch", str(source), post_operation_oid],
        cwd=target,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "--detach", "--force", post_operation_oid],
        cwd=target,
        check=True,
        capture_output=True,
    )
    nested_file.write_text("post-operation bytes\n")
    monkeypatch.setattr(
        undo_restore,
        "_tree_entries",
        lambda *_args: [("100644", archive_blob, "worktree/sub")],
    )

    undo_restore.restore_worktree(
        "checkpoint",
        {
            "worktree_paths": [
                {
                    "path": "sub",
                    "kind": "gitlink",
                    "exists": True,
                    "worktree_oid": worktree_oid,
                    "archive": True,
                }
            ]
        },
    )

    assert nested_file.read_text() == "saved dirty bytes\n"
    assert (target / ".git").is_file()
    assert subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == worktree_oid


def test_restore_gitlink_refuses_superproject_walk_up(tmp_path, monkeypatch):
    """Legacy worktree IDs cannot redirect undo checkout to the superproject."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        check=True,
    )
    (tmp_path / "tracked.txt").write_text("tracked\n")
    subprocess.run(["git", "add", "tracked.txt"], check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        check=True,
        capture_output=True,
    )
    head_oid = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    symbolic_head = subprocess.run(
        ["git", "symbolic-ref", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (tmp_path / "sub").mkdir()
    monkeypatch.setattr(undo_restore, "_tree_entries", lambda *_args: [])

    with pytest.raises(CommandError, match="not a standalone Git repository"):
        undo_restore.restore_worktree(
            "checkpoint",
            {
                "worktree_paths": [
                    {
                        "path": "sub",
                        "kind": "gitlink",
                        "exists": True,
                        "worktree_oid": head_oid,
                        "archive": False,
                    }
                ]
            },
        )

    assert subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == head_oid
    assert subprocess.run(
        ["git", "symbolic-ref", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == symbolic_head


def test_restore_legacy_clean_gitlink_initializes_missing_submodule(
    tmp_path,
    monkeypatch,
):
    """A missing legacy clean worktree gets one registered-submodule fallback."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    target = tmp_path / "sub"
    update_calls = []

    def initialize_submodule(paths, **kwargs):
        update_calls.append((paths, kwargs))
        subprocess.run(["git", "init", str(target)], check=True, capture_output=True)
        return subprocess.CompletedProcess(["git", "submodule", "update"], 0, "", "")

    checkout_calls = []
    monkeypatch.setattr(undo_restore, "git_submodule_update_checkout", initialize_submodule)
    monkeypatch.setattr(
        undo_restore,
        "git_checkout_detached",
        lambda oid, **kwargs: (
            checkout_calls.append((oid, kwargs))
            or subprocess.CompletedProcess(["git", "checkout"], 0, "", "")
        ),
    )
    monkeypatch.setattr(undo_restore, "_tree_entries", lambda *_args: [])

    undo_restore.restore_worktree(
        "checkpoint",
        {
            "worktree_paths": [
                {
                    "path": "sub",
                    "kind": "gitlink",
                    "exists": True,
                    "worktree_oid": "1" * 40,
                    "archive": False,
                }
            ]
        },
    )

    assert update_calls == [
        (["sub"], {"cwd": str(tmp_path), "check": False})
    ]
    assert checkout_calls[0][0] == "1" * 40


def test_restore_directory_archive_allows_self_created_symlinks(tmp_path, monkeypatch):
    """The explicit tar filter remains compatible with archived symlinks."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    source = tmp_path / "source"
    source.mkdir()
    (source / "link").symlink_to("/outside")
    archive_blob = undo_worktree._create_directory_archive_blob(source)
    target = tmp_path / "nested"

    undo_restore._restore_directory_archive(
        target,
        ("100644", archive_blob),
        file_path="path/to/nested",
    )

    assert (target / "link").is_symlink()
    assert os.readlink(target / "link") == "/outside"


def test_extract_directory_archive_supports_early_python_310(monkeypatch, tmp_path):
    """Runtimes predating extraction filters use the trusted-archive fallback."""
    calls = []

    class LegacyArchive:
        def extractall(self, path):
            calls.append(path)

    monkeypatch.delattr(undo_restore.tarfile, "tar_filter")

    undo_restore._extract_directory_archive(LegacyArchive(), tmp_path)

    assert calls == [tmp_path]


def test_restore_directory_archive_error_keeps_repository_path(tmp_path):
    """Missing archive diagnostics identify the scoped repository path."""
    with pytest.raises(CommandError, match="nested/path"):
        undo_restore._restore_directory_archive(
            tmp_path / "path",
            None,
            file_path="nested/path",
        )


def test_restore_intent_to_add_entries_checks_git_failures(tmp_path, monkeypatch):
    """intent-to-add restoration does not silently accept failed index commands."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    (tmp_path / "new.txt").write_text("content\n")
    update_calls = []
    add_calls = []
    monkeypatch.setattr(
        undo_restore,
        "git_update_index",
        lambda **kwargs: update_calls.append(kwargs),
    )
    monkeypatch.setattr(
        undo_restore,
        "git_add_paths",
        lambda paths, **kwargs: add_calls.append((paths, kwargs)),
    )

    undo_restore.restore_intent_to_add_entries(["new.txt"])

    assert update_calls == [{"file_path": "new.txt", "force_remove": True}]
    assert add_calls == [(["new.txt"], {"intent_to_add": True})]
