---
name: release-tweet-thread
description: Draft an algorithm-optimized tweet thread announcing the latest release.
whenToUse: Use this when the user wants to announce a release on X/Twitter or draft social media posts about a release.
allowed-tools: Bash(git *), Bash(gh *), Read
user-invocable: true
---

Your task is to draft a tweet thread announcing the latest release of this project,
optimized for the X recommendation algorithm's ranking signals.

## Workflow

1. Find the most recent release tag with `git describe --tags --abbrev=0`.
2. Get the GitHub release notes with `gh release view <tag>`.
3. Inspect commits in the range `<previous-tag>..<latest-tag>` for detail.
4. Draft a tweet thread (see format below).

## Thread format

Write 2-4 tweets. Each tweet must be under 280 characters.

**Tweet 1 (hook):** Lead with a concrete before/after or a surprising result — something that makes people stop scrolling (dwell time is a ranking signal). Mention the project name and version. Include a link to the GitHub release page.

**Tweet 2-3 (highlights):** One highlight per tweet. Keep them concrete — what can users do now that they could not before? Avoid jargon. Use short sentences. Where possible, suggest including a short demo video or screenshot — media activates dedicated ranking signals (video quality views, photo expand) that plain text does not.

**Final tweet (engagement close):** End with a genuine question that invites replies — reply probability is one of the highest-weighted ranking signals. Good patterns: ask what feature users want next, ask about their current workflow pain point, or pose a "how do you handle X?" question. Pair with a low-friction call to action (install command, link to docs).

## Output format

Present each tweet in a clearly numbered, copy-paste-ready block. Use a visual separator between tweets. Example:

```
--- Tweet 1 ---
<tweet text>

--- Tweet 2 ---
<tweet text>
```

After the thread, show the character count for each tweet so the user can verify they fit.

If any tweet would benefit from an attached image or video, note that below the thread with a brief description of what to capture (e.g., "Attach: 15-second terminal recording of the new `--hierarchical` flag").

## Guidelines

- Do not use hashtags unless the user asks for them.
- Do not use emoji unless the user asks for them.
- Write in a direct, conversational tone — not marketing speak.
- If the release is small (one fix, one feature), two tweets are enough. Do not pad.
- Keep the thread short. The algorithm applies author diversity decay — each additional tweet from the same author in a feed session scores lower than the previous one. A tight 2-3 tweet thread outperforms a padded 5-tweet thread.
- Write for shareability. Tweets that get reposted, bookmarked, or shared via DM each activate separate weighted ranking signals. Practical tips and concrete results are more shareable than vague announcements.
- Avoid anything that could trigger mute/block/not-interested signals — no clickbait, no "like and repost" begging, no generic filler.
