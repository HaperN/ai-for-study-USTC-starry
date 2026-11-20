import pytest
from aiforstudy.pipeline.graph import CANONICAL_STAGES, GRAPH_VERSION, stage, dispatch_quality_gate
from aiforstudy.pipeline.stubs import smoke_registry, replay_registry
from aiforstudy.pipeline.registry import release_registry
from aiforstudy.core.usage import BUCKETS, UsageRecord


def test_v5_order_and_registered_profiles():
    assert GRAPH_VERSION == '7.0'
    names = [s.name for s in CANONICAL_STAGES]
    assert names[names.index('lesson'):] == ['lesson', 'text_audit', 'media_plan', 'figures', 'audit', 'interaction', 'render', 'delivery']
    assert set(smoke_registry().names()) == set(names)
    assert set(replay_registry(None).names()) == set(names)
    assert set(release_registry().names()) == set(names)


def test_candidate_edges_keep_execution_dependencies_without_global_approval_gate():
    assert dispatch_quality_gate('text_audit', 'lesson') is None
    assert dispatch_quality_gate('audit', 'figures') is None
    for consumer, producer in [('media_plan','text_audit'),('figures','media_plan'),('interaction','audit'),('render','text_audit'),('render','audit'),('render','interaction'),('delivery','render')]:
        assert dispatch_quality_gate(consumer, producer) is None
    assert 'media_plan' in stage('figures').requires
    assert {'text_audit','audit','interaction'} <= set(stage('render').requires)


def test_media_plan_has_separate_usage_bucket():
    assert 'media_plan' in BUCKETS
    record = UsageRecord(run_id='v4', bucket='media_plan', role='figures', stage='media_plan', tokens=12)
    assert record.as_dict()['tokens'] == 12


def test_v5_never_uses_legacy_terminal_certificate_for_quality(monkeypatch):
    from types import SimpleNamespace
    from aiforstudy.pipeline.runner import Runner
    r=object.__new__(Runner); r.graph=SimpleNamespace(graph_version='7.0',by_id=lambda:{})
    monkeypatch.setattr(r,'_final_audit_evidence',lambda: {'lesson:U':{'quality_state':'accepted'}})
    assert r._final_quality_overrides()=={}


@pytest.mark.parametrize("until", ["media_plan", "delivery", None])
def test_default_registry_reaches_media_plan_only_after_current_content_receipt(tmp_path, monkeypatch, until):
    from support.runner_v5 import setup_book, TeachingTransport, _request
    from aiforstudy.pipeline.engine import run_request
    from aiforstudy.pipeline.services import Providers
    transport=TeachingTransport();workspace=setup_book(tmp_path)
    result,store,_=run_request(_request(),workspace,providers=Providers(llm=transport),until=until)
    assert result.status=='succeeded',result.recovery_hint
    assert store.node_states()['text_audit:CH01-S01'].quality_state.value=='accepted'
    assert store.node_states()['media_plan:CH01-S01'].quality_state.value=='accepted'
    if until=='media_plan':
        assert 'figures:CH01-S01' not in store.node_states()
    else:
        assert store.node_states()['figures:CH01-S01'].execution_state.value=='not_applicable'
        assert store.node_states()['render:CH01'].quality_state.value=='accepted'
        assert store.node_states()['delivery'].quality_state.value=='accepted'
    assert transport.author_calls==1
