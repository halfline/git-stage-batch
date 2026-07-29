# Releasing

Publishing a GitHub release triggers
[`release.yml`](https://github.com/halfline/git-stage-batch/blob/main/.github/workflows/release.yml).
The workflow builds the wheel and source distribution in a job without
publishing credentials, transfers those files as a workflow artifact, and
gives only the final PyPI job permission to request an OpenID Connect token.

## One-time Trusted Publisher setup

Configure the existing `git-stage-batch` project under
[PyPI's **Publishing** settings](https://docs.pypi.org/trusted-publishers/adding-a-publisher/)
with this GitHub publisher:

- **Owner:** `halfline`
- **Repository:** `git-stage-batch`
- **Workflow name:** `release.yml`
- **Environment name:** `pypi`

Create a matching `pypi` environment in the GitHub repository. Add required
reviewers to that environment when releases should require approval after the
distribution build completes.

The workflow intentionally has no PyPI password or API-token secret. Its
publish job receives only `id-token: write`, downloads the distributions
created by the unprivileged build job, and publishes them with
[PyPA's official Trusted Publishing action](https://docs.pypi.org/trusted-publishers/using-a-publisher/).

## Publish a release

1. Confirm that CI passes on the release commit.
2. Create and push the version tag.
3. Publish the corresponding GitHub release.
4. Approve the `pypi` environment deployment when protection rules require it.
5. Verify that both the wheel and source distribution appear on PyPI.

The GitHub release event selects the tagged commit, so do not publish a draft
release until its tag points to the intended source.
