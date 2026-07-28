# Rewrite Procedures

Use these boundary-changing procedures while running `refine-history`.
Delegate all message-only rewording to `refine-commit-messages`.

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
targets, first use the broad-snapshot split procedure to replace it with one
temporary repair commit per historical target. Confirm that `HEAD^{tree}`
still matches `pre-tree.txt`, restart the audit, and integrate each temporary
repair separately. Do not carry one unsplit patch through several `edit` stops;
residual hunks at the first stop make later rebase steps unsafe.

For one target:

```bash
TARGET_SHA=PUT_COMMIT_THAT_SHOULD_HAVE_CONTAINED_THE_HUNK
TARGET_SHORT=$(git rev-parse --short=7 "$TARGET_SHA")
REPAIR_SHORT=$(git rev-parse --short=7 "$REPAIR_SHA")
git --no-optional-locks show --format= --binary "$REPAIR_SHA" > "$REFINE_HISTORY_STATE_DIR/repair-$REPAIR_SHORT.patch"
python3 "$REFINE_HISTORY_HELPER" mark --phase rewriting --note "integrate $REPAIR_SHA into $TARGET_SHA"
GIT_SEQUENCE_EDITOR="sed -i -E -e 's/^pick (${TARGET_SHORT}[0-9a-f]*) /edit \\1 /' -e 's/^pick (${REPAIR_SHORT}[0-9a-f]*) /drop \\1 /'" git rebase -i "$BASE_SHA"
git apply --check "$REFINE_HISTORY_STATE_DIR/repair-$REPAIR_SHORT.patch"
git apply "$REFINE_HISTORY_STATE_DIR/repair-$REPAIR_SHORT.patch"
git-stage-batch start
git-stage-batch show
git-stage-batch include --line ALL_REPAIR_HUNK_LINE_IDS --no-auto-advance
git --no-optional-locks diff --cached
git --no-optional-locks diff --check
git commit --amend --no-edit
python3 .claude/skills/refine-history/scripts/verify-head-snapshot.py --ref HEAD -- python3 -m compileall -q src tests
git-stage-batch stop
test -z "$(git --no-optional-locks status --short)"
git rebase --continue
```

If `git apply --check` fails, reconstruct the same one-target hunks manually
and verify the working diff against the saved patch. Never suppress the apply
failure. Restart the complete audit when the rebase finishes.

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
