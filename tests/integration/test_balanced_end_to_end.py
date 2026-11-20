"""Real stages, SVG worker and browser; only provider responses are substituted."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from aiforstudy.core.jsonio import read_json, write_json_atomic
from aiforstudy.pipeline.engine import build_engine, execute
from aiforstudy.pipeline.services import Providers
from support.runner_v5 import setup_book, _request, TeachingTransport, UNIT


class BalancedTransport(TeachingTransport):
    def __init__(self, persist_failure, string_content=False):
        super().__init__()
        self.persist_failure = persist_failure
        self.string_content = string_content
        self.roles = []
        self.repair_inputs = []
        self.review_inputs = []

    def body(self, revision):
        definition = 'n 表示正整数，不含零。' if self.persist_failure else 'n 表示含零的非负整数。'
        return definition + f' 当前讲解为第 {revision} 版。下图标出了数轴上的零和一。'

    def _answer(self, system, user):
        data = json.loads(user)
        task = data.get('role_task')
        self.roles.append(task)
        if task == 'author':
            self.author_calls += 1
            authored={'content': {'title': '非负整数', 'body_markdown': self.body(0),
                'math_models': [{'not_a_required_internal_model': True}], 'optional_note': '保留'},
                'media_requirements': {'decisions': [], 'requests': [{
                    'request_key': 'number-line', 'kind': 'drawing', 'caption': '数轴上的零和一',
                    'placement': {'target_pointer': '/body_markdown'}}]}}
            if self.string_content:
                authored.update(title='非负整数',content=self.body(0))
            return json.dumps(authored,ensure_ascii=False)
        if task == 'repair':
            frozen = data['frozen_context']
            self.repair_inputs.append(deepcopy(frozen))
            self.content_repairs += 1
            content = deepcopy(frozen['content'])
            content['body_markdown'] = self.body(self.content_repairs)
            return json.dumps({'content': content}, ensure_ascii=False)
        if task == 'reviewer':
            frozen = data['frozen_context']
            self.review_inputs.append(deepcopy(frozen))
            self.review_calls += 1
            rows = []
            for criterion in frozen['criteria']:
                failed = self.persist_failure and criterion['criterion_id'] == 'CONTENT-KNOWLEDGE'
                rows.append({'criterion_id': criterion['criterion_id'], 'version': 1,
                    'status': 'fail' if failed else 'pass', 'evidence_refs': [],
                    'reason': '正文首句“n 表示正整数，不含零”遗漏零；本节约定含零，请改为非负整数。'
                        if failed else '当前知识和适用条件正确，已有过程、自测无问题。'})
            return json.dumps({'verdicts': rows}, ensure_ascii=False)
        if task == 'media_code_generation':
            return json.dumps({'code': 'from matplotlib.figure import Figure\n'
                'def build_figure(context):\n'
                '    figure = Figure()\n'
                '    axes = figure.add_subplot()\n'
                '    axes.plot([0, 1], [0, 0], "o-")\n'
                '    axes.set_xticks([0, 1])\n'
                '    return figure\n'})
        if task == 'media_substantive_review':
            return json.dumps({'verdicts': [{'status': 'pass',
                'reason': '实际成品标出了数轴上的零和一，刻度清楚，与图示用途对应。'}]}, ensure_ascii=False)
        raise AssertionError('unexpected model task: '+str(task))


@pytest.mark.parametrize('persist_failure,string_content', [(True,False),(False,False),(False,True)])
def test_balanced_review_repairs_then_keeps_real_media_and_html(tmp_path, monkeypatch, persist_failure,string_content):
    workspace = setup_book(tmp_path)
    from aiforstudy.providers.capabilities import resolve_manim_interpreter
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv('AIFORSTUDY_MANIM_PYTHON', resolve_manim_interpreter(root))
    monkeypatch.setenv('AIFS_PLAYWRIGHT_MODULE', str(root/'node_modules/playwright'))
    caps = read_json(workspace.root/'config/capabilities.json')
    caps['render_environment']['worker_dir'] = str(root/'tools/manim_worker')
    write_json_atomic(workspace.root/'config/capabilities.json', caps)
    write_json_atomic(workspace.root/'config/review_mode.json', {'mode': 'balanced'})
    transport = BalancedTransport(persist_failure,string_content)
    result, store = execute(build_engine(_request(), workspace, providers=Providers(llm=transport)))
    state = store.load_state()
    assert result.status == ('needs_review' if persist_failure else 'succeeded'), (result.as_dict(), state)
    assert result.review_status == ('needs_review' if persist_failure else 'passed')
    assert not result.scope_accepted and not result.release_ready
    assert not result.failed_nodes and not result.blocked_nodes
    assert transport.author_calls == 1
    assert transport.content_repairs == (2 if persist_failure else 0)
    assert transport.review_calls == (3 if persist_failure else 1)
    assert all(len(request['criteria']) == 3 for request in transport.review_inputs)
    assert set(transport.roles) <= {'author', 'repair', 'reviewer', 'media_code_generation', 'media_substantive_review'}
    assert transport.roles.count('media_code_generation') == 1
    assert transport.roles.count('media_substantive_review') == 1
    media_call = next(call for call in transport.calls
        if json.loads(call['user']).get('role_task') == 'media_substantive_review')
    assert media_call['images'] and all(Path(p).is_file() for p in media_call['images'])
    if persist_failure:
        for index, request in enumerate(transport.repair_inputs):
            assert request['content']['body_markdown'] == transport.body(index)
            assert '遗漏零' in str(request['repair_tickets'])
        repairs = state['repair_states'][UNIT]
        assert repairs['content_rounds_by_artifact']['lesson:'+UNIT] == 2
        assert '次数已用尽' in repairs['stopped_reason']
    audit = read_json(store.work/'products/text_audit'/f'{UNIT}.json')['payload']
    assert audit['review_outcome']['status'] == ('known_error' if persist_failure else 'verified')
    chapter = next((store.work/'legacy/render').glob('*/index.html')).parent
    assert (chapter/'index.html').stat().st_size > 100
    data = (chapter/'data/data.js').read_text()
    reader = json.loads(data.split('window.__DATA__ = ', 1)[1].rsplit(';', 1)[0])
    assert reader['units'][UNIT]['title']=='非负整数'
    assert transport.body(2 if persist_failure else 0) in data
    assert '数轴上的零和一' in data
    assert any('<svg' in row.get('svg', '') and '<path' in row['svg'] for row in reader['figures'].values())
    browser = read_json(chapter/'browser-report.json')
    assert browser['ok'] is True and browser['status'] == 'accepted', browser
    assert result.preview_available
    delivery=read_json(store.work/'products/delivery/delivery.json')['payload']
    assert delivery['review_outcome']['status']==('known_error' if persist_failure else 'verified')


class TeachingSupportTransport(BalancedTransport):
    """Exercise the existing repair route with a concrete teaching omission."""
    def __init__(self):
        super().__init__(False)

    def _answer(self, system, user):
        data = json.loads(user)
        task = data.get('role_task')
        if task == 'author':
            authored = json.loads(super()._answer(system, user))
            authored['content'].update(
                annotations=[{'id': 'boundary', 'kind': 'pitfall', 'title': '零属于哪一类',
                    'markdown': '零是非负整数，但不是正整数。'}],
                examples=[{'id': 'E', 'label': '例1', 'statement': '非负整数 n 满足 n+1=3，求 n。',
                    'approach': '解方程后核对非负整数条件。',
                    'steps': [{'id': 'S', 'markdown': 'n=2。', 'basis': ''}],
                    'conclusion': 'n=2，满足非负整数条件。', 'key_points': [], 'pitfalls': []}])
            return json.dumps(authored, ensure_ascii=False)
        if task == 'reviewer':
            self.roles.append(task)
            self.review_calls += 1
            self.review_inputs.append(deepcopy(data['frozen_context']))
            if self.content_repairs:
                rows = [{'status': 'pass', 'reason': '例1补齐等式两边同减1的依据和条件核对，原零边界提醒正确。'}]
            else:
                rows = [{'status': 'fail', 'reason': '例1唯一一步“n=2”直接给结果，初学者无法复现；请说明等式两边同减1，再核对原条件。'},
                        {'status': 'fail', 'reason': '例1思路“核对非负整数条件”尚未说明核对什么；请说明既要是整数又要不小于零，可补数轴直觉及具体错法。'}]
            return json.dumps({'verdicts': rows}, ensure_ascii=False)
        if task == 'repair':
            self.roles.append(task)
            self.content_repairs += 1
            frozen = data['frozen_context']
            self.repair_inputs.append(deepcopy(frozen))
            content = deepcopy(frozen['content'])
            content['annotations'].append({'id': 'number-line', 'kind': 'intuition', 'title': '',
                'markdown': '在数轴上，非负整数对应零及其右边的整数刻度；右边的非整数刻度仍不合条件。'})
            example = content['examples'][0]
            example['steps'] = [{'id': 'S', 'markdown': '等式两边同减1，得 n=3-1=2。',
                'basis': '等式两边减去同一个数仍相等。2是整数且2≥0，符合题设。[[NOTE:number-line]]'}]
            example['key_points'] = ['先用等式性质求解，再逐项核对题设范围。']
            example['pitfalls'] = ['不能只检查结果不小于零：例如1.5虽非负，却不是整数。']
            return json.dumps({'content': content}, ensure_ascii=False)
        return super()._answer(system, user)


def test_teaching_support_repair_reaches_formal_html_without_extra_review(tmp_path, monkeypatch):
    """Real pipeline/figure worker; model I/O is scripted and browser is explicitly unverified."""
    from aiforstudy.checks import offline
    from aiforstudy.providers.capabilities import resolve_manim_interpreter
    workspace = setup_book(tmp_path)
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv('AIFORSTUDY_MANIM_PYTHON', resolve_manim_interpreter(root))
    caps = read_json(workspace.root/'config/capabilities.json')
    caps['render_environment']['worker_dir'] = str(root/'tools/manim_worker')
    write_json_atomic(workspace.root/'config/capabilities.json', caps)
    write_json_atomic(workspace.root/'config/review_mode.json', {'mode': 'balanced'})
    monkeypatch.setattr(offline, 'check_browser_execution', lambda *a, **k:
        offline.OfflineCheck('browser-execution', 'blocked', '此离线链路测试不启动浏览器；页面实际运行另由DOM测试覆盖。'))
    transport = TeachingSupportTransport()
    result, store = execute(build_engine(_request(), workspace, providers=Providers(llm=transport)))
    assert not result.failed_nodes and not result.blocked_nodes, result.as_dict()
    assert (transport.author_calls, transport.content_repairs, transport.review_calls) == (1, 1, 2)
    assert set(transport.roles) <= {'author', 'repair', 'reviewer', 'media_code_generation', 'media_substantive_review'}
    assert transport.roles.count('media_code_generation') == 1
    original = transport.repair_inputs[0]['content']
    repaired = transport.review_inputs[-1]['candidate_content']
    assert repaired['annotations'][0] == original['annotations'][0]
    assert len(transport.repair_inputs[0]['repair_tickets']) == 1
    assert '无法复现' in transport.repair_inputs[0]['repair_tickets'][0]['problem']
    assert '核对什么' in transport.repair_inputs[0]['repair_tickets'][0]['problem']
    assert read_json(store.work/'products/text_audit'/f'{UNIT}.json')['payload']['review_outcome']['status'] == 'verified'
    assert store.load_state()['repair_states'][UNIT]['content_rounds_by_artifact']['lesson:'+UNIT] == 1
    chapter = next((store.work/'legacy/render').glob('*/index.html')).parent
    data = (chapter/'data/data.js').read_text()
    reader = json.loads(data.split('window.__DATA__ = ', 1)[1].rsplit(';', 1)[0])
    blocks = reader['units'][UNIT]['reader_document']['blocks']
    assert next(b for b in blocks if b['json_pointer'] == '/annotations/1/markdown')['field_label'] == '直觉'
    for group, field in [('annotations', 'markdown'), ('examples', 'key_points'), ('examples', 'pitfalls')]:
        for row in repaired[group]:
            for text in ([row[field]] if isinstance(row[field], str) else row[field]):
                assert sum(b['raw_text'] == text for b in blocks) == 1
    assert any('<svg' in row.get('svg', '') for row in reader['figures'].values())
    assert read_json(chapter/'browser-report.json')['status'] == 'blocked'
    assert result.review_status != 'passed'  # No invented browser approval.
