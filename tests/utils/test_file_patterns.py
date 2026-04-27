"""Tests for gitignore-style file pattern resolution."""

from git_stage_batch.utils.file_patterns import resolve_gitignore_style_patterns


def test_resolve_gitignore_style_patterns_matches_basename_anywhere():
    """Basename-only patterns should match in any directory."""
    resolved = resolve_gitignore_style_patterns(
        ["foo.py", "src/bar.py", "docs/readme.md"],
        ["*.py"],
    )

    assert resolved == ["foo.py", "src/bar.py"]


def test_resolve_gitignore_style_patterns_matches_root_anchored_pattern():
    """Leading / should anchor the pattern at the repository root."""
    resolved = resolve_gitignore_style_patterns(
        ["src/main.py", "nested/src/main.py"],
        ["/src/*.py"],
    )

    assert resolved == ["src/main.py"]


def test_resolve_gitignore_style_patterns_matches_directory_pattern():
    """Trailing / should match files within directories of that name."""
    resolved = resolve_gitignore_style_patterns(
        ["build/output.o", "nested/build/cache.txt", "src/build.py"],
        ["build/"],
    )

    assert resolved == ["build/output.o", "nested/build/cache.txt"]


def test_resolve_gitignore_style_patterns_supports_ordered_exclusion():
    """Later negated patterns should remove earlier matches."""
    resolved = resolve_gitignore_style_patterns(
        ["dir/keep.py", "dir/exception.py", "other.py"],
        ["dir/*", "!dir/exception.py"],
    )

    assert resolved == ["dir/keep.py"]


def test_resolve_gitignore_style_patterns_supports_character_classes():
    """Character classes should behave like shell-style wildcards."""
    resolved = resolve_gitignore_style_patterns(
        ["file1.py", "file2.py", "filea.py", "fileb.py"],
        ["file[1a].py"],
    )

    assert resolved == ["file1.py", "filea.py"]


def test_resolve_gitignore_style_patterns_supports_negated_character_classes():
    """Negated character classes should exclude listed characters."""
    resolved = resolve_gitignore_style_patterns(
        ["file1.py", "file2.py", "filea.py"],
        ["file[!a].py"],
    )

    assert resolved == ["file1.py", "file2.py"]


def test_resolve_gitignore_style_patterns_supports_reinclusion_after_exclusion():
    """Later positive patterns should be able to re-include candidates."""
    resolved = resolve_gitignore_style_patterns(
        ["dir/a.py", "dir/exception.py", "dir/z.py"],
        ["dir/*", "!dir/*.py", "dir/exception.py"],
    )

    assert resolved == ["dir/exception.py"]


def test_resolve_gitignore_style_patterns_supports_escaped_comment_marker():
    """Escaped # should be treated as a literal character."""
    resolved = resolve_gitignore_style_patterns(
        ["#literal", "other"],
        [r"\#literal"],
    )

    assert resolved == ["#literal"]


def test_resolve_gitignore_style_patterns_supports_escaped_negation_marker():
    """Escaped ! should be treated as a literal character, not an exclusion."""
    resolved = resolve_gitignore_style_patterns(
        ["!literal", "other"],
        [r"\!literal"],
    )

    assert resolved == ["!literal"]
