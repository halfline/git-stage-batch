---
name: publish-unpushed-commits
description: Turn a clean local range of unpublished commits into reviewable GitHub pull requests or GitLab merge requests, choosing provider-native stacks when suitable and ordinary review requests for singletons, unavailable stack support, and forks
user-invocable: true
disable-model-invocation: true
argument-hint: "[ready|draft|audit|resume]"
when_to_use: "Use when the user asks Claude Code to publish, submit, open review requests for, or resume publishing local unpublished commits. Publish ready for review by default; use explicit draft mode for drafts and audit mode for a non-publishing plan. Never merge or change unrelated collaboration state."
allowed-tools:
  - Read
  - Grep
  - Glob
  - LS
  - Edit
  - Write
  - Agent(commit-message-drafter)
  - Bash(git *)
  - Bash(gh api *)
  - Bash(gh auth status *)
  - Bash(gh pr list *)
  - Bash(gh pr view *)
  - Bash(gh repo view)
  - Bash(gh repo view *)
  - Bash(glab api *)
  - Bash(glab auth status *)
  - Bash(glab config get *)
  - Bash(glab repo view)
  - Bash(glab repo view *)
  - Bash(git-stage-batch *)
  - Bash(pipx run git-stage-batch *)
  - Bash(python3 *)
  - Bash(mkdir *)
  - Bash(mktemp *)
  - Bash(test *)
  - Bash(uname *)
---

# Publish Unpushed Commits

Publish a curated local commit range as the smallest natural set of pull
requests or merge requests. Preserve useful commit and review boundaries,
choose the remote topology from provider capabilities, and stop after verified
publication.

Read the matching `references/github-publication.md` or
`references/gitlab-publication.md` before any push or review-request creation.
Use `scripts/publish-checkpoint.py` for every mutating run so partial
multi-branch publication can be resumed without conversation memory.

## Usage and authority

Invocation arguments: `$ARGUMENTS`

```text
/publish-unpushed-commits
/publish-unpushed-commits ready
/publish-unpushed-commits draft
/publish-unpushed-commits audit
/publish-unpushed-commits resume
```

Treat an invocation without a mode as `ready`. Ready mode creates review
requests as ready from the outset; do not create drafts and promote them as a
staging mechanism. Draft status is opt-in and applies to every new review
request in the run. Resume preserves the recorded requested status and accepts
no new status. Audit performs remote queries and produces the complete grouping,
topology, validation, branch, title, and body plan without rewriting history,
pushing branches, creating review requests, or writing a checkpoint.

A mutating invocation authorizes rebasing and refining only the demonstrably
unpublished range, creating private recovery and planning refs, pushing new
run-owned branches, creating its review requests with frozen prose, and
creating eligible native GitHub stack relationships. It does not authorize
merging, enabling auto-merge, requesting reviews, changing labels or
milestones, bypassing repository rules, editing any existing review request,
or making product changes solely to obtain passing continuous integration.

## Preserve these invariants

- Require a clean index and worktree, a named local branch, a nonempty linear
  range, no Git operation in progress, working authentication for the selected
  GitHub or GitLab host, and no active `git-stage-batch` session. Saved batches
  do not block publication.
- Read the repository instructions, contribution guide, review-request
  template, commit hook, workflows, and representative accepted reviews before
  changing history or drafting prose.
- Select one publication remote: use the explicitly supplied remote when
  present and `origin` otherwise. Fetch and inspect only that remote. Do not
  enumerate, fetch, inspect, or query other configured Git remotes merely
  because they exist. Before any rewrite, query advertised tags from the
  selected remote and from each resolved target or head repository that it
  does not represent. Reject a range commit that is a lightweight-tag target,
  the peeled target of an annotated tag, reachable from the selected remote's
  remote-tracking refs, or associated with an existing review request unless
  resuming the exact recorded run. Do not classify an unadvertised local tag as
  publication.
- Resolve the provider, host URL, target repository, head repository, push
  remote, target trunk, and fetched trunk commit independently. Never assume
  `origin` is both the fork and its upstream.
- For GitLab, pin every API call to the authenticated hostname and resolve both
  repository paths to numeric project IDs before starting recovery state. Do
  not store credentials. Reject self-managed installations served below a
  relative URL path.
- For GitHub, resolve and checkpoint the immutable numeric IDs of both
  repositories as well as their canonical owner/name paths. Re-read both
  identities before every push or remote review-request mutation so a rename,
  transfer, deletion, or path reuse fails closed.
- Create a recovery ref before the first publication-owned rewrite. Use only
  unique run-owned branch names and explicit force-with-lease expectations.
  Bind the sole configured push URL to an API-observed clone URL for the head
  repository, checkpoint credential-safe digests, and reject an unverifiable,
  remapped, or ambiguous remote on resume. Never force-push the trunk, adopt an
  existing remote branch on a fresh run, or push an unrecorded branch.
- Preserve the aggregate intended change. Reorder groups only when patches and
  prerequisites prove independence. Preserve authors and compare rewritten
  series with recovery refs using `git --no-optional-locks range-diff`.
- Keep every review-request snapshot reviewable and runnable to the degree
  required by the repository. A higher review request must not be the only
  place that repairs an unintentionally broken lower snapshot.
- Draft every exact title and body template before publication. A higher layer
  contains exactly one frozen placeholder for its preceding review request;
  render that URL before creation. Ready mode creates ready review requests
  with that final prose, avoiding both later body edits and a draft-to-ready
  notification cycle.
- Use native GitHub stacks only for eligible dependent multi-pull-request
  groups in one repository. GitLab recognizes eligible same-project branch
  chains as stacked merge requests. Use ordinary review requests for
  singletons, unavailable stack support, and forks.
- Treat every push and remote mutation as non-atomic. Record and inspect partial
  results before retrying. Never delete remote recovery evidence automatically.

Stop on ambiguous range ownership, provider or authentication failure, changed
remote branches, existing review requests not owned by the checkpoint, unsafe
history rewrites, conflicts that disprove the grouping, missing local validation,
partial publication whose exact result cannot be established, or any need for
new product work. Report the recovery ref and run directory.

## Git command concurrency

Prefix every read-only Git command with `git --no-optional-locks`, including
commands run while executing either refinement contract in process. This
applies to repository inspection such as `status`, `diff`, `log`, `show`,
`branch`, `remote`, `rev-parse`, `for-each-ref`, `ls-remote`, `range-diff`, and
`apply --check`. It prevents optional index-stat refreshes from contending for
`.git/index.lock` when independent reads run concurrently.

The publication helper and product rewrite executor already apply this option
to their Git subprocesses. Do not infer that it makes mutations safe to
overlap: run fetches, history rewrites, branch or ref updates, commits, and
pushes serially in their documented order.

## Progress discipline

Use the compact audit table as the progress record for sections 1 and 2. Once
section 3 starts the checkpoint, run `python3 "$PUBLISH_HELPER" status --json`
at the end of every numbered section and before continuing after compaction or
interruption. The expected phases after sections 3 through 6 are respectively
`started` with a normalized result, `planned`, `validated`, and `published`.
Resolve any mismatch before continuing; do not create a parallel progress file
or rely on a conversational checklist instead of the checkpoint.

## 1. Resolve repositories and freeze the range

Move to the repository root and inspect local state. Before first use, read
`git-stage-batch --help`. For GitHub, read `gh api --help`, `gh repo view
--help`, `gh pr list --help`, and `gh pr view --help`. For GitLab, read `glab
api --help`, `glab repo view --help`, and `glab config get --help`, plus `glab
auth status --help` when using its optional diagnostics.

For a fresh run, use an explicitly supplied target repository, push remote,
trunk, or base when present. Otherwise:

1. Use an explicitly supplied push remote when present; otherwise require and
   use `origin`. Do not infer a different remote from branch configuration.
2. Identify its host and provider. Use `gh repo view` for GitHub and
   `glab repo view` for GitLab. If both or neither CLI recognizes a self-hosted
   remote, require an explicit provider instead of guessing.
3. When the head repository is a fork, default the target repository to its
   provider-reported parent. Otherwise target the head repository.
4. Resolve the target repository's default branch as the trunk.
5. Fetch only the selected publication remote. If it does not represent the
   target repository, fetch the target trunk by URL into a uniquely named
   private ref without adding or changing Git configuration. Ignore every
   other configured remote.

For GitLab, derive the hostname from the root host URL and use a host-pinned
`glab api --hostname HOST user` query as the authoritative authentication
check. `glab auth status` is optional diagnostics because an environment-only
token need not appear as stored login state. Authentication comes from
`glab`'s credential store or its documented token environment variables;
never inspect or persist the token itself. Resolve and verify the numeric
target and head project IDs, their canonical project URLs, and the configured
API host before starting recovery state. Read
`references/gitlab-publication.md` for the exact preflight.

For GitHub, likewise derive the exact root hostname, pass it to every `gh api`
call with `--hostname`, and use fully qualified `[HOST/]OWNER/REPO` arguments
for other `gh` commands. Use `gh api --hostname HOST user` as the authentication
check and verify the target and head repository identities before recovery state.
Authentication may come from the exact host's `gh` credential store or its
documented token environment variables; never inspect or persist the token.
Record each repository's positive numeric API `id`, canonical `full_name`,
canonical URL, and provider-returned HTTPS and SSH clone URLs. Require the
configured push URL to match one of the head repository's observed clone URLs;
fail closed on SSH aliases or other forms that cannot be matched exactly.
Read `references/github-publication.md` for the exact preflight.

Record `HEAD`, provider, host URL, the target trunk commit, their merge base,
the current branch, the selected remote URL, repository owners, and every
commit in merge-base-to-`HEAD` order. Reject a detached head, merge commits in
the range, an empty range, dirty state, or any range commit found through the
selected remote's tracking refs or the provider's review-request queries.

Before invoking either refinement skill, build a tag-query source set covering
the selected publication remote plus the resolved target and head repositories.
Use the selected remote name when it represents that repository and a
provider-reported clone URL otherwise; deduplicate sources by canonical
provider repository identity. Never enumerate other configured Git remotes or
their remote-tracking refs. Run this command for every source and require every
query to succeed:

```bash
git --no-optional-locks ls-remote --tags SOURCE
```

Do not add `--refs`, because that would omit the `^{}` records that peel
annotated tags.
Compare the complete range with both direct tag object identifiers and peeled
identifiers; any matching commit has already been published. Perform this
query again if the range boundary changes before checkpoint start.

When installed, run `git-stage-batch status`; block only an active session.
`git-stage-batch list` may report durable saved batches without blocking this
workflow because publication does not reinterpret them.

## 2. Audit outcomes and dependencies

Inspect each commit and patch once. Build one compact table with original
position, commit identifier, plain-language outcome, prerequisite, proposed
group, proposed review request, and boundary reason. Reuse the table for
history, branch, title, body, and completion work.

Choose boundaries by outcome:

- Keep one cohesive independently landable outcome in one review request.
- Put a prerequisite below its consumers in one dependent group.
- Put unrelated independently landable work in separate groups rooted at the
  target trunk; adjacency does not create a dependency.
- Keep behavior with the tests, fixtures, documentation, generated output, and
  exact checker policy needed to review that behavior.
- Keep groundwork separate when it is independently understandable and its
  adopter will be a later review request in the same publication group.
- Do not split by directory, file type, commit count, or equal size.
- Do not manufacture multiple review requests for a cohesive singleton.

Do not change commit boundaries solely to achieve a preferred review-request
count. If one commit mixes outcomes that must publish independently, invoke
`/refine-history BASE_SHA` before starting the publication checkpoint. Rebuild
the compact audit after that rewrite. If a safe split is not justified, keep
the commit in one review request.

When this workflow says to invoke an installed refinement skill, use the
runtime's nested skill mechanism when it is available. If the runtime cannot
recursively invoke a user-only skill, locate and read that skill's complete
`SKILL.md`, then execute its contract in-process with the explicit base and
mode. Apply the same fallback recursively when `refine-history` hands work to
`refine-commit-messages`. Do not replace either contract with an abridged copy
or ask the user to restart the publication workflow.

Both refinement dependencies are installed with this skill, and the
publisher's tool surface covers their contracts. The full-contract fallback
preserves their checkpoints and validation without duplicating a weaker
refinement procedure here.

In audit mode, complete the remaining planning and validation analysis without
executing its mutations, then report the proposed topology and stop.

## 3. Start recovery state and normalize history

After fetching and any required boundary refinement, start the checkpoint. The
explicit base is the frozen merge base; the target base is the fetched target
trunk commit:

```bash
PUBLISH_HELPER="${CLAUDE_SKILL_DIR}/scripts/publish-checkpoint.py"
PROVIDER_ID_ARGS=()
if [[ "$PROVIDER" == github ]]; then
  PROVIDER_ID_ARGS=(
    --target-repository-id "$TARGET_REPOSITORY_ID"
    --head-repository-id "$HEAD_REPOSITORY_ID"
  )
else
  PROVIDER_ID_ARGS=(
    --target-project-id "$TARGET_PROJECT_ID"
    --head-project-id "$HEAD_PROJECT_ID"
  )
fi
python3 "$PUBLISH_HELPER" start \
  --base "$FORK_POINT" \
  --target-base "$TARGET_BASE_SHA" \
  --provider "$PROVIDER" \
  --host-url "$HOST_URL" \
  --target-repository "$TARGET_REPOSITORY" \
  --head-repository "$HEAD_REPOSITORY" \
  "${PROVIDER_ID_ARGS[@]}" \
  --remote "$PUSH_REMOTE" \
  --head-push-url "$HEAD_HTTPS_CLONE_URL" \
  --head-push-url "$HEAD_SSH_CLONE_URL" \
  --trunk "$TRUNK_BRANCH" \
  --status "$REQUESTED_STATUS"
RUN_DIR=$(python3 "$PUBLISH_HELPER" run-dir)
```

The helper rejects remotely reachable history, creates a recovery ref, and
preserves prior completed runs. It requires exactly one push URL for the named
remote and proves that URL matches an API-observed head-repository clone URL.
It records only credential-safe SHA-256 destination identities. Recognized
credential rotation therefore does not invalidate a resume, but an account,
host, port, path, or non-credential query remap does. Do not manually replace
its active pointer or checkpoint.

Create a unique private planning branch at the recorded source head. Rebase it
onto the exact fetched target base before grouping. Resolve only conflicts whose
result is established by the existing patches and tests; otherwise stop. Check
the rewrite with `git --no-optional-locks range-diff` and compare the aggregate
diff semantically.

Create one cumulative branch at each proposed review-request tip. Each
dependent group starts at the target base, and each higher branch contains the
preceding branch. Independent groups each start at the target base. Use an
integration branch to compose independent group tips in intended landing order;
require its tree to represent the normalized aggregate result.

Refine messages bottom-up within each review request by invoking
`/refine-commit-messages DIRECT_PARENT_TIP`. Cascade changed identifiers into
descendant branches, then recheck all anchors and the integration result. Treat
each review request as its own message series: a final commit concludes its
review request, while a real dependency names the preceding pull request or
merge request in the stack or series in plain long-form prose. Do not use
compressed labels such as "preceding stack layer."

After every rewrite and branch tip stabilizes, freeze the integration result:

```bash
python3 "$PUBLISH_HELPER" record-normalized --tip "$INTEGRATION_TIP"
```

## 4. Choose publication topology and draft prose

Read the selected provider reference now. For GitHub, probe stack support before
any push and select `github-stack` only for an eligible same-repository group of
two to one hundred dependent pull requests. For GitLab, select `gitlab-stack`
only for an eligible same-project group of two to ten dependent merge requests;
GitLab recognizes the direct-base chain without a separate stack mutation.
Select `ordinary` for every other group. Unavailable native support, a
singleton, an oversized group, or a fork is a supported ordinary path.
Authentication, provider mismatch, or transient API failure is ambiguous and
must stop instead of silently changing topology.

For a provider-native stack, plan the bottom review request against the trunk
and each higher review request against the branch immediately below it. For
ordinary publication, including same-repository fallbacks and cross-fork
groups, plan every cumulative review request against the target trunk and
explain the temporary overlapping diff in its body. This distinction prevents
an oversized GitLab group from being inferred as a native stack despite its
`ordinary` plan.

Derive each title and body from that review request's direct-base diff. Follow
the repository template and recent accepted style. Apply the repository's
complete commit-message guidance at review-request scope rather than copying
commit bodies. Write for a drive-by reviewer who does not know the local group
names.

Every body must explain the selected state, concrete limitation or motivation,
the review request's outcome, validation, and any prerequisite. Name a
dependency as `the preceding pull request in the stack` or `the preceding merge
request in the stack` for a provider stack, and use `the preceding pull request
in the series` or `the preceding merge request in the series` for ordinary
publication. The bottom body contains no relationship placeholder. Every
higher body contains exactly one literal `{{PRECEDING_REVIEW_URL}}` where the
preceding review request's canonical URL belongs. A directed link to the
preceding review request is sufficient; do not promise a successor or plan a
post-creation edit. Store exact body templates under the active run directory
before recording the plan.

Write `plan-input.json` with this shape and record it:

```json
{
  "schema": 1,
  "base": "FULL_TARGET_BASE_SHA",
  "integration_tip": "FULL_INTEGRATION_TIP_SHA",
  "groups": [
    {
      "id": "cohesive-group",
      "transport": "ordinary",
      "layers": [
        {
          "id": "reviewable-outcome",
          "branch": "publish/reviewable-outcome-RUN_ID",
          "base_branch": "main",
          "tip": "FULL_LAYER_TIP_SHA",
          "title": "Exact review-request title",
          "body_file": "bodies/reviewable-outcome.md"
        }
      ]
    }
  ]
}
```

```bash
python3 "$PUBLISH_HELPER" record-plan --file "$RUN_DIR/plan-input.json"
```

The helper is authoritative for mechanical plan validity. It rejects an empty
or net-zero normalized result, an empty incremental layer, a net-zero group, a
target-trunk publication branch, an invalid body placeholder layout, a provider
stack outside its size bounds, and any set of group outcomes that does not
compose cleanly and exactly to the frozen integration tree. This summary
explains the aggregate-coverage gate; it is not a replacement for semantic
patch review.

## 5. Validate every planned snapshot

Validate each layer tip against its direct base before pushing:

1. Re-read its commit messages, exact diff, title, and body.
2. Run the repository's complete local continuous integration gate at that tip.
3. Confirm that no higher layer is required to repair an unintended failure.
4. Re-fetch the target trunk. If it moved, rebuild from the checkpoint's
   recovery state and rerun every affected check. Before recording the rebuilt
   plan, replace the pre-validation target and aggregate result explicitly:

   ```bash
   python3 "$PUBLISH_HELPER" refresh-normalized \
     --target-base "$NEW_TARGET_BASE_SHA" \
     --tip "$NEW_INTEGRATION_TIP_SHA"
   ```

   Rewrite any body whose direct-base diff changed, then record the complete
   replacement plan. The helper returns the run to `started` and does not
   permit this transition after validation freezes.
5. Query remote branches and all review-request states again. Require every
   branch to be absent on a fresh run. On resume, require exact checkpoint
   ownership.
6. Verify the integration tree and aggregate diff against the normalized result.

Record successful completion of the entire local gate:

```bash
python3 "$PUBLISH_HELPER" advance --phase validated
```

Do not publish a subset whose planned lower or higher snapshots failed. If
passing requires new product work, stop and ask for that separate authority.

## 6. Publish and verify

Advance immediately before the first push:

```bash
python3 "$PUBLISH_HELPER" advance --phase publishing
```

Follow the selected provider reference. Push every run-owned branch with an
explicit lease, running `prepare-push` before each remote side effect and
`confirm-push` after fetching its exact result. Create ordinary or stack-member
review requests bottom-to-top with their exact final prose and requested
status. Before creating a higher layer, record the preceding layer so the
helper can render its frozen URL placeholder into an immutable body file.
GitLab uses helper-generated JSON with host-pinned `glab api` calls, not
`glab mr create` or `glab mr update`. After creating every member of a native
GitHub stack, use `github-stack-request` and the direct GitHub stack-creation
REST endpoint to create or adopt exactly the planned stack. A GitLab stack
needs no separate mutation; verify its direct target-branch chain.

Immediately after each creation call, query the target repository, verify the
new review request's initial identity, exact head and base objects, title,
body, open state, and requested draft state, then checkpoint the immutable
provider-assigned number and URL before creating the next layer or a GitHub
stack relationship:

```bash
python3 "$PUBLISH_HELPER" record-created-review \
  --layer "$LAYER_ID" \
  --number "$REVIEW_NUMBER" \
  --url "$REVIEW_URL" \
  --head "$VERIFIED_HEAD_SHA" \
  --base "$VERIFIED_BASE_BRANCH" \
  --base-head "$VERIFIED_BASE_HEAD_SHA" \
  --status "$REQUESTED_STATUS"
```

The checkpoint binds every later operation to that observed review request.
Never accept a caller-supplied review number as a mutation target. Do not edit a
created title or body: providers do not offer a portable conditional update for
that prose, so any mismatch or collaborator drift must stop the run rather than
be overwritten. After creating or adopting a native GitHub stack, verify its
server-side stack object rather than relying on command output or local
metadata. For GitLab, verify the merge-request chain through the host-pinned
API.

After all creation and stack operations, query every review request again and
verify its open state, head repository, head owner, branch, exact commit,
direct base, title, rendered body, requested draft state, and unchanged
provider-assigned identity. Normalize only a terminal newline when a provider
CLI does not preserve it. Preserve reviewers, labels, assignees, milestones,
and collaborator edits. Record only that final verified response:

```bash
python3 "$PUBLISH_HELPER" record-review \
  --layer "$LAYER_ID" \
  --number "$REVIEW_NUMBER" \
  --url "$REVIEW_URL" \
  --head "$VERIFIED_HEAD_SHA" \
  --base "$VERIFIED_BASE_BRANCH" \
  --base-head "$VERIFIED_BASE_HEAD_SHA" \
  --status "$REQUESTED_STATUS"
```

For a native GitHub stack member, append `--stack-number "$STACK_NUMBER"`. Omit
that argument for GitLab and ordinary review requests. Once every planned
review request is recorded, finish the checkpoint:

```bash
python3 "$PUBLISH_HELPER" finish
```

Query checks once for the report. An absent suite is not success. Wait and
monitor only when the user explicitly requested it; publication itself does not
include merging or check-waiting.

## Resume without duplication

For literal `resume`, locate the installed helper and run:

```bash
PUBLISH_HELPER="${CLAUDE_SKILL_DIR}/scripts/publish-checkpoint.py"
python3 "$PUBLISH_HELPER" status --json
RUN_DIR=$(python3 "$PUBLISH_HELPER" run-dir)
```

Do not start a new run. Strictly inspect the checkpoint, plan, recovery ref,
local branches, fetched remote branches, all target-repository review requests,
created review identities, rendered body files, and any provider stack object.
Trust verified provider state over conversational memory, but never absorb
unrecorded remote state merely because names resemble the plan.

Continue from the first incomplete phase. A branch or review request created by
the run but not yet recorded is partial publication evidence: verify it against
the plan and record it rather than recreating it. For a prepared push, compare
the remote with both the expected old object and planned tip. Materialize a
higher layer's body only after its preceding review is recorded, then verify the
exact rendered prose before creation. For a GitHub stack, reclassify the exact
server state as create, adopt, or conflict before mutation. Stop on any third
push state, changed heads, bases, readiness, prose, closed unmerged review
requests, unknown stack members, or collaborator edits requiring a policy
choice.

## Completion

Complete only when the helper reports `published` and every planned review
request is open, points to its verified commit and base, contains the exact
audited prose and relationship links, has the requested ready or draft state,
and has the verified provider stack relationship when applicable. Do not merge.

Report the provider and host; grouping and dependency rationale; selected
transport for each group; every branch, review request number, URL, title, base,
head, and readiness; local validation and currently registered remote checks;
relationship prose; recovery ref; run directory; planning and anchor branches;
and any partial or manual follow-up state.
