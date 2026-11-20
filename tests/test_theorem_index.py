from copy import deepcopy
import json
from pathlib import Path
import pytest

from aiforstudy.blocks.content.theorem_index import build_theorem_index, available_theorem_explanations, load_theorem_rows
from aiforstudy.blocks.content.reader_document import project_reader, _anchor
from aiforstudy.blocks.content.json_only import normalize_author_output
from aiforstudy.blocks.content.review_outcome import content_identity
from aiforstudy.blocks.media.media_requirements import content_hash
from aiforstudy.core.review_mode import review_mode
from support.content_case import teaching_case


def row(unit='A', cid='C-A', name='实虚部分别收敛判据', status='verified'):
    _, source=teaching_case()
    source.update(unit_id=unit, unit={**source.get('unit',{}),'unit_id':unit})
    source['reference_catalog']={'schema_version':'FrozenReferenceCatalogV1','entries':[
        {'id':cid,'kind':'CONCEPT','label':'定理1','version':'v1','unit_id':unit,'markdown':'完整条件和结论'},
        {'id':'C-OTHER','kind':'CONCEPT','label':'另一个定理','version':'v1','unit_id':'EARLY','markdown':'前文条件'}]}
    raw={'content':{'title':'本节', 'body_markdown':'[[EXPLAIN:T|'+name+']]\n\n继续读正文。',
        'explainables':[{'id':'T','kind':'theorem','concept_id':cid,'name':name,'what':'条件完整，结论完整。',
            'why':'把复数问题分解为两个实数问题。','markdown':'',
            'steps':[{'id':'s1','no':1,'markdown':'由[[CONCEPT:C-OTHER|另一依据]]给出完整第一步。','basis':''},
                     {'id':'s2','no':2,'markdown':'完整第二步。','basis':''}]}],
        'annotations':[{'id':'note','kind':'pitfall','title':'无需依赖的批注','markdown':'不会复制整个课程。'}]}}
    with review_mode('balanced'):
        content=normalize_author_output(raw,source)['content']
        reader=project_reader(content,source['reference_catalog']).as_dict()
    return {'content':content,'reader_document':reader,'reference_catalog':source['reference_catalog'],
            'review_outcome':{'status':status}}


def test_index_reuses_exact_first_object_and_keeps_proof_separate_without_mutation():
    first=row();before=deepcopy(first)
    result=build_theorem_index([first])
    entry=result['by_unit']['A']['C-A']
    assert entry['name']=='实虚部分别收敛判据'
    assert entry['source_label']=='定理1'
    assert any('条件完整' in b['raw_text'] for b in entry['core_blocks'])
    assert {b['step_id'] for b in entry['proof_blocks'] if b['step_id']} == {'s1','s2'}
    assert any(b['owner_id']=='C-OTHER' for b in entry['reader_document']['blocks'])
    assert not any(b['owner_id']=='note' for b in entry['reader_document']['blocks'])
    assert result['refs_by_unit']['A'][_anchor('explainables','T')]=={'unit_id':'A','concept_id':'C-A'}
    assert first==before


def test_same_name_and_local_explanation_id_do_not_merge_other_origins():
    a,b=row(),row('B','C-B','另一个收敛判据')
    result=build_theorem_index([a,b])
    assert result['by_unit']['A']['C-A']['name']!=result['by_unit']['B']['C-B']['name']
    assert result['refs_by_unit']['A'][_anchor('explainables','T')]['unit_id']=='A'
    assert result['refs_by_unit']['B'][_anchor('explainables','T')]['unit_id']=='B'


def test_reused_theorem_explanation_cannot_replace_its_first_definition():
    a=row();b=row('B','C-A','后文不应改名')
    b['reference_catalog']['entries'][0]['unit_id']='A'
    result=build_theorem_index([b,a])
    assert 'B' not in result['by_unit']
    assert result['refs_by_unit']['B'][_anchor('references','C-A')]=={'unit_id':'A','concept_id':'C-A'}
    assert result['by_unit']['A']['C-A']['name']==a['content']['explainables'][0]['name']


def test_ambiguous_duplicate_first_objects_are_not_guessed():
    a=row();a['content']['explainables'].append({**a['content']['explainables'][0],'id':'T2','name':'冲突名称'})
    result=build_theorem_index([a]);assert not result['by_unit']


@pytest.mark.parametrize('other_kind', ['theorem', 'concept'])
def test_shared_explanation_id_cannot_mix_distinct_theorem_proofs(other_kind):
    a=row()
    a['reference_catalog']['entries'].append({**a['reference_catalog']['entries'][0], 'id':'C-SECOND'})
    a['content']['explainables'].append({**deepcopy(a['content']['explainables'][0]),
        'concept_id':'C-SECOND', 'kind':other_kind, 'name':'同一局部 ID 的另一定理',
        'steps':[{'id':'different-proof', 'no':1, 'markdown':'不能混入第一条定理的证明。', 'basis':''}]})
    with review_mode('balanced'):
        a['reader_document']=project_reader(a['content'],a['reference_catalog']).as_dict()
    assert not build_theorem_index([a])['by_unit']


def save(run,r,audit_status='verified',audit_hash=None):
    content=r['content'];unit=content['unit_id'];ch=content_hash(content)
    with review_mode('balanced'):
        identity=content_identity(content,r['reference_catalog'],policy_digest='test-policy')
    lesson={'content':content,'content_hash':ch,'content_identity':identity}
    audit={'content_hash':audit_hash or ch,'content_identity':deepcopy(identity),'review_outcome':{'status':audit_status}}
    for kind,payload in [('lesson',lesson),('text_audit',audit)]:
        p=run/'work/products'/kind/(unit+'.json');p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(json.dumps({'payload':payload}))


def product_path(run,stage,unit='A'):
    return run/'work/products'/stage/(unit+'.json')


@pytest.mark.parametrize('stage', ['lesson', 'text_audit'])
def test_changed_catalog_identity_cannot_certify_reusable_teaching(tmp_path,stage):
    save(tmp_path,row())
    path=product_path(tmp_path,stage)
    record=json.loads(path.read_text())
    record['payload']['content_identity']['reference_catalog']['entries'][0]['markdown']='未绑定审核的另一条结论。'
    path.write_text(json.dumps(record))
    with review_mode('balanced'):
        assert available_theorem_explanations(tmp_path,'B',unit_order=['A','B'])==[]
        rows=load_theorem_rows(tmp_path)
    if stage=='text_audit':
        assert len(rows)==1 and rows[0]['review_outcome']['status']=='unverified'
    else:
        assert rows==[]


def save_current_head(run,r):
    from aiforstudy.contracts.artifact import ArtifactRef
    unit=r['content']['unit_id']
    base=run/'work/products/content'/unit
    revision=base/'revisions/000001.json';revision.parent.mkdir(parents=True,exist_ok=True)
    revision.write_text(json.dumps(r['content']))
    ref=ArtifactRef(book_id=r['content']['book_id'],kind='lesson.content',logical_key=unit,
                    path=revision).with_content().as_dict()
    path=product_path(run,'lesson',unit);record=json.loads(path.read_text())
    record['payload']['content_ref']=ref;path.write_text(json.dumps(record))
    head=base/'current.json'
    head.write_text(json.dumps({'content_hash':content_hash(r['content']),'content_ref':ref}))
    return head,revision


@pytest.mark.parametrize('corruption', ['old-head', 'different-ref', 'changed-revision', 'different-content'])
def test_current_head_and_immutable_revision_control_reuse(tmp_path,corruption):
    a=row();save(tmp_path,a);head,revision=save_current_head(tmp_path,a)
    with review_mode('balanced'):
        assert len(available_theorem_explanations(tmp_path,'B',unit_order=['A','B']))==1
    if corruption in {'changed-revision','different-content'}:
        revision.write_text(json.dumps({**a['content'],'title':'另一个版本'}))
        if corruption=='different-content':
            from aiforstudy.core.digest import digest_file
            current=json.loads(head.read_text())
            current['content_ref']['content_digest']=digest_file(revision)
            head.write_text(json.dumps(current))
            lesson=product_path(tmp_path,'lesson');record=json.loads(lesson.read_text())
            record['payload']['content_ref']=current['content_ref'];lesson.write_text(json.dumps(record))
    else:
        current=json.loads(head.read_text())
        if corruption=='old-head':
            current['content_hash']='sha256:older-current-content'
        else:
            duplicate=revision.with_name('000002.json');duplicate.write_bytes(revision.read_bytes())
            current['content_ref']['path']=str(duplicate)
        head.write_text(json.dumps(current))
    with review_mode('balanced'):
        assert available_theorem_explanations(tmp_path,'B',unit_order=['A','B'])==[]
        assert load_theorem_rows(tmp_path)==[]


@pytest.mark.parametrize('relative', ['lesson/A.json', 'text_audit/A.json',
                                     'content/A/current.json', 'figures/A.json', 'audit/A.json'])
def test_damaged_optional_reuse_input_does_not_interrupt_the_next_author(tmp_path,relative):
    save(tmp_path,row())
    path=tmp_path/'work/products'/relative;path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('{unfinished')
    with review_mode('balanced'):
        assert available_theorem_explanations(tmp_path,'B',unit_order=['A','B'])==[]


@pytest.mark.parametrize('stage', ['figures', 'audit'])
@pytest.mark.parametrize('finding', ['current', 'stale', 'closed', 'other-rule'])
def test_media_content_findings_override_text_approval_only_for_current_open_error(tmp_path,stage,finding):
    from aiforstudy.pipeline.diagnostics import technical_ticket
    a=row();save(tmp_path,a)
    ticket=technical_ticket(unit_id='A',artifact_id='lesson:A',
        artifact_hash='old-hash' if finding=='stale' else content_hash(a['content']),
        problem='共轭变号的正文结论错误。',source='共轭变号的正文结论错误。',
        owner='lesson',kind='text_content',allowed_paths=('/body_markdown',),
        action='修正正文中的符号。',expected='正文与正确的共轭运算一致。',
        rule='STATIC_LAYOUT' if finding=='other-rule' else 'POST_MEDIA_CONTENT').as_dict()
    if finding=='closed':
        ticket['status']='closed'
    path=product_path(tmp_path,stage);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({'payload':{'tickets':[ticket]}}))
    with review_mode('balanced'):
        rows=load_theorem_rows(tmp_path)
        available=available_theorem_explanations(tmp_path,'B',unit_order=['A','B'])
    assert len(rows)==1
    if finding=='current':
        assert rows[0]['review_outcome']['status']=='known_error'
        assert ticket['problem'] in rows[0]['review_outcome']['reasons']
        assert rows[0]['review_outcome']['upstream'][0]['status']=='verified'
        assert available==[]
    else:
        assert rows[0]['review_outcome']['status']=='verified'
        assert len(available)==1


def test_author_support_only_uses_completed_verified_earlier_units(tmp_path):
    save(tmp_path,row('A','C-A'));save(tmp_path,row('B','C-B'),audit_status='known_error');save(tmp_path,row('D','C-D'))
    with review_mode('balanced'):
        entries=available_theorem_explanations(tmp_path,'C',unit_order=['A','B','C','D'])
    assert [(e['unit_id'],e['concept_id']) for e in entries]==[('A','C-A')]
    assert entries[0]['steps'][1]['markdown']=='完整第二步。'
    assert entries[0]['review_outcome']['status']=='verified'


def test_stale_audit_and_unknown_reading_order_do_not_claim_completed_support(tmp_path):
    save(tmp_path,row(),audit_hash='old-revision')
    with review_mode('balanced'):
        assert available_theorem_explanations(tmp_path,'B',unit_order=['A','B'])==[]
        assert available_theorem_explanations(tmp_path,'B')==[]


def test_chapter_snapshot_packages_earlier_theorem_without_adding_earlier_page(tmp_path):
    from aiforstudy.blocks.render.diagnostics import build_reader_snapshot
    from aiforstudy.core.digest import digest_file
    run=tmp_path/'run';a=row();b=row('B','C-B')
    b['reference_catalog']['entries'].append(deepcopy(a['reference_catalog']['entries'][0]))
    b['content']['body_markdown']='此处引用[[CONCEPT:C-A|实虚部分别收敛判据]]，留在本节阅读。'
    b['content']['explainables']=[]
    with review_mode('balanced'):
        b['reader_document']=project_reader(b['content'],b['reference_catalog']).as_dict()
        save(run,a);save(run,b)
        (run/'state.json').write_text(json.dumps({'book_id':'demo','run_id':'test-index'}))
        before={str(p):digest_file(p) for p in run.rglob('*') if p.is_file()}
        report=build_reader_snapshot(run,unit_ids=['B'],out_dir=tmp_path/'page')
        js=(Path(report['output_dir'])/'data/data.js').read_text()
        data=json.loads(js.split('window.__DATA__ = ',1)[1].rsplit(';',1)[0])
        assert list(data['units'])==['B']
        assert data['theorem_index']['by_unit']['A']['C-A']['proof_blocks']
        assert data['units']['B']['theorem_refs'][_anchor('references','C-A')]=={'unit_id':'A','concept_id':'C-A'}
        assert 'work/products/lesson/A.json' in report['source_hashes']
        assert {str(p):digest_file(p) for p in run.rglob('*') if p.is_file()}==before
        a['content']['explainables'][0]['steps'][1]['markdown']='修正后的完整第二步。'
        save(run,a)
        refreshed=build_reader_snapshot(run,unit_ids=['B'],out_dir=tmp_path/'page')
        assert refreshed['snapshot_hash']!=report['snapshot_hash']
        assert (Path(report['output_dir'])/'data/data.js').read_text()==js
