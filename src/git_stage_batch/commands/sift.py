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

from dataclasses import dataclass
from pathlib import Path
import sys

from ..exceptions import MergeError
from ..batch.state.validation import read_validated_batch_metadata
from ..batch.state.reference_names import (
    format_batch_content_ref_name,
    format_batch_state_ref_name,
    format_legacy_batch_ref_name,
)
from ..batch.state.lifecycle import create_batch
from ..batch.state.batch_names import batch_exists, validate_batch_name
from ..batch.source.selector import require_plain_batch_name
from .batch_transform import sift_persistence as _sift_persistence
from .batch_transform import sift_jobs as _sift_jobs
from .batch_transform import sift_results as _sift_results
from ..data.file_target_identity import (
    WorktreeIdentity,
    capture_worktree_identities,
    capture_worktree_identity,
)
from ..exceptions import BatchMetadataError, exit_with_error
from ..i18n import _
from ..utils.git_repository import (
    get_git_repository_root_path,
    require_git_repository,
)
from ..utils.file_job_workspace import FileJobWorkspace
from ..utils.file_jobs import (
    OrderedFileJob,
    run_file_jobs,
    run_validated_file_jobs,
)
from ..utils.git_object_io import list_git_tree_blobs, resolve_git_objects


@dataclass(frozen=True, slots=True)
class _SourceBatchSnapshot:
    metadata: dict
    ref_identities: tuple[tuple[str, str | None], ...]


@dataclass(frozen=True, slots=True)
class _TextSiftInput:
    ordinal: int
    file_path: str
    file_meta: dict
    worktree_identity: WorktreeIdentity
    worktree_artifact_path: Path
    scratch_directory: Path


@dataclass(frozen=True, slots=True)
class _SiftInputCapture:
    text_inputs: tuple[_TextSiftInput, ...]
    worktree_identities: dict[str, WorktreeIdentity]
    content_artifacts: dict[str, Path]


@dataclass(frozen=True, slots=True)
class _SiftTextExecution:
    jobs_by_ordinal: dict[int, _sift_jobs.SiftTextFileJob]
    results_by_ordinal: dict[int, _sift_jobs.SiftTextFileJobResult]


def command_sift_batch(source_batch: str, dest_batch: str) -> None:
    """Sift a batch to remove portions already present at tip."""
    require_git_repository()
    source_batch = require_plain_batch_name(source_batch, "sift")
    dest_batch = require_plain_batch_name(dest_batch, "sift")
    validate_batch_name(source_batch)
    validate_batch_name(dest_batch)

    if not batch_exists(source_batch):
        exit_with_error(_("Batch '{name}' does not exist").format(name=source_batch))

    try:
        source_snapshot = _read_source_batch_snapshot(source_batch)
    except BatchMetadataError as error:
        exit_with_error(str(error))
    source_metadata = source_snapshot.metadata

    source_files = source_metadata.get("files", {})
    if not source_files:
        _require_unchanged_sift_inputs(
            source_batch=source_batch,
            source_snapshot=source_snapshot,
            expected_worktree_identities={},
        )
        _handle_empty_source_batch(
            source_batch,
            dest_batch,
            source_metadata=source_metadata,
        )
        return

    in_place = source_batch == dest_batch
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

    repo_root = get_git_repository_root_path()
    with FileJobWorkspace() as workspace:
        retained_files: list[_sift_persistence.RetainedSiftedFile] = []
        try:
            (
                retained_files,
                expected_worktree_identities,
            ) = _build_sifted_files(
                source_metadata=source_metadata,
                repository_root=repo_root,
                workspace=workspace,
            )
            _require_unchanged_sift_inputs(
                source_batch=source_batch,
                source_snapshot=source_snapshot,
                expected_worktree_identities=expected_worktree_identities,
            )
            if in_place:
                _sift_persistence.replace_batch_with_sifted_files(
                    batch_name=source_batch,
                    retained_files=retained_files,
                    source_metadata=source_metadata,
                )
            else:
                _sift_persistence.publish_sifted_files(
                    destination_batch=dest_batch,
                    retained_files=retained_files,
                    source_metadata=source_metadata,
                    destination_note=f"Sifted from {source_batch}",
                    replace_existing=False,
                )
        except MergeError as error:
            exit_with_error(
                _("Could not sift batch '{source}': {error}").format(
                    source=source_batch,
                    error=error,
                )
            )
        finally:
            _close_sifted_results(retained_files)

    _print_sift_summary(
        source_batch=source_batch,
        dest_batch=dest_batch,
        retained_count=len(retained_files),
        total_count=len(source_files),
    )


def _print_sift_summary(
    *,
    source_batch: str,
    dest_batch: str,
    retained_count: int,
    total_count: int,
) -> None:
    """Print the translated summary for one completed sift."""
    in_place = source_batch == dest_batch
    if retained_count == 0:
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
                    retained=retained_count,
                    total=total_count,
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
                    retained=retained_count,
                    total=total_count,
                ),
                file=sys.stderr,
            )


def _build_sifted_files(
    *,
    source_metadata: dict,
    repository_root: Path,
    workspace: FileJobWorkspace,
) -> tuple[
    list[_sift_persistence.RetainedSiftedFile],
    dict[str, WorktreeIdentity],
]:
    source_files = source_metadata.get("files", {})
    capture = _capture_sift_inputs(
        source_files=source_files,
        workspace=workspace,
    )
    jobs = _build_sift_text_jobs(
        source_metadata=source_metadata,
        capture=capture,
        workspace=workspace,
    )
    execution = _run_sift_text_jobs(
        jobs,
        repository_root=repository_root,
    )
    retained_files = _reduce_sift_results(
        source_files=source_files,
        capture=capture,
        execution=execution,
        repository_root=repository_root,
        workspace=workspace,
    )
    return retained_files, capture.worktree_identities


def _capture_sift_inputs(
    *,
    source_files: dict[str, dict],
    workspace: FileJobWorkspace,
) -> _SiftInputCapture:
    """Capture immutable worktree inputs for every sifted file."""
    text_inputs: list[_TextSiftInput] = []
    worktree_identities: dict[str, WorktreeIdentity] = {}
    content_artifacts: dict[str, Path] = {}
    for ordinal, (file_path, file_meta) in enumerate(source_files.items()):
        is_text = file_meta.get("file_type") not in {"binary", "mode"}
        captures_content = file_meta.get("file_type") != "mode"
        if captures_content:
            worktree_artifact = workspace.artifact_path(
                ordinal,
                "worktree-input",
            )
            identity = capture_worktree_identity(
                file_path,
                content_artifact_path=worktree_artifact,
            )
            content_artifacts[file_path] = worktree_artifact
        else:
            identity = capture_worktree_identity(file_path)
        if is_text:
            text_inputs.append(
                _TextSiftInput(
                    ordinal=ordinal,
                    file_path=file_path,
                    file_meta=file_meta,
                    worktree_identity=identity,
                    worktree_artifact_path=worktree_artifact,
                    scratch_directory=workspace.scratch_directory(ordinal),
                )
            )
        worktree_identities[file_path] = identity
    return _SiftInputCapture(
        text_inputs=tuple(text_inputs),
        worktree_identities=worktree_identities,
        content_artifacts=content_artifacts,
    )


def _build_sift_text_jobs(
    *,
    source_metadata: dict,
    capture: _SiftInputCapture,
    workspace: FileJobWorkspace,
) -> list[OrderedFileJob[_sift_jobs.SiftTextFileJob]]:
    """Build compact text jobs from captured sift inputs."""
    text_inputs = capture.text_inputs

    baseline_blob_by_path = _resolve_baseline_blobs(
        source_metadata.get("baseline"),
        text_inputs,
    )
    source_blob_by_input = _resolve_source_blobs(text_inputs)
    object_info_by_name = resolve_git_objects(
        [
            *baseline_blob_by_path.values(),
            *source_blob_by_input.values(),
        ]
    )
    jobs: list[OrderedFileJob[_sift_jobs.SiftTextFileJob]] = []
    for text_input in text_inputs:
        baseline_object_id = baseline_blob_by_path.get(text_input.file_path)
        source_object_id = source_blob_by_input.get(
            (text_input.ordinal, text_input.file_path)
        )
        input_artifact = workspace.write_pickle(
            text_input.ordinal,
            "sift-input.pickle",
            {
                "baseline_object_id": baseline_object_id,
                "batch_source_object_id": source_object_id,
                "file_meta": text_input.file_meta,
                "working_tree_artifact_path": str(
                    text_input.worktree_artifact_path
                ),
            },
        )
        target_output_path = workspace.output_path(
            text_input.ordinal,
            "target.bin",
        )
        manifest_output_path = workspace.output_path(
            text_input.ordinal,
            "manifest.json",
        )
        deletion_output_directory = (
            target_output_path.parent / "deletions"
        )
        payload = _sift_jobs.SiftTextFileJob(
            ordinal=text_input.ordinal,
            file_path=text_input.file_path,
            input_artifact_path=str(input_artifact),
            target_output_path=str(target_output_path),
            manifest_output_path=str(manifest_output_path),
            deletion_output_directory=str(deletion_output_directory),
            scratch_directory=str(text_input.scratch_directory),
            expected_worktree_identity=text_input.worktree_identity,
        )
        estimated_bytes = text_input.worktree_identity.size or 0
        for object_id in (baseline_object_id, source_object_id):
            if object_id is not None and object_id in object_info_by_name:
                estimated_bytes += object_info_by_name[object_id].size
        jobs.append(
            OrderedFileJob(
                ordinal=text_input.ordinal,
                file_path=text_input.file_path,
                estimated_bytes=estimated_bytes,
                payload=payload,
            )
        )
    return jobs


def _run_sift_text_jobs(
    jobs: list[OrderedFileJob[_sift_jobs.SiftTextFileJob]],
    *,
    repository_root: Path,
) -> _SiftTextExecution:
    """Execute and validate sift text jobs in stable ordinal order."""
    paired_results = run_validated_file_jobs(
        jobs,
        _sift_jobs.compute_sifted_text_file_job,
        _sift_jobs.validate_sifted_text_file_job_result,
        repository_root=repository_root,
        result_label="sift text",
        run_jobs=run_file_jobs,
    )
    job_by_ordinal = {}
    result_by_ordinal = {}
    for ordered_job, result in paired_results:
        job_by_ordinal[ordered_job.ordinal] = ordered_job.payload
        result_by_ordinal[result.ordinal] = result
    return _SiftTextExecution(job_by_ordinal, result_by_ordinal)


def _reduce_sift_results(
    *,
    source_files: dict[str, dict],
    capture: _SiftInputCapture,
    execution: _SiftTextExecution,
    repository_root: Path,
    workspace: FileJobWorkspace,
) -> list[_sift_persistence.RetainedSiftedFile]:
    """Reduce atomic and text sift results in source-file order."""
    retained_files: list[_sift_persistence.RetainedSiftedFile] = []
    try:
        for ordinal, (file_path, file_meta) in enumerate(source_files.items()):
            if file_meta.get("file_type") == "mode":
                sifted_result = _sift_results.compute_sifted_mode_file(
                    file_path,
                    file_meta,
                    repository_root,
                )
            elif file_meta.get("file_type") == "binary":
                sifted_result = _sift_results.compute_sifted_binary_file(
                    file_path,
                    file_meta,
                    repository_root,
                    working_tree_artifact_path=(
                        capture.content_artifacts[file_path]
                    ),
                    captured_working_tree_exists=(
                        capture.worktree_identities[file_path].exists
                    ),
                )
            else:
                job = execution.jobs_by_ordinal[ordinal]
                job_result = execution.results_by_ordinal[ordinal]
                if job_result.outcome == "merge_error":
                    raise MergeError(job_result.error_message or "sift failed")
                sifted_result = (
                    None
                    if job_result.outcome == "removed"
                    else _sift_jobs.load_sifted_text_file_result(
                        workspace,
                        job,
                        job_result,
                    )
                )
            if sifted_result is not None:
                retained_files.append((file_path, file_meta, sifted_result))
        return retained_files
    except BaseException:
        _close_sifted_results(retained_files)
        raise


def _resolve_baseline_blobs(
    baseline_commit: str | None,
    text_inputs: tuple[_TextSiftInput, ...],
) -> dict[str, str]:
    if baseline_commit is None:
        return {}
    return {
        file_path: entry.blob_sha
        for file_path, entry in list_git_tree_blobs(
            baseline_commit,
            (text_input.file_path for text_input in text_inputs),
        ).items()
    }


def _resolve_source_blobs(
    text_inputs: tuple[_TextSiftInput, ...],
) -> dict[tuple[int, str], str]:
    inputs_by_commit: dict[str, list[_TextSiftInput]] = {}
    for text_input in text_inputs:
        source_commit = text_input.file_meta["batch_source_commit"]
        inputs_by_commit.setdefault(source_commit, []).append(text_input)

    blob_by_input: dict[tuple[int, str], str] = {}
    for source_commit, commit_inputs in inputs_by_commit.items():
        entries = list_git_tree_blobs(
            source_commit,
            (text_input.file_path for text_input in commit_inputs),
        )
        for text_input in commit_inputs:
            entry = entries.get(text_input.file_path)
            if entry is not None:
                blob_by_input[(text_input.ordinal, text_input.file_path)] = (
                    entry.blob_sha
                )
    return blob_by_input


def _read_source_batch_snapshot(batch_name: str) -> _SourceBatchSnapshot:
    initial_ref_identities = _capture_source_batch_ref_identities(batch_name)
    metadata = read_validated_batch_metadata(batch_name)
    final_ref_identities = _capture_source_batch_ref_identities(batch_name)
    if final_ref_identities != initial_ref_identities:
        raise BatchMetadataError(
            f"Batch '{batch_name}' changed while its sift inputs were read. "
            "Retry the operation against the latest batch state."
        )
    return _SourceBatchSnapshot(
        metadata=metadata,
        ref_identities=final_ref_identities,
    )


def _capture_source_batch_ref_identities(
    batch_name: str,
) -> tuple[tuple[str, str | None], ...]:
    ref_names = (
        format_batch_content_ref_name(batch_name),
        format_batch_state_ref_name(batch_name),
        format_legacy_batch_ref_name(batch_name),
    )
    object_info_by_ref = resolve_git_objects(ref_names)
    return tuple(
        (
            ref_name,
            (
                None
                if ref_name not in object_info_by_ref
                else object_info_by_ref[ref_name].object_id
            ),
        )
        for ref_name in ref_names
    )


def _require_unchanged_sift_inputs(
    *,
    source_batch: str,
    source_snapshot: _SourceBatchSnapshot,
    expected_worktree_identities: dict[str, WorktreeIdentity],
) -> None:
    current_ref_identities = _capture_source_batch_ref_identities(source_batch)
    try:
        current_metadata = read_validated_batch_metadata(source_batch)
    except BatchMetadataError as error:
        exit_with_error(str(error))
    final_ref_identities = _capture_source_batch_ref_identities(source_batch)
    if (
        current_ref_identities != source_snapshot.ref_identities
        or final_ref_identities != current_ref_identities
        or current_metadata != source_snapshot.metadata
    ):
        exit_with_error(
            _(
                "Cannot sift batch '{source}' because it changed while sift "
                "was running. Retry against the latest batch state."
            ).format(source=source_batch)
        )

    current_worktree_identities = capture_worktree_identities(
        expected_worktree_identities
    )
    for file_path, expected_identity in expected_worktree_identities.items():
        if current_worktree_identities[file_path] != expected_identity:
            exit_with_error(
                _(
                    "Cannot sift batch '{source}' because working-tree file "
                    "'{file}' changed while sift was running. Retry against "
                    "the current working tree."
                ).format(source=source_batch, file=file_path)
            )

def _close_sifted_results(
    retained_files: list[_sift_persistence.RetainedSiftedFile],
) -> None:
    """Close all buffers held by retained sift results."""
    for _file_path, _file_meta, result in retained_files:
        result.close()


def _handle_empty_source_batch(
    source_batch: str,
    dest_batch: str,
    *,
    source_metadata: dict,
) -> None:
    """Handle the case where the source batch is empty."""
    if source_batch == dest_batch:
        print(_("Batch '{name}' is already empty").format(name=source_batch), file=sys.stderr)
        return

    create_batch(
        dest_batch,
        note=f"Sifted from {source_batch} (was empty)",
        baseline_commit=source_metadata.get("baseline"),
    )

    print(
        _("✓ Sifted batch '{source}' to '{dest}': source was empty").format(
            source=source_batch,
            dest=dest_batch,
        ),
        file=sys.stderr,
    )
