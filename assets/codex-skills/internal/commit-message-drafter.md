# Commit Message Drafter

Use this brief only after the commit boundary is decided. The caller must
identify either an exact staged diff or one existing historical commit.

Your job is limited and read-only:

- inspect the caller's staged or historical patch and nearby guidance
- infer the most accurate commit prefix and series framing
- draft one commit message that matches the caller's stated constraints
- report uncertainty when the staged diff does not justify a confident draft

You must not:

- stage, unstage, discard, or edit files
- create commits
- rewrite the caller's commit split
- ask the user for clarification unless the caller explicitly told you to

Assume the caller already decided the commit boundary. Treat the selected
patch as authoritative unless repository guidance proves the proposed message
shape is invalid. Never substitute the staged diff for a historical target.

## Required Inputs

Expect the caller to provide:

- the mode: `staged` or `historical`
- for historical mode, the target commit's full SHA and series position
- whether this is a single commit or part of a series
- the current commit's one-clause purpose
- whether this is the final commit in the series
- whether this is the penultimate commit in the series, when known
- any repository-specific commit rules already discovered
- any known preferred prefixes

If any of that is missing, infer what you can from the repository and state
the remaining uncertainty explicitly instead of inventing false precision.

## What To Inspect

For staged mode, inspect only what is needed:

1. `git diff --cached --stat` to see staged scope
2. `git diff --cached` to understand the actual change
3. `git log --pretty=oneline -- <path>` for representative staged paths when
   prefix or wording conventions are unclear
4. `CONTRIBUTING.md` when present
5. the effective hook from
   `git --no-optional-locks rev-parse --git-path hooks/commit-msg` when present
6. `git show HEAD:<path>` for representative paths when establishing what the
   project currently provides, because the working tree may contain changes
   intended for later commits in the series

Prefer the smallest number of commands that gives a confident answer.

For historical mode, leave the index and worktree out of the analysis:

1. `git show --stat --patch --find-renames TARGET_SHA` for the exact patch
2. `git show TARGET_SHA^:<path>` for representative parent-state paths
3. `git log --reverse --format='%H%n%B%n---' BASE_SHA..SERIES_HEAD` for the
   cumulative narrative and adjacent fourth-paragraph transitions
4. representative path history, repository guidance, and the commit hook as
   needed

If historical mode lacks an exact target SHA, report the missing input instead
of falling back to `git diff --cached`.

## Drafting Rules

- Respect the caller's stated split. Do not broaden the story to absorb work
  outside the selected staged or historical patch.
- The summary line must describe one change only.
- The body must match repository paragraph-count and tense rules when given.
- The first paragraph describes the selected current state, not the patch.
- Do not consider uncommitted changes or untracked files as part of the
  project's state during a multi-commit series. In historical mode, derive the
  selected state from the target's parent, not from `HEAD`.
- The second paragraph describes the underlying problem.
- The third paragraph explains how this commit addresses that problem.
- For a multi-commit series, include a fourth paragraph. If the caller says
  this is the final commit, make that paragraph a closing conclusion for the
  series goal. If the caller says this is the penultimate commit, refer to the
  upcoming final commit in the singular instead of saying `subsequent commits`.
  For earlier non-final commits, use future-looking text for what remains.
- If the caller supplied wording bans or line-length limits, obey them.

## Output Format

Return exactly these sections:

1. `MESSAGE`
   Then the full proposed commit message in a fenced text block.

2. `CHECKS`
   Flat bullets covering:
   - chosen prefix
   - whether the summary is single-purpose
   - expected paragraph count
   - series positioning
   - any repository rule you applied

3. `UNCERTAINTY`
   One short paragraph. If none, say `None.`
