"""Tests for reviewed batch candidate choices."""

import json
import subprocess
from types import SimpleNamespace

import pytest

import git_stage_batch.batch.operation_candidates as operation_candidates
import git_stage_batch.commands.apply_from as apply_from_module
import git_stage_batch.commands.include_from as include_from_module
import git_stage_batch.commands.show_from as show_from_module
from git_stage_batch.batch import create_batch
from git_stage_batch.batch.ownership import AbsenceClaim, BatchOwnership
from git_stage_batch.batch.storage import add_file_to_batch
from git_stage_batch.commands.apply_from import command_apply_from_batch
from git_stage_batch.commands.include_from import command_include_from_batch
from git_stage_batch.commands.show_from import command_show_from_batch
from git_stage_batch.data.session import initialize_abort_state
from git_stage_batch.editor import EditorBuffer
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.paths import (
    ensure_state_directory_exists,
    get_batch_candidate_state_file_path,
)


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for candidate preview tests."""
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
    subprocess.run(["git", "add", "README.md"], check=True, cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        check=True,
        cwd=repo,
        capture_output=True,
    )

    ensure_state_directory_exists()
    initialize_abort_state()
    return repo


def _create_displaced_absence_batch(repo, note=None):
    (repo / "file.txt").write_text("a\nb\n")
    subprocess.run(["git", "add", "file.txt"], check=True, cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add source file"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    initialize_abort_state()
    if note is None:
        create_batch("ambiguous")
    else:
        create_batch("ambiguous", note)
    add_file_to_batch(
        "ambiguous",
        "file.txt",
        BatchOwnership(
            [],
            [AbsenceClaim(anchor_line=1, content_lines=[b"x\n"])],
        ),
        "100644",
    )
    (repo / "file.txt").write_text("a\ninsert\nx\nmid\nx\nb\n")


def _create_displaced_absence_block_batch(repo):
    (repo / "file.txt").write_text("a\nb\n")
    subprocess.run(["git", "add", "file.txt"], check=True, cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add source file"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    initialize_abort_state()
    create_batch("ambiguous-lines")
    add_file_to_batch(
        "ambiguous-lines",
        "file.txt",
        BatchOwnership(
            [],
            [
                AbsenceClaim(
                    anchor_line=1,
                    content_lines=[b"x\n", b"y\n", b"z\n"],
                )
            ],
        ),
        "100644",
    )
    (repo / "file.txt").write_text(
        "a\ninsert\nx\ny\nz\nmid\nx\ny\nz\nb\n"
    )


def _candidate_state_has_file(batch_name, file_path):
    state_path = get_batch_candidate_state_file_path()
    if not state_path.exists():
        return False

    data = json.loads(state_path.read_text(encoding="utf-8"))
    return any(
        scope.get("batch_name") == batch_name and scope.get("file") == file_path
        for scope in data.get("scopes", {}).values()
    )


def test_show_candidate_set_lists_context_and_commands(temp_git_repo, capsys):
    """Unnumbered candidate selectors should list choices without a full diff."""
    _create_displaced_absence_batch(temp_git_repo)

    command_show_from_batch("ambiguous:apply", file="file.txt")

    captured = capsys.readouterr()
    assert "file.txt  ·  ambiguous  ·  apply candidates  ·  2 choices" in captured.out
    assert (
        "The working tree has changed in an ambiguous way since this batch was created."
        in captured.out
    )
    assert "The batch can be applied in more than one way:" in captured.out
    assert "Note:" not in captured.out
    assert 'Candidate 1/2   Remove "x" before "mid"' in captured.out
    assert 'Candidate 2/2   Remove "x" after "mid"' in captured.out
    assert "Working tree:" not in captured.out
    assert "3│ -x" in captured.out
    assert "5│ -x" in captured.out
    assert "Preview this candidate:\n     git-stage-batch show --from ambiguous:apply:1 --file file.txt" in captured.out
    assert "Apply this candidate:\n     git-stage-batch apply --from ambiguous:apply:1 --file file.txt" in captured.out
    assert "Apply candidate 1 of 2 for batch 'ambiguous'." not in captured.err


def test_multiline_ambiguous_block_summary_uses_ellipsis():
    """Candidate summaries should name a multi-line block by its endpoints."""
    assert show_from_module._summarize_ambiguity_block(
        ["vanilla extract", "nutmeg"],
    ) == '"vanilla extract … nutmeg"'


def test_candidate_overview_subject_names_only_ambiguous_targets():
    """The overview prose should mention only targets with multiple resolutions."""
    preview = SimpleNamespace(
        targets=(
            SimpleNamespace(target="index", resolution_count=1),
            SimpleNamespace(target="worktree", resolution_count=2),
        ),
    )
    assert show_from_module._candidate_overview_subject((preview,)) == (
        "working tree",
        "has",
    )

    preview = SimpleNamespace(
        targets=(
            SimpleNamespace(target="index", resolution_count=2),
            SimpleNamespace(target="worktree", resolution_count=1),
        ),
    )
    assert show_from_module._candidate_overview_subject((preview,)) == (
        "index",
        "has",
    )

    preview = SimpleNamespace(
        targets=(
            SimpleNamespace(target="index", resolution_count=2),
            SimpleNamespace(target="worktree", resolution_count=2),
        ),
    )
    assert show_from_module._candidate_overview_subject((preview,)) == (
        "working tree and index",
        "have",
    )


def test_show_candidate_set_highlights_candidate_regions_when_colored(
    temp_git_repo,
    capsys,
    monkeypatch,
):
    """Candidate summaries should visually mark the chosen ambiguous region."""
    _create_displaced_absence_batch(temp_git_repo)
    monkeypatch.setattr(
        show_from_module.Colors,
        "enabled",
        staticmethod(lambda: True),
    )

    command_show_from_batch("ambiguous:apply", file="file.txt")

    captured = capsys.readouterr()
    assert show_from_module.Colors.REVERSE in captured.out
    assert show_from_module.Colors.GRAY in captured.out
    assert show_from_module.Colors.RED in captured.out
    assert f"{show_from_module.Colors.REVERSE}{show_from_module.Colors.RED}" not in captured.out
    assert f"{show_from_module.Colors.REVERSE}{show_from_module.Colors.GRAY}" in captured.out
    assert "3│ " in captured.out


def test_apply_candidate_can_run_from_overview(temp_git_repo, capsys):
    """The candidate overview should count as review for shown candidates."""
    _create_displaced_absence_batch(temp_git_repo)

    command_show_from_batch("ambiguous:apply", file="file.txt")
    capsys.readouterr()
    assert _candidate_state_has_file("ambiguous", "file.txt")

    command_apply_from_batch("ambiguous:apply:1", file="file.txt")

    captured = capsys.readouterr()
    assert "Applied candidate 1 of 2 from batch 'ambiguous'" in captured.err
    assert "delete target line" not in captured.err
    assert (temp_git_repo / "file.txt").read_text() == "a\ninsert\nmid\nx\nb\n"
    assert not _candidate_state_has_file("ambiguous", "file.txt")


def test_candidate_preview_allows_equivalent_line_selection_spelling(
    temp_git_repo,
    capsys,
    monkeypatch,
):
    """Equivalent --line spellings should authorize the same selected lines."""
    _create_displaced_absence_block_batch(temp_git_repo)

    def resolve_selected_lines(batch_name, file_path, selected_ids, action):
        return set(selected_ids), None

    monkeypatch.setattr(
        show_from_module,
        "translate_batch_file_gutter_ids_to_selection_ids",
        resolve_selected_lines,
    )
    monkeypatch.setattr(
        apply_from_module,
        "translate_batch_file_gutter_ids_to_selection_ids",
        resolve_selected_lines,
    )

    command_show_from_batch(
        "ambiguous-lines:apply:1",
        line_ids="1-3",
        file="file.txt",
    )
    capsys.readouterr()

    command_apply_from_batch(
        "ambiguous-lines:apply:1",
        line_ids="1,2,3",
        file="file.txt",
    )

    captured = capsys.readouterr()
    assert "Applied candidate 1 of 2 from batch 'ambiguous-lines'" in captured.err
    assert (temp_git_repo / "file.txt").read_text() == (
        "a\ninsert\nmid\nx\ny\nz\nb\n"
    )


def test_apply_from_reports_candidate_enumeration_error(
    temp_git_repo,
    monkeypatch,
):
    """Candidate-count failures should not look like missing candidates."""
    _create_displaced_absence_batch(temp_git_repo)

    def fail_count(*args, **kwargs):
        raise RuntimeError("metadata drift")

    monkeypatch.setattr(
        apply_from_module,
        "build_apply_candidate_previews",
        fail_count,
    )

    with pytest.raises(CommandError) as exc_info:
        command_apply_from_batch("ambiguous", file="file.txt")

    assert "Cannot enumerate apply candidates for file.txt: metadata drift" in (
        exc_info.value.message
    )
    assert (temp_git_repo / "file.txt").read_text() == "a\ninsert\nx\nmid\nx\nb\n"


def test_numbered_show_candidate_records_its_own_preview(temp_git_repo, capsys):
    """A later numbered preview should not replace an earlier reviewed choice."""
    _create_displaced_absence_batch(temp_git_repo)

    command_show_from_batch("ambiguous:apply:1", file="file.txt")
    first_preview = capsys.readouterr()
    assert first_preview.out.startswith(
        "file.txt  ·  ambiguous  ·  apply candidate 1/2\n"
    )
    assert "Preview apply candidate 1 of 2 for batch 'ambiguous'." not in first_preview.out
    assert "Note:" not in first_preview.out
    assert "No changes applied." not in first_preview.out
    assert "\nfile.txt\nCandidate 1 of 2\n" not in first_preview.out
    assert 'Remove "x" before "mid"' in first_preview.out
    assert "─\nRemove" in first_preview.out
    assert 'Remove "x" before "mid"\n\nfile.txt ::' in first_preview.out
    assert "Working tree:" not in first_preview.out
    assert "Plan: delete target line" not in first_preview.out
    assert "file.txt :: @@ -1,6 +1,5 @@" in first_preview.out
    assert "3│ -x" in first_preview.out
    assert "--- a/file.txt" not in first_preview.out
    assert "overview: git-stage-batch show --from ambiguous:apply --file file.txt" in first_preview.out
    assert "next: git-stage-batch show --from ambiguous:apply:2 --file file.txt" in first_preview.out
    command_show_from_batch("ambiguous:apply:2", file="file.txt")
    second_preview = capsys.readouterr()
    assert "overview: git-stage-batch show --from ambiguous:apply --file file.txt" in second_preview.out
    assert "previous: git-stage-batch show --from ambiguous:apply:1 --file file.txt" in second_preview.out

    command_apply_from_batch("ambiguous:apply:1", file="file.txt")

    captured = capsys.readouterr()
    assert "Applied candidate 1 of 2 from batch 'ambiguous'" in captured.err
    assert (temp_git_repo / "file.txt").read_text() == "a\ninsert\nmid\nx\nb\n"


def test_numbered_show_candidate_header_preserves_batch_note(temp_git_repo, capsys):
    """Numbered candidate previews should keep the same framed header as overview."""
    _create_displaced_absence_batch(temp_git_repo, note="Auto-created")

    command_show_from_batch("ambiguous:apply:1", file="file.txt")

    captured = capsys.readouterr()
    assert captured.out.startswith(
        "file.txt  ·  ambiguous  ·  apply candidate 1/2\n"
        "Note: Auto-created\n"
    )
    assert "Preview apply candidate" not in captured.out
    assert "─\nRemove" in captured.out


def test_numbered_show_candidate_keeps_diff_colors_when_colored(
    temp_git_repo,
    capsys,
    monkeypatch,
):
    """Candidate diffs should use normal red/green diff colors."""
    _create_displaced_absence_batch(temp_git_repo)
    monkeypatch.setattr(
        show_from_module.Colors,
        "enabled",
        staticmethod(lambda: True),
    )

    command_show_from_batch("ambiguous:apply:1", file="file.txt")

    captured = capsys.readouterr()
    assert show_from_module.Colors.REVERSE in captured.out
    assert show_from_module.Colors.RED in captured.out
    assert f"{show_from_module.Colors.REVERSE}{show_from_module.Colors.RED}" not in captured.out
    assert f"{show_from_module.Colors.REVERSE}{show_from_module.Colors.GRAY}" in captured.out


def test_numbered_include_candidate_separates_target_sections(
    temp_git_repo,
    capsys,
):
    """Numbered include previews should not duplicate target labels."""
    _create_displaced_absence_batch(temp_git_repo)

    command_show_from_batch("ambiguous:include:2", file="file.txt")

    captured = capsys.readouterr()
    assert captured.out.startswith(
        "file.txt  ·  ambiguous  ·  include candidate 2/2\n"
    )
    assert "Preview include candidate" not in captured.out
    assert "Index update: No text changes\n\nWorking tree update: Remove" in captured.out
    assert "Working tree update:\nRemove" not in captured.out
    assert "Working tree update: Remove" in captured.out
    assert "\n\n\nWorking tree update:" not in captured.out
    assert "Index result:" not in captured.out
    assert "Working-tree result:" not in captured.out
    assert "Index: Remove" not in captured.out
    assert "Working tree: Remove" not in captured.out


def test_apply_candidate_rejects_changed_materialized_result(
    temp_git_repo,
    capsys,
    monkeypatch,
):
    """A reviewed apply candidate should not execute a different result."""
    _create_displaced_absence_batch(temp_git_repo)

    command_show_from_batch("ambiguous:apply:1", file="file.txt")
    capsys.readouterr()

    original_merge = operation_candidates.merge_batch_from_line_sequences_as_buffer

    def changed_result(*args, **kwargs):
        result = original_merge(*args, **kwargs)
        if kwargs.get("resolution") is None:
            return result
        try:
            changed = result.to_bytes().replace(b"insert\n", b"drifted\n", 1)
        finally:
            result.close()
        return EditorBuffer.from_bytes(changed)

    monkeypatch.setattr(
        operation_candidates,
        "merge_batch_from_line_sequences_as_buffer",
        changed_result,
    )

    with pytest.raises(CommandError) as exc_info:
        command_apply_from_batch("ambiguous:apply:1", file="file.txt")

    assert "has not been previewed" in exc_info.value.message
    assert (temp_git_repo / "file.txt").read_text() == "a\ninsert\nx\nmid\nx\nb\n"


def test_show_candidate_rejects_unknown_choice(temp_git_repo):
    """Operation candidate selectors should reject ordinals outside the set."""
    _create_displaced_absence_batch(temp_git_repo)

    with pytest.raises(CommandError) as exc_info:
        command_show_from_batch("ambiguous:apply:3", file="file.txt")

    assert "candidate 3 does not exist" in exc_info.value.message


def test_include_candidate_can_run_from_overview(temp_git_repo, capsys):
    """Include candidates should also be executable after the overview."""
    _create_displaced_absence_batch(temp_git_repo)

    command_show_from_batch("ambiguous:include", file="file.txt")
    overview = capsys.readouterr()
    assert "file.txt  ·  ambiguous  ·  include candidates  ·  2 choices" in overview.out
    assert (
        "The working tree has changed in an ambiguous way since this batch was created."
        in overview.out
    )
    assert "The batch can be included in more than one way:" in overview.out
    assert "Note:" not in overview.out
    assert "Index update, same for all candidates:" in overview.out
    assert 'Candidate 1/2   Remove "x" before "mid"' in overview.out
    assert 'Candidate 2/2   Remove "x" after "mid"' in overview.out
    assert overview.out.index("Candidate 1/2") < overview.out.index("Index update")
    assert "Candidate 1/2   Working tree:" not in overview.out
    assert "Candidate 2/2   Working tree:" not in overview.out
    assert "Include this candidate:\n     git-stage-batch include --from ambiguous:include:2 --file file.txt" in overview.out
    assert _candidate_state_has_file("ambiguous", "file.txt")

    command_include_from_batch("ambiguous:include:2", file="file.txt")

    captured = capsys.readouterr()
    assert "Included candidate 2 of 2 from batch 'ambiguous'" in captured.err
    assert "delete target line" not in captured.err
    assert (temp_git_repo / "file.txt").read_text() == "a\ninsert\nx\nmid\nb\n"
    assert not _candidate_state_has_file("ambiguous", "file.txt")


def test_include_from_reports_candidate_limit(temp_git_repo, monkeypatch):
    """Candidate-count limits should not look like missing candidates."""
    _create_displaced_absence_batch(temp_git_repo)

    def fail_count(*args, **kwargs):
        raise operation_candidates.CandidateEnumerationLimitError(
            "too many include candidates to preview safely"
        )

    monkeypatch.setattr(
        include_from_module,
        "build_include_candidate_previews",
        fail_count,
    )

    with pytest.raises(CommandError) as exc_info:
        command_include_from_batch("ambiguous", file="file.txt")

    assert "file.txt has too many include candidates to preview safely" in (
        exc_info.value.message
    )
    assert (temp_git_repo / "file.txt").read_text() == "a\ninsert\nx\nmid\nx\nb\n"


def test_include_candidate_rejects_changed_materialized_result(
    temp_git_repo,
    capsys,
    monkeypatch,
):
    """A reviewed include candidate should not execute a different result."""
    _create_displaced_absence_batch(temp_git_repo)

    command_show_from_batch("ambiguous:include:2", file="file.txt")
    capsys.readouterr()

    original_merge = operation_candidates.merge_batch_from_line_sequences_as_buffer

    def changed_result(*args, **kwargs):
        result = original_merge(*args, **kwargs)
        if kwargs.get("resolution") is None:
            return result
        try:
            changed = result.to_bytes().replace(b"mid\n", b"drifted\n", 1)
        finally:
            result.close()
        return EditorBuffer.from_bytes(changed)

    monkeypatch.setattr(
        operation_candidates,
        "merge_batch_from_line_sequences_as_buffer",
        changed_result,
    )

    with pytest.raises(CommandError) as exc_info:
        command_include_from_batch("ambiguous:include:2", file="file.txt")

    assert "has not been previewed" in exc_info.value.message
    assert (temp_git_repo / "file.txt").read_text() == "a\ninsert\nx\nmid\nx\nb\n"
