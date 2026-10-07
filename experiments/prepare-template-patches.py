"""Capture upstream commit identity, regenerate patches, and save lintable results."""
import base64
import difflib
import importlib.util
import json
from pathlib import Path
import subprocess
spec = importlib.util.spec_from_file_location('migration', 'scripts/migrate-pipeline.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
manifest = {}
commits_path = Path('templates/upstream-commits.json')
previous_commits = json.loads(commits_path.read_text()) if commits_path.exists() else {}
for path in sorted(Path('experiments').glob('*-release-upstream.yml')):
    language = path.name.split('-')[0]
    repo = f'{language}-ai-driven-development-pipeline-template'
    commit = previous_commits.get(repo) or subprocess.check_output(['gh', 'api', f'repos/link-foundation/{repo}/branches/main', '--jq', '.commit.sha'], text=True).strip()
    content = subprocess.check_output(['gh', 'api', f'repos/link-foundation/{repo}/contents/.github/workflows/release.yml?ref={commit}', '--jq', '.content'])
    original = base64.b64decode(content).decode()
    path.write_text(original)
    migrated = module.migrate(original)
    Path(f'experiments/{language}-release-migrated.yml').write_text(migrated)
    diff = ''.join(difflib.unified_diff(original.splitlines(True), migrated.splitlines(True),
        fromfile='a/.github/workflows/release.yml', tofile='b/.github/workflows/release.yml'))
    diff += ''.join(difflib.unified_diff([], Path('workflow-templates/automation-pr.yml').read_text().splitlines(True), fromfile='/dev/null', tofile='b/.github/workflows/automation-pr.yml'))
    Path(f'templates/patches/{repo}.patch').write_text(diff)
    manifest[repo] = commit
    print(repo, len(diff.splitlines()))
Path('templates/upstream-commits.json').write_text(json.dumps(manifest, indent=2) + '\n')
