# Rewrite Procedures

Use these boundary-changing procedures while running `refine-history`.
Substitute the installed skill root for `.agents/skills/refine-history` when
using another assistant. Delegate all message-only rewording to
`refine-commit-messages`.

These interactive-rebase procedures are a temporary executor fallback. Use
them only while installed `git-stage-batch rewrite --help` lacks the required
`apply` operation. Before each boundary change, require the current immutable
scan to validate:

```bash
git-stage-batch rewrite validate "$REWRITE_PLAN" --porcelain
```

After each completed rebase, regenerate the scan from the canonical base
before making another boundary decision.

## Split a broad committed snapshot

For one split candidate, rebase from that commit's parent. Rebase from
`BASE_SHA` only when editing several commits deliberately:

```bash
SPLIT_SHA=PUT_BROAD_COMMIT_SHA_HERE
python3 "$REFINE_HISTORY_HELPER" mark --phase rewriting --note "split $SPLIT_SHA"
GIT_SEQUENCE_EDITOR="sed -i '1s/^pick /edit /'" git rebase -i "$SPLIT_SHA^"
```

When the rebase stops, uncommit the snapshot:

```bash
git reset --mixed HEAD^
git --no-optional-locks status --short
git --no-optional-locks diff --stat
git-stage-batch start
```

Plan the replacement mini-series before staging. For each replacement commit,
identify:

- the smaller product state that will exist;
- the exact earlier state it evolves;
- the proof that belongs with or immediately after it;
- final-tree content that must still be absent;
- whether original hunks or a reconstructed whole-file snapshot best express
  the intermediate state; and
- residual content that is a separate later outcome.

Do not let original hunk boundaries define the new history. When hunks already
match the desired sublayer, use line selection:

```bash
git-stage-batch show
git-stage-batch include --line SUBLAYER_LINE_IDS --no-auto-advance
git --no-optional-locks diff --cached
git --no-optional-locks diff --cached --check
git commit
python3 .agents/skills/refine-history/scripts/verify-head-snapshot.py --ref HEAD -- python3 -m compileall -q src tests
git-stage-batch stop
git --no-optional-locks status --short
```

When the intermediate state is clearer as rewritten file content, edit the
working file to that state and stage its exact snapshot:

```bash
git-stage-batch show --file PATH --no-advance
git-stage-batch include --file PATH --as-stdin --no-auto-advance < PATH
git --no-optional-locks diff --cached -- PATH
git --no-optional-locks diff --cached --check
git commit
python3 .agents/skills/refine-history/scripts/verify-head-snapshot.py --ref HEAD -- python3 -m compileall -q src tests
git-stage-batch stop
git --no-optional-locks status --short
```

Whole-file reconstruction may simplify a tangled historical patch, but it
must express only one smaller product state. For Python snapshots, compilation
is only a floor; run a narrow import or behavior test that catches missing
runtime names.

Inspect the residual unstaged diff after every replacement commit. Do not
assume the residue is one final commit. Prefer minimal groundwork, first
consumer, narrow proof, later consumers and proof, then docs/examples after
the behavior exists. After `git-stage-batch stop`, run `git-stage-batch start`
again before reviewing or staging the next residual sublayer.

When the broad commit is fully replaced and the tree is clean:

```bash
git --no-optional-locks status --short
git rebase --continue
```

Resolve conflicts carefully. Use `git add` only to mark resolved conflict
paths; use `git-stage-batch` for ordinary staging. Restart the complete audit
after the rebase.

## Integrate a late repair or process commit

Inspect each suspicious commit and locate where every hunk first belongs:

```bash
REPAIR_SHA=PUT_REPAIR_SHA_HERE
git --no-optional-locks show --stat --patch --find-renames "$REPAIR_SHA"
git --no-optional-locks log --reverse --format='%H %s' "$BASE_SHA"..HEAD -- PATH_TOUCHED_BY_REPAIR
```

Do not squash a whole repair into a convenient earlier commit when its hunks
belong at different historical points. If any hunk cannot be allocated
confidently, stop instead of retaining a repair commit.

If the repair has several targets, first use the broad-snapshot split procedure
to replace it with one temporary repair commit per historical target. Confirm
that the resulting `HEAD^{tree}` still matches `pre-tree.txt`, then restart the
audit. Integrate each temporary repair with the one-target procedure below.
Do not mark several targets `edit` while carrying one unsplit patch through the
rebase: residual hunks at the first stop make later rebase steps unsafe.

For a one-target repair, save its patch, edit the target, and drop the repair:

```bash
TARGET_SHA=PUT_COMMIT_THAT_SHOULD_HAVE_CONTAINED_THE_HUNK
TARGET_SHORT=$(git --no-optional-locks rev-parse --short=7 "$TARGET_SHA")
REPAIR_SHORT=$(git --no-optional-locks rev-parse --short=7 "$REPAIR_SHA")
git --no-optional-locks show --format= --binary "$REPAIR_SHA" > "$REFINE_HISTORY_STATE_DIR/repair-$REPAIR_SHORT.patch"
python3 "$REFINE_HISTORY_HELPER" mark --phase rewriting --note "integrate $REPAIR_SHA into $TARGET_SHA"
GIT_SEQUENCE_EDITOR="sed -i -E -e 's/^pick (${TARGET_SHORT}[0-9a-f]*) /edit \\1 /' -e 's/^pick (${REPAIR_SHORT}[0-9a-f]*) /drop \\1 /'" git rebase -i "$BASE_SHA"
```

At the target, first require the complete one-target patch to apply. If the
check fails, reconstruct the same hunks manually and verify the resulting
working diff against the saved patch. Never suppress an apply failure:

```bash
git --no-optional-locks apply --check "$REFINE_HISTORY_STATE_DIR/repair-$REPAIR_SHORT.patch"
git apply "$REFINE_HISTORY_STATE_DIR/repair-$REPAIR_SHORT.patch"
git-stage-batch start
git-stage-batch show
git-stage-batch include --line ALL_REPAIR_HUNK_LINE_IDS --no-auto-advance
git --no-optional-locks diff --cached
git --no-optional-locks diff --check
git commit --amend --no-edit
python3 .agents/skills/refine-history/scripts/verify-head-snapshot.py --ref HEAD -- python3 -m compileall -q src tests
git-stage-batch stop
test -z "$(git --no-optional-locks status --short)"
git rebase --continue
```

Restart the complete audit when the rebase finishes.

## Repair a failing committed snapshot

Never add a later repair commit. If the newest rewritten commit fails, stage
the minimal fix and amend immediately:

```bash
git-stage-batch start
git-stage-batch show
git-stage-batch include --line FIX_LINE_IDS --no-auto-advance
git --no-optional-locks diff --cached
git commit --amend --no-edit
python3 .agents/skills/refine-history/scripts/verify-head-snapshot.py --ref HEAD -- python3 -m compileall -q src tests
git-stage-batch stop
```

To find an older failing snapshot:

```bash
for c in $(git --no-optional-locks rev-list --reverse "$BASE_SHA"..HEAD); do
  python3 .agents/skills/refine-history/scripts/verify-head-snapshot.py --ref "$c" -- python3 -m compileall -q src tests || { echo "FIRST_BAD=$c"; break; }
done
```

Edit that commit, stage the minimal fix, amend, verify, and continue:

```bash
BAD_SHA=PUT_FIRST_BAD_SHA_HERE
python3 "$REFINE_HISTORY_HELPER" mark --phase rewriting --note "repair snapshot $BAD_SHA"
GIT_SEQUENCE_EDITOR="sed -i '1s/^pick /edit /'" git rebase -i "$BAD_SHA^"
git-stage-batch start
git-stage-batch show
git-stage-batch include --line FIX_LINE_IDS --no-auto-advance
git --no-optional-locks diff --cached
git commit --amend --no-edit
python3 .agents/skills/refine-history/scripts/verify-head-snapshot.py --ref HEAD -- python3 -m compileall -q src tests
git-stage-batch stop
git rebase --continue
```

After any repair, rerun the verification loop and the full audit.
