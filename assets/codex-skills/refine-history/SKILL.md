---
name: refine-history
description: Rewrite an existing local commit series into a clean incremental history while preserving its final tree. Use when the user wants to polish, split, reword, or integrate fixup and repair commits after an explicit base, or resume an interrupted refinement. Do not use for unstaged work or shared/protected history.
---

# Refine History

Rewrite the committed series in `BASE_SHA..HEAD` so each commit represents one
coherent product state. Preserve the final `HEAD` tree exactly.

Read `references/rewrite-procedures.md` before changing history. It contains
the required split, repair-integration, and snapshot-repair procedures. The
first-class `refine-commit-messages` skill owns the final message audit and
rewording pass.

## Usage

```text
$refine-history BASE_SHA
$refine-history resume
```

For a fresh run, require one explicit base argument. Do not infer it from
branch names, merge bases, reflogs, old checkpoints, or prior audit files.
Bind that argument to `BASE_SHA`. The literal `resume` argument is the only
case that may read the skill checkpoint to recover the canonical base.

This skill is autonomous after invocation, but fail closed if the range is
shared/protected or that cannot be established safely.

If `git-stage-batch` is not in `PATH`, use `pipx run git-stage-batch`. Read the
installed help for each batch command before using it; installed help wins if
it disagrees with this skill.

Before rewriting, read the repository's contribution guide and representative
recent commits. Follow project message conventions for replacement commits
created while changing boundaries. The final message-only pass will reconcile
every message and series transition.

## Establish the rewrite boundary

For a fresh run, move to the repository root, locate the installed helper, and
freeze the explicit base to a full commit ID. The helper rejects an empty or
non-linear range and any range commit contained in a remote-tracking ref:

```bash
REPO_ROOT=$(git --no-optional-locks rev-parse --show-toplevel)
cd "$REPO_ROOT"
REFINE_HISTORY_HELPER=.agents/skills/refine-history/scripts/refine-history-checkpoint.py
export REFINE_HISTORY_STATE_DIR=$(python3 "$REFINE_HISTORY_HELPER" state-dir)
if test -z "${BASE_SHA:-}"; then
  echo "refine-history requires an explicit BASE_SHA"
  exit 1
fi
BASE_SHA=$(python3 "$REFINE_HISTORY_HELPER" check-range --base "$BASE_SHA")
```

Confirm separately that the branch is a local draft. The helper can inspect
local remote-tracking refs, but it cannot prove server state or branch
protection. Stop when a configured remote is not known to be current, branch
policy is unknown, the branch is protected, or any publication evidence is
ambiguous:

```bash
git --no-optional-locks branch --show-current
git --no-optional-locks branch -a --contains HEAD
git --no-optional-locks remote -v
git --no-optional-locks log --reverse --format='%H %s' "$BASE_SHA"..HEAD
```

Require a clean index and worktree, no rebase/cherry-pick/merge in progress,
and no active or saved `git-stage-batch` work:

```bash
git-stage-batch --help
git-stage-batch start --help
git-stage-batch show --help
git-stage-batch include --help
git-stage-batch stop --help
git-stage-batch status --help
git-stage-batch list --help
git-stage-batch block-file --local-only .git-stage-batch/
git --no-optional-locks status --short
git-stage-batch list
git-stage-batch status
test ! -e "$(git --no-optional-locks rev-parse --git-path rebase-merge)"
test ! -e "$(git --no-optional-locks rev-parse --git-path rebase-apply)"
test ! -e "$(git --no-optional-locks rev-parse --git-path MERGE_HEAD)"
test ! -e "$(git --no-optional-locks rev-parse --git-path CHERRY_PICK_HEAD)"
```

If status reports ordinary pending work, a batch exists, a session is active,
or an operation marker exists, stop and report the blocker.

Start fresh state only after those checks. This clears only the prior
`refine-history` state, records the original series and tree, and creates a
recovery ref:

```bash
python3 "$REFINE_HISTORY_HELPER" start --base "$BASE_SHA"
python3 "$REFINE_HISTORY_HELPER" status --json
```

Do not read old refine-history artifacts before `start`.

## Resume an interrupted run

Run this section only for the literal `resume` invocation. Do not call
`start`, do not clear state, and do not accept a second base argument:

```bash
REPO_ROOT=$(git --no-optional-locks rev-parse --show-toplevel)
cd "$REPO_ROOT"
REFINE_HISTORY_HELPER=.agents/skills/refine-history/scripts/refine-history-checkpoint.py
export REFINE_HISTORY_STATE_DIR=$(python3 "$REFINE_HISTORY_HELPER" state-dir)
python3 "$REFINE_HISTORY_HELPER" status --json
BASE_SHA=$(python3 "$REFINE_HISTORY_HELPER" check-resume)
RECOVERY_REF=$(python3 "$REFINE_HISTORY_HELPER" recovery-ref)
git --no-optional-locks show-ref --verify "$RECOVERY_REF"
```

`check-resume` requires the checkpoint, recovery ref, original range,
`pre-tree.txt`, `pre-count.txt`, and `pre-series.txt` to agree and rechecks
local remote-tracking containment. It does not refresh remotes or determine
branch policy. If a rebase is active, inspect `git --no-optional-locks status`,
the rebase todo/done files, the last checkpoint event, and the relevant rewrite
procedure. Continue only when they identify the same interrupted refinement
step. It is also safe to use `git rebase --abort` to return to that step's
pre-rebase state; never abort and then call `start`.

If no Git operation is active, require a clean tree and no batch/session before
starting another rewrite. When an active batch or dirty tree cannot be tied
unambiguously to the recorded active rebase stop, fail closed and report the
recovery ref. Regenerate `pressure.json` and `audit.json` from the current
range, then continue at the audit or rewrite pass implied by the checkpoint.
If the recorded phase is `complete`, rerun the completion gate against the
current repository instead of starting another rewrite.

## Build the audit

Generate a mechanical pressure document. It contains every current commit in
series order; non-empty `reasons` make that commit a presumed split candidate:

```bash
python3 "$REFINE_HISTORY_HELPER" pressure --base "$BASE_SHA"
```

Inspect every commit in order, including its subject, body, diffstat, and
patch. During a working pass, classify it as `KEEP`, `SPLIT`, `MESSAGE`, or
`INTEGRATE` with a concrete reason. Apply every boundary-changing decision
first and leave `MESSAGE` decisions for `refine-commit-messages`. For the final
pass, write `$REFINE_HISTORY_STATE_DIR/audit.json` with this exact shape:

```json
{
  "schema": 1,
  "base": "FULL_BASE_SHA",
  "head": "FULL_CURRENT_HEAD_SHA",
  "commits": [
    {
      "sha": "FULL_COMMIT_SHA",
      "subject": "Exact current subject",
      "verdict": "KEEP",
      "reason": "Concrete single-outcome boundary",
      "pressure": ["Exact reason from pressure.json"],
      "smallest_runnable_spine": "Smallest state that still works",
      "later_enrichments_checked": ["Specific later slice considered"],
      "split_probes": [
        {
          "candidate": "Specific slice moved to a later commit",
          "blocking_reason": "Exact path-specific immediate breakage"
        }
      ],
      "repair_process_false_positive": "Required only when that pressure reason is a product-domain false positive"
    }
  ]
}
```

The `commits` array must contain every current commit exactly once in series
order. `sha` and `subject` must match Git exactly. Copy each pressured commit's
`reasons` array verbatim into `pressure`. Unpressured commits may omit the five
pressure-analysis fields.

Regardless of mechanical pressure, treat a commit as a split or reorder
candidate when it:

- lists several outcomes in its subject/body or hides patch content behind a
  narrower message;
- drops a finished module, command, coordinator, docs section, fixture tree,
  test surface, or build hook that could have started smaller;
- combines groundwork with its first adopter, or one adopter with later
  adopters;
- delays proof into a later test-only run or tests several separable behaviors;
- documents behavior before that behavior exists; or
- introduces a final file shape all at once instead of evolving it.

For a pressured `KEEP`, record:

- the smallest runnable spine;
- every later enrichment, adopter, variant, error path, proof, fixture, docs
  section, and build hook considered;
- at least one concrete split probe; and
- the exact path-specific immediate breakage or narrative regression caused by
  every probe.

If any probe has no immediate breakage, split the commit. A module, function,
pipeline, command, test file, or fixture tree is not a concern boundary by
itself. Generic rationales such as "same module", "single pipeline", "tests
belong together", "shared helper", "coherent unit", "large but related", or
"no meaningful subdivision" are invalid.

Reconcile every commit message with its patch. Each meaningful helper, result
field, fixture family, API surface, data model, CLI branch, docs section, and
build hook must be the named outcome, required support for that outcome, or a
separate later commit.

The pressure scanner also flags repair/process-shaped messages and
multi-outcome subjects. Integrate a genuine repair/process commit where its
hunks first belonged. Use `repair_process_false_positive` only for a concrete
product-domain false positive. Always split or reword a multi-outcome subject.

## Rewrite to convergence

Run these passes:

1. Split broad snapshots into smaller runnable product states.
2. Integrate repair, fixup, cleanup, and process commits into the earliest
   commits where their hunks belong, then drop the late commits.
3. Run the first-class `refine-commit-messages` skill in its default mutating
   mode over the same canonical base.

Use the exact boundary procedures in `references/rewrite-procedures.md`. After
any rewrite, regenerate `pressure.json` and restart the audit from the
beginning because SHAs and dependencies changed. Verify every changed
committed snapshot before continuing. Never leave a broken intermediate
commit for a later repair.

After the split and integration passes converge, require
`.agents/skills/refine-commit-messages/SKILL.md`, then mark and invoke it:

```bash
python3 "$REFINE_HISTORY_HELPER" mark --phase refine-commit-messages-running --note "handing converged boundaries to refine-commit-messages"
```

Use `$refine-commit-messages BASE_SHA` for a fresh message pass. If resuming
and the last refine-history event records that handoff, use
`$refine-commit-messages resume` instead. Do not invoke its `audit` mode here.
When it completes, regenerate pressure and the complete refine-history audit.
Do not reword commits directly inside this skill.

Continue until one complete pass makes no changes and every audit entry has a
valid final `KEEP` verdict.

## Completion gate

Validate the structured audit against the current range. This recomputes the
pressure signals and rejects missing, stale, reordered, non-`KEEP`, weakly
justified, multi-outcome, or insufficiently probed entries:

```bash
python3 "$REFINE_HISTORY_HELPER" validate-audit --base "$BASE_SHA"
```

Then require all of the following:

```bash
git --no-optional-locks status --short
git-stage-batch list
git-stage-batch status
python3 .agents/skills/refine-commit-messages/scripts/refine-commit-messages-checkpoint.py status --json
```

- The worktree and index are clean.
- No batch or session remains.
- The repository's normal tests pass.
- No subject names multiple outcomes or merely names an artifact.
- No late repair/process commit remains.
- A full final audit found no further coherent split.
- The default `refine-commit-messages` completion gate passed for the same
  base, branch, and current `HEAD`.

Run an actual verification command against every commit, including commits
that were never rewritten. Choose a repository-appropriate build/import and
narrow behavior check; do not run the Python example literally in a
non-Python repository:

```bash
python3 "$REFINE_HISTORY_HELPER" verify-range --base "$BASE_SHA" -- python3 -m compileall -q src tests
```

Only after the audit, normal tests, status checks, and range-wide verification
pass, record completion:

```bash
git --no-optional-locks branch -a --contains HEAD
git --no-optional-locks remote -v
python3 "$REFINE_HISTORY_HELPER" complete --base "$BASE_SHA"
```

Immediately before completion, reconfirm that remote-tracking information is
current, branch protection still permits rewriting, and publication evidence
is unambiguous. `complete` rechecks the canonical base, checkpoint branch,
local remote-tracking containment, clean tree/index, structured audit, matching
successful `verify-range` record, and exact original final tree. Remote
freshness and branch policy remain the caller's responsibility. The helper
writes `post-count.txt` and `post-series.txt` and only then marks the checkpoint
complete.

Report the original and final commit counts, final subjects in order, splits,
integrated/dropped commits, rewords, pressured commits kept with exact breakage
reasons, validation commands, and the recovery ref.
