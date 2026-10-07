"""Opt-in live fixture lifecycle; the fixture workflow must already be on main."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

spec = importlib.util.spec_from_file_location('dispatch', Path(__file__).parents[1] / 'actions/dispatch-checks/main.py')
dispatch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dispatch)
gh = dispatch.gh


def output(key, value):
    with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
        stream.write(f'{key}={value}\n')


def create(repo, branch):
    default = gh('api', f'repos/{repo}')['default_branch']
    # Bootstrap check precedes mutation; a PR-only workflow cannot be dispatched.
    gh('api', f'repos/{repo}/contents/.github/workflows/fixture-checks.yml?ref={default}')
    sha = dispatch.branch_sha(repo, default)
    gh('api', f'repos/{repo}/git/refs', '--method', 'POST', '-f', f'ref=refs/heads/{branch}', '-f', f'sha={sha}')
    output('branch', branch)  # Cleanup remains possible if a later operation fails.
    # Add one disposable fixture file so the pull request has a diff.
    import base64
    gh('api', f'repos/{repo}/contents/credential-fixture.txt', '--method', 'PUT',
       '-f', 'message=chore: exercise credential checks', '-f', f'branch={branch}',
       '-f', 'content=' + base64.b64encode(branch.encode()).decode())
    output('sha', dispatch.branch_sha(repo, branch))
    pr = gh('api', f'repos/{repo}/pulls', '--method', 'POST',
            '-f', f'title=Temporary credential fixture: {branch}', '-f', f'head={branch}',
            '-f', f'base={default}', '-f', 'body=Disposable fixture created by the opt-in credential integration test.')
    output('pr', pr['number'])


def verify(repo, branch):
    event = os.environ['EXPECT_EVENT']
    urls = json.loads(os.environ['RUN_URLS'])
    if event == 'pull_request':
        assert urls == [], 'Token layer must not dispatch a duplicate workflow'
    else:
        assert len(urls) == 1, 'Default layer must report the dispatched run'
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        pages = gh('api', f'repos/{repo}/actions/workflows/fixture-checks.yml/runs?branch={branch}&event={event}',
                   '--paginate', '--slurp')
        for run in [run for page in pages for run in page['workflow_runs']]:
            # PR events use a merge SHA for GITHUB_SHA; their run head_sha is the PR head.
            if run['head_sha'] != os.environ['FIXTURE_SHA']:
                continue
            if event == 'workflow_dispatch' and run['html_url'] not in urls:
                continue
            if run['status'] == 'completed':
                assert run['conclusion'] == 'success', run['html_url']
                print(f'Verified {event} checks: {run["html_url"]}')
                return
        time.sleep(3)
    raise RuntimeError(f'No successful {event} fixture checks appeared within 180 seconds')


def cleanup(repo, branch):
    pr = os.environ.get('FIXTURE_PR')
    if pr:
        gh('api', f'repos/{repo}/pulls/{pr}', '--method', 'PATCH', '-f', 'state=closed')
    gh('api', f'repos/{repo}/git/refs/heads/{branch}', '--method', 'DELETE')


if __name__ == '__main__':
    globals()[sys.argv[1]](os.environ['GITHUB_REPOSITORY'], os.environ['FIXTURE_BRANCH'])
