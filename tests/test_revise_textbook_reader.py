from copy import deepcopy
from pathlib import Path
import pytest
from tools.revise_textbook_reader import apply_lessons, validate_lessons


def example():
    body='先看原来的问题。\n\n[[TEACHING_VISUAL:V1]]\n\n这个观察帮助我们进入下一步。'
    lesson={'unit_id':'U','title':'复数乘法','source_path':'original_text/u.md',
            'body_markdown':body,'visuals':[{'id':'V1','marker':'[[TEACHING_VISUAL:V1]]',
            'kind':'multiply','model_title':'复数乘法','title':'乘以i的旋转','formula':'w=iz',
            'purpose':'观察旋转','action':'改变乘数','observation':'观察两个向量','conclusion':'模与辐角控制乘法'}],
            'media':[],'next_bridge':'下一步研究开方。'}
    data={'book':'test','scope':['U'],'animations':{},'units':{'U':{'title':'复数乘法',
        'reader_media':[{'request_id':'unrelated'}], 'reader_document':{
        'schema_version':'ReaderDocumentV1','unit_id':'U','content_hash':'old',
        'blocks':[{'block_id':'body','anchor':'reader-body','owner_id':'U','json_pointer':'/body_markdown',
                   'raw_text':'旧讲解','markdown':'旧讲解','group':'body_markdown','field_label':'正文'}],
        'reference_catalog':{'schema_version':'FrozenReferenceCatalogV1','entries':[]}}}}}
    return data,lesson


def test_revision_is_a_new_coherent_projection_and_preserves_source():
    data,lesson=example();before=deepcopy(data)
    result=apply_lessons(data,{'U':lesson})
    unit=result['units']['U']
    assert data==before
    assert unit['reader_document']['blocks'][0]['markdown']==lesson['body_markdown']
    assert unit['study_source_document']==before['units']['U']['reader_document']
    assert unit['reader_media']==[]
    assert unit['study_lesson']==lesson
    assert result['study_reader']['visual_policy']=='authored'
    assert result['study_reader']['theme']=='light'


def test_markers_require_unique_real_plans_and_actual_source_paths(tmp_path):
    data,lesson=example();source=tmp_path/'original_text/u.md';source.parent.mkdir();source.write_text('复数乘法教材')
    assert validate_lessons(data,{'U':lesson},tmp_path)==[]
    broken=deepcopy(lesson);broken['body_markdown']+='\n\n[[TEACHING_VISUAL:V1]]'
    assert validate_lessons(data,{'U':broken},tmp_path)
    broken=deepcopy(lesson);broken['source_path']='../escape.md'
    assert validate_lessons(data,{'U':broken},tmp_path)


def test_html_references_versioned_local_assets_so_updated_teaching_code_is_loaded(tmp_path):
    from blocks.render.build import write_reader_site
    data,_=example();write_reader_site(data,tmp_path,book='教材')
    html=(tmp_path/'index.html').read_text()
    assert 'assets/reader-layout.js?v=' in html
    assert 'assets/study-lesson.js?v=' in html
    assert 'data/data.js?v=' in html


def result_for(lesson):
    return {'id':'U-C1','title':'乘法交换律','statement_markdown':'任意复数 $z,w$ 满足 $zw=wz$。',
            'after':lesson['body_markdown'].split('\n\n')[0],
            'source_start_line':1,'source_end_line':1,'source_quote':'定理1 乘法交换律：zw=wz。'}


def test_required_conclusion_is_inserted_in_reading_flow_instead_of_only_in_collapsed_sources():
    data,lesson=example();lesson['conclusions']=[result_for(lesson)]
    revised=apply_lessons(data,{'U':lesson})['units']['U']
    body=next(b['markdown'] for b in revised['reader_document']['blocks'] if b['group']=='body_markdown')
    assert '[[TEXTBOOK_RESULT:U-C1]]' in body
    assert body.index('[[TEXTBOOK_RESULT:U-C1]]') < body.index('[[TEACHING_VISUAL:V1]]')
    assert revised['study_lesson']['conclusions'][0]['statement_markdown'].endswith('$zw=wz$。')


def test_result_cannot_claim_a_source_quote_or_position_that_does_not_exist(tmp_path):
    data,lesson=example();source=tmp_path/'original_text/u.md';source.parent.mkdir();source.write_text('定理1 乘法交换律：zw=wz。')
    result=result_for(lesson);lesson['conclusions']=[result]
    assert validate_lessons(data,{'U':lesson},tmp_path)==[]
    result['source_quote']='原文没有这句话'
    assert validate_lessons(data,{'U':lesson},tmp_path)
    result['source_quote']='定理1 乘法交换律：zw=wz。';result['after']='不存在的段落'
    assert validate_lessons(data,{'U':lesson},tmp_path)


def test_preserving_one_result_does_not_allow_the_next_numbered_theorem_to_disappear(tmp_path):
    data,lesson=example();p=tmp_path/'original_text/u.md';p.parent.mkdir()
    p.write_text('定理1 乘法交换律：zw=wz。\n\n定理2 加法交换律：z+w=w+z。')
    lesson['conclusions']=[result_for(lesson)]
    errors=validate_lessons(data,{'U':lesson},tmp_path)
    assert any('定理2' in e for e in errors),'each numbered statement needs its own documented coverage'


def test_authored_coverage_uses_the_actual_visible_paragraph_without_a_duplicate_result_card(tmp_path):
    data,lesson=example();result=result_for(lesson)
    paragraph='两个数的相乘次序可以调换：$zw=wz$，这里 $z,w$ 是任意复数。'
    lesson['body_markdown']=paragraph+'\n\n[[TEACHING_VISUAL:V1]]'
    lesson['conclusions']=[result]
    lesson['knowledge_coverage']=[{'id':'U-C1','excerpts':[paragraph]}]
    source=tmp_path/'original_text/u.md';source.parent.mkdir();source.write_text(result['source_quote'])
    assert validate_lessons(data,{'U':lesson},tmp_path)==[]
    revised=apply_lessons(data,{'U':lesson})['units']['U']['study_lesson']['body_markdown']
    assert revised.count(paragraph)==1
    assert '[[TEXTBOOK_RESULT:U-C1]]' not in revised
    assert '[[TEXTBOOK_INTEGRATED:U-C1]]' in revised


def test_coverage_cannot_claim_an_absent_or_duplicate_paragraph(tmp_path):
    data,lesson=example();lesson['conclusions']=[result_for(lesson)]
    source=tmp_path/'original_text/u.md';source.parent.mkdir();source.write_text(lesson['conclusions'][0]['source_quote'])
    lesson['knowledge_coverage']=[{'id':'U-C1','excerpts':['没有讲到的内容']}]
    assert any('覆盖' in e for e in validate_lessons(data,{'U':lesson},tmp_path))
    first=lesson['body_markdown'].split('\n\n')[0]
    lesson['knowledge_coverage']=[{'id':'U-C1','excerpts':[first]}]
    lesson['body_markdown']+='\n\n'+first
    assert any('覆盖' in e for e in validate_lessons(data,{'U':lesson},tmp_path))


def test_authored_roles_bind_final_document_and_keep_coverage_main():
    from aiforstudy.blocks.content.content_contracts import content_digest
    data, lesson = example()
    lesson['reading_roles'] = [{'reader_pointer':'/body_markdown','quote':'这个观察帮助我们进入下一步。','reader_role':'proof'}]
    unit = apply_lessons(data, {'U':lesson})['units']['U']
    p = unit['teaching_presentation']
    assert p['reader_document_hash'] == content_digest(unit['reader_document'])
    assert 'proof' in p['roles'].values()
    assert 'main' in p['roles'].values()
    lesson['knowledge_coverage'] = [{'id':'C','excerpts':['这个观察帮助我们进入下一步。']}]
    with pytest.raises(ValueError, match='覆盖'):
        apply_lessons(data, {'U':lesson})


@pytest.mark.parametrize('quote', ['这个观察', '不存在'])
def test_authored_roles_reject_invalid_quotes(quote):
    data, lesson = example()
    lesson['reading_roles'] = [{'reader_pointer':'/body_markdown','quote':quote,'reader_role':'proof'}]
    with pytest.raises(Exception): apply_lessons(data, {'U':lesson})


def test_authored_projection_through_actual_renderer():
    import subprocess, json
    data, lesson = example()
    lesson['reading_roles'] = [{'reader_pointer':'/body_markdown','quote':'这个观察帮助我们进入下一步。','reader_role':'proof'}]
    unit = apply_lessons(data, {'U':lesson})['units']['U']
    proc = subprocess.run(['node', 'tests/ui/authored_projection_roundtrip.cjs'], input=json.dumps(unit), text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr
