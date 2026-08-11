# Targeted EXACT Rewrites

Use this procedure only for one explicit transformation admitted by the
targeted fast path in `SKILL.md`. The user's exact selection supplies the
semantic boundary, order, or message decision for that target. Product apply
supplies the complete mechanical proof.

Contents:

- **Preserve the generated scope** — keep every unrelated output untouched.
- **Squash one adjacent pair** — collapse two whole sources.
- **Swap one adjacent pair** — cross only a proven adjacent boundary.
- **Inspect and apply once** — use apply's pre-state validation and verify.

## Preserve the generated scope

Start from one fresh generated plan. Require empty `plan.partitioned_units`,
retain `materialization: "EXACT"` on every output, and leave every non-target
output field-for-field unchanged. Those generated `KEEP` outputs preserve
out-of-scope sources without claiming to audit them.

Inspect the complete source commits and units named by the selected output or
pair. Exit if the fresh scan does not contain the exact selected full commit
IDs, an intended pair is not adjacent, either selected output combines several
sources, or the requested change needs only part of a source's units.

## Squash one adjacent pair

Let `EARLIER` and `LATER` be consecutive generated whole-source outputs:

1. Copy `EARLIER` and change its operation to `INTEGRATE`.
2. Set `source_commits` to the complete `EARLIER` list followed by the complete
   `LATER` list.
3. Set `source_unit_ids` to all `EARLIER` units followed by all `LATER` units,
   retaining each source's order.
4. Preserve `EARLIER`'s exact author, and supply one accurate message,
   encoding, and rationale for the combined outcome.
5. Replace the adjacent pair with that one output; do not retain a residual
   output for `LATER`.

This `INTEGRATE` expresses the user-selected boundary collapse, not a finding
that `LATER` repairs an earlier owner. When the sources have different authors
or attribution trailers, require the combined message and repository policy to
make the intended attribution unambiguous before using the fast path.

## Swap one adjacent pair

Let `EARLIER` and `LATER` be consecutive generated whole-source outputs. Place
`LATER` immediately before `EARLIER`, change only `LATER`'s operation to
`REORDER` and its rationale, and preserve its complete sources, units, message,
encoding, and author. Leave `EARLIER` as the generated `KEEP` output.

Before apply, inspect both patches and messages in their proposed context.
Require the intermediate state after `LATER` to be coherent by inspection and
both messages to remain truthful. A required `BLOCKED` or `UNKNOWN` crossing
must make apply reject the plan; never select `RESOLVED` materialization in the
targeted path.

## Inspect and apply once

Do not run a separate `rewrite validate` for this targeted path. Compare the
edited document with the generated plan before apply. Require empty partitions,
every output `EXACT`, exactly one non-`KEEP` operation, and these exact shapes:

- squash: one integrated output, zero reworded, split, reordered, or resolved
  outputs, and one fewer output commit than source commit;
- reorder: one reordered output, zero reworded, integrated, split, or resolved
  outputs, and equal source and output counts.

Run apply directly on that exact plan:

```bash
if test -n "${REVIEW_HEAD_REF:-}"; then
  git-stage-batch rewrite apply "$REWRITE_PLAN" \
    --allow-published-ref "$REVIEW_HEAD_REF" --porcelain
else
  git-stage-batch rewrite apply "$REWRITE_PLAN" --porcelain
fi
git-stage-batch rewrite status --porcelain > "$PLAN_DIR/status.json"
git-stage-batch rewrite verify --porcelain
```

Use only the exact current review-head refs authorized by the run-local
publication-scope record in `SKILL.md`. Require zero overlap with its freshly
queried provider-default and protected-branch tips. Repeat
`--allow-published-ref` only for those verified current review-head refs; never
pass an excluded WIP branch, tag, archived or closed review ref, target branch,
or protected ref to clear `published-range`. If apply cannot express the bound
scope, stop without mutation. Apply reacquires the frozen snapshot and live
safety facts, then validates schema, unit conservation, dependency crossings,
exact replay, authorship, metadata, and the final tree before it creates
operation state or a recovery ref. Its successful completion is the product's
unit and final-tree proof.

Require status to report the latest operation as `phase: "COMPLETE"`,
`active: false`, the expected `progress.planned_output_count`, and the expected
`plan.operation_counts`. After a swap, narrowly check the first output of the
pair, the moved `LATER` source. If that check fails, report the failure and
recovery ref; do not claim completion or improvise a rollback or broader
rewrite. The branch remains at the rewritten tip, and `rewrite abort` cannot
undo a `COMPLETE` operation. Ask before any separately reviewed recovery.

Exit without mutation when the plan needs partial units, partitioned
provenance, more than one non-`KEEP` operation, any `RESOLVED` output, or a
different output shape. An invalid or unsafe plan, including a squash whose
non-empty sources have a net-empty result, must fail during apply's validation
before operation creation. Use the full workflow only when it remains within
the user's request; never reconstruct the rewrite manually.
