# Decompose Rebuilder Reference

Restore approved batches in `rebuild_order` and create their atomic commits.
Read
`.agents/skills/decompose-and-commit-unstaged-changes/references/decompose-planning.md`
first. Applying a batch writes to the working tree only; it never counts as
staging.

## Preparation

Read the approved plan, snapshot patches, Gate 2 replay evidence, input
manifest, checkpoint, and batch list. Confirm the maintained tree is the
recorded base, the index is clean, and all remaining batches belong to this
workflow. On resume, compare completed commits with their planned snapshots
and continue from the verified boundary.

Read repository commit/test guidance, its commit-msg hook if present, and the
installed git-stage-batch help for all commands used. Read the
`.agents/skills/commit-unstaged-changes/SKILL.md` staging guidance when
available. Do not use native `git add` for ordinary slices in the maintained
repository.

Compute `DECOMPOSE_STATE_DIR` with the checkpoint helper if needed. Mark
`phase3-running` before applying batches. Resolve the installed snapshot
verifier at `.agents/skills/refine-history/scripts/verify-head-snapshot.py`;
use the companion helper so verification follows the same implementation as
the required final refinement.

## Rebuild loop

For each remaining concern, highest number first:

```bash
python .agents/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py mark --phase phase3-running --current-batch decompose-NN-NAME
```

Use the order recorded by Gate 2's exact batch replay. With no repair, apply
the concern. With a repair, the two documented operations are:

```bash
git-stage-batch apply --from decompose-NN-NAME
git-stage-batch discard --from decompose-NN-NAME-repair
```

Run them in the proven order, which may be the reverse of the display above.
For a newly added file whose earlier version is present, reverse its repair
first if applying the later version would otherwise be incompatible. Do not
choose an order from file type alone; compare the resulting tree with the
approved after snapshot before staging.

Inspect `git diff` and `git diff --cached`. The index should remain empty
until explicit staging. Compare the restored content with the planned
before/after patch, including imports, registrations, and absent future
content. Do not copy a final file version that introduces later concerns.

The default is the one atomic commit already approved for that concern, with
proof placement governed by repository conventions. Required separate `tests:
Validate ...` concerns immediately follow the code concerns they validate. Do
not invent another mandatory mini-series for every batch. If restoration
exposes independent outcomes absent from the plan, use a targeted boundary
correction and complete revalidation before committing the affected content.
Do not silently broaden or subdivide history.

Start/continue a verified workflow session, inspect the review, then stage
only the approved change using git-stage-batch. For example:

```bash
git-stage-batch status
git-stage-batch start
git-stage-batch show --file PATH
git-stage-batch include --line IDS --no-auto-advance
git --no-optional-locks diff --cached
```

Run `start` only if no session is active. Refresh `show` after each mutation;
IDs belong to the immediately preceding review. Use whole-file inclusion only
when every diff region belongs to the approved change. Exact complete
historical replacements can use `--as-stdin`.

Verify the staged tree matches the planned after tree before committing.
Without a separate-proof convention, tests and documentation establishing that
change normally land in the same commit. With that convention, keep the
approved code/proof adjacency and `validates` link. Do not group all
implementation commits first and defer their proof commits to the end.

## Messages and proof

Follow repository message conventions and relevant path history. The subject
names one concrete action. Explain the previous committed state, the reason
for the patch, and the actual result. Internal APIs are valid outcomes; do not
claim later callers already use them. Do not fabricate a failure, benchmark,
or motivation. Feature-level summaries belong to later review requests.

Create the commit using native `git commit` after git-stage-batch staging.
Inspect the resulting committed diff, then run an explicit appropriate command
against the committed snapshot:

```bash
python .agents/skills/refine-history/scripts/verify-head-snapshot.py --ref HEAD -- ACTUAL_CHECK_COMMAND
```

Replace the command placeholder with discovered executable arguments. Honor
the approved prerequisites; generate ignored assets using the committed
scripts when needed. Verify source imports come from that checkout. A dirty
tree containing future changes must never supply passing proof for HEAD.
Syntax/import checks supplement relevant behavior/build checks. For a
low-impact docs change, perform the planned inspection. Record proof limits
and external blockers honestly.

After each successful commit:

```bash
python .agents/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py mark --phase phase3-running --commit HEAD
```

Drop a concern and its repair batch only when their content is fully
committed, verified, and recoverable in history. Record the completed batch:

```bash
git-stage-batch drop decompose-NN-NAME
python .agents/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py mark --phase phase3-running --completed-batch decompose-NN-NAME
```

Drop the companion repair as well if present. Inspect any remaining unstaged
content; it must be explained by the current verified workflow state.

## Recovery

A hook failure before commit creation is a retry of the same commit. If a
newly committed snapshot fails, fix and amend it before continuing; do not
leave a later repair commit. Stage the fix with git-stage-batch, inspect the
index, amend, and rerun the exact failed proof. Update evidence for affected
successors if the fix changes their planned snapshots.

For an older failure, do not rebase over future dirty work. Preserve it using
the documented workflow recovery mechanism, require a clean tree, and edit the
first failed commit in the local series. Native `git add` is permitted only
for conflict-resolution bookkeeping during that rebase. Reverify the repaired
commit and changed successors. Do not run destructive recovery against user
work or published history without authorization.

## Handoff

After all batches are committed and dropped, stop the workflow session and
return to the orchestrator. Compare the final committed tree with the captured
target, report commits/checks/repairs and blockers, and let the orchestrator
invoke the required `refine-history` skill. Do not run a separate embedded
history rewrite here or declare the workflow complete before Gate 3.
