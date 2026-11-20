"""Frozen review must not hide otherwise readable teaching text or media."""
from pathlib import Path

from aiforstudy.core.jsonio import read_json, write_json_atomic
from aiforstudy.core.review_mode import review_mode
from test_diagnostic_preview_boundary import run_fixture
from test_reader_admission import intact_drawing, product, save


def test_json_only_reader_preserves_unknown_marker_and_following_text():
    from test_content_v4_reader_contract import candidate, catalog
    from aiforstudy.blocks.content.reader_document import project_reader
    content=candidate()
    content['body_markdown']='保留前文 [[CONCEPT:missing|待解释概念]] 保留后文。'
    with review_mode('json_only'):
        document=project_reader(content,catalog()).as_dict()
    block=next(b for b in document['blocks'] if b['json_pointer']=='/body_markdown')
    assert '保留前文' in block['markdown'] and '保留后文。' in block['markdown']
    assert '待解释概念' in block['visible_text']


def test_json_only_snapshot_keeps_text_and_media_without_approval_or_hash_gate(tmp_path):
    from aiforstudy.blocks.render.diagnostics import build_reader_snapshot
    run=run_fixture(tmp_path);intact_drawing(run)
    lesson=product(run,'lesson');lesson['content_hash']='stale'
    lesson['content']['body_markdown'] += ' 本轮正文仍须展示。'
    save(run,'lesson',lesson)
    audit=product(run,'text_audit');audit['review_outcome']={'status':'known_error','reasons':['old verdict']}
    save(run,'text_audit',audit)
    with review_mode('json_only'):
        report=build_reader_snapshot(run,unit_ids=['U'],out_dir=tmp_path/'reader',require_complete_units=True)
    data=(Path(report['output_dir'])/'data/data.js').read_text()
    assert '本轮正文仍须展示。' in data
    assert 'circle' in data
    assert report['review_status']=='not_run'
    assert report['review_outcome']['status']=='unverified'
    assert report['production_accepted'] is False


def test_json_only_projection_accepts_not_run_browser_without_verified_label():
    from aiforstudy.blocks.media.composition import reader_projection_status
    with review_mode('json_only'):
        state,partial=reader_projection_status({'missing':[], 'review_outcome':{'status':'known_error'}},
                                             {'status':'not_run','ok':None})
    assert state['status']=='unverified' and state['review_status']=='not_run'
    assert partial is False


def test_json_only_animation_assembly_keeps_available_bytes_without_review(tmp_path, monkeypatch):
    from aiforstudy.blocks.render import diagnostics
    media=tmp_path/'clip.mp4';media.write_bytes(b'available video bytes')
    def forbidden(*args,**kwargs):
        raise AssertionError('frozen animation audit must not execute')
    monkeypatch.setattr(diagnostics,'_check_animation_bytes',forbidden)
    animation={'request_id':'A','resources':[{'kind':'video','relative_path':'clip.mp4','sha256':'old'}]}
    with review_mode('json_only'):
        result=diagnostics._attach_production_animation(tmp_path/'out',unit='U',book_id='B',
            animation=animation,sources={'clip.mp4':media},profile='draft',request={},
            row={'request_hash':'old'},review={'status':'known_error'})
    assert result['production_accepted'] is False
    assert result['review_outcome']['review_status']=='not_run'
    assert (tmp_path/'out'/result['base']/result['resources']['video']['relative_path']).read_bytes()==media.read_bytes()
    assert result['technical_checks']['status']=='not_run'


def test_json_only_resource_uses_available_bytes_without_old_digest_gate(tmp_path):
    from aiforstudy.blocks.render.diagnostics import _resource
    resource=tmp_path/'image.svg';resource.write_text('<svg/>')
    with review_mode('json_only'):
        assert _resource(tmp_path,{'source_dir':str(tmp_path)},
                         {'relative_path':'image.svg','sha256':'old'},{})==resource


def test_json_only_layout_places_media_despite_stale_owner_and_anchor():
    from test_content_v4_reader_contract import candidate, catalog
    from aiforstudy.blocks.content.reader_document import project_reader
    from aiforstudy.blocks.render.layout import layout_from_reader_media
    with review_mode('json_only'):
        doc=project_reader(candidate(),catalog()).as_dict()
        layout=layout_from_reader_media(doc,[{'request_id':'A','placement':{
            'target_pointer':'/missing','owner_id':'old','anchor_text':'old','span':[0,3],
            'field_hash':'old','position':'after','anchor_id':'A'}}])
    assert len(layout['mounts'])==1
    assert layout['mounts'][0]['target_pointer']=='/body_markdown'


def test_json_only_snapshot_keeps_unverified_interaction_with_no_approved_placement(tmp_path):
    from aiforstudy.blocks.render.diagnostics import build_reader_snapshot
    from test_diagnostic_animation_preview import data_at
    run=run_fixture(tmp_path);intact_drawing(run)
    plan=product(run,'media_plan');plan['decisions'].append({'handled_by':'interaction'})
    save(run,'media_plan',plan)
    model={'id':'M','kind':'jsxgraph','title':'未审核交互','code':'const x = 1;'}
    save(run,'interaction',{'models':[model],'results':[{'state':'unverified','placement':{}}],
                          'review_outcome':{'status':'unverified'}})
    with review_mode('json_only'):
        report=build_reader_snapshot(run,unit_ids=['U'],out_dir=tmp_path/'reader',require_complete_units=True)
    data=data_at(report)
    assert data['models']['U.M']['code']==model['code']
    mounts=data['units']['U']['reader_layout']['mounts']
    assert any(m['source_kind']=='interaction' and m['source_id']=='U.M' for m in mounts)


def test_json_only_review_note_is_not_a_reader_problem_without_media_plan(tmp_path):
    from aiforstudy.blocks.render.diagnostics import build_reader_snapshot
    from test_diagnostic_animation_preview import data_at
    run=run_fixture(tmp_path)
    (run/'work/products/media_plan/U.json').unlink()
    with review_mode('json_only'):
        report=build_reader_snapshot(run,unit_ids=['U'],out_dir=tmp_path/'reader',require_complete_units=True)
    data=data_at(report)
    assert report['problems']==[]
    assert data['problem_summary']==[]
    assert data['units']['U']['diagnostic_problems']==[]
    assert report['partial'] is False
    assert report['review_outcome']['review_status']=='not_run'


def test_json_only_reader_keeps_body_when_optional_collection_is_null():
    from test_content_v4_reader_contract import catalog
    from aiforstudy.blocks.content.json_only import content_record
    from aiforstudy.blocks.content.reader_document import project_reader
    raw={'body_markdown':'正常正文','examples':None}
    with review_mode('json_only'):
        content=content_record(raw)
        doc=project_reader(content,catalog()).as_dict()
    assert next(b for b in doc['blocks'] if b['json_pointer']=='/body_markdown')['raw_text']=='正常正文'
    assert content.as_dict()['examples'] is None


def test_json_only_reader_keeps_exercise_without_optional_source_metadata():
    from test_content_v4_reader_contract import catalog
    from aiforstudy.blocks.content.json_only import content_record
    from aiforstudy.blocks.content.reader_document import project_reader
    raw={'body_markdown':'正常正文','exercise_regions':[{'title':'原书习题','raw_markdown':'题目'}]}
    with review_mode('json_only'):
        content=content_record(raw)
        doc=project_reader(content,catalog()).as_dict()
    block=next(b for b in doc['blocks'] if b['json_pointer']=='/exercise_regions/0/raw_markdown')
    assert block['raw_text']=='题目' and block['raw_source'] is True
    assert block['source_lines']==[] and block['source_sha256']=='' and block['source_kind']==''
    assert 'source_sha256' not in content.as_dict()['exercise_regions'][0]


def test_json_only_normalization_preserves_usable_learning_check_metadata():
    from test_learning_checks import check_fixture
    from test_content_v4_reader_contract import catalog
    from aiforstudy.blocks.content.json_only import content_record
    from aiforstudy.blocks.content.reader_document import project_reader
    for kind in ('choice','boolean','numeric','key_step','self_explanation'):
        original=check_fixture(kind)
        with review_mode('json_only'):
            content=content_record({'body_markdown':'保留正文','learning_checks':[original]})
            doc=project_reader(content,catalog()).as_dict()
        assert doc['learning_checks'], kind
        assert doc['learning_checks'][0]['kind']==kind
        assert content.as_dict()['learning_checks'][0]==original
        text={b['json_pointer']:b['raw_text'] for b in doc['blocks']}
        assert text['/learning_checks/0/statement']==original['statement']
        assert text['/learning_checks/0/answer']==original['answer']
        assert text['/body_markdown']=='保留正文'
