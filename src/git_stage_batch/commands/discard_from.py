"""Discard from batch command implementation."""

from __future__ import annotations

import subprocess
from typing import Optional

from ..batch import (
    get_batch_baseline_commit,
    get_batch_diff,
    list_batch_files,
)
from ..data.session import snapshot_file_if_untracked
from ..staging.operations import build_target_working_tree_content_with_discarded_lines
from ..exceptions import exit_with_error
from ..i18n import _
from ..core.line_selection import parse_line_selection
from ..core.diff_parser import build_current_lines_from_patch_text, parse_unified_diff_into_single_hunk_patches
from ..utils.git import get_git_repository_root_path, require_git_repository, run_git_command
from ..utils.paths import get_context_lines


def command_discard_from_batch(batch_name: str, line_ids: Optional[str] = None, file_only: bool = False) -> None:
    """Remove batch changes from working tree."""
    require_git_repository()

    # If file_only, restore files to baseline state (wholesale)
    if file_only:
        baseline = get_batch_baseline_commit(batch_name)
        if not baseline:
            exit_with_error(_("Cannot determine baseline for batch '{name}'").format(name=batch_name))

        files = list_batch_files(batch_name)
        if not files:
            exit_with_error(_("Batch '{name}' is empty or does not exist").format(name=batch_name))

        # Snapshot files before modifying
        for file_path in files:
            snapshot_file_if_untracked(file_path)

        # Checkout files from baseline
        result = run_git_command(
            ["checkout", baseline, "--"] + files,
            check=False
        )
        if result.returncode != 0:
            exit_with_error(_("Failed to restore files from baseline: {error}").format(error=result.stderr))

        print(_("✓ Discarded all files from batch '{name}' from working tree (wholesale)").format(name=batch_name))
        print(_("Note: Batch '{name}' still exists (use 'drop' to delete it)").format(name=batch_name))
        return

    # Get batch diff
    context_lines = get_context_lines()
    diff = get_batch_diff(batch_name, context_lines)

    if not diff:
        exit_with_error(_("Batch '{name}' is empty or does not exist").format(name=batch_name))

    # Parse diff into patches
    patches = parse_unified_diff_into_single_hunk_patches(diff)

    if not patches:
        exit_with_error(_("No patches found in batch '{name}'").format(name=batch_name))

    # If line_ids specified, use line-level discarding
    if line_ids:
        selected_ids = parse_line_selection(line_ids)

        for patch in patches:
            patch_text = patch.to_patch_text()
            current_lines = build_current_lines_from_patch_text(patch_text)
            file_path = current_lines.path

            # Filter to selected lines
            filtered_lines = [line for line in current_lines.lines if line.id in selected_ids]
            if not filtered_lines:
                continue

            # Snapshot file before modifying
            snapshot_file_if_untracked(file_path)

            # Get current working tree content
            repo_root = get_git_repository_root_path()
            full_path = repo_root / file_path
            if full_path.exists():
                working_text = full_path.read_text(encoding="utf-8", errors="surrogateescape")
            else:
                exit_with_error(_("File not found in working tree: {file}").format(file=file_path))

            # Build target content with selected lines discarded
            target_content = build_target_working_tree_content_with_discarded_lines(
                current_lines, selected_ids, working_text
            )

            # Write to working tree
            repo_root = get_git_repository_root_path()
            full_path = repo_root / file_path
            full_path.write_text(target_content, encoding="utf-8", errors="surrogateescape")

        print(_("✓ Discarded selected lines from batch '{name}' from working tree").format(name=batch_name))
        print(_("Note: Batch '{name}' still exists (use 'drop' to delete it)").format(name=batch_name))
    else:
        # Apply entire patches in reverse to the working tree (strict mode)
        failed_files = []
        for patch in patches:
            file_path = patch.new_path

            # Try to apply the patch in reverse
            result = subprocess.run(
                ["git", "apply", "--reverse", "--unidiff-zero"],
                input=patch.to_patch_text(),
                capture_output=True,
                text=True,
                check=False
            )

            if result.returncode != 0:
                failed_files.append(file_path)

        if failed_files:
            exit_with_error(
                f"Failed to discard patches for files: {', '.join(failed_files)}\n" +
                f"Run 'git-stage-batch show --from {batch_name}' to review changes\n" +
                "Use --file or --line to discard compatible parts"
            )

        print(_("✓ Discarded changes from batch '{name}' from working tree").format(name=batch_name))
        print(_("Note: Batch '{name}' still exists (use 'drop' to delete it)").format(name=batch_name))
