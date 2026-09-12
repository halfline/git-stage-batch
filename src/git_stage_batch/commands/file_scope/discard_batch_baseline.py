"""Carry selected staged context into an existing batch's baseline."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from ...batch.merge.baseline_reference_translation import (
    translate_ownership_baseline_references,
)
from ...batch.ownership.metadata_loading import acquire_ownership_for_metadata_dict
from ...batch.ownership.replacement_units import ReplacementUnitOrigin
from ...batch.realized_file_content import build_realized_buffer_from_lines
from ...batch.state.compatibility_metadata import write_file_backed_batch_metadata
from ...batch.state.metadata_types import add_ownership_metadata
from ...batch.state.query import get_batch_commit_sha, read_batch_metadata
from ...batch.state.references import sync_batch_state_refs
from ...exceptions import exit_with_error
from ...core.buffer import buffer_byte_chunks
from ...core.mapped_storage import MappedRecordVector
from ...git_paths import decode_path, display_path, nul_records
from ...i18n import _
from ...utils.git_command import run_git_command
from ...utils.git_index import (
    GitIndexEntryUpdate,
    git_commit_tree,
    git_read_tree,
    git_update_index_entries,
    git_write_tree,
    temp_git_index,
)
from ...utils.git_object_io import (
    create_git_blob,
    get_git_object_type,
    list_git_tree_blobs,
)
from ...utils.repository_buffers import read_git_object_buffer_or_empty
from ...utils.session_start_point import session_comparison_base


def _content_identity(lines: Sequence[bytes]) -> tuple[int, ...]:
    digest = hashlib.sha256()
    for chunk in buffer_byte_chunks(lines):
        digest.update(chunk)
    value = digest.digest()
    return tuple(
        int.from_bytes(value[offset : offset + 8], "little")
        for offset in range(0, 32, 8)
    )


def prepare_existing_discard_baseline(
    batch_name: str,
    index_tree: str,
    paths: list[str],
) -> None:
    """Rebase staged context once, preserving unrelated files and saved claims.

    Only the selected index-versus-HEAD changes participate in this merge.
    Unstaged changes and later committed context cannot enter the baseline.
    The enclosing command checkpoint rolls back this publication on failure.
    """
    head = session_comparison_base()
    head_entries = list_git_tree_blobs(head, paths)
    index_entries = list_git_tree_blobs(index_tree, paths)
    staged_paths = [
        path for path in paths if head_entries.get(path) != index_entries.get(path)
    ]
    if not staged_paths:
        return
    metadata = read_batch_metadata(batch_name)
    baseline = metadata.get("baseline")
    content_commit = get_batch_commit_sha(batch_name)
    if baseline is None or content_commit is None:
        raise ValueError("existing batch omitted its baseline or content commit")
    baseline_entries = list_git_tree_blobs(baseline, staged_paths)
    if all(
        baseline_entries.get(path) == index_entries.get(path) for path in staged_paths
    ):
        return

    with temp_git_index() as env:
        git_read_tree(head, env=env)
        git_update_index_entries(
            [
                GitIndexEntryUpdate(path, entry.mode, entry.blob_sha)
                if (entry := index_entries.get(path)) is not None
                else GitIndexEntryUpdate(path, force_remove=True)
                for path in staged_paths
            ],
            env=env,
        )
        selected_index_tree = git_write_tree(env=env)
    common_parent = (
        head
        if get_git_object_type(head) == "commit"
        else git_commit_tree(head, message="Unborn comparison base")
    )
    old_tree = run_git_command(
        ["rev-parse", f"{baseline}^{{tree}}"],
        requires_index_lock=False,
    ).stdout.strip()
    old_side = git_commit_tree(
        old_tree, parents=[common_parent], message="Saved comparison base"
    )
    staged_side = git_commit_tree(
        selected_index_tree,
        parents=[common_parent],
        message="Selected staged context",
    )
    result = run_git_command(
        # This update is scoped to exact selected paths. Rename detection can
        # compare every deleted file with every added file and is unnecessary.
        [
            "-c",
            "merge.renames=false",
            "merge-tree",
            "--write-tree",
            old_side,
            staged_side,
        ],
        check=False,
        requires_index_lock=False,
    )
    if result.returncode != 0:
        _refuse_baseline_update(
            batch_name,
            staged_paths[0],
            "staged context conflicts with the saved baseline",
        )
    merged_tree = result.stdout.splitlines()[0]
    if merged_tree == old_tree:
        return
    changed_paths = run_git_command(
        ["diff", "--name-only", "-z", "--no-renames", old_tree, merged_tree],
        text_output=False,
        requires_index_lock=False,
    ).stdout
    selected_paths = set(staged_paths)
    if any(
        decode_path(path) not in selected_paths for path in nul_records(changed_paths)
    ):
        _refuse_baseline_update(
            batch_name,
            staged_paths[0],
            "staged context would change an unselected baseline path",
        )
    merged_entries = list_git_tree_blobs(merged_tree, staged_paths)
    updates: list[GitIndexEntryUpdate] = []
    for path in staged_paths:
        entry = merged_entries.get(path)
        if entry == baseline_entries.get(path):
            continue
        file_metadata = metadata.get("files", {}).get(path)
        if file_metadata is None:
            updates.append(
                GitIndexEntryUpdate(path, entry.mode, entry.blob_sha)
                if entry is not None
                else GitIndexEntryUpdate(path, force_remove=True)
            )
            continue
        if (
            file_metadata.get("file_type") is not None
            or file_metadata.get("change_type") == "deleted"
        ):
            _refuse_baseline_update(
                batch_name, path, "staged context overlaps a saved whole-file change"
            )
        with (
            read_git_object_buffer_or_empty(f"{baseline}:{path}") as old_lines,
            read_git_object_buffer_or_empty(f"{merged_tree}:{path}") as new_lines,
            read_git_object_buffer_or_empty(
                f"{file_metadata['batch_source_commit']}:{path}"
            ) as source_lines,
            acquire_ownership_for_metadata_dict(file_metadata) as ownership,
            MappedRecordVector(len(ownership.deletions), "QQQQ") as deletion_identities,
        ):
            try:
                if any(claim.source_alternative for claim in ownership.deletions):
                    raise ValueError(
                        "staged context overlaps a saved source alternative"
                    )
                reference_count = sum(
                    len(claim.baseline_references)
                    for claim in ownership.presence_claims
                )
                for claim in ownership.deletions:
                    deletion_identities.append(_content_identity(claim.content_lines))
                translate_ownership_baseline_references(
                    ownership,
                    old_lines,
                    new_lines,
                    replacement_origin_source_lines=old_lines,
                )
                if (
                    any(
                        claim.baseline_reference is None
                        for claim in ownership.deletions
                    )
                    or sum(
                        len(claim.baseline_references)
                        for claim in ownership.presence_claims
                    )
                    != reference_count
                    or any(
                        _content_identity(claim.content_lines)
                        != deletion_identities[number]
                        for number, claim in enumerate(ownership.deletions)
                    )
                ):
                    raise ValueError(
                        "staged context overlaps or obscures saved ownership"
                    )
                for number, unit in enumerate(ownership.replacement_units):
                    origin = unit.origin
                    if origin is None:
                        continue
                    reference = origin.baseline_reference
                    assert reference is not None
                    start = (reference.after_line or 0) + 1
                    ownership.replacement_units[number] = unit.with_origin(
                        ReplacementUnitOrigin(
                            old_start=start,
                            old_end=start + origin.old_line_count - 1,
                            new_start=origin.new_start,
                            new_end=origin.new_end,
                            baseline_reference=reference,
                        )
                    )
                with build_realized_buffer_from_lines(
                    new_lines, source_lines, ownership
                ) as realized:
                    blob = create_git_blob(realized.byte_chunks())
                add_ownership_metadata(file_metadata, ownership.to_metadata_dict())
                file_metadata["change_type"] = (
                    "modified" if entry is not None else "added"
                )
                updates.append(
                    GitIndexEntryUpdate(path, file_metadata.get("mode", "100644"), blob)
                )
            except ValueError as error:
                _refuse_baseline_update(batch_name, path, str(error))

    new_baseline = git_commit_tree(
        merged_tree, message="Baseline with selected staged context"
    )
    metadata["baseline"] = new_baseline
    with temp_git_index() as env:
        git_read_tree(content_commit, env=env)
        git_update_index_entries(updates, env=env)
        new_content_tree = git_write_tree(env=env)
    new_content = git_commit_tree(
        new_content_tree,
        parents=[new_baseline, content_commit],
        message=f"Batch: {batch_name}",
    )
    model = write_file_backed_batch_metadata(batch_name, metadata)
    sync_batch_state_refs(batch_name, model, content_commit=new_content)


def _refuse_baseline_update(batch_name: str, path: str, reason: str) -> None:
    exit_with_error(
        _(
            "Cannot discard file to batch: batch source is stale and remapping failed.\n"
            "File: {file}\n"
            "Batch: {batch}\n"
            "Error: {error}"
        ).format(file=display_path(path), batch=batch_name, error=reason)
    )
