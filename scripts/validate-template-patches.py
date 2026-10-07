"""Verify migrations against pinned upstream templates without modifying them."""
import base64
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('migration', ROOT / 'scripts/migrate-pipeline.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def validate(actionlint):
    commits = json.loads((ROOT / 'templates/upstream-commits.json').read_text())
    for repo, commit in commits.items():
        content = subprocess.check_output(['gh', 'api',
            f'repos/link-foundation/{repo}/contents/.github/workflows/release.yml?ref={commit}',
            '--jq', '.content'], text=True)
        original = base64.b64decode(content).decode()
        directory = ROOT / 'experiments/template-validation' / repo
        directory.mkdir(parents=True, exist_ok=True)
        workflow = directory / '.github/workflows/release.yml'
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text(original)
        automation = workflow.with_name('automation-pr.yml')
        if automation.exists():
            automation.unlink()  # Only our disposable validation fixture.
        subprocess.run(['git', 'apply', '--check', str(ROOT / f'templates/patches/{repo}.patch')],
                       cwd=directory, check=True)
        subprocess.run(['git', 'apply', str(ROOT / f'templates/patches/{repo}.patch')],
                       cwd=directory, check=True)
        expected = migration.migrate(original)
        assert workflow.read_text() == expected, repo
        assert automation.read_text() == (ROOT / 'workflow-templates/automation-pr.yml').read_text(), repo
        before = yaml.load(original, Loader=yaml.BaseLoader)
        after = yaml.load(expected, Loader=yaml.BaseLoader)
        assert set(before['jobs']) == set(after['jobs']), repo
        assert after['on']['workflow_dispatch']['inputs']['mode']['default'] == 'checks'
        for name, job in after['jobs'].items():
            if ('release' in name and name != 'release-preflight') or any(
                term in name for term in ('publish', 'changelog-pr', 'changeset-pr', 'deploy-docs', 'docker-merge')
            ):
                assert migration.GATE in job['if'], (repo, name)
        # actionlint 1.7.12 does not yet recognize upstream's existing concurrency.queue setting.
        subprocess.run([actionlint, '-shellcheck=', '-ignore',
                        'unexpected key "queue" for "concurrency" section', str(workflow), str(automation)], check=True)
        print(f'{repo}: patch applies to {commit}; jobs preserved; contracts and workflow syntax passed', flush=True)


if __name__ == '__main__':
    validate(sys.argv[1])
