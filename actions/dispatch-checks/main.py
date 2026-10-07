"""Dispatch checks using gh, with bounded polling and commit/run identity checks."""
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse


def gh(*args):
    result = subprocess.run(['gh', *args], text=True, capture_output=True, timeout=30)
    if result.returncode:
        # gh diagnostics may contain user-controlled responses; keep credentials out of errors.
        raise RuntimeError(f'GitHub command failed ({args[0]}): check workflow availability and token permissions.')
    return json.loads(result.stdout) if result.stdout.strip() and args[0] == 'api' else None


def workflow_files(value):
    files = list(dict.fromkeys(item.strip() for item in re.split('[,\n]', value) if item.strip()))
    if not files or any(not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*\.ya?ml', file) for file in files):
        raise ValueError('workflows must list filenames such as release.yml, without paths or flags.')
    return files


def branch_sha(repository, ref):
    encoded = urllib.parse.quote(ref, safe='')
    return gh('api', f'repos/{repository}/git/ref/heads/{encoded}')['object']['sha']


def runs(repository, workflow, ref, sha):
    query = urllib.parse.urlencode({'event': 'workflow_dispatch', 'branch': ref,
                                  'head_sha': sha, 'per_page': 100})
    pages = gh('api', f'repos/{repository}/actions/workflows/{workflow}/runs?{query}',
               '--paginate', '--slurp')
    return [run for page in pages for run in page['workflow_runs']]


def wait_for_run(repository, workflow, ref, sha, previous_ids, wait_seconds, poll_seconds):
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if branch_sha(repository, ref) != sha:
            raise RuntimeError('Pull request head changed during dispatch; rerun dispatch-checks for the new commit.')
        for run in runs(repository, workflow, ref, sha):
            if (run['id'] not in previous_ids and run.get('head_sha') == sha
                    and run.get('head_branch') == ref and run.get('event') == 'workflow_dispatch'):
                return run
        time.sleep(poll_seconds)
    raise RuntimeError(f'Timed out waiting for {workflow} on the pull request head commit.')


def output_urls(urls):
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
            output.write('run-urls=' + json.dumps(urls) + '\n')


def main():
    triggers = os.environ.get('TRIGGERS_WORKFLOWS', 'false')
    if triggers not in ('true', 'false'):
        raise ValueError('triggers-workflows must be true or false.')
    if triggers == 'true':
        print('dispatch-checks: no dispatch needed; the selected credential triggers workflows normally.')
        output_urls([])
        return
    repository = os.environ['TARGET_REPOSITORY']
    ref = os.environ['INPUT_REF']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository) or not ref or ref.startswith('-'):
        raise ValueError('A repository owner/name and pull request head branch are required.')
    files = workflow_files(os.environ['WORKFLOWS'])
    wait_seconds = int(os.environ.get('WAIT_SECONDS', '120'))
    poll_seconds = int(os.environ.get('POLL_SECONDS', '3'))
    if not 1 <= wait_seconds <= 600 or not 1 <= poll_seconds <= 30:
        raise ValueError('wait-seconds must be 1..600 and poll-seconds must be 1..30.')
    default = gh('api', f'repos/{repository}')['default_branch']
    # Validate every workflow before dispatching any, including newly added PR-only files.
    for workflow in files:
        try:
            gh('api', f'repos/{repository}/contents/.github/workflows/{workflow}'
               f'?ref={urllib.parse.quote(default, safe="")}')
        except RuntimeError as error:
            raise RuntimeError(f'{workflow} must exist on the default branch ({default}) before dispatch.') from error
        metadata = gh('api', f'repos/{repository}/actions/workflows/{workflow}')
        if metadata.get('state') != 'active':
            raise RuntimeError(f'{workflow} is disabled; enable it before dispatching checks.')
    sha = branch_sha(repository, ref)
    urls = []
    for workflow in files:
        previous_ids = {run['id'] for run in runs(repository, workflow, ref, sha)}
        if branch_sha(repository, ref) != sha:
            raise RuntimeError('Pull request head changed before dispatch; rerun for the new commit.')
        gh('workflow', 'run', workflow, '--repo', repository, '--ref', ref, '-f', 'mode=checks')
        run = wait_for_run(repository, workflow, ref, sha, previous_ids, wait_seconds, poll_seconds)
        url = run['html_url']
        urls.append(url)
        print(f'dispatch-checks: {workflow} on {sha}: {url}')
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as summary:
            summary.write(f'- [{workflow} checks on `{sha}`]({url})\n')
        output_urls(urls)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f'::error::{error}', file=sys.stderr)
        sys.exit(1)
