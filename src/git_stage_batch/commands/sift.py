"""Sift batch command: remove already-present portions from a batch.

For text files, sift derives a new destination batch whose source content is the
*target* file content the original batch wanted to realize, and whose ownership
represents the remaining delta needed to merge that target with the current
working tree.

That means sifted text batches intentionally use slightly different persistence
semantics than ordinary text batches:

- the synthetic batch source commit stores the target file content directly
- the batch ref for that file also stores that same target file content directly
- the ownership describes how to merge that target with the current working tree

This is deliberate. Validation still proves the real semantic invariant:
merging the destination representation against the current working tree must
produce the intended target content.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from ..batch.comparison import SemanticChangeKind, derive_semantic_change_runs
from ..batch.merge import merge_batch
from ..exceptions import MergeError
from ..batch.metadata_validation import read_validated_batch_metadata
from ..batch.operations import create_batch, delete_batch
from ..batch.ownership import BatchOwnership, DeletionClaim
from ..batch.query import get_batch_baseline_commit, read_batch_metadata
from ..batch.state_refs import (
    delete_batch_state_refs,
    get_batch_content_ref_name,
    sync_batch_state_refs,
)
from ..batch.storage import add_binary_file_to_batch, _build_realized_content, _update_batch_commit
from ..batch.validation import batch_exists, validate_batch_name
from ..core.line_selection import format_line_ids
from ..core.models import BinaryFileChange
from ..exceptions import BatchMetadataError, exit_with_error
from ..i18n import _
from ..utils.file_io import write_text_file_contents
from ..utils.git import (
    create_git_blob,
    get_git_repository_root_path,
    require_git_repository,
    run_git_command,
)
from ..utils.paths import (
    get_batch_metadata_file_path,
)


def _set_batch_baseline(
    batch_name: str,
    baseline_commit: str,
) -> None:
    """Update a batch's baseline commit in metadata."""
    metadata = read_batch_metadata(batch_name)
    metadata["baseline"] = baseline_commit
    metadata_path = get_batch_metadata_file_path(batch_name)
    write_text_file_contents(metadata_path, json.dumps(metadata, indent=2))



def create_synthetic_batch_source_commit(
    baseline_commit: str,
    file_path: str,
    file_content: bytes,
    file_mode: str = "100644",
) -> str:
    """Create a synthetic batch source commit for a single file.

    The created commit has ``baseline_commit`` as its parent, but the file at
    ``file_path`` contains ``file_content``. Sift uses this to persist target
    content for text files in a batch-source commit even when that content does
    not exist as-is in history.
    """
    blob_sha = create_git_blob([file_content])

    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".index") as tmp:
        temp_index = tmp.name

    try:
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = temp_index

        subprocess.run(
            ["git", "read-tree", baseline_commit],
            env=env,
            capture_output=True,
            check=True,
        )

        subprocess.run(
            [
                "git",
                "update-index",
                "--add",
                "--cacheinfo",
                f"{file_mode},{blob_sha},{file_path}",
            ],
            env=env,
            capture_output=True,
            check=True,
        )

        tree_result = subprocess.run(
            ["git", "write-tree"],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        new_tree = tree_result.stdout.strip()
    finally:
        if os.path.exists(temp_index):
            os.unlink(temp_index)

    commit_result = subprocess.run(
        ["git", "commit-tree", new_tree, "-p", baseline_commit, "-m", f"Sift batch source for {file_path}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return commit_result.stdout.strip()



def add_sifted_text_file_to_batch(
    batch_name: str,
    file_path: str,
    target_content: bytes,
    ownership: BatchOwnership,
    file_mode: str = "100644",
) -> None:
    """Persist a sifted text file into a batch.

    ``target_content`` is the file content the sifted batch wants to realize
    when merged with an appropriate working tree. For sifted text files, the
    synthetic batch-source commit stores this target content directly, and the
    batch ref stores the same target content directly.

    The ownership is expressed in ``target_content`` coordinate space and is
    validated separately against the working tree before this helper is called.
    """
    validate_batch_name(batch_name)

    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    baseline_commit = get_batch_baseline_commit(batch_name)
    if not baseline_commit:
        exit_with_error(_("Batch '{name}' has no baseline commit").format(name=batch_name))

    batch_source_commit = create_synthetic_batch_source_commit(
        baseline_commit=baseline_commit,
        file_path=file_path,
        file_content=target_content,
        file_mode=file_mode,
    )

    target_blob_sha = create_git_blob([target_content])

    metadata = read_batch_metadata(batch_name)
    if "files" not in metadata:
        metadata["files"] = {}

    metadata["files"][file_path] = {
        "batch_source_commit": batch_source_commit,
        **ownership.to_metadata_dict(),
        "mode": file_mode,
    }

    metadata_path = get_batch_metadata_file_path(batch_name)
    write_text_file_contents(metadata_path, json.dumps(metadata, indent=2))

    _update_batch_commit(batch_name, file_path, target_blob_sha, file_mode)



def command_sift_batch(source_batch: str, dest_batch: str) -> None:
    """Sift a batch to remove portions already present at tip."""
    require_git_repository()
    validate_batch_name(source_batch)
    validate_batch_name(dest_batch)

    if not batch_exists(source_batch):
        exit_with_error(_("Batch '{name}' does not exist").format(name=source_batch))

    try:
        source_metadata = read_validated_batch_metadata(source_batch)
    except BatchMetadataError as e:
        exit_with_error(str(e))

    source_files = source_metadata.get("files", {})
    if not source_files:
        _handle_empty_source_batch(source_batch, dest_batch)
        return

    in_place = source_batch == dest_batch
    dest_created = False

    if not in_place:
        if batch_exists(dest_batch):
            exit_with_error(
                _(
                    "Destination batch '{name}' already exists. "
                    "Drop it first or use --to {source} for in-place sift."
                ).format(
                    name=dest_batch,
                    source=source_batch,
                )
            )

        create_batch(dest_batch, note=f"Sifted from {source_batch}")
        dest_created = True
        _set_batch_baseline(dest_batch, source_metadata.get("baseline"))

    try:
        repo_root = get_git_repository_root_path()
        stats = {
            "total_files": len(source_files),
            "files_removed": 0,
            "files_retained": 0,
        }
        retained_files = []

        for file_path, file_meta in source_files.items():
            is_binary = file_meta.get("file_type") == "binary"

            if is_binary:
                result = _compute_sifted_binary_file(
                    source_batch,
                    file_path,
                    file_meta,
                    repo_root,
                )
            else:
                result = _compute_sifted_text_file(
                    source_batch,
                    file_path,
                    file_meta,
                    repo_root,
                )

            if result is None:
                stats["files_removed"] += 1
            else:
                stats["files_retained"] += 1
                retained_files.append((file_path, file_meta, result))

        if in_place:
            _perform_atomic_in_place_sift(
                batch_name=source_batch,
                retained_files=retained_files,
                source_metadata=source_metadata,
            )
        else:
            for file_path, file_meta, result in retained_files:
                if result["type"] == "binary":
                    add_binary_file_to_batch(
                        dest_batch,
                        result["binary_change"],
                        file_mode=file_meta.get("mode", "100644"),
                    )
                else:
                    add_sifted_text_file_to_batch(
                        batch_name=dest_batch,
                        file_path=file_path,
                        target_content=result["target_content"],
                        ownership=result["ownership"],
                        file_mode=file_meta.get("mode", "100644"),
                    )
    except MergeError as e:
        if dest_created and batch_exists(dest_batch):
            delete_batch(dest_batch)
        exit_with_error(
            _("Could not sift batch '{source}': {error}").format(
                source=source_batch,
                error=e,
            )
        )

    if stats["files_retained"] == 0:
        if in_place:
            print(
                _(
                    "✓ Sifted batch '{name}' in-place: all content already present at tip (batch now empty)"
                ).format(name=source_batch),
                file=sys.stderr,
            )
        else:
            print(
                _(
                    "✓ Sifted batch '{source}' to '{dest}': all content already present at tip (destination empty)"
                ).format(
                    source=source_batch,
                    dest=dest_batch,
                ),
                file=sys.stderr,
            )
    else:
        if in_place:
            print(
                _(
                    "✓ Sifted batch '{name}' in-place: {retained} of {total} files still need changes"
                ).format(
                    name=source_batch,
                    retained=stats["files_retained"],
                    total=stats["total_files"],
                ),
                file=sys.stderr,
            )
        else:
            print(
                _(
                    "✓ Sifted batch '{source}' to '{dest}': {retained} of {total} files still need changes"
                ).format(
                    source=source_batch,
                    dest=dest_batch,
                    retained=stats["files_retained"],
                    total=stats["total_files"],
                ),
                file=sys.stderr,
            )



def _perform_atomic_in_place_sift(
    batch_name: str,
    retained_files: list,
    source_metadata: dict,
) -> None:
    """Perform atomic in-place sift by writing to a temp batch then replacing."""
    temp_batch_name = f"{batch_name}-sift-temp"

    if batch_exists(temp_batch_name):
        delete_batch(temp_batch_name)

    create_batch(temp_batch_name, note=f"Temporary sift of {batch_name}")
    _set_batch_baseline(temp_batch_name, source_metadata.get("baseline"))

    try:
        for file_path, file_meta, result in retained_files:
            if result["type"] == "binary":
                add_binary_file_to_batch(
                    temp_batch_name,
                    result["binary_change"],
                    file_mode=file_meta.get("mode", "100644"),
                )
            else:
                add_sifted_text_file_to_batch(
                    batch_name=temp_batch_name,
                    file_path=file_path,
                    target_content=result["target_content"],
                    ownership=result["ownership"],
                    file_mode=file_meta.get("mode", "100644"),
                )

        temp_commit = run_git_command(["rev-parse", get_batch_content_ref_name(temp_batch_name)], check=False)
        if temp_commit.returncode == 0:
            commit_sha = temp_commit.stdout.strip()
            temp_metadata = read_batch_metadata(temp_batch_name)
            metadata_path = get_batch_metadata_file_path(batch_name)
            write_text_file_contents(metadata_path, json.dumps(temp_metadata, indent=2))
            sync_batch_state_refs(batch_name, content_commit=commit_sha)

        delete_batch_state_refs(temp_batch_name)

    except Exception:
        if batch_exists(temp_batch_name):
            delete_batch(temp_batch_name)
        raise



def _handle_empty_source_batch(source_batch: str, dest_batch: str) -> None:
    """Handle the case where the source batch is empty."""
    if source_batch == dest_batch:
        print(_("Batch '{name}' is already empty").format(name=source_batch), file=sys.stderr)
        return

    source_metadata = read_batch_metadata(source_batch)
    create_batch(dest_batch, note=f"Sifted from {source_batch} (was empty)")
    _set_batch_baseline(dest_batch, source_metadata.get("baseline"))

    print(
        _("✓ Sifted batch '{source}' to '{dest}': source was empty").format(
            source=source_batch,
            dest=dest_batch,
        ),
        file=sys.stderr,
    )



def _compute_sifted_binary_file(
    source_batch: str,
    file_path: str,
    file_meta: dict,
    repo_root: Path,
) -> Optional[dict]:
    """Compute a sifted binary file result."""
    batch_source_commit = file_meta["batch_source_commit"]
    change_type = file_meta["change_type"]

    batch_source_result = run_git_command(
        ["show", f"{batch_source_commit}:{file_path}"],
        check=False,
        text_output=False,
    )
    if batch_source_result.returncode != 0:
        batch_source_content = b""
    else:
        batch_source_content = batch_source_result.stdout

    full_path = repo_root / file_path
    if full_path.exists():
        working_content = full_path.read_bytes()
    else:
        working_content = b""

    if change_type == "deleted":
        if not full_path.exists():
            return None
    elif change_type in ("added", "modified"):
        if working_content == batch_source_content:
            return None

    old_path = file_path if change_type != "added" else "/dev/null"
    new_path = file_path if change_type != "deleted" else "/dev/null"

    return {
        "type": "binary",
        "binary_change": BinaryFileChange(
            old_path=old_path,
            new_path=new_path,
            change_type=change_type,
        ),
    }



def _compute_sifted_text_file(
    source_batch: str,
    file_path: str,
    file_meta: dict,
    repo_root: Path,
) -> Optional[dict]:
    """Compute a sifted text file result.

    Returns a destination ownership in target-content coordinate space plus the
    target content itself.
    """
    batch_source_commit = file_meta["batch_source_commit"]

    batch_source_result = run_git_command(
        ["show", f"{batch_source_commit}:{file_path}"],
        check=False,
        text_output=False,
    )
    if batch_source_result.returncode != 0:
        batch_source_content = b""
    else:
        batch_source_content = batch_source_result.stdout

    baseline_commit = get_batch_baseline_commit(source_batch)
    if baseline_commit:
        baseline_result = run_git_command(
            ["show", f"{baseline_commit}:{file_path}"],
            check=False,
            text_output=False,
        )
        if baseline_result.returncode == 0:
            baseline_content = baseline_result.stdout
        else:
            baseline_content = b""
    else:
        baseline_content = b""

    full_path = repo_root / file_path
    if full_path.exists():
        working_content = full_path.read_bytes()
    else:
        working_content = b""

    source_ownership = BatchOwnership.from_metadata_dict(file_meta)
    target_content = _build_realized_content(
        baseline_content,
        batch_source_content,
        source_ownership,
    )

    if working_content == target_content:
        return None

    new_ownership = build_ownership_from_working_to_target_delta(
        working_content=working_content,
        target_content=target_content,
    )
    if new_ownership is None or new_ownership.is_empty():
        return None

    _validate_sifted_text_file_result(
        target_content=target_content,
        dest_ownership=new_ownership,
        working_content=working_content,
    )

    return {
        "type": "text",
        "ownership": new_ownership,
        "target_content": target_content,
    }



def build_ownership_from_working_to_target_delta(
    working_content: bytes,
    target_content: bytes,
) -> Optional[BatchOwnership]:
    """Build ownership describing changes needed to turn working into target."""
    working_normalized = working_content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    target_normalized = target_content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")

    if working_normalized:
        working_lines = working_normalized.splitlines(keepends=True)
    else:
        working_lines = []

    if target_normalized:
        target_lines = target_normalized.splitlines(keepends=True)
    else:
        target_lines = []

    semantic_runs = derive_semantic_change_runs(
        source_lines=working_lines,
        target_lines=target_lines,
    )

    claimed_line_numbers = []
    deletion_claims = []

    for run in semantic_runs:
        if run.kind == SemanticChangeKind.PRESENCE:
            if run.target_run:
                claimed_line_numbers.extend(run.target_run)
        elif run.kind == SemanticChangeKind.DELETION:
            if run.source_run:
                deletion_content = [working_lines[i - 1] for i in run.source_run]
                deletion_claims.append(
                    DeletionClaim(
                        anchor_line=run.target_anchor,
                        content_lines=deletion_content,
                    )
                )
        elif run.kind == SemanticChangeKind.REPLACEMENT:
            if run.source_run:
                deletion_content = [working_lines[i - 1] for i in run.source_run]
                deletion_claims.append(
                    DeletionClaim(
                        anchor_line=run.target_anchor,
                        content_lines=deletion_content,
                    )
                )
            if run.target_run:
                claimed_line_numbers.extend(run.target_run)

    if not claimed_line_numbers and not deletion_claims:
        return None

    if claimed_line_numbers:
        claimed_lines = [format_line_ids(sorted(claimed_line_numbers))]
    else:
        claimed_lines = []

    return BatchOwnership(
        claimed_lines=claimed_lines,
        deletions=deletion_claims,
    )



def _validate_sifted_text_file_result(
    target_content: bytes,
    dest_ownership: BatchOwnership,
    working_content: bytes,
) -> None:
    """Validate that a sifted destination representation is semantically correct."""
    resolved = dest_ownership.resolve()
    target_normalized = target_content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if target_normalized:
        target_lines = target_normalized.splitlines(keepends=True)
    else:
        target_lines = []

    for claimed_line in resolved.claimed_line_set:
        if claimed_line < 1 or claimed_line > len(target_lines):
            raise MergeError(
                f"Sift validation failed: claimed line {claimed_line} is out of bounds "
                f"(target content has {len(target_lines)} lines)"
            )

    for deletion_claim in resolved.deletion_claims:
        if deletion_claim.anchor_line is not None:
            if deletion_claim.anchor_line < 1 or deletion_claim.anchor_line > len(target_lines):
                raise MergeError(
                    f"Sift validation failed: deletion anchor {deletion_claim.anchor_line} "
                    f"is out of bounds (target content has {len(target_lines)} lines)"
                )
        if not deletion_claim.content_lines:
            raise MergeError("Sift validation failed: deletion claim has empty content")

    try:
        reconstructed = merge_batch(
            batch_source_content=target_content,
            ownership=dest_ownership,
            working_content=working_content,
        )
    except MergeError as e:
        raise MergeError(
            f"Sift validation failed: destination representation cannot be merged: {e}"
        ) from e

    if reconstructed != target_content:
        raise MergeError(
            f"Sift validation failed: applying destination representation does not produce "
            f"the expected target content. Expected {len(target_content)} bytes, got "
            f"{len(reconstructed)} bytes."
        )
