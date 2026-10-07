# Shared GitHub automation actions

Every repository uses optional credentials and one credential for all GitHub
workloads: bot pull requests, issue/PR integration tests, repository fixtures,
and cleanup. No extra token is needed to open a pull request or start its checks.

```yaml
permissions:
  contents: write
  pull-requests: write
  actions: write

steps:
  - id: gh
    uses: link-foundation/.github/actions/resolve-github-token@main
    with:
      app-id: ${{ vars.AUTOMATION_APP_ID }}
      app-private-key: ${{ secrets.AUTOMATION_APP_PRIVATE_KEY }}
      token: ${{ secrets.AUTOMATION_TOKEN }}
      default-token: ${{ github.token }}

  # Use steps.gh.outputs.token for every GitHub workload in this job.
  # After opening or pushing a bot PR, dispatch its checks when needed:
  - uses: link-foundation/.github/actions/dispatch-checks@main
    with:
      ref: automation/update
      workflows: release.yml
      token: ${{ steps.gh.outputs.token }}
      triggers-workflows: ${{ steps.gh.outputs.triggers-workflows }}
```

The shared actions must be merged to `main` before these references work. Consumers
may pin a reviewed commit instead. See [a complete bot PR workflow](examples/bot-pull-request.yml)
and [a checks/release workflow](examples/checks-and-release.yml). Replace the
example `scripts/check.sh`, `scripts/release.sh`, and generation command with the
repository's existing commands.

## Credential resolution

[resolve-github-token](actions/resolve-github-token/action.yml) chooses the first
usable layer and writes its choice to the log and job summary on every invocation.

| Layer | Caller configuration | `triggers-workflows` | Behavior |
| --- | --- | --- | --- |
| `app` | `vars.AUTOMATION_APP_ID` and `secrets.AUTOMATION_APP_PRIVATE_KEY` | `true` | Installation token minted with `actions/create-github-app-token@v3`; normal workflow events. |
| `token` | `secrets.AUTOMATION_TOKEN` | `true` | One fine-grained, classic, or organization-provided token for all repository workloads. |
| `default` | `github.token` (input defaults to it) | `false` | PR events require approval; explicitly dispatched checks run normally. |

All configuration is optional. An incomplete App configuration or a failed mint
falls through to the supplied token, then the default token, with a warning.
An explicitly supplied expired or unauthorized token can still fail a later
GitHub operation; optional configuration does not grant additional permissions.
Pass the built-in token through `default-token`, not `token`.

Composite actions receive secrets through inputs; they cannot read the caller's
secrets themselves. `AUTOMATION_TOKEN` is the sole optional token name because
GitHub does not allow custom secret names beginning with `GITHUB_`.

The outputs are `token`, `layer`, `triggers-workflows`, and
`can-create-repositories`. Boolean outputs are strings: compare them to `'true'`
or `'false'`. Tokens are masked before they are written as outputs.

The `owner` input defaults to `github.repository_owner`. The App token covers that
owner's installation, so fixtures and cleanup can use the same credential.
Set `repositories` to narrow its scope when cross-repository workloads are not
needed. Tokens expire after an hour and are revoked by the minting action's
post-job cleanup; resolve a fresh credential in each job that needs one.

`can-create-repositories` is always `false` for the default token. For App and
token layers, the resolver looks up the owner and submits `{"name":""}` to its
repository creation endpoint. The permanently invalid blank name cannot create
a repository. Only a name-specific HTTP 422 validation response confirms
capability; denied, unavailable, and unrecognized responses return `false`.
For a personal owner, the authenticated user must match. The probe tests public
repository creation: private visibility, organization policy, or a narrower App
installation may impose further restrictions. A false result lets a caller use
an existing repository for fixtures instead of requiring another secret.

Request the permissions each workload needs on the same credential. Bot PRs need
Contents write and Pull requests write; dispatch needs Actions write and Contents
read. Creating/deleting repository fixtures additionally needs repository
Administration or Repository creation permissions, or the corresponding classic
token scopes. App/PAT permissions are configured on that credential; a workflow's
`permissions` block controls its built-in token. Repository settings must allow
Actions to create pull requests. The resolver cannot increase permissions.

## Dispatching checks

[dispatch-checks](actions/dispatch-checks/action.yml) accepts:

| Input | Meaning |
| --- | --- |
| `ref` | Same-repository PR head branch, after pushing the latest commit. |
| `workflows` | Workflow filenames, separated by commas or newlines; no paths. |
| `token` | Resolver output, with permission to dispatch workflows. |
| `triggers-workflows` | Resolver output; `'true'` returns immediately without GitHub calls. Default: `'false'`. |
| `repository` | Target `owner/repository`; defaults to `github.repository`. |
| `wait-seconds` | Run creation wait per workflow, 1–600 seconds; default 120. |
| `poll-seconds` | Poll delay, 1–30 seconds; default 3. |

For each workflow the action executes:

```console
gh workflow run release.yml --repo OWNER/REPO --ref HEAD_BRANCH -f mode=checks
```

It first checks that **every** workflow exists on the default branch and is
active. It records existing run IDs and the branch head SHA, then waits for a new
`workflow_dispatch` run on that branch and commit. Older runs, ordinary PR runs,
and runs on other commits do not satisfy the wait. A moving branch or a missing
run produces an actionable failure. Creation waits are bounded; the action does
not wait for the checks to finish.

Each run URL is logged and written to the job summary immediately, preserving
links if a later dispatch fails. `run-urls` is a JSON array of those URLs (`[]`
for the App/token no-op). Dispatched checks attach to the head commit and appear
in the PR's checks. This avoids approving the default-token PR runs, which can
remain waiting for approval. No PR comment permission is needed for reporting.

Serialize writers/dispatches for the same workflow and branch with a job or
workflow `concurrency` group and `cancel-in-progress: false`. GitHub's CLI dispatch
does not provide a request correlation ID, so simultaneous identical dispatches
cannot be distinguished by commit and new run ID alone. Fork head branches must
be checked in their own repository; they are not refs in the base repository.

## Checks-only dispatch contract

Every listed workflow must be present on the default branch and accept a
`workflow_dispatch` input named `mode`, with `checks` as its default. A newly
added PR-only workflow cannot be dispatched until it has been merged.

Checks must run for `workflow_dispatch`/`mode=checks` as well as `pull_request`.
Conditions based only on changed-file outputs or the PR event need a dispatch
alternative; PR validators need head/default-base context on dispatch. Release,
publishing, deployment, and bot PR creation jobs require an explicit release or
automation mode **and** the default branch. Existing automatic releases on pushes
to the default branch can keep their behavior.

```yaml
workflow_dispatch:
  inputs:
    mode:
      type: choice
      default: checks
      options: [checks, release]

# On a release job:
if: >-
  github.ref == format('refs/heads/{0}', github.event.repository.default_branch) &&
  (github.event_name == 'push' ||
  (github.event_name == 'workflow_dispatch' && inputs.mode == 'release'))
```

Consumers using `release_mode: instant` must change the default to `checks`,
accept `mode`, and gate publishing before using this action. See the
[template migration bundle](templates/README.md), including existing check-job
conditions and the additional default-branch gates. This also applies to
`link-assistant/hive-mind` and `link-assistant/formal-ai` consumers tracked by
[issue #1](https://github.com/link-foundation/.github/issues/1).

## Testing and rollout

Run local validation with Python 3.12+ and GitHub CLI:

```console
python3 -m pip install -r tests/requirements.txt
python3 -m unittest discover -s tests -v
# With the actionlint 1.7.12 executable available:
actionlint .github/workflows/*.yml examples/*.yml workflow-templates/*.yml
python3 scripts/validate-template-patches.py /path/to/actionlint
```

CI runs regression/contract tests, workflow lint, all eight pinned template patch
application checks, and real default/supplied-token resolver smoke tests. Tests
assert layer log lines and summary text using captured action output. The supplied
layer smoke test uses `AUTOMATION_TOKEN` when present, otherwise `github.token`
solely to exercise input selection; it does not claim that a built-in token
triggers ordinary workflows. An installed test App enables the third live
resolver invocation. Missing App credentials are explicitly reported, while the
selection and fallback paths remain covered by unit tests.

After merging the fixture workflow to the default branch, run the opt-in live
integration test:

```console
gh workflow run credential-integration.yml --repo link-foundation/.github --ref main -f mode=checks
```

[credential-integration.yml](.github/workflows/credential-integration.yml) creates
a disposable branch and PR in this repository, dispatches its fixture checks
with the default token, verifies successful checks on the head SHA, then closes
the PR and deletes the branch in an `always()` cleanup step. With the optional
`AUTOMATION_TOKEN`, it additionally verifies natural `pull_request` checks and
that dispatch returns `[]`. Its workflow is serialized and jobs have ten-minute
limits. It deliberately checks default-branch fixture availability before
creating anything. No fixture PR is merged.

The live App/token paths cannot be exercised without those credentials, and the
new dispatch fixture cannot run before it exists on `main`. Template patches are
prepared here; applying them to the eight separate repositories is a subsequent
rollout through their own pull requests.
