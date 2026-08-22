"""Read-only rewrite scan command."""

from __future__ import annotations

from pathlib import Path

from ..history.scan import acquire_history_plan_document
from ..output.rewrite_scan import print_rewrite_scan
from ..utils.git_repository import require_git_repository
from ..utils.session_start_point import require_repository_history


def command_rewrite_scan(
    boundary: str | None = None,
    *,
    onto_boundary: str | None = None,
    output_path: str | None = None,
    porcelain: bool = False,
) -> None:
    """Capture exact range facts and emit an editable KEEP plan template.

    ``boundary`` is the movable base: commits after it may be split,
    reordered, consumed, or donate units. ``onto_boundary`` is the older
    frozen base that the scan captures and that movable units may integrate
    into; when omitted the frozen base equals the movable base and the whole
    range is movable.
    """
    require_git_repository()
    require_repository_history()
    document = acquire_history_plan_document(boundary, onto_boundary=onto_boundary)
    print_rewrite_scan(
        document,
        output_path=Path(output_path) if output_path is not None else None,
        porcelain=porcelain,
    )
