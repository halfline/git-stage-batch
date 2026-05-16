"""Tests for reviewed batch candidate choices."""

import json
import subprocess

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


def _create_displaced_absence_batch(repo):
    (repo / "file.txt").write_text("a\nb\n")
    subprocess.run(["git", "add", "file.txt"], check=True, cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add source file"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    initialize_abort_state()
    create_batch("ambiguous")
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
    assert "Apply candidates for batch 'ambiguous' in file.txt." in captured.out
    assert "Candidates: 2" in captured.out
    assert '1. Working tree: Remove "x" near "insert"' in captured.out
    assert '2. Working tree: Remove "x" near "mid"' in captured.out
    assert "3 -x" in captured.out
    assert "5 -x" in captured.out
    assert "show: git-stage-batch show --from ambiguous:apply:1 --file file.txt" in captured.out
    assert "apply: git-stage-batch apply --from ambiguous:apply:1 --file file.txt" in captured.out
    assert "Apply candidate 1 of 2 for batch 'ambiguous'." not in captured.err


def test_apply_candidate_can_run_from_overview(temp_git_repo, capsys):
    """The candidate overview should count as review for shown candidates."""
    _create_displaced_absence_batch(temp_git_repo)

    command_show_from_batch("ambiguous:apply", file="file.txt")
    capsys.readouterr()
    assert _candidate_state_has_file("ambiguous", "file.txt")

    command_apply_from_batch("ambiguous:apply:1", file="file.txt")

    captured = capsys.readouterr()
    assert "Applied candidate 1 of 2 from batch 'ambiguous'" in captured.err
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
    assert "overview: git-stage-batch show --from ambiguous:apply --file file.txt" in first_preview.err
    assert "next: git-stage-batch show --from ambiguous:apply:2 --file file.txt" in first_preview.err
    command_show_from_batch("ambiguous:apply:2", file="file.txt")
    second_preview = capsys.readouterr()
    assert "overview: git-stage-batch show --from ambiguous:apply --file file.txt" in second_preview.err
    assert "previous: git-stage-batch show --from ambiguous:apply:1 --file file.txt" in second_preview.err

    command_apply_from_batch("ambiguous:apply:1", file="file.txt")

    captured = capsys.readouterr()
    assert "Applied candidate 1 of 2 from batch 'ambiguous'" in captured.err
    assert (temp_git_repo / "file.txt").read_text() == "a\ninsert\nmid\nx\nb\n"


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
    assert "include: git-stage-batch include --from ambiguous:include:2 --file file.txt" in overview.out
    assert _candidate_state_has_file("ambiguous", "file.txt")

    command_include_from_batch("ambiguous:include:2", file="file.txt")

    captured = capsys.readouterr()
    assert "Included candidate 2 of 2 from batch 'ambiguous'" in captured.err
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
