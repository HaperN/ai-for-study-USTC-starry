"""User-confirmed outer retry, merged review, and content-only repair behavior."""
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from support.content_case import teaching_case, authoring
from test_content_generation_v3 import context as stage_context
from test_content_repair_rewrite_rounds import setup, open_repair_batch, REPAIR_TAG
from aiforstudy.blocks.content.source_context import SourceContext
from aiforstudy.blocks.content.generation import _validate_content_only
from aiforstudy.pipeline import content_stages as stages, stage_io as T


def batch(number, tickets):
    return SimpleNamespace(batch_id=f'outer-{number}', round=number, jobs=[SimpleNamespace(
        owner='lesson', kind='text_content', ticket_ids=tickets)])


@pytest.mark.parametrize('media', [None, {}, {'decisions': [{'kind': 'none'}]}, 'invalid'])
def test_content_rewrite_does_not_validate_media(media):
    content, raw = teaching_case()
    candidate = authoring(content)
    candidate['media_requirements'] = media
    result = _validate_content_only(candidate, SourceContext.model_validate(raw))
    assert result['content']['body_markdown'] == content['body_markdown']


def test_no_draft_outer_round_has_previous_response_and_new_call(tmp_path, monkeypatch):
    client, _ = setup(tmp_path, monkeypatch, {'content-author:U': ['incomplete', 'repair']})
    ctx = stage_context(tmp_path)
    rejected = stages.lesson_stage(ctx)
    assert rejected.execution_state.value == 'blocked'
    assert rejected.repair_requests
    ctx.repair_batch = batch(1, list(rejected.repair_requests[0].ticket_ids))
    fixed = stages.lesson_stage(ctx)
    assert fixed.execution_state.value == 'succeeded', fixed.error
    requests = [m for tag,m in zip(client.calls,client.requests) if tag == 'content-author:U']
    assert len(requests) == 2
    assert 'original_response' in json.dumps(requests[1])
    assert 'summary' in json.dumps(requests[1])
    # Same reserved round replays its accepted response without another API call.
    again = stages.lesson_stage(ctx)
    assert again.execution_state.value == 'succeeded', again.error
    assert client.calls.count('content-author:U') == 2


def test_rejected_rewrite_keeps_body_and_passes_feedback_to_next_round(tmp_path, monkeypatch):
    ctx, client, tickets = open_repair_batch(tmp_path, monkeypatch,
        {'content-author:U': ['author'], REPAIR_TAG: ['incomplete', 'repair']})
    ids = [t['ticket_id'] for t in tickets]
    ctx.repair_batch = batch(1, ids)
    before = stages.load_content(ctx)[0]
    result = stages.lesson_stage(ctx)
    assert result.execution_state.value == 'blocked'
    feedback = next(row['feedback'] for row in result.findings if row['code']=='CONTENT_REJECTED')
    assert feedback['original_response']['content']['summary'] == 123
    assert feedback['ticket_refs'] == ids
    assert stages.load_content(ctx)[0] == before
    ctx.repair_feedback = feedback
    ctx.repair_batch = batch(2, ids)
    result = stages.lesson_stage(ctx)
    assert result.execution_state.value == 'succeeded', result.error
    request = json.loads(client.requests[-1][1]['content'])['frozen_context']
    assert request['previous_rejection']['original_response']['content']['summary'] == 123
    assert request['content']['body_markdown'] == before['body_markdown']


def test_machine_failure_still_gets_independent_model_review(tmp_path, monkeypatch):
    client, _ = setup(tmp_path, monkeypatch, {'content-author:U': ['repair']})
    ctx = stage_context(tmp_path)
    assert stages.lesson_stage(ctx).execution_state.value == 'succeeded'
    content, head = stages.load_content(ctx)
    content = deepcopy(content); content['summary'] = ''
    monkeypatch.setattr(stages, 'load_content', lambda *a, **k: (content, head))
    ctx.node.stage = 'text_audit'
    result = stages.text_audit_stage(ctx)
    assert result.execution_state.value == 'succeeded', result.error
    review_requests = [m for tag,m in zip(client.calls,client.requests) if tag.startswith('content-review:')]
    assert review_requests, 'Ordinary machine findings must not skip model review'
    assert 'machine_findings' in json.dumps(review_requests)
    payload = json.loads((stages._root(ctx)/'text_audit/U.json').read_text())['payload']
    assert payload['review_outcome']['status'] == 'known_error'
    assert len(payload['check_reports']) == 2
    assert result.repair_requests


def test_unknown_gets_content_repair_without_claiming_known_error(tmp_path, monkeypatch):
    client, _ = setup(tmp_path, monkeypatch, {'content-author:U': ['repair']})
    original = client._verdicts
    def unknown(payload):
        rows = original(payload)
        for row in rows:
            row.update(status='unknown', reason='证据不足，请补充当前步骤的条件和依据。')
        return rows
    client._verdicts = unknown
    ctx = stage_context(tmp_path)
    assert stages.lesson_stage(ctx).execution_state.value == 'succeeded'
    ctx.node.stage = 'text_audit'
    result = stages.text_audit_stage(ctx)
    assert result.execution_state.value == 'succeeded', result.error
    payload = json.loads((stages._root(ctx)/'text_audit/U.json').read_text())['payload']
    assert payload['review_outcome']['status'] == 'unverified'
    assert result.repair_requests
    assert all(d.owner == 'lesson' for d in result.repair_requests)


def test_changed_body_with_same_verdict_uses_available_rounds(tmp_path):
    from test_repair_runtime import _make_emitter, _engine_with, _mk, _events, UNIT
    from aiforstudy.pipeline.engine import execute
    emitter = _make_emitter([[_mk()]], always_request=True)
    engine, _ = _engine_with(emitter, tmp_path, until='lesson')
    _, store = execute(engine)
    batches = [e for e in _events(store,'repair.batch.planned') if e.scope_key==UNIT]
    stops = [e for e in _events(store,'repair.stopped') if e.scope_key==UNIT]
    assert len(batches) == 2
    assert stops[-1].data['stopped_by'] == 'content_rounds_exhausted'


def test_body_repair_publishes_without_entering_any_media_validator(tmp_path, monkeypatch):
    ctx, client, tickets = open_repair_batch(tmp_path, monkeypatch,
        {'content-author:U': ['author'], REPAIR_TAG: ['repair']})
    before = stages.load_author_media(ctx, stages.load_content(ctx)[0])
    from aiforstudy.blocks.media import author_requirements as media
    def forbidden(*a, **kw):
        raise AssertionError('Body repair must defer all media validation')
    monkeypatch.setattr(media,'bind_author_requirements',forbidden)
    monkeypatch.setattr(media,'validate_author_artifact',forbidden)
    result = stages.lesson_stage(ctx)
    assert result.execution_state.value == 'succeeded', result.error
    head = json.loads((stages._root(ctx)/'content/U/current.json').read_text())
    carried = json.loads(__import__('pathlib').Path(head['author_media_ref']['path']).read_text())
    assert carried['requirements'] == before['requirements']


def test_source_inventory_rejection_reaches_new_outer_request(tmp_path, monkeypatch):
    client, _ = setup(tmp_path, monkeypatch, {'content-author:U': ['repair']})
    original = client.chat_json
    seen = []
    def once_bad(messages, **kw):
        reply = original(messages, **kw)
        if kw['tag'].startswith('content-source-inventory:'):
            seen.append(messages)
            if len(seen) == 1:
                reply['data']['obligations'][0]['source_excerpt'] = '不存在的来源句子'
        return reply
    client.chat_json = once_bad
    ctx = stage_context(tmp_path)
    result = stages.lesson_stage(ctx)
    assert result.execution_state.value == 'blocked'
    assert result.repair_requests
    assert result.findings[0]['feedback']['original_response']['obligations']
    ctx.repair_feedback=result.findings[0]['feedback']
    ctx.repair_batch = batch(1, list(result.repair_requests[0].ticket_ids))
    result = stages.lesson_stage(ctx)
    assert result.execution_state.value == 'succeeded', result.error
    assert len(seen) == 2
    assert '不存在的来源句子' in json.dumps(seen[1], ensure_ascii=False)
    author_request=next(m for tag,m in zip(client.calls,client.requests) if tag=='content-author:U')
    assert json.loads(author_request[-1]['content'])['frozen_context'].get('previous_rejection') is None


def test_invalid_media_repair_never_rewrites_body(tmp_path):
    from test_author_intent_pipeline import IntentTransport
    from support.runner_v5 import run_book
    from support.book_factory import UNIT
    class BadMedia(IntentTransport):
        def _answer(self, system, user):
            if json.JSONDecoder().raw_decode(user)[0].get('role_task') == 'author_media_repair':
                self.intent_patch_calls += 1
                return '{}'
            return super()._answer(system,user)
    transport = BadMedia()
    _,store,_,_ = run_book(tmp_path,transport=transport,until='media_plan')
    state = store.load_state()['repair_states'][UNIT]
    assert state['content_rounds_by_artifact'].get('lesson:'+UNIT,0) == 0
    assert 1 <= transport.intent_patch_calls <= 2


def test_unavailable_reviewer_does_not_invent_body_findings(tmp_path, monkeypatch):
    from test_decision_content_review_guard import audit, Reviewer
    result,payload = audit(tmp_path,monkeypatch,reviewer=Reviewer(TimeoutError('temporary timeout')))
    assert payload['review_outcome']['status'] == 'unverified'
    assert not result.repair_requests


@pytest.mark.parametrize('statuses', [('unknown','fail','unknown'), ('fail','unknown','unknown')])
def test_unknown_retains_same_body_confirmed_failure(tmp_path, monkeypatch, statuses):
    from test_decision_content_review_guard import audit, Reviewer
    from aiforstudy.blocks.audit.contracts import validate_ticket
    previous=[]; expected_known=False
    for status in statuses:
        result,payload = audit(tmp_path,monkeypatch,previous=previous,
            reviewer=Reviewer(failed_rule='SOURCE-FIDELITY' if status=='fail' else None))
        expected_known |= status == 'fail'
        assert payload['review_outcome']['status'] == ('known_error' if expected_known else 'unverified')
        previous=[validate_ticket(t) for t in payload['tickets']]


@pytest.mark.parametrize('damage',['missing','unknown_goal'])
def test_deferred_bad_media_gets_media_author_feedback_and_can_rewrite(tmp_path, monkeypatch,damage):
    from aiforstudy.pipeline import media_stages
    from aiforstudy.blocks.content.content_contracts import content_digest
    from aiforstudy.blocks.media.author_requirements import rewrite_author_requirements
    ctx,client,_ = open_repair_batch(tmp_path,monkeypatch,
        {'content-author:U':['author'],REPAIR_TAG:['repair']})
    content=stages.load_content(ctx)[0]; context=stages._frozen_context(ctx)
    original=stages.load_author_media(ctx,content)['requirements']
    damaged=None if damage=='missing' else deepcopy(original)
    if damaged:
        damaged['decisions'][0].update(target_id='UNKNOWN',goal_id='UNKNOWN')
    content,_,_=stages._publish_revision(ctx,content,context,author_media=damaged,
        author_response_hash=content_digest({'test':'author response'}),defer_media=True)
    monkeypatch.setattr(media_stages,'_content_not_passed',lambda _: '')
    monkeypatch.setattr(media_stages,'_load_content',lambda _: (content,{}))
    monkeypatch.setattr(media_stages,'_capabilities',lambda _: {})
    ctx.services.control=SimpleNamespace(check=lambda:None)
    ctx.node.stage='media_plan'
    result=media_stages.media_plan_stage(ctx)
    assert result.repair_requests, result.as_dict()
    assert all('author-media:U' in d.artifact_hashes for d in result.repair_requests)
    record=stages.load_author_media(ctx,content,allow_stale=True)
    payload=json.loads((stages._root(ctx)/'media_plan/U.json').read_text())['payload']
    repaired=rewrite_author_requirements(record,original,payload['tickets'],content)
    assert repaired['status']=='bound'


def test_capacity_failure_at_actual_lesson_entry_has_feedback(tmp_path,monkeypatch):
    from aiforstudy.blocks.content import generation_corrections as G, prompt
    ctx,client,_=open_repair_batch(tmp_path,monkeypatch,
        {'content-author:U':['author'],REPAIR_TAG:['repair']})
    before=len(client.calls)
    monkeypatch.setattr(prompt,'MAX_REQUEST_BYTES',1)
    monkeypatch.setattr(G,'MAX_REQUEST_BYTES',1)
    result=stages.lesson_stage(ctx)
    assert result.execution_state.value=='blocked'
    assert result.findings[0]['feedback']['errors'][0]['code']=='capacity'
    assert result.repair_requests and len(client.calls)==before


@pytest.mark.parametrize('failure_kind',['transport','capacity'])
def test_generation_infrastructure_failure_has_outer_feedback(tmp_path,monkeypatch,failure_kind):
    from aiforstudy.blocks.content import generation_corrections as G
    class Unavailable:
        def chat_json(self,*a,**kw):
            raise TimeoutError('temporary provider timeout')
    if failure_kind=='capacity': monkeypatch.setattr(G,'MAX_REQUEST_BYTES',1)
    path=tmp_path/'attempt.json'
    with pytest.raises(Exception):
        G.run_local_generation([{'role':'user','content':'generate'}],Unavailable(),lambda x:x,
            tag='content-author:U',correction_tag='unused',correction_journal=path)
    feedback=json.loads(path.read_text())['merged_feedback']
    assert feedback['kind']==failure_kind
    assert feedback['errors'][0]['passing_criterion']
