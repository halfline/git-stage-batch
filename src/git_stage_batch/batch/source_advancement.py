"""Batch source advancement with refreshed line provenance."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..core.buffer import LineBuffer
from ..data.batch_sources import create_batch_source_commit
from ..utils.repository_buffers import (
    load_git_object_as_buffer,
    load_working_tree_file_as_buffer,
)
from ..utils.git_repository import get_git_repository_root_path
from .lineage import BatchSourceLineage, LineageRun
from .presence_constraints import apply_presence_constraints
from .realized_entries import realized_entry_content_chunks
from .ownership import BatchOwnership
from .ownership_remapping import remap_batch_ownership_with_lineage


@dataclass
class SourceContentWithLineProvenance:
    """Synthesized source buffer with line provenance from its inputs."""

    source_buffer: LineBuffer
    lineage: BatchSourceLineage

    def close(self) -> None:
        """Release the synthesized buffer and line lineage."""
        self.source_buffer.close()
        self.lineage.close()

    def __enter__(self) -> SourceContentWithLineProvenance:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


@dataclass
class BatchSourceAdvanceResult:
    """Result of advancing one file's batch source."""

    batch_source_commit: str
    ownership: BatchOwnership
    source_buffer: LineBuffer
    lineage: BatchSourceLineage

    def close(self) -> None:
        """Release the refreshed source buffer and line lineage."""
        self.source_buffer.close()
        self.lineage.close()

    def __enter__(self) -> BatchSourceAdvanceResult:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def advance_source_lines_preserving_existing_presence(
    old_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    ownership: BatchOwnership,
) -> SourceContentWithLineProvenance:
    """Build source content with provenance from line sequences."""
    presence_lines = ownership.presence_line_set()

    entries = apply_presence_constraints(
        old_lines,
        working_lines,
        presence_lines,
    )

    lineage = BatchSourceLineage()
    try:
        for run in entries.provenance_runs():
            line_count = run.dest_end - run.dest_start
            new_start = run.dest_start + 1
            if run.source_start != 0:
                lineage.append_source_run(
                    LineageRun(
                        old_start=run.source_start,
                        old_end=run.source_start + line_count - 1,
                        new_start=new_start,
                    )
                )
            if run.target_start != 0:
                lineage.append_working_run(
                    LineageRun(
                        old_start=run.target_start,
                        old_end=run.target_start + line_count - 1,
                        new_start=new_start,
                    )
                )

        return SourceContentWithLineProvenance(
            source_buffer=LineBuffer.from_chunks(
                realized_entry_content_chunks(entries)
            ),
            lineage=lineage,
        )
    except Exception:
        lineage.close()
        raise
    finally:
        entries.close()


def advance_batch_source_for_file_with_provenance(
    batch_name: str,
    file_path: str,
    old_batch_source_commit: str,
    existing_ownership: BatchOwnership,
) -> BatchSourceAdvanceResult:
    """Advance batch source and expose provenance for re-annotation."""
    repo_root = get_git_repository_root_path()
    working_file_path = repo_root / file_path
    if not working_file_path.exists():
        raise ValueError(
            f"Cannot advance batch source for {file_path}: "
            f"file does not exist in working tree"
        )

    old_source_buffer = load_git_object_as_buffer(
        f"{old_batch_source_commit}:{file_path}"
    )
    if old_source_buffer is None:
        raise ValueError(
            f"Cannot read old batch source for {file_path} at {old_batch_source_commit}"
        )

    source_with_provenance: SourceContentWithLineProvenance | None = None
    try:
        with (
            old_source_buffer as old_source_lines,
            load_working_tree_file_as_buffer(file_path) as working_lines,
        ):
            source_with_provenance = advance_source_lines_preserving_existing_presence(
                old_lines=old_source_lines,
                working_lines=working_lines,
                ownership=existing_ownership,
            )

        new_batch_source_commit = create_batch_source_commit(
            file_path,
            file_buffer_override=source_with_provenance.source_buffer,
        )

        remapped_ownership = remap_batch_ownership_with_lineage(
            ownership=existing_ownership,
            lineage=source_with_provenance.lineage,
        )

        return BatchSourceAdvanceResult(
            batch_source_commit=new_batch_source_commit,
            ownership=remapped_ownership,
            source_buffer=source_with_provenance.source_buffer,
            lineage=source_with_provenance.lineage,
        )
    except Exception:
        if source_with_provenance is not None:
            source_with_provenance.close()
        raise
