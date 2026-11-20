"""Stable cache identity retains substantive capability and isolation changes."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from aiforstudy.providers import capabilities as caps


def sandbox_probe(tmp_path, monkeypatch):
    from aiforstudy.blocks.visual import worker
    asset = tmp_path / 'policy.py'
    asset.write_text('deny network')
    monkeypatch.setattr(caps, 'resolve_worker_dir', lambda *a: tmp_path)
    monkeypatch.setattr(caps, 'resolve_manim_interpreter', lambda *a: '/python')
    backend = SimpleNamespace(available=True, backend='seatbelt', detail='policy available')
    monkeypatch.setattr(worker, 'select_isolation', lambda *a: backend)
    state = {'calls': 0, 'safe': True, 'network': 'deny'}

    def probe(**kwargs):
        state['calls'] += 1
        sentinel = f"/tmp/aiforstudy-probe/probe-{state['calls']}/sentinel/fake-provider.env"
        return {'probe_paths': [sentinel, *kwargs['probe_paths']],
                'isolation': {'safe': state['safe'], 'backend': backend.backend,
                              'reason': '' if state['safe'] else 'network reachable',
                              'applied': {'seatbelt': {'backend': backend.backend,
                                                      'network': state['network']}}}}
    monkeypatch.setattr(worker, 'probe_isolation', probe)
    workspace = SimpleNamespace(root=tmp_path, config_json=lambda *a: {
        'sandboxes': {'worker': {'required_for': ['render']}}})
    return workspace, backend, state, asset


def test_sandbox_diagnostics_do_not_change_identity_but_probe_runs_each_time(tmp_path, monkeypatch):
    workspace, _, state, _ = sandbox_probe(tmp_path, monkeypatch)
    first, second = caps.probe(workspace), caps.probe(workspace)
    assert first.as_dict() != second.as_dict()  # Full diagnostic paths remain inspectable.
    assert state['calls'] == 2
    assert first.identity_dict() == second.identity_dict()
    assert first.identity_dict()['items'][0]['required_for'] == ['render']


@pytest.mark.parametrize('change', ['backend', 'available', 'policy_receipt', 'policy_asset'])
def test_sandbox_identity_changes_with_security_state(tmp_path, monkeypatch, change):
    workspace, backend, state, asset = sandbox_probe(tmp_path, monkeypatch)
    before = caps.probe(workspace).identity_dict()
    if change == 'backend': backend.backend = 'landlock-seccomp'
    elif change == 'available': state['safe'] = False
    elif change == 'policy_receipt': state['network'] = 'different-policy'
    else: asset.write_text('changed policy implementation')
    assert caps.probe(workspace).identity_dict() != before


def test_other_capability_details_keep_version_and_availability_identity():
    item = caps.Capability('python:example', 'python', True, detail='1.0')
    original = caps.Capabilities((item,)).identity_dict()
    for changed in (replace(item, detail='2.0'), replace(item, available=False)):
        assert caps.Capabilities((changed,)).identity_dict() != original
    assert original == caps.Capabilities((item,)).as_dict()
