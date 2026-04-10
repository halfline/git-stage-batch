"""Include from batch command implementation."""

from __future__ import annotations

import sys
from typing import Optional

from ..batch.display import filter_batch_by_display_ids
from ..batch.merge import merge_batch
from ..batch.query import read_batch_metadata
from ..batch.validation import batch_exists
from ..core.line_selection import parse_line_selection
from ..exceptions import exit_with_error, MergeError
from ..i18n import _
from ..staging.operations import update_index_with_blob_content
from ..utils.git import require_git_repository, run_git_command


def command_include_from_batch(batch_name: str, line_ids: Optional[str] = None, file_only: bool = False) -> None:
    """Stage batch changes to index using structural merge."""
    require_git_repository()

    # Refresh index to ensure git's cached stat info is up-to-date
    run_git_command(["update-index", "--refresh"], check=False)

    # Check batch exists
    if not batch_exists(batch_name):
        exit_with_error(_("Batch '{name}' does not exist").format(name=batch_name))

    # Read batch metadata
    metadata = read_batch_metadata(batch_name)
    files = metadata.get("files", {})

    if not files:
        exit_with_error(_("Batch '{name}' is empty").format(name=batch_name))

    # If file_only, filter to current file
    if file_only:
        from ..data.hunk_tracking import require_current_hunk_and_check_stale
        from ..data.line_state import load_current_lines_from_state

        require_current_hunk_and_check_stale()
        current_lines = load_current_lines_from_state()
        current_file = current_lines.path

        if current_file not in files:
            exit_with_error(_("Batch '{name}' has no changes for {file}").format(name=batch_name, file=current_file))

        files = {current_file: files[current_file]}

    # Parse line selection if provided
    selected_ids = None
    if line_ids:
        selected_ids = parse_line_selection(line_ids)

    # Apply all files in batch
    failed_files = []

    for file_path, file_meta in files.items():
        try:
            # Get batch source commit content
            batch_source_commit = file_meta["batch_source_commit"]
            batch_source_result = run_git_command(["show", f"{batch_source_commit}:{file_path}"], check=False)
            if batch_source_result.returncode != 0:
                failed_files.append(file_path)
                continue
            batch_source_content = batch_source_result.stdout

            # Get current index content
            index_result = run_git_command(["show", f":{file_path}"], check=False)
            if index_result.returncode == 0:
                index_content = index_result.stdout
            else:
                index_content = ""

            # Get ownership from metadata
            from ..batch.ownership import BatchOwnership
            ownership = BatchOwnership.from_metadata_dict(file_meta)

            # Filter by line IDs if specified
            if selected_ids:
                ownership = filter_batch_by_display_ids(
                    ownership,
                    batch_source_content,
                    selected_ids
                )

                # If nothing selected for this file, skip it
                if ownership.is_empty():
                    continue

            # Perform structural merge
            merged_content = merge_batch(
                batch_source_content,
                ownership,
                index_content
            )

            # Update index with merged content
            update_index_with_blob_content(file_path, merged_content)

        except MergeError as e:
            print(_("Error merging {file}: {error}").format(file=file_path, error=str(e)), file=sys.stderr)
            failed_files.append(file_path)
        except Exception as e:
            print(_("Error staging {file}: {error}").format(file=file_path, error=str(e)), file=sys.stderr)
            failed_files.append(file_path)

    if failed_files:
        exit_with_error(
            _("Failed to stage batch for files: {files}\nRun 'git-stage-batch show --from {name}' to review changes").format(
                files=', '.join(failed_files),
                name=batch_name
            )
        )

    if line_ids:
        print(_("✓ Staged selected lines from batch '{name}'").format(name=batch_name), file=sys.stderr)
    elif file_only:
        print(_("✓ Staged changes for {file} from batch '{name}'").format(file=list(files.keys())[0], name=batch_name), file=sys.stderr)
    else:
        print(_("✓ Staged changes from batch '{name}'").format(name=batch_name), file=sys.stderr)
