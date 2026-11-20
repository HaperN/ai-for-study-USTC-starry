"""Preflight credentials and HTTP error handling match the real fetch route."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blocks.parse.mineru import fetch as F
from aiforstudy.core.workspace import Workspace
from aiforstudy.providers.mineru import has_api_credentials


@pytest.mark.parametrize('name', ['MINERU_API_TOKEN', 'MINERU_API_KEY', 'MINERU_TOKEN'])
def test_advertised_token_alias_is_usable_by_fetch(tmp_path, monkeypatch, name):
    for key in ['MINERU_API_TOKEN', 'MINERU_API_KEY', 'MINERU_TOKEN']:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(name, 'offline-token')
    monkeypatch.setattr(F, 'SECRETS', ())
    assert has_api_credentials(Workspace(tmp_path))[0]
    assert F.load_token() == 'offline-token'


def test_empty_secret_file_does_not_pass_preflight(tmp_path, monkeypatch):
    for key in ['MINERU_API_TOKEN', 'MINERU_API_KEY', 'MINERU_TOKEN']:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    secret = tmp_path / '.secrets/mineru.env'
    secret.parent.mkdir()
    secret.write_text('MINERU_API_TOKEN=""\n')
    assert not has_api_credentials(Workspace(tmp_path))[0]


def test_production_preflight_does_not_borrow_home_credentials(tmp_path, monkeypatch):
    from aiforstudy.pipeline.preparation import _mineru_token
    for key in ['MINERU_API_TOKEN', 'MINERU_API_KEY', 'MINERU_TOKEN']:
        monkeypatch.delenv(key, raising=False)
    home = tmp_path / 'other-home'
    secret = home / '.secrets/mineru.env'
    secret.parent.mkdir(parents=True)
    secret.write_text('MINERU_API_TOKEN=offline-other-account\n')
    monkeypatch.setattr(Path, 'home', lambda: home)
    workspace = Workspace(tmp_path / 'explicit-workspace')
    assert not has_api_credentials(workspace)[0]
    assert _mineru_token(workspace) == ''


@pytest.mark.parametrize(('status', 'body', 'error'), [
    (503, b'{"code":0}', F.MinerUUnreachable),
    (429, b'busy', F.MinerUUnreachable),
    (401, b'{"code":0}', F.MinerUFetchError),
    (200, b'[]', F.MinerUFetchError),
    (200, b'null', F.MinerUFetchError),
    (200, b'{"code":"A0211","msg":"expired"}', F.MinerUFetchError),
])
def test_http_or_invalid_envelope_cannot_be_success(monkeypatch, status, body, error):
    monkeypatch.setattr(F, '_request', lambda *a, **kw: (status, body))
    with pytest.raises(error):
        F._api_json('http://127.0.0.1/api', 'offline-token')


def test_preflight_workspace_token_reaches_fetch_without_other_account_fallback(tmp_path, monkeypatch):
    from aiforstudy.pipeline.preparation import _mineru_token

    for key in ['MINERU_API_TOKEN', 'MINERU_API_KEY', 'MINERU_TOKEN']:
        monkeypatch.delenv(key, raising=False)
    workspace = Workspace(tmp_path / 'workspace')
    workspace_secret = workspace.root / '.secrets/mineru.env'
    workspace_secret.parent.mkdir(parents=True)
    workspace_secret.write_text('MINERU_API_TOKEN=""\nMINERU_API_KEY=offline-workspace-token\n')
    repository_secret = tmp_path / 'repository.env'
    repository_secret.write_text('MINERU_API_TOKEN=offline-other-repository-token\n')
    home = tmp_path / 'home'
    home_secret = home / '.secrets/mineru.env'
    home_secret.parent.mkdir(parents=True)
    home_secret.write_text('MINERU_API_TOKEN=offline-other-home-token\n')
    monkeypatch.setattr(Path, 'home', lambda: home)
    monkeypatch.setattr(F, 'SECRETS', (repository_secret, home_secret))

    available, reference = has_api_credentials(workspace)
    assert available and reference == f'file:{workspace_secret}#MINERU_API_KEY'
    token = _mineru_token(workspace)
    assert F.load_token(token) == 'offline-workspace-token'
