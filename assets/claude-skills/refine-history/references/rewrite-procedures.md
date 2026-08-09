# Rewrite Plan Procedures

Use these procedures only while running `refine-history`. The semantic plan is
the sole executable description of changed boundaries. The external causal
ledger retains non-executable ownership evidence across passes.
`git-stage-batch rewrite` is the sole mechanism for validating and applying
the boundaries.

## Bind publication scope

Create a run-local publication-scope record before deciding whether the range
is unpublished. Its default included set is deliberately narrow:

1. Query the provider immediately before the decision for the repository's
   current default branch. Record the provider and repository identity, query
   time and response digest, then map that branch to one exact full
   remote-tracking ref and freshly fetched tip.
2. In the same fresh evidence window, query every currently protected branch
   in that repository. Record its query time and response digest, then map each
   returned branch to an exact full remote-tracking ref and freshly fetched tip.
3. Resolve a configured upstream, when one exists, only as a consistency fact.
   It participates through step 1 only when its provider repository, full ref,
   and fetched tip exactly match the provider-default binding. Do not add an
   arbitrary feature, WIP, or review upstream to the included set.
4. Deduplicate the full ref/tip vector and compute reachability from every
   commit in `BASE_SHA..HEAD` only to those bound tips. Classify the range as
   unpublished only when that in-scope overlap set is empty.

Fail closed when the provider cannot resolve its current default branch or
enumerate protected branches, either query is stale, the provider-default or a
protected branch cannot be mapped and fetched exactly, or any identity or tip
fact is ambiguous. Never infer the default branch or protection from names such
as `main`, `master`, `release`, or `stable`, a configured upstream, or a remote
symbolic HEAD.

Inventory and report excluded evidence without using it in the default overlap
decision: unprotected WIP remote branches, including any configured upstream
that does not match the provider-default binding, tags, and archived or closed
review refs. Report each category, its observed exact refs, and why it is
excluded. These refs neither prove safety nor block the default audit. Do not
silently promote one into scope because it exists or because the product
reports it. Only an explicit user instruction or repository policy may expand
the included set; record the exact additional category or refs before
recollecting facts and recomputing all overlap. Never narrow the default set.

Treat a current pull-request or merge-request head separately. It may authorize
local rewrite only when fresh provider evidence binds the exact open review,
head repository, head branch, current head object, and expected force-push
workflow; the provider-default and protected overlap must still be empty.
A configured upstream that is this exact current active review head may use
only this exception; it never joins the default included set. Record only the
exact full `refs/remotes/...` ref or refs that map to that head and pass only
those repeated values as `--allow-published-ref`. An unprotected WIP ref, tag,
archived or closed review ref, target branch, protected review head, or
name-based guess is never this exception.

Compare the bound scope with the product's fresh `safety.remote_containment`
record. The installed product may conservatively report `published-range` for
an excluded remote-tracking ref. Never pass that ref as a review-head exception
merely to make apply proceed. When apply cannot express the reviewed scope
without such an allowance, preserve the history and report the exact executor
limitation. The audit may still report zero in-scope overlap together with the
excluded containment and non-mutating result.

## Immutable and editable fields

Edit only `plan.outputs` and `plan.partitioned_units` in a fresh
`rewrite scan` document. Never edit:

- `schema_version`;
- any `snapshot` fact, including commit metadata, unit IDs, dependencies, or
  tree IDs; or
- `safety`, which is advisory and recollected by validation and apply.

Each output retains the generated fields `operation`, `source_commits`,
`materialization`, `source_unit_ids`, `message`, `encoding`, `author`, and
`rationale`. Copy the complete generated author object instead of
reconstructing it. Use full object and unit IDs. Rationale explains semantic
intent but never authorizes a crossing.

`plan.partitioned_units` starts empty. Add a record only when one mechanical
unit contains semantic content for several output snapshots. Its `unit_id`
names that source unit and its strictly increasing `output_indexes` name every
zero-based RESOLVED output where part of the unit appears.

Across the complete output list:

- assign every ordinary source unit exactly once; repeat a declared
  partitioned unit exactly once in each listed RESOLVED output and nowhere
  else;
- keep unit order within each source unless a proven movement changes output
  order;
- preserve every empty source explicitly unless an integration deliberately
  consumes it;
- keep every output non-empty unless the source was already empty; and
- require validation to reproduce the frozen final tree.

## Assign ownership, prerequisites, and placement

Treat the generated KEEP outputs as an identity-plan template. Before editing
them, audit every source from newest to oldest at exact-unit or semantically
inseparable-group granularity and decide where each outcome first belongs.
When one mechanical unit contains several semantic outcomes, name the
sub-unit ownership. Exact replay cannot divide it; use declared partitioned
provenance and explicit RESOLVED snapshots only when the resolution workflow
can materialize every portion safely.

For each group, first identify the earliest commit whose promised product
state would be wrong or incomplete without it. Test mixed sources by removing
their genuinely new outcome from consideration: if the group is still needed
earlier and the later outcome remains coherent without it, the group is a
repair for the earlier owner. Fix that causal owner before choosing an output
position.

After ownership is fixed, determine the semantic prerequisite edges among the
owned outcomes. Then overlay the scan's `earliest_position`, `BLOCKED`,
`UNKNOWN`, and exact replay evidence on that prerequisite graph. Choose the
earliest feasible chronology that simultaneously preserves causal ownership,
semantic prerequisites, and mechanical placement. This ordering pass is
mandatory: placement evidence constrains and selects the chronology even
though it never changes the owner.

Use `EXACT` for an output when every crossing required by that chronology is
proven. Use `RESOLVED` when the owner or prerequisite order requires a
noncommuting placement and the explicit resolution workspace can safely
materialize the intended snapshot. A mechanical blocker is a placement
frontier, never an owner. A required placement that is neither exactly proven
nor safely materializable is `UNREPRESENTABLE`; do not retain or retarget it
merely to make the plan validate.

Tests, documentation paragraphs, translated strings, fixtures, examples,
completion, build, and packaging entries each belong to the behavior they
prove, describe, translate, or expose; a shared file or hunk does not give
them one semantic owner.

Keep four non-plan audit states. `OWNED_HERE` means the group is already at its
earliest honest owner. `MOVE` means another known owner has a plan candidate.
`UNRESOLVED` means ownership is not yet known. `UNREPRESENTABLE` means
ownership is known but the current unit inventory or executor cannot express
it. None may be written as a plan operation; open findings may not be
retargeted to a blocker or relabeled KEEP.

## Keep or reword one source

Leave a generated output unchanged for `KEEP` only after every semantic group
in it is causally owned at that position. Mechanical validation of a generated
KEEP template is not that audit.

For a message-only correction, change only `operation` to `REWORD`, `message`,
the declared `encoding` when necessary, and `rationale`. Preserve the single
source, its complete ordered unit list, and its author. The specialized
`refine-commit-messages` skill permits only `KEEP` and `REWORD` plans.

## Split one broad source

Replace one generated output with two or more outputs that all:

1. use `operation: "SPLIT"`;
2. name that same single source commit;
3. copy its exact author;
4. consume a non-empty ordered subset of its units; and
5. supply an accurate message, encoding, and semantic rationale.

The split outputs must occur in the intended replacement order. Ordinary
source-unit subsets must be disjoint and together consume every ordinary unit
from the source. If one unit spans several split outputs, mark every occurrence
RESOLVED and add its exact output indexes to `plan.partitioned_units`. Do not
use original hunk count as the semantic boundary: a unit is the smallest exact
inventory item, not necessarily a semantic atom, while every resolved output
still needs a runnable product rationale. If explicit resolution cannot safely
materialize those portions, the unit remains `UNREPRESENTABLE`, not KEEP.

Validation applies the selected units in order and rejects an accidental empty
commit, a coordinate-shifting patch that cannot replay, lost authorship, or a
final-tree mismatch. Do not uncommit the source, stage reconstructed files, or
start an interactive rebase when validation rejects a split.

## Integrate later repair units

Locate the causal owner where every repair unit first belongs before reading
placement evidence. A mixed repair source may place repair units at earlier
owners while its genuinely new outcome remains in a later residual SPLIT
output. With distinct mechanical units, assign each ordinary unit once. When
one unit contains both portions, repeat it only in the affected RESOLVED
outputs and declare those indexes in `plan.partitioned_units`. Do not move the
new outcome earlier or discard it merely to make the plan validate.

For each target output:

1. change its operation to `INTEGRATE`;
2. keep the target source first in `source_commits`, followed by each later
   repair source represented in that output;
3. keep every target unit first in `source_unit_ids`, followed by the assigned
   repair units in source order;
4. preserve the target author; and
5. update the message only when the integrated result changes its stated
   outcome.

The same repair source may appear in several integrated outputs through
disjoint ordinary units or declared partitioned occurrences. Retain a later
residual SPLIT output when that source has a genuinely new outcome. Remove its
standalone boundary only after no residual outcome remains and every ordinary
or partitioned unit is fully accounted for. Leave unrelated intervening
outputs in their semantic order. Never merge an unrelated blocker chain merely
to make validation pass.

If moving one unit from a source causes a later `EXACT` output to be rejected,
re-audit every moved unit from that source rather than changing only the
rejected output. Treat the rejection as placement evidence: it reopens
materialization, prerequisites, partitioning, and chronology, but never
overrides established causal ownership. Retain `RESOLVED` when the explicit
workspace safely materializes the intended snapshots; otherwise retain the
intended owner as an open `UNREPRESENTABLE` finding with the exact diagnostic.
Return a coherent later outcome to its natural source boundary only when
independent causal evidence changes its owner.

If a repair unit has no confident semantic target, record it as `UNRESOLVED`.
One repair unit may follow a `BLOCKED` predecessor farther back when the plan
keeps their complete blocker chain ordered inside the same output; validation
then requires exact full-plan replay to prove the compound movement and every
unit in that chain must share one causal outcome. A blocker assigned to another
output and every `UNKNOWN` edge still reject that `EXACT` plan. When the
intended owner requires a noncommuting placement, use a `RESOLVED` output and
the explicit workspace rather than merge an unrelated blocker chain. Keep the
intended owner as an open `UNREPRESENTABLE` finding with the exact diagnostic
when neither route validates; never substitute the blocker, retained history,
or manual patch application for failed validation.

## Reorder an independent source

Move a generated whole-source output earlier and mark the moved output
`REORDER`. Preserve its single source, complete ordered unit list, exact
message, encoding, and author. Outputs displaced later may remain `KEEP` when
their own patch has not been explicitly moved earlier.

For `EXACT` materialization, every moving unit must remain at or after its
recorded `earliest_position` unless it belongs to the complete same-output
`BLOCKED` predecessor chain allowed above. Every unit in that exception must
share the same causal outcome, and validation must prove the compound move by
exact full-plan replay. An ungrouped `BLOCKED` edge, a blocker assigned to
another output, or any `UNKNOWN` edge rejects the exact crossing. Use
`RESOLVED` for required noncommuting placement only when the workspace can
materialize the desired snapshots safely. Dependency records are a limit and
explanation for exact replay; resolved replay bound to the current plan and the
frozen final tree remain the oracle for explicit materialization.

Do not split and reorder the same source as a shortcut. Express the desired
unit outputs in their final positions and let validation determine whether
the complete plan is representable.

## Resolve, validate, apply, and recover

For an all-`EXACT` plan, validate after every semantic edit and immediately
before apply:

```bash
git-stage-batch rewrite validate "$REWRITE_PLAN" --porcelain
```

For any plan with `RESOLVED` outputs, first create a dedicated external
workspace. Inspect the reported request, edit only its `result.json` and result
artifacts, and advance one output:

The workspace binding ties the fresh plan to its resolved-output inventory.
Each request's `output_key` also binds its output index, planned output, and
exact `parent_tree`. Preserve those fields and the declared path/artifact
inventory in `result.json`; edit only each path's intended `state`, `mode`, and
result artifact bytes. Acceptance records the result digest, artifact digests,
and resulting `output_tree` in the receipt.

Before accepting a result, compare each authorized path's actual
`CURRENT_PARENT` with the complete `SOURCE_BEFORE` and `SOURCE_AFTER` source
transition, then audit that path through every later natural source boundary.
The parent-to-result transition may introduce only the semantic portion owned
by this output. Bytes owned by a later output or copied from the frozen final
tree are a hidden stepping stone, not resolution evidence.

For a unit repeated at several partitioned `output_indexes`, treat its requests
as one progressive transition chain. Each occurrence starts from the actual
parent left by prior outputs, must really change every authorized path, and
introduces only its owned portion. Never resolve occurrences independently from
`SOURCE_BEFORE`, publish a later occurrence's result early, or allow a no-op
occurrence. When this chronology cannot be materialized, change the output
topology or keep the causal placement `UNREPRESENTABLE`.

```bash
git-stage-batch rewrite resolve "$REWRITE_PLAN" \
  --workspace "$REWRITE_WORKSPACE" --porcelain
git-stage-batch rewrite resolve "$REWRITE_PLAN" \
  --workspace "$REWRITE_WORKSPACE" --accept --porcelain
```

Repeat the edit-and-accept step until the workspace reports `COMPLETE`.
After each `--accept` imports the current result, immediately replay every
intervening `EXACT` output before reporting the next `RESOLVED` request. If
that replay rejects, reopen materialization, prerequisites, partitioning, and
chronology for every moved unit from the affected source; do not change only
the rejected output or infer a new owner from placement failure.
Validate that exact plan-workspace pair with:

```bash
git-stage-batch rewrite validate "$REWRITE_PLAN" \
  --workspace "$REWRITE_WORKSPACE" --porcelain
```

Any plan-file byte change after workspace creation invalidates use of the
prior workspace binding. Start a fresh workspace for the exact file; never
copy completed records forward. A semantic change to either editable plan
field additionally invalidates its prior request keys, results, receipts, and
artifact assumptions. The edit does not itself invalidate immutable Git tree
or object IDs. An unchanged ID remains content-addressed evidence, but no
prior record binds it to or authorizes the edited plan. A workspace
materializes the reviewed ownership and order but never changes either
decision.

Product resolve and validation replay are the authoritative tree proof. Do
not reconstruct every candidate tree independently. Only to diagnose an
unexplained request, result, receipt, or replay-tree discrepancy, load the
request's exact `parent_tree` into a temporary Git index, apply only the
declared path/mode transitions, and write candidate blobs through a temporary
Git object store. Do not derive that diagnostic tree from a checkout or
filesystem walk; generated, cached, and untracked files can describe a
different snapshot. The diagnostic never replaces product validation.

Validation is read-only. Treat stale immutable facts, missing units,
unsupported headers, barriers, and final-tree mismatches as plan failures.
When validation rejects an intended causal assignment, preserve the branch
and the assignment as an open finding. A later fresh scan remaps it to fresh
unit IDs; it does not erase the intended owner. Another pass may follow a
rewrite that placed coherent groups at their real owners and changed the
inventory for remaining findings. Before starting that pass, name the newly
enabled `INTEGRATE`, `SPLIT`, or `REORDER` decision and its expected
output-count or operation-count delta. Fresh unit IDs or a fresh base with
unchanged ownership decisions are not progress. Never apply a non-owner
blocker landing as a stepping stone.

## Continue from a verified prefix

A completed full-series output may become a verified prefix only when its
product apply and `rewrite verify` reports and selected semantic-check receipts
are complete. Freeze the canonical base, prefix tip/tree, ordered commit/tree
vector, verification digest, command-allocation policy, and clean repository
state. Later direct appends must be a linear chain whose first parent is that
exact prefix and whose remaining parents are the preceding appended commits.

Keep continuation evidence in a new external causal ledger anchored at the
verified prefix. Record each appended source, its earlier semantic owner,
changed paths, parent/tree transition, checks, and intended next-pass
disposition. Direct append records source chronology; it does not reassign the
owner. Never reopen or edit the checkpoint that completed the prefix. A later
semantic pass requires a fresh scan and fresh unit IDs, then places each suffix
group at its recorded owner through normal validation.

Run that fresh scan from the same canonical base through the appended tip. The
suffix is new input; it is never a reason to call `rewrite continue`, which only
resumes the active operation reported by status. Do not rebuild verified-prefix
commits or recreate a completed `RESOLVED` workspace merely because the tip
grew. The prefix outputs become ordinary immutable sources in the fresh scan.
Require a demonstrated dependency or topology constraint before introducing a
new `RESOLVED` output and bind any new workspace to the fresh plan.

Reject continuation when any prefix commit or tree changed, when its
verification digest or command-allocation policy is unavailable, when an
earlier boundary's assumptions changed, or when the invalidation cone cannot
be bounded. Re-audit the affected cone, or the complete range when necessary,
before treating another result as a verified prefix.

When the authority repository uses object alternates, borrowed packs, or
another non-ordinary object store, a detached worktree is not portability
proof. Build a disposable ordinary clone with no alternates, authenticate the
same verified prefix, recreate the direct append, and reacquire every fresh
scan or resolution workspace needed for the follow-on topology. Never copy a
repository-bound plan or workspace. Require the same prefix and resulting
commit/tree sequence before authority mutation.

Each rehearsal or verification attempt uses a new immutable run root. If it
fails, preserve its exact command, environment, exit status, diagnosed cause,
transcript, receipt, diagnostics, and repository state and stop. Do not retry in
place, overwrite it, delete it, or blindly repeat the same command and
environment. Correct a documented runner defect and start a separately
identified attempt. A semantic failure reopens the plan. The failed attempt
remains negative evidence: it does not invalidate an independently
authenticated prefix, but it cannot authorize continuation or authority
mutation.

## Stabilize and recover harness execution

An execution harness that compares Git-generated object IDs or object-store
deltas across repositories must not consume automatic `%h` abbreviations.
Force `core.abbrev=40` through every direct Git invocation and every product
child's Git environment, assert the effective setting and full-length log
output before mutation, and retain full IDs in manifests and receipts. Prove
the object-producing path in two ordinary no-alternates clones with materially
different object-store cardinalities. Their pinned full-ID object graphs must
match even when an explicit negative probe shows their unpinned automatic
abbreviations differ.

A successful commit followed by a failed object, lease, test, or receipt gate
is a landed-state recovery, not permission to retry. Preserve the failed run
and do not reset, amend, or recommit its exact HEAD. A new run may adopt that
commit only after it authenticates the failed-run inventory and proves the
current HEAD, parent, tree, index, refs, reflog suffixes, hook transcript,
product operation state, and object-namespace delta are exactly the landed
state. Its new checkpoint lineage records `ADOPTED_PENDING_VERIFICATION`, runs
all checks skipped after the failure, then records `VERIFIED` before it creates
the next commit. Reject adoption when the commit boundary, content, identity,
hook result, namespace delta, or failure position is ambiguous; require manual
recovery instead.

Apply only a fully reviewed plan:

```bash
git-stage-batch rewrite apply "$REWRITE_PLAN" --porcelain
git-stage-batch rewrite verify --porcelain
```

For a plan with `RESOLVED` outputs, pass the same completed workspace through
the final validation and apply:

```bash
git-stage-batch rewrite validate "$REWRITE_PLAN" \
  --workspace "$REWRITE_WORKSPACE" --porcelain
git-stage-batch rewrite apply "$REWRITE_PLAN" \
  --workspace "$REWRITE_WORKSPACE" --porcelain
git-stage-batch rewrite verify --porcelain
```

Pass each separately verified current review-head exception from the bound
publication-scope record with a repeated full
`--allow-published-ref refs/remotes/...` option. Never pass an excluded WIP,
tag, archived review ref, target branch, or protected ref to clear the product's
broader blocker. The exception permits only local rewriting. Apply revalidates
the external workspace binding and copies it into operation-owned state before
activation. Continue and verify replay the owned copy; they do not depend on
the external workspace remaining present.

If apply is interrupted, use `rewrite status --porcelain`; continue only when
`inspection.resume_ready` is true and `inspection.resolution_matches` is null
for an all-`EXACT` operation or true for a resolved operation. A false
resolution match blocks continuation. `rewrite continue` executes the
recorded next action. `rewrite abort` restores only operation-owned ref
values. Never inspect or edit product-owned plan/state files to choose a
transition, and never use rebase, reset, amend, or a sequence editor as a
fallback.

## Verify runnable snapshots

Verify a full-series result in three tiers:

1. The object-and-plan tier audits every output's commit, parent, tree, message,
   author, encoding, operation, source-unit and path ownership, output order,
   original-source provenance, apply report, and `rewrite verify`.
2. The semantic-boundary tier runs relevant narrow build, import, or behavior
   commands at changed owners, immediate adopters or test successors, later
   natural boundaries, and justified risk-selected preserved states.
   Freeze a boundary manifest that maps each exact selected output to its risk
   and exact commands. Require every command to exist at that snapshot. Run all
   commands for one output in one clean checkout, sharing generated state only
   within that group and storing it outside the checkout.
3. The final-tip tier runs the complete normal repository test suite, message
   checks, and repository-appropriate build at the final candidate tip.

For each moved group, check the snapshot at its changed owner output, its
immediate adopter or test successor, and every later natural source or test
boundary whose API or representation the group crosses. A passing final tip
does not replace those intermediate checks.

For a verified-prefix continuation, immutable receipts may satisfy unchanged
semantic boundaries whose exact inputs and assumptions remain valid. Audit
every output in the complete combined chain through the object-and-plan tier,
rerun the semantic-boundary tier for the suffix and every boundary whose
allocation or assumptions changed, and always run the complete final-tip tier.
The boundary tier follows its exact risk manifest rather than universal
snapshot commands. The ordinary-clone rehearsal follows the same tiers and the
same scoped boundaries.

The bundled `scripts/verify-head-snapshot.py` runs one selected command in a
temporary detached worktree without owning refinement checkpoints or recovery
refs. Use it after the final plan converges. A failing semantic boundary is a
new plan problem: regroup available units with `SPLIT`, `INTEGRATE`, or
`REORDER`, then validate again. Do not add a late repair or edit history
manually.
