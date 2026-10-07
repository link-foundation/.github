"""Check invalid-name validation without ever sending a valid repository name."""
import json
import subprocess
import urllib.error
import urllib.request

credential = subprocess.check_output(['gh', 'auth', 'token'], text=True).strip()
for label, token in [('invalid', 'invalid-credential'), ('configured', credential)]:
    req = urllib.request.Request(
        'https://api.github.com/orgs/link-foundation/repos',
        data=json.dumps({'name': ''}).encode(), method='POST',
        headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            print(label, response.status)
    except urllib.error.HTTPError as error:
        data = json.load(error)
        print(label, error.code, data.get('message'), data.get('errors'))
