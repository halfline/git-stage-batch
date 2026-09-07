"""Checks for composing saved paragraph prefixes with applied ownership."""

from git_stage_batch.batch.applied_text_replay import AppliedTextApplication, _AcquiredTextApplication
from git_stage_batch.batch.ownership.model import BatchOwnership



def _application(source, presence):
    record = AppliedTextApplication(
        batch_name="ownership",
        file_path="contract.md",
        baseline_commit=None,
        source_object_id="unused",
        file_metadata={},
    )
    return _AcquiredTextApplication(
        record,
        source,
        BatchOwnership.from_presence_lines(presence),
        (),
    )
