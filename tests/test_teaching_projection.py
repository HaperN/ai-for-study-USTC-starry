import pytest
from aiforstudy.blocks.content.content_contracts import TeachingEvidenceV1, LessonContentV4, content_digest
from aiforstudy.blocks.content.reader_document import ReaderDocumentV1
from aiforstudy.blocks.content.teaching_projection import compile_teaching_presentation
from aiforstudy.errors import ContractError


def test_evidence_accepts_explicit_role_and_quote():
    row = TeachingEvidenceV1(goal_id='g', purpose='derivation', reader_pointer='/body_markdown', claim_refs=['c'], quote='Proof.', reader_role='proof')
    assert row.reader_role == 'proof'


def test_projection_rejects_stale_document():
    content = {'unit_id': 'u', 'teaching_plan': {'claims': []}, 'teaching_evidence': []}
    reader = ReaderDocumentV1(unit_id='u', content_hash='stale', blocks=[])
    with pytest.raises(ContractError):
        compile_teaching_presentation(content, reader)


def fixture():
    raw = '条件成立时结论成立。\n\n证明：逐步推导。\n\n应用这个结论。'
    content = {'unit_id': 'u', 'teaching_plan': {'claims': [{'id': 'c'}]}, 'teaching_evidence': [
        {'reader_pointer': '/body_markdown', 'claim_refs': ['c'], 'quote': '条件成立时结论成立。', 'reader_role': 'main'},
        {'reader_pointer': '/body_markdown', 'claim_refs': ['c'], 'quote': '证明：逐步推导。', 'reader_role': 'proof'}]}
    reader = ReaderDocumentV1(unit_id='u', content_hash=content_digest(content), blocks=[{'json_pointer':'/body_markdown', 'block_id':'b', 'owner_id':'u', 'raw_text':raw, 'markdown':raw}])
    return content, reader


def test_projection_binds_main_and_optional_to_original_blocks():
    c, r = fixture()
    result = compile_teaching_presentation(c, r)
    assert list(result['roles'].values()) == ['main', 'proof']
    assert result['claim_main_blocks']['c'] == ['b:0']
    assert result['reader_document_hash'] == content_digest(r)


@pytest.mark.parametrize('quote', ['逐步推导', '不存在', '条件成立时结论成立。\n\n证明'])
def test_projection_rejects_missing_or_partial_tokens(quote):
    c, r = fixture()
    c['teaching_evidence'][1]['quote'] = quote
    r._data['content_hash'] = content_digest(c)
    with pytest.raises(ContractError): compile_teaching_presentation(c, r)


def test_projection_rejects_unknown_claim():
    c, r = fixture()
    c['teaching_evidence'][1]['claim_refs'] = ['unknown']
    r._data['content_hash'] = content_digest(c)
    with pytest.raises(ContractError): compile_teaching_presentation(c, r)


def test_duplicate_quote_and_partial_list_are_rejected():
    from aiforstudy.blocks.content.teaching_projection import compile_reader_roles
    for raw, quote in [('重复。\n\n重复。', '重复。'), ('- 一\n- 二', '- 一')]:
        reader = ReaderDocumentV1(blocks=[{'json_pointer':'/body_markdown','block_id':'b','owner_id':'u','raw_text':raw,'markdown':raw}])
        with pytest.raises(ContractError):
            compile_reader_roles(reader, [{'reader_pointer':'/body_markdown','quote':quote,'reader_role':'proof'}])


def test_omitted_role_defaults_to_main():
    c, r = fixture()
    del c['teaching_evidence'][0]['reader_role']
    r._data['content_hash'] = content_digest(c)
    assert compile_teaching_presentation(c, r)['claim_main_blocks']['c'] == ['b:0']


def test_worked_example_has_separate_main_claim_locator():
    c, r = fixture()
    c['teaching_evidence'][1]['reader_role'] = 'worked_example'
    r._data['content_hash'] = content_digest(c)
    projection = compile_teaching_presentation(c, r)
    assert list(projection['roles'].values()) == ['main', 'worked_example']
    assert projection['claim_main_blocks']['c'] == ['b:0']


def test_legacy_omission_survives_valid_content_roundtrip():
    from support.content_case import teaching_case, reference_catalog
    from aiforstudy.blocks.content.content_contracts import parse_content
    from aiforstudy.blocks.content.reader_document import project_reader
    c, _ = teaching_case()
    c['body_markdown'] = '结论及条件。\n\n证明：完整推导。\n\n证毕。\n\n### 例3：旧题\n\n旧题及解答。'
    c['teaching_evidence'] = [{'goal_id':'G','purpose':'definition','reader_pointer':'/body_markdown','claim_refs':['K']}]
    saved = parse_content(c).as_dict()
    assert 'reader_role' not in saved['teaching_evidence'][0]
    restored = parse_content(saved)
    reader = project_reader(restored, reference_catalog())
    projection = compile_teaching_presentation(restored, reader)
    assert projection['roles'] == {}
    assert projection['claim_main_blocks']['K']


def test_normalized_legacy_and_explicit_roles_render_together():
    import json, subprocess
    from pathlib import Path
    from support.content_case import teaching_case, reference_catalog
    from aiforstudy.blocks.content.content_contracts import parse_content
    from aiforstudy.blocks.content.reader_document import project_reader
    from aiforstudy.blocks.render.layout import layout_from_reader_media
    c, _ = teaching_case()
    c['body_markdown'] = ('## 定理\n\n证明：旧证明。\n\n证毕。\n\n### 例3：旧题\n\n旧题解答。\n\n'
                          '### 例4：新题\n\n明确例题。\n\n条件成立时，适用所有此类问题。\n\n'
                          '## 补充\n\n明确证明。')
    def evidence(**kw):
        return {'goal_id':'G','purpose':'definition','reader_pointer':'/body_markdown','claim_refs':['K'],**kw}
    c['teaching_evidence'] = [evidence(), evidence(quote='明确例题。',reader_role='worked_example'),
                              evidence(quote='条件成立时，适用所有此类问题。',reader_role='main'),
                              evidence(quote='明确证明。',reader_role='proof')]
    saved = json.loads(json.dumps(parse_content(c).as_dict(), ensure_ascii=False))
    normalized = parse_content(saved)
    reader = project_reader(normalized, reference_catalog())
    presentation = compile_teaching_presentation(normalized, reader)
    optional = {key for key, role in presentation['roles'].items() if role != 'main'}
    assert optional.isdisjoint(presentation['claim_main_blocks']['K'])
    layout = layout_from_reader_media(reader.as_dict(), [])
    unit={'unit_id':'U','title':c['title'],'reader_document':reader.as_dict(), 'reader_layout':layout,'teaching_presentation':presentation}
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node',str(root/'tests/ui/teaching_projection_roundtrip.cjs')], input=json.dumps(unit,ensure_ascii=False),text=True,capture_output=True,cwd=root)
    assert result.returncode == 0, result.stderr
