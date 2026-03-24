"""Discard from batch command implementation."""

from __future__ import annotations

import subprocess
import sys
from typing import Optional

from ..batch import (
    get_batch_baseline_commit,
    get_batch_diff,
)
from ..data.session import snapshot_file_if_untracked
from ..staging.operations import build_target_working_tree_content_with_discarded_lines
from ..exceptions import exit_with_error
from ..i18n import _
from ..core.line_selection import parse_line_selection
from ..core.diff_parser import build_line_changes_from_patch_text, parse_unified_diff_into_single_hunk_patches
from ..utils.git import get_git_repository_root_path, require_git_repository, run_git_command
from ..utils.paths import get_context_lines


def command_discard_from_batch(batch_name: str, line_ids: Optional[str] = None, file_only: bool = False) -> None:
    """Remove batch changes from working tree."""
    require_git_repository()

    # Get batch diff
    context_lines = get_context_lines()
    diff = get_batch_diff(batch_name, context_lines)

    if not diff:
        exit_with_error(_("Batch '{name}' is empty or does not exist").format(name=batch_name))

    # Parse diff into patches
    patches = parse_unified_diff_into_single_hunk_patches(diff)

    if not patches:
        exit_with_error(_("No patches found in batch '{name}'").format(name=batch_name))

    # If file_only, filter to selected file only
    if file_only:
        from ..data.hunk_tracking import require_selected_hunk
        from ..data.line_state import load_line_changes_from_state

        require_selected_hunk()
        line_changes = load_line_changes_from_state()
        selected_file = line_changes.path

        # Filter patches to selected file
        patches = [p for p in patches if p.new_path == selected_file]

        if not patches:
            exit_with_error(_("Batch '{name}' has no changes for {file}").format(name=batch_name, file=selected_file))

        # Apply reverse patches for selected file
        failed_files = []
        for patch in patches:
            # Snapshot before modifying
            snapshot_file_if_untracked(selected_file)

            result = subprocess.run(
                ["git", "apply", "--reverse", "--unidiff-zero"],
                input=patch.to_patch_text(),
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode != 0:
                failed_files.append(patch.new_path)

        if failed_files:
            exit_with_error(
                _("Failed to discard patches for file: {file}\nRun 'git-stage-batch show --from {name}' to review changes").format(
                    file=selected_file,
                    name=batch_name
                )
            )

        print(_("✓ Discarded changes for {file} from batch '{name}' from working tree").format(file=selected_file, name=batch_name), file=sys.stderr)
        print(_("Note: Batch '{name}' still exists (use 'drop' to delete it)").format(name=batch_name), file=sys.stderr)
        return

    # If line_ids specified, use line-level discarding
    if line_ids:
        selected_ids = parse_line_selection(line_ids)

        for patch in patches:
            patch_text = patch.to_patch_text()
            line_changes = build_line_changes_from_patch_text(patch_text)
            file_path = line_changes.path

            # Filter to selected lines
            filtered_lines = [line for line in line_changes.lines if line.id in selected_ids]
            if not filtered_lines:
                continue

            # Snapshot file before modifying
            snapshot_file_if_untracked(file_path)

            # Get selected working tree content
            repo_root = get_git_repository_root_path()
            full_path = repo_root / file_path
            if full_path.exists():
                working_text = full_path.read_text(encoding="utf-8", errors="surrogateescape")
            else:
                exit_with_error(_("File not found in working tree: {file}").format(file=file_path))

            # Build target content with selected lines discarded
            target_content = build_target_working_tree_content_with_discarded_lines(
                line_changes, selected_ids, working_text
            )

            # Write to working tree
            repo_root = get_git_repository_root_path()
            full_path = repo_root / file_path
            full_path.write_text(target_content, encoding="utf-8", errors="surrogateescape")

        print(_("✓ Discarded selected lines from batch '{name}' from working tree").format(name=batch_name), file=sys.stderr)
        print(_("Note: Batch '{name}' still exists (use 'drop' to delete it)").format(name=batch_name), file=sys.stderr)
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
                _("Failed to discard patches for files: {files}\nRun 'git-stage-batch show --from {name}' to review changes\nUse --file or --line to discard compatible parts").format(
                    files=', '.join(failed_files),
                    name=batch_name
                )
            )

        print(_("✓ Discarded changes from batch '{name}' from working tree").format(name=batch_name), file=sys.stderr)
        print(_("Note: Batch '{name}' still exists (use 'drop' to delete it)").format(name=batch_name), file=sys.stderr)
