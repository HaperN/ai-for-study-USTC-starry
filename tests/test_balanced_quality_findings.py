"""Actual findings survive free-form IDs and drive the existing repair cycle."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from aiforstudy.core.review_mode import review_mode
from aiforstudy.blocks.content.review import review_content
from aiforstudy.blocks.media.media_planning import plan_media_requirements
from aiforstudy.pipeline import content_stages as stage, stage_io
from support.content_case import teaching_case
from test_balanced_content_review import Client
from test_content_generation_v3 import context


def test_quality_review_keeps_multiple_findings_without_ids_or_category_quotas():
    content, source = teaching_case()
    rows = [
        {'status': 'fail', 'reason': '例2第三步遗漏分母非零条件，请在约分前补明。'},
        {'status': 'fail', 'reason': '例3只给结果，补出交叉项合并的实际计算。'},
        {'status': 'unknown', 'reason': '提供的教材片段在例4中断，无法确认原题范围。'},
    ]
    client = Client([{'verdicts': rows}])
    with review_mode('balanced'):
        report = review_content(content, {}, source, (), None, client).as_dict()
    assert report['verdicts'] == rows
    assert report['blocking_issues'] == rows[:2]
    assert report['decision'] == 'needs_review'
    assert len(client.calls) == 1
    assert report['responses'] == [{'verdicts': rows}]


def test_single_complete_quality_pass_needs_no_fixed_ids():
    content, source = teaching_case()
    answer = {'verdicts': [{'status': 'pass', 'reason': '已核对教材范围、条件和讲解，本节约定准确且没有遗漏；没有适用推导或自测。'}]}
    with review_mode('balanced'):
        report = review_content(content, {}, source, (), None, Client([answer])).as_dict()
    assert report['decision'] == 'accepted'
    assert report['verdicts'] == answer['verdicts']


def test_complete_main_text_with_empty_support_arrays_accepts_model_pass():
    from aiforstudy.blocks.content.json_only import normalize_author_output
    _, source = teaching_case()
    raw = {'body_markdown': (
        '设 $z_1,z_2$ 为任意复数。为了处理和的模平方，先用 $|v|^2=v\\bar v$ 转成乘积。'
        '展开得到 $z_1\\bar z_1+z_1\\bar z_2+z_2\\bar z_1+z_2\\bar z_2$。'
        '平方项分别是两模平方。令 $w=z_1\\bar z_2$，由共轭保持乘法及双重共轭还原，'
        '$\\bar w=\\bar z_1z_2=z_2\\bar z_1$。若 $w=a+ib$，则 $\\bar w=a-ib$，'
        '相加抵消虚部，故两交叉项之和为 $2\\operatorname{Re}w$，得到模平方恒等式。'
        '不能把两交叉项合为 $2w$：$w=1+i$ 时，前者为2而后者为 $2+2i$，只有w为实数时相同。'
        '下次遇到复数和的模平方，先转成与共轭相乘，再识别交叉项是否互为共轭。'),
        'annotations': [], 'explainables': [], 'examples': [], 'learning_checks': []}
    client = Client([{'verdicts': [{'status': 'pass',
        'reason': '离线替身：正文已有方法选择、逐步依据、具体反例与方法回顾，无需重复拆卡。'}]}])
    with review_mode('balanced'):
        content = normalize_author_output(raw, source)['content']
        report = review_content(content, {}, source, (), None, client).as_dict()
    candidate = json.loads(client.calls[0][0][1]['content'])['frozen_context']['candidate_content']
    assert candidate['body_markdown'] == raw['body_markdown']
    assert all(candidate[name] == [] for name in ('annotations', 'explainables', 'examples', 'learning_checks'))
    assert report['decision'] == 'accepted'
    assert report['blocking_issues'] == []
    assert len(client.calls) == 1


def _old_media():
    return {'requests': [{'request_key': 'OLD-FIGURE', 'kind': 'image',
            'learning_goal': '对照同类项', 'placement': {'target_pointer': '/body_markdown',
                'anchor_text': '例5需要追踪交叉项。', 'position': 'after'}}], 'decisions': []}


def _animation():
    return {'request_key': 'EX5-CROSS-TERMS', 'kind': 'animation',
            'learning_goal': '追踪两交叉项为何互为共轭并相加得到两倍实部。',
            'animation': {'initial_state': '展示两个模平方及交叉项。',
                'steps': ['分别对应展开的交叉项。', '配对共轭项并合并为两倍实部。'],
                'key_pauses': ['停留在共轭配对处。'], 'final_state': '展示合并结果。'},
            'placement': {'target_pointer': '/body_markdown',
                'anchor_text': '例5需要追踪交叉项。', 'position': 'after'}}


def _audit(tmp_path):
    return json.loads((tmp_path/'work/products/text_audit/U.json').read_text())['payload']


@pytest.mark.parametrize('media_reply', ['complete', 'additions_only', 'omitted'])
def test_same_content_repair_can_add_animation_and_omission_preserves_old_requests(tmp_path, monkeypatch, media_reply):
    draft = {'content': {'title': '共轭', 'body_markdown': '例5需要追踪交叉项。[[NOTE:N|共轭对称]]',
             'annotations': [{'id': 'N', 'kind': 'intuition', 'markdown': '共轭对实部相同、虚部相反。'},
                             {'id': 'P', 'kind': 'pitfall', 'markdown': '交叉项不能无条件写成2w。'}],
             'examples': [{'id': 'E', 'statement': '辨认共轭对。',
                           'key_points': ['先确认共轭，再合并为两倍实部。'],
                           'pitfalls': ['例如1+i与1-i相加为2，而不是2+2i。']}]},
             'media_requirements': _old_media()}
    reason = '例5第二步交叉项来自何处难以跟随；增加对应、配对和合并的动画，比只列结果更易理解。'
    fixed = {'content': deepcopy(draft['content'])}
    include_media = media_reply != 'omitted'
    if include_media:
        fixed['media_requirements'] = {'requests': (
            [deepcopy(_old_media()['requests'][0]), _animation()] if media_reply == 'complete' else [_animation()]), 'decisions': []}
    client = Client([draft, {'verdicts': [{'status': 'fail', 'reason': reason}]}, fixed,
                     {'verdicts': [{'status': 'pass', 'reason': '已重读正文及媒体需求，当前问题已解决。'}]}])
    monkeypatch.setattr(stage_io, '_gateway_client', lambda *a, **k: (client, 'offline'))
    with review_mode('balanced'):
        first = stage.lesson_stage(context(tmp_path))
        assert first.execution_state.value == 'succeeded', first.error
        first_content = stage.load_content(context(tmp_path))[0]
        original = stage.load_author_media(context(tmp_path), first_content)['requirements']
        audit = stage.text_audit_stage(context(tmp_path, 'text_audit'))
        request = json.loads(client.calls[1][0][1]['content'])['frozen_context']
        assert request['author_media'] == original
        assert len(audit.repair_requests) == 1
        ticket_ids = audit.repair_requests[0].ticket_ids
        repair = context(tmp_path)
        repair.repair_batch = SimpleNamespace(batch_id='repair-media', round=1,
            jobs=[SimpleNamespace(owner='lesson', kind='text_content', ticket_ids=ticket_ids, artifact_id='lesson:U')])
        repaired = stage.lesson_stage(repair)
        assert repaired.execution_state.value == 'succeeded', repaired.error
        repair_input = json.loads(client.calls[2][0][1]['content'])['frozen_context']
        assert repair_input['author_media'] == original
        for field in ('annotations', 'examples'):
            assert repair_input['content'][field] == first_content[field]
        content = stage.load_content(context(tmp_path))[0]
        media = stage.load_author_media(context(tmp_path), content)
        assert media['requirements']['requests'][0] == original['requests'][0]
        assert [r['request_key'] for r in media['requirements']['requests']] == (
            ['OLD-FIGURE', 'EX5-CROSS-TERMS'] if include_media else ['OLD-FIGURE'])
        last = stage.text_audit_stage(context(tmp_path, 'text_audit'))
        assert last.quality_state.value == 'accepted', last.error
        final_input = json.loads(client.calls[3][0][1]['content'])['frozen_context']
        assert final_input['author_media'] == media['requirements']
        for field in ('annotations', 'examples'):
            assert final_input['candidate_content'][field] == first_content[field]
        assert set(ticket_ids) <= set(_audit(tmp_path)['closed_ticket_ids'])
        plan = plan_media_requirements(content, {}, author_requirements=media,
            capabilities={'code_generation': 'GeneratedMediaCodeV1'})
        assert [r['kind'] for r in plan['requests']] == (['drawing', 'animation'] if include_media else ['drawing'])
        original_file = tmp_path/'work/products/author_media/U/revisions/000001.json'
        assert json.loads(original_file.read_text())['requirements'] == original
    assert [c[1]['tag'].split(':')[0] for c in client.calls] == ['content-author', 'content-review', 'content-repair', 'content-review']


def test_partial_repair_replaces_current_findings_under_same_ticket(tmp_path, monkeypatch):
    first = {'status': 'fail', 'reason': '例1选法处只写“用共轭”；没有解释为什么模平方要转成与共轭相乘，读者无法自行选择方法；补明模平方恒等式的用途。'}
    second = {'status': 'fail', 'reason': '例2交叉项合并处只写“合并可得”；没有解释两项互为共轭，读者易写成2w；补出共轭对应和w=1+i的区别。'}
    client = Client([{'body_markdown': '初稿'}, {'verdicts': [first, second]},
                     {'body_markdown': '例1已补条件'}, {'verdicts': [second]},
                     {'body_markdown': '两例均补好'}, {'verdicts': [{'status': 'pass', 'reason': '两项原问题均已解决，全文质量符合要求。'}]}])
    monkeypatch.setattr(stage_io, '_gateway_client', lambda *a, **k: (client, 'offline'))
    with review_mode('balanced'):
        stage.lesson_stage(context(tmp_path))
        audit = stage.text_audit_stage(context(tmp_path, 'text_audit'))
        assert len(audit.repair_requests) == 1
        old_ids = audit.repair_requests[0].ticket_ids
        assert _audit(tmp_path)['check_reports'][0]['verdicts'] == [first, second]
        for round_no in (1, 2):
            ctx = context(tmp_path)
            ctx.repair_batch = SimpleNamespace(batch_id=f'repair-{round_no}', round=round_no,
                jobs=[SimpleNamespace(owner='lesson', kind='text_content', ticket_ids=old_ids, artifact_id='lesson:U')])
            assert stage.lesson_stage(ctx).execution_state.value == 'succeeded'
            audit = stage.text_audit_stage(context(tmp_path, 'text_audit'))
            if round_no == 1:
                assert audit.repair_requests[0].ticket_ids == old_ids
                assert _audit(tmp_path)['tickets'][0]['problem'] == second['reason']
                assert first['reason'] not in _audit(tmp_path)['review_outcome']['reasons']
        assert audit.quality_state.value == 'accepted'
        assert set(old_ids) <= set(_audit(tmp_path)['closed_ticket_ids'])


def test_partial_media_update_preserves_animation_type_steps_and_old_order():
    previous = {'requests': [_old_media()['requests'][0], _animation()], 'decisions': []}
    revised = {'requests': [{'request_key': 'EX5-CROSS-TERMS', 'caption': '修正后的图注。'},
                            {'request_key': 'NEW-IMAGE', 'kind': 'image', 'caption': '补充图。'}]}
    result = stage._merge_balanced_media(previous, revised)
    assert [r['request_key'] for r in result['requests']] == ['OLD-FIGURE', 'EX5-CROSS-TERMS', 'NEW-IMAGE']
    assert result['requests'][1]['kind'] == 'animation'
    assert result['requests'][1]['animation'] == _animation()['animation']
    assert result['requests'][1]['caption'] == '修正后的图注。'
    assert previous['requests'][1] == _animation()


def test_replayed_media_repair_keeps_response_identity_without_another_call(tmp_path, monkeypatch):
    repaired = {'content': {'body_markdown': '正文不变。'}, 'media_requirements': {'requests': [_animation()]}}
    client = Client([{'body_markdown': '正文不变。'},
        {'verdicts': [{'status': 'fail', 'reason': '例题需要对应交叉项的动画，请补申请。'}]}, repaired])
    monkeypatch.setattr(stage_io, '_gateway_client', lambda *a, **k: (client, 'offline'))
    original_publish = stage._publish_revision
    with review_mode('balanced'):
        stage.lesson_stage(context(tmp_path))
        audit = stage.text_audit_stage(context(tmp_path, 'text_audit'))
        ctx = context(tmp_path)
        ctx.repair_batch = SimpleNamespace(batch_id='interrupted-publication', round=1,
            jobs=[SimpleNamespace(owner='lesson', kind='text_content',
                ticket_ids=audit.repair_requests[0].ticket_ids, artifact_id='lesson:U')])
        def interrupted(*a, **kw):
            raise OSError('simulated publication interruption')
        monkeypatch.setattr(stage, '_publish_revision', interrupted)
        assert stage.lesson_stage(ctx).execution_state.value == 'blocked'
        monkeypatch.setattr(stage, '_publish_revision', original_publish)
        assert stage.lesson_stage(ctx).execution_state.value == 'succeeded'
        current = stage.load_content(context(tmp_path))[0]
        media = stage.load_author_media(context(tmp_path), current)
    from aiforstudy.blocks.content.content_contracts import content_digest
    assert media['author_response_hash'] == content_digest({'response': repaired})
    assert len(client.calls) == 3
