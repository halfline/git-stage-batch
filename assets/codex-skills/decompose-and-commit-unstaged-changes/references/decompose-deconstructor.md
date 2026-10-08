# Decompose Deconstructor Reference

Peel approved concerns outermost-to-innermost through git-stage-batch.
Read the planning reference and peeler brief. Preserve original source in
batches; do not author a prototype history or stage the maintained index.

## Preparation and peel loop

Read the approved schema 2 plan, manifest, ledger, accepted findings, and
checkpoint. Verify the input before the first peel; on resume account for the
recorded batches and remaining tree instead. Confirm scope and installed help.
Mark `phase2-running`. Start a tool session only when none is active, and
continue only one owned by this workflow.

For each concern in ascending `peel_order`:

1. Mark `current-batch` with the checkpoint helper.
2. Execute the peeler brief, or give a worker the scoped concern/ownership,
   exclusions, relevant source, findings, and session state. Keep mutations
   sequential; a new worker for each concern is optional.
3. Audit the batch and optional repair, original ownership, shared context,
   and remaining-source coherence. Preserve local evidence with exact ref IDs.
4. Mark `completed-batch` only after that audit passes. Use `again` as needed
   and refresh `show` after mutations.

When source reveals a narrower boundary, pause that selection, record concrete
findings, and correct the affected concerns/ledger/mini-series. Run global
structural validation, review affected boundaries, and retain other accepted
findings. Update already peeled affected batches through tool operations.
Do not return to constructing a global history or weaken a boundary to avoid
intricate selection work.

## Gate 2: one cumulative preservation replay

The remaining maintained tree must equal the actual committed base, with an
empty index. Every planned concern and recorded repair has its tool-created
batch, deliberate note, complete ownership, and provenance. Account for all
input, including new/deleted files, modes, symlinks, and gitlinks.

Create a uniquely named disposable clone at the base and copy the required
existing batch/state refs there. Record their immutable IDs. For each concern
in reverse order:

1. Use its recorded successful repair order. If none is known, inspect overlap
   and choose either apply concern then reverse repair, or reverse repair then
   apply concern. Use installed `apply --from` and `discard --from` semantics.
2. Inspect the restored diff and check it against the concern ledger.
   Stage the replay increment explicitly with git-stage-batch and make a
   temporary native commit to establish the next base. Those commits prove
   batch preservation; final atomic mini-series are built in Phase 3.
3. Record the actual before/after trees and operations. If application fails,
   retain the failure and test the alternate order from a clean copy of the
   preceding replay commit only when the failure warrants it. Once an order
   succeeds, retain it; do not routinely run both.
4. Run additional focused checks only for a concrete replay/repair risk.
   Reuse valid local evidence. Final behavioral proof belongs to actual
   atomic commits during reconstruction.

After the cumulative replay:

```bash
python .agents/skills/decompose-and-commit-unstaged-changes/scripts/decompose-plan.py --repo REPLAY_REPO verify-target INPUT_MANIFEST
```

Use the original `DECOMPOSE_STATE_DIR` and real paths. This checks the complete
committed tree against the immutable input manifest. Do not manufacture batch
metadata, copy prototype versions over replay results, use native staging,
or substitute hand-written patch/claim application for the tool.

Write `decompose-replay.json` with the base, input digest, exact ordered
batch/state IDs, repair operations, intermediate tree IDs, retained replay
repository/tip, and local verification receipts. Confirm its final target and
complete ownership mapping. A reused replay must match all those inputs;
changed refs invalidate their boundaries and the affected replay suffix.
An unchanged authenticated replay does not need rebuilding merely on resume.

Stop the live tool session and confirm no active/completed session before
returning the complete evidence to the coordinator for `phase2-complete`.
Report blockers without claiming the gate passed.
