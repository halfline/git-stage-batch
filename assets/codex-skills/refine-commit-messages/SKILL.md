---
name: refine-commit-messages
description: Audit and, by default, rewrite the messages in an existing local linear commit series while preserving every patch and commit boundary. Use when the user wants to polish commit prose, enforce repository message conventions, repair series narrative or fourth-paragraph transitions, or resume an interrupted message-only rewrite. Use explicit audit mode for findings and proposed messages without history changes. Do not use for changing commit contents, order, or boundaries.
---

# Refine Commit Messages

Refine every message in `BASE_SHA..HEAD` as one series. By default, reword
noncompliant commits while preserving source order, commit count, every output
tree, patch-unit ownership, author and committer metadata, and the final tree.

Read `references/message-guidelines.md` completely before auditing. Read the
repository contribution guide, commit-message hook, documented message
validators, and representative recent messages. Repository rules override the
bundled fallback. Use `.agents/internal/commit-message-drafter.md` in its
historical-commit mode when installed.

## Usage

```text
$refine-commit-messages BASE_SHA
$refine-commit-messages audit BASE_SHA
$refine-commit-messages resume
```

A fresh run requires one explicit excluded base. Do not infer it from branch
names, merge bases, reflogs, prior plans, or product state. Default mode is
mutating. Recognize `audit` only when the user explicitly requests no history
changes. `resume` applies only to an active or latest durable product operation.

## Mechanical boundary

Use `git-stage-batch rewrite` for all range facts, plan validation, commit
construction, checkpoints, refs, recovery, continuation, abort, and
verification. A message plan may contain only `KEEP` and `REWORD` outputs.
Preserve each output's single source, complete ordered unit list, author, and
position. Edit only its operation, message, encoding when needed, and
rationale. Never stage, amend, rebase, reorder, split, integrate, drop, or
manually rewrite commits.

`rewrite apply` constructs deterministic unsigned commits and does not invoke
commit hooks or signing. If any source in a mutating range is signed, validation
reports it and apply removes the invalidated signature header by audited digest.
Do not promise signature preservation or attempt to copy a cryptographic
signature to a new commit. Run documented message validators when available
and inspect hook rules before apply.

If the patch does not fit one coherent message, stop and report that
`refine-history` is needed. If `git-stage-batch` is not in `PATH`, use
`pipx run git-stage-batch`; installed `rewrite --help` wins over this skill.

## Build the series audit once

Move to the repository root and create analysis files outside the worktree:

```bash
REPO_ROOT=$(git --no-optional-locks rev-parse --show-toplevel)
cd "$REPO_ROOT"
PLAN_DIR=$(mktemp -d)
REWRITE_PLAN="$PLAN_DIR/rewrite-plan.json"
VALIDATION="$PLAN_DIR/validation.json"
git-stage-batch rewrite scan "$BASE_SHA" --output "$REWRITE_PLAN"
git-stage-batch rewrite validate "$REWRITE_PLAN" --porcelain > "$VALIDATION"
```

The initial KEEP plan freezes the exact linear range. Read its canonical base,
tip, messages, encodings, authors, committers, signatures, patch units, trees,
publication facts, and mutation blockers. Scan and validation are read-only
and create no refs or checkpoints.

Build one compact series index before drafting. For each position, record the
SHA, subject, narrative role, one plain-language patch outcome, relevant parent
state, and only the prior capabilities needed to understand the change. Inspect
each complete patch once in order. Do not copy the whole prefix into each entry
or reread the raw series for every draft.

For each message, compare the full patch with repository rules and adjacent
series transitions. Reject unexplained local terms, coined shorthand,
artifact-only subjects, compressed labels, claims absent from the patch, and
meaningful patch outcomes absent from the prose. Draft from the target patch
and its relevant neighboring context, not from the clean staged index.

Classify every output:

- `KEEP` when the exact message is compliant and narratively accurate.
- `REWORD` when a complete replacement is required.

For `REWORD`, edit only `operation`, `message`, optional `encoding`, and a
concrete `rationale`. Keep all other generated fields exact. Cover every output
once and preserve order.

Validate the edited plan:

```bash
git-stage-batch rewrite validate "$REWRITE_PLAN" --porcelain > "$VALIDATION"
```

Require `valid: true`, equal source/output counts, zero split, integration, and
reorder outputs, exact final-tree replay, and no stale facts. Validation also
proves that every replacement is encodable.

## Audit mode

Audit mode stops after the edited plan validates. Report every source in order
with `KEEP` or `REWORD`, the exact repository rule and patch evidence, adjacent
series transition, and complete proposed message for every `REWORD`. Report
validator failures as failures; do not silently weaken or apply the plan.

Audit mode must not call `rewrite apply`, create recovery refs or checkpoints,
or write audit files inside the repository. Its external temporary plan is
disposable.

## Apply the message plan

If every output is `KEEP`, no rewrite is needed. Otherwise, refresh relevant
remote-tracking facts and verify provider metadata. A containing remote ref is
allowed only when it is the exact verified force-push review head. Permission
to rewrite locally does not authorize a push.

Require the validation report to say mutation is ready, then apply the same
validated plan:

```bash
if test -n "${REVIEW_HEAD_REF:-}"; then
  git-stage-batch rewrite apply "$REWRITE_PLAN" \
    --allow-published-ref "$REVIEW_HEAD_REF" --porcelain
else
  git-stage-batch rewrite apply "$REWRITE_PLAN" --porcelain
fi
git-stage-batch rewrite verify --porcelain
```

Repeat the review-head option for every authorized containing ref. Apply
rechecks the live branch, tip, index, worktree, Git operations, staging state,
saved batches, publication, and plan before creating its durable operation.
It builds behind an output ref and updates the branch once by compare-and-swap.

After apply, inspect the final messages in order, rerun repository message
checks and normal tests, and report source signatures removed by digest. Do not
redraft unchanged semantic decisions merely because descendant object IDs
changed.

## Resume or abort

For literal `resume`, run:

```bash
git-stage-batch rewrite status --porcelain
```

Before continuing, require `plan.operation_counts` to contain only `KEEP` and
`REWORD`. This prevents the message-only skill from adopting a boundary-changing
operation. When `active` is true, also require
`inspection.resume_ready: true`, then run:

```bash
git-stage-batch rewrite continue --porcelain
git-stage-batch rewrite status --porcelain
```

Continue according to the product's exact `next_action` until terminal. Require
`rewrite verify --porcelain` for `COMPLETE`. Never inspect private files, infer
a rebase position, or run Git continuation commands.

Use `rewrite abort` only when abandoning the active message operation. It
restores only operation-owned values and preserves safe manual recovery
guidance after foreign ref movement. Do not delete product state or refs.

When status reports a latest `COMPLETE` KEEP/REWORD operation, verify it and
finish the completion checks. `ABORTED` is not successful message refinement.
If there is no product operation, there is nothing to resume;
request an explicit base for a fresh run.

## Completion gate

Complete only when:

- every final message has a `KEEP` verdict under the discovered rules;
- a fresh message-only KEEP plan validates for the same base and current tip;
- source count, order, every position's tree and patch units, author and
  committer metadata, and final tree match the product proofs;
- `rewrite verify` passes for the latest completed rewrite;
- the index and tracked worktree are clean and no operation, batch, or staging
  session is active;
- normal tests and documented message checks pass; and
- publication evidence is freshly rechecked and remains authorized.

Report old-to-new subjects and SHAs, conventions applied, exact invariants,
removed signature digests, validation commands, and the product recovery ref.
Never publish or force-push unless the user separately asks.
