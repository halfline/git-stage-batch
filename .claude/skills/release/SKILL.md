---
name: release
description: Cut, publish, or ship a project release through an annotated tag, GitHub Actions, and PyPI Trusted Publishing.
whenToUse: Use this when the user wants to cut, publish, or ship a new release of the project.
allowed-tools: Bash(git *), Bash(gh *), Bash(uv run pytest *), Bash(uv build), Bash(uv publish), Bash(cat VERSION), Bash(ls dist/), Bash(rm -rf dist/), Read, Write, Edit
user-invocable: true
---

Cut a release only from the canonical repository.

## Safety rules

- Follow the steps in order and verify each result before continuing.
- Stop on a failed or ambiguous check. Do not skip it or infer success.
- Finalize one changelog against the exact release commit and reuse it for the
  PR, annotated tag, and GitHub release.
- Never run `uv publish` or upload locally built distributions.
- Never bypass branch protection, use an administrative merge, or force-push.
- Never move or replace an existing release tag.

## 1. Establish the release inputs

Ask for the new version when the user has not supplied it. `VERSION` contains
the current version without a `v` prefix.

Before changing the repository:

1. Require a clean worktree on `main` and require `gh repo view` to identify
   `halfline/git-stage-batch` as the current repository.
2. Fetch branches and tags from `origin`, update `main` with a fast-forward-only
   pull, and require local `main` and `origin/main` to resolve to the same SHA.
3. Require the new version to follow the project convention and be newer than
   `VERSION`.
4. Require the proposed tag, GitHub release, and PyPI version not to exist.
   Distinguish PyPI's HTTP 404 response from a network failure.
5. Read `docs/releasing.md` and `.github/workflows/release.yml`. Require the
   workflow to run on a published release, publish through the `pypi`
   environment, and grant `id-token: write` only to its publish job.
6. Verify that the repository has a `pypi` GitHub environment with:

   ```
   gh api repos/{owner}/{repo}/environments/pypi
   ```

7. For the first release through Trusted Publishing, require confirmation that
   PyPI has the publisher documented in `docs/releasing.md`. A successful
   earlier `Release` workflow run is sufficient evidence on later releases.

Stop and report any missing one-time setup before creating a release branch.

Generate a draft changelog of user-visible changes from the latest release tag
through synchronized `main`. Use the `changes-since-last-release` skill output
when it is already available; otherwise derive it from Git history. Save it
outside the repository so shell quoting cannot alter it.

## 2. Prepare and validate the release commit

Create `release-v<VERSION>` from the synchronized `main` branch:

```
git switch -c release-v<VERSION>
```

Write the new version to `VERSION` without a `v` prefix or trailing newline,
then commit it with this summary:

```
project: Bump version to <VERSION>
```

Follow the commit-message convention in `CONTRIBUTING.md`.

Prepare the same development and build environment used by the release
workflow:

```
uv venv --allow-existing
uv pip install --group dev
uv sync --all-groups
```

Run the full test suite:

```
uv run pytest -n auto
```

Build into a new temporary directory rather than the repository's potentially
stale `dist/` directory:

```
release_dist=$(mktemp -d)
uv build --out-dir "$release_dist"
```

Require that directory to contain a wheel and source distribution for exactly
the new version.

## 3. Open and merge the release PR

Push the branch and open a PR titled `Release v<VERSION>`. Include the
changelog in its body.

Watch the PR checks and require them to pass:

```
gh pr checks <PR_NUMBER> --watch --fail-fast
```

Record the checked PR head SHA. Merge with a merge commit, require that exact
head SHA, and request remote branch deletion:

```
gh pr merge <PR_NUMBER> --merge --delete-branch \
  --match-head-commit <PR_HEAD_SHA>
```

Query the PR afterward. Do not continue unless its state is `MERGED` and
`mergeCommit.oid` is present; protected branches may leave an auto-merge
pending even after the merge command returns. Wait for the pending merge or
stop.

```
gh pr view <PR_NUMBER> --json state,mergedAt,mergeCommit,url
```

Record `mergeCommit.oid` as `RELEASE_SHA`. Switch to `main`, pull with
`--ff-only`, and require all of these conditions:

- `HEAD` equals `RELEASE_SHA`.
- `origin/main` equals `RELEASE_SHA`.
- `VERSION` contains the requested version.

Regenerate the changelog from the previous release tag through `RELEASE_SHA`,
excluding the mechanical version-bump commit. If concurrent merges changed
the draft, replace it with this final changelog and update the merged PR body
so all release surfaces describe the exact tagged history.

Find the `CI` workflow run for the push of `RELEASE_SHA`, retrying the lookup
briefly if event delivery has not created it yet:

```
gh run list --workflow ci.yml --event push --commit <RELEASE_SHA> \
  --json databaseId,headSha,status,conclusion,url
gh run watch <RUN_ID> --exit-status
```

Require that exact run to conclude successfully before tagging.

## 4. Create and verify the annotated tag

Recheck that the tag, GitHub release, and PyPI version still do not exist in
case another release raced this one.

Create a tag-message file outside the repository containing `v<VERSION>` as
its first line, followed by a blank line and the unchanged changelog. Create an
annotated `v<VERSION>` tag explicitly at `RELEASE_SHA` using that file.

```
git tag -a --file <TAG_MESSAGE_FILE> v<VERSION> <RELEASE_SHA>
```

Before pushing, require:

- `git cat-file -t v<VERSION>` prints `tag`.
- `git rev-list -n 1 v<VERSION>` equals `RELEASE_SHA`.

Push only that tag. Verify that the remote annotated tag dereferences to
`RELEASE_SHA`.

```
git push origin v<VERSION>
git ls-remote origin 'refs/tags/v<VERSION>^{}'
```

## 5. Publish the GitHub release

Create a published GitHub release from the existing remote tag:

```
gh release create v<VERSION> \
  --verify-tag \
  --fail-on-no-commits \
  --title "v<VERSION>" \
  --notes-file <CHANGELOG_FILE>
```

Do not use `--draft` and do not attach the locally built wheel or source
distribution. Verify the release is published for the intended tag and record
its URL and publication time.

## 6. Monitor Trusted Publishing

Find the `Release` workflow run created after that publication time. Retry the
lookup briefly if event delivery has not created it yet, and match both the
`release` event and `RELEASE_SHA`:

```
gh run list --workflow release.yml --event release --commit <RELEASE_SHA> \
  --json databaseId,headSha,status,conclusion,createdAt,url
```

Select the matching run ID explicitly, then watch it:

```
gh run watch <RUN_ID> --exit-status
```

If the publish job waits for approval of the `pypi` environment, report the
pending approval and wait for an authorized reviewer. Do not bypass it.

If the run fails, inspect it with:

```
gh run view <RUN_ID> --log-failed
```

Stop and report the failure instead of publishing the locally built files.

## 7. Verify and report

After the workflow succeeds, retrieve:

```
curl --fail --silent --show-error \
  https://pypi.org/pypi/git-stage-batch/<VERSION>/json
```

Confirm that the requested version page contains a wheel and source
distribution.

Report:

- The version, tag, and `RELEASE_SHA`.
- The merged PR and published GitHub release URLs.
- The exact release workflow URL and successful conclusion.
- The PyPI version URL and confirmation that both distributions are present.
