# GitLab Publication Methods

Use this reference after the publication plan and exact merge-request prose are
complete. Installed `glab` help and the selected GitLab host's API responses win
when behavior differs from this reference.

- [Pin the host, authentication, and projects](#pin-the-host-authentication-and-projects)
- [Choose the transport](#choose-the-transport)
- [Push run-owned branches](#push-run-owned-branches)
- [Create exact merge requests](#create-exact-merge-requests)
- [Describe and verify relationships](#describe-and-verify-relationships)
- [Verify merge requests](#verify-merge-requests)

## Pin the host, authentication, and projects

This workflow supports GitLab.com and self-managed GitLab installations whose
public URL is rooted at the host, with an optional port. Reject installations
served below a relative URL such as `https://example.com/gitlab`; `glab` cannot
reliably pin those API paths without host configuration outside this skill's
authority.

Derive the API hostname, including an explicit port, and protocol from the
validated root URL. Reject a per-host or environment `api_host` or
`api_protocol` override that differs from that destination; otherwise
`--hostname` can select credentials for one host while sending the API request
to another. Every GitLab CLI query and mutation must name the validated
hostname:

```bash
GITLAB_HOST=$(python3 -c \
  'import sys, urllib.parse; print(urllib.parse.urlsplit(sys.argv[1]).netloc)' \
  "$HOST_URL")
GITLAB_SCHEME=$(python3 -c \
  'import sys, urllib.parse; print(urllib.parse.urlsplit(sys.argv[1]).scheme)' \
  "$HOST_URL")
if ! CONFIGURED_API_HOST=$(glab config get api_host --host "$GITLAB_HOST"); then
  echo "Cannot read GitLab api_host configuration" >&2
  exit 1
fi
if ! CONFIGURED_API_PROTOCOL=$(glab config get api_protocol --host "$GITLAB_HOST"); then
  echo "Cannot read GitLab api_protocol configuration" >&2
  exit 1
fi
if [[ -n "$CONFIGURED_API_HOST" && "$CONFIGURED_API_HOST" != "$GITLAB_HOST" ]]; then
  echo "GitLab api_host does not match the selected host" >&2
  exit 1
fi
if [[ -n "$CONFIGURED_API_PROTOCOL" && "$CONFIGURED_API_PROTOCOL" != "$GITLAB_SCHEME" ]]; then
  echo "GitLab api_protocol does not match the selected scheme" >&2
  exit 1
fi
glab api --hostname "$GITLAB_HOST" user
```

The successful `user` query is the authoritative authentication check. `glab`
selects the credential registered for that exact host, while `GITLAB_TOKEN`,
`GITLAB_ACCESS_TOKEN`, or `OAUTH_TOKEN` can supply an environment-only token
and take precedence over stored credentials. `glab auth status --hostname
"$GITLAB_HOST"` is optional diagnostics, not a required gate, because an
environment-only token need not be represented as stored login state. Never
print a token, copy it into the checkpoint, or pass it on a command line. Stop
before pushing if the authenticated API preflight fails.

Resolve both project paths through the same host before starting the checkpoint.
URL-encode nested namespaces, require each response's `path_with_namespace` to
equal the intended path, and record its positive numeric `id`:

```bash
ENCODED_TARGET=$(python3 -c \
  'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' \
  "$TARGET_REPOSITORY")
ENCODED_HEAD=$(python3 -c \
  'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' \
  "$HEAD_REPOSITORY")
glab api --hostname "$GITLAB_HOST" "projects/$ENCODED_TARGET"
glab api --hostname "$GITLAB_HOST" "projects/$ENCODED_HEAD"
```

For each response, require the positive numeric `id`, exact
`path_with_namespace`, and canonical `web_url` to equal respectively the
observed ID, intended repository path, and
`$HOST_URL/$INTENDED_REPOSITORY_PATH`. Require its `http_url_to_repo` to use
the same scheme, host, port, and repository path. Record the head project's
exact `http_url_to_repo` and `ssh_url_to_repo` values and pass both as
`--head-push-url` arguments at checkpoint start. This lets the helper bind
either ordinary HTTP or exact SSH push configuration while failing closed on
aliases it cannot prove. It also detects an `api_host` redirect, wrong-host
response, or stale namespace before recovery state is created.

Pass those IDs as `--target-project-id` and `--head-project-id` to
`publish-checkpoint.py start`. Numeric IDs bind later API requests to the
observed projects and let fork publication avoid changing Git remotes; they do
not make a namespace rename transparent. If either project's canonical path or
URL changes during a run, stop instead of updating the checkpoint identity.
Re-read both numeric project objects and both GitLab API configuration fields
immediately before every branch push, merge-request creation, and resume.
Require the same path, canonical URL, clone host, API hostname, and API
protocol. This makes the stop-on-rename and stop-on-reroute rules pre-mutation
checks rather than only completion-time diagnostics.

## Choose the transport

GitLab recognizes a stack from ordinary merge-request target branches. Select
`gitlab-stack` only when all of these conditions hold:

- the group has two to ten dependent merge requests;
- every source branch and the trunk live in the target project;
- the selected host's `version` API reports GitLab 19.1 or newer; and
- the bottom merge request targets the trunk while each higher merge request
  targets the source branch immediately below it.

Use `ordinary` for a singleton, an older GitLab host, a group larger than the
native stack limit, or a cross-fork publication. In an ordinary dependent
series, target every cumulative merge request at the trunk. Do not retain a
same-project source-to-source chain while labeling it ordinary: GitLab can
infer that chain as a native stack and reject an oversized group only after
remote creation has begun.

Do not use `glab stack create`, `save`, `amend`, or `sync`. That experimental
workflow owns commit creation, local branches, pushing, and merge-request prose,
which duplicates this skill's refinement and checkpoint ownership. GitLab's
server recognizes the planned target-branch chain without separate stack
metadata or a linking mutation.

## Push run-owned branches

Resolve the expected old object for every remote branch immediately before
pushing. Require a new branch on a fresh run. On resume, compare the remote
object with the checkpointed lease. First require the configured push URL to
still match the checkpoint's credential-safe digest; the helper enforces this
when preparing the push. Record the lease before the remote side effect:

```bash
python3 "$PUBLISH_HELPER" prepare-push \
  --layer "$LAYER_ID" \
  --expected-old "$EXPECTED_OLD_OR_ABSENT"
```

Obtain the complete Git argument vector from the checkpoint. In the same
executor, reject an API configuration reroute and re-read the numeric head
project before executing Git without caller-supplied remote, branch, source,
or lease fields:

```bash
PUSH_TARGET=$(python3 "$PUBLISH_HELPER" push-target --layer "$LAYER_ID")
python3 -c '
import json, subprocess, sys, urllib.parse
target = json.load(sys.stdin)
host_url = target["host_url"]
parsed_host = urllib.parse.urlsplit(host_url)
host = parsed_host.netloc
for key, expected in (("api_host", host), ("api_protocol", parsed_host.scheme)):
    configured = subprocess.check_output(
        ["glab", "config", "get", key, "--host", host],
        text=True,
    ).strip()
    if configured and configured != expected:
        raise SystemExit(f"GitLab {key} changed: {configured}")
for path_key, id_key in (
    ("target_repository", "target_project_id"),
    ("head_repository", "head_project_id"),
):
    path = target[path_key]
    project_id = target[id_key]
    observed = json.loads(subprocess.check_output(
        ["glab", "api", "--hostname", host, f"projects/{project_id}"],
        text=True,
    ))
    clone = urllib.parse.urlsplit(observed.get("http_url_to_repo", ""))
    if (
        observed.get("id") != project_id
        or observed.get("path_with_namespace") != path
        or observed.get("web_url", "").rstrip("/") != f"{host_url}/{path}"
        or clone.scheme != parsed_host.scheme
        or clone.netloc != host
        or clone.path.removesuffix(".git").rstrip("/") != f"/{path}"
    ):
        raise SystemExit(f"GitLab project identity changed: {path}")
subprocess.run(["git", *target["arguments"]], check=True)
' <<<"$PUSH_TARGET"
```

For a new branch, pass `absent` to `prepare-push`; the generated Git lease uses
an empty expected object. Fetch or query the source project after every push,
require the branch to equal the planned tip, and confirm it:

```bash
python3 "$PUBLISH_HELPER" confirm-push \
  --layer "$LAYER_ID" \
  --remote-head "$FETCHED_REMOTE_SHA"
```

Pushes are not atomic across branches. On resume, a prepared branch still at
its expected old object can be retried with the same lease; one at the planned
tip can be confirmed without another push. Any third object is a collision and
must stop publication.

## Create exact merge requests

Do not use `glab mr create` or `glab mr update`. Depending on local remotes,
those commands can add Git configuration while resolving a fork, and their
draft option rewrites the title. Generate an exact JSON API request instead:

```bash
REQUEST=$(python3 "$PUBLISH_HELPER" gitlab-request --layer "$LAYER_ID")
python3 -c '
import json, subprocess, sys, urllib.parse
request = json.load(sys.stdin)
host_url = request["host_url"]
parsed_host = urllib.parse.urlsplit(host_url)
host = parsed_host.netloc
for key, expected in (("api_host", host), ("api_protocol", parsed_host.scheme)):
    configured = subprocess.check_output(
        ["glab", "config", "get", key, "--host", host],
        text=True,
    ).strip()
    if configured and configured != expected:
        raise SystemExit(f"GitLab {key} changed: {configured}")
for path_key, id_key in (
    ("target_repository", "target_project_id"),
    ("head_repository", "head_project_id"),
):
    path = request[path_key]
    project_id = request[id_key]
    observed = json.loads(subprocess.check_output(
        ["glab", "api", "--hostname", host, f"projects/{project_id}"],
        text=True,
    ))
    clone = urllib.parse.urlsplit(observed.get("http_url_to_repo", ""))
    if (
        observed.get("id") != project_id
        or observed.get("path_with_namespace") != path
        or observed.get("web_url", "").rstrip("/") != f"{host_url}/{path}"
        or clone.scheme != parsed_host.scheme
        or clone.netloc != host
        or clone.path.removesuffix(".git").rstrip("/") != f"/{path}"
    ):
        raise SystemExit(f"GitLab project identity changed: {path}")
def branch_commit(project_id, branch):
    encoded = urllib.parse.quote(branch, safe="")
    observed = json.loads(subprocess.check_output(
        [
            "glab", "api", "--hostname", host,
            f"projects/{project_id}/repository/branches/{encoded}",
        ],
        text=True,
    ))
    return observed.get("commit", {}).get("id")
if branch_commit(request["head_project_id"], request["head_branch"]) != request["head_commit"]:
    raise SystemExit("GitLab merge request source branch changed before creation")
if branch_commit(request["target_project_id"], request["base_branch"]) != request["base_head"]:
    raise SystemExit("GitLab merge request target branch changed before creation")
subprocess.run(
    [
        "glab",
        "api",
        "--hostname",
        host,
        "--method",
        "POST",
        request["endpoint"],
        "--input",
        request["payload_file"],
    ],
    check=True,
)
' <<<"$REQUEST"
```

Create each group from bottom to top. A dependent layer's frozen description
template contains exactly one `{{PRECEDING_REVIEW_URL}}` placeholder. After the
preceding merge request has been created, verified, and recorded,
`gitlab-request` replaces that placeholder with its checkpointed canonical URL
and writes the rendered description into immutable recovery state before
producing the POST payload. The bottom merge request needs no successor link,
so no published description is edited and collaborator prose cannot be
overwritten.

The helper creates the request through the numeric source-project endpoint and
includes the numeric target project ID. It preserves the description's exact
UTF-8 text and terminal newline. Ready titles are used verbatim. In explicit
draft mode, the final planned title begins with exactly `Draft: `; that title is
also sent verbatim, so the API marks the merge request draft without a second
prefix.

GitLab descriptions can execute quick actions. The helper rejects a line that
looks like one rather than silently changing labels, reviewers, assignees,
milestones, readiness, merge state, or other collaboration state. If repository
template prose intentionally requires a quick action, the helper deliberately
offers no bypass. Remove or escape it, or stop and perform a separately
authorized manual collaboration-state mutation outside this workflow.

Immediately before creation, query the source branch through the numeric head
project and the direct base branch through the numeric target project. Require
their objects to equal the planned layer tip and base head. Query both again
after creation; a changed base or source makes the result ambiguous and must
stop the run.

Parse the new merge request's positive `iid` and canonical `web_url`, then
query that exact IID from the numeric target project. Verify its initial title,
description, source and target project IDs, source and target branches, head
and base objects, opened state, and requested draft state. Run
`record-created-review` as shown in the main skill before creating the next
layer. On resume, search the target project by the exact numeric source project
and branch, and adopt only a unique response that passes the same verification.

## Describe and verify relationships

For an eligible same-project stack, create the bottom merge request against the
trunk and each higher merge request against the source branch immediately below
it. GitLab detects the stack from that chain. Verify the ordered source and
target branches through the host-pinned API after every member exists.

Every higher merge request also explains its dependency in prose:

```markdown
## Merge request stack

This merge request depends on {{PRECEDING_REVIEW_URL}}, the preceding merge
request in the stack, and must merge after it.
```

For an ordinary cumulative series, every merge request targets the trunk. Use
ordinary relationship prose and add:

```markdown
Until {{PRECEDING_REVIEW_URL}} merges, this merge request's diff also contains
that prerequisite. GitLab will recalculate the diff against the target branch
after the prerequisite lands.
```

Do not add GitLab merge-request dependencies as an implicit substitute. They
are a separate licensed merge gate and broader collaboration-state mutation.
Use them only when the user or repository policy explicitly requires them.

The helper accepts the predecessor token exactly once in every higher layer
and forbids it in a bottom layer. It replaces only that token with the
checkpointed canonical URL. Verify the rendered description in the creation
payload and remote response. Never edit a published description to add a
successor link; directed predecessor links completely describe the dependency
without risking collaborator-authored prose.

## Verify merge requests

Read every merge request from the numeric target project on the selected host:

```bash
glab api \
  --hostname "$GITLAB_HOST" \
  "projects/$TARGET_PROJECT_ID/merge_requests/$MR_NUMBER"
```

Require `target_project_id`, `source_project_id`, source branch, exact `sha`,
target branch, title, description, `opened` state, `draft` value, and `web_url`
to match the checkpoint and plan. Query the direct target branch once more and
require its object to equal the pre-creation observation and the plan's expected
base head. Pass that exact object to `record-review --base-head`.

For a stack, require the complete source-to-target branch chain and the
documented two-to-ten member bound. For an ordinary series, require every
target branch to be the trunk and every higher description to identify its real
prerequisite. On resume, search the target project for the exact source project
and branch before creating anything; adopt only a unique response that verifies
against the plan.

Do not wait for pipelines unless the user explicitly asks. Query the merge
request's registered pipelines once for the completion report. Treat an empty
result as "not registered," not as success.
