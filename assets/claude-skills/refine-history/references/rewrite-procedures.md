# Rewrite Plan Procedures

Use these procedures only while running `refine-history`. The semantic plan is
the sole description of changed boundaries. `git-stage-batch rewrite` is the
sole mechanism for validating and applying them.

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

Edit only `plan.outputs` in a fresh `rewrite scan` document. Never edit:

- `schema_version`;
- any `snapshot` fact, including commit metadata, unit IDs, dependencies, or
  tree IDs; or
- `safety`, which is advisory and recollected by validation and apply.

Each output retains the generated fields `operation`, `source_commits`,
`unit_ids`, `message`, `encoding`, `author`, and `rationale`. Copy the complete
generated author object instead of reconstructing it. Use full object and unit
IDs. Rationale explains semantic intent but never authorizes a crossing.

Across the complete output list:

- consume every source patch unit exactly once;
- keep unit order within each source unless a proven movement changes output
  order;
- preserve every empty source explicitly unless an integration deliberately
  consumes it;
- keep every output non-empty unless the source was already empty; and
- require validation to reproduce the frozen final tree.

## Keep or reword one source

Leave a generated output unchanged for `KEEP`.

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

The split outputs must occur in the intended replacement order. Their unit
subsets must be disjoint and together consume every unit from the source. Do
not use original hunk count as the semantic boundary: a unit is the smallest
mechanical inventory item, while the output grouping still needs a runnable
product rationale.

Validation applies the selected units in order and rejects an accidental empty
commit, a coordinate-shifting patch that cannot replay, lost authorship, or a
final-tree mismatch. Do not uncommit the source, stage reconstructed files, or
start an interactive rebase when validation rejects a split.

## Integrate later repair units

Locate where every repair unit first belongs. One repair source may be divided
across several earlier targets without creating temporary repair commits.

For each target output:

1. change its operation to `INTEGRATE`;
2. keep the target source first in `source_commits`, followed by each later
   repair source represented in that output;
3. keep every target unit first in `unit_ids`, followed by the assigned repair
   units in source order;
4. preserve the target author; and
5. update the message only when the integrated result changes its stated
   outcome.

The same repair source may appear in several integrated outputs when its units
are partitioned. Remove its standalone output only after all its units are
assigned exactly once. Leave unrelated intervening outputs in their semantic
order.

If a repair unit has no confident semantic target, retain the repair commit.
One repair unit may follow a `BLOCKED` predecessor farther back when the plan
keeps their complete blocker chain ordered inside the same output; validation
then requires exact full-plan replay to prove the compound movement. A blocker
assigned to another output and every `UNKNOWN` edge still require a revised
assignment or retained history. Never substitute manual patch application for
failed validation.

## Reorder an independent source

Move a generated whole-source output earlier and mark the moved output
`REORDER`. Preserve its single source, complete ordered unit list, exact
message, encoding, and author. Outputs displaced later may remain `KEEP` when
their own patch has not been explicitly moved earlier.

Every moving unit must remain at or after its recorded `earliest_position`.
One `BLOCKED` or `UNKNOWN` edge rejects the crossing. Dependency records are a
limit and explanation; complete Git replay and the frozen final tree remain
the final oracle.

Do not split and reorder the same source as a shortcut. Express the desired
unit outputs in their final positions and let validation determine whether
the complete plan is representable.

## Validate, apply, and recover

Validate after every semantic edit and immediately before apply:

```bash
git-stage-batch rewrite validate "$REWRITE_PLAN" --porcelain
```

Validation is read-only. Treat stale immutable facts, missing units,
unsupported headers, barriers, and final-tree mismatches as plan failures.

Apply only a fully reviewed plan:

```bash
git-stage-batch rewrite apply "$REWRITE_PLAN" --porcelain
git-stage-batch rewrite verify --porcelain
```

Pass each separately verified current review-head exception from the bound
publication-scope record with a repeated full
`--allow-published-ref refs/remotes/...` option. Never pass an excluded WIP,
tag, archived review ref, target branch, or protected ref to clear the product's
broader blocker. The exception permits only local rewriting.

If apply is interrupted, use `rewrite status --porcelain`; continue only when
its live inspection is resume-ready. `rewrite continue` executes the recorded
next action. `rewrite abort` restores only operation-owned ref values. Never
inspect or edit private plan/state files to choose a transition, and never use
rebase, reset, amend, or a sequence editor as a fallback.

## Verify runnable snapshots

Product verification proves topology, metadata, patch conservation, and the
final tree. Semantic refinement must additionally choose a relevant build,
import, or narrow behavior command for every final commit snapshot.

The bundled `scripts/verify-head-snapshot.py` runs one command in a temporary
detached worktree without owning refinement checkpoints or recovery refs. Use
it after the final plan converges. A failing snapshot is a new semantic plan
problem: regroup available units with `SPLIT`, `INTEGRATE`, or `REORDER`, then
validate again. Do not add a late repair or edit history manually.
