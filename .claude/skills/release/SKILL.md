---
name: release
description: Cut a new release — bump VERSION, build, open a PR, merge, tag, push, upload to GitHub and PyPI.
whenToUse: Use this when the user wants to cut, publish, or ship a new release of the project.
allowed-tools: Bash(git *), Bash(gh *), Bash(uv build), Bash(uv run twine *), Bash(cat VERSION), Bash(ls dist/), Bash(rm -rf dist/), Read, Write, Edit
user-invocable: true
---

Your task is to cut a new release of this project.

## Prerequisites

Ask the user for the new version number if they have not already provided one. The current version lives in the `VERSION` file at the repository root.

Before starting, verify:
1. The working tree is clean (`git status --short` produces no output).
2. You are on the `main` branch.
3. All tests pass (`uv run pytest`).

If any check fails, stop and report the problem.

## Release workflow

Follow these steps in order. Each step depends on the previous one succeeding. Confirm each step succeeded before moving on. If any step fails, stop and report the error — do not skip steps.

### 1. Create the release branch

```
git checkout -b release-v<VERSION>
```

### 2. Bump the version

Write the new version string (without a `v` prefix, without a trailing newline) to the `VERSION` file. Commit with the message format:

```
project: Bump version to <VERSION>
```

The commit body should follow the project's three-paragraph commit-message convention (see CONTRIBUTING.md). Describe the current version state, why a bump is needed, and what this commit does.

### 3. Build the wheel

```
rm -rf dist/
uv build
```

Verify a `.whl` and `.tar.gz` appeared in `dist/`.

### 4. Push the branch and open a PR

```
git push -u origin release-v<VERSION>
```

Open a PR with `gh pr create`. The title should be `Release v<VERSION>`. The body should contain a changelog section summarizing user-visible changes since the previous release tag. Use the `changes-since-last-release` skill output if available in conversation context; otherwise generate a summary from `git log`.

### 5. Merge the PR

```
gh pr merge --merge --delete-branch
```

After merging, update the local main branch:

```
git checkout main
git pull origin main
```

### 6. Tag the merge commit

The project uses **lightweight tags** on the merge commit (not annotated tags). Find the merge commit on main and tag it:

```
git tag v<VERSION>
```

### 7. Push the tag

```
git push origin v<VERSION>
```

### 8. Create the GitHub release

```
gh release create v<VERSION> --title "v<VERSION>" --notes "<changelog markdown>"
```

The release notes should match the PR body changelog section. Do not attach wheel or sdist artifacts to the GitHub release — those go to PyPI only.

### 9. Upload to PyPI

```
uv run twine upload dist/*
```

If twine is not available, try:

```
uv run python -m twine upload dist/*
```

If credentials are not configured or upload fails, report the error and tell the user what command to run manually.

## After the release

Report the final state:
- The new version number
- Links to the GitHub PR (now merged) and release page
- Whether the PyPI upload succeeded
- The exact tag and commit SHA
