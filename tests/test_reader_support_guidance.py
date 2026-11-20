"""Reader support is an author/reviewer contract, carried by the existing repair cycle."""
from copy import deepcopy
import json
import re
from types import SimpleNamespace

import pytest

from aiforstudy.blocks.content.content_policy import load_content_policy
from aiforstudy.blocks.content.json_only import normalize_author_output
from aiforstudy.blocks.content.prompt import build_content_messages
from aiforstudy.blocks.content.reader_document import project_reader
from aiforstudy.core.review_mode import review_mode
from aiforstudy.pipeline import content_stages as stage, stage_io
from support.content_case import teaching_case
from test_balanced_content_review import Client
from test_content_generation_v3 import context


@pytest.mark.parametrize('mode', ['balanced', 'full', 'json_only'])
@pytest.mark.parametrize('role', ['author', 'repair', 'reviewer'])
def test_all_actual_role_messages_share_reader_support_and_no_obsolete_exemption(mode, role):
    content, source = teaching_case()
    with review_mode(mode):
        messages = build_content_messages(role, {'source_context': source, 'content': content}, load_content_policy())
    system = messages[0]['content']
    for instruction in ('> **概念·', '独立可拖动浮窗', 'explainables', 'annotations',
                        '[[EXPLAIN:', '[[NOTE:', '[[CONCEPT:',
                        '所有证明默认折叠', '不设固定字数、卡片数、链接数',
                        '不能以正文已有为由取消', '完整检查卡内内容', '语义对应'):
        assert instruction in system, (mode, role, instruction)
    for obsolete in ('未单列解析卡或缺链接不构成失败', '不要求搬进指定数组',
                     '不要求推导、额外解释或单独解析卡', '无需为每个概念强制证明或另做解析卡',
                     '正文已有作用不重复拆卡'):
        assert obsolete not in system, (mode, role, obsolete)
    if role == 'repair':
        assert '迁移' in system and '未涉及' in system


def test_production_examples_keep_core_blocks_and_distinct_linked_support_objects():
    _, source = teaching_case()
    with review_mode('balanced'):
        payload = json.loads(build_content_messages('author', source, load_content_policy())[1]['content'])
        for example in (payload['output_schema_example'], payload['animation_requirement_example']):
            content = normalize_author_output(example, source)['content']
            reader = project_reader(content, source['reference_catalog']).as_dict()
            assert '> **概念·' in content['body_markdown']
            blocks = reader['blocks']
            links = {target for block in blocks for target in re.findall(r'\]\(#([^)]*)\)', block['markdown'])}
            assert links <= {block['anchor'] for block in blocks}
            assert content['annotations']
            for index, annotation in enumerate(content['annotations']):
                block = next(block for block in blocks if block['json_pointer'] == f'/annotations/{index}/markdown')
                assert block['anchor'] in links, '每个示例补充应有就地入口'
                assert sum(other['raw_text'] == annotation['markdown'] for other in blocks) == 1
                assert annotation['markdown'] not in content['body_markdown'], '补充正文不复制到主线'


def test_semantic_support_findings_use_the_existing_author_review_repair_review_cycle(tmp_path, monkeypatch):
    """Scripted verdicts test routing/prompt coverage, not an LLM quality judgement."""
    core = '> **概念·非负整数**\n> 非负整数是大于或等于零的整数，包括零。'
    draft = {'content': {'title': '判断取值', 'body_markdown': core + '\n\n零的边界见[[NOTE:line|常见误区]]。',
        'annotations': [{'id': 'line', 'kind': 'intuition', 'title': '数轴对应',
                         'markdown': '数轴上零及其右侧的整数刻度对应非负整数。'},
                        {'id': 'trap', 'kind': 'pitfall', 'title': '非负不是正数',
                         'markdown': '非负包括零；正数不包括零，不能把这两个条件等同。'}]}}
    reason = '正文“常见误区”链接打开“数轴对应”，未对应非负与正数的区别；真正的易错说明没有就地入口。请在零这一边界处链接“非负不是正数”，保留独立直觉入口和正文定义。'
    fixed = deepcopy(draft)
    fixed['content']['body_markdown'] = core + '\n\n零的边界见[[NOTE:trap|非负不是正数]]；也可看[[NOTE:line|数轴对应]]。'
    client = Client([draft, {'verdicts': [{'status': 'fail', 'reason': reason}]}, fixed,
                     {'verdicts': [{'status': 'pass', 'reason': '离线替身：定义与条件保留，易错和直觉的内容及就地链接分别对应。'}]}])
    monkeypatch.setattr(stage_io, '_gateway_client', lambda *a, **k: (client, 'offline'))
    with review_mode('balanced'):
        first = stage.lesson_stage(context(tmp_path))
        assert first.execution_state.value == 'succeeded'
        original = stage.load_content(context(tmp_path))[0]
        audit = stage.text_audit_stage(context(tmp_path, 'text_audit'))
        assert len(audit.repair_requests) == 1
        ticket_ids = audit.repair_requests[0].ticket_ids
        repair = context(tmp_path)
        repair.repair_batch = SimpleNamespace(batch_id='reader-support', round=1,
            jobs=[SimpleNamespace(owner='lesson', kind='text_content', ticket_ids=ticket_ids, artifact_id='lesson:U')])
        assert stage.lesson_stage(repair).execution_state.value == 'succeeded'
        assert stage.text_audit_stage(context(tmp_path, 'text_audit')).quality_state.value == 'accepted'
        current = stage.load_content(context(tmp_path))[0]
    assert current['annotations'] == original['annotations']
    assert current['body_markdown'] == fixed['content']['body_markdown']
    assert [call[1]['tag'].split(':')[0] for call in client.calls] == [
        'content-author', 'content-review', 'content-repair', 'content-review']
    for messages, _ in client.calls:
        assert '独立可拖动浮窗' in messages[0]['content']
    reviewed = json.loads(client.calls[1][0][1]['content'])['frozen_context']
    assert reviewed['candidate_content']['annotations'] == original['annotations']
    assert len(reviewed['criteria']) == 3, '沿用原有审核项目'
    knowledge = next(row['expected'] for row in reviewed['criteria'] if row['criterion_id'] == 'CONTENT-KNOWLEDGE')
    assert '语义对应' in knowledge and '卡内' in knowledge
    repaired = json.loads(client.calls[2][0][1]['content'])['frozen_context']
    assert reason in json.dumps(repaired['repair_tickets'], ensure_ascii=False)
    assert repaired['content']['annotations'] == original['annotations']
