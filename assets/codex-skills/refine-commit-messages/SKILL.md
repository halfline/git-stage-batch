---
name: refine-commit-messages
description: Audit and, by default, rewrite the messages in an existing local linear commit series while preserving every patch and commit boundary. Use when the user wants to polish commit prose, enforce repository message conventions, repair series narrative or fourth-paragraph transitions, or resume an interrupted message-only rewrite. Use the explicit audit mode when the user wants findings and proposed messages without changing history. Do not use for changing commit contents or boundaries.
---

# Refine Commit Messages

Refine every message in `BASE_SHA..HEAD` as one series. By default, reword
noncompliant commits. Preserve the commit count, order, tree at every series
position, author identity/date, signature presence, and final tree.

Read `references/message-guidelines.md` before auditing. Treat the patches as
evidence for the prose, never as editable material. Within each message,
reject unexplained local terms, coined shorthand, and compressed labels that
make a newcomer decode the prose when a plain sentence could state the
behavior directly.

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
historical-commit mode when installed.

Build one compact series index before drafting. Record the base, source head,
and overall goal once. For each position, record its SHA, subject, narrative
role, one plain-language sentence describing the patch outcome, and only the
prior capabilities needed to understand that change. Do not copy the whole
prefix of earlier entries into each new entry. Inspect each patch once in
order and update the index with that commit's state change.

For an individual draft, provide the target SHA, relevant parent state, patch,
overall goal, target index entry, adjacent entries, and discovered rules. Do
not resend the full index or reread the complete raw series for every commit,
and never ask the drafter to inspect the clean staged index for an
already-committed patch.

Persist the index as `series-index.json`, bound to the canonical base and
source head. In audit mode, keep it under the external audit temporary
directory. In default mode, write it under
`$REFINE_MESSAGES_STATE_DIR` after `start`. Reuse a matching index after
context compaction or `resume`; never repeat the range-wide semantic read just
to reconstruct lost working context.

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
```

`scan.json` contains every message and its mechanical signals. Inspect the
patches once:

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
can re-sign them. The one-pass callbacks explicitly re-sign originally signed
positions and keep originally unsigned positions unsigned; a signing failure
stops the rebase.

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

Use the checkpoint phase reported by `status`. For the current `applying`
phase, inspect `git --no-optional-locks status`, the rebase todo/done files, and
the last checkpoint event. Continue only when they identify the same rewrite
plan and series position. After resolving a transient hook failure or other
understood stop, run:

```bash
git --no-optional-locks -c commit.gpgSign=false rebase --continue
python3 "$REFINE_MESSAGES_HELPER" finalize-apply --base "$BASE_SHA"
```

If no rebase is active while the phase is `applying`, compare `HEAD` with the
checkpoint's `rewrite_source_head`. An equal value means the rebase never
started or was aborted; fix the cause and rerun `apply-audit`. A different
value means the rebase changed history, so run only `finalize-apply` and let it
verify the result. Aborting the rebase is safe; never abort and then call
`start`. If a message itself fails the commit hook, abort the controlled
rebase, correct that proposal in `audit.json`, and rerun `apply-audit` from the
existing checkpoint. Outside a rebase,
`check-resume` requires the exact original tree sequence and author metadata
to remain intact.

A checkpoint in the older `rewriting` phase predates the one-pass workflow. If
its rebase is active, abort that rebase to return to the saved pre-step state,
then run `apply-audit`, which validates the existing audit; do not call
`start`.
If no rebase is active, first run `verify` and `scan`, update the audit's
position-bound SHAs and mechanical signals, and recheck only the message that
already landed and its neighbors before using `apply-audit`.

## Audit and rewrite

Generate the current mechanical scan:

```bash
python3 "$REFINE_MESSAGES_HELPER" scan --base "$BASE_SHA"
```

Use the series index to inspect every commit's body and patch exactly once.
Write
`$REFINE_MESSAGES_STATE_DIR/audit.json` with this shape:

```json
{
  "schema": 1,
  "mode": "refine",
  "base": "FULL_BASE_SHA",
  "head": "FULL_CURRENT_HEAD_SHA",
  "conventions": {
    "sources": ["CONTRIBUTING.md", "fallback message guidelines"],
    "summary": "Concrete structure, plain-language, term, and wrapping rules"
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

For a `REWORD`, write `patch_fidelity` and `series_transition` about the
proposed replacement; the helper carries those findings into the final audit.
Cover every commit exactly once in order.

If every verdict is `KEEP`, proceed directly to the completion gate. Otherwise
apply every replacement in one controlled rebase:

```bash
python3 "$REFINE_MESSAGES_HELPER" apply-audit --base "$BASE_SHA"
```

`apply-audit` validates `scan.json` and the complete audit before creating a
rebase. Do not run a separate validation pass first.

Do not stage, edit, reset, split, squash, reorder, or drop content. If a hook
changes the index or tree, stop and restore from the recovery ref. If a patch
does not match one coherent message, report that `refine-history` is needed
instead of changing its boundary here.

`apply-audit` freezes the validated audit by series position and adds one
constant-size verification callback after every pick in a portable rebase
todo. Each callback restores that position's expected message and signature
presence while verifying its tree and author. The callbacks run from a frozen
copy inside the worktree's Git directory, so historical checkouts cannot
replace the helper. The rebase disables abbreviated todo commands, autosquash,
update-refs, rebase-merges, and autostash. Afterward, the helper performs one
linear whole-series check, regenerates `scan.json`, and converts the audit to
current all-`KEEP` entries.

Do not rebuild the complete audit merely because rewritten ancestors changed
descendant SHAs. Reinspect only a message that differs from the validated
plan, its adjacent transitions, or evidence affected by a changed repository
rule. If boundaries, order, or patches changed, stop rather than trying to
repair the audit. Ordinary successful application needs one initial semantic
audit. Later checks are linear mechanical verification and never repeat
semantic drafting once per reworded commit.

## Completion gate

Reconfirm publication safety, then let the helper validate the final
all-`KEEP` audit and complete:

```bash
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
