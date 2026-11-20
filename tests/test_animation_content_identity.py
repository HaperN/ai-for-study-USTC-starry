"""The live typed animation route binds input identity without claiming approval."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from aiforstudy.blocks.content.review_outcome import content_identity
from aiforstudy.core.jsonio import write_json_atomic
from aiforstudy.errors import ContractError
from test_animation_teaching_bindings import derivative_animation_case
from test_media_plan_stage import ctx


def identity_case():
    context, _ = derivative_animation_case()
    content = context._data()['content']
    return content, content_identity(content, {'entries': []}, policy_digest='offline-policy'), context.request


def test_typed_animation_accepts_identity_and_still_requires_real_render_environment(tmp_path, monkeypatch):
    from aiforstudy.pipeline import media as M, stage_io as T
    content, identity, request = identity_case()
    monkeypatch.setattr(M.animation, 'render_environment', lambda _: SimpleNamespace(available=False, detail='offline renderer unavailable'))
    monkeypatch.setattr(T, '_gateway_client', lambda *a, **k: pytest.fail('unexpected paid call'))
    pairs = [('body', SimpleNamespace(request_id=request['request_id']), request)]
    rows, payloads, tickets, directives = M.fulfill(ctx(tmp_path, 'figures'), pairs, content, {}, {}, content_approval=identity)
    assert rows[0]['reason'] == 'CapabilityError: offline renderer unavailable'
    assert rows[0]['state'] == 'blocked'
    assert not payloads and not tickets and not directives
    assert 'approval' not in rows[0]


def test_runner_animation_identity_includes_bound_content_and_current_environment(tmp_path, monkeypatch):
    from aiforstudy.pipeline import media as M, media_stages as S, stage_io as T
    from aiforstudy.pipeline.runner import Runner
    content, identity, request = identity_case()
    context = ctx(tmp_path, 'figures')
    context.workspace.config_json = lambda _: {'roles': {'judge': {'model': 'offline'}}}
    context.services.workspace = context.workspace
    context.services.context = context.context
    context.services.capabilities = {}
    runner = object.__new__(Runner)
    runner.services = context.services
    runner.store = context.services.store
    runner._request_obj = runner._plan_obj = SimpleNamespace()
    write_json_atomic(tmp_path / 'products/media_plan/U.json', {'payload': {'requests': [request]}})
    monkeypatch.setattr(S, '_load_content', lambda _: (content, identity))
    monkeypatch.setattr(S, '_capabilities', lambda _: {})
    monkeypatch.setattr(T, '_gateway_client', lambda *a, **k: pytest.fail('unexpected paid call'))
    environment = {'environment': 'offline-1'}
    monkeypatch.setattr(M, '_animation_runtime_identity', lambda _: deepcopy(environment))
    first = runner._media_resource_identities(context.node)
    assert 'unavailable' not in first, first
    assert set(first[request['request_id']]) == {'generation_dependency_hash', 'review_dependency_hash'}
    environment['environment'] = 'offline-2'
    second = runner._media_resource_identities(context.node)
    assert second[request['request_id']]['generation_dependency_hash'] != first[request['request_id']]['generation_dependency_hash']
    assert second[request['request_id']]['review_dependency_hash'] == first[request['request_id']]['review_dependency_hash']


def test_known_error_content_is_no_longer_blocked_by_a_duplicate_content_gate(tmp_path, monkeypatch):
    """D1：媒体阶段不再因为"正文确定有错误"就整段拒绝。

    那道闸门与审核层重复：正文能不能用，text_audit 已经判过；"这一节还有问题"由上游
    outcome 继续传播（`inherit_outcome(media_outcome(row), upstream)`）并在阅读页
    problem_summary 里列出。所以这里只断言**那条理由不再出现**；
    若仍抛错，必须是别的原因（例如来源不一致）。
    """
    from aiforstudy.pipeline import content_stages as C, media_stages as M
    content, identity, _ = identity_case()
    monkeypatch.setattr(C, 'load_content', lambda _: (content, identity))
    monkeypatch.setattr(M, '_content_outcome', lambda *_: {'status': 'known_error', 'reasons': ['已知数学错误']})
    try:
        loaded, _approval = M._load_content(ctx(tmp_path, 'figures'))
    except ContractError as exc:
        assert '已知数学错误' not in str(exc), '那道"确定错误不能作数"的闸门必须已经删除'
        return
    assert loaded == content


def test_runner_code_animation_uses_the_same_dependencies_as_code_execution(tmp_path, monkeypatch):
    from aiforstudy.pipeline import media as M, media_stages as S
    from aiforstudy.pipeline.runner import Runner
    content, identity, request = identity_case()
    context = ctx(tmp_path, 'figures')
    context.services.workspace = context.workspace
    context.services.context = context.context
    context.services.capabilities = {}
    runner = object.__new__(Runner)
    runner.services, runner.store = context.services, context.services.store
    runner._request_obj = runner._plan_obj = SimpleNamespace()
    write_json_atomic(tmp_path / 'products/media_plan/U.json', {'payload': {'requests': [request]}})
    capabilities = {'code_generation': 'GeneratedMediaCodeV1'}
    monkeypatch.setattr(S, '_capabilities', lambda _: capabilities)
    monkeypatch.setattr(S, '_load_content', lambda _: (content, identity))
    expected = {'generation_dependency_hash': 'sha256:code-generation', 'review_dependency_hash': 'sha256:code-review'}
    calls = []
    def code_dependencies(c, req, caps):
        calls.append((req, caps))
        return expected
    monkeypatch.setattr(S, 'resource_dependencies', code_dependencies)
    monkeypatch.setattr(M, 'cache_identity', lambda *a, **k: pytest.fail('code animation entered typed backend'))
    assert runner._media_resource_identities(context.node) == {request['request_id']: expected}
    assert calls == [(request, capabilities)]
