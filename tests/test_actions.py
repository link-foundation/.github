"""Regression tests for shared credential and workflow dispatch actions."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_action(name):
    path = ROOT / 'actions' / name / 'main.py'
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.action = load_action('resolve-github-token')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name) / 'output'
        self.summary = Path(self.tmp.name) / 'summary'
        self.env = {
            'APP_ID': '', 'APP_PRIVATE_KEY': '', 'APP_TOKEN': '',
            'INPUT_TOKEN': '', 'DEFAULT_TOKEN': 'default-secret',
            'TARGET_OWNER': 'example', 'GITHUB_API_URL': 'https://api.github.com',
            'GITHUB_OUTPUT': str(self.output), 'GITHUB_STEP_SUMMARY': str(self.summary),
        }

    def resolve(self, **env):
        with patch.dict(os.environ, self.env | env, clear=True), \
                patch.object(self.action, 'can_create_repositories', return_value=True) as probe, \
                contextlib.redirect_stdout(io.StringIO()) as log:
            self.action.main()
        return self.output.read_text(), self.summary.read_text(), log.getvalue(), probe

    def test_no_optional_credentials_always_resolves_default(self):
        output, summary, log, probe = self.resolve()
        self.assertIn('layer=default\n', output)
        self.assertIn('triggers-workflows=false\n', output)
        self.assertIn('can-create-repositories=false\n', output)
        self.assertIn('token=default-secret\n', output)
        self.assertIn('GitHub credentials: layer default', log)
        self.assertIn('dispatch-checks', summary)
        probe.assert_not_called()

    def test_app_has_priority_over_token(self):
        output, summary, log, _ = self.resolve(APP_ID='123', APP_PRIVATE_KEY='private',
                                              APP_TOKEN='app-secret', INPUT_TOKEN='pat-secret')
        self.assertIn('layer=app\n', output)
        self.assertIn('token=app-secret\n', output)
        self.assertIn('triggers-workflows=true\n', output)
        self.assertIn('can-create-repositories=true\n', output)
        self.assertIn('layer app', summary)
        self.assertNotIn('pat-secret', log)
        self.assertNotIn('private', log)

    def test_one_token_has_priority_over_default(self):
        output, summary, log, _ = self.resolve(INPUT_TOKEN='pat-secret')
        self.assertIn('layer=token\n', output)
        self.assertIn('token=pat-secret\n', output)
        self.assertIn('layer token', log)
        self.assertNotIn('pat-secret', summary)

    def test_partial_or_failed_app_falls_back(self):
        for inputs in ({'APP_ID': '123'}, {'APP_PRIVATE_KEY': 'private'},
                       {'APP_ID': '123', 'APP_PRIVATE_KEY': 'private'}):
            with self.subTest(inputs=inputs):
                output, _, log, _ = self.resolve(**inputs, INPUT_TOKEN='pat-secret')
                self.assertIn('layer=token\n', output)
                self.assertIn('::warning::', log)

    def test_invalid_name_probe_requires_specific_validation_error(self):
        responses = [
            (422, {'errors': [{'resource': 'Repository', 'field': 'name', 'code': 'missing_field'}]}, True),
            (403, {}, False), (401, {}, False), (404, {}, False),
            (422, {'message': 'Abuse detected'}, False),
            (422, {'errors': [{'field': 'other', 'code': 'invalid'}]}, False),
        ]
        for status, body, expected in responses:
            with self.subTest(status=status, body=body), \
                    patch.object(self.action, 'request', side_effect=[
                        (200, {'type': 'Organization', 'login': 'example'}), (status, body),
                    ]) as request:
                self.assertEqual(self.action.can_create_repositories('secret', 'example'), expected)
                self.assertEqual(request.call_args.args[2], {'name': ''})

    def test_personal_owner_must_match_authenticated_user(self):
        with patch.object(self.action, 'request', side_effect=[
            (200, {'type': 'User', 'login': 'other'}), (200, {'login': 'me'}),
        ]) as request:
            self.assertFalse(self.action.can_create_repositories('secret', 'other'))
            self.assertEqual(request.call_count, 2)


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.action = load_action('dispatch-checks')

    def test_app_and_pat_are_no_ops_without_other_inputs(self):
        with patch.dict(os.environ, {'TRIGGERS_WORKFLOWS': 'true'}, clear=True), \
                patch.object(self.action, 'gh') as gh:
            self.action.main()
            gh.assert_not_called()

    def test_workflow_list_accepts_lines_or_commas_and_rejects_paths(self):
        self.assertEqual(self.action.workflow_files('release.yml, lint.yaml\nrelease.yml'),
                         ['release.yml', 'lint.yaml'])
        for value in ('../release.yml', '--help', 'a.yml extra.yml', ''):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.action.workflow_files(value)

    def test_ignores_old_runs_wrong_commits_and_wrong_events(self):
        runs = [
            {'id': 1, 'head_sha': 'sha', 'head_branch': 'bot', 'event': 'workflow_dispatch'},
            {'id': 2, 'head_sha': 'old', 'head_branch': 'bot', 'event': 'workflow_dispatch'},
            {'id': 3, 'head_sha': 'sha', 'head_branch': 'other', 'event': 'workflow_dispatch'},
            {'id': 4, 'head_sha': 'sha', 'head_branch': 'bot', 'event': 'pull_request'},
            {'id': 5, 'head_sha': 'sha', 'head_branch': 'bot', 'event': 'workflow_dispatch',
             'html_url': 'https://github.com/example/repo/actions/runs/5'},
        ]
        with patch.object(self.action, 'runs', return_value=runs), \
                patch.object(self.action, 'branch_sha', return_value='sha'):
            found = self.action.wait_for_run('example/repo', 'release.yml', 'bot', 'sha', {1}, 1, 0)
        self.assertEqual(found['id'], 5)

    def test_run_creation_timeout_is_finite(self):
        with patch.object(self.action, 'runs', return_value=[]), \
                patch.object(self.action, 'branch_sha', return_value='sha'), \
                patch.object(self.action.time, 'monotonic', side_effect=[0, 0, 2]), \
                patch.object(self.action.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'Timed out'):
                self.action.wait_for_run('example/repo', 'release.yml', 'bot', 'sha', set(), 1, 0)

    def test_branch_movement_fails_instead_of_linking_wrong_commit(self):
        with patch.object(self.action, 'branch_sha', return_value='new'):
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                self.action.wait_for_run('example/repo', 'release.yml', 'bot', 'sha', set(), 1, 0)

    def test_dispatches_each_workflow_and_writes_urls_to_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary, output = Path(tmp) / 'summary', Path(tmp) / 'output'
            env = {'TRIGGERS_WORKFLOWS': 'false', 'TARGET_REPOSITORY': 'example/repo',
                   'INPUT_REF': 'bot', 'WORKFLOWS': 'lint.yml\nrelease.yml',
                   'GH_TOKEN': 'secret', 'WAIT_SECONDS': '120', 'POLL_SECONDS': '3',
                   'GITHUB_OUTPUT': str(output), 'GITHUB_STEP_SUMMARY': str(summary)}
            def gh(*args):
                if args == ('api', 'repos/example/repo'):
                    return {'default_branch': 'main'}
                if 'contents/' in ' '.join(args):
                    return {}
                if args[0] == 'api' and '/actions/workflows/' in args[1]:
                    return {'state': 'active'}
                return None
            with patch.dict(os.environ, env, clear=True), \
                    patch.object(self.action, 'gh', side_effect=gh) as command, \
                    patch.object(self.action, 'branch_sha', return_value='sha'), \
                    patch.object(self.action, 'runs', return_value=[]), \
                    patch.object(self.action, 'wait_for_run', side_effect=[
                        {'html_url': 'https://github.com/example/repo/actions/runs/10'},
                        {'html_url': 'https://github.com/example/repo/actions/runs/11'},
                    ]):
                self.action.main()
            commands = [call.args for call in command.call_args_list if call.args[0] == 'workflow']
            self.assertEqual(len(commands), 2)
            self.assertEqual(commands[0], ('workflow', 'run', 'lint.yml', '--repo', 'example/repo',
                                            '--ref', 'bot', '-f', 'mode=checks'))
            self.assertIn('runs/10', summary.read_text())
            self.assertIn('runs/11', summary.read_text())
            self.assertIn('runs/11', output.read_text())

    def test_new_workflow_missing_on_default_branch_does_not_dispatch(self):
        env = {'TRIGGERS_WORKFLOWS': 'false', 'TARGET_REPOSITORY': 'example/repo',
               'INPUT_REF': 'bot', 'WORKFLOWS': 'new.yml', 'GH_TOKEN': 'secret',
               'WAIT_SECONDS': '120', 'POLL_SECONDS': '3'}
        with patch.dict(os.environ, env, clear=True), \
                patch.object(self.action, 'branch_sha', return_value='sha'), \
                patch.object(self.action, 'gh', side_effect=[
                    {'default_branch': 'main'}, RuntimeError('Not Found'),
                ]) as gh:
            with self.assertRaisesRegex(RuntimeError, 'default branch'):
                self.action.main()
        self.assertFalse(any(call.args[0] == 'workflow' for call in gh.call_args_list))


if __name__ == '__main__':
    unittest.main()
