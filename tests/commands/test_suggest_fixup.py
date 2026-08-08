"""Integration tests for exact-evidence fixup suggestions."""

import json
import subprocess

import pytest

from git_stage_batch.commands.fixup import search_flow
from git_stage_batch.commands.start import command_start
from git_stage_batch.commands.suggest_fixup import (
    command_suggest_fixup,
    command_suggest_fixup_line,
)
from git_stage_batch.data.file_hunk_display import render_file_as_single_hunk
from git_stage_batch.data.hunk_tracking import fetch_next_change
from git_stage_batch.data.suggest_fixup_state import read_suggest_fixup_state
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.paths import get_suggest_fixup_state_file_path


def _git(repo, *arguments):
    return subprocess.run(
        ["git", *arguments],
        check=True,
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repo, message):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a repository with one excluded-boundary commit."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "README.md").write_text("# Test\n")
    _commit(repo, "Initial commit")
    return repo


def _select_current_hunk():
    command_start()
    fetch_next_change()


def test_suggest_fixup_requires_selected_hunk(temp_git_repo):
    _git(temp_git_repo, "commit", "--allow-empty", "-m", "Range commit")
    with pytest.raises(CommandError):
        command_suggest_fixup(boundary="HEAD~1")


def test_human_output_separates_lineage_and_placement(temp_git_repo, capsys):
    source = temp_git_repo / "test.py"
    source.write_text("line 1\n")
    base = _commit(temp_git_repo, "Add test.py")
    source.write_text("line 1 committed\n")
    target = _commit(temp_git_repo, "Modify line 1")
    source.write_text("line 1 corrected\n")
    _select_current_hunk()
    capsys.readouterr()

    command_suggest_fixup(boundary=base)

    output = capsys.readouterr().out
    assert "Lineage:" in output
    assert "Placement:" in output
    assert "Decision:" in output
    assert "Candidate 1 of 1:" in output
    assert "Modify line 1" in output
    assert target[:12] in output
    assert "git commit --fixup=" in output


def test_porcelain_records_sha256_object_format(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "sha256-repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    _git(repo, "init", "-q", "--object-format=sha256")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    source = repo / "test.py"
    source.write_text("base\n")
    base = _commit(repo, "Add test.py")
    source.write_text("committed\n")
    target = _commit(repo, "Change test.py")
    source.write_text("correction\n")
    _select_current_hunk()
    capsys.readouterr()

    command_suggest_fixup(boundary=base, porcelain=True)

    output = json.loads(capsys.readouterr().out)
    assert output["object_format"] == "sha256"
    assert output["candidate"]["id"] == target
    assert len(output["range"]["base"]) == 64
    state = read_suggest_fixup_state()
    assert state is not None
    assert state["object_format"] == "sha256"


def test_equivalent_boundary_spellings_continue_canonical_iteration(
    temp_git_repo,
    capsys,
):
    source = temp_git_repo / "test.py"
    source.write_text("v1\n")
    base = _commit(temp_git_repo, "Add test.py")
    source.write_text("v2\n")
    newest = _commit(temp_git_repo, "Change to v2")
    source.write_text("v3\n")
    older = _commit(temp_git_repo, "Change to v3")
    source.write_text("worktree\n")
    _select_current_hunk()
    capsys.readouterr()

    command_suggest_fixup(boundary="HEAD~2", porcelain=True)
    first = json.loads(capsys.readouterr().out)
    state = read_suggest_fixup_state()
    assert state is not None
    assert state["base_commit"] == base
    assert state["head_commit"] == older
    assert first["candidate"]["id"] == older
    assert first["iteration"] == {"index": 1, "total": 2}

    command_suggest_fixup(boundary=base, porcelain=True)
    second = json.loads(capsys.readouterr().out)

    assert second["candidate"]["id"] == newest
    assert second["iteration"] == {"index": 2, "total": 2}


def test_reset_and_last_control_iteration(temp_git_repo, capsys):
    source = temp_git_repo / "test.py"
    source.write_text("v1\n")
    base = _commit(temp_git_repo, "Add test.py")
    source.write_text("v2\n")
    _commit(temp_git_repo, "Change to v2")
    source.write_text("v3\n")
    latest = _commit(temp_git_repo, "Change to v3")
    source.write_text("worktree\n")
    _select_current_hunk()
    capsys.readouterr()

    command_suggest_fixup(boundary=base, porcelain=True)
    capsys.readouterr()
    command_suggest_fixup(boundary=base, show_last=True, porcelain=True)
    repeated = json.loads(capsys.readouterr().out)
    assert repeated["candidate"]["id"] == latest
    assert repeated["iteration"]["index"] == 1

    command_suggest_fixup(boundary=base, reset=True, porcelain=True)
    reset = json.loads(capsys.readouterr().out)
    assert reset["candidate"]["id"] == latest
    assert reset["iteration"]["index"] == 1


def test_head_change_invalidates_saved_iteration(temp_git_repo, capsys):
    source = temp_git_repo / "test.py"
    source.write_text("v1\n")
    base = _commit(temp_git_repo, "Add test.py")
    source.write_text("v2\n")
    _commit(temp_git_repo, "Change to v2")
    source.write_text("worktree\n")
    _select_current_hunk()
    capsys.readouterr()

    command_suggest_fixup(boundary=base, porcelain=True)
    capsys.readouterr()
    first_state = read_suggest_fixup_state()
    assert first_state is not None

    _git(temp_git_repo, "commit", "--allow-empty", "-m", "Move HEAD")
    moved_head = _git(temp_git_repo, "rev-parse", "HEAD")
    command_suggest_fixup(boundary=base, porcelain=True)
    output = json.loads(capsys.readouterr().out)
    second_state = read_suggest_fixup_state()

    assert output["iteration"]["index"] == 1
    assert second_state is not None
    assert second_state["head_commit"] == moved_head
    assert second_state["range_fingerprint"] != first_state["range_fingerprint"]


def test_hunk_rejects_index_content_that_differs_from_head(temp_git_repo):
    source = temp_git_repo / "test.py"
    base = _git(temp_git_repo, "rev-parse", "HEAD")
    source.write_text("committed\n")
    _commit(temp_git_repo, "Add test.py")
    source.write_text("staged change\n")
    _git(temp_git_repo, "add", "test.py")
    source.write_text("worktree correction\n")
    _select_current_hunk()

    with pytest.raises(
        CommandError,
        match="Index content no longer matches the selected line view",
    ):
        command_suggest_fixup(boundary=base)


def test_hunk_change_during_analysis_fails_before_state_or_output(
    temp_git_repo,
    capsys,
    monkeypatch,
):
    source = temp_git_repo / "test.py"
    source.write_text("base\n")
    base = _commit(temp_git_repo, "Add test.py")
    source.write_text("committed\n")
    _commit(temp_git_repo, "Change test.py")
    source.write_text("correction\n")
    _select_current_hunk()
    capsys.readouterr()

    original = search_flow.analyze_lineage_history

    def mutate_after_history(*args, **kwargs):
        history = original(*args, **kwargs)
        source.write_text("changed during analysis\n")
        return history

    monkeypatch.setattr(
        search_flow,
        "analyze_lineage_history",
        mutate_after_history,
    )

    with pytest.raises(CommandError, match="Cached hunk is stale"):
        command_suggest_fixup(boundary=base, porcelain=True)

    assert capsys.readouterr().out == ""
    assert read_suggest_fixup_state() is None


def test_disjoint_line_selection_preserves_exact_source_ranges(
    temp_git_repo,
    capsys,
):
    source = temp_git_repo / "test.py"
    source.write_text("one\ntwo\nthree\nfour\n")
    base = _commit(temp_git_repo, "Add test.py")
    source.write_text("one committed\ntwo\nthree\nfour\n")
    _commit(temp_git_repo, "Change line one")
    source.write_text("one committed\ntwo\nthree committed\nfour\n")
    _commit(temp_git_repo, "Change line three")
    source.write_text("one fixed\ntwo\nthree fixed\nfour\n")

    command_start()
    line_changes = render_file_as_single_hunk("test.py")
    assert line_changes is not None
    selected_ids = [
        line.id
        for line in line_changes.lines
        if line.id is not None and line.kind in {"+", "-"}
    ]
    capsys.readouterr()

    command_suggest_fixup_line(
        ",".join(str(line_id) for line_id in selected_ids),
        boundary=base,
        file="test.py",
        porcelain=True,
    )

    output = json.loads(capsys.readouterr().out)
    assert output["unit"]["lineage"]["queried_ranges"] == [
        {"start": 1, "end": 1},
        {"start": 3, "end": 3},
    ]
    assert output["unit"]["lineage"]["queried_line_count"] == 2
    assert all(len(commit) == 40 for commit in output["candidates"])


def test_addition_uses_anchor_lineage_and_placement(temp_git_repo, capsys):
    source = temp_git_repo / "test.py"
    source.write_text("anchor\n")
    base = _commit(temp_git_repo, "Add test.py")
    source.write_text("anchor committed\n")
    target = _commit(temp_git_repo, "Change anchor")
    source.write_text("anchor committed\ninserted fix\n")
    _select_current_hunk()
    capsys.readouterr()

    command_suggest_fixup(boundary=base, porcelain=True)

    output = json.loads(capsys.readouterr().out)
    assert output["unit"]["kind"] == "text-addition"
    assert output["unit"]["lineage"]["queried_ranges"] == [
        {"start": 1, "end": 1}
    ]
    assert output["unit"]["placement"]["barrier"] == target
    assert output["candidate"]["id"] == target


def test_addition_to_empty_tracked_file_uses_placement_only(
    temp_git_repo,
    capsys,
):
    source = temp_git_repo / "empty.txt"
    source.write_text("")
    base = _git(temp_git_repo, "rev-parse", "HEAD")
    target = _commit(temp_git_repo, "Add empty file")
    source.write_text("first line\n")
    _select_current_hunk()
    capsys.readouterr()

    command_suggest_fixup(boundary=base, porcelain=True)

    output = json.loads(capsys.readouterr().out)
    assert output["unit"]["status"] == "placement-only"
    assert output["unit"]["lineage"]["queried_ranges"] == []
    assert output["unit"]["placement"]["barrier"] == target
    assert output["candidate_sources"] == ["placement-barrier"]


def test_porcelain_no_candidate_has_reasoned_record(temp_git_repo, capsys):
    source = temp_git_repo / "test.py"
    source.write_text("owned before range\n")
    base = _commit(temp_git_repo, "Add test.py")
    _git(temp_git_repo, "commit", "--allow-empty", "-m", "Unrelated commit")
    source.write_text("worktree fix\n")
    _select_current_hunk()
    capsys.readouterr()

    with pytest.raises(SystemExit) as error:
        command_suggest_fixup(boundary=base, porcelain=True)

    assert error.value.code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["result"] == "no-candidates"
    assert output["candidate"] is None
    assert output["unit"]["reason"] == "no-target-evidence"


def test_abort_clears_state_without_output(temp_git_repo, capsys):
    state_path = get_suggest_fixup_state_file_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("stale state")

    command_suggest_fixup(abort=True, porcelain=True)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert not state_path.exists()


def test_line_selection_rejects_malformed_ids(temp_git_repo):
    source = temp_git_repo / "test.py"
    source.write_text("base\n")
    base = _commit(temp_git_repo, "Add test.py")
    _git(temp_git_repo, "commit", "--allow-empty", "-m", "Range commit")
    source.write_text("changed\n")
    command_start()

    with pytest.raises(CommandError, match="Invalid line ID: abc"):
        command_suggest_fixup_line("abc", boundary=base, file="test.py")
