"""Rule implementation changes revalidate, rather than erase, repaired content."""
from copy import deepcopy
import pytest

from aiforstudy.core.jsonio import read_json, write_json_atomic
from aiforstudy.pipeline.engine import resume_request
from aiforstudy.pipeline.services import Providers
from aiforstudy.pipeline.runner import Runner
from support.runner_v5 import run_book, TeachingTransport, UNIT


def change_implementation(monkeypatch):
    original = Runner._stage_runtime_identity
    def changed(self, stage):
        result = deepcopy(original(self, stage))
        if stage in ('lesson', 'text_audit'):
            result['regression_rule_revision'] = 'new-validation'
        return result
    monkeypatch.setattr(Runner, '_stage_runtime_identity', changed)


def test_rule_change_keeps_latest_repaired_candidate_and_rounds(tmp_path, monkeypatch):
    result, store, engine, transport = run_book(tmp_path, transport=TeachingTransport(reject_first=True))
    assert result.status == 'succeeded'
    head_path = store.work / f'products/content/{UNIT}/current.json'
    head = read_json(head_path)
    original = head_path.read_bytes()
    repaired = read_json(head['content_ref']['path'])
    assert repaired['revision'] == 2
    before_rounds = deepcopy(store.load_state()['repair_states'][UNIT]['content_rounds_by_artifact'])
    before_calls = transport.author_calls, transport.patch_calls
    change_implementation(monkeypatch)
    again, _, _ = resume_request(engine.request, store.run_id, engine.services.workspace,
        providers=Providers(llm=transport), until='text_audit')
    assert again.status == 'succeeded', again.recovery_hint
    assert head_path.read_bytes() == original
    assert read_json(head['content_ref']['path']) == repaired
    assert (transport.author_calls, transport.patch_calls) == before_calls
    assert store.load_state()['repair_states'][UNIT]['content_rounds_by_artifact'] == before_rounds
    lesson = read_json(store.work / f'products/lesson/{UNIT}.json')['payload']
    assert lesson['recheck_only'] is True and lesson['model_calls'] == 0


@pytest.mark.parametrize('changed', ['source', 'author_request', 'content'])
def test_revalidation_never_grants_changed_candidate_or_request(tmp_path, monkeypatch, changed):
    result, store, engine, transport = run_book(tmp_path)
    assert result.status == 'succeeded'
    base = store.work / f'products/content/{UNIT}'
    before = (base / 'current.json').read_bytes()
    if changed == 'source':
        context = engine.services.context
        chapters = read_json(context.parse / 'chapters.json')
        source = next(u['original_text'] for u in chapters['lesson_units'] if u['unit_id'] == UNIT)
        (context.parse / source).write_text('完全不同的教材。', encoding='utf-8')
    elif changed == 'author_request':
        from aiforstudy.blocks.content import prompt
        original = prompt.build_content_messages
        def messages(role, *args, **kwargs):
            rows = original(role, *args, **kwargs)
            if role == 'author': rows[0]['content'] += '\n新的教学指令'
            return rows
        monkeypatch.setattr(prompt, 'build_content_messages', messages)
        monkeypatch.setattr('aiforstudy.blocks.content.generation.build_content_messages', messages)
    else:
        path = read_json(base / 'current.json')['content_ref']['path']
        value = read_json(path); value['summary'] = '未经授权的改动'
        write_json_atomic(path, value)
    change_implementation(monkeypatch)
    from aiforstudy.errors import ContractError
    try:
        again, _, _ = resume_request(engine.request, store.run_id, engine.services.workspace,
            providers=Providers(llm=transport), until='text_audit')
    except ContractError:
        return
    assert again.status != 'succeeded' or not (
        read_json(store.work / f'products/lesson/{UNIT}.json')['payload'].get('recheck_only'))
    if changed != 'source':
        assert (base / 'current.json').read_bytes() == before
