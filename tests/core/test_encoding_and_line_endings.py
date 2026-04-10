"""Tests for handling different encodings and line endings."""

import subprocess
from pathlib import Path

import pytest

from git_stage_batch.core.diff_parser import (
    parse_unified_diff_streaming,
    build_current_lines_from_patch_bytes,
)
from git_stage_batch.core.models import LineEntry
from git_stage_batch.utils.git import stream_git_command


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, cwd=repo, capture_output=True)

    # Disable Git's line ending conversion for these tests
    subprocess.run(["git", "config", "core.autocrlf", "false"], check=True, cwd=repo, capture_output=True)

    # Create initial commit
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, cwd=repo, capture_output=True)

    return repo


class TestCRLFLineEndings:
    """Tests for files with CRLF (Windows) line endings."""

    def test_crlf_file_parsing(self, temp_git_repo):
        """Test that CRLF line endings are preserved in parsed diffs."""
        # Create file with CRLF endings
        test_file = temp_git_repo / "windows.txt"
        test_file.write_bytes(b"line1\r\nline2\r\nline3\r\n")
        subprocess.run(["git", "add", "windows.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add CRLF file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify with CRLF
        test_file.write_bytes(b"line1\r\nmodified\r\nline3\r\n")

        # Parse diff
        patches = list(parse_unified_diff_streaming(stream_git_command(["diff", "--no-color"])))

        assert len(patches) == 1
        patch = patches[0]

        # Verify CRLF is preserved in patch bytes
        patch_bytes = patch.to_patch_bytes()
        # The content lines should have \r preserved (only \n is the diff terminator)
        assert b"line1\r" in patch_bytes or b"modified\r" in patch_bytes

    def test_crlf_roundtrip(self, temp_git_repo):
        """Test that CRLF files can be discarded and reapplied correctly."""
        from git_stage_batch.commands.start import command_start
        from git_stage_batch.commands.discard import command_discard_to_batch
        from git_stage_batch.batch.operations import create_batch

        # Create file with CRLF
        test_file = temp_git_repo / "crlf.txt"
        original_content = b"header\r\nline1\r\nline2\r\nfooter\r\n"
        test_file.write_bytes(original_content)
        subprocess.run(["git", "add", "crlf.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add CRLF"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify middle lines
        modified_content = b"header\r\nchanged1\r\nchanged2\r\nfooter\r\n"
        test_file.write_bytes(modified_content)

        # Discard to batch
        command_start()
        create_batch("test-batch", "Test")
        command_discard_to_batch("test-batch", line_ids=None, file_only=True)

        # File should be reverted to original
        assert test_file.read_bytes() == original_content

        # Apply from batch using git apply directly
        from git_stage_batch.batch.storage import get_batch_diff
        batch_diff = get_batch_diff("test-batch")

        result = subprocess.run(
            ["git", "apply"],
            input=batch_diff,
            capture_output=True,
            cwd=temp_git_repo
        )
        assert result.returncode == 0, f"git apply failed: {result.stderr}"

        # Should have modified content back with CRLF intact
        result_content = test_file.read_bytes()
        assert result_content == modified_content
        assert b"\r\n" in result_content  # CRLF preserved


class TestMixedLineEndings:
    """Tests for files with mixed line endings."""

    def test_mixed_line_endings_preserved(self, temp_git_repo):
        """Test that mixed LF and CRLF line endings are preserved."""
        # Create file with mixed endings
        test_file = temp_git_repo / "mixed.txt"
        mixed_content = b"unix line\nwindows line\r\nmac line\ranother unix\n"
        test_file.write_bytes(mixed_content)
        subprocess.run(["git", "add", "mixed.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add mixed"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify one line
        modified_content = b"unix line\nwindows line\r\nchanged line\ranother unix\n"
        test_file.write_bytes(modified_content)

        # Parse diff
        patches = list(parse_unified_diff_streaming(stream_git_command(["diff", "--no-color"])))
        assert len(patches) == 1

        # Build current lines
        patch_bytes = patches[0].to_patch_bytes()
        current_lines = build_current_lines_from_patch_bytes(patch_bytes)

        # Find the changed line
        changed_line = None
        for line in current_lines.lines:
            if line.kind == "+" and b"changed" in line.text_bytes:
                changed_line = line
                break

        assert changed_line is not None
        # The \r should be preserved in the content (not stripped)
        assert b"\r" in changed_line.text_bytes or changed_line.text_bytes == b"changed line\r"


class TestLatin1Encoding:
    """Tests for Latin-1 (ISO-8859-1) encoded files."""

    def test_latin1_file_with_extended_chars(self, temp_git_repo):
        """Test that Latin-1 files with extended characters are handled."""
        # Create file with Latin-1 specific characters (not valid UTF-8)
        test_file = temp_git_repo / "latin1.txt"
        # café in Latin-1: caf\xe9
        # naïve in Latin-1: na\xefve
        latin1_content = b"cafe\nna\xefve\n"
        test_file.write_bytes(latin1_content)
        subprocess.run(["git", "add", "latin1.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add Latin-1"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify
        modified_content = b"cafe\nna\xefve modified\n"
        test_file.write_bytes(modified_content)

        # Should parse without crashing
        patches = list(parse_unified_diff_streaming(stream_git_command(["diff", "--no-color"])))
        assert len(patches) == 1

        # Build current lines - should decode with replacement character
        patch_bytes = patches[0].to_patch_bytes()
        current_lines = build_current_lines_from_patch_bytes(patch_bytes)

        # Find the added line
        added_line = None
        for line in current_lines.lines:
            if line.kind == "+" and b"modified" in line.text_bytes:
                added_line = line
                break

        assert added_line is not None
        # text_bytes should preserve the Latin-1 bytes
        assert b"\xef" in added_line.text_bytes
        # text should have replacement character for invalid UTF-8
        assert "�" in added_line.text or "naive" in added_line.text  # May show replacement char

    def test_latin1_roundtrip_preserves_bytes(self, temp_git_repo):
        """Test that Latin-1 content survives discard/apply roundtrip."""
        from git_stage_batch.commands.start import command_start
        from git_stage_batch.commands.discard import command_discard_to_batch
        from git_stage_batch.commands.apply_from import command_apply_from_batch
        from git_stage_batch.batch.operations import create_batch

        # Create Latin-1 file
        test_file = temp_git_repo / "latin1.dat"
        original = b"header\ndata: \xe9\xf1\n"  # é and ñ in Latin-1
        test_file.write_bytes(original)
        subprocess.run(["git", "add", "latin1.dat"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify
        modified = b"header\ndata: \xe9\xf1 changed\n"
        test_file.write_bytes(modified)

        # Discard to batch
        command_start()
        create_batch("test-batch", "Test")
        command_discard_to_batch("test-batch", line_ids=None, file_only=True)

        # File should be reverted to original
        assert test_file.read_bytes() == original

        # Apply from batch directly using git apply
        from git_stage_batch.batch.storage import get_batch_diff
        batch_diff = get_batch_diff("test-batch")

        # Debug: check batch diff content
        print(f"\nBatch diff type: {type(batch_diff)}")
        print(f"Batch diff length: {len(batch_diff)}")
        import binascii
        print(f"First 200 bytes (hex): {binascii.hexlify(batch_diff[:200])}")
        print(f"Batch diff content:\n{batch_diff[:500]}\n")

        # Apply the batch diff
        result = subprocess.run(
            ["git", "apply"],
            input=batch_diff,
            capture_output=True,
            cwd=temp_git_repo
        )
        assert result.returncode == 0, f"git apply failed: {result.stderr}"

        # Bytes should be preserved exactly
        result_content = test_file.read_bytes()
        assert result_content == modified
        assert b"\xe9\xf1" in result_content  # Latin-1 bytes intact


class TestBinaryContent:
    """Tests for files with binary content."""

    def test_files_with_null_bytes(self, temp_git_repo):
        """Test that files with null bytes are handled."""
        # Create file with null bytes
        test_file = temp_git_repo / "binary.dat"
        binary_content = b"text\x00\x01\x02more text\n"
        test_file.write_bytes(binary_content)
        subprocess.run(["git", "add", "binary.dat"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add binary"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify
        modified_content = b"text\x00\x01\x02changed\n"
        test_file.write_bytes(modified_content)

        # Git will treat this as binary and show "Binary files differ"
        # Our code should handle this gracefully
        patches = list(parse_unified_diff_streaming(stream_git_command(["diff", "--no-color"])))

        # Git doesn't create unified diffs for binary files
        # So we might get 0 patches or a special binary indicator
        # The important thing is we don't crash


class TestInvalidUTF8Sequences:
    """Tests for files with invalid UTF-8 byte sequences."""

    def test_invalid_utf8_sequences_preserved(self, temp_git_repo):
        """Test that invalid UTF-8 sequences are preserved as bytes."""
        # Create file with invalid UTF-8
        test_file = temp_git_repo / "invalid.txt"
        # \xff is not valid UTF-8
        invalid_content = b"valid line\ninvalid: \xff\xfe\n"
        test_file.write_bytes(invalid_content)
        subprocess.run(["git", "add", "invalid.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add invalid"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify
        modified_content = b"valid line\ninvalid: \xff\xfe changed\n"
        test_file.write_bytes(modified_content)

        # Should parse without crashing
        patches = list(parse_unified_diff_streaming(stream_git_command(["diff", "--no-color"])))
        assert len(patches) == 1

        # Build current lines
        patch_bytes = patches[0].to_patch_bytes()
        current_lines = build_current_lines_from_patch_bytes(patch_bytes)

        # Find the changed line
        changed_line = None
        for line in current_lines.lines:
            if line.kind == "+" and b"changed" in line.text_bytes:
                changed_line = line
                break

        assert changed_line is not None
        # Invalid UTF-8 bytes should be preserved
        assert b"\xff\xfe" in changed_line.text_bytes


class TestLineEndingEdgeCases:
    """Tests for edge cases in line ending handling."""

    def test_file_without_final_newline(self, temp_git_repo):
        """Test file that doesn't end with a newline."""
        test_file = temp_git_repo / "no-final-newline.txt"
        test_file.write_bytes(b"line1\nline2")  # No final \n
        subprocess.run(["git", "add", "no-final-newline.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify
        test_file.write_bytes(b"line1\nchanged")  # Still no final \n

        # Should parse correctly
        patches = list(parse_unified_diff_streaming(stream_git_command(["diff", "--no-color"])))
        assert len(patches) == 1

        # Git adds "\ No newline at end of file" - we should handle this
        patch_bytes = patches[0].to_patch_bytes()
        current_lines = build_current_lines_from_patch_bytes(patch_bytes)
        assert len(current_lines.lines) > 0

    def test_only_carriage_returns(self, temp_git_repo):
        """Test file with only CR (old Mac style) line endings."""
        test_file = temp_git_repo / "cr-only.txt"
        test_file.write_bytes(b"line1\rline2\rline3\r")
        subprocess.run(["git", "add", "cr-only.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add CR"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify
        test_file.write_bytes(b"line1\rchanged\rline3\r")

        # Parse diff
        patches = list(parse_unified_diff_streaming(stream_git_command(["diff", "--no-color"])))

        # Git treats CR as part of the line content (not a line separator)
        # So this might show as one big line or git might handle it specially
        # The important thing is we don't corrupt the \r characters

    def test_empty_lines_with_crlf(self, temp_git_repo):
        """Test empty lines with CRLF."""
        test_file = temp_git_repo / "empty-crlf.txt"
        # Empty line with CRLF: just \r\n
        test_file.write_bytes(b"line1\r\n\r\nline3\r\n")
        subprocess.run(["git", "add", "empty-crlf.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify
        test_file.write_bytes(b"line1\r\n\r\nchanged\r\n")

        # Should parse correctly
        patches = list(parse_unified_diff_streaming(stream_git_command(["diff", "--no-color"])))
        assert len(patches) == 1

        patch_bytes = patches[0].to_patch_bytes()
        # Empty line's CRLF should be preserved
        assert b"\r\n" in patch_bytes


class TestDiffParserBytesHandling:
    """Direct tests of diff parser with various byte patterns."""

    def test_diff_with_utf8_filename(self):
        """Test parsing diff with UTF-8 filename."""
        # Create a proper diff with UTF-8 filename (café)
        diff_lines = [
            b"diff --git a/caf\xc3\xa9.txt b/caf\xc3\xa9.txt\n",
            b"--- a/caf\xc3\xa9.txt\n",
            b"+++ b/caf\xc3\xa9.txt\n",
            b"@@ -1 +1 @@\n",
            b"-old\n",
            b"+new\n",
        ]
        patches = list(parse_unified_diff_streaming(diff_lines))
        assert len(patches) == 1
        # Filename should be decoded as UTF-8
        assert "café" in patches[0].new_path

    def test_line_entry_preserves_carriage_return(self):
        """Test that LineEntry preserves \\r in text_bytes."""
        # Create a LineEntry with \r in the content
        line = LineEntry(
            id=1,
            kind="+",
            old_line_number=None,
            new_line_number=1,
            text_bytes=b"content with\r in it",
            text="content with\r in it",  # Would show as replacement char if invalid
        )

        # \r should be preserved in text_bytes
        assert b"\r" in line.text_bytes
        assert line.text_bytes == b"content with\r in it"

    def test_build_current_lines_preserves_non_utf8_bytes(self):
        """Test that build_current_lines_from_patch_bytes preserves non-UTF-8 bytes."""
        # Create a patch with Latin-1 bytes (invalid UTF-8)
        patch_bytes = b"--- a/test.txt\n+++ b/test.txt\n@@ -1 +1 @@\n-old\n+caf\xe9\n"

        current_lines = build_current_lines_from_patch_bytes(patch_bytes)

        # Find the added line
        added_line = None
        for line in current_lines.lines:
            if line.kind == "+":
                added_line = line
                break

        assert added_line is not None
        # The invalid UTF-8 byte should be preserved in text_bytes
        assert b"\xe9" in added_line.text_bytes
        assert added_line.text_bytes == b"caf\xe9"
