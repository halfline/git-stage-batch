from types import SimpleNamespace

import git_stage_batch.commands.file_scope.multi_file_actions as multi_file_actions
from git_stage_batch.exceptions import CommandError


class _FileScope:
    def __init__(self, files=(), optional_file=None):
        self.files = tuple(files)
        self._optional_file = optional_file

    @property
    def is_multiple(self):
        return len(self.files) > 1

    def optional_file(self):
        return self._optional_file


class _Checkpoint:
    def __init__(self, calls, operation, worktree_paths):
        self._calls = calls
        self._operation = operation
        self._worktree_paths = worktree_paths

    def __enter__(self):
        self._calls.append(("enter", self._operation, self._worktree_paths))

    def __exit__(self, exc_type, exc, traceback):
        self._calls.append(("exit",))
        return False


def _capture_undo_checkpoints(monkeypatch):
    calls = []

    def fake_undo_checkpoint(
        operation,
        *,
        worktree_paths=None,
        rollback_on_error=False,
    ):
        assert rollback_on_error is True
        return _Checkpoint(calls, operation, worktree_paths)

    monkeypatch.setattr(multi_file_actions, "undo_checkpoint", fake_undo_checkpoint)
    return calls


def test_run_for_each_resolved_file_wraps_multiple_files(monkeypatch):
    checkpoint_calls = _capture_undo_checkpoints(monkeypatch)
    callback_calls = []

    multi_file_actions.run_for_each_resolved_file(
        _FileScope(files=("alpha.txt", "beta.txt")),
        callback_calls.append,
        undo_operation="include --from scratch",
        worktree_paths=("alpha.txt", "beta.txt"),
    )

    assert checkpoint_calls == [
        (
            "enter",
            "include --from scratch --files alpha.txt beta.txt",
            ["alpha.txt", "beta.txt"],
        ),
        ("exit",),
    ]
    assert callback_calls == ["alpha.txt", "beta.txt"]


def test_discard_each_resolved_file_expands_live_checkpoint_paths(monkeypatch):
    checkpoint_calls = _capture_undo_checkpoints(monkeypatch)
    discard_calls = []
    expansion_calls = []

    def expand_paths(paths):
        expansion_calls.append(paths)
        return [*paths, "rename-source.txt"]

    monkeypatch.setattr(
        multi_file_actions,
        "checkpoint_paths_for_live_files",
        expand_paths,
    )
    monkeypatch.setattr(
        multi_file_actions,
        "_prepare_live_multi_file_action",
        lambda: None,
    )
    monkeypatch.setattr(
        multi_file_actions._discard_file,
        "discard_file_changes",
        lambda file_path, *, auto_advance=None: discard_calls.append(
            (file_path, auto_advance)
        ),
    )

    multi_file_actions.discard_each_resolved_file(
        ["rename-target.txt", "other.txt"],
        auto_advance=False,
    )

    assert expansion_calls == [["rename-target.txt", "other.txt"]]
    assert checkpoint_calls == [
        (
            "enter",
            "discard --files rename-target.txt other.txt",
            ["rename-target.txt", "other.txt", "rename-source.txt"],
        ),
        ("exit",),
    ]
    assert discard_calls == [
        ("rename-target.txt", False),
        ("other.txt", False),
    ]


def test_run_for_each_resolved_file_dispatches_optional_file(monkeypatch):
    checkpoint_calls = _capture_undo_checkpoints(monkeypatch)
    callback_calls = []

    multi_file_actions.run_for_each_resolved_file(
        _FileScope(optional_file="alpha.txt"),
        callback_calls.append,
        undo_operation="include --from scratch",
    )

    assert checkpoint_calls == []
    assert callback_calls == ["alpha.txt"]


def test_run_for_each_resolved_file_rejects_line_ids_for_multiple_files():
    callback_calls = []

    try:
        multi_file_actions.run_for_each_resolved_file(
            _FileScope(files=("alpha.txt", "beta.txt")),
            callback_calls.append,
            line_ids="1",
        )
    except CommandError as error:
        assert "Cannot use --lines with multiple files" in error.message
    else:
        raise AssertionError("expected CommandError")

    assert callback_calls == []


def test_include_each_resolved_file_reports_aggregate_result(
    monkeypatch,
    capsys,
):
    checkpoint_calls = _capture_undo_checkpoints(monkeypatch)
    include_calls = []
    selection_calls = []
    show_calls = []

    def fake_include_file(file_path, *, quiet=False, advance=True):
        include_calls.append((file_path, quiet, advance))
        return {"alpha.txt": 2, "beta.txt": 0}[file_path]

    monkeypatch.setattr(
        multi_file_actions,
        "_prepare_live_multi_file_action",
        lambda: None,
    )
    monkeypatch.setattr(
        multi_file_actions._include_file,
        "include_file_changes",
        fake_include_file,
    )
    monkeypatch.setattr(
        multi_file_actions,
        "select_next_change_after_action",
        lambda *, auto_advance=None: selection_calls.append(auto_advance) or True,
    )
    monkeypatch.setattr(
        multi_file_actions,
        "show_selected_change",
        lambda: show_calls.append("show"),
    )

    multi_file_actions.include_each_resolved_file(
        ["alpha.txt", "beta.txt"],
        auto_advance=True,
    )

    captured = capsys.readouterr()
    assert checkpoint_calls == [
        (
            "enter",
            "include --files alpha.txt beta.txt",
            ["alpha.txt", "beta.txt"],
        ),
        ("exit",),
    ]
    assert include_calls == [
        ("alpha.txt", True, False),
        ("beta.txt", True, False),
    ]
    assert selection_calls == [True]
    assert show_calls == ["show"]
    assert "Staged 2 hunks from alpha.txt" in captured.err


def test_skip_each_resolved_file_stops_after_empty_result(
    monkeypatch,
    capsys,
):
    checkpoint_calls = _capture_undo_checkpoints(monkeypatch)
    skip_calls = []
    selection_calls = []

    def fake_skip_file(file_path, *, quiet=False, advance=True):
        skip_calls.append((file_path, quiet, advance))
        return 0

    monkeypatch.setattr(
        multi_file_actions,
        "_prepare_live_multi_file_action",
        lambda: None,
    )
    monkeypatch.setattr(
        multi_file_actions._skip_file,
        "skip_file_changes",
        fake_skip_file,
    )
    monkeypatch.setattr(
        multi_file_actions,
        "select_next_change_after_action",
        lambda *, auto_advance=None: selection_calls.append(auto_advance) or True,
    )

    multi_file_actions.skip_each_resolved_file(["alpha.txt", "beta.txt"])

    captured = capsys.readouterr()
    assert checkpoint_calls == [
        (
            "enter",
            "skip --files alpha.txt beta.txt",
            ["alpha.txt", "beta.txt"],
        ),
        ("exit",),
    ]
    assert skip_calls == [
        ("alpha.txt", True, False),
        ("beta.txt", True, False),
    ]
    assert selection_calls == []
    assert "No hunks skipped from matched files." in captured.err


def test_discard_to_batch_each_resolved_file_reports_batch_result(
    monkeypatch,
    capsys,
):
    checkpoint_calls = _capture_undo_checkpoints(monkeypatch)
    discard_calls = []
    selection_calls = []
    show_calls = []

    def fake_discard_files_to_batch(
        batch_name,
        files,
        *,
        quiet=False,
        advance=True,
        auto_advance=None,
    ):
        discard_calls.append((batch_name, files, quiet, advance, auto_advance))
        return SimpleNamespace(
            discarded_hunks=3,
            discarded_files=["alpha.txt", "beta.txt"],
        )

    monkeypatch.setattr(
        multi_file_actions,
        "discard_files_to_batch",
        fake_discard_files_to_batch,
    )
    monkeypatch.setattr(
        multi_file_actions,
        "checkpoint_paths_for_live_files",
        lambda files: [*files, "rename-source.txt"],
    )
    monkeypatch.setattr(
        multi_file_actions,
        "select_next_change_after_action",
        lambda *, auto_advance=None: selection_calls.append(auto_advance) or False,
    )
    monkeypatch.setattr(
        multi_file_actions,
        "show_selected_change",
        lambda: show_calls.append("show"),
    )

    multi_file_actions.discard_to_batch_each_resolved_file(
        "scratch",
        ["alpha.txt", "beta.txt"],
        auto_advance=False,
    )

    captured = capsys.readouterr()
    assert checkpoint_calls == [
        (
            "enter",
            "discard --to scratch --files alpha.txt beta.txt",
            ["alpha.txt", "beta.txt", "rename-source.txt"],
        ),
        ("exit",),
    ]
    assert discard_calls == [
        (
            "scratch",
            ["alpha.txt", "beta.txt"],
            True,
            False,
            False,
        )
    ]
    assert selection_calls == [False]
    assert show_calls == []
    assert "Saved 3 hunks from 2 files to batch 'scratch'" in captured.err
