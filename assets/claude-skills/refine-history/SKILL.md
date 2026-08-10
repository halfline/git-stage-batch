---
name: refine-history
description: Rewrite or audit an existing local commit series as a clean incremental history while preserving its final tree
user-invocable: true
disable-model-invocation: true
context: fork
argument-hint: "[base-sha] | audit [base-sha] | resume"
when_to_use: "Use when the user wants Claude Code to inspect, polish, split, reword, reorder, or integrate fixup and repair commits in a local draft series after an optional base commit, infer the boundary from a tracked remote branch, safely rewrite a pull-request or merge-request branch, run an audit without mutation, or resume an interrupted refinement. Examples: \"refine this history\", \"audit these commits\", \"split the broad commits after BASE_SHA\", \"fold fixups into the right commits\", \"resume refine-history\". Do not use for unstaged work or commits published outside an explicitly verified force-push review branch."
allowed-tools:
  - Read
  - Grep
  - Glob
  - LS
  - Edit
  - Write
  - Agent(commit-message-drafter)
  - Bash(git *)
  - Bash(git-stage-batch *)
  - Bash(pipx run git-stage-batch *)
  - Bash(mktemp *)
  - Bash(python3 *)
  - Bash(test *)
  - Bash(uname *)
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
/refine-history [BASE_SHA]
/refine-history audit [BASE_SHA]
/refine-history resume
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

Treat the repository, the dedicated resolution workspace, and helper processes
as one trusted same-user execution boundary. Use the product's existing
digests, workspace binding, owned refs, compare-and-swap updates, and recovery
records for stale-state detection, tree conservation, atomic updates, and
recovery.

Keep that workflow direct. Do not add a second approval format, custom Python
module loader, chained helper manifest, or parallel workspace-transfer
transaction. Add infrastructure only for a demonstrated correctness,
consistency, atomicity, or recovery requirement.

Edit only `plan.outputs` and `plan.partitioned_units`. Never edit `snapshot` or
`safety`, treat rationale as mechanical proof, run interactive rebase, reset or
amend commits, apply a rejected patch manually, or manipulate product-owned
refs and state files.
When the installed CLI cannot validate the intended history, report the exact
limitation as an open `UNREPRESENTABLE` finding and preserve the current
history for safety. Preserving a boundary is not a semantic `KEEP` verdict.

If `git-stage-batch` is not in `PATH`, use `pipx run git-stage-batch`. Read
`git-stage-batch rewrite --help`; installed help wins if it disagrees with this
skill.

## Scan a fresh range

Move to the repository root and keep the semantic plan outside the worktree:

```bash
REPO_ROOT=$(git --no-optional-locks rev-parse --show-toplevel)
cd "$REPO_ROOT"
PLAN_PARENT=${TMPDIR:-${TEMP:-${TMP:-}}}
if test -z "$PLAN_PARENT" && test "$(uname -s)" = Linux; then
  PLAN_PARENT=/var/tmp
fi
if test -n "$PLAN_PARENT"; then
  PLAN_DIR=$(mktemp -d "$PLAN_PARENT/git-stage-batch-refine-history.XXXXXXXX")
else
  PLAN_DIR=$(mktemp -d)
fi
REWRITE_PLAN="$PLAN_DIR/rewrite-plan.json"
VALIDATION="$PLAN_DIR/validation.json"
CAUSAL_LEDGER="$PLAN_DIR/causal-ledger.md"
REWRITE_WORKSPACE="$PLAN_DIR/rewrite-workspace"
if test -n "${BASE_SHA:-}"; then
  git-stage-batch rewrite scan "$BASE_SHA" --output "$REWRITE_PLAN"
else
  git-stage-batch rewrite scan --output "$REWRITE_PLAN"
fi
git-stage-batch rewrite validate "$REWRITE_PLAN" --porcelain > "$VALIDATION"
```

Read the canonical base, tip, branch, safety blockers, remote containment, and
signature count from these product records. Scan writes the requested fresh
plan; neither command updates commits, refs, checkpoints, an existing plan, or
resolution workspaces. They may reuse or update the disposable
history-snapshot cache; their candidate objects are quarantined and they create
no operation state. Dirty state may appear as an audit fact, but it blocks
apply.

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

First inspect every commit oldest to newest, including its full message,
diffstat, patch, and exact patch units, but defer dependency evidence. Build
one compact series index with each commit's stated outcome, prerequisites,
narrative role, and smallest runnable state. Do not reread the complete range
for every decision.

Keep the documented `scan`, resolve or validate, apply, and verify workflow
authoritative for the current plan. Avoid duplicate product invocations for
the same state, and reuse exact immutable commit, tree, unit, and dependency
evidence from its records while those inputs are unchanged.

Memoize semantic-audit conclusions in the causal ledger with
content-addressed keys over the patch-unit content and relevant snapshot,
dependency, and ownership evidence. When an input or decision changes,
invalidate and re-audit only its cone: the affected units, owners,
prerequisite dependents, and output snapshots. Expand to the whole range only
when that cone cannot be bounded or the unit inventory changes globally.
The CLI persistently caches immutable snapshot and dependency analysis for an
exact range and Git behavior while collecting live safety facts again. Reuse
that product cache, but never treat a cache hit as plan, replay, or final-range
proof. Incremental suffix scans, replay-tree caching, and Bloom-filter path
prefilters are not implemented; do not claim or depend on them, and never use
a probabilistic prefilter as correctness proof.

At each snapshot, compare the commit's claim with its implementation, model,
tests, documentation, and user-visible behavior. A contradiction among those
views pressures the unit that repairs it even when the later subject describes
a credible independent feature.

Then perform a mandatory causal pass newest to oldest. Audit every exact unit,
coherent unit group, and semantic sub-unit; when one mechanical unit contains
several semantic outcomes, record those sub-unit outcomes and use partitioned
RESOLVED provenance only when explicit resolution can materialize them safely.
For each group, keep a compact causal ledger
with its original source, fresh unit IDs, the outcome newly introduced by its
source, its earliest honest semantic owner and evidence, its semantic
prerequisites, its mechanical placement evidence, its intended output position
and materialization, its intended disposition, and its validation state. Use
the series index, full messages, focused `git --no-optional-locks log -S` or
`-G` searches, and the relevant source, test, and documentation snapshots as
ownership evidence.

Persist that compact ledger at `CAUSAL_LEDGER`, outside the worktree. It is
non-executable audit evidence; the validated plan remains the only rewrite
instruction. Update the ledger after every validation and apply so original
source provenance survives fresh scans and context loss.

Assign semantic ownership before consulting `earliest_position`, `BLOCKED`,
or `UNKNOWN`. The owner is the earliest commit whose stated product scope,
runnable behavior, model, or contract would already be wrong or incomplete
without the change. It is not the oldest touched line and never defaults to the
first blocker.

After ownership, perform a mandatory ordering pass:

1. Add the semantic prerequisite edges among owned outcomes.
2. Overlay `earliest_position`, `BLOCKED`, `UNKNOWN`, and exact replay evidence
   from the scan onto those edges.
3. Choose the earliest feasible chronology that preserves every causal owner,
   semantic prerequisite, and mechanical placement constraint.
4. Use `EXACT` when every required crossing is proven. Use `RESOLVED` when the
   required owner or prerequisite order is noncommuting but the explicit
   resolution workflow can safely materialize the intended snapshots.

Mechanical placement therefore selects and constrains chronology after
ownership; it never reassigns ownership. A required placement that is neither
exactly proven nor safely materializable remains `UNREPRESENTABLE`.

For every `RESOLVED` output, audit each authorized path from its actual parent
tree through every later natural source/path transition. The parent-to-result
transition may introduce only the semantic portion owned by that output; never
seed an earlier snapshot with bytes owned by a later output or the frozen final
tree as a hidden stepping stone. If the intended chronology cannot be
materialized directly, change the output topology or leave the placement
`UNREPRESENTABLE`.

When a partitioned unit occurs in several outputs, audit its strictly increasing
`output_indexes` as one progressive chronology. Each occurrence must start from
the state left by preceding outputs, make a real transition, and introduce only
its owned portion. Never resolve occurrences independently from
`SOURCE_BEFORE`, repeat a later result early, or accept a no-op occurrence.

Use this counterfactual test for every later group:

1. Without the source commit's genuinely new outcome, is the group still
   needed to make an earlier promised outcome correct or complete?
2. Without this group, is the source commit's new outcome still coherent?

Two yes answers identify a mixed source: partition the repair from the new
outcome. An explicitly narrower earlier scope can instead make a later
extension legitimate.

A later `EXACT` rejection after moving units is source-wide placement
evidence: reopen every moved unit from that source for materialization,
prerequisites, partitioning, and chronology. It is not contrary causal
evidence and never overrides an established semantic owner. Retain
`RESOLVED` when explicit resolution safely materializes the intended
snapshots; otherwise retain the intended owner as `UNREPRESENTABLE` with the
exact diagnostic. Reassign a unit to its natural source boundary only when
independent causal evidence changes its owner.

Classify intended outputs after the group audit:

- `KEEP`: one coherent, accurately described state exists at its earliest
  honest causal position.
- `SPLIT`: independent ordered unit groups make smaller runnable states.
- `INTEGRATE`: later repair units belong in one or more earlier outcomes.
- `REORDER`: a complete source belongs earlier and every crossing is proven.
- `MESSAGE`: the boundary is sound but the prose needs the later message pass.

The generated all-`KEEP` plan is an unaudited identity template, not a semantic
verdict. Subjects are claims to test, not candidate filters. Treat a commit as
a split or reorder candidate when it combines groundwork with adopters,
several user-visible outcomes, independent variants, later enrichments, docs
before behavior, or unrelated proof. Search for repair content under every
subject, not only repair-, fixup-, cleanup-, hardening-, or process-shaped
commits. A file, module, test file, or shared helper is not a concern boundary
by itself.

Tests, documentation and manual paragraphs, examples, fixtures, translated
strings, completion entries, build files, and packaging metadata are
first-class groups. Map each one to the behavior it proves, describes,
translates, or exposes. Respect repository conventions that keep support
artifacts in separate commits, but place each artifact at the earliest support
commit for that behavior and partition mixed artifact commits by outcome.

Use `OWNED_HERE`, `MOVE`, `UNRESOLVED`, and `UNREPRESENTABLE` as audit states,
never as plan operations. `OWNED_HERE` means the group is already at its
earliest honest owner. `MOVE` means it has a different known owner and a plan
candidate. `UNRESOLVED` means semantic ownership is not yet known, while
`UNREPRESENTABLE` means the owner is known but the current mechanical unit or
executor cannot express the move. A rejected intended plan remains
`UNREPRESENTABLE` with its exact unit IDs and validator diagnostic; never
retarget it to the blocker or relabel it `KEEP`.

For every pressured `KEEP`, identify a concrete candidate extraction and the
path-specific immediate breakage or narrative regression it would cause. If a
candidate can move without such breakage, keep auditing rather than accepting
a generic "related" rationale. Every output rationale should name the product
state and why its exact units belong together, while recognizing that the CLI
does not use prose as proof.

Edit the external plan according to `references/rewrite-procedures.md`. For an
all-`EXACT` plan, validate it directly:

```bash
git-stage-batch rewrite validate "$REWRITE_PLAN" --porcelain > "$VALIDATION"
```

When the reviewed plan contains any `RESOLVED` output, create its dedicated
external workspace and advance one reported result at a time instead:

```bash
git-stage-batch rewrite resolve "$REWRITE_PLAN" \
  --workspace "$REWRITE_WORKSPACE" --porcelain
# Edit only each path's state, mode, and result artifact bytes.
git-stage-batch rewrite resolve "$REWRITE_PLAN" \
  --workspace "$REWRITE_WORKSPACE" --accept --porcelain
```

Repeat the edit and `--accept` step until the product reports `COMPLETE`,
then validate the same plan-workspace pair:

```bash
git-stage-batch rewrite validate "$REWRITE_PLAN" \
  --workspace "$REWRITE_WORKSPACE" --porcelain > "$VALIDATION"
```

Any plan-file byte change after workspace creation invalidates use of the
prior workspace binding; resolve the exact file in a new workspace. A semantic
edit to `plan.outputs` or `plan.partitioned_units` additionally invalidates the
prior request keys, results, receipts, and artifact assumptions. The edit does
not itself invalidate immutable Git tree or object IDs; an unchanged ID
remains content-addressed evidence, but no prior record binds it to or
authorizes the edited plan.

Validation must assign every ordinary source unit exactly once, account for
every declared partitioned occurrence, accept every requested crossing, and
reproduce the frozen final tree. Every `UNKNOWN` and every unaccounted
`BLOCKED` crossing in an `EXACT` output fail closed; an implemented compound
movement may cross a complete `BLOCKED` chain only when all grouped units share
the same semantic outcome. A `RESOLVED` output instead requires a completed
workspace bound to the current plan that explicitly materializes its intended
snapshot. Unsupported atomic sources may remain whole in a `KEEP` output but
may not be crossed or split without safe explicit resolution.

In `audit` mode, stop after validation and report both the desired semantic
history and whether each disposition is representable. Include every
`UNRESOLVED` or `UNREPRESENTABLE` finding, rejected proposal, and exact
validator diagnostic. A mechanically valid all-`KEEP` plan cannot suppress an
open causal finding. Do not create refs, checkpoints, commits, or
repository-local audit files.

## Apply a validated plan

Prefer one whole-range plan. Use another convergence pass only after the prior
pass places one or more groups at their actual semantic owners and the rewrite
exposes a new decision or changes the inventory for remaining open groups.
Before starting another pass, name the newly enabled `INTEGRATE`, `SPLIT`, or
`REORDER` decision and its expected output-count or operation-count delta. A
fresh base or fresh unit IDs alone are not progress.
Never apply a validated landing at a non-owner blocker as a mechanical
stepping stone. Before apply, require every output in that pass to be a
coherent runnable state, zero overlap in the bound publication scope, and a
fresh validation report that says mutation is ready. For a verified review
head, accept `published-range` only when it is the sole safety blocker, the
provider-default and protected scope has zero overlap, and every allowed
containing ref is the exact verified current review-head ref that will be
passed to apply. Never pass an excluded WIP, tag, or archived review ref merely
to clear the blocker. Reconfirm the scope and publication permission. The
product recollects all preconditions with those allowed refs during apply.

When a full-series result has already passed apply, `rewrite verify`, and its
selected semantic snapshot checks, its exact ordered output chain may serve as
a verified prefix for later direct child commits. Continue from that prefix
only after binding its canonical base, tip and tree, ordered commit/tree vector,
verification digest, command-allocation policy, and clean repository state.
Require every appended commit to be one coherent child of the preceding tip,
and record its earlier causal owner in a new external ledger. Direct append is
source chronology for the next pass; it neither changes semantic ownership nor
permits editing a completed checkpoint. A changed prefix object or tree,
verification policy, earlier command allocation, or unbounded dependency cone
requires fresh verification instead of continuation.

After a direct append, run a fresh `rewrite scan` from the same canonical base
through the new tip. The suffix is new refinement input, not an active product
operation that `rewrite continue` can resume. Do not rebuild the authenticated
prefix or recreate a completed `RESOLVED` workspace merely because the tip
grew. In the fresh scan, prefix outputs are ordinary immutable sources. Use a
new `RESOLVED` output only for a demonstrated dependency or topology constraint
and bind its new workspace to the fresh plan.

Use three verification tiers for a full-series result:

1. **Object and plan:** audit every output, including its parent, tree, message,
   author, encoding, operation, source-unit and path ownership, output order,
   original-source provenance, product apply report, and `rewrite verify`.
2. **Semantic boundaries:** run the relevant narrow build, import, or behavior
   checks at changed owners, immediate adopters or test successors, later
   natural boundaries, and explicitly justified risk-selected preserved states.
   Freeze a manifest that maps each selected exact output to its risk and exact
   commands. Each command must exist at that snapshot. Run all commands for one
   output in one clean checkout, sharing generated state only within that group
   and keeping it outside the checkout.
3. **Final tip:** run the complete normal repository test suite, message checks,
   and repository-appropriate build at the final candidate tip.

An unchanged verified prefix may reuse immutable semantic-boundary receipts
whose exact inputs and assumptions remain valid. Reauthenticate the complete
combined output chain in the object-and-plan tier, run the semantic-boundary
tier for the suffix and every boundary whose allocation or assumptions changed,
then run the complete final-tip tier. Keep boundary selection tied to recorded
risk rather than expanding it to unchanged outputs without cause.

When the authority repository uses object alternates, borrowed packs, or
another non-ordinary object store, a detached worktree is not portability
proof. Rehearse the verified prefix, direct append, and any follow-on rewrite
in a disposable ordinary clone with no alternates. Reacquire every
repository-bound scan or resolution workspace there, require the same prefix
and resulting commit/tree sequence, and run the same three verification tiers
at their scoped boundaries before authority mutation.

Give every verification or rehearsal attempt a unique immutable run root. On
failure, preserve the exact command, environment, exit status, diagnosed cause,
transcript, receipt, diagnostics, and repository state; never retry in place,
overwrite the failed evidence, or treat it as authorization. Do not blindly
repeat an unchanged command and environment. A runner defect requires a
documented correction and a new attempt, while a semantic failure reopens the
plan. A failed suffix attempt does not invalidate an independently
authenticated prefix, but only a clean receipt may authorize continuation or
authority mutation.

When an execution harness authenticates Git-generated object IDs or object
namespace deltas across repositories, never depend on Git's automatic `%h`
abbreviation. Set `core.abbrev=40` in every Git and product child environment,
reject a child that does not observe it, and record full object IDs. Rehearse
the object-producing path in two ordinary no-alternates clones whose object
stores have materially different cardinalities, and require identical full-ID
object graphs. An identical content prefix does not make automatic
abbreviations repository-independent.

If a commit lands but a later harness gate fails before its verification or
checkpoint record, stop and preserve that failed run. Do not reset, amend, or
recommit the exact landed commit. A separately identified successor may adopt
it only after binding its HEAD, parent, tree, index, refs, reflogs, hook
transcript, product operation state, and exact object-namespace delta, and
proving the failure occurred after commit creation but before verification and
checkpoint publication. Start a new checkpoint lineage with an explicit
adoption record, run every omitted check, and mark the adopted commit verified
before creating another commit. Any mismatch requires manual recovery.

For a plan with `RESOLVED` outputs, revalidate and apply the same completed
plan-workspace pair:

```bash
git-stage-batch rewrite validate "$REWRITE_PLAN" \
  --workspace "$REWRITE_WORKSPACE" --porcelain > "$VALIDATION"
git-stage-batch rewrite apply "$REWRITE_PLAN" \
  --workspace "$REWRITE_WORKSPACE" --porcelain
git-stage-batch rewrite verify --porcelain
```

For an all-`EXACT` plan, omit every resolution command and `--workspace`
option and use the ordinary flow below.

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
For a resolved plan, combine those repeated options with
`--workspace "$REWRITE_WORKSPACE"`. Apply revalidates the completed external
workspace binding and copies it into operation-owned state before activation.
Continuation and independent verification replay that owned copy, so they do
not depend on the external workspace remaining present.
Apply builds the complete replacement chain behind owned refs, verifies it,
and updates the checked-out branch once by compare-and-swap. It does not run
commit hooks or sign commits. Inspect repository message rules before apply,
run documented message validators when available, and report every source
signature that validation says will be removed. Never imply that a rewritten
cryptographic signature remains valid.

After successful apply, save the returned recovery ref in the run report,
rescan from the same canonical base, and restart the semantic audit because
commit IDs and dependency positions changed. Never reuse or repair stale
snapshot fields. Remap every open causal intention to fresh unit IDs and keep
its semantic owner and evidence until it is resolved. Also carry the
original-source provenance of every completed integration and confirm on the
fresh snapshot that its outcome appears at the intended owner rather than
merely disappearing into a new hunk.

For every moved group, verify the changed owner output, its immediate adopter
or test successor, and every later natural source or test boundary whose API
or representation the group crosses before declaring convergence.

When boundaries converge, invoke `/refine-commit-messages BASE_SHA` in its
default mutating mode. Do not reword commits directly in this skill. Rescan and
perform one final boundary audit afterward; if message work exposed a boundary
problem, return to a newly scanned rewrite plan.

## Resume or abort

For literal `resume`, run only:

```bash
git-stage-batch rewrite status --porcelain
```

When `active` is true, trust its phase, `next_action`, plan operation counts,
owned refs, blockers, `inspection.resolution_matches`, and
`inspection.resume_ready`. Require `inspection.resume_ready` to be true.
`inspection.resolution_matches` must be null for an all-`EXACT` operation or
true for a resolved operation; false blocks continuation. When those
conditions hold, run:

```bash
git-stage-batch rewrite continue --porcelain
git-stage-batch rewrite status --porcelain
```

Continue until the product reaches a terminal phase, then require
`git-stage-batch rewrite verify --porcelain` for `COMPLETE`. Never infer a
missing step from Git state or product-owned files. If status reports blockers,
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
If the external causal ledger is unavailable after resume, use the reported
recovery ref and read-only Git inspection to reconstruct original-source
provenance before another apply. Until that reconstruction succeeds, record
the missing provenance as `UNRESOLVED` and do not claim completion.

## Completion gate

Complete only after a final chronological index and newest-to-oldest causal
ledger leave every fresh semantic group `OWNED_HERE`, the default
`refine-commit-messages` pass has converged, and the final KEEP plan validates.
Generated or validated all-`KEEP` output is necessary mechanical proof, not
sufficient semantic proof. Also require:

- `rewrite verify` passes for the latest completed mutation;
- the index and tracked worktree are clean, with no active Git operation,
  staging session, saved batch, or rewrite operation;
- normal repository tests and message checks pass;
- no repair unit hides under a clean subject and no `UNRESOLVED` or
  `UNREPRESENTABLE` finding remains;
- no output combines independent outcomes, and every prior integration's
  original-source provenance has been checked against its intended owner;
- remote containment is freshly rechecked and still authorized; and
- the object-and-plan, selected semantic-boundary, and final-tip verification
  tiers all pass, including any authenticated verified-prefix continuation.

The bundled `scripts/verify-head-snapshot.py` may run selected boundary checks
in temporary detached worktrees. Choose commands for the repository and each
boundary; do not blindly use a Python example in a non-Python project.

Report the canonical base, original and final counts, final subjects, splits,
integrations, reorders, rewords, pressured keeps and exact breakage reasons,
removed signature digests, validation/test commands, and every product
recovery ref. Never publish or force-push unless the user separately asks.
