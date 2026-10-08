---
name: decompose-and-commit-unstaged-changes
description: Decompose unstaged working-tree changes into a clean commit series by preparing narrow concerns, peeling them with git-stage-batch, and rebuilding atomic commits. Use for unstaged work, not an existing committed series.
metadata:
  short-description: Decompose unstaged changes into commits
---

# Decompose and Commit Unstaged Changes

Prepare a development sequence, peel narrow concerns into named batches, and
rebuild them as atomic commits. A concern can require several atomic slices;
a feature can span several concerns. There is no target commit count.
Preserve the intended final source exactly.

Read `references/decompose-planning.md` for boundaries, chronology, local
corrections, and evidence reuse. Read only the brief for the current phase:

- Phase 1: `references/decompose-analyzer.md`
- Phase 2: `references/decompose-deconstructor.md`; the single-concern selection
  brief is `references/decompose-batch-peeler.md`
- Phase 3: `references/decompose-rebuilder.md`

Use a fresh phase context or independent review when it adds useful isolation.
Give workers the relevant brief, scoped source, ownership, exclusions, and
accumulated findings. The deconstructor can process successive concerns itself;
do not require a new worker or a full source reread for every concern. Keep
mutations sequential and gather independent review findings before correcting.

## Modes and tools

```text
$decompose-and-commit-unstaged-changes
$decompose-and-commit-unstaged-changes deconstruct
$decompose-and-commit-unstaged-changes reconstruct
$decompose-and-commit-unstaged-changes resume
```

Default runs all phases; `deconstruct` ends after Gate 2; `reconstruct` starts
with existing batches; `resume` audits recovery state. Continue autonomously
within the user's scope without intermediate approval requests.

git-stage-batch is the execution workflow in every repository, including
disposable replay repositories. Use it to save, peel, restore, reverse repairs,
and stage changes. Native `git commit` follows explicit tool staging. Native
`git add` is limited to marking resolved conflicts in an authorized rewrite;
never use it, an alternate index, Git plumbing, hand-built batch metadata, or a
prototype history to assemble ordinary slices. Read installed top-level and
relevant subcommand help before use; do not invent syntax or emulate batch
application when the tool reports a limitation. Read-only Git inspection,
cloning, copying existing batch refs into a replay clone, and detached worktrees
for checks are allowed. Pass `--no-optional-locks` to read-only Git commands.

Phase 1 writes preparation artifacts only. It must not edit source, stage,
commit, peel, or construct historical implementations in any repository.

## State and input

From the repository root, resolve the checkpoint directory:

```bash
export DECOMPOSE_STATE_DIR=$(python .agents/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py state-dir)
mkdir -p "$DECOMPOSE_STATE_DIR"
python .agents/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py status --json
git-stage-batch status
git-stage-batch list
git --no-optional-locks status --short
git --no-optional-locks diff --cached
```

Block the actual state directory from review when it is inside the repository:
`git-stage-batch block-file --local-only .git-stage-batch/` for the default
path; use its repository-relative path for an override. Keep preparation,
review findings, receipts, and replay repositories there. Preserve sibling
recovery directories and uniquely named evidence from older runs.

For a fresh full/deconstruct run, staged changes, an active session, or existing
`decompose-*` batches conflict with the requested scope. Resolve/report that
state rather than absorbing it. Existing batches are expected for resume or
reconstruct. Capture the current base, start the checkpoint, and capture input:

```bash
BASE_SHA=$(git --no-optional-locks rev-parse HEAD)
python .agents/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py start --mode full --base "$BASE_SHA"
python .agents/skills/decompose-and-commit-unstaged-changes/scripts/decompose-plan.py capture --base "$BASE_SHA"
```

Use `--mode deconstruct` when appropriate. Capture prints an immutable,
state-relative manifest path. It records source hashes/modes/gitlinks, the
original diff, and backups of changed file contents without staging or writing
Git objects. Ignored downloads/build outputs are prerequisites. Dirty nested
submodules have their own scope. Reuse that manifest throughout the run.
A local correction does not call fresh `start` or overwrite captured input.

## Phase 1 and Gate 1: preparation

Use the analyzer brief. Produce the schema 2 candidate, concise evolution
ladder/narrative, ownership ledger, planned checks, and review findings.
Historical implementations and runtime evidence are produced during execution.

Run the cheap structural check and confirm Phase 1 preserved input:

```bash
python .agents/skills/decompose-and-commit-unstaged-changes/scripts/decompose-plan.py validate "$DECOMPOSE_STATE_DIR/decompose-plan.candidate.json"
python .agents/skills/decompose-and-commit-unstaged-changes/scripts/decompose-plan.py verify-input INPUT_MANIFEST
```

Replace `INPUT_MANIFEST` with the captured state-relative path. Independently
review the actual input and proposed boundaries for one reason per atomic slice,
complete owned regions, supported dependencies, code/proof adjacency, and a
plausible development sequence. Check chronology now, before detailed execution.
This gate establishes a reviewed plan, not tested imaginary intermediate code.

Collect precise findings together. Correct affected concerns and consumers;
retain accepted findings for unchanged boundaries. Rerun global structural
validation after edits, with semantic review of affected concerns. Complete the
semantic review at the phase boundary. Copy the accepted candidate to
`decompose-plan.json`, then mark `phase1-complete`.

## Phase 2 and Gate 2: deconstruction and preservation

Use the deconstructor/peeler briefs. Peel in ascending concern order through
git-stage-batch. Audit owned content, context, and recorded minimal repairs
after each concern. Additional splits update the affected plan locally.

At Gate 2 require the remaining source to equal the recorded base, an empty
index, every planned batch, complete owned regions, and no unrelated content.
Perform one cumulative replay from that base in a disposable clone using the
actual batches, recorded repair order, and git-stage-batch staging. Record
batch/state ref IDs and intermediate tree IDs. The replay's final commit must
pass `decompose-plan.py --repo REPLAY_REPO verify-target INPUT_MANIFEST` using
the original state directory. This proves preservation and application; run
additional behavioral checks only for a concrete remaining replay risk.

A resume can reuse a completed replay only when its base, input digest, exact
batch/state ref IDs, ordered boundaries, repairs, and retained replay tree
still match. Otherwise replay the affected suffix from a verified boundary.
Always check the complete mapping and final target. Stop the live session
before marking `phase2-complete`. Deconstruct mode ends here.

## Phase 3 and Gate 3: atomic history

Use the rebuilder brief. Restore each batch in reverse order, inspect its
actual diff, and execute its atomic mini-series. Stage intentional historical
versions with git-stage-batch `include --as-stdin`. Honor immediately following
separate test commits where required. Verify each actual committed snapshot
with relevant grouped checks and content-bound receipts; amend a failed newest
commit before continuing.

After reconstruction, invoke the companion `.agents/skills/refine-history`
skill with the original base. Hand over the mini-series, accepted findings, and
verification receipts. That skill owns any final rewrite and message cleanup;
do not run another embedded rewrite or duplicate unchanged behavioral checks.
Reuse receipts only within its verification contract; its full object/plan
verification and final semantic audit still apply.

Gate 3 requires clean source/index, no workflow batches or active/completed
session, all required snapshot checks, the normal final repository checks,
valid gitlinks, successful `verify-target` against the original manifest, and
refine-history completion. Mark `phase3-complete` only after this gate passes.
Report concerns/commits, subjects in order, verification, and material repairs.

## Resume and recovery

Use the original checkpoint base and manifest. Audit current source, HEAD,
batch refs, recorded commits, and active operations before resuming. Schema 1
prototype plans require a fresh preparation audit and schema 2 mapping; preserve
their artifacts and any live batches. Never replay a prototype in place of
tool-created batches.

Continue Phase 1 from the last valid findings; after Gate 1, validate the plan
and remaining peel state; after Gate 2, authenticate its replay record before
reconstruction. When batches are exhausted, finish/refine the committed range.
A completed checkpoint still needs current Gate 3 validation.

Keep batches until their contents are committed, checked, and recoverable.
Use installed `undo`/`abort` semantics for selection mistakes. Preserve future
dirty work with named batches before correcting an older commit through the
companion history workflow. Do not conceal a broken snapshot with a later
repair commit or run destructive recovery over unrelated user work.
