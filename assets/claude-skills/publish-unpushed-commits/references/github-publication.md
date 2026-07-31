# GitHub Publication Methods

Use this reference after the publication plan and exact pull request prose are
complete. Installed command help wins when a flag or behavior differs from this
reference.

- [Pin the host and authentication](#pin-the-host-and-authentication)
- [Choose the transport](#choose-the-transport)
- [Push run-owned branches](#push-run-owned-branches)
- [Create exact pull requests](#create-exact-pull-requests)
- [Create or adopt a native stack](#create-or-adopt-a-native-stack)
- [Describe ordinary relationships](#describe-ordinary-relationships)
- [Verify ordinary pull requests](#verify-ordinary-pull-requests)

## Pin the host and authentication

Derive the hostname, including an explicit port, from the validated root host
URL. Pass it explicitly to every `gh api` call. For `gh repo` and `gh pr`
queries, pass a fully qualified `[HOST/]OWNER/REPO` argument instead of relying
on shell-wide host state:

```bash
GITHUB_HOST=$(python3 -c \
  'import sys, urllib.parse; print(urllib.parse.urlsplit(sys.argv[1]).netloc)' \
  "$HOST_URL")
gh api --hostname "$GITHUB_HOST" user
```

The successful `user` query is the authentication gate. `gh` uses the stored
credential for that host, or its documented `GH_TOKEN`, `GITHUB_TOKEN`,
`GH_ENTERPRISE_TOKEN`, or `GITHUB_ENTERPRISE_TOKEN` environment variables as
appropriate. Never print or checkpoint a token. Verify the target and head
repositories' canonical host and owner/name through host-pinned API responses
before starting recovery state. Record each response's positive numeric `id`,
exact `full_name`, canonical `html_url`, `clone_url`, and `ssh_url`. Pass the
numeric IDs and both head clone URLs to `publish-checkpoint.py start`; the
helper rejects a push remote that does not match an observed head-repository
URL.

Repeat those canonical target and head repository queries immediately before
every branch push, pull-request creation, stack creation, and resume.
Require both numeric IDs as well as the paths and URLs to remain unchanged.
Stop if a repository was transferred, renamed, deleted, recreated at the same
path, or resolved through a different host; do not silently update the
checkpoint identity.

## Choose the transport

Use a native GitHub stack only when all of these conditions hold:

- the group has at least two dependent pull requests;
- the group has no more than one hundred pull requests;
- every head branch and the trunk branch live in the target repository;
- `gh api --hostname "$GITHUB_HOST"
  "repos/$TARGET_REPOSITORY/stacks"` confirms that Stacked Pull
  Requests are available.

Use ordinary pull requests for a singleton, when Stacked Pull Requests are
unavailable, or when the head branches live in a fork. Never turn unrelated
work into a dependency merely to obtain a stack.

Create the relationship through GitHub's stack-creation REST endpoint after
the exact pull requests exist. It accepts their numbers in bottom-to-top order
as one server-side request and rejects a chain that is no longer eligible. Do
not substitute `gh stack init`, `submit`, or `link`: those commands can own
local branches, push, create pull requests, correct bases, or add to an
existing stack, duplicating or exceeding this skill's checkpoint authority.

## Push run-owned branches

Resolve the expected old object for every remote branch immediately before
pushing. Require the branch to be absent on a fresh run; the helper rejects a
first preparation that supplies an existing object. On resume, require the old
object to equal the checkpoint's already prepared lease or confirmed object.
First revalidate the head repository's numeric identity and require the
configured push URL to still match its checkpointed, credential-safe digest;
the helper enforces the URL checks when preparing the push. Checkpoint the
lease before the remote side effect:

```bash
python3 "$PUBLISH_HELPER" prepare-push \
  --layer "$LAYER_ID" \
  --expected-old "$EXPECTED_OLD_OR_ABSENT"
```

Then obtain the complete Git argument vector from the checkpoint and execute it
without caller-supplied remote, branch, source, or lease fields:

```bash
PUSH_TARGET=$(python3 "$PUBLISH_HELPER" push-target --layer "$LAYER_ID")
python3 -c '
import json, subprocess, sys, urllib.parse
target = json.load(sys.stdin)
host_url = target["host_url"]
host = urllib.parse.urlsplit(host_url).netloc
for path_key, id_key in (
    ("target_repository", "target_repository_id"),
    ("head_repository", "head_repository_id"),
):
    path = target[path_key]
    observed = json.loads(subprocess.check_output(
        ["gh", "api", "--hostname", host, f"repos/{path}"],
        text=True,
    ))
    if (
        observed.get("id") != target[id_key]
        or observed.get("full_name") != path
        or observed.get("html_url", "").rstrip("/") != f"{host_url}/{path}"
    ):
        raise SystemExit(f"GitHub repository identity changed: {path}")
subprocess.run(["git", *target["arguments"]], check=True)
' <<<"$PUSH_TARGET"
```

For a new branch, pass `absent` to `prepare-push`; the generated Git lease uses
an empty expected object. Pushes are not atomic across branches. Fetch the
remote branch after every successful command, require its object to equal the
planned layer tip, then record that observation:

```bash
python3 "$PUBLISH_HELPER" confirm-push \
  --layer "$LAYER_ID" \
  --remote-head "$FETCHED_REMOTE_SHA"
```

After any failure, fetch and inspect every planned branch and pull request
before retrying. If a prepared push has no confirmation, a remote branch still
at the recorded expected old object can be retried with the same lease; a
remote branch already at the planned tip can be confirmed without another
push. Any third object is a collision and must stop publication.

## Create exact pull requests

Create pull requests with their final audited title and body. Ready-for-review
publication is the default and sends `draft: false`. Explicit draft mode sends
`draft: true` from the frozen plan.

Generate an exact REST endpoint and JSON payload from the checkpoint. Re-query
both immutable repository identities in the same executor immediately before
the mutation, then send the helper-owned payload without caller-supplied
repository, head, base, title, body, or draft fields:

```bash
REQUEST=$(python3 "$PUBLISH_HELPER" github-request --layer "$LAYER_ID")
python3 -c '
import json, subprocess, sys, urllib.parse
request = json.load(sys.stdin)
host_url = request["host_url"]
host = urllib.parse.urlsplit(host_url).netloc
for path_key, id_key in (
    ("target_repository", "target_repository_id"),
    ("head_repository", "head_repository_id"),
):
    path = request[path_key]
    observed = json.loads(subprocess.check_output(
        ["gh", "api", "--hostname", host, f"repos/{path}"],
        text=True,
    ))
    expected_url = f"{host_url}/{path}"
    if (
        observed.get("id") != request[id_key]
        or observed.get("full_name") != path
        or observed.get("html_url", "").rstrip("/") != expected_url
    ):
        raise SystemExit(f"GitHub repository identity changed: {path}")
def branch_commit(path, branch):
    encoded = urllib.parse.quote(branch, safe="")
    reference = json.loads(subprocess.check_output(
        ["gh", "api", "--hostname", host, f"repos/{path}/git/ref/heads/{encoded}"],
        text=True,
    ))
    if reference.get("object", {}).get("type") != "commit":
        raise SystemExit(f"GitHub branch no longer names a commit: {path}:{branch}")
    return reference["object"]["sha"]
if branch_commit(request["head_repository"], request["head_branch"]) != request["head_commit"]:
    raise SystemExit("GitHub pull request head branch changed before creation")
if branch_commit(request["target_repository"], request["base_branch"]) != request["base_head"]:
    raise SystemExit("GitHub pull request base branch changed before creation")
subprocess.run(
    [
        "gh", "api", "--hostname", host, "--method", "POST",
        request["endpoint"], "--input", request["payload_file"],
    ],
    check=True,
)
' <<<"$REQUEST"
```

Create each group from bottom to top. A dependent layer's frozen body template
contains exactly one `{{PRECEDING_REVIEW_URL}}` placeholder. After the preceding
pull request has been created, verified, and recorded, `github-request`
replaces that placeholder with its checkpointed canonical URL and writes the
rendered body into immutable recovery state before producing the POST payload.
The bottom pull request needs no successor link, so no pull request body is
edited after creation and collaborator prose cannot be overwritten.

For same-repository publication, the helper uses the plain checkpointed branch
name. Native-stack members use a chained base: the bottom pull request targets
the trunk and each higher pull request targets the branch immediately below
it. Every ordinary cumulative pull request targets the trunk, including a
same-repository fallback when native stacks are unavailable or exceed their
size bound. For a fork, the helper additionally uses the owner-qualified head
`HEAD_OWNER:BRANCH` and includes the exact `head_repo` name required to
disambiguate organization-owned sibling repositories. This direct REST path
avoids the GitHub CLI's user-owned-fork restriction on `gh pr create --head`.

Higher ordinary pull requests temporarily include their prerequisites in their
diffs. Their descriptions must name the preceding pull request in the series,
state that it must merge first, and explain that GitHub will recalculate the
diff against the trunk afterward. Put this relationship in prose written for a
new reader; do not rely only on a local group label.

Parse the created pull request's number and canonical URL from the creation
response. Query that exact number through `gh api --hostname "$GITHUB_HOST"
"repos/$TARGET_REPOSITORY/pulls/$PR_NUMBER"`, verify its initial title, body,
head repository, branch, head object, direct base branch and object, open state,
and requested draft state, then run `record-created-review` as shown in the main
skill. Do this before native-stack creation. On resume, search by
the exact checkpointed head repository and branch and adopt only a unique
response that passes the same verification.

## Create or adopt a native stack

After creating and verifying every same-repository pull request, query every
exact pull request again. Reverify all ordinary fields and write the fresh
stack membership into a helper input ordered from bottom to top:

```json
{
  "schema": 1,
  "pull_requests": [
    {"number": 41, "stack": null},
    {"number": 42, "stack": null}
  ]
}
```

When membership exists, replace `null` with an object containing the positive
stack `number`, member `position`, and total `size` from the REST pull-request
resource. Generate the exact stack-creation request or adoption decision from
that file. The helper refuses incomplete, foreign, oversized, differently
ordered, or already finally recorded groups:

```bash
STACK_REQUEST=$(python3 "$PUBLISH_HELPER" github-stack-request \
  --group "$GROUP_ID" \
  --observations "$STACK_OBSERVATIONS_FILE")
python3 -c '
import hashlib, json, pathlib, subprocess, sys, urllib.parse
target = json.load(sys.stdin)
host_url = target["host_url"]
host = urllib.parse.urlsplit(host_url).netloc
path = target["target_repository"]
observed = json.loads(subprocess.check_output(
    ["gh", "api", "--hostname", host, f"repos/{path}"],
    text=True,
))
if (
    observed.get("id") != target["target_repository_id"]
    or observed.get("full_name") != path
    or observed.get("html_url", "").rstrip("/") != f"{host_url}/{path}"
):
    raise SystemExit(f"GitHub repository identity changed: {path}")
for expected in target["pull_requests"]:
    number = expected["number"]
    pull = json.loads(subprocess.check_output(
        [
            "gh", "api", "--hostname", host,
            f"repos/{path}/pulls/{number}",
        ],
        text=True,
    ))
    expected_body = pathlib.Path(expected["body_path"]).read_text(encoding="utf-8")
    body_digest = hashlib.sha256(expected_body.encode("utf-8")).hexdigest()
    actual_stack = pull.get("stack")
    expected_stack = expected["stack"]
    if expected_stack is None:
        stack_matches = actual_stack is None
    else:
        stack_matches = actual_stack is not None and all(
            actual_stack.get(key) == expected_stack[key]
            for key in ("number", "position", "size")
        )
    if (
        body_digest != expected["body_sha256"]
        or pull.get("number") != expected["number"]
        or pull.get("html_url", "").rstrip("/") != expected["url"]
        or pull.get("state") != "open"
        or pull.get("draft") != (expected["status"] == "draft")
        or pull.get("title") != expected["title"]
        or pull.get("body") != expected_body
        or pull.get("head", {}).get("repo", {}).get("id") != target["head_repository_id"]
        or pull.get("head", {}).get("ref") != expected["branch"]
        or pull.get("head", {}).get("sha") != expected["head"]
        or pull.get("base", {}).get("repo", {}).get("id") != target["target_repository_id"]
        or pull.get("base", {}).get("ref") != expected["base"]
        or pull.get("base", {}).get("sha") != expected["base_head"]
        or not stack_matches
    ):
        raise SystemExit(
            f"GitHub pull request changed before stack creation: {number}"
        )
if target["action"] == "adopt":
    raise SystemExit(0)
subprocess.run(
    [
        "gh", "api", "--hostname", host, "--method", "POST",
        target["endpoint"], "--input", target["payload_file"],
    ],
    check=True,
)
' <<<"$STACK_REQUEST"
```

If every pull request is unstacked, the decision is `create` and the helper
writes the exact bottom-to-top number list accepted by GitHub's stack-creation
REST endpoint. The executor rechecks each complete pull request and membership
immediately before that single mutation. The server validates the whole chain;
if a pull request became ineligible or joined another stack, creation fails
instead of additively extending that stack. If all pull requests already
occupy exactly one stack in the planned order and with no extra members, the
decision is `adopt` and no mutation runs. Any partial or foreign membership
stops.

Verify each stacked pull request through the repository API. Require the same
non-null stack number, expected stack size and position, recorded head commit,
direct base branch, title, body, and readiness:

```bash
gh api --hostname "$GITHUB_HOST" \
  "repos/$TARGET_REPOSITORY/pulls/$PR_NUMBER"
gh api --hostname "$GITHUB_HOST" \
  "repos/$TARGET_REPOSITORY/stacks/$STACK_NUMBER"
```

If stack creation fails after pull requests were created, retain and report the
ordinary pull requests and checkpoint. Do not delete them, silently retry as a
different topology, or claim native stack publication.

## Describe ordinary relationships

Every higher pull request in an ordinary dependent series includes a final
section whose frozen predecessor placeholder is rendered during bottom-to-top
creation:

```markdown
## Pull request series

This pull request depends on {{PRECEDING_REVIEW_URL}}, the preceding pull
request in the series, and must merge after it.
```

For any ordinary cumulative series, add:

```markdown
Until {{PRECEDING_REVIEW_URL}} merges, this pull request's diff also contains
that prerequisite.
GitHub will recalculate the diff against the target branch after the
prerequisite lands.
```

The helper accepts that token exactly once in every higher layer and forbids it
in a bottom layer. It replaces only the token, using the canonical URL already
recorded for the preceding pull request. Verify the rendered body in the
creation payload and remote response. Never edit a published body to add a
successor link; directed predecessor links completely describe the dependency
without risking collaborator-authored prose.

## Verify ordinary pull requests

For every pull request, require the API response to match the checkpoint's
target repository, head repository and owner, branch, commit, direct base,
title, body, open state, and requested draft state. Resolve the direct base
branch through the target repository API immediately before and after creation;
both observations must equal the plan's target-base commit or, for a native
stack, the preceding layer tip. Pass that exact object to `record-review
--base-head`. For an ordinary series, require every base to be the target trunk
and every higher body to identify its real prerequisite.

Do not wait for continuous integration unless the user explicitly asks. Query
the registered checks once for the completion report. Treat an empty result as
"not registered," not as success.
