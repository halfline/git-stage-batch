# AI Assistant Configuration

Configure AI coding assistants to use git-stage-batch for creating atomic, well-structured commits.

## Why Use git-stage-batch with AI?

AI coding assistants often make multiple changes across many files. git-stage-batch allows them to:

- ✅ Create atomic commits (one logical change per commit)
- ✅ Separate orthogonal changes that ended up together
- ✅ Build a clean, reviewable git history
- ✅ Stage changes incrementally with fine-grained control

## Install for your user

Use `--global` to make the bundled skills available across repositories:

```bash
git-stage-batch install-assets --global claude-skills
git-stage-batch install-assets --global codex-skills
```

Claude assets go under `~/.claude/skills` and `~/.claude/agents`; Codex
skills go under `~/.agents/skills`. No Git repository is required. Global
installation does not create or change Codex configuration or sandbox
settings, including with `--force`. Omit `--global` for a repository install.

Both installation modes include a portable default contribution guide under
`.claude/internal/CONTRIBUTING.md` or `.agents/internal/CONTRIBUTING.md`.
The skills use it only when the target project has no `CONTRIBUTING.md` in
its tree. Project instructions and hooks still take precedence. The default
preserves the bundled commit-writing conventions without assuming this
project's language, build tools, or release process. It is an internal
resource, so installing assets does not write a project contribution guide.

## Claude Code

If `git-stage-batch` is available in your environment, you can install the
bundled Claude commit skills into the current repository:

```bash
git-stage-batch install-assets claude-skills --filter 'commit-*'
```

Omit `--filter` to install all bundled Claude skills.
Use `--force` to replace an existing repo-local copy.

For a new environment, a minimal Claude setup looks like:

```bash
python -m pipx install git-stage-batch
git-stage-batch install-assets claude-skills --filter 'commit-*'
git-stage-batch block-file --local-only .claude/
claude "/commit-unstaged-changes"
```

The `block-file --local-only` line is useful when the Claude assets should
remain local. It keeps `.claude/` out of future reviews without adding it to
the project `.gitignore`.

The bundled Claude skills currently include:

- `commit-staged-changes` for turning the current staged index into one commit
- `commit-unstaged-changes` for splitting unstaged work into one or more commits
- `decompose-and-commit-unstaged-changes` for peeling larger unstaged work into
  concern batches and rebuilding a fine-grained commit series
- `publish-unpushed-commits` for turning a clean unpublished commit range into
  ready-for-review GitHub pull requests or GitLab merge requests, including
  provider-native same-repository stacks and cross-fork publication
- `refine-commit-messages` for auditing and rewording messages in an existing
  series without changing any patch or commit boundary
- `refine-history` for splitting, rewording, and integrating commits in an
  existing local draft series after an optional base or its tracked upstream

The regular commit skills stage the current working tree into new commits.
The decomposition skill hands its rebuilt series to `refine-history` for the
final boundary rewrite, and `refine-history` delegates its final prose pass to
`refine-commit-messages`. You can invoke the refinement and publication
workflows directly:

```bash
claude "/refine-commit-messages BASE_SHA"
claude "/refine-commit-messages audit BASE_SHA"
claude "/refine-commit-messages resume"
claude "/refine-history"
claude "/refine-history BASE_SHA"
claude "/refine-history audit"
claude "/refine-history audit BASE_SHA"
claude "/refine-history resume"
claude "/publish-unpushed-commits"
claude "/publish-unpushed-commits draft"
claude "/publish-unpushed-commits audit"
claude "/publish-unpushed-commits resume"
```

The default forms of both refinement skills require a clean, non-empty, linear
unpublished range or an explicitly verified force-push review head, and refuse
protected or ambiguously published history. The review-head exception permits
only a local rewrite and neither performs nor authorizes a push. Message
refinement requires an explicit base; history refinement can use the current
branch's tracked upstream. Message refinement rewords by default and proves
every patch, boundary, tree, author, committer, and series position is
unchanged. Rewritten cryptographic signatures are removed and reported because
they cannot remain valid. Explicit audit forms may inspect blocked history
because scan and validation do not update refs or commits. The `resume` forms
use the product rewrite checkpoint and its exact next action.

For one unambiguous adjacent two-commit squash, adjacent two-commit swap, or
complete message replacement, `refine-history` can use a targeted exact mode.
It still scans, validates, checks live mutation safety, applies, and verifies
the selected transformation, but it does not claim to have audited the
untouched series. Broader cleanup requests use the full semantic audit.

Publication is ready for review by default. The explicit `draft` form keeps all
new review requests in draft status, `audit` plans without publishing, and
`resume` continues the strict publication checkpoint. The skill uses GitHub's
native stack relationship or GitLab's recognized target-branch chain for
eligible dependent branches in one repository. It uses ordinary review
requests for a singleton, when native stacks are unavailable, and when head
branches live in a fork. It stops after verified publication and never merges.

For eligible groups, the skill probes GitHub's Stacked Pull Requests REST API
and creates the relationship directly after the pull requests exist. It falls
back to ordinary pull requests when that API is unavailable; no GitHub CLI
extension is required. See GitHub's
[Stacked Pull Requests API documentation](https://docs.github.com/en/pull-requests/reference/stacked-pull-requests-rest-and-graphql-apis)
for repository availability and endpoint details.

GitLab recognizes same-project stacked merge requests from their target-branch
chain on GitLab 19.1 and newer. Publication creates and verifies that chain with
host-pinned `glab api` calls rather than adopting `glab stack`, whose
experimental workflow also owns commit and branch construction. GitLab
publication requires an authenticated `glab` CLI for the selected root-hosted
GitLab URL and records numeric source and target project identities. See the
[GitLab stacked merge request documentation](https://docs.gitlab.com/user/project/merge_requests/reviews/stacked_merge_requests/)
for the server-side relationship and limits.

Selecting the decomposition or publication skill installs both refinement
dependencies, and selecting `refine-history` installs
`refine-commit-messages`:

```bash
git-stage-batch install-assets claude-skills \
  --filter decompose-and-commit-unstaged-changes
git-stage-batch install-assets claude-skills \
  --filter publish-unpushed-commits
```

Create or update `CLAUDE.md` in your repository root:

````markdown
## Commit Workflow

**Commits** should be atomic and well-structured. Each commit should represent one
logical step in the project's development. The goal is not to preserve the exact
twists and turns of drafting, but to produce a commit history that tells a clear
story of progress.

To aid in this endeavor, use the `git-stage-batch` tool. It provides
functionality similar to `git add -p` in a multi-command flow more suitable for
automation.

### Staging Process

1. Run `git-stage-batch start` to begin the process
2. For each presented patch hunk, run either:
   - `git-stage-batch include` (stage this hunk)
   - `git-stage-batch skip` (skip this hunk for now)

   These commands automatically display the next hunk to evaluate. Use
   `--no-auto-advance` when the automation should stop after one action and
   run `git-stage-batch show` explicitly before the next bare action.

3. Repeatedly run these commands until all hunks relevant to the selected commit
   are processed, then commit the results.

4. Run `git-stage-batch again` to run through all previously skipped
   and unprocessed hunks for the next commit.

5. Repeat until all commits are in place.

### Fine-Grained Line Selection

If a hunk is too coarse and contains multiple orthogonal changes, individual lines
may be included or skipped using:
- `git-stage-batch include --line 1,3,5-7` (stage specific lines)
- `git-stage-batch skip --line 2,4` (skip specific lines)

Line IDs are shown in the hunk output as `[#N]` markers.

### Commit Messages

Commit messages should aid competent developers who do not know this part of
the codebase. Explain local roles, shared context, and unfamiliar terms where
they first matter; later messages
may rely on that explanation. Keep useful repetition, but make it a shorter
reminder rather than repeating the detail. Add new detail where it becomes
relevant. A standalone commit still needs its own context.

The first body sentence is a longer, non-imperative version of the summary
line. State the same change in plain language as an indicative, declarative
sentence, with enough detail to make the outcome clear. Keep that opening
before the background and reasoning.
Use "This commit ..." to attribute the change clearly. Explain the relevant
old behavior and the correction afterward. Keep narrative present tense and
declarative body prose.

Use straight quotes and apostrophes in commit message prose. Avoid em
dashes. Use commas, parentheses, or separate sentences where needed.

In prose, prefer periods to semicolons.

After the opening, explain the decision as you would to another
maintainer who has not investigated the problem. Use ordinary verbs and
familiar terms, with contractions when they fit. Let the reader follow
the thought: a reasonable assumption, the catch, and the consequence that
makes the change worth doing. Include those turns when the reasoning
needs them; a direct fix may need only its direct reason.

Match the firmness of each claim to its basis. State a demonstrated
failure, unnecessary restriction, or supported cost plainly. Preserve
uncertainty about people's behavior, predictions, and design judgments
where the evidence leaves room. A message can explain the code confidently
and qualify an inference about its users in the next sentence.

State established behavior directly, with the condition that matters.
Use "can" or "may" for meaningful capability, contingency, or uncertainty,
not automatic precision. Check changed implications against the evidence.

Write decisions from inside the project. When relevant context explains
who made an earlier choice and why, retain that ownership and reasoning.
"We" can describe a shared decision or need; a component name is often
clearer for an operation. First person does not establish personal
experience or project history that the evidence does not supply.

Caller-supplied motivation, observations, assessments, and relevant history
can explain the change even when absent from the diff. Carry their meaning
and degree of certainty through the drafting handoff. In the message,
combine clarifications that explain the same fact. Include history when
it helps explain the present decision. Keep provenance and
verification limits outside the commit message. Report contradictions
with source there and avoid relying on a contradicted account as fact.
Never claim independent verification merely because context was supplied.

Supplied context establishes meaning, not a list of points to cover. Treat
a wording clarification as a constraint on the relevant claim. Do not turn
it into a separate explanation of a hypothetical misunderstanding.

Before drafting a behavior fix, explain the old operation to a developer
who knows programming but not the subsystem's domain: what is being
processed, what result is intended, and what policy relates them. Trace
the condition that exposes the defect through the intermediate effect to
the wrong result. Use prior source and supplied context to fill gaps in
that account before composing the message.

Draft from that account rather than from the sequence of edits. A reader
may understand every technical term and still not know why the bad case
arises. Establish both the relationship that creates that case and the
old code's incorrect handling of it. Use the changed code to verify the
correction and its limits. This is a drafting aid, not a required
paragraph structure.

Once the opening states the correction, the rest of the message may
explain just the old behavior and why it is wrong. Return to the fix when
the reader still needs a design choice, a non-obvious mechanism, or a
meaningful limit explained. A separate solution paragraph is not required.
Give a judgment or a turn in the argument its own sentence or paragraph
when that helps it land. Keep a brief reminder when it connects the next
step. Once the reason, decision, and necessary scope are clear, stop.

The summary should make the difference from the status quo apparent, rather
than merely name a desirable end state. Choose wording from the actual
before-and-after behavior. For example, `Align statistics with the visible
sensor crop` implies that the measured and displayed regions differ;
`Process only the selected files` implies that unselected files are currently
included. These illustrate how meaning supplies the contrast, not a fixed
vocabulary or a list of required verbs. Choose the words that best fit the
specific patch. Use a qualifier such as `only` when the restriction is real;
do not imply exact exclusion if alignment or rounding can include extra data.

The expanded opening should state that same concrete action in ordinary
words, without cataloguing internal operations or unchanged preconditions.

Select technical details for the understanding they give the reader.
For a behavior fix, describe what changes for the affected input or
operation. Establish the existing relationships and policy that explain
when the result is wrong, even when that context is unchanged by the
patch. Explain the correction at that level before introducing formulas,
fields, or helper calls. Those mechanics belong when they explain a
design choice or meaningful limit, rather than merely demonstrate how
the patch implements the correction.

A new internal API can be the outcome of a preparatory patch; explain
its purpose without cataloguing all its capabilities. When remaining
work matters to the explanation, say what still needs to be done. Use
active verbs for those actions instead of listing missing components or
call sites.

For tone, given repeated reads of an unchanged file, "We read the same
file for every row. Once is enough" explains the judgment more naturally
than "Repeated file reads represent avoidable overhead". When a field is
not yet read or forwarded, "We still need to read the field and pass its
value to the worker" puts the remaining actions in active verbs. "The reader
and value forwarding still need to be added" leaves them in nouns and
passive prose. When work remains, use a person or component as the subject
and say what they still need to do. These illustrate ordinary explanation,
not phrases to copy or a requirement to use first person.

Keep verification notes outside the commit message. A condition belongs
in the message when leaving it out would obscure the reasoning or
materially misstate the claim. The body need not demonstrate every fact
checked during inspection. Scope the claim accurately and give its
explanation room.

Make each necessary scope limit qualify the claim it belongs to when
that reads plainly. Once a clarification is expressed in that claim,
remove a sentence that only restates the same distinction. Keep a separate
limit when it explains a stopping point or another consequence. Do not
append an inventory of operations or unchanged behavior simply to account
for the patch.

Before accepting the message, read it on its own as an explanation to
someone who has not inspected the diff. Consider the question each
paragraph answers at that point in the explanation. Keep the details
that answer that question; move answers to separate questions raised
only by inspecting the implementation into review notes outside the message.

Revise in both directions: restore an intermediate step the reader
needs to understand the consequence, and remove facts whose main purpose
is to demonstrate thorough inspection. A feature's presence in the patch
supports its accuracy, but does not by itself explain why the reader
needs it. Keep the maintainer's supported judgment and the connection
between ideas when shortening the message.

After drafting or rewording, verify the meaning of the final sentences
against the selected patch, relevant prior code, and supplied context.
Check the conditions, timing and effects the message states or implies.
A simpler phrase can introduce a new claim; an operation's name does not establish
its effect. Inspect the relevant implementation when that distinction
matters. Narrow or remove unsupported implications and report any
remaining uncertainty outside the message. Correct contradictions in the
affected claim, then remove any caveat or reassurance that only repeats
its corrected meaning. Repeat the accuracy check after further edits.
Keep code evidence, supplied context, and inference distinct in review
notes.

Before using an assistant-written message, edit it into an alternative and
compare clarity, naturalness and reasoning. Keep the best version so far.
Make at most three comparisons, including the first, and stop after two
consecutive comparisons offer no clear improvement. Extra comparisons do
not require accepting an edit. Fix factual errors and required conventions
regardless of that prose limit. Keep these review notes out of the commit.

**Format:**
- **First line**: a concise summary with a lowercase prefix (`module:`, `cli:`, etc.)
- **Opening**: make the changed behavior clear before the reader needs the code
- **Relevant state**: establish what the program has or provides when needed
  - If recent work established that state, briefly recount the change in
    past tense and loosely when it happened
  - Add a temporal cue only where it clarifies time-dependent behavior;
    timeless background may already be clear from the project or component
  - Let "This commit ..." mark the transition
  - Use "already" selectively for a capability the patch builds on
  - Match the state after the previous commit without an unrelated recap
  - Combine the state with the reason or change when that avoids repetition
- **Reason**: explain the actual reason at the smallest useful scope
  - Explain internal concerns from inside the project, with supported judgments
  - Use the user's perspective for external concerns
  - Internal simplification or clarification of a contract can be sufficient
  - Ground assessments in concrete consequences
- **Change**: describe precisely what this commit changes
  - Be precise about scope (if it only improves one aspect, say so)
  - Prefer "This commit ..." wherever the explanation turns to the change
  - Verify that the patch makes behavior run before claiming it begins
  - A new helper or representation is a valid outcome before callers adopt it
- **Series context**: retain a prerequisite, design choice, consumer contract,
  or scope deliberately left unfinished when it explains the current patch
  - Identify the particular fact about this patch that each reference explains
  - Remove references that only say what happens elsewhere in the series
  - Describe a useful relationship wherever it belongs, without repetition
  - Verify references against actual patches and describe future work as future
  - Being first or last creates no introduction or conclusion duty
  - Omit unrelated recaps, next-item announcements, and ceremonial conclusions

A representation change can explain how it enables a later consumer
simplification. An unused-argument cleanup should not announce an unrelated
GPU-copy fix merely because that patch comes next.

**Key rules:**
- Use imperative voice only in the summary. Every body sentence is an
  indicative, declarative statement; never instruct the reader.
- Keep narrative present tense about the selected state ("has", not "used to
  have") and the change ("This commit removes ...", not "Remove ...").
- Prefer concrete subsystem prefixes when prefixes are free-form; generic
  `fix:` and `refactor:` labels usually add little signal. Follow Conventional
  Commits or another declared type-based format when the repository requires it.
- Distinguish caller-supplied motivation, observations, assessments, and
  relevant history from independently verified source facts. Preserve their
  degree of certainty and relevant project decisions through the handoff. Useful supplied context need not
  occur in the diff; keep it when it explains how an otherwise reasonable
  earlier change leads to the problem. Include history only where relevant.
  Report source contradictions outside the message and avoid relying on a
  contradicted account as established fact. Never claim independent
  verification merely because context was supplied. Do not invent reported
  failures, benchmarks, testing history, or rejected alternatives.
- Put series-wide overviews and recaps in the review request or cover letter.
- Use "Right now, ...", "Currently, ...", or "As things stand, ..." when it
  resolves a temporal ambiguity, not automatically in the first paragraph.
  Leave timeless background and lasting contracts unqualified. If the
  ambiguity is in the problem paragraph, anchor that claim instead.
  Avoid implying that every background fact changes afterward with a
  before/after clause. Vary naturally without repeating cues throughout.
- Use "already" for a useful contrast, not merely because a capability
  exists before the patch. If a recent change established the relevant
  state, recount it in past tense and attribute it to earlier work:
  "Recent commits moved ...". Describing that past change differs from
  describing the existing state in present tense. Avoid "now" alone, which
  can imply the current commit made that change. Verify claims against
  the previous commit, not later work.
- Use "commit" and "previous commit" in message prose, not "revision" or
  "parent commit".
- Use present tense for the change ("This commit returns ...") and future
  tense for work in later commits.
- A future use for a capability does not establish follow-up commits.
  Mention later commits only when the supplied series or plan establishes
  them; otherwise explain the purpose without promising a sequence.
- Make references to future work in the series explicit: "in a later commit",
  "later commits will ...", or "the final commit will ...", not bare "later".
- Use "this" only when referring to the commit itself
- Don't overstate impact or use words like "comprehensive" or "crucial"

**Anti-patterns to avoid:**

❌ Imperative body sentence:
```
Add verbose output to the CLI.
```

✅ Declarative opening:
```
This commit adds a --verbose flag that shows review progress.
```

❌ Unsupported judgment (reason excerpt):
```
The code is cumbersome to use.
```

✅ Judgment with a concrete basis (reason excerpt):
```
We have to run another command for each hunk. That gets tedious over a
large change.
```

❌ Overstated scope:
```
This commit solves the discoverability problem.
```

✅ Concrete change (opening excerpt):
```
This commit adds an interactive review walkthrough to the man page.
```

````

## Codex

If `git-stage-batch` is available in your environment, you can install the
bundled Codex commit skills into the current repository:

```bash
git-stage-batch install-assets codex-skills --filter 'commit-*'
```

Omit `--filter` to install all bundled Codex skills.
Use `--force` to replace an existing repo-local copy.

This writes the skills into `.agents/skills/` and installs
`.codex/config.toml` with `sandbox_mode = "workspace-write"` so trusted
project config can grant the skills write access in local Codex sessions.
Codex scans the skills automatically from the repository root.

The bundled Codex skills currently include:

- `commit-staged-changes` for turning the current staged index into one commit
- `commit-unstaged-changes` for splitting unstaged work into one or more commits
- `decompose-and-commit-unstaged-changes` for peeling larger unstaged work into
  concern batches and rebuilding a fine-grained commit series
- `publish-unpushed-commits` for turning a clean unpublished commit range into
  ready-for-review GitHub pull requests or GitLab merge requests, including
  provider-native same-repository stacks and cross-fork publication
- `refine-commit-messages` for auditing and rewording messages in an existing
  series without changing any patch or commit boundary
- `refine-history` for splitting, rewording, and integrating commits in an
  existing local draft series after an optional base or its tracked upstream

The decomposition skill uses `refine-history` for its final committed-series
rewrite, which delegates its final prose pass to `refine-commit-messages`.
Invoke `$refine-history` with no base to use the tracked upstream, or invoke
`$refine-history BASE_SHA` to select an explicit excluded boundary. Add
`audit` before the optional base for a read-only report, and use
`$refine-history resume` to continue its durable product checkpoint.

Invoke `$refine-commit-messages BASE_SHA` directly for a message-only rewrite;
it rewords by default, while `$refine-commit-messages audit BASE_SHA` only
reports findings and proposed messages. Message refinement requires an
explicit base. Both mutating skills require a clean, linear unpublished range
or an explicitly verified force-push review head. That exception permits only
a local rewrite and neither performs nor authorizes a push. Both skills support
`resume` and create recovery refs. Message refinement verifies that every patch
and boundary is unchanged. Full history refinement instead audits every output
and its plan, runs the exact risk-selected semantic-boundary manifest, and
completes the final-tip test and build suite.

For one unambiguous adjacent two-commit squash, adjacent two-commit swap, or
complete message replacement, `refine-history` can use a targeted exact mode.
It still scans, validates, checks live mutation safety, applies, and verifies
the selected transformation, but it does not claim to have audited the
untouched series. Broader cleanup requests use the full semantic audit.

Invoke `$publish-unpushed-commits` to publish ready-for-review pull requests or
merge requests, or add `draft`, `audit`, or `resume`. The skill chooses a native
GitHub stack relationship or GitLab's recognized target-branch chain only when a
dependent group and repository support it; singletons, unavailable stacks, and
forks use ordinary review requests with explicit dependency prose. Publication
never includes merging.

Selecting `decompose-and-commit-unstaged-changes` or
`publish-unpushed-commits` automatically installs both refinement
dependencies. Selecting `refine-history` automatically installs
`refine-commit-messages`.

Installing `codex-skills` also writes a shared internal drafter brief to
`.agents/internal/commit-message-drafter.md` for those skills to use when
they spawn a fresh-context subagent.

## Cursor

Create or update `.cursorrules` in your repository root with the same instructions as above.

## Continue.dev

Create or update `.continuerules`:

```yaml
# git-stage-batch workflow
staging:
  tool: git-stage-batch
  workflow: |
    Use git-stage-batch for atomic commits:

    1. Start: git-stage-batch start
    2. Process hunks:
       - git-stage-batch include (stage)
       - git-stage-batch skip (skip for now)
       - git-stage-batch include --line 1,3,5-7 (specific lines)
    3. Commit when done: git commit -m "..."
    4. Review skipped: git-stage-batch again
    5. Repeat

    Line IDs shown as [#N] in output.
```

## Example AI Workflow

Here's what an AI assistant would do:

```
# AI makes changes to multiple files
# Now needs to create atomic commits

# First commit: Authentication changes
git-stage-batch start
# Shows: auth.py hunk
git-stage-batch include
# Shows: database.py hunk
git-stage-batch skip
# Shows: config.py hunk (auth-related)
git-stage-batch include
# Shows: utils.py hunk
git-stage-batch skip
# No more hunks

git commit -m "auth: Add an OAuth2 flow

This commit adds an OAuth2 flow with token refresh and configuration for
OAuth providers.

We need to integrate with services that use OAuth2. The authentication
code currently supports basic auth only."

# Second commit: Database changes
git-stage-batch again
# Shows: database.py hunk (skipped earlier)
git-stage-batch include
# Shows: utils.py hunk
git-stage-batch skip
# No more hunks

git commit -m "database: Add connection pooling

This commit adds a database connection pool with configurable size and
timeout settings.

The database module currently opens a new connection for each query.
Concurrent requests exhaust the connection limit under load."

# Continue until all changes are committed...
```

## Machine-Readable Output

AI assistants can use `--porcelain` flags for programmatic access:

```
# Check if there's a selected hunk
if git-stage-batch show --porcelain; then
    echo "Hunk exists"
fi

# Get structured status
status=$(git-stage-batch status --porcelain)
echo "$status" | jq '.selected_change'
echo "$status" | jq '.file_review'
echo "$status" | jq '.progress.remaining'
```

## Tips for AI Configuration

1. **Be explicit** - Tell the AI to use git-stage-batch in your instructions
2. **Show examples** - Include example workflows in your config
3. **Emphasize atomic commits** - Make it clear commits should be focused
4. **Line-level control** - Remind the AI about `include --line` for mixed hunks
5. **`again` command** - Ensure the AI knows to run `again` after commits
6. **Capture repair edits** - When decomposing a messy working tree, an AI assistant
   may need to edit files between `discard --to` operations to keep the remaining
   intermediate state coherent. Instruct the AI to capture those intentional edits
   with `include --to repair-*` rather than leaving them as anonymous working-tree
   changes. Anonymous repairs can cause later `apply --from` or `discard --from`
   operations to fail because the structural merge engine expects the file state
   reflected in the batch, not an undocumented in-between state. See the
   [bridge repair batch workflow](batches.md#advanced-workflow-decomposing-and-recomposing-history)
   in the batches documentation.

## Benefits for AI-Assisted Development

| Without git-stage-batch | With git-stage-batch |
|------------------------|---------------------|
| One giant commit | Multiple atomic commits |
| Mixed concerns | Separated concerns |
| Hard to review | Easy to review |
| Difficult to revert | Easy to revert specific changes |
| Unclear history | Clear, logical progression |

## Common Patterns

**Note:** Examples below abbreviate commit messages. Actual bodies should use
declarative narrative present tense and provide the reasoning the reader needs.

### Pattern 1: Feature Implementation

```
# AI implements a feature touching multiple files
git-stage-batch start

# Include all feature-related hunks
git-stage-batch include  # feature code
git-stage-batch skip     # tests (separate commit)
git-stage-batch include  # feature code
git-stage-batch skip     # docs (separate commit)

git commit -m "dashboard: Display account activity"

# Tests
git-stage-batch again
git-stage-batch include  # tests
git commit -m "tests: Add dashboard tests"

# Docs
git-stage-batch again
git-stage-batch include  # docs
git commit -m "docs: Document dashboard feature"
```

### Pattern 2: Refactoring

```
# AI refactors code with both rename and logic changes
git-stage-batch start

# Separate rename from logic changes
# First prompt shows the structural rename
git-stage-batch include               # rename only
git commit -m "processing: Rename helper_function to process_data"

git-stage-batch again
git-stage-batch include             # logic changes
git commit -m "processing: Avoid repeated record scans"
```

### Pattern 3: Bug Fix with Tests

```
# AI fixes bug and adds regression test
git-stage-batch start
git-stage-batch include  # bug fix
git-stage-batch skip     # test
git commit -m "parser: Accept an empty trailing field"

git-stage-batch again
git-stage-batch include  # test
git commit -m "tests: Add regression test for parser edge case"
```

## Next Steps

- [Examples](examples.md) - See more workflow examples
- [Commands Reference](commands.md) - Full command documentation
