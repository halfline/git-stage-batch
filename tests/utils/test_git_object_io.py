"""Tests for Git object IO helpers."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from git_stage_batch.data.undo import snapshots as undo_snapshots
from git_stage_batch.utils.git_command import run_git_command
from git_stage_batch.utils.git_index import git_write_tree, temp_git_index
from git_stage_batch.utils.git_object_io import (
    create_git_blob,
    create_git_blobs_from_paths,
    get_empty_git_tree_object_id,
    get_git_object_type,
    list_git_tree_blobs,
    list_git_tree_entries,
    read_git_blobs_as_bytes,
    resolve_git_objects,
    stream_git_blobs,
    temporary_git_object_environment,
)
from git_stage_batch.utils import git_object_io


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        check=True,
        cwd=repo,
        capture_output=True,
    )

    (repo / "README.md").write_text("# Test\n")
    subprocess.run(
        ["git", "add", "README.md"], check=True, cwd=repo, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        check=True,
        cwd=repo,
        capture_output=True,
    )

    return repo


def test_empty_tree_helper_returns_a_tree_object(temp_git_repo):
    """The shared empty-tree helper should create a repository tree object."""
    object_id = get_empty_git_tree_object_id()

    assert run_git_command(["cat-file", "-t", object_id]).stdout.strip() == "tree"
    assert run_git_command(["ls-tree", object_id]).stdout == ""


def test_create_git_blobs_from_paths_hashes_path_bytes(temp_git_repo):
    """Path-based blob creation should store exact file bytes."""
    files = [
        temp_git_repo / "alpha.txt",
        temp_git_repo / "nested" / "beta.bin",
        temp_git_repo / "name,with,commas.txt",
    ]
    files[1].parent.mkdir()
    files[0].write_bytes(b"alpha\n")
    files[1].write_bytes(b"\x00\x01beta\n")
    files[2].write_bytes(b"comma\n")

    blobs = create_git_blobs_from_paths([files[0], files[1], files[0], files[2]])

    assert set(blobs) == set(files)
    for file_path in files:
        result = run_git_command(
            ["cat-file", "blob", blobs[file_path]],
            text_output=False,
            requires_index_lock=False,
        )
        assert result.stdout == file_path.read_bytes()


def test_create_git_blob_streams_path_filtered_input(monkeypatch):
    """Supplying a clean-filter path does not materialize the input chunks."""
    chunks = iter([b"first\n", b"second\n"])
    observed = {}

    def fake_stream_git_command(arguments, stdin_chunks, **kwargs):
        observed["arguments"] = arguments
        observed["same_iterator"] = stdin_chunks is chunks
        observed["content"] = b"".join(stdin_chunks)
        yield b"abc123\n"

    monkeypatch.setattr(
        git_object_io,
        "stream_git_command",
        fake_stream_git_command,
    )

    blob = create_git_blob(chunks, path="new file.txt")

    assert blob == "abc123"
    assert observed == {
        "arguments": [
            "hash-object",
            "-w",
            "--path=new file.txt",
            "--stdin",
        ],
        "same_iterator": True,
        "content": b"first\nsecond\n",
    }


def test_read_git_blobs_as_bytes_accepts_revision_paths(temp_git_repo):
    """Batch object reads should support Git revision:path expressions."""
    file_path = temp_git_repo / "unicodé.txt"
    file_path.write_text("accented\n")
    subprocess.run(["git", "add", file_path.name], check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add unicode path"],
        check=True,
        capture_output=True,
    )

    blobs = read_git_blobs_as_bytes([f"HEAD:{file_path.name}"])

    assert blobs[f"HEAD:{file_path.name}"] == b"accented\n"


def test_read_git_blobs_as_bytes_can_ignore_non_blob_objects(temp_git_repo):
    """Tolerant batch readers should skip wrong object types without desync."""
    blob = create_git_blob([b"content\n"])
    tree = run_git_command(["write-tree"]).stdout.strip()
    missing = "HEAD:path does not exist"

    with pytest.raises(RuntimeError, match="Unexpected git cat-file"):
        read_git_blobs_as_bytes([tree, blob])

    blobs = read_git_blobs_as_bytes(
        [tree, missing, blob],
        ignore_non_blobs=True,
    )

    assert tree not in blobs
    assert missing not in blobs
    assert blobs[blob] == b"content\n"


def test_resolve_git_objects_canonicalizes_revision_paths(temp_git_repo):
    """Object checks should expose the shared canonical blob identity."""
    blob = run_git_command(["rev-parse", "HEAD:README.md"]).stdout.strip()
    tree = run_git_command(["write-tree"]).stdout.strip()
    expressions = ["HEAD:README.md", blob, tree, "missing-object"]

    resolved = resolve_git_objects(expressions)

    assert resolved["HEAD:README.md"].object_id == blob
    assert resolved[blob].object_id == blob
    assert resolved[blob].object_type == "blob"
    assert resolved[blob].size == len(b"# Test\n")
    assert resolved[tree].object_type == "tree"
    assert "missing-object" not in resolved


def test_stream_git_blobs_preserves_binary_object_boundaries(
    temp_git_repo,
):
    """Streaming batch reads should yield exact payloads one object at a time."""
    first = create_git_blob([b"first\x00payload\n"])
    second = create_git_blob([b"second\nline\n"])

    blobs = [
        (blob.requested_name, blob.object_id, b"".join(blob.content_chunks))
        for blob in stream_git_blobs([first, second])
    ]

    assert blobs == [
        (first, first, b"first\x00payload\n"),
        (second, second, b"second\nline\n"),
    ]


def test_stream_git_blobs_does_not_materialize_large_payloads(temp_git_repo):
    """Blob streams should expose bounded chunks instead of one payload value."""
    payload_size = 256 * 1024
    blob_id = create_git_blob([b"x" * payload_size])

    streamed_size = 0
    largest_chunk = 0
    for blob in stream_git_blobs([blob_id]):
        assert blob.size == payload_size
        for chunk in blob.content_chunks:
            streamed_size += len(chunk)
            largest_chunk = max(largest_chunk, len(chunk))

    assert streamed_size == payload_size
    assert largest_chunk < payload_size


def test_quarantined_blob_requires_environment_and_does_not_leak(temp_git_repo):
    """Streamed blob IO should remain confined to its object quarantine."""
    with temporary_git_object_environment() as quarantine:
        env = quarantine.environment()
        blob_id = create_git_blob([b"quarantined payload\n"], env=env)

        assert list(stream_git_blobs([blob_id])) == []
        streamed = [
            (blob.object_id, b"".join(blob.content_chunks))
            for blob in stream_git_blobs([blob_id], env=env)
        ]
        assert streamed == [(blob_id, b"quarantined payload\n")]

    result = run_git_command(
        ["cat-file", "-e", blob_id],
        check=False,
        requires_index_lock=False,
    )
    assert result.returncode != 0


def test_quarantine_environment_cannot_be_redirected(temp_git_repo):
    """Issued quarantine capabilities must preserve their object directory."""
    with temporary_git_object_environment() as quarantine:
        issued = quarantine.environment()
        object_directory = issued["GIT_OBJECT_DIRECTORY"]
        issued["GIT_OBJECT_DIRECTORY"] = ".git/objects"

        assert not isinstance(quarantine, dict)
        assert quarantine.environment()["GIT_OBJECT_DIRECTORY"] == object_directory
        with pytest.raises(TypeError):
            dict.__setitem__(  # type: ignore[arg-type]
                quarantine,
                "GIT_OBJECT_DIRECTORY",
                ".git/objects",
            )


def test_quarantine_certifies_the_persistent_object_store(
    temp_git_repo,
    tmp_path,
    monkeypatch,
):
    """Ambient quarantine variables must not redirect later promotion writes."""
    redirected_objects = tmp_path / "redirected-objects"
    redirected_objects.mkdir()
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(redirected_objects))
    monkeypatch.setenv("GIT_ALTERNATE_OBJECT_DIRECTORIES", str(redirected_objects))
    monkeypatch.setenv("GIT_QUARANTINE_PATH", str(tmp_path / "foreign"))

    with temporary_git_object_environment() as quarantine:
        persistent = quarantine.persistent_environment()
        head = run_git_command(
            ["rev-parse", "HEAD"],
            env=persistent,
            requires_index_lock=False,
        ).stdout.strip()

        assert "GIT_OBJECT_DIRECTORY" not in persistent
        assert "GIT_ALTERNATE_OBJECT_DIRECTORIES" not in persistent
        assert "GIT_QUARANTINE_PATH" not in persistent
        assert get_git_object_type(head, env=quarantine.environment()) == "commit"
        quarantine.require_persistent_identity()


def test_quarantine_can_disable_replace_objects_and_grafts_in_every_environment(
    temp_git_repo,
    monkeypatch,
):
    base = run_git_command(
        ["rev-parse", "HEAD"],
        requires_index_lock=False,
    ).stdout.strip()
    (temp_git_repo / "README.md").write_text("# Changed\n")
    subprocess.run(["git", "commit", "-am", "Change"], check=True)
    tip = run_git_command(
        ["rev-parse", "HEAD"],
        requires_index_lock=False,
    ).stdout.strip()
    grafts = temp_git_repo / "custom-grafts"
    grafts.write_text(f"{tip}\n", encoding="ascii")
    monkeypatch.setenv("GIT_NO_REPLACE_OBJECTS", "0")
    monkeypatch.setenv("GIT_GRAFT_FILE", str(grafts))

    with temporary_git_object_environment(disable_replace_objects=True) as quarantine:
        assert quarantine.environment()["GIT_NO_REPLACE_OBJECTS"] == "1"
        assert quarantine.environment()["GIT_GRAFT_FILE"] == os.devnull
        assert quarantine.persistent_environment()["GIT_NO_REPLACE_OBJECTS"] == "1"
        assert quarantine.persistent_environment()["GIT_GRAFT_FILE"] == os.devnull
        with quarantine.pinned_environment() as environment:
            assert environment["GIT_NO_REPLACE_OBJECTS"] == "1"
            assert environment["GIT_GRAFT_FILE"] == os.devnull
            parents = run_git_command(
                ["rev-list", "--parents", "-n", "1", "HEAD"],
                env=environment,
                requires_index_lock=False,
            ).stdout.split()

    assert parents == [tip, base]


def test_quarantine_rejects_nonboolean_replace_control(temp_git_repo):
    with pytest.raises(ValueError, match="must be a boolean"):
        with temporary_git_object_environment(
            disable_replace_objects=1,  # type: ignore[arg-type]
        ):
            pass


def test_quarantine_object_directory_pin_certifies_filesystem_identity(
    temp_git_repo,
):
    with temporary_git_object_environment() as quarantine:
        object_directory = Path(quarantine.environment()["GIT_OBJECT_DIRECTORY"])
        metadata = object_directory.stat()
        expected = metadata.st_dev, metadata.st_ino

        with quarantine.pinned_quarantine_object_directory() as descriptor:
            assert (
                quarantine.require_quarantine_object_directory_identity(descriptor)
                == expected
            )
            assert (
                os.fstat(descriptor).st_dev,
                os.fstat(descriptor).st_ino,
            ) == expected


def test_quarantine_object_directory_pin_rejects_visible_inode_change(
    temp_git_repo,
):
    with temporary_git_object_environment() as quarantine:
        object_directory = Path(quarantine.environment()["GIT_OBJECT_DIRECTORY"])
        displaced = object_directory.with_name(f"{object_directory.name}-displaced")
        try:
            with pytest.raises(RuntimeError, match="quarantine identity changed"):
                with quarantine.pinned_quarantine_object_directory():
                    object_directory.rename(displaced)
                    object_directory.mkdir(mode=0o700)
        finally:
            shutil.rmtree(object_directory)
            displaced.rename(object_directory)


def test_pinned_quarantine_environment_prevents_object_path_aba_leakage(
    temp_git_repo,
):
    payload = f"candidate only for {temp_git_repo}\n".encode()
    with temporary_git_object_environment() as quarantine:
        ordinary_environment = quarantine.environment()
        persistent_environment = quarantine.persistent_environment()
        object_directory = Path(ordinary_environment["GIT_OBJECT_DIRECTORY"])
        persistent_directory = Path(
            run_git_command(
                ["rev-parse", "--git-path", "objects"],
                env=persistent_environment,
                requires_index_lock=False,
            ).stdout.strip()
        ).resolve()
        displaced = object_directory.with_name(f"{object_directory.name}-displaced")

        with quarantine.pinned_environment() as environment:
            object_directory.rename(displaced)
            object_directory.symlink_to(
                persistent_directory,
                target_is_directory=True,
            )
            try:
                environment["GIT_OBJECT_DIRECTORY"] = str(persistent_directory)
                environment["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = str(
                    persistent_directory
                )
                environment["GIT_QUARANTINE_PATH"] = str(persistent_directory)
                object_id = create_git_blob([payload], env=environment)
                tree_id = run_git_command(
                    ["mktree"],
                    stdin_chunks=[
                        f"100644 blob {object_id}\tcandidate.txt\n".encode("ascii")
                    ],
                    env=environment,
                    requires_index_lock=False,
                ).stdout.strip()
                displaced_environment = persistent_environment.copy()
                displaced_environment["GIT_OBJECT_DIRECTORY"] = str(displaced)

                assert (
                    get_git_object_type(
                        object_id,
                        env=displaced_environment,
                    )
                    == "blob"
                )
                assert (
                    get_git_object_type(
                        tree_id,
                        env=displaced_environment,
                    )
                    == "tree"
                )
                assert (
                    get_git_object_type(
                        object_id,
                        env=persistent_environment,
                    )
                    is None
                )
                assert (
                    get_git_object_type(
                        tree_id,
                        env=persistent_environment,
                    )
                    is None
                )
            finally:
                object_directory.unlink()
                displaced.rename(object_directory)

    assert get_git_object_type(object_id) is None
    assert get_git_object_type(tree_id) is None


def test_quarantined_tree_requires_environment_and_does_not_leak(temp_git_repo):
    """Tree listing should use the same temporary object environment."""
    file_path = "candidate.txt"
    with temporary_git_object_environment() as quarantine:
        env = quarantine.environment()
        blob_id = create_git_blob([b"candidate content\n"], env=env)
        tree_id = run_git_command(
            ["mktree"],
            stdin_chunks=[
                f"100644 blob {blob_id}\t{file_path}\n".encode("ascii")
            ],
            env=env,
            requires_index_lock=False,
        ).stdout.strip()

        assert list_git_tree_blobs(tree_id, [file_path]) == {}
        assert get_git_object_type(tree_id) is None
        assert get_git_object_type(tree_id, env=env) == "tree"
        entries = list_git_tree_blobs(tree_id, [file_path], env=env)
        assert entries[file_path].blob_sha == blob_id
        assert entries[file_path].mode == "100644"

    for object_id in (blob_id, tree_id):
        result = run_git_command(
            ["cat-file", "-e", object_id],
            check=False,
            requires_index_lock=False,
        )
        assert result.returncode != 0


def test_tree_entry_listing_preserves_non_blob_types_and_exact_paths(temp_git_repo):
    """Exact entry inspection should expose trees without recursing into them."""
    nested = temp_git_repo / "nested"
    nested.mkdir()
    (nested / "child.txt").write_text("child\n", encoding="utf-8")
    subprocess.run(["git", "add", "nested/child.txt"], check=True)
    tree_id = run_git_command(["write-tree"]).stdout.strip()

    entries = list_git_tree_entries(
        tree_id,
        ["nested", "nested/child.txt", "missing"],
    )

    assert set(entries) == {"nested", "nested/child.txt"}
    assert entries["nested"].object_type == "tree"
    assert entries["nested"].mode == "040000"
    assert entries["nested/child.txt"].object_type == "blob"


def test_tree_entry_listing_uses_root_relative_paths_from_subdirectory(temp_git_repo):
    """Exact path comparison must not depend on the process working directory."""
    nested = temp_git_repo / "nested"
    nested.mkdir()
    (nested / "child.txt").write_text("child\n", encoding="utf-8")
    subprocess.run(["git", "add", "nested/child.txt"], check=True)
    tree_id = run_git_command(["write-tree"]).stdout.strip()
    original_directory = Path.cwd()
    try:
        os.chdir(nested)
        entries = list_git_tree_entries(tree_id, ["nested/child.txt"])
    finally:
        os.chdir(original_directory)

    assert entries["nested/child.txt"].object_type == "blob"


def test_tree_entry_listing_fails_closed_for_missing_tree(temp_git_repo):
    """Operational lookup failure must not masquerade as an absent path."""
    with pytest.raises(subprocess.CalledProcessError):
        list_git_tree_entries("f" * 40, ["missing.txt"])


def test_directory_snapshot_hashes_normal_files_in_one_batch(
    temp_git_repo,
    monkeypatch,
):
    """Undo directory snapshots should not spawn one hash-object per file."""
    source_dir = temp_git_repo / "session"
    source_dir.mkdir()
    files = [
        source_dir / "one.txt",
        source_dir / "nested" / "two.txt",
        source_dir / "three.txt",
    ]
    files[1].parent.mkdir()
    for file_path in files:
        file_path.write_text(f"{file_path.name}\n")

    blob_sha = create_git_blob([b"snapshot\n"])
    calls = []

    def fake_create_git_blobs_from_paths(paths):
        paths = tuple(paths)
        calls.append(paths)
        return {path: blob_sha for path in paths}

    monkeypatch.setattr(
        undo_snapshots,
        "create_git_blobs_from_paths",
        fake_create_git_blobs_from_paths,
    )

    with temp_git_index() as env:
        undo_snapshots.add_directory_to_index(
            env,
            source_dir=source_dir,
            tree_prefix="session",
        )
        git_write_tree(env=env)

    assert calls == [tuple(sorted(files))]
