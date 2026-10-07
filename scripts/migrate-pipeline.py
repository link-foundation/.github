"""Prepare a reviewable release.yml migration without changing surrounding formatting.

Usage: python3 scripts/migrate-pipeline.py PATH > migrated.yml
Requires tests/requirements.txt. Apply only after the shared actions are on main.
"""
import re
import sys
import textwrap
from pathlib import Path
import yaml

RESOLVE = '''      - name: Resolve GitHub credentials
        id: gh
        uses: link-foundation/.github/actions/resolve-github-token@main
        with:
          app-id: ${{ vars.AUTOMATION_APP_ID }}
          app-private-key: ${{ secrets.AUTOMATION_APP_PRIVATE_KEY }}
          token: ${{ secrets.AUTOMATION_TOKEN }}
          default-token: ${{ github.token }}
'''
DISPATCH = '''
      - name: Dispatch checks for automation pull request
        if: steps.automation-pr.outputs.pull-request-number != ''
        uses: link-foundation/.github/actions/dispatch-checks@main
        with:
          ref: ${{ steps.automation-pr.outputs.pull-request-branch }}
          workflows: release.yml
          token: ${{ steps.gh.outputs.token }}
          triggers-workflows: ${{ steps.gh.outputs.triggers-workflows }}
'''
MODE = '''      mode:
        description: Checks only, or explicitly enable release and automation jobs.
        type: choice
        default: checks
        options: [checks, release]
'''
GATE = "github.ref == format('refs/heads/{0}', github.event.repository.default_branch) && (github.event_name != 'workflow_dispatch' || inputs.mode == 'release')"
TOKEN = re.compile(r'\$\{\{\s*(?:secrets\.(?:GITHUB_TOKEN|RELEASE_PR_TOKEN)|github\.token)\s*\}\}')


def set_condition(block, condition):
    match = re.search(r'^    if:[^\n]*(?:\n(?:      [^\n]*|[ \t]*$))*', block, re.M)
    replacement = '    if: >-\n' + textwrap.fill(condition, width=110, initial_indent='      ', subsequent_indent='      ', break_long_words=False, break_on_hyphens=False)
    if match:
        return block[:match.start()] + replacement + block[match.end():]
    position = block.index('\n') + 1
    return block[:position] + replacement + block[position:]


def guard(block, condition):
    expression = ' '.join(line.strip() for line in condition.strip().splitlines())
    if expression.startswith('${{') and expression.endswith('}}'):
        expression = expression[3:-2].strip()
    return set_condition(block, GATE + ' && (' + expression + ')')


def migrate(text):
    data = yaml.load(text, Loader=yaml.BaseLoader)
    if 'link-foundation/.github/actions/resolve-github-token@main' in text:
        raise ValueError('This workflow is already migrated; review it instead of migrating twice.')
    if 'workflow_dispatch' not in data['on'] or 'pull_request' not in data['on']:
        raise ValueError('Expected both pull_request and workflow_dispatch triggers.')
    text = text.replace('  workflow_dispatch:\n    inputs:\n', '  workflow_dispatch:\n    inputs:\n' + MODE, 1)
    for key in ('release_mode', 'bump_type'):
        field = re.search(r'^      ' + key + r':\n(?:        .*\n)+', text, re.M)
        if not field:
            continue
        block = field.group()
        default = 'checks' if key == 'release_mode' else 'patch'
        if re.search(r'^        default:', block, re.M):
            block = re.sub(r'^        default:.*$', '        default: ' + default, block, flags=re.M)
        else:
            block = block.replace('        type: choice\n', '        type: choice\n        default: ' + default + '\n')
        if key == 'release_mode':
            block = block.replace('        options:\n', '        options:\n          - checks\n')
        text = text[:field.start()] + block + text[field.end():]
    header, jobs_text = text.split('jobs:\n', 1)
    starts = list(re.finditer(r'^  ([\w-]+):\n', jobs_text, re.M))
    blocks = [jobs_text[:starts[0].start()]]
    for i, start in enumerate(starts):
        block = jobs_text[start.start():starts[i + 1].start() if i + 1 < len(starts) else len(jobs_text)]
        lines = block.splitlines(keepends=True)
        end = len(lines)
        while end and (not lines[end - 1].strip() or lines[end - 1].startswith('  #')):
            end -= 1
        suffix = ''.join(lines[end:])
        block = ''.join(lines[:end]) + '\n'
        name = start.group(1)
        job = data['jobs'][name]
        # Publish/deploy/PR jobs retain their existing condition and get an additional safe gate.
        writes = ('release' in name and name != 'release-preflight') or any(
            word in name for word in ('publish', 'changelog-pr', 'changeset-pr', 'deploy-docs', 'docker-merge')
        )
        if writes:
            block = guard(block, job.get('if', 'success()'))
        elif name not in ('detect-changes', 'release-preflight', 'pipeline-status') and job.get('if'):
            condition = ' '.join(line.strip() for line in job['if'].strip().splitlines())
            if condition.startswith('${{') and condition.endswith('}}'):
                condition = condition[3:-2].strip()
            # Dispatch has no changed-file output, so run checks despite detect-changes being skipped.
            block = set_condition(block, "!cancelled() && ((github.event_name == 'workflow_dispatch' && inputs.mode == 'checks') || (" + condition + '))')
        if not writes and name not in ('detect-changes', 'release-preflight', 'pipeline-status'):
            # Existing PR validators use these env values; supply head/default-base context on dispatch.
            for old, new in (
                ('github.base_ref', 'github.base_ref || github.event.repository.default_branch'),
                ('github.head_ref', 'github.head_ref || github.ref_name'),
                ('github.event.pull_request.head.sha', 'github.event.pull_request.head.sha || github.sha'),
            ):
                block = block.replace('${{ ' + old + ' }}', '${{ ' + new + ' }}')
            block = block.replace("GITHUB_EVENT_NAME: ${{ github.event_name }}", "GITHUB_EVENT_NAME: ${{ github.event_name == 'workflow_dispatch' && 'pull_request' || github.event_name }}")
        # Make preflight credential checks respect checks-only dispatch too.
        if name == 'release-preflight':
            block = block.replace("github.event_name == 'workflow_dispatch'",
                                  "(github.event_name == 'workflow_dispatch' && inputs.mode == 'release')")
        prs = [s for s in job.get('steps', []) if 'create-pull-request@' in s.get('uses', '')]
        # Eliminate the mandatory legacy-token guard, then supply one resolved token everywhere.
        block = re.sub(r'^      - name: Require release pull-request token\n.*?(?=^      - )',
                       '', block, flags=re.M | re.S)
        needs_token = bool(TOKEN.search(block)) or bool(prs)
        if needs_token:
            block = TOKEN.sub('${{ steps.gh.outputs.token }}', block)
            block = block.replace('    steps:\n', '    steps:\n' + RESOLVE, 1)
        if prs:
            if len(prs) != 1 or prs[0].get('id'):
                raise ValueError(f'{name}: review existing PR step IDs before migration.')
            block = re.sub(r'^(      - name: [^\n]+\n)(        uses: peter-evans/create-pull-request@)',
                           r'\1        id: automation-pr\n\2', block, count=1, flags=re.M)
            if 'id: automation-pr' not in block:
                raise ValueError(f'{name}: could not identify the PR creation step.')
            block = block.rstrip() + '\n' + DISPATCH + '\n'
            # Fallback GITHUB_TOKEN must be able to write branches, open PRs, and dispatch checks.
            permissions = job.get('permissions', {})
            for permission in ('contents', 'pull-requests', 'actions'):
                if permission in permissions:
                    block = re.sub(r'^      ' + permission + r':.*$', '      ' + permission + ': write', block, flags=re.M)
                elif '    permissions:\n' in block:
                    block = block.replace('    permissions:\n', '    permissions:\n      ' + permission + ': write\n', 1)
                else:
                    block = block.replace('    steps:\n', '    permissions:\n      ' + permission + ': write\n    steps:\n', 1)
        blocks.append(block.rstrip() + '\n' + suffix)
    result = (header + 'jobs:\n' + ''.join(blocks)).rstrip() + '\n'
    # Parse again to catch damaged YAML before emitting any migration result.
    yaml.load(result, Loader=yaml.BaseLoader)
    return result


if __name__ == '__main__':
    print(migrate(Path(sys.argv[1]).read_text()), end='')
