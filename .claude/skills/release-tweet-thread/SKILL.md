---
name: release-tweet-thread
description: Draft a copy-paste tweet thread announcing the latest release.
whenToUse: Use this when the user wants to announce a release on X/Twitter or draft social media posts about a release.
allowed-tools: Bash(git *), Bash(gh *), Read
user-invocable: true
---

Your task is to draft a tweet thread announcing the latest release of this project.

## Workflow

1. Find the most recent release tag with `git describe --tags --abbrev=0`.
2. Get the GitHub release notes with `gh release view <tag>`.
3. Inspect commits in the range `<previous-tag>..<latest-tag>` for detail.
4. Draft a tweet thread (see format below).

## Thread format

Write 2-4 tweets. Each tweet must be under 280 characters.

**Tweet 1:** Lead with the release announcement. Mention the project name, version, and the single most compelling change. Include a link to the GitHub release page.

**Tweet 2-3:** One highlight per tweet. Keep them concrete — what can users do now that they could not before? Avoid jargon. Use short sentences.

**Tweet 4 (optional):** Call to action — install command (`pip install git-stage-batch`), link to docs, or invitation to try it.

## Output format

Present each tweet in a clearly numbered, copy-paste-ready block. Use a visual separator between tweets. Example:

```
--- Tweet 1 ---
<tweet text>

--- Tweet 2 ---
<tweet text>
```

After the thread, show the character count for each tweet so the user can verify they fit.

## Guidelines

- Do not use hashtags unless the user asks for them.
- Do not use emoji unless the user asks for them.
- Write in a direct, conversational tone — not marketing speak.
- If the release is small (one fix, one feature), two tweets are enough. Do not pad.
