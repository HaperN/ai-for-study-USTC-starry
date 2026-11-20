"""Exercise actual model-facing messages; no API calls or extra review stages."""
import json
import pytest
from aiforstudy.blocks.content.content_policy import load_content_policy, prompt_rules
from aiforstudy.blocks.content.prompt import build_content_messages
from aiforstudy.blocks.media.code_generation import media_code_prompt
from aiforstudy.core.review_mode import review_mode
from support.content_case import teaching_case
from test_media_code_contracts import approved_case

@pytest.mark.parametrize('mode', ['full','balanced'])
@pytest.mark.parametrize('role', ['author','reviewer','repair'])
def test_actual_content_roles_preserve_textbook_narrative_and_media_purpose(mode,role):
    _, source=teaching_case()
    with review_mode(mode):
        messages=build_content_messages(role,source,load_content_policy())
    system=messages[0]['content']
    for phrase in ['连续讲课','教材章节顺序','先修依赖','前文提出观察任务','后文解释观察支持','标题关键词','中文口语','LaTeX','教材结论逐项保留','只折叠证明']:
        assert phrase in system,(mode,role,phrase)
    assert '所有例题' in system and '默认收起' in system
    assert '不新增字段' in system


def test_existing_coherence_criterion_carries_narrative_no_new_review_item():
    policy=load_content_policy()
    rule=next(r for r in policy.rules if r['rule_id']=='READER-COHERENCE')
    assert '连续讲课' in rule['requirement']
    assert '教材章节顺序' in rule['requirement']
    assert '定义—性质' in rule['requirement']
    assert not any(r['rule_id'].startswith('MEDIA-NARRATIVE') for r in policy.rules)

@pytest.mark.parametrize('kind',['drawing','animation'])
def test_actual_media_messages_keep_student_narration_separate_from_rendering(kind):
    content,approval,request,_=approved_case(kind)
    messages=media_code_prompt(request,content=content,approval=approval)
    system=messages[0]['content']
    for phrase in ['前文提出观察任务','后文解释观察支持','标题关键词','caption','observation','中文口语','可视 LaTeX','制图命令','不新增字段']:
        assert phrase in system,(kind,phrase)
    payload=json.loads(messages[1]['content'])
    assert 'narration_steps' not in payload['output_json_schema']['properties']
