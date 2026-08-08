---
name: refine-history
description: Rewrite or audit an existing local commit series as a clean incremental history while preserving its final tree. Use when the user wants to inspect, polish, split, reword, reorder, or integrate fixup and repair commits after an optional base, infer the boundary from a tracked remote branch, safely rewrite a pull-request or merge-request branch, run an audit without mutation, or resume an interrupted refinement. Do not use for unstaged work or commits published outside an explicitly verified force-push review branch.
---

# Refine History

Rewrite the committed series in `BASE_SHA..HEAD` so every commit is one
coherent product state. Preserve the final tree exactly.

Read `references/rewrite-procedures.md` completely before editing a history
plan. Read the repository contribution guide, commit-message hook, and a
representative sample of recent commits before drafting messages. The
first-class `refine-commit-messages` skill owns the final message-only pass.

## Usage

```text
$refine-history [BASE_SHA]
$refine-history audit [BASE_SHA]
$refine-history resume
```

For a fresh run, accept an optional explicit excluded base. When omitted, let
`rewrite scan` use the fork point or merge base with the configured upstream.
Fail with an actionable request for `BASE_SHA` when no upstream exists. Never
infer a base from branch names, reflogs, old plan files, or prose notes.

`audit` performs the complete semantic and mechanical review without applying
the plan. `resume` applies only to a durable product operation reported by
`rewrite status`; it never resumes a prose phase or an interactive rebase.

## Authority boundary

Use `git-stage-batch rewrite` as the only rewrite engine:

- `scan` owns canonical range, commit, tree, metadata, signature, patch-unit,
  dependency, publication, and local-safety facts.
- `validate` owns schema, unit conservation, dependency crossings, exact Git
  replay, authorship, metadata, and final-tree proof.
- `apply`, `continue`, `abort`, `status`, and `verify` own commits, refs,
  checkpoints, recovery, resume, and independent verification.
- This skill owns semantic boundaries, narrative order, runnable snapshots,
  messages, and permission to use a verified review-head exception.

Edit only `plan.outputs`. Never edit `snapshot` or `safety`, treat rationale
as mechanical proof, run interactive rebase, reset or amend commits, apply a
rejected patch manually, or manipulate product-owned refs and state files.
When the installed CLI cannot validate the intended history, report the exact
limitation or keep the existing boundary.

If `git-stage-batch` is not in `PATH`, use `pipx run git-stage-batch`. Read
`git-stage-batch rewrite --help`; installed help wins if it disagrees with this
skill.

## Scan a fresh range

Move to the repository root and keep the semantic plan outside the worktree:

```bash
REPO_ROOT=$(git --no-optional-locks rev-parse --show-toplevel)
cd "$REPO_ROOT"
PLAN_DIR=$(mktemp -d)
REWRITE_PLAN="$PLAN_DIR/rewrite-plan.json"
VALIDATION="$PLAN_DIR/validation.json"
if test -n "${BASE_SHA:-}"; then
  git-stage-batch rewrite scan "$BASE_SHA" --output "$REWRITE_PLAN"
else
  git-stage-batch rewrite scan --output "$REWRITE_PLAN"
fi
git-stage-batch rewrite validate "$REWRITE_PLAN" --porcelain > "$VALIDATION"
```

Read the canonical base, tip, branch, safety blockers, remote containment, and
signature count from these product records. Scan and validation are read-only;
their candidate objects are quarantined and they create no operation state.
Dirty state may appear as an audit fact, but it blocks apply.

Before classifying publication, bind an explicit run-local publication scope.
By default it contains only the exact remote-tracking ref mapped from the
provider's freshly queried default branch and the exact remote-tracking refs
mapped from a fresh provider protected-branch query. Bind the provider
repository identity, both query records, full ref names, and fetched tips. A
configured upstream participates only when its provider repository, full ref,
and fetched tip exactly match that provider-default binding. An arbitrary
feature, WIP, or review upstream remains excluded. Stop when any default-branch,
protection, identity, or ref fact is stale, unavailable, or ambiguous. Require
zero range overlap only against those in-scope tips. Never infer the default
branch or protection from a branch name or configured upstream.

Report excluded categories and observed refs separately: unprotected WIP
branches, tags, and archived or closed review refs do not block the default
audit. Do not silently broaden the scope or turn an excluded ref into an apply
exception. Expand it only when the user or repository policy explicitly says
to, record that expansion, and recompute the bound scope before mutation.

An active review head, including one configured as the current upstream,
remains a narrow exception, not a scope expansion. Require fresh provider
evidence that it is the exact current review head and that force pushing is
expected; require zero overlap with the provider-default and protected scope;
and pass only each exact full `refs/remotes/...` review-head ref to apply. If
the product's broader `published-range` blocker includes an excluded ref and
cannot express the bound policy without allowing that ref, stop without
mutation and report the executor limitation. Permission to rewrite locally
never grants permission to push. The base may be published because it is
excluded from the rewrite.

## Build the semantic audit

Inspect every commit in order, including its full message, diffstat, patch,
exact patch units, and dependency evidence. Build one compact series index
with each commit's outcome, prerequisites, narrative role, and smallest
runnable state. Do not reread the complete range for every decision.

Classify every source as one of these intended outcomes:

- `KEEP`: one coherent, accurately described state already exists.
- `SPLIT`: independent ordered unit groups make smaller runnable states.
- `INTEGRATE`: later repair units belong in one or more earlier outcomes.
- `REORDER`: a complete source belongs earlier and every crossing is proven.
- `MESSAGE`: the boundary is sound but the prose needs the later message pass.

Treat a commit as a split or reorder candidate when it combines groundwork
with adopters, several user-visible outcomes, independent variants, later
enrichments, docs before behavior, or unrelated proof. Treat repair, fixup,
cleanup, and process-shaped commits as integration candidates. A file, module,
test file, or shared helper is not a concern boundary by itself.

For every pressured `KEEP`, identify a concrete candidate extraction and the
path-specific immediate breakage or narrative regression it would cause. If a
candidate can move without such breakage, keep auditing rather than accepting
a generic "related" rationale. Every output rationale should name the product
state and why its exact units belong together, while recognizing that the CLI
does not use prose as proof.

Edit the external plan according to `references/rewrite-procedures.md`, then
validate it:

```bash
git-stage-batch rewrite validate "$REWRITE_PLAN" --porcelain > "$VALIDATION"
```

Validation must account for every source unit exactly once, accept every
requested crossing, and reproduce the frozen final tree. An `UNKNOWN` or
`BLOCKED` edge fails closed. Unsupported atomic sources may remain whole in a
`KEEP` output but may not be crossed or split.

In `audit` mode, stop after validation and report every proposed output in
order. Include rejected proposals and their exact validator diagnostics. Do
not create refs, checkpoints, commits, or repository-local audit files.

## Apply a validated plan

Prefer one whole-range plan. Use another convergence pass only when the new
history exposes a genuinely new semantic decision. Before apply, require zero
overlap in the bound publication scope and a fresh validation report that says
mutation is ready. For a verified review head, accept `published-range` only
when it is the sole safety blocker, the provider-default and protected scope
has zero overlap, and every allowed containing ref is the exact verified
current review-head ref that will be passed to apply. Never pass an excluded
WIP, tag, or archived review ref merely to clear the blocker. Reconfirm the
scope and publication permission. The product recollects all preconditions
with those allowed refs during apply.

```bash
git-stage-batch rewrite validate "$REWRITE_PLAN" --porcelain > "$VALIDATION"
if test -n "${REVIEW_HEAD_REF:-}"; then
  git-stage-batch rewrite apply "$REWRITE_PLAN" \
    --allow-published-ref "$REVIEW_HEAD_REF" --porcelain
else
  git-stage-batch rewrite apply "$REWRITE_PLAN" --porcelain
fi
git-stage-batch rewrite verify --porcelain
```

Repeat `--allow-published-ref` for every verified containing review-head ref.
Apply builds the complete replacement chain behind owned refs, verifies it,
and updates the checked-out branch once by compare-and-swap. It does not run
commit hooks or sign commits. Inspect repository message rules before apply,
run documented message validators when available, and report every source
signature that validation says will be removed. Never imply that a rewritten
cryptographic signature remains valid.

After successful apply, save the returned recovery ref in the run report,
rescan from the same canonical base, and restart the semantic audit because
commit IDs and dependency positions changed. Never reuse or repair stale
snapshot fields.

When boundaries converge, invoke `$refine-commit-messages BASE_SHA` in its
default mutating mode. Do not reword commits directly in this skill. Rescan and
perform one final boundary audit afterward; if message work exposed a boundary
problem, return to a newly scanned rewrite plan.

## Resume or abort

For literal `resume`, run only:

```bash
git-stage-batch rewrite status --porcelain
```

When `active` is true, trust its phase, `next_action`, plan operation counts,
owned refs, blockers, and `inspection.resume_ready`. If resume is ready, run:

```bash
git-stage-batch rewrite continue --porcelain
git-stage-batch rewrite status --porcelain
```

Continue until the product reaches a terminal phase, then require
`git-stage-batch rewrite verify --porcelain` for `COMPLETE`. Never infer a
missing step from Git state or private files. If status reports blockers,
report them and the recovery ref without modifying unrelated state.

Use `rewrite abort` only when abandoning the active product operation. It
restores only a tip still owned by that operation and leaves compare-and-swap
manual recovery guidance when foreign movement makes restoration unsafe. Do
not delete state or refs and do not run the manual recovery command without
separately reviewing the exact live ref values.

When status names a latest `COMPLETE` operation, verify it, recover the base
from `source.base`, rescan, and resume the semantic audit. A latest `ABORTED`
operation is not rewrite evidence. If status has no operation,
there is nothing durable to resume; request an explicit base for a fresh run.

## Completion gate

Complete only after a final scan and semantic pass leave every output as
`KEEP`, the default `refine-commit-messages` pass has converged, and the final
KEEP plan validates. Also require:

- `rewrite verify` passes for the latest completed mutation;
- the index and tracked worktree are clean, with no active Git operation,
  staging session, saved batch, or rewrite operation;
- normal repository tests and message checks pass;
- no late repair/process commit or multi-outcome subject remains;
- remote containment is freshly rechecked and still authorized; and
- a repository-appropriate narrow build/import/behavior check passes for
  every commit snapshot, including commits that were never rewritten.

The bundled `scripts/verify-head-snapshot.py` may run that last check in a
temporary detached worktree. Choose the command for the repository; do not
blindly use a Python example in a non-Python project.

Report the canonical base, original and final counts, final subjects, splits,
integrations, reorders, rewords, pressured keeps and exact breakage reasons,
removed signature digests, validation/test commands, and every product
recovery ref. Never publish or force-push unless the user separately asks.
