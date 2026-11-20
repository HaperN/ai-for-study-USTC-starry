from types import SimpleNamespace
import json

from aiforstudy.core.review_mode import review_mode
from aiforstudy.pipeline import content_stages as stage, stage_io
from test_content_generation_v3 import context
from test_balanced_content_review import Client, IDS, verdicts


def artifact(tmp_path):
    return json.loads((tmp_path / 'work/products/text_audit/U.json').read_text())['payload']


def test_fail_returns_current_body_and_reason_to_one_repair_batch(tmp_path, monkeypatch):
    bad = {'content': {'title': '复数', 'body_markdown': '例题末步：1+1=3。'},
           'media_requirements': {'requests': [], 'decisions': []}}
    fixed = {'content': {'title': '复数', 'body_markdown': '例题末步：1+1=2。'}}
    failed = verdicts()
    failed['verdicts'][0].update(status='fail', reason='正文末句“1+1=3”错误；按加法应为2，请改为1+1=2。')
    client = Client([bad, failed, fixed, verdicts()])
    monkeypatch.setattr(stage_io, '_gateway_client', lambda *a, **k: (client, 'offline'))
    monkeypatch.setattr(stage, '_prepare_source_context', lambda *a, **k: (_ for _ in ()).throw(AssertionError('old inventory')))
    monkeypatch.setattr(stage, 'check_content', lambda *a, **k: (_ for _ in ()).throw(AssertionError('old schema gate')))
    with review_mode('balanced'):
        assert stage.lesson_stage(context(tmp_path)).execution_state.value == 'succeeded'
        audit = stage.text_audit_stage(context(tmp_path, 'text_audit'))
        assert audit.execution_state.value == 'succeeded', audit.error
        assert len(audit.repair_requests) == 1
        assert artifact(tmp_path)['review_outcome']['status'] == 'known_error'
        ticket_ids = audit.repair_requests[0].ticket_ids
        ctx = context(tmp_path)
        ctx.repair_batch = SimpleNamespace(batch_id='repair-1', round=1,
            jobs=[SimpleNamespace(owner='lesson', kind='text_content', ticket_ids=ticket_ids, artifact_id='lesson:U')])
        result = stage.lesson_stage(ctx)
        assert result.execution_state.value == 'succeeded', result.error
        request = json.loads(client.calls[2][0][1]['content'])
        assert request['frozen_context']['content']['body_markdown'] == bad['content']['body_markdown']
        assert request['frozen_context']['repair_tickets'][0]['problem'] == failed['verdicts'][0]['reason']
        result = stage.text_audit_stage(context(tmp_path, 'text_audit'))
        assert result.quality_state.value == 'accepted', result.error
        assert artifact(tmp_path)['review_outcome']['status'] == 'verified'
        assert not result.repair_requests
        assert stage.load_content(context(tmp_path))[0]['body_markdown'] == fixed['content']['body_markdown']
    assert [c[1]['tag'].split(':')[0] for c in client.calls] == ['content-author', 'content-review', 'content-repair', 'content-review']


def test_missing_verdict_and_format_exhaustion_do_not_dispatch_content_or_evidence(tmp_path, monkeypatch):
    client = Client([{'body_markdown': '可供阅读的正文'}, {'verdicts': []}])
    monkeypatch.setattr(stage_io, '_gateway_client', lambda *a, **k: (client, 'offline'))
    with review_mode('balanced'):
        assert stage.lesson_stage(context(tmp_path)).execution_state.value == 'succeeded'
        result = stage.text_audit_stage(context(tmp_path, 'text_audit'))
    assert result.execution_state.value == 'succeeded'
    assert not result.repair_requests
    assert artifact(tmp_path)['review_outcome']['status'] == 'unverified'
    assert len(client.calls) == 2


def test_author_json_exhaustion_stops_after_two_corrections_without_outer_repair(tmp_path, monkeypatch):
    client = Client(['{"body_markdown":'] * 3)
    monkeypatch.setattr(stage_io, '_gateway_client', lambda *a, **k: (client, 'offline'))
    with review_mode('balanced'):
        result = stage.lesson_stage(context(tmp_path))
        replay = stage.lesson_stage(context(tmp_path))
    assert len(client.calls) == 3
    assert result.execution_state.value == replay.execution_state.value == 'blocked'
    assert not result.repair_requests and not replay.repair_requests
    assert len(list((tmp_path / 'work/products/content/U/responses').glob('*.json'))) == 3


def test_review_format_exhaustion_preserves_body_and_never_reopens_budget(tmp_path, monkeypatch):
    client = Client([{'body_markdown': '全部正文，应完整保留。'}, {'verdicts': False}, [], '"wrong envelope"'])
    monkeypatch.setattr(stage_io, '_gateway_client', lambda *a, **k: (client, 'offline'))
    with review_mode('balanced'):
        stage.lesson_stage(context(tmp_path))
        body = stage.load_content(context(tmp_path))[0]
        result = stage.text_audit_stage(context(tmp_path, 'text_audit'))
        replay = stage.text_audit_stage(context(tmp_path, 'text_audit'))
        assert stage.load_content(context(tmp_path))[0] == body
    assert result.execution_state.value == replay.execution_state.value == 'succeeded'
    assert not result.repair_requests and not replay.repair_requests
    assert artifact(tmp_path)['review_failure']['kind'] == 'protocol'
    assert artifact(tmp_path)['review_outcome']['status'] == 'unverified'
    assert len(client.calls) == 4
    assert len(list((tmp_path/'work/products/content/U/review_responses').glob('*.json'))) == 3


def test_review_transport_error_does_not_become_content_defect(tmp_path, monkeypatch):
    client = Client([{'body_markdown': '仍可读取'}, RuntimeError('provider outage 503')])
    monkeypatch.setattr(stage_io, '_gateway_client', lambda *a, **k: (client, 'offline'))
    with review_mode('balanced'):
        stage.lesson_stage(context(tmp_path))
        result = stage.text_audit_stage(context(tmp_path, 'text_audit'))
    assert result.execution_state.value == 'blocked'
    assert 'provider outage 503' in result.error['reason']
    assert not result.repair_requests


def test_repair_json_exhaustion_keeps_last_body_without_new_outer_directive(tmp_path, monkeypatch):
    client = Client([{'body_markdown': '1+1=3。'}, verdicts('fail'), '{', '{', '{'])
    monkeypatch.setattr(stage_io, '_gateway_client', lambda *a, **k: (client, 'offline'))
    with review_mode('balanced'):
        stage.lesson_stage(context(tmp_path))
        original = stage.load_content(context(tmp_path))[0]
        audit = stage.text_audit_stage(context(tmp_path, 'text_audit'))
        assert len(audit.repair_requests) == 1
        assert len(audit.repair_requests[0].ticket_ids) == 1
        assert artifact(tmp_path)['check_reports'][0]['responses'][0] == verdicts('fail')
        ctx = context(tmp_path)
        ctx.repair_batch = SimpleNamespace(batch_id='repair-1', round=1,
            jobs=[SimpleNamespace(owner='lesson', kind='text_content',
                ticket_ids=audit.repair_requests[0].ticket_ids, artifact_id='lesson:U')])
        result = stage.lesson_stage(ctx)
        assert stage.load_content(context(tmp_path))[0] == original
    assert result.execution_state.value == 'blocked'
    assert not result.repair_requests
    assert len(client.calls) == 5


def test_media_content_findings_reuse_body_budget_and_pass_original_problem(tmp_path, monkeypatch):
    from aiforstudy.core.jsonio import write_json_atomic
    from aiforstudy.pipeline.execution_control import ExecutionPolicy
    from aiforstudy.pipeline.repair import RepairCoordinator, StopDecision, UnitRepairState
    problem = {'target_artifact_kind':'content', 'status':'fail',
               'reason':'正文例2末式写成 y=-x；实际图与代数均应为 y=x，请修正正文末式。'}
    client = Client([{'body_markdown':'例2末式 y=-x。'}, verdicts(),
                     {'body_markdown':'例2末式 y=x。'}, verdicts()])
    monkeypatch.setattr(stage_io, '_gateway_client', lambda *a, **k: (client, 'offline'))
    with review_mode('balanced'):
        stage.lesson_stage(context(tmp_path))
        stage.text_audit_stage(context(tmp_path, 'text_audit'))
        content = stage.load_content(context(tmp_path))[0]
        tickets, directives, routes = stage.balanced_media_content_repairs(context(tmp_path, 'figures'), content, [problem])
        assert len(tickets) == len(directives) == len(routes) == 1
        assert directives[0].artifact_hashes.keys() == {'lesson:U'}
        assert routes[0]['target_artifact_kind'] == 'content'
        assert routes[0]['origin_stage'] == 'figures'
        assert tickets[0]['problem'] == problem['reason']
        state = UnitRepairState(unit_id='U', content_rounds_by_artifact={'lesson:U':2})
        stop = RepairCoordinator(ExecutionPolicy(), max_content_rounds=2).plan(directives, state)
        assert isinstance(stop, StopDecision) and stop.stopped_by == 'content_rounds_exhausted'
        assert state.content_rounds_by_artifact == {'lesson:U':2}
        write_json_atomic(tmp_path/'work/products/figures/U.json', {'payload':{'tickets':tickets}})
        ctx = context(tmp_path)
        ctx.repair_batch = SimpleNamespace(batch_id='media-body-1', round=1,
            jobs=[SimpleNamespace(owner='lesson', kind='text_content', ticket_ids=directives[0].ticket_ids, artifact_id='lesson:U')])
        result = stage.lesson_stage(ctx)
        assert result.execution_state.value == 'succeeded', result.error
        request = json.loads(client.calls[2][0][1]['content'])['frozen_context']
        assert request['content']['body_markdown'] == '例2末式 y=-x。'
        assert request['repair_tickets'][0]['problem'] == problem['reason']
        result = stage.text_audit_stage(context(tmp_path, 'text_audit'))
        assert result.quality_state.value == 'accepted', result.error
        request = json.loads(client.calls[3][0][1]['content'])['frozen_context']
        assert request['repair_context']['previous_tickets'][0]['problem'] == problem['reason']
        assert tickets[0]['ticket_id'] in artifact(tmp_path)['closed_ticket_ids']
        # A fresh media finding on this new revision reopens the same identity,
        # without restoring its already consumed lesson budget.
        current = stage.load_content(context(tmp_path))[0]
        fresh, _, _ = stage.balanced_media_content_repairs(context(tmp_path, 'figures'), current, [problem])
        write_json_atomic(tmp_path/'work/products/figures/U.json', {'payload':{'tickets':fresh}})
        loaded, _ = stage._load_tickets(context(tmp_path))
        assert loaded[0].target['artifact_hash'] == fresh[0]['target']['artifact_hash']


def test_media_unknown_or_non_content_problem_does_not_rewrite_body(tmp_path):
    with review_mode('balanced'):
        for problem in ({'target_artifact_kind':'content','status':'unknown','reason':'无法确认'},
                        {'target_artifact_kind':'content','status':'needs_review','reason':'答复未完成'},
                        {'target_artifact_kind':'content','reason':'缺判词'},
                        {'target_artifact_kind':'visual','status':'fail','reason':'字太小'}):
            assert stage.balanced_media_content_repairs(context(tmp_path,'figures'),
                {'unit_id':'U','body_markdown':'原正文'},[problem]) == ([], (), ())
