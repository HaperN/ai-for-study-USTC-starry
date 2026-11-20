from copy import deepcopy
import json
import sys
from types import ModuleType

from aiforstudy.blocks.content.content_policy import load_content_policy
from aiforstudy.blocks.content.generation import generate_content
from aiforstudy.blocks.content.source_context import SourceContext
from aiforstudy.core.review_mode import review_mode
from aiforstudy.pipeline import content_stages as stages
from support.content_case import teaching_case, authoring
from test_content_generation_v3 import context, scripted_client


EXPLANATION = {'concept_id': 'C-PREV', 'unit_id': 'U10', 'name': '模的三角不等式',
               'what': '模满足三角不等式。', 'why': '给出和的长度上界。',
               'markdown': '条件与结论的完整讲解。', 'steps': [], 'source_label': '定理1',
               'review_outcome': {'status': 'verified'}}


def _install_available(monkeypatch, callback):
    module = ModuleType('aiforstudy.blocks.content.theorem_index')
    module.available_theorem_explanations = callback
    monkeypatch.setitem(sys.modules, module.__name__, module)


def test_theorem_material_is_prompt_only_and_stable_during_json_correction(tmp_path):
    content, source = teaching_case()
    original = deepcopy(source)
    requests = []
    class Client:
        def chat_json(self, messages, **_kwargs):
            requests.append(deepcopy(messages))
            return '{"content":' if len(requests) == 1 else {'data': authoring(content)}
    with review_mode('balanced'):
        before = SourceContext.model_validate(source).digest
        result = generate_content(source, load_content_policy(), Client(),
            available_theorem_names=[EXPLANATION['name']], correction_journal=tmp_path/'journal.json')
        assert SourceContext.model_validate(source).digest == before
    assert source == original
    assert result['content']['source_context_hash'] == before
    assert len(requests) == 2
    for messages in requests[:1]:
        frozen = json.loads(messages[1]['content'])['frozen_context']
        assert frozen['available_theorem_names'] == [EXPLANATION['name']]
        assert 'available_theorem_explanations' not in frozen
        assert EXPLANATION['markdown'] not in json.dumps(frozen, ensure_ascii=False)
    correction = json.loads(requests[1][-1]['content'])
    assert correction['original_response'] == '{"content":'
    assert 'frozen_context' not in correction
    assert 'available_theorem_names' not in correction
    assert EXPLANATION['markdown'] not in json.dumps(correction, ensure_ascii=False)


def test_author_journal_freezes_completed_material_in_textbook_order(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    chapters = json.loads((ctx.context.parse/'chapters.json').read_text())
    chapters['lesson_units'][:0] = [{'unit_id': 'U10'}, {'unit_id': 'U2'}]
    (ctx.context.parse/'chapters.json').write_text(json.dumps(chapters))
    scans = []
    def available(run_dir, unit_id, *, unit_order):
        scans.append((run_dir, unit_id, unit_order))
        return [deepcopy(EXPLANATION)]
    _install_available(monkeypatch, available)
    requests = []
    class Client:
        def chat_json(self, messages, **kwargs):
            requests.append(deepcopy(messages))
            return {'data': authoring(teaching_case()[0])}
    monkeypatch.setattr(stages._t(), '_gateway_client', lambda *_a, **_k: (Client(), 'offline'))
    with review_mode('balanced'):
        first = stages.lesson_stage(ctx)
        assert first.execution_state.value == 'succeeded', first.error
        _install_available(monkeypatch, lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError('已冻结的作者请求不能重新扫描已完成讲解')))
        again = stages.lesson_stage(ctx)
        assert again.execution_state.value == 'succeeded', again.error
    assert scans == [(tmp_path, 'U', ['U10', 'U2', 'U'])]
    assert len(requests) == 1
    frozen = json.loads(requests[0][1]['content'])['frozen_context']
    assert frozen['available_theorem_names'] == [EXPLANATION['name']]
    journal_input = json.loads((tmp_path/'work/products/content/U/generation-journal.input.json').read_text())
    assert 'available_theorem_explanations' not in frozen
    assert EXPLANATION['markdown'] not in json.dumps(frozen, ensure_ascii=False)
    assert journal_input['available_theorem_names'] == [EXPLANATION['name']]


def test_candidate_revalidation_uses_frozen_author_material(tmp_path, monkeypatch):
    from aiforstudy.pipeline.content_revalidation import request_matches_published_candidate
    ctx = context(tmp_path)
    _install_available(monkeypatch, lambda *_a, **_k: [deepcopy(EXPLANATION)])
    monkeypatch.setattr(stages._t(), '_gateway_client', lambda *_a, **_k: (scripted_client(), 'offline'))
    with review_mode('full'):
        result = stages.lesson_stage(ctx)
        assert result.execution_state.value == 'succeeded', result.error
        saved = json.loads((tmp_path/'work/products/content/U/generation-journal.input.json').read_text())
        assert saved.get('available_theorem_names') == [EXPLANATION['name']]
        ctx.services.store.load_state = lambda: {'nodes': {'lesson:U': {
            'execution_state': 'succeeded', 'outputs': [ref.as_dict() for ref in result.outputs]}}}
        _install_available(monkeypatch, lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError('重验不能扫描最新讲解')))
        source = stages._frozen_context(ctx)
        assert request_matches_published_candidate(ctx, source, load_content_policy(source['learner_profile']))
