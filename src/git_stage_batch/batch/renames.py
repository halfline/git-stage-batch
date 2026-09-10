"""Persisted relationships between the two paths of a saved rename."""

from __future__ import annotations

from collections.abc import Sequence

from .state.compatibility_metadata import write_file_backed_batch_metadata
from .state.metadata_types import BatchFileMetadataDict
from .state.query import get_batch_commit_sha, read_batch_metadata
from .state.references import sync_batch_state_refs
from ..core.models import RenameChange
from ..exceptions import CommandError
from ..git_paths import display_path
from ..i18n import _
from ..utils.git_object_io import create_git_blob
from ..utils.repository_buffers import read_git_object_buffer_or_none


def record_batch_renames(
    batch_name: str,
    renames: Sequence[RenameChange],
    comparison_base: str,
) -> None:
    """Link complete captured paths and retain the exact pre-rename content."""
    if not renames:
        return
    metadata = read_batch_metadata(batch_name)
    content_commit = get_batch_commit_sha(batch_name)
    files = metadata.get("files", {})
    for rename in renames:
        source = files.get(rename.old_path)
        destination = files.get(rename.new_path)
        if source is None or destination is None:
            raise CommandError(_("A saved rename must contain both paths."))
        with_buffer = read_git_object_buffer_or_none(
            f"{comparison_base}:{rename.old_path}"
        )
        if with_buffer is None:
            raise CommandError(
                _("The original content of the renamed file is missing.")
            )
        with with_buffer as buffer:
            base_blob = create_git_blob(buffer.byte_chunks())
        target_buffer = read_git_object_buffer_or_none(
            f"{content_commit}:{rename.new_path}"
        )
        if target_buffer is None:
            raise CommandError(_("The saved rename destination content is missing."))
        with target_buffer as buffer:
            target_blob = create_git_blob(buffer.byte_chunks())
        source["rename_to"] = rename.new_path
        destination["rename_from"] = rename.old_path
        destination["rename_base_blob"] = base_blob
        destination["rename_target_blob"] = target_blob
    model = write_file_backed_batch_metadata(batch_name, metadata)
    sync_batch_state_refs(batch_name, model)


def require_complete_rename_selection(
    files: dict[str, BatchFileMetadataDict],
    *,
    selected_lines: bool = False,
) -> None:
    """Refuse selections that would separate a rename's paired paths."""
    for path, metadata in files.items():
        partner = metadata.get("rename_from", metadata.get("rename_to"))
        if partner is not None and (partner not in files or selected_lines):
            raise CommandError(
                _(
                    "Select both complete paths of the saved rename: {file} and {partner}."
                ).format(
                    file=display_path(path),
                    partner=display_path(partner),
                )
            )


def refuse_rename_rewrite(files: dict[str, BatchFileMetadataDict]) -> None:
    """Keep per-file ownership rewrites from dropping a saved path relationship."""
    if any("rename_from" in meta or "rename_to" in meta for meta in files.values()):
        raise CommandError(
            _(
                "Saved renames cannot be split or rewritten. Apply or discard both paths, or drop the batch."
            )
        )
