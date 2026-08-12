"""Human and porcelain rendering for rewrite scans."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ..git_paths import terminal_safe_text
from ..history.json_files import write_history_json_file
from ..history.models import HistoryPlanDocument
from ..history.records import history_plan_document_record
from ..i18n import _, ngettext


def print_rewrite_scan(
    document: HistoryPlanDocument,
    *,
    output_path: Path | None,
    porcelain: bool,
) -> None:
    """Render a scan and optionally persist its reusable JSON document."""
    record = history_plan_document_record(document)
    if output_path is not None:
        write_history_json_file(output_path, record)
    if porcelain:
        json.dump(record, fp=sys.stdout, indent=2, ensure_ascii=True)
        print()
        return

    snapshot = document.snapshot
    unit_count = sum(len(commit.units) for commit in snapshot.commits)
    print(
        _("History snapshot {base}..{tip}").format(
            base=snapshot.base_commit[:12],
            tip=snapshot.tip_commit[:12],
        )
    )
    print(
        ngettext(
            "{count} commit.",
            "{count} commits.",
            len(snapshot.commits),
        ).format(count=len(snapshot.commits))
    )
    print(
        ngettext(
            "{count} exact patch unit.",
            "{count} exact patch units.",
            unit_count,
        ).format(count=unit_count)
    )
    if snapshot.branch_ref is None:
        print(_("Branch: detached HEAD"))
    else:
        print(
            _("Branch: {branch}").format(
                branch=terminal_safe_text(snapshot.branch_ref)
            )
        )
    if document.safety.mutation_ready:
        print(_("Mutation preconditions: ready"))
    else:
        print(
            _("Mutation preconditions: blocked ({blockers})").format(
                blockers=", ".join(document.safety.blockers)
            )
        )
    signature_count = sum(
        len(commit.signatures) for commit in snapshot.commits
    )
    if signature_count:
        print(
            ngettext(
                "Warning: {count} source signature cannot remain valid after rewriting.",
                "Warning: {count} source signatures cannot remain valid after rewriting.",
                signature_count,
            ).format(count=signature_count)
        )
    if output_path is not None:
        print(
            _("Wrote reusable rewrite plan to {path}.").format(
                path=terminal_safe_text(str(output_path))
            )
        )
