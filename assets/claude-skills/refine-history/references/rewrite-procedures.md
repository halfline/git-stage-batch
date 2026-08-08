# Rewrite Procedures

Use these boundary-changing procedures while running `refine-history`.
Substitute the installed skill root for `.claude/skills/refine-history` when
using another assistant. Delegate all message-only rewording to
`refine-commit-messages`.

The interactive-rebase procedures are now a temporary SPLIT fallback only.
Use the object executor for KEEP, REWORD, and INTEGRATE plans. Before each
boundary change, require the current immutable scan to validate:

```bash
git-stage-batch rewrite validate "$REWRITE_PLAN" --porcelain
```

After each completed rebase, regenerate the scan from the canonical base
before making another boundary decision.

## Split a broad committed snapshot

For one split candidate, rebase from its parent:

```bash
SPLIT_SHA=PUT_BROAD_COMMIT_SHA_HERE
python3 "$REFINE_HISTORY_HELPER" mark --phase rewriting --note "split $SPLIT_SHA"
GIT_SEQUENCE_EDITOR="sed -i '1s/^pick /edit /'" git rebase -i "$SPLIT_SHA^"
git reset --mixed HEAD^
git --no-optional-locks status --short
git --no-optional-locks diff --stat
git-stage-batch start
```

Plan each replacement commit as a smaller runnable product state, with nearby
proof and final-tree content that remains absent. Do not let original hunk
boundaries define the new history.

Use line selection when the original hunks match the sublayer:

```bash
git-stage-batch show
git-stage-batch include --line SUBLAYER_LINE_IDS --no-auto-advance
git --no-optional-locks diff --cached
git --no-optional-locks diff --cached --check
git commit
python3 .claude/skills/refine-history/scripts/verify-head-snapshot.py --ref HEAD -- python3 -m compileall -q src tests
git-stage-batch stop
```

When a smaller state is clearer as rewritten file content, edit the working
file to that state and stage its exact snapshot:

```bash
git-stage-batch show --file PATH --no-advance
git-stage-batch include --file PATH --as-stdin --no-auto-advance < PATH
git --no-optional-locks diff --cached -- PATH
git --no-optional-locks diff --cached --check
git commit
python3 .claude/skills/refine-history/scripts/verify-head-snapshot.py --ref HEAD -- python3 -m compileall -q src tests
git-stage-batch stop
```

For Python snapshots, compilation is only a floor; run a narrow import or
behavior test. Inspect the residual unstaged diff after every commit instead
of treating it as one final residue. Prefer minimal groundwork, first
consumer, narrow proof, later consumers/proof, then docs. After
`git-stage-batch stop`, run `git-stage-batch start` again before reviewing or
staging the next residual sublayer.

When the broad commit is fully replaced:

```bash
git --no-optional-locks status --short
git rebase --continue
```

Use `git add` only for conflict-resolution bookkeeping. Restart the complete
audit after the rebase.

## Integrate a late repair or process commit

Inspect the repair and locate where each hunk first belongs:

```bash
REPAIR_SHA=PUT_REPAIR_SHA_HERE
git --no-optional-locks show --stat --patch --find-renames "$REPAIR_SHA"
git --no-optional-locks log --reverse --format='%H %s' "$BASE_SHA"..HEAD -- PATH_TOUCHED_BY_REPAIR
```

If any hunk cannot be allocated confidently, stop. If the repair has several
targets, first use the broad-snapshot split fallback to replace it with one
temporary repair commit per target. Confirm the final tree, regenerate
`rewrite-plan.json`, then handle each one-target repair separately.

For a one-target repair, edit only `plan.outputs` in the scan document:

1. Change the target output operation to `INTEGRATE`.
2. List the target followed by its repair commit or commits in chronological
   `source_commits` order.
3. Concatenate every listed source commit's complete `unit_ids` in that order.
4. Remove the separate repair output while leaving intervening outputs in
   source order after the integrated target.
5. Preserve the target author and keep its message/encoding unless its stated
   outcome genuinely changes.

Then require product proof and execution:

```bash
git-stage-batch rewrite validate "$REWRITE_PLAN" --porcelain
if test -n "${REVIEW_HEAD_REF:-}"; then
  git-stage-batch rewrite apply "$REWRITE_PLAN" \
    --allow-published-ref "$REVIEW_HEAD_REF"
else
  git-stage-batch rewrite apply "$REWRITE_PLAN"
fi
git-stage-batch rewrite status --porcelain
git-stage-batch rewrite verify --porcelain
```

Do not fall back to patch application when validation reports a conflict,
unequal final tree, unsupported header, stale snapshot, or other mechanical
failure. Revise the assignment or retain the repair. If apply is interrupted,
use `rewrite continue`; use `rewrite abort` only for an operation-owned tip.
Restart the complete audit after successful application.

## Repair a failing committed snapshot

Never add a later repair commit. Amend the newest rewritten commit immediately,
or find and edit the first older failing snapshot:

```bash
for c in $(git --no-optional-locks rev-list --reverse "$BASE_SHA"..HEAD); do
  python3 .claude/skills/refine-history/scripts/verify-head-snapshot.py --ref "$c" -- python3 -m compileall -q src tests || { echo "FIRST_BAD=$c"; break; }
done
BAD_SHA=PUT_FIRST_BAD_SHA_HERE
python3 "$REFINE_HISTORY_HELPER" mark --phase rewriting --note "repair snapshot $BAD_SHA"
GIT_SEQUENCE_EDITOR="sed -i '1s/^pick /edit /'" git rebase -i "$BAD_SHA^"
git-stage-batch start
git-stage-batch show
git-stage-batch include --line FIX_LINE_IDS --no-auto-advance
git --no-optional-locks diff --cached
git commit --amend --no-edit
python3 .claude/skills/refine-history/scripts/verify-head-snapshot.py --ref HEAD -- python3 -m compileall -q src tests
git-stage-batch stop
git rebase --continue
```

Rerun the snapshot loop and full audit after every repair.
