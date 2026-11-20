"""The actual production messages carry an executable presentation protocol."""
import json
import pytest
from aiforstudy.blocks.content.content_contracts import LessonContentV4, content_digest
from aiforstudy.blocks.content.reader_document import ReaderDocumentV1
from aiforstudy.blocks.content.teaching_projection import compile_teaching_presentation
from aiforstudy.blocks.content.content_policy import load_content_policy
from aiforstudy.blocks.content.prompt import build_content_messages
from aiforstudy.core.review_mode import review_mode
from support.content_case import teaching_case


@pytest.mark.parametrize('mode', ['balanced', 'json_only', 'full'])
@pytest.mark.parametrize('role', ['author', 'repair', 'reviewer'])
def test_production_messages_deliver_reader_roles_and_valid_example(mode, role):
    _, source = teaching_case()
    with review_mode(mode):
        messages = build_content_messages(role, source, load_content_policy())
    system = messages[0]['content']
    for text in ['teaching_evidence', 'reader_role', 'worked_example', '完整 Markdown 块',
                 'goal_id', 'claim_refs', '省略 reader_role', '显式 main', '显示协议',
                 '正式证明和完整例题可选', '必要条件', '可迁移']:
        assert text in system, (mode, role, text)
    payload = json.loads(messages[1]['content'])
    example = LessonContentV4.model_validate(payload['reader_presentation_example'])
    goals = {g.id for g in example.teaching_plan.goals}
    claims = {c.id for c in example.teaching_plan.claims}
    assert {e.reader_role for e in example.teaching_evidence} == {'main', 'proof', 'worked_example'}
    for evidence in example.teaching_evidence:
        assert evidence.goal_id in goals
        assert set(evidence.claim_refs) <= claims
        assert evidence.reader_pointer == '/body_markdown'
        assert evidence.quote and evidence.quote in example.body_markdown
        assert example.body_markdown.count(evidence.quote) == 1
    serialized = example.model_dump(mode='json', by_alias=True)
    reader = ReaderDocumentV1(unit_id=example.unit_id, content_hash=content_digest(serialized),
        blocks=[{'json_pointer': '/body_markdown', 'block_id': 'body',
                 'owner_id': example.unit_id, 'raw_text': example.body_markdown,
                 'markdown': example.body_markdown}])
    projection = compile_teaching_presentation(serialized, reader)
    assert set(projection['roles'].values()) == {'main', 'proof', 'worked_example'}
    assert projection['claim_main_blocks']['K-CONVENTION']
    # Existing drafts must not silently become explicit-main protections.
    raw = example.model_dump(mode='json', by_alias=True)
    for evidence in raw['teaching_evidence']:
        evidence.pop('reader_role', None)
    legacy = LessonContentV4.model_validate(raw)
    assert all(e.reader_role is None for e in legacy.teaching_evidence)


@pytest.mark.parametrize('role', ['author', 'repair', 'reviewer'])
def test_balanced_optional_proof_keeps_substantive_audit(role):
    _, source = teaching_case()
    with review_mode('balanced'):
        system = build_content_messages(role, source, load_content_policy())[0]['content']
    for obsolete in ['不能因标题写“性质”或“概念性性质”就免去推导',
                     '本节用于计算或证明的运算性质则须交代成立依据和实际推导',
                     '不能把应证明的性质当概念放过']:
        assert obsolete not in system
    for retained in ['必要条件', '完整题设', '评分', '缺内部证据', '来源', '完整展开每一步']:
        assert retained in system
    if role == 'reviewer':
        assert '只有实际提供的证明或演算缺步' in system
        assert '错误原句或公式' in system


@pytest.mark.parametrize('mode', ['balanced', 'json_only', 'full'])
@pytest.mark.parametrize('role', ['author', 'repair', 'reviewer'])
def test_production_messages_do_not_require_optional_theorem_proofs(mode, role):
    """Role assets must not override the shared optional-proof delivery contract."""
    _, source = teaching_case()
    with review_mode(mode):
        system = build_content_messages(role, source, load_content_policy())[0]['content']
    assert '正式证明和完整例题可选' in system
    for mandatory in ['定理须完整展开证明', '定理必须完整展开每一步证明',
                      '保存完整条件、结论、含义与证明']:
        assert mandatory not in system, (mode, role, mandatory)
    for retained in ['必要条件', '可迁移', '主线']:
        assert retained in system, (mode, role, retained)
    if mode != 'balanced' and role in {'author', 'repair'}:
        assert '实际提供的定理证明须完整展开每一步推导及依据' in system
    if mode != 'balanced' and role == 'author':
        assert '保存完整条件、结论、含义与用途' in system
        assert '若提供正式证明，将完整证明保存于同一对象' in system
