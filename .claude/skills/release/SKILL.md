---
name: release
description: Cut, publish, or ship a project release through an annotated tag, GitHub Actions, and PyPI Trusted Publishing.
whenToUse: Use this when the user wants to cut, publish, or ship a new release of the project.
allowed-tools: Bash(git *), Bash(gh *), Bash(uv run pytest *), Bash(uv build), Bash(uv publish), Bash(cat VERSION), Bash(ls dist/), Bash(rm -rf dist/), Read, Write, Edit
user-invocable: true
---

Your task is to cut a new release of this project.

## Prerequisites

Ask the user for the new version number if they have not already provided one.
The current version lives in the `VERSION` file at the repository root.

Before starting, verify:

1. The working tree is clean (`git status --short` produces no output) and
   checked out on `main`.
2. Branches and tags have been fetched from `origin`, `main` has been updated
   with a fast-forward-only pull, and local `main` and `origin/main` resolve
   to the same SHA.
3. The new version follows the project convention and is newer than
   `VERSION`.
4. The proposed tag, GitHub release, and PyPI version do not exist. Treat an
   HTTP 404 from PyPI as absence, but do not mistake a network failure for
   absence.
5. `gh repo view` identifies `halfline/git-stage-batch` as the current
   repository.
6. `docs/releasing.md` and `.github/workflows/release.yml` describe a workflow
   triggered by a published release, using the `pypi` environment, and
   granting `id-token: write` only to the publish job.
7. The repository has a `pypi` GitHub environment:

   ```
   gh api repos/{owner}/{repo}/environments/pypi
   ```

8. For the first release through Trusted Publishing, PyPI has the publisher
   documented in `docs/releasing.md`. A successful earlier `Release` workflow
   run is sufficient evidence on later releases.

If any check fails, stop and report the problem before creating a release
branch.

## Release workflow

Follow these steps in order. Each step depends on the previous one succeeding.
Confirm each step succeeded before moving on. If any step fails, stop and
report the error.

### 1. Create the release branch

Create the branch from the synchronized `main`:

```
git switch -c release-v<VERSION>
```

### 2. Bump the version

Write the new version string without a `v` prefix or trailing newline to
`VERSION`. Commit with the message format:

```
project: Bump version to <VERSION>
```

Follow the commit-message convention in `CONTRIBUTING.md`.

### 3. Build the distributions

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

### 4. Push the branch and open a PR

```
git push -u origin release-v<VERSION>
```

Open a PR with `gh pr create`. Title it `Release v<VERSION>` and include a
changelog of user-visible changes since the previous release tag. Use the
`changes-since-last-release` skill output if it is already available;
otherwise generate the changelog from Git history.

### 5. Merge the PR

```
gh pr merge --merge --delete-branch
git switch main
git pull --ff-only origin main
```

### 6. Tag the merge commit

Recheck that the tag, GitHub release, and PyPI version still do not exist in
case another release raced this one.

Create an annotated `v<VERSION>` tag on the merge commit. Use `v<VERSION>` as
the first line of the annotation and include the changelog in its body.

### 7. Push the tag

```
git push origin v<VERSION>
```

### 8. Publish the GitHub release

```
gh release create v<VERSION> \
  --verify-tag \
  --fail-on-no-commits \
  --title "v<VERSION>" \
  --notes "<changelog markdown>"
```

Publish the release immediately rather than creating a draft. Do not attach
the wheel or source distribution. Publishing the GitHub release triggers
`.github/workflows/release.yml`, which rebuilds and publishes both
distributions through PyPI Trusted Publishing.

### 9. Monitor publication

Find the `Release` workflow run created by the `release` event. Match it to the
tagged commit rather than assuming that the newest run is correct:

```
gh run list --workflow release.yml --event release --commit <COMMIT_SHA>
gh run watch <RUN_ID> --exit-status
```

If the `pypi` environment requires approval, report the pending approval and
wait for an authorized reviewer. If the workflow fails, inspect it with:

```
gh run view <RUN_ID> --log-failed
```

Stop and report the failure instead of publishing the locally built files.

After the workflow succeeds, verify on PyPI that the new version contains a
wheel and source distribution:

```
curl --fail --silent --show-error \
  https://pypi.org/pypi/git-stage-batch/<VERSION>/json
```

## After the release

Report:

- The new version number.
- Links to the merged PR, GitHub release, release workflow, and PyPI version.
- Whether both distributions are present on PyPI.
- The exact tag and commit SHA.
