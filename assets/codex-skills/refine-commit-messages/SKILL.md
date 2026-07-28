---
name: refine-commit-messages
description: Audit and, by default, rewrite the messages in an existing local linear commit series while preserving every patch and commit boundary. Use when the user wants to polish commit prose, enforce repository message conventions, repair series narrative or fourth-paragraph transitions, or resume an interrupted message-only rewrite. Use the explicit audit mode when the user wants findings and proposed messages without changing history. Do not use for changing commit contents or boundaries.
---

# Refine Commit Messages

Refine every message in `BASE_SHA..HEAD` as one series. By default, reword
noncompliant commits. Preserve the commit count, order, tree at every series
position, author identity/date, signature presence, and final tree.

Read `references/message-guidelines.md` before auditing. Treat the patches as
evidence for the prose, never as editable material.

## Usage

```text
$refine-commit-messages BASE_SHA
$refine-commit-messages audit BASE_SHA
$refine-commit-messages resume
```

For a fresh run, accept either one explicit base or the literal `audit`
followed by one explicit base. Do not infer the base from branch names, merge
bases, reflogs, or old state. The default is mutating refinement. Recognize
audit mode only when the user explicitly supplies `audit` as the first
argument or unmistakably asks for no history changes.

`resume` applies only to an interrupted default refinement. Audit runs have no
checkpoint to resume.

Before either mode, read repository guidance, representative recent messages,
and the commit-message hook. Repository-specific rules override the bundled
fallback rules. Use `.agents/internal/commit-message-drafter.md` in its
historical-commit mode when installed. Give it the target SHA, parent state,
patch, complete ordered series, position, and discovered rules; never ask it
to inspect the clean staged index for an already-committed patch.

## Audit mode

Audit mode must not update refs, run rebase, amend commits, create a
recovery ref, or write inside the repository. It may create temporary files
outside the repository for structured analysis.

The user-facing `audit` mode maps to the checkpoint helper's internal
`--audit-only` flag. Freeze and inspect the explicit range:

```bash
REPO_ROOT=$(git --no-optional-locks rev-parse --show-toplevel)
cd "$REPO_ROOT"
REFINE_MESSAGES_HELPER=.agents/skills/refine-commit-messages/scripts/refine-commit-messages-checkpoint.py
BASE_SHA=$(python3 "$REFINE_MESSAGES_HELPER" check-range --audit-only --base "$BASE_SHA")
AUDIT_TMP=$(mktemp -d)
python3 "$REFINE_MESSAGES_HELPER" inspect --base "$BASE_SHA" > "$AUDIT_TMP/scan.json"
git --no-optional-locks log --reverse --format='%H%n%B%n---' "$BASE_SHA"..HEAD
```

Inspect every patch as well as every message:

```bash
git --no-optional-locks log --reverse --stat --patch --find-renames "$BASE_SHA"..HEAD
```

Report every commit in order with `KEEP` or `REWORD`, the concrete rule and
patch evidence, and a complete proposed replacement for each `REWORD`.
Cross-check all mechanical signals in `scan.json`; explain a signal only when
repository guidance makes it a concrete false positive. Do not silently fix
anything.

For a machine-checked audit, use the structured shape in the default workflow
with `"mode": "audit-only"`, save it outside the repository, and run:

```bash
python3 "$REFINE_MESSAGES_HELPER" validate-audit --audit-only --audit-file "$AUDIT_TMP/audit.json" --base "$BASE_SHA"
```

## Start the default refinement

The helper rejects empty or nonlinear ranges and commits contained in local
remote-tracking refs:

```bash
REPO_ROOT=$(git --no-optional-locks rev-parse --show-toplevel)
cd "$REPO_ROOT"
REFINE_MESSAGES_HELPER=.agents/skills/refine-commit-messages/scripts/refine-commit-messages-checkpoint.py
export REFINE_MESSAGES_STATE_DIR=$(python3 "$REFINE_MESSAGES_HELPER" state-dir)
BASE_SHA=$(python3 "$REFINE_MESSAGES_HELPER" check-range --base "$BASE_SHA")
```

Confirm separately that the branch is a local unpublished draft. Stop when a
configured remote is stale or unavailable, branch policy is unknown, the
branch is protected, or publication evidence is ambiguous:

```bash
git --no-optional-locks branch --show-current
git --no-optional-locks branch -a --contains HEAD
git --no-optional-locks remote -v
git --no-optional-locks status --short
```

Require a named local branch, a clean index and worktree, and no Git operation
in progress. Then record the exact original sequence and create a recovery ref:

```bash
python3 "$REFINE_MESSAGES_HELPER" start --base "$BASE_SHA"
python3 "$REFINE_MESSAGES_HELPER" status --json
```

Do not read or reuse artifacts from an older run before `start`.
If the range contains signed commits, ensure the configured signing mechanism
can re-sign replacements. `verify-head` rejects a rewrite that strips or adds
signature presence.

## Resume

For the literal `resume` invocation, do not call `start`:

```bash
REPO_ROOT=$(git --no-optional-locks rev-parse --show-toplevel)
cd "$REPO_ROOT"
REFINE_MESSAGES_HELPER=.agents/skills/refine-commit-messages/scripts/refine-commit-messages-checkpoint.py
export REFINE_MESSAGES_STATE_DIR=$(python3 "$REFINE_MESSAGES_HELPER" state-dir)
python3 "$REFINE_MESSAGES_HELPER" status --json
BASE_SHA=$(python3 "$REFINE_MESSAGES_HELPER" check-resume)
RECOVERY_REF=$(python3 "$REFINE_MESSAGES_HELPER" recovery-ref)
git --no-optional-locks show-ref --verify "$RECOVERY_REF"
```

If a rebase is active, inspect `git status`, its todo/done files, and the last
checkpoint event. Continue only when they identify the same message edit and
series position. Aborting that rebase is safe; never abort and then call
`start`. Outside a rebase, `check-resume` requires the exact original tree
sequence and author metadata to remain intact.

## Audit and rewrite

Generate the current mechanical scan:

```bash
python3 "$REFINE_MESSAGES_HELPER" scan --base "$BASE_SHA"
```

Inspect every commit's body and patch. Write
`$REFINE_MESSAGES_STATE_DIR/audit.json` with this shape:

```json
{
  "schema": 1,
  "mode": "refine",
  "base": "FULL_BASE_SHA",
  "head": "FULL_CURRENT_HEAD_SHA",
  "conventions": {
    "sources": ["CONTRIBUTING.md", "fallback message guidelines"],
    "summary": "Concrete paragraph, prefix, tense, and wrapping rules applied"
  },
  "commits": [
    {
      "sha": "FULL_CURRENT_COMMIT_SHA",
      "subject": "Exact current subject",
      "signals": ["Exact signal copied from scan.json"],
      "verdict": "REWORD",
      "reason": "Concrete mismatch between message, patch, or series position",
      "patch_fidelity": "How the message accounts for the complete patch",
      "series_transition": "How this position follows and leads to its neighbors",
      "proposed_message": "Complete replacement subject and body"
    }
  ]
}
```

Use `KEEP` for compliant entries and omit `proposed_message`. When keeping a
mechanically signaled message because of an explicit repository override, add:

```json
"signal_false_positives": [
  {
    "signal": "Exact signal",
    "source": "Exact entry from conventions.sources",
    "reason": "Concrete overriding repository rule"
  }
]
```

Cover every commit exactly once in order. Validate the working audit:

```bash
python3 "$REFINE_MESSAGES_HELPER" validate-audit --allow-reword --base "$BASE_SHA"
```

Reword the earliest `REWORD` only. Save its proposed message under the state
directory, then let the helper create a controlled stop at that exact commit:

```bash
POSITION=PUT_ONE_BASED_POSITION_HERE
TARGET_SHA=PUT_CURRENT_FULL_SHA_HERE
MESSAGE_FILE="$REFINE_MESSAGES_STATE_DIR/message-$POSITION.txt"
python3 "$REFINE_MESSAGES_HELPER" begin-reword --base "$BASE_SHA" --position "$POSITION" --target "$TARGET_SHA"
git --no-optional-locks show --stat --patch --find-renames HEAD
git commit --amend -F "$MESSAGE_FILE"
python3 "$REFINE_MESSAGES_HELPER" verify-head --position "$POSITION"
git rebase --continue
python3 "$REFINE_MESSAGES_HELPER" verify --base "$BASE_SHA"
```

Do not stage, edit, reset, split, squash, reorder, or drop content. If a hook
changes the index or tree, stop and restore from the recovery ref. If a patch
does not match one coherent message, report that `refine-history` is needed
instead of changing its boundary here.

`begin-reword` uses a portable sequence editor and command-scoped Git settings
that disable abbreviated todo commands, autosquash, update-refs, rebase-merges,
and autostash. Do not replace it with an ad hoc interactive-rebase recipe.

After each reword, regenerate `scan.json` and rebuild the complete audit from
the beginning because all descendant SHAs changed. Continue until every entry
is `KEEP`. Vary fourth-paragraph phrasing while preserving the required series
position: future work for earlier commits, the singular final commit for the
penultimate commit, and a conclusion for the final commit.

## Completion gate

Validate the final all-`KEEP` audit, then complete:

```bash
python3 "$REFINE_MESSAGES_HELPER" validate-audit --base "$BASE_SHA"
python3 "$REFINE_MESSAGES_HELPER" verify --base "$BASE_SHA"
git --no-optional-locks status --short
git --no-optional-locks branch -a --contains HEAD
git --no-optional-locks remote -v
python3 "$REFINE_MESSAGES_HELPER" complete --base "$BASE_SHA"
```

Immediately before completion, reconfirm that remote-tracking information is
current, branch protection still permits rewriting, and publication evidence
is unambiguous. `complete` rechecks local remote-tracking containment,
checkpoint ownership, out-of-scope local refs, the structured audit, and every
message-only invariant; remote freshness and branch policy remain the caller's
responsibility. Report the recovery ref, old-to-new subjects and SHAs,
conventions applied, and the exact tree/author invariants verified.
