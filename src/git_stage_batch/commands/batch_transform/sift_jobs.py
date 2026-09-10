"""Run text sift work through temporary files."""

from __future__ import annotations

from base64 import b64decode, b64encode
from binascii import Error as Base64Error
from dataclasses import dataclass
import json
from pathlib import Path
import pickle
from typing import Literal, TypedDict, cast

from . import sift_results as _sift_results
from ...batch.ownership.absence_claims import AbsenceClaim
from ...batch.ownership.model import BatchOwnership
from ...batch.ownership.references import BaselineReference
from ...batch.ownership.replacement_units import (
    ReplacementUnit,
    ReplacementUnitOrigin,
    normalize_replacement_units,
)
from ...batch.state.metadata_types import BatchFileMetadataDict
from ...core.buffer import LineBuffer
from ...core.line_selection import LineRanges
from ...data.file_target_identity import WorktreeIdentity
from ...exceptions import MergeError
from ...git_paths import display_path
from ...utils.buffer_io import write_buffer_to_path
from ...utils.file_job_workspace import FileJobWorkspace


SiftTextJobOutcome = Literal["retained", "removed", "merge_error"]
_MANIFEST_VERSION = 2
_MAX_ERROR_MESSAGE_CHARACTERS = 4 * 1024


class SiftTextJobInput(TypedDict):
    """Inputs saved for one sift worker."""

    baseline_object_id: str | None
    batch_source_object_id: str | None
    file_meta: BatchFileMetadataDict
    working_tree_artifact_path: str
    applied_predecessor_artifact_path: str | None


class _SiftDeletionRecord(TypedDict):
    anchor_line: int | None
    content_path: str
    baseline_reference: BaselineReference | None


@dataclass(frozen=True, slots=True)
class SiftTextFileJob:
    """Files and expected state for one sift worker."""

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
    """Result returned by one sift worker."""

    ordinal: int
    file_path: str
    outcome: SiftTextJobOutcome
    manifest_path: str | None = None
    target_path: str | None = None
    error_message: str | None = None


def compute_sifted_text_file_job(
    job: SiftTextFileJob,
) -> SiftTextFileJobResult:
    """Run one text sift and write its result to temporary files."""
    input_value = _read_pickle(job.input_artifact_path)
    if type(input_value) is not dict:
        raise TypeError("sift text input must be a dictionary")
    input_metadata = cast(SiftTextJobInput, input_value)

    try:
        result = _sift_results.compute_sifted_text_file(
            job.file_path,
            input_metadata["file_meta"],
            baseline_object_id=input_metadata["baseline_object_id"],
            batch_source_object_id=input_metadata["batch_source_object_id"],
            working_tree_artifact_path=input_metadata[
                "working_tree_artifact_path"
            ],
            applied_predecessor_artifact_path=input_metadata.get(
                "applied_predecessor_artifact_path"
            ),
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
    """Check one worker result before opening its files."""
    if not isinstance(result, SiftTextFileJobResult):
        raise TypeError("sift text worker returned an invalid result")
    if result.ordinal != job.ordinal or result.file_path != job.file_path:
        raise ValueError(
            "sift text worker returned a mismatched result for "
            f"{display_path(job.file_path)}"
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
    """Load one retained result from its temporary files."""
    validate_sifted_text_file_job_result(job, result)
    if result.outcome != "retained":
        raise ValueError("only retained sift text results can be loaded")

    manifest = workspace.read_json(job.manifest_output_path)
    (
        presence_lines,
        presence_references,
        deletion_records,
        replacement_units,
        change_type,
    ) = _validate_manifest(
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
        deletions: list[AbsenceClaim] = []
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
                    baseline_reference=deletion_record[
                        "baseline_reference"
                    ],
                )
            )
        ownership = BatchOwnership.from_presence_lines(
            presence_lines,
            deletions,
            replacement_units=replacement_units,
            baseline_references=presence_references,
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
                "baseline_reference": (
                    _baseline_reference_record(deletion.baseline_reference)
                    if deletion.baseline_reference is not None
                    else None
                ),
            }
        )
    replacement_units = normalize_replacement_units(
        result.ownership.replacement_units,
        deletion_count=len(result.ownership.deletions),
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
        "presence_references": [
            {
                "line": line,
                "reference": _baseline_reference_record(reference),
            }
            for line, reference in sorted(
                result.ownership.presence_baseline_references().items()
            )
        ],
        "deletions": deletion_records,
        "replacement_units": [
            _replacement_unit_record(unit) for unit in replacement_units
        ],
    }
    with Path(job.manifest_output_path).open("x", encoding="utf-8") as output:
        json.dump(manifest, output, ensure_ascii=True, separators=(",", ":"))
        output.write("\n")


def _require_supported_ownership(ownership: BatchOwnership) -> None:
    if any(
        deletion.source_alternative or deletion.complete_file_pair
        for deletion in ownership.deletions
    ):
        raise ValueError("sift text artifacts do not support source alternatives")
    if any(
        not isinstance(unit, ReplacementUnit)
        for unit in ownership.replacement_units
    ):
        raise TypeError("sift text artifacts require replacement-unit records")


def _validate_manifest(
    value: object,
    job: SiftTextFileJob,
) -> tuple[
    list[str],
    dict[int, BaselineReference],
    list[_SiftDeletionRecord],
    list[ReplacementUnit],
    str,
]:
    if type(value) is not dict:
        raise TypeError("sift text manifest must be a dictionary")
    if set(value) != {
        "version",
        "ordinal",
        "file_path",
        "output_order",
        "change_type",
        "presence_lines",
        "presence_references",
        "deletions",
        "replacement_units",
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
    presence_line_set = LineRanges.from_specs(presence_lines)
    presence_references = _validate_presence_references(
        value.get("presence_references"),
        presence_line_set,
    )
    deletion_values = value.get("deletions")
    if not isinstance(deletion_values, list):
        raise TypeError("sift text manifest has invalid deletions")
    deletion_records: list[_SiftDeletionRecord] = []
    deletion_directory = Path(job.deletion_output_directory)
    for index, deletion_value in enumerate(deletion_values):
        if type(deletion_value) is not dict:
            raise TypeError("sift text manifest has an invalid deletion")
        if set(deletion_value) != {
            "anchor_line",
            "content_path",
            "output_order",
            "baseline_reference",
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
        baseline_value = deletion_value.get("baseline_reference")
        deletion_records.append(
            {
                "anchor_line": anchor_line,
                "content_path": str(expected_path),
                "baseline_reference": (
                    _baseline_reference_from_record(
                        baseline_value,
                        label="deletion reference",
                    )
                    if baseline_value is not None
                    else None
                ),
            }
        )
    replacement_units = _validate_replacement_units(
        value.get("replacement_units"),
        presence_line_set=presence_line_set,
        deletion_count=len(deletion_records),
    )
    return (
        cast(list[str], presence_lines),
        presence_references,
        deletion_records,
        replacement_units,
        cast(str, change_type),
    )


def _baseline_reference_record(reference: BaselineReference) -> dict[str, object]:
    return {
        "after_known": reference.has_after_line,
        "after_line": reference.after_line,
        "after_content": _encoded_bytes(reference.after_content),
        "before_known": reference.has_before_line,
        "before_line": reference.before_line,
        "before_content": _encoded_bytes(reference.before_content),
    }


def _encoded_bytes(content: bytes | None) -> str | None:
    if content is None:
        return None
    return b64encode(content).decode("ascii")


def _baseline_reference_from_record(
    value: object,
    *,
    label: str,
) -> BaselineReference:
    if type(value) is not dict or set(value) != {
        "after_known",
        "after_line",
        "after_content",
        "before_known",
        "before_line",
        "before_content",
    }:
        raise ValueError(f"sift text manifest has an invalid {label}")

    after_known, after_line, after_content = _validated_boundary_record(
        value,
        side="after",
        label=label,
    )
    before_known, before_line, before_content = _validated_boundary_record(
        value,
        side="before",
        label=label,
    )
    return BaselineReference(
        after_line=after_line,
        after_content=after_content,
        has_after_line=after_known,
        before_line=before_line,
        before_content=before_content,
        has_before_line=before_known,
    )


def _validated_boundary_record(
    value: dict[object, object],
    *,
    side: str,
    label: str,
) -> tuple[bool, int | None, bytes | None]:
    known = value[f"{side}_known"]
    line = value[f"{side}_line"]
    encoded_content = value[f"{side}_content"]
    if type(known) is not bool:
        raise TypeError(f"sift text manifest has an invalid {label}")
    if line is not None and (type(line) is not int or line < 1):
        raise ValueError(f"sift text manifest has an invalid {label}")
    if encoded_content is not None and not isinstance(encoded_content, str):
        raise TypeError(f"sift text manifest has an invalid {label}")
    if not known and (line is not None or encoded_content is not None):
        raise ValueError(f"sift text manifest has an invalid {label}")
    if line is None and encoded_content is not None:
        raise ValueError(f"sift text manifest has an invalid {label}")
    try:
        content = (
            b64decode(encoded_content, validate=True)
            if encoded_content is not None
            else None
        )
    except (Base64Error, ValueError) as error:
        raise ValueError(
            f"sift text manifest has an invalid {label}"
        ) from error
    return known, line, content


def _validate_presence_references(
    value: object,
    presence_line_set: LineRanges,
) -> dict[int, BaselineReference]:
    if not isinstance(value, list):
        raise TypeError("sift text manifest has invalid presence references")
    references: dict[int, BaselineReference] = {}
    for record in value:
        if type(record) is not dict or set(record) != {"line", "reference"}:
            raise ValueError(
                "sift text manifest has an invalid presence reference"
            )
        line = record.get("line")
        if type(line) is not int or line < 1 or line not in presence_line_set:
            raise ValueError(
                "sift text manifest has an invalid presence reference line"
            )
        if line in references:
            raise ValueError(
                "sift text manifest has a duplicate presence reference"
            )
        references[line] = _baseline_reference_from_record(
            record.get("reference"),
            label="presence reference",
        )
    return references


def _replacement_unit_record(unit: ReplacementUnit) -> dict[str, object]:
    origin = unit.origin
    return {
        "presence_lines": list(unit.presence_lines),
        "deletion_indices": unit.deletion_indices,
        "origin": (
            {
                "old_start": origin.old_start,
                "old_end": origin.old_end,
                "new_start": origin.new_start,
                "new_end": origin.new_end,
                "baseline_reference": (
                    _baseline_reference_record(origin.baseline_reference)
                    if origin.baseline_reference is not None
                    else None
                ),
            }
            if origin is not None
            else None
        ),
    }


def _validate_replacement_units(
    value: object,
    *,
    presence_line_set: LineRanges,
    deletion_count: int,
) -> list[ReplacementUnit]:
    if not isinstance(value, list):
        raise TypeError("sift text manifest has invalid replacement units")
    units: list[ReplacementUnit] = []
    for record in value:
        if type(record) is not dict or set(record) != {
            "presence_lines",
            "deletion_indices",
            "origin",
        }:
            raise ValueError(
                "sift text manifest has an invalid replacement unit"
            )
        unit_presence = record.get("presence_lines")
        if not isinstance(unit_presence, list) or any(
            not isinstance(line_range, str) for line_range in unit_presence
        ):
            raise TypeError(
                "sift text manifest has invalid replacement presence ranges"
            )
        unit_presence_set = LineRanges.from_specs(unit_presence)
        if not unit_presence_set or any(
            line not in presence_line_set for line in unit_presence_set
        ):
            raise ValueError(
                "sift text manifest replacement presence is not owned"
            )
        deletion_indices = record.get("deletion_indices")
        if not isinstance(deletion_indices, list) or any(
            type(index) is not int or not 0 <= index < deletion_count
            for index in deletion_indices
        ):
            raise ValueError(
                "sift text manifest has invalid replacement deletions"
            )
        if deletion_indices != sorted(set(deletion_indices)):
            raise ValueError(
                "sift text manifest has duplicate replacement deletions"
            )
        origin = _replacement_origin_from_record(record.get("origin"))
        units.append(
            ReplacementUnit(
                presence_lines=cast(list[str], unit_presence),
                deletion_indices=cast(list[int], deletion_indices),
                origin=origin,
            )
        )
    return units


def _replacement_origin_from_record(
    value: object,
) -> ReplacementUnitOrigin | None:
    if value is None:
        return None
    if type(value) is not dict or set(value) != {
        "old_start",
        "old_end",
        "new_start",
        "new_end",
        "baseline_reference",
    }:
        raise ValueError("sift text manifest has an invalid replacement origin")
    coordinates = [
        value.get("old_start"),
        value.get("old_end"),
        value.get("new_start"),
        value.get("new_end"),
    ]
    if any(type(coordinate) is not int for coordinate in coordinates):
        raise TypeError("sift text manifest has an invalid replacement origin")
    reference_value = value.get("baseline_reference")
    return ReplacementUnitOrigin(
        old_start=cast(int, coordinates[0]),
        old_end=cast(int, coordinates[1]),
        new_start=cast(int, coordinates[2]),
        new_end=cast(int, coordinates[3]),
        baseline_reference=(
            _baseline_reference_from_record(
                reference_value,
                label="replacement origin reference",
            )
            if reference_value is not None
            else None
        ),
    )


def _read_pickle(path: str | Path) -> object:
    with Path(path).open("rb") as source:
        value: object = pickle.load(source)
        return value
