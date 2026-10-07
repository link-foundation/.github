"""Credential selection and a non-creating repository permission probe (stdlib only)."""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def request(token, endpoint, data=None):
    url = os.environ.get('GITHUB_API_URL', 'https://api.github.com').rstrip('/') + '/' + endpoint
    req = urllib.request.Request(
        url, data=None if data is None else json.dumps(data).encode(),
        headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
                 'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def can_create_repositories(token, owner):
    """Confirm authorization using a blank name that GitHub can never create.

    A name-specific 422 means the creation endpoint reached name validation;
    forbidden, hidden, unavailable, or unrecognized responses are false.
    This probes public repository creation; private visibility may have stricter policy.
    """
    try:
        status, account = request(token, 'users/' + urllib.parse.quote(owner, safe=''))
        if status != 200:
            return False
        if account.get('type') == 'Organization':
            endpoint = 'orgs/' + urllib.parse.quote(owner, safe='') + '/repos'
        else:
            status, user = request(token, 'user')
            if status != 200 or user.get('login', '').lower() != owner.lower():
                return False
            endpoint = 'user/repos'
        # Never change this to a valid name: this request must not create resources.
        status, response = request(token, endpoint, {'name': ''})
        return status == 422 and any(
            isinstance(error, dict) and error.get('resource') == 'Repository'
            and error.get('field') == 'name' and error.get('code') in ('missing_field', 'invalid')
            for error in response.get('errors', [])
        )
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        print('::warning::Repository creation permission could not be confirmed; reporting false.')
        return False


def main():
    app = os.environ.get('APP_TOKEN', '')
    supplied = os.environ.get('INPUT_TOKEN', '')
    default = os.environ.get('DEFAULT_TOKEN', '')
    if app:
        layer, token = 'app', app
    elif supplied:
        layer, token = 'token', supplied
    else:
        layer, token = 'default', default
    if not token or '\n' in token or '\r' in token:
        raise ValueError('A valid default-token (github.token) is required in an Actions job.')
    # Escape workflow command control characters before masking any credential.
    masked = token.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
    print(f'::add-mask::{masked}')
    if (os.environ.get('APP_ID') or os.environ.get('APP_PRIVATE_KEY')) and not app:
        print('::warning::App credentials are incomplete or minting failed; using the next available layer.')
    triggers = layer != 'default'
    can_create = triggers and can_create_repositories(token, os.environ['TARGET_OWNER'])
    if layer == 'default':
        message = ('GitHub credentials: layer default (no usable AUTOMATION_APP_ID / AUTOMATION_TOKEN '
                   'configured); pull requests opened here get their checks through dispatch-checks')
    else:
        message = f'GitHub credentials: layer {layer}; credential-triggered workflows start normally'
    print(message)
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as summary:
        summary.write(message + '\n\n')
    with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
        output.write(f'token={token}\nlayer={layer}\ntriggers-workflows={str(triggers).lower()}\n'
                     f'can-create-repositories={str(can_create).lower()}\n')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        print(f'::error::{error}', file=sys.stderr)
        sys.exit(1)
