"""Tests for file I/O utilities."""

import os
import errno
import stat

import pytest

import git_stage_batch.utils.file_io as file_io
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.file_io import read_file_paths_file
from git_stage_batch.utils.file_io import write_file_paths_file
from git_stage_batch.utils.file_io import append_file_path_to_file
from git_stage_batch.utils.file_io import remove_file_path_from_file

from git_stage_batch.utils.file_io import (
    AtomicWriteModePolicy,
    PROJECT_FILE_MODE,
    append_lines_to_file,
    read_text_file_contents,
    write_file_bytes,
    write_text_file_contents,
)


class TestReadTextFileContents:
    """Tests for read_text_file_contents function."""

    def test_read_existing_file(self, tmp_path):
        """Test reading an existing file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!\n", encoding="utf-8")

        content = read_text_file_contents(test_file)

        assert content == "Hello, World!\n"

    def test_read_nonexistent_file_returns_empty_string(self, tmp_path):
        """Test reading a nonexistent file returns empty string."""
        test_file = tmp_path / "nonexistent.txt"

        content = read_text_file_contents(test_file)

        assert content == ""

    def test_read_empty_file(self, tmp_path):
        """Test reading an empty file."""
        test_file = tmp_path / "empty.txt"
        test_file.write_text("", encoding="utf-8")

        content = read_text_file_contents(test_file)

        assert content == ""

    def test_read_multiline_file(self, tmp_path):
        """Test reading a file with multiple lines."""
        test_file = tmp_path / "multiline.txt"
        test_file.write_text("Line 1\nLine 2\nLine 3\n", encoding="utf-8")

        content = read_text_file_contents(test_file)

        assert content == "Line 1\nLine 2\nLine 3\n"


class TestWriteTextFileContents:
    """Tests for write_text_file_contents function."""

    def test_write_creates_file(self, tmp_path):
        """Test that writing creates a new file."""
        test_file = tmp_path / "test.txt"

        write_text_file_contents(test_file, "Hello, World!")

        assert test_file.exists()
        assert test_file.read_text(encoding="utf-8") == "Hello, World!"

    def test_write_creates_parent_directories(self, tmp_path):
        """Test that writing creates parent directories."""
        test_file = tmp_path / "subdir" / "nested" / "test.txt"

        write_text_file_contents(test_file, "Content")

        assert test_file.exists()
        assert test_file.read_text(encoding="utf-8") == "Content"

    def test_write_overwrites_existing_file(self, tmp_path):
        """Test that writing overwrites existing file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Old content", encoding="utf-8")

        write_text_file_contents(test_file, "New content")

        assert test_file.read_text(encoding="utf-8") == "New content"

    def test_write_empty_string(self, tmp_path):
        """Test writing an empty string."""
        test_file = tmp_path / "empty.txt"

        write_text_file_contents(test_file, "")

        assert test_file.exists()
        assert test_file.read_text(encoding="utf-8") == ""

    def test_failed_atomic_replace_preserves_existing_contents(
        self,
        tmp_path,
        monkeypatch,
    ):
        """A failed replacement should leave the previous state file intact."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Old content", encoding="utf-8")

        def fail_replace(source, destination):
            assert destination == test_file
            raise OSError("replace failed")

        monkeypatch.setattr(os, "replace", fail_replace)

        with pytest.raises(OSError, match="replace failed"):
            file_io.write_text_file_contents(test_file, "New content")

        assert test_file.read_text(encoding="utf-8") == "Old content"
        assert list(tmp_path.glob(".test.txt.*.tmp")) == []

    @pytest.mark.parametrize("mode", [0o600, 0o644, 0o660, 0o755])
    def test_project_write_preserves_existing_mode(self, tmp_path, mode):
        test_file = tmp_path / "project-file"
        test_file.write_text("old", encoding="utf-8")
        test_file.chmod(mode)

        write_text_file_contents(
            test_file,
            "new",
            mode_policy=AtomicWriteModePolicy.PRESERVE_EXISTING,
            mode=PROJECT_FILE_MODE,
        )

        assert stat.S_IMODE(test_file.stat().st_mode) == mode

    def test_new_private_file_is_0600_under_permissive_umask(self, tmp_path):
        test_file = tmp_path / "private-state"
        old_umask = os.umask(0)
        try:
            write_text_file_contents(test_file, "state")
        finally:
            os.umask(old_umask)

        assert stat.S_IMODE(test_file.stat().st_mode) == 0o600

    def test_private_write_restricts_existing_file(self, tmp_path):
        test_file = tmp_path / "private-state"
        test_file.write_text("old", encoding="utf-8")
        test_file.chmod(0o666)

        write_text_file_contents(test_file, "new")

        assert stat.S_IMODE(test_file.stat().st_mode) == 0o600

    def test_new_project_file_receives_documented_mode(self, tmp_path):
        test_file = tmp_path / "project-file"

        write_file_bytes(
            test_file,
            b"content",
            mode_policy=AtomicWriteModePolicy.PRESERVE_EXISTING,
            mode=PROJECT_FILE_MODE,
        )

        assert stat.S_IMODE(test_file.stat().st_mode) == 0o644

    def test_caller_supplied_mode_is_applied(self, tmp_path):
        test_file = tmp_path / "generated-file"

        write_text_file_contents(
            test_file,
            "content",
            mode_policy=AtomicWriteModePolicy.CALLER_SUPPLIED,
            mode=0o640,
        )

        assert stat.S_IMODE(test_file.stat().st_mode) == 0o640

    def test_atomic_write_refuses_symlink(self, tmp_path):
        target = tmp_path / "target"
        target.write_text("target", encoding="utf-8")
        link = tmp_path / "link"
        link.symlink_to(target)

        with pytest.raises(CommandError, match="Refusing to replace symlink"):
            write_text_file_contents(
                link,
                "replacement",
                mode_policy=AtomicWriteModePolicy.PRESERVE_EXISTING,
            )

        assert link.is_symlink()
        assert target.read_text(encoding="utf-8") == "target"

    def test_failed_temp_file_fsync_preserves_existing_contents(
        self,
        tmp_path,
        monkeypatch,
    ):
        test_file = tmp_path / "test.txt"
        test_file.write_text("old", encoding="utf-8")

        def fail_fsync(_file_descriptor):
            raise OSError("fsync failed")

        monkeypatch.setattr(os, "fsync", fail_fsync)

        with pytest.raises(OSError, match="fsync failed"):
            write_text_file_contents(test_file, "new")

        assert test_file.read_text(encoding="utf-8") == "old"
        assert list(tmp_path.glob(".test.txt.*.tmp")) == []

    def test_unwritable_directory_failure_preserves_existing_contents(
        self,
        tmp_path,
        monkeypatch,
    ):
        test_file = tmp_path / "test.txt"
        test_file.write_text("old", encoding="utf-8")

        def refuse_temp_file(**_kwargs):
            raise PermissionError("directory is read-only")

        monkeypatch.setattr(file_io.tempfile, "mkstemp", refuse_temp_file)

        with pytest.raises(PermissionError, match="read-only"):
            write_text_file_contents(test_file, "new")

        assert test_file.read_text(encoding="utf-8") == "old"

    def test_ownership_permission_fallback_does_not_broaden_access(
        self,
        tmp_path,
        monkeypatch,
    ):
        test_file = tmp_path / "project-file"
        test_file.write_text("old", encoding="utf-8")
        test_file.chmod(0o660)

        def refuse_chown(_file_descriptor, _uid, _gid):
            raise PermissionError("not permitted")

        monkeypatch.setattr(os, "fchown", refuse_chown)

        write_text_file_contents(
            test_file,
            "new",
            mode_policy=AtomicWriteModePolicy.PRESERVE_EXISTING,
        )

        assert test_file.read_text(encoding="utf-8") == "new"
        assert stat.S_IMODE(test_file.stat().st_mode) == 0o600

    def test_unsupported_directory_fsync_is_tolerated(
        self,
        tmp_path,
        monkeypatch,
    ):
        test_file = tmp_path / "test.txt"
        real_open = os.open

        def reject_directory_open(path, flags, mode=0o777):
            if os.fspath(path) == os.fspath(tmp_path):
                raise OSError(errno.EINVAL, "directory fsync unsupported")
            return real_open(path, flags, mode)

        monkeypatch.setattr(os, "open", reject_directory_open)

        write_text_file_contents(test_file, "content")

        assert test_file.read_text(encoding="utf-8") == "content"


class TestAppendLinesToFile:
    """Tests for append_lines_to_file function."""

    def test_append_to_new_file(self, tmp_path):
        """Test appending to a new file."""
        test_file = tmp_path / "test.txt"

        append_lines_to_file(test_file, ["Line 1", "Line 2"])

        content = test_file.read_text(encoding="utf-8")
        assert content == "Line 1\nLine 2\n"

    def test_append_to_existing_file(self, tmp_path):
        """Test appending to an existing file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Existing line\n", encoding="utf-8")

        append_lines_to_file(test_file, ["New line 1", "New line 2"])

        content = test_file.read_text(encoding="utf-8")
        assert content == "Existing line\nNew line 1\nNew line 2\n"

    def test_append_normalizes_newlines(self, tmp_path):
        """Test that appending normalizes line endings."""
        test_file = tmp_path / "test.txt"

        append_lines_to_file(test_file, ["Line with newline\n", "Line without"])

        content = test_file.read_text(encoding="utf-8")
        assert content == "Line with newline\nLine without\n"

    def test_append_creates_parent_directories(self, tmp_path):
        """Test that appending creates parent directories."""
        test_file = tmp_path / "subdir" / "test.txt"

        append_lines_to_file(test_file, ["Line 1"])

        assert test_file.exists()
        assert test_file.read_text(encoding="utf-8") == "Line 1\n"

    def test_append_creates_private_file_under_permissive_umask(self, tmp_path):
        test_file = tmp_path / "journal"
        old_umask = os.umask(0)
        try:
            append_lines_to_file(test_file, ["entry"])
        finally:
            os.umask(old_umask)

        assert stat.S_IMODE(test_file.stat().st_mode) == 0o600

    def test_append_restricts_existing_state_file(self, tmp_path):
        test_file = tmp_path / "journal"
        test_file.write_text("old\n", encoding="utf-8")
        test_file.chmod(0o666)

        append_lines_to_file(test_file, ["new"])

        assert stat.S_IMODE(test_file.stat().st_mode) == 0o600

    def test_append_empty_list(self, tmp_path):
        """Test appending an empty list."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Existing\n", encoding="utf-8")

        append_lines_to_file(test_file, [])

        content = test_file.read_text(encoding="utf-8")
        assert content == "Existing\n"


class TestFilePathListManagement:
    """Tests for file path list management functions."""

    def test_read_file_paths_file_empty(self, tmp_path):
        """Test reading an empty file paths file."""

        path = tmp_path / "empty.txt"
        path.write_text("", encoding="utf-8")
        result = read_file_paths_file(path)
        assert result == []

    def test_read_file_paths_file_nonexistent(self, tmp_path):
        """Test reading a nonexistent file paths file."""

        path = tmp_path / "nonexistent.txt"
        result = read_file_paths_file(path)
        assert result == []

    def test_write_file_paths_file(self, tmp_path):
        """Test writing file paths to a file."""

        path = tmp_path / "paths.txt"
        file_paths = ["path/to/file1.txt", "path/to/file2.txt", "another/file.py"]
        write_file_paths_file(path, file_paths)

        # Read back and verify sorted and deduplicated
        result = read_file_paths_file(path)
        assert result == sorted(file_paths)

    def test_write_file_paths_file_deduplicates(self, tmp_path):
        """Test that write_file_paths_file deduplicates entries."""

        path = tmp_path / "paths.txt"
        file_paths = ["file1.txt", "file2.txt", "file1.txt", "file3.txt", "file2.txt"]
        write_file_paths_file(path, file_paths)

        result = read_file_paths_file(path)
        assert result == ["file1.txt", "file2.txt", "file3.txt"]

    @pytest.mark.parametrize(
        "file_path",
        [
            "line\nname.txt",
            "line\rname.txt",
            " leading-space.txt",
            "trailing-space.txt ",
        ],
    )
    def test_write_file_paths_file_preserves_special_paths(self, tmp_path, file_path):
        """Path manifests should round-trip names unsafe for line storage."""
        path = tmp_path / "paths.txt"

        write_file_paths_file(path, ["ordinary.txt", file_path])

        assert read_file_paths_file(path) == sorted(["ordinary.txt", file_path])

    def test_append_file_path_to_file(self, tmp_path):
        """Test appending a file path to a list."""

        path = tmp_path / "paths.txt"
        append_file_path_to_file(path, "file1.txt")
        append_file_path_to_file(path, "file2.txt")
        append_file_path_to_file(path, "file3.txt")

        result = read_file_paths_file(path)
        assert result == ["file1.txt", "file2.txt", "file3.txt"]

    def test_append_file_path_to_file_no_duplicates(self, tmp_path):
        """Test that appending doesn't create duplicates."""

        path = tmp_path / "paths.txt"
        append_file_path_to_file(path, "file1.txt")
        append_file_path_to_file(path, "file2.txt")
        append_file_path_to_file(path, "file1.txt")  # Duplicate

        result = read_file_paths_file(path)
        assert result == ["file1.txt", "file2.txt"]

    def test_remove_file_path_from_file(self, tmp_path):
        """Test removing a file path from a list."""

        path = tmp_path / "paths.txt"
        write_file_paths_file(path, ["file1.txt", "file2.txt", "file3.txt"])
        remove_file_path_from_file(path, "file2.txt")

        result = read_file_paths_file(path)
        assert result == ["file1.txt", "file3.txt"]

    def test_remove_file_path_from_file_nonexistent(self, tmp_path):
        """Test removing a nonexistent file path doesn't error."""

        path = tmp_path / "paths.txt"
        write_file_paths_file(path, ["file1.txt", "file2.txt"])
        remove_file_path_from_file(path, "nonexistent.txt")

        result = read_file_paths_file(path)
        assert result == ["file1.txt", "file2.txt"]
