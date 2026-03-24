"""Include from batch command implementation."""

from __future__ import annotations

import subprocess
import sys
from typing import Optional

from ..batch import (
    get_batch_baseline_commit,
    get_batch_diff,
)
from ..staging.operations import (
    build_target_index_content_with_selected_lines,
    update_index_with_blob_content,
)
from ..exceptions import exit_with_error
from ..i18n import _
from ..core.line_selection import parse_line_selection
from ..core.diff_parser import build_line_changes_from_patch_text, parse_unified_diff_into_single_hunk_patches
from ..utils.git import require_git_repository, run_git_command
from ..utils.paths import get_context_lines


def command_include_from_batch(batch_name: str, line_ids: Optional[str] = None, file_only: bool = False) -> None:
    """Stage changes from a batch to the index."""
    require_git_repository()

    # Refresh index to ensure git's cached stat info is up-to-date
    # This prevents "does not match index" errors when files have been manually modified
    run_git_command(["update-index", "--refresh"], check=False)

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

        # Apply patches for selected file
        failed_files = []
        for patch in patches:
            result = subprocess.run(
                ["git", "apply", "--index", "--unidiff-zero"],
                input=patch.to_patch_text(),
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode != 0:
                failed_files.append(patch.new_path)

        if failed_files:
            exit_with_error(
                _("Failed to apply patches for file: {file}\nRun 'git-stage-batch show --from {name}' to review changes").format(
                    file=selected_file,
                    name=batch_name
                )
            )

        print(_("✓ Staged changes for {file} from batch '{name}'").format(file=selected_file, name=batch_name), file=sys.stderr)
        return

    # If line_ids specified, use line-level staging
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

            # Get base content from batch baseline
            baseline_commit = get_batch_baseline_commit(batch_name)
            if not baseline_commit:
                exit_with_error(_("Cannot determine baseline for batch '{name}'").format(name=batch_name))

            # Read base file content from baseline commit
            base_result = run_git_command(
                ["show", f"{baseline_commit}:{file_path}"],
                check=False
            )
            base_text = base_result.stdout if base_result.returncode == 0 else ""

            # Build target content with selected lines
            target_content = build_target_index_content_with_selected_lines(
                line_changes, selected_ids, base_text
            )

            # Update index
            update_index_with_blob_content(file_path, target_content)

        print(_("✓ Staged selected lines from batch '{name}'").format(name=batch_name), file=sys.stderr)
    else:
        # Apply entire patches to working tree and index (strict mode)
        failed_files = []
        for patch in patches:
            file_path = patch.new_path

            # Try to apply the patch
            # Apply to both working tree and index using --index
            result = subprocess.run(
                ["git", "apply", "--index", "--unidiff-zero"],
                input=patch.to_patch_text(),
                capture_output=True,
                text=True,
                check=False
            )

            if result.returncode != 0:
                failed_files.append(file_path)

        if failed_files:
            exit_with_error(
                _("Failed to apply patches for files: {files}\nRun 'git-stage-batch show --from {name}' to review changes\nUse --file or --line to apply compatible parts").format(
                    files=', '.join(failed_files),
                    name=batch_name
                )
            )

        print(_("✓ Staged changes from batch '{name}'").format(name=batch_name), file=sys.stderr)
