"""Real preparation consumers must recheck changed run-local source products."""
import json

import pytest

from aiforstudy.pipeline.engine import resume_request
from aiforstudy.pipeline.services import Providers
from support.runner_v5 import UNIT, run_book


@pytest.mark.parametrize('change,consumer', [
    ('extra-part', 'concepts'), ('missing-part', 'concepts'),
    ('concept-reference', 'concepts'), ('body-ambiguity', 'structure'),
])
def test_changed_prepared_products_cannot_reuse_prior_proof(tmp_path, change, consumer):
    result, store, engine, transport = run_book(tmp_path)
    assert result.status == 'succeeded'
    root = engine.services.context.parse
    if change == 'extra-part':
        (root / 'concepts.parts/UNKNOWN.json').write_text(json.dumps({'unit_id':'UNKNOWN'}))
    elif change == 'missing-part':
        (root / 'concepts.parts' / (UNIT + '.json')).unlink()
    elif change == 'concept-reference':
        path = root / 'concepts.json'
        payload = json.loads(path.read_text())
        payload['concepts'].append({'id':'bad', 'name':'bad', 'type':'definition',
                                    'occurrences':[{'unit_id':'UNKNOWN'}]})
        path.write_text(json.dumps(payload))
    else:
        path = root / 'body.json'
        payload = json.loads(path.read_text())
        payload['unconfirmed'].append({'body_node':UNIT, 'reason':'unresolved offline evidence'})
        path.write_text(json.dumps(payload))
    again, resumed, _ = resume_request(engine.request, store.run_id, engine.services.workspace,
        providers=Providers(llm=transport), until='concepts')
    assert resumed.node_states()[consumer].execution_state.value != 'cached', again.as_dict()
    if change in ('extra-part', 'concept-reference'):
        # 用户 2026-10-20 定：引用对不上只记警告（指向不存在单元的引用不会生成任何内容）。
        findings = resumed.node_states()[consumer].findings
        assert any(f.get('code') == 'reference-missing' for f in findings), findings
    else:
        assert again.status != 'succeeded', again.as_dict()


def test_preparation_dependencies_do_not_include_files_generated_later(tmp_path):
    result, store, engine, transport = run_book(tmp_path)
    assert result.status == 'succeeded'
    calls = len(transport.calls)
    before = {name:row.attempt for name,row in store.node_states().items()}
    again, resumed, _ = resume_request(engine.request, store.run_id, engine.services.workspace,
        providers=Providers(llm=transport), until='text_audit')
    assert again.status == 'succeeded', again.as_dict()
    assert len(transport.calls) == calls
    assert {name:row.attempt for name,row in resumed.node_states().items()} == before


@pytest.mark.parametrize('bad_reference', [False, True])
def test_new_concept_output_is_checked_after_generation_without_invalidating_body(tmp_path, monkeypatch, bad_reference):
    from types import SimpleNamespace
    from aiforstudy.pipeline import preparation_checks
    from aiforstudy.pipeline.engine import run_request
    from support.runner_v5 import setup_book, TeachingTransport
    from support.book_factory import _request
    workspace = setup_book(tmp_path)
    part = workspace.root / 'book/parse/concepts.parts' / (UNIT + '.json')
    saved = part.read_text(); part.unlink()
    original = preparation_checks._legacy
    generated = []
    def extract(parse, **kwargs):
        generated.append(1)
        (parse / 'concepts.parts' / (UNIT + '.json')).write_text(saved)
        if bad_reference:
            path = parse / 'concepts.json'; value = json.loads(path.read_text())
            value['concepts'].append({'id':'bad', 'name':'bad', 'type':'definition',
                                      'occurrences':[{'unit_id':'UNKNOWN'}]})
            path.write_text(json.dumps(value))
        return {'status':'offline generated'}
    monkeypatch.setattr(preparation_checks, '_legacy', lambda name:
        SimpleNamespace(extract_concepts=extract) if name == 'blocks.parse.concepts.extract_concepts'
        else original(name))
    transport = TeachingTransport()
    result, store, engine = run_request(_request(), workspace, providers=Providers(llm=transport), until='concepts')
    assert generated == [1]
    if bad_reference:
        # 用户 2026-10-20 定：只记警告，不拦概念阶段。
        assert any(f.get('code') == 'reference-missing' for f in store.node_states()['concepts'].findings)
    else:
        assert result.status == 'succeeded', result.as_dict()
        before = {name:row.attempt for name,row in store.node_states().items()}
        again, resumed, _ = resume_request(engine.request, store.run_id, workspace,
            providers=Providers(llm=transport), until='concepts')
        assert again.status == 'succeeded', again.as_dict()
        assert generated == [1]
        assert {name:row.attempt for name,row in resumed.node_states().items()} == before
