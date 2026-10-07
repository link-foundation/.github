"""Validate runnable action metadata and examples, including release safety."""
import importlib.util
import json
from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def document(path):
    return yaml.load(path.read_text(), Loader=yaml.BaseLoader)


class ContractTests(unittest.TestCase):
    def test_resolver_uses_v3_app_minting_and_runtime_input_env(self):
        action = document(ROOT / 'actions/resolve-github-token/action.yml')
        self.assertEqual(set(action['outputs']),
                         {'token', 'layer', 'triggers-workflows', 'can-create-repositories'})
        self.assertEqual(action['inputs']['default-token']['default'], '${{ github.token }}')
        mint = action['runs']['steps'][0]
        self.assertEqual(mint['uses'], 'actions/create-github-app-token@v3')
        self.assertIn('inputs.app-id', mint['if'])
        self.assertIn('inputs.app-private-key', mint['if'])
        self.assertEqual(mint['continue-on-error'], 'true')

    def test_runtime_shell_scripts_do_not_interpolate_untrusted_inputs(self):
        for path in (ROOT / 'actions').glob('*/action.yml'):
            for step in document(path)['runs']['steps']:
                if 'run' in step:
                    self.assertNotIn('${{', step['run'])

    def test_dispatch_contract_defaults_to_checks(self):
        paths = [ROOT / '.github/workflows/fixture-checks.yml', ROOT / 'examples/checks-and-release.yml',
                 ROOT / '.github/workflows/npm-build.yml']
        for path in paths:
            with self.subTest(path=path):
                data = document(path)
                self.assertIn('pull_request', data['on'])
                mode = data['on']['workflow_dispatch']['inputs']['mode']
                self.assertEqual(mode['default'], 'checks')
                self.assertIn('checks', mode['options'])

    def test_example_releases_need_explicit_mode_and_default_branch(self):
        data = document(ROOT / 'examples/checks-and-release.yml')
        condition = data['jobs']['release']['if']
        self.assertIn("inputs.mode == 'release'", condition)
        self.assertIn('github.event.repository.default_branch', condition)
        self.assertIn("github.event_name == 'push'", condition)
        self.assertEqual(data['jobs']['release']['needs'], 'checks')

    def test_all_template_repositories_have_both_action_migrations(self):
        commits = json.loads((ROOT / 'templates/upstream-commits.json').read_text())
        self.assertEqual(len(commits), 8)
        for repo, commit in commits.items():
            with self.subTest(repo=repo):
                self.assertEqual(len(commit), 40)
                patch = (ROOT / f'templates/patches/{repo}.patch').read_text()
                self.assertIn('actions/resolve-github-token@main', patch)
                self.assertIn('actions/dispatch-checks@main', patch)
                self.assertIn('+        default: checks', patch)
                self.assertIn('+++ b/.github/workflows/automation-pr.yml', patch)

    def test_migration_keeps_check_jobs_and_original_release_conditions(self):
        spec = importlib.util.spec_from_file_location('migration', ROOT / 'scripts/migrate-pipeline.py')
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        original = '''name: Minimal pipeline
on:
  pull_request:
  workflow_dispatch:
    inputs:
      release_mode:
        type: choice
        default: instant
        options:
          - instant
      bump_type:
        type: choice
        options:
          - patch
jobs:
  checks:
    runs-on: ubuntu-latest
    steps:
      - run: echo checks
  manual-release:
    if: github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - run: echo publish
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
'''
        data = document_from_text(migration.migrate(original))
        before = document_from_text(original)
        self.assertEqual(data['jobs']['checks'], before['jobs']['checks'])
        release = data['jobs']['manual-release']
        self.assertIn(migration.GATE, release['if'])
        self.assertIn(before['jobs']['manual-release']['if'], release['if'])
        self.assertEqual(release['steps'][0]['id'], 'gh')
        self.assertEqual(release['steps'][1]['env']['GH_TOKEN'], '${{ steps.gh.outputs.token }}')
        self.assertEqual(data['on']['workflow_dispatch']['inputs']['bump_type']['default'], 'patch')


def document_from_text(text):
    return yaml.load(text, Loader=yaml.BaseLoader)


if __name__ == '__main__':
    unittest.main()
