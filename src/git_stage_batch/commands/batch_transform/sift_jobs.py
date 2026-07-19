"""Artifact-backed text computation for sift commands."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import pickle
from typing import Literal

from . import sift_results as _sift_results
from ...batch.ownership.absence_claims import AbsenceClaim
from ...batch.ownership.model import BatchOwnership
from ...core.buffer import LineBuffer, write_buffer_to_path
from ...data.file_target_identity import WorktreeIdentity
from ...exceptions import MergeError
from ...utils.file_job_workspace import FileJobWorkspace


SiftTextJobOutcome = Literal["retained", "removed", "merge_error"]
_MANIFEST_VERSION = 1
_MAX_ERROR_MESSAGE_CHARACTERS = 4 * 1024


@dataclass(frozen=True, slots=True)
class SiftTextFileJob:
    """Compact worker request for one captured text sift computation."""

    ordinal: int
    file_path: str
    input_artifact_path: str
    target_output_path: str
    manifest_output_path: str
    deletion_output_directory: str
    scratch_directory: str
    expected_worktree_identity: WorktreeIdentity


@dataclass(frozen=True, slots=True)
class SiftTextFileJobResult:
    """Compact worker response for one text sift computation."""

    ordinal: int
    file_path: str
    outcome: SiftTextJobOutcome
    manifest_path: str | None = None
    target_path: str | None = None
    error_message: str | None = None


def compute_sifted_text_file_job(
    job: SiftTextFileJob,
) -> SiftTextFileJobResult:
    """Compute and stream one text sift result into private artifacts."""
    input_metadata = _read_pickle(job.input_artifact_path)
    if type(input_metadata) is not dict:
        raise TypeError("sift text input must be a dictionary")

    try:
        result = _sift_results.compute_sifted_text_file(
            job.file_path,
            input_metadata["file_meta"],
            baseline_object_id=input_metadata["baseline_object_id"],
            batch_source_object_id=input_metadata["batch_source_object_id"],
            working_tree_artifact_path=input_metadata[
                "working_tree_artifact_path"
            ],
            captured_working_tree_exists=(
                job.expected_worktree_identity.exists
            ),
            spool_dir=job.scratch_directory,
        )
    except MergeError as error:
        return SiftTextFileJobResult(
            ordinal=job.ordinal,
            file_path=job.file_path,
            outcome="merge_error",
            error_message=str(error)[:_MAX_ERROR_MESSAGE_CHARACTERS],
        )

    if result is None:
        return SiftTextFileJobResult(
            ordinal=job.ordinal,
            file_path=job.file_path,
            outcome="removed",
        )

    try:
        _write_sifted_text_result(job, result)
    finally:
        result.close()
    return SiftTextFileJobResult(
        ordinal=job.ordinal,
        file_path=job.file_path,
        outcome="retained",
        manifest_path=job.manifest_output_path,
        target_path=job.target_output_path,
    )


def validate_sifted_text_file_job_result(
    job: SiftTextFileJob,
    result: SiftTextFileJobResult,
) -> None:
    """Validate one worker response before opening its artifacts."""
    if not isinstance(result, SiftTextFileJobResult):
        raise TypeError("sift text worker returned an invalid result")
    if result.ordinal != job.ordinal or result.file_path != job.file_path:
        raise ValueError(
            f"sift text worker returned a mismatched result for {job.file_path}"
        )
    if result.outcome == "retained":
        if result.manifest_path != job.manifest_output_path:
            raise ValueError("retained sift text result omitted its manifest")
        if result.target_path != job.target_output_path:
            raise ValueError("retained sift text result omitted its target")
        if result.error_message is not None:
            raise ValueError("retained sift text result returned an error")
        return
    if result.outcome == "removed":
        if any(
            value is not None
            for value in (
                result.manifest_path,
                result.target_path,
                result.error_message,
            )
        ):
            raise ValueError("removed sift text result returned artifacts")
        return
    if result.outcome == "merge_error":
        if result.manifest_path is not None or result.target_path is not None:
            raise ValueError("failed sift text result returned content artifacts")
        if not isinstance(result.error_message, str) or not result.error_message:
            raise ValueError("failed sift text result omitted its error")
        return
    raise ValueError(f"unsupported sift text result outcome: {result.outcome}")


def load_sifted_text_file_result(
    workspace: FileJobWorkspace,
    job: SiftTextFileJob,
    result: SiftTextFileJobResult,
) -> _sift_results.SiftedTextFileResult:
    """Reconstruct one retained result from validated private artifacts."""
    validate_sifted_text_file_job_result(job, result)
    if result.outcome != "retained":
        raise ValueError("only retained sift text results can be loaded")

    manifest = workspace.read_json(job.manifest_output_path)
    presence_lines, deletion_records, change_type = _validate_manifest(
        manifest,
        job,
    )
    opened_buffers: list[LineBuffer] = []
    try:
        target_buffer = workspace.read_buffer(
            job.target_output_path,
            spool_dir=job.scratch_directory,
        )
        opened_buffers.append(target_buffer)
        deletions = []
        for deletion_record in deletion_records:
            content_buffer = workspace.read_buffer(
                deletion_record["content_path"],
                spool_dir=job.scratch_directory,
            )
            opened_buffers.append(content_buffer)
            if len(content_buffer) == 0:
                raise ValueError(
                    "sift text manifest deletion content must not be empty"
                )
            deletions.append(
                AbsenceClaim(
                    anchor_line=deletion_record["anchor_line"],
                    content_lines=content_buffer,
                )
            )
        ownership = BatchOwnership.from_presence_lines(
            presence_lines,
            deletions,
        )
        loaded = _sift_results.SiftedTextFileResult(
            ownership=ownership,
            target_buffer=target_buffer,
            change_type=change_type,
        )
        opened_buffers.clear()
        return loaded
    finally:
        for buffer in opened_buffers:
            buffer.close()


def _write_sifted_text_result(
    job: SiftTextFileJob,
    result: _sift_results.SiftedTextFileResult,
) -> None:
    _require_supported_ownership(result.ownership)
    write_buffer_to_path(job.target_output_path, result.target_buffer)
    deletion_directory = Path(job.deletion_output_directory)
    deletion_directory.mkdir(mode=0o700)
    deletion_records = []
    for index, deletion in enumerate(result.ownership.deletions):
        content_path = deletion_directory / f"{index:08d}-content.bin"
        write_buffer_to_path(content_path, deletion.content_lines)
        deletion_records.append(
            {
                "anchor_line": deletion.anchor_line,
                "content_path": str(content_path),
                "output_order": index,
            }
        )
    manifest = {
        "version": _MANIFEST_VERSION,
        "ordinal": job.ordinal,
        "file_path": job.file_path,
        "output_order": job.ordinal,
        "change_type": result.change_type,
        "presence_lines": (
            result.ownership.presence_line_set().to_range_strings()
        ),
        "deletions": deletion_records,
    }
    with Path(job.manifest_output_path).open("x", encoding="utf-8") as output:
        json.dump(manifest, output, ensure_ascii=True, separators=(",", ":"))
        output.write("\n")


def _require_supported_ownership(ownership: BatchOwnership) -> None:
    if ownership.replacement_units:
        raise ValueError("sift text artifacts do not support replacement units")
    if any(claim.baseline_references for claim in ownership.presence_claims):
        raise ValueError("sift text artifacts do not support presence references")
    if any(
        deletion.baseline_reference is not None
        for deletion in ownership.deletions
    ):
        raise ValueError("sift text artifacts do not support deletion references")


def _validate_manifest(
    value: object,
    job: SiftTextFileJob,
) -> tuple[list[str], list[dict[str, object]], str]:
    if type(value) is not dict:
        raise TypeError("sift text manifest must be a dictionary")
    if set(value) != {
        "version",
        "ordinal",
        "file_path",
        "output_order",
        "change_type",
        "presence_lines",
        "deletions",
    }:
        raise ValueError("sift text manifest has unsupported fields")
    if value.get("version") != _MANIFEST_VERSION:
        raise ValueError("sift text manifest has an unsupported version")
    if value.get("ordinal") != job.ordinal or value.get("output_order") != job.ordinal:
        raise ValueError("sift text manifest has a mismatched output order")
    if value.get("file_path") != job.file_path:
        raise ValueError("sift text manifest has a mismatched file path")
    change_type = value.get("change_type")
    if change_type not in {"added", "modified", "deleted"}:
        raise ValueError("sift text manifest has an invalid change type")
    presence_lines = value.get("presence_lines")
    if not isinstance(presence_lines, list) or any(
        not isinstance(line_range, str) for line_range in presence_lines
    ):
        raise TypeError("sift text manifest has invalid presence ranges")
    deletion_values = value.get("deletions")
    if not isinstance(deletion_values, list):
        raise TypeError("sift text manifest has invalid deletions")
    deletion_records: list[dict[str, object]] = []
    deletion_directory = Path(job.deletion_output_directory)
    for index, deletion_value in enumerate(deletion_values):
        if type(deletion_value) is not dict:
            raise TypeError("sift text manifest has an invalid deletion")
        if set(deletion_value) != {
            "anchor_line",
            "content_path",
            "output_order",
        }:
            raise ValueError("sift text manifest deletion has unsupported fields")
        anchor_line = deletion_value.get("anchor_line")
        if anchor_line is not None and (
            type(anchor_line) is not int or anchor_line < 1
        ):
            raise ValueError("sift text manifest has an invalid deletion anchor")
        expected_path = deletion_directory / f"{index:08d}-content.bin"
        if deletion_value.get("content_path") != str(expected_path):
            raise ValueError("sift text manifest has a mismatched deletion path")
        if deletion_value.get("output_order") != index:
            raise ValueError("sift text manifest has a mismatched deletion order")
        deletion_records.append(
            {
                "anchor_line": anchor_line,
                "content_path": str(expected_path),
            }
        )
    return presence_lines, deletion_records, change_type


def _read_pickle(path: str | Path):
    with Path(path).open("rb") as source:
        return pickle.load(source)
