"""Explicit teaching links survive relaxed metadata without new review gates."""
from copy import deepcopy
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from aiforstudy.blocks.content.reader_document import project_reader
from aiforstudy.blocks.render.diagnostics import build_reader_snapshot
from aiforstudy.core.digest import digest_of
from aiforstudy.core.jsonio import write_json_atomic
from aiforstudy.core.review_mode import review_mode
from support.reader_site import write_reader_fixture
from test_content_v4_reader_contract import candidate, catalog
from contracts.test_diagnostic_preview_boundary import run_fixture


def linked_content():
    content = candidate()
    content['body_markdown'] = ('设 $z=x+iy$。[[CONCEPT:C|共轭复数]]保持实部，改变虚部符号。'
        '[[EXPLAIN:F|看解释]][[EXAMPLE:E|看例题]][[NOTE:N|看易错点]]')
    content['explainables'][0].update(name='共轭为何关于实轴对称',
        markdown='作者解释：横坐标仍是 x，纵坐标由 y 变为 -y，因此关于实轴对称。')
    content['annotations'][0].update(title='实部不变', markdown='作者提醒：不要同时改变实部符号。')
    content['examples'][0].update(statement='求 $2+3i$ 的共轭。', approach='分别识别实部与虚部。')
    content['examples'][0]['steps'] = [{'id':'S', 'markdown':'保持 2，把 3 改为 -3，得到 $2-3i$。', 'basis':'共轭的定义。'}]
    return content


@pytest.mark.parametrize('mode', ['full', 'balanced', 'json_only'])
@pytest.mark.parametrize('title,label', [('交叉项','直觉·交叉项'),('','直觉'),(None,'直觉')])
def test_note_default_category_label_and_explicit_label_keep_the_same_target(mode, title, label):
    content=linked_content()
    content['annotations'][0].update(kind='intuition',title=title)
    if title is None: content['annotations'][0].pop('title')
    content['body_markdown']='前[[NOTE:N]]后[[NOTE:N|作者入口]]末'
    with review_mode(mode):
        doc=project_reader(content,catalog()).as_dict()
    body=next(b for b in doc['blocks'] if b['json_pointer']=='/body_markdown')
    target=next(b for b in doc['blocks'] if b['json_pointer']=='/annotations/0/markdown')
    assert body['visible_text']=='前'+label+'后作者入口末'
    assert body['markdown']==f"前[{label}](#{target['anchor']})后[作者入口](#{target['anchor']})末"
    first,second=body['marker_spans']
    assert first['synthetic'] and not second['synthetic']
    assert body['source_map'][first['visible_start']:first['visible_end']]==[1]*len(label)
    assert body['source_map'][second['visible_start']:second['visible_end']]==list(range(21,25))
    assert target['raw_text']=='作者提醒：不要同时改变实部符号。'


@pytest.mark.parametrize('mode', ['balanced', 'json_only'])
@pytest.mark.parametrize('location', ['body', 'explanation'])
def test_explicit_concept_link_supplies_its_target_without_concepts_list(mode, location):
    content=linked_content(); content['concepts']=[]
    if location == 'explanation':
        content['body_markdown']='[[EXPLAIN:F|看解释]]'
        content['explainables'][0]['markdown'] += '这里使用[[CONCEPT:C|共轭复数]]。'
    before=deepcopy(content)
    with review_mode(mode):
        document=project_reader(content,catalog()).as_dict()
    links={target for block in document['blocks'] for target in re.findall(r'\]\(#(reader-[a-f0-9]{20})\)', block['markdown'])}
    anchors={block['anchor'] for block in document['blocks']}
    assert links and links <= anchors
    assert any(b['group']=='references' and b['owner_id']=='C' for b in document['blocks'])
    assert content == before
    assert next(b for b in document['blocks'] if b['json_pointer']=='/body_markdown')['raw_text']==content['body_markdown']
    assert any('作者解释：' in b['raw_text'] for b in document['blocks'])


@pytest.mark.parametrize('mode', ['balanced', 'json_only'])
def test_unknown_concept_or_plain_term_does_not_create_requirement(mode):
    content=linked_content();content['concepts']=[]
    content['body_markdown']='共轭复数是普通正文。保留[[CONCEPT:unknown|尚无资料的概念]]。'
    with review_mode(mode):
        document=project_reader(content,catalog()).as_dict()
    assert not [b for b in document['blocks'] if b['group']=='references']
    body=next(b for b in document['blocks'] if b['json_pointer']=='/body_markdown')
    assert body['raw_text']==content['body_markdown']
    assert '尚无资料的概念' in body['visible_text']


@pytest.mark.parametrize('mode', ['full', 'balanced', 'json_only'])
def test_offline_page_expands_explicit_concept_note_explanation_and_example(tmp_path, mode):
    content=linked_content()
    if mode == 'full':
        with review_mode(mode):
            write_reader_fixture([content],catalog(),tmp_path/'reader')
        output=tmp_path/'reader'
    else:
        content['concepts']=[]
        run=run_fixture(tmp_path)
        write_json_atomic(run/'work/products/lesson/U.json',{'payload':{
            'content':content,'content_hash':digest_of(content),
            'content_identity':{'reference_catalog':catalog().as_dict()}}})
        write_json_atomic(run/'work/products/text_audit/U.json',{'payload':{
            'content_hash':digest_of(content),
            'review_outcome':{'status':'unverified','reasons':[]}}})
        with review_mode(mode):
            report=build_reader_snapshot(run,unit_ids=['U'],out_dir=tmp_path/'reader')
        output=Path(report['output_dir'])
    script=Path(__file__).resolve().parents[1]/'ui/reader_reference_links.cjs'
    result=subprocess.run([shutil.which('node'),str(script),str(output)],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    checks=json.loads(result.stdout)
    assert checks['links']==4 and checks['opened']==3 and checks['author_explanation']


@pytest.mark.parametrize('mode', ['balanced', 'json_only'])
def test_offline_unscored_question_keeps_answer_and_explains_manual_check(tmp_path, mode):
    content=linked_content()
    content['learning_checks']=[{'id':'Q','statement':'$2+3i$ 的共轭是什么？',
        'answer':'$2-3i$：实部不变，虚部变号。'}]
    with review_mode(mode):
        write_reader_fixture([content],catalog(),tmp_path/'reader')
    script=Path(__file__).resolve().parents[1]/'ui/reader_reference_links.cjs'
    result=subprocess.run([shutil.which('node'),str(script),str(tmp_path/'reader'),'unscored'],
        capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads(result.stdout)['unscored_answer_visible']


@pytest.mark.parametrize('mode', ['balanced', 'json_only'])
def test_offline_supported_question_still_grades_without_manual_only_notice(tmp_path, mode):
    from test_learning_checks import check_fixture
    first=check_fixture('boolean');second=deepcopy(first)
    first.update(statement='零是非负整数。',answer='正确。')
    second.update(id='OTHER',statement='负一是非负整数。',answer='错误。')
    second['scoring']['accepted_inputs']=[False]
    second['misconception_feedback'][0]['inputs']=[True]
    for case in second['test_cases']:
        if type(case['input']) is bool: case['input']=not case['input']
    content=linked_content();content['learning_checks']=[first,second]
    with review_mode(mode):
        write_reader_fixture([content],catalog(),tmp_path/'reader')
    script=Path(__file__).resolve().parents[1]/'ui/reader_reference_links.cjs'
    result=subprocess.run([shutil.which('node'),str(script),str(tmp_path/'reader'),'scored'],
        capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads(result.stdout)=={'automatic_scoring':'correct','questions':2}


@pytest.mark.parametrize('mode', ['balanced', 'json_only'])
@pytest.mark.parametrize('variant', ['feedback', 'no_feedback', 'one_runtime'])
def test_duplicate_question_ids_keep_separate_manual_answers(tmp_path, mode, variant):
    from test_learning_checks import check_fixture
    from aiforstudy.blocks.content.json_only import normalize_author_output
    first=check_fixture('boolean');second=deepcopy(first)
    first.update(statement='第一题：零是非负整数。',answer='第一题答案：正确。')
    second.update(statement='第二题：负一是非负整数。',answer='第二题答案：错误。')
    second['scoring']['accepted_inputs']=[False]
    second['misconception_feedback'][0]['inputs']=[True]
    if variant == 'no_feedback':
        first['misconception_feedback']=[];second['misconception_feedback']=[]
    if variant == 'one_runtime':
        second['kind']='not_an_automatic_question'
    content=linked_content();content['learning_checks']=[first]
    raw={'content':content,'learning_checks':[second]};before=deepcopy(raw)
    with review_mode(mode):
        normalized=normalize_author_output(raw)['content']
        data=write_reader_fixture([normalized],catalog(),tmp_path/'reader')
    assert raw==before
    assert [row['id'] for row in normalized['learning_checks']]==['L','L']
    runtime=data['units']['U']['reader_document']['learning_checks']
    assert len(runtime)==(1 if variant=='one_runtime' else 2)
    script=Path(__file__).resolve().parents[1]/'ui/reader_reference_links.cjs'
    result=subprocess.run([shutil.which('node'),str(script),str(tmp_path/'reader'),'duplicate_questions'],
        capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads(result.stdout)['separate_manual_questions']==2


@pytest.mark.parametrize('mode', ['balanced', 'json_only'])
def test_duplicate_example_ids_do_not_remove_either_explanation(tmp_path, mode):
    content=linked_content();second=deepcopy(content['examples'][0])
    content['examples'][0].update(statement='第一例题题目。',conclusion='第一例题结论。')
    second.update(statement='第二例题题目。',conclusion='第二例题结论。')
    content['examples'].append(second)
    with review_mode(mode):
        write_reader_fixture([content],catalog(),tmp_path/'reader')
    script=Path(__file__).resolve().parents[1]/'ui/reader_reference_links.cjs'
    result=subprocess.run([shutil.which('node'),str(script),str(tmp_path/'reader'),'duplicate_examples'],
        capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads(result.stdout)['retained_examples']==2
