"""Actual figures-stage cache decisions follow effective role and transport code."""
from types import SimpleNamespace
from pathlib import Path
import json
import pytest

from aiforstudy.blocks.media import dependencies as D
from aiforstudy.providers.factory import configured_providers
from test_media_code_contracts import approved_case
from test_media_code_execution import mock_runtime, configure_stage


def setup_stage(tmp_path,monkeypatch,config):
    content,approval,request,program=approved_case()
    calls=mock_runtime(monkeypatch,program)
    stages,ctx=configure_stage(tmp_path,monkeypatch,content,approval,request,{})
    ctx.workspace.config_json=lambda name:config
    def refresh():
        ctx.services.providers=configured_providers(SimpleNamespace(profile=SimpleNamespace(value='release')),ctx.workspace)
    refresh()
    # Restore the real descriptor: only network/render/reviewer work is offline.
    monkeypatch.setattr(stages,'resource_dependencies',D.resource_dependencies)
    def execute():
        assert stages.figures_stage(ctx).quality_state.value=='accepted'
    execute();execute()
    assert calls=={'generate':1,'render':['final'],'review':1}
    return ctx,calls,refresh,execute


@pytest.mark.parametrize('change',['global_endpoint','author_route','judge_route','judge_version'])
def test_effective_role_change_invalidates_outer_figures_reuse(tmp_path,monkeypatch,change):
    monkeypatch.setenv('DEEPSEEK_API_KEY','offline-test-secret')
    monkeypatch.setenv('AIFORSTUDY_MODEL_BASE_URL','http://127.0.0.1:8001/v1')
    config={'provider':'deepseek','base_url':'http://127.0.0.1:8000/v1','roles':{
        'figures':{'model':'author','effort':'high'},'judge':{'model':'judge','max_tokens':100}}}
    ctx,calls,refresh,execute=setup_stage(tmp_path,monkeypatch,config)
    if change=='global_endpoint':
        monkeypatch.setenv('AIFORSTUDY_MODEL_BASE_URL','http://127.0.0.1:8002/v1');refresh()
    elif change=='judge_version':
        from aiforstudy.providers.transport import RealTransport
        route=ctx.services.providers.llm.for_role('judge')
        cache_descriptor=RealTransport.cache_identity.fget
        monkeypatch.setattr(RealTransport,'cache_identity',property(lambda self:{**cache_descriptor(self),'version':2 if self is route else 1}))
    else:
        role='figures' if change=='author_route' else 'judge'
        config['roles'][role].update(provider='local-compatible',base_url='http://localhost:8099/v1')
        refresh()
    execute()
    expected=2 if change in ('global_endpoint','author_route') else 1
    assert calls=={'generate':expected,'render':['final']*expected,'review':2}
    execute();assert calls['review']==2
    for role in ('figures','judge'):
        identity=D.model_role_identity(ctx,role)
        assert 'offline-test-secret' not in json.dumps(identity)
        selected=ctx.services.providers.llm.for_role(role)
        assert identity['endpoint']==selected.cache_identity['endpoint']


@pytest.mark.parametrize('component',['factory.py','routing.py','local_compatible.py','dsh_account.py','dsh_bridge.cjs'])
def test_transport_source_edit_invalidates_real_resource_reuse(tmp_path,monkeypatch,component):
    from support.runtime_tree import copy_runtime
    package=tmp_path/'package';copy_runtime(package)
    monkeypatch.setattr(D,'__file__',str(package/'blocks/media/dependencies.py'))
    config={'provider':'deepseek-account','roles':{'figures':{'model':'author'},'judge':{'model':'judge'}}}
    ctx,calls,refresh,execute=setup_stage(tmp_path,monkeypatch,config)
    path=package/'providers'/component
    path.write_bytes(path.read_bytes()+b'\n// offline identity probe\n')
    execute()
    assert calls=={'generate':2,'render':['final','final'],'review':2}
