# Security Policy

## Supported Versions

Security fixes are provided for the latest released version on a best-effort
basis.

Older releases are generally not supported for security updates. If you report
an issue against an older version, you may be asked to reproduce it on the
current release first.

| Version | Supported |
| --- | --- |
| Latest release | Yes |
| Older releases | No |
| Unreleased development branch | Best effort |

## Reporting a Vulnerability

Please do not report security vulnerabilities through public GitHub issues.

Instead, report them privately to:

- Ray Strode
- Email: <git-stage-batch@halfline.org>

Include as much of the following as you can:

- The `git-stage-batch` version
- Your operating system and Python version
- A clear description of the issue and expected impact
- Reproduction steps or a minimal repository that demonstrates the problem
- Whether the issue requires local repository control, malicious file contents,
  symlinks, unusual permissions, or other special setup

If you are unsure whether something is a security issue, report it anyway.

## What To Expect

Reports will be reviewed on a best-effort basis. A useful report will usually
receive an acknowledgement within a few days, but no formal response-time SLA
is guaranteed.

After triage, the maintainer may:

- Ask for additional reproduction details
- Confirm the issue and work on a fix
- Decide the report is better handled as a normal bug if the primary impact is
  correctness, data loss, or workflow breakage rather than a security boundary

Please allow time for a fix to be prepared before public disclosure.

## Scope

`git-stage-batch` is a local command-line tool that operates on Git
repositories and repository-local state. The most relevant classes of security
issues for this project are likely to include:

- Unsafe handling of repository-controlled paths, symlinks, or permissions
- Cases where untrusted repository contents can cause writes outside the
  intended repository or `.git` area
- Command execution risks caused by repository-controlled input
- Disclosure of sensitive local data through program output, logs, or stored
  state

The following are usually not treated as security vulnerabilities by
themselves:

- Crashes, incorrect staging, or state corruption without a plausible security
  impact
- Behavior that requires intentionally running the tool inside a repository you
  do not trust, unless the behavior crosses an expected local safety boundary
- Requests for general hardening without a concrete exploit path

## Handling Sensitive Data

Avoid sending private repositories, credentials, or proprietary patches unless
they are necessary to reproduce the problem. If a reproducer contains sensitive
material, say so in the report and provide a reduced example when possible.
