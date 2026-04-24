---
name: changes-since-last-release
description: Summarize all changes in this repository since the most recent release tag.
whenToUse: Use this when the user wants a release summary, changelog draft, or an overview of commits since the latest tagged release.
allowed-tools: Bash(git tag --sort=-creatordate), Bash(git describe --tags --abbrev=0), Bash(git log), Bash(git diff --stat), Bash(git shortlog), Bash(git status --short), Bash(rg), Read
user-invocable: true
---

Your task is to summarize everything that changed since the last release in this repository.

Workflow:

1. Find the most recent release tag with `git describe --tags --abbrev=0`.
2. If no tag exists, say that clearly and summarize the full project history instead of failing.
3. Inspect commits in the range `<latest-tag>..HEAD`.
4. Inspect the high-level file delta with `git diff --stat <latest-tag>..HEAD`.
5. Group the changes into a concise release-oriented summary:
   - user-visible features
   - fixes
   - docs or packaging changes
   - internal or refactor work
6. Include the exact tag and commit range you used.
7. Call out if the working tree is dirty, but do not mix uncommitted changes into the release summary unless the user explicitly asks.

Output requirements:

- Start with `Changes since <tag>`.
- Include a short bullet list of the most important items.
- Include a commit count.
- Include a compact file-change summary based on `git diff --stat`.
- If helpful, draft a changelog section in markdown.

Do not create tags, commits, or release artifacts unless the user explicitly asks.
