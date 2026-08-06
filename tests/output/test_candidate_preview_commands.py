"""Tests for terminal-safe candidate preview command text."""

from git_stage_batch.batch.operation_candidate_types import OperationCandidatePreview
from git_stage_batch.git_paths import terminal_safe_shell_quote
from git_stage_batch.output.candidate_preview_commands import (
    execute_candidate_command,
    show_candidate_command,
)


def _preview(*, batch_name: str, file_path: str) -> OperationCandidatePreview:
    return OperationCandidatePreview(
        operation="apply",
        batch_name=batch_name,
        file_path=file_path,
        ordinal=1,
        count=2,
        candidate_id="candidate-1",
        targets=(),
        batch_fingerprint="batch",
        target_fingerprints={},
        target_result_fingerprints={},
        scope_fingerprint="scope",
    )


def test_candidate_commands_quote_selectors_and_terminal_control_paths():
    """Candidate commands must be safe to display and paste into a shell."""
    file_path = "evil\x1b[2Jname\nnext.txt"
    preview = _preview(batch_name="cleanup;next", file_path=file_path)

    show_command = show_candidate_command(preview, preview.ordinal)
    execute_command = execute_candidate_command(preview)

    for command in (show_command, execute_command):
        assert file_path not in command
        assert "\x1b" not in command
        assert "\nnext.txt" not in command
        assert terminal_safe_shell_quote(file_path) in command
        assert terminal_safe_shell_quote("cleanup;next:apply:1") in command
