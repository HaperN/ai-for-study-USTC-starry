"""P6: semantic template selection and bounded harmonic data, with no paid calls."""


import json


from pathlib import Path


import pytest


from aiforstudy.core.jsonio import write_json_atomic


from aiforstudy.pipeline import stage_io as t


from test_p2_content_delivery import setup_run, ctx


def generator():
    return t._legacy('model_generate')


def lesson():
    return {'title': '共轭调和函数', 'body_markdown':
        '共轭调和函数的等值线互相正交。例：$f(z)=z^2$，$u=x^2-y^2$，$v=2xy$。'
        '除原点外，拖动交点可观察两族等值线的切线夹角。[[EXPLAIN:F-01|等值线正交]]',
        'concepts': [{'id': 'C-0226'}], 'explainables': [
            {'id': 'F-01', 'name': '等值线正交', 'concept_id': 'C-0226',
             'markdown': '共轭调和函数 f(z)=z^2 的两族等值线，除原点外互相正交。'}]}


def spec():
    return {'id': 'M-01', 'template': 'harmonic-contours', 'kind': 'svg',
        'title': '移动交点看两族等值线', 'binds_to': 'F-01',
        'why_here': '拖动交点同时观察两族等值线与切线方向，特别检查原点的例外。',
        'anchor_hint': '共轭调和函数的等值线',
        'params': {'initial_point': [1, 1], 'extent': 3},
        'caption': '移动交点，观察两条切线；原点不适用正交结论。',
        'fallback_text': 'u=x²−y²，v=2xy；在非原点处两条等值线正交。'}


class Client:
    def __init__(self, data):
        self.data, self.calls = data, []
    def chat_json(self, messages, **kw):
        self.calls.append(messages)
        return {'data': self.data, 'usage': {}}


def test_fixed_book_specific_id_does_not_enable_linear_algebra():
    g = generator()
    plan = g.plan_models(lesson(), diagrams=[], context={
        'introduced_ids': ['C-0004'], 'introduced_names': ['虚部'],
        'concept_names_by_id': {'C-0226': '共轭调和函数'}})
    assert plan['templates'] == ['harmonic-contours']
    assert plan['bindings']['harmonic-contours'] == ['F-01']


def test_actual_concept_names_are_resolved_in_prompt():
    prompt = generator().build_user_prompt(lesson(), [], context={
        'concept_names_by_id': {'C-0226': '共轭调和函数'}})
    assert '* C-0226 共轭调和函数' in prompt
    assert '线性代数教材' not in generator().SYSTEM


@pytest.mark.parametrize('change', ['no_square', 'no_explanation', 'later_only'])
def test_harmonic_requires_current_supported_example_and_binding(change):
    d = lesson()
    if change == 'no_square':
        d['body_markdown'] = '共轭调和函数的等值线互相正交。'
        d['explainables'][0]['markdown'] = '共轭调和函数的等值线。'
    elif change == 'no_explanation':
        d['explainables'] = []
    else:
        d = {'body_markdown': '本章先介绍定义，再证明定理。', 'concepts': []}
    plan = generator().plan_models(d, diagrams=[], context={
        'introduced_names': ['共轭调和函数', '等值线'], 'all_names': ['共轭调和函数']})
    assert 'harmonic-contours' not in plan['templates']


@pytest.mark.parametrize('payload', [
    {'initial_point': [10**500, 1], 'extent': 3},
    {'initial_point': [1, 1], 'extent': 10**500},
    {'initial_point': [float('nan'), 1], 'extent': 3},
    {'initial_point': [1, float('inf')], 'extent': 3},
    {'initial_point': [True, 1], 'extent': 3},
    {'initial_point': [1, 1, 1], 'extent': 3},
    {'initial_point': [4, 1], 'extent': 3},
    {'initial_point': [1, 1], 'extent': 0},
    {'initial_point': [1, 1], 'extent': 5},
    {'initial_point': [1, 1], 'extent': 3, 'function': 'eval(1)'},
])
def test_unsafe_or_out_of_domain_parameters_are_rejected(payload):
    s = spec(); s['params'] = payload
    output = generator().generate_models(lesson(), diagrams=[], client=Client({'models': [s]}),
                                         unit_id='U', retries=0)
    assert not output['models']
    assert output['checks']['dropped']


@pytest.mark.parametrize('field,value', [('binds_to', 'C-0226'), ('anchor_hint', '不存在的正文落点'),
                                       ('why_here', ''), ('code', 'alert(1)')])
def test_harmonic_cannot_bind_to_unverified_or_executable_input(field, value):
    s = spec(); s[field] = value
    output = generator().generate_models(lesson(), diagrams=[], client=Client({'models': [s]}),
                                         unit_id='U', retries=0)
    assert not output['models']
    assert output['checks']['dropped']


def test_supported_harmonic_is_nonempty_and_canonical():
    client = Client({'models': [spec()]})
    output = generator().generate_models(lesson(), diagrams=[], client=client, unit_id='U', retries=0)
    assert len(output['models']) == 1
    assert output['models'][0]['kind'] == 'svg'
    assert output['models'][0]['params'] == {'initial_point': [1, 1], 'extent': 3}
    assert output['checks']['status'] == 'generated'
    assert len(client.calls) == 1


@pytest.mark.parametrize('body,expected', [
    ({'body_markdown': '本章先回顾前一章，再介绍新的研究方向。'}, 'not_applicable'),
    ({'body_markdown': '改变电阻 R，观察电流 I=U/R。', 'explainables': [{'id': 'F-01', 'name': '欧姆定律'}]}, 'unsupported')])
def test_no_template_skips_client_and_records_evidence(body, expected):
    client = Client({'models': []})
    output = generator().generate_models(body, diagrams=[], client=client, unit_id='U')
    assert client.calls == []
    assert output['checks']['attempts'] == 0
    assert output['checks']['status'] == expected
    assert output['checks']['policy'] and output['checks']['evidence']
    assert bool(output['checks']['problems']) == (expected == 'unsupported')


def test_unexplained_empty_response_is_not_accepted():
    output = generator().generate_models(lesson(), diagrams=[], client=Client({'models': []}), unit_id='U')
    assert output['checks']['status'] == 'needs_review'
    assert output['checks']['problems']


def test_justified_empty_decision_retains_body_evidence():
    d = lesson()
    output = generator().generate_models(d, diagrams=[], client=Client({'models': [], 'decision': {
        'status': 'not_applicable', 'reason': '本节已有读者可逐步核对的说明，额外交互不能新增需要观察的变化。',
        'evidence': [{'quote': '共轭调和函数的等值线互相正交。',
                     'explanation': '这段已有说明可直接核对本单元目标，不需要另外拖动。'}]}}), unit_id='U')
    assert output['checks']['status'] == 'not_applicable'
    assert not output['checks']['problems']
    assert output['checks']['decision']['evidence'][0]['quote'] in d['body_markdown']


@pytest.mark.parametrize('expression', ['z^20', 'z^2+z', 'z^2-1', 'z^2/z'])
def test_harmonic_does_not_substitute_a_different_function(expression):
    d = lesson()
    d['body_markdown'] = d['body_markdown'].replace('z^2', expression)
    d['explainables'][0]['markdown'] = d['explainables'][0]['markdown'].replace('z^2', expression)
    assert 'harmonic-contours' not in generator().plan_models(d, diagrams=[])['templates']


@pytest.mark.parametrize('frozen_controls', [False, True])
def test_browser_rejects_harmonic_empty_shell_or_unresponsive_values(tmp_path, frozen_controls):
    from aiforstudy.checks.offline import run_offline_acceptance
    import shutil
    fake = ''
    if frozen_controls:
        fake = '<svg class="harmonic-plot"><path data-contour="u" d="M 1 1 L 2 2"/><path data-contour="v" d="M 2 1 L 1 2"/></svg>'
        fake += '<input type="range" data-axis="x" min="-3" max="3"><input type="range" data-axis="y" min="-3" max="3">'
        fake += '<span data-harmonic-value="u">0.00</span><span data-harmonic-value="v">2.00</span><span data-harmonic-value="angle">90°</span><p class="harmonic-status">互相正交</p>'
    data = {'models': {'U.M-01': {'template': 'harmonic-contours', 'kind': 'svg', 'params': {'extent': 3, 'initial_point': [1,1]}}},
            'units': {'U': {'models': {'U.M-01': {}}}}}
    (tmp_path / 'index.html').write_text('<html><body><script>window.__DATA__=' + json.dumps(data) + '</script>'
        '<button data-unit="U">U</button><figure class="model-svg" data-model-id="U.M-01">' + fake + '</figure></body></html>')
    report = run_offline_acceptance(tree_root=tmp_path, node=shutil.which('node'))
    assert not report.passed
    runtime = next(check for check in report.checks if check.name == 'browser-execution')
    assert runtime.evidence['model_errors']
