---
name: decompose-rebuilder
description: "Phase 3 agent for decompose-and-commit-unstaged-changes. Applies batches in reverse order, splits each restored layer into atomic commits with proper narrative, and delegates message drafting to commit-message-drafter."
tools: Read, Grep, Glob, LS, Edit, Write, Agent(commit-message-drafter), Bash
---

# Decompose Rebuilder Reference

Restore narrow batches in reverse order and develop their atomic mini-series.
Read `.claude/skills/decompose-and-commit-unstaged-changes/references/decompose-planning.md`, approved plan, ownership, accepted findings,
Gate 2 replay record, captured input, and checkpoint. Applying a batch changes
the working tree only; staging is a separate explicit git-stage-batch action.

## Restore and plan from the real diff

Verify the current maintained base or completed reconstruction boundary and
empty index. Authenticate remaining batch/state IDs and repair orders against
Gate 2. Read repository commit/test conventions and relevant installed help.
Mark `phase3-running`.

For each highest-numbered remaining concern, mark `current-batch`, then restore
it through `apply --from` and reverse any repair with `discard --from` in its
proven order. Inspect the resulting unstaged diff, relevant imports/calls, and
ownership. Never copy a prototype or final version over the restored source.

Refine `expected_commits` into a short atomic mini-series from that actual
diff before staging. Each slice has one purpose and coherent prerequisites.
Expose providers before consumers, keep adoption and proof near the useful
flow, and exclude future imports/registrations/behavior. Code/tests/docs need
separate commits when repository conventions require them; test proof follows
its code immediately.

If the actual diff reveals independent outcomes, update the affected boundary
and mini-series, validate global structure, and review those changes. Preserve
unaffected decisions. Do not require a global Phase 1 restart or one commit
per batch. Broad unrelated batches still require correction before committing.

## Stage, commit, and verify

Use `show` and explicit `include` operations to stage only the current slice.
Refresh review IDs after every mutation. Whole-file inclusion requires the
entire current diff to belong to that slice. For shared functions/files,
stage an intentional complete earlier version through `include --line
--as-stdin` or `include --file --as-stdin`. Leave future source in the working
tree, recorded in the current batch, until its slice is ready.

Inspect `git diff --cached` and `git show :PATH`. The staged version must be
one coherent increment using only its present prerequisites. Generate valid
dependency/lock snapshots with the matching package manager; stage them through
the tool. Use native `git commit` after explicit staging.

For each actual commit, write the relevant check specification and execute all
its checks in one isolated source snapshot:

```bash
python .claude/skills/decompose-and-commit-unstaged-changes/scripts/verify-head-snapshot.py --ref HEAD --checks CHECK_SPEC_JSON --evidence-dir "$DECOMPOSE_STATE_DIR/verification" --reuse
```

Use real command arguments, runtime identities, external harness input paths,
and prerequisites as described in `.claude/skills/decompose-and-commit-unstaged-changes/references/decompose-planning.md`. For docs/mechanical
changes, record inspection rather than create unnecessary tests. A later test
file or dirty future source cannot prove the current code snapshot.

The helper records exact tree/command/input/environment/executable/log bindings.
Setup and checks may create ignored generated assets, but cannot modify tracked
source or introduce untracked source. Group setup and commands for the snapshot;
share caches only with pinned identities. Focus browser proof on browser
contracts and limit simultaneous browser runs under memory pressure.

A matching failed receipt does not trigger another unchanged attempt. Diagnose
the code, runner, or transient prerequisite and retain a new attempt with
`--retry` when appropriate. Fix and amend a failed newest commit through
git-stage-batch before proceeding; record and reverify the corrected snapshot.

After each verified commit, checkpoint `--commit HEAD`. A number/message/parent
change alone does not invalidate a successful result for an identical tree and
execution inputs. The helper can reuse that evidence; source changes need new
checks.

## Completion and recovery

Drop the concern/repair only after its full atomic mini-series is committed,
verified, and recoverable. Mark `completed-batch` and audit any remaining dirty
source against the current workflow. Stop the session after all batches.

For an older broken commit, preserve future dirty work with named batches and
use the companion refine-history workflow to correct the relevant committed
range. Do not implement a separate rebase engine, leave a later repair commit,
or mutate unrelated user work. Hook failures before commit creation retry that
same commit.

Compare the final committed tree with the original input using
`decompose-plan.py verify-target INPUT_MANIFEST`. Return subjects, mini-series
mapping, accepted review findings, verification specifications/receipts,
repairs, and blockers. The coordinator then invokes the required companion
refine-history skill. Hand over reusable evidence so its changed-boundary checks
and final audit do not repeat unchanged behavioral verification.
