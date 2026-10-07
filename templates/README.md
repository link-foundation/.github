# Pipeline template migration bundle

This repository owns the shared actions. The Rust, JavaScript, Python, C#, C++,
Go, Java, and PHP pipeline templates live in separate repositories. The
[patches](patches) provide their reviewable migrations without pushing to their
default branches or replacing their existing pipelines.

Each patch is generated against the exact default-branch commit recorded in
[upstream-commits.json](upstream-commits.json). It:

- Adds `mode: checks` as the safe dispatch default and `release_mode: checks`
  where that input exists; other required dispatch inputs gain safe defaults.
- Preserves every existing job and its original release conditions, while adding
  an explicit release mode and default-branch gate to publication/deployment/PR jobs.
- Lets checks run when dispatch has no changed-file detection results and supplies
  head/default-base context to existing PR validation scripts.
- Resolves a single credential for GitHub operations, replaces legacy
  `GITHUB_TOKEN`/`RELEASE_PR_TOKEN` input expressions, and removes the mandatory
  release-PR token check in the JavaScript template.
- Dispatches `release.yml` after existing bot PR steps, with the same credential
  and enough default-token permissions for branches, PRs, and workflow dispatch.
- Adds the same optional-credential documentation PR starter to every template,
  so even templates without an existing bot PR job demonstrate both actions.

The [organization starter](../workflow-templates/automation-pr.yml) writes a
requested documentation update when explicitly dispatched with `mode=create-pr`
on the default branch. Its default `mode=checks` performs no writes. Replace the
documentation generation step with the repository's intended automation. The
companion properties file makes the starter available in the organization's
workflow chooser after this PR is merged.

Merge the shared actions first. In a checkout of each template on its own review
branch, apply its matching patch from this repository:

```console
git apply --check /path/to/shared-actions/templates/patches/rust-ai-driven-development-pipeline-template.patch
git apply /path/to/shared-actions/templates/patches/rust-ai-driven-development-pipeline-template.patch
```

Run that template's complete local/CI checks and review its release-specific
scripts before merging its migration. To release manually after migration, set
`mode=release` **and** its existing `release_mode` (for example `instant`); for the
Python/PHP workflows without `release_mode`, `mode=release` is the explicit choice.
`mode=checks` never authorizes a release job, including on the default branch.

Regenerate a changed release workflow with:

```console
python3 scripts/migrate-pipeline.py /path/to/template/.github/workflows/release.yml > /tmp/migrated-release.yml
```

The script requires `tests/requirements.txt`, keeps surrounding formatting, and
rejects an already-migrated workflow. Recheck the resulting diff; template-specific
PR validators can need adjustments beyond their workflow environment. For
example, a validator reading only the event JSON must also support dispatched
head/base context. The patches' validation checks YAML contracts and preserved
jobs; it does not execute eight languages' release scripts or publish packages.

`python3 scripts/validate-template-patches.py /path/to/actionlint` fetches the
pinned originals, applies each patch in `experiments/template-validation/`,
checks it equals the migration output, checks release gates and preserved jobs,
and lints the resulting workflows. Only the existing upstream
`concurrency.queue` syntax is excluded from actionlint 1.7.12's validation, because
that version does not recognize it. All other workflow errors fail validation.

These patches have not been applied to the external repositories by this PR.
After application, new repositories created from those templates inherit the
optional credential policy. Existing hive-mind/formal-ai callers should use the
same contract when their related issues are implemented.
