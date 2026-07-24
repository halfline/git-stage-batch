"""Tests for selected file-hunk caching."""

import git_stage_batch.data.selected_change.file_hunk_cache as file_hunk_cache


def test_file_hunk_cache_uses_one_comparison_base(monkeypatch):
    """Rendered coordinates and snapshot metadata must share one base."""
    line_changes = object()
    calls = []

    def comparison_base():
        calls.append("resolve")
        if len(calls) > 1:
            raise AssertionError("comparison base must be resolved once")
        return "session-base"

    def render(file_path, *, comparison_base):
        calls.append(("render", file_path, comparison_base))
        return line_changes

    def cache(file_path, rendered, *, comparison_base):
        calls.append(("cache", file_path, rendered, comparison_base))
        return rendered

    monkeypatch.setattr(
        file_hunk_cache,
        "session_comparison_base",
        comparison_base,
    )
    monkeypatch.setattr(
        file_hunk_cache,
        "render_file_as_single_hunk",
        render,
    )
    monkeypatch.setattr(
        file_hunk_cache,
        "_cache_combined_file_line_changes",
        cache,
    )

    result = file_hunk_cache.cache_file_as_single_hunk("module.py")

    assert result is line_changes
    assert calls == [
        "resolve",
        ("render", "module.py", "session-base"),
        ("cache", "module.py", line_changes, "session-base"),
    ]
