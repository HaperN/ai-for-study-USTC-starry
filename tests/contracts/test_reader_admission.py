"""Teaching HTML retains current text and lists unavailable text/media."""
from copy import deepcopy
from pathlib import Path

import pytest

from aiforstudy.blocks.render.diagnostics import build_reader_snapshot
from aiforstudy.core.digest import digest_file, digest_of
from aiforstudy.core.jsonio import read_json, write_json_atomic
from aiforstudy.errors import ContractError
from test_diagnostic_preview_boundary import run_fixture
from test_diagnostic_animation_preview import animation_fixture, data_at


def product(run, stage):
    return read_json(run/'work/products'/stage/'U.json')['payload']


def save(run, stage, payload):
    write_json_atomic(run/'work/products'/stage/'U.json', {'payload': payload})


def teaching(run, out, units=None):
    return build_reader_snapshot(run, unit_ids=units or ['U'], out_dir=out,
                                 require_complete_units=True)


def intact_drawing(run):
    current_plan(run)
    file=run/'work/media/R/figure.svg'
    file.parent.mkdir(parents=True)
    file.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="20"/></svg>')
    plan=product(run, 'media_plan'); manifest=product(run, 'figures')
    manifest['requests'][0].update(request_hash=digest_of(plan['requests'][0]),
        source_dir=str(file.parent), resources=[{'kind':'svg','relative_path':file.name,'sha256':digest_file(file)}])
    save(run,'figures',manifest)


def current_plan(run, kind='drawing', *, has_media=True):
    from test_media_decisions_v2 import math_request_case
    from support.content_case import author_media, reference_catalog, none_decision, reviewed_plan
    from aiforstudy.blocks.content.review_outcome import content_identity
    from aiforstudy.blocks.media.media_planning import plan_media_requirements
    content, _, raw, _ = math_request_case('animation' if kind=='animation' else 'geometry')
    content['book_id']='test-book'
    if not has_media: raw={'decisions':[none_decision()],'requests':[]}
    identity=content_identity(content,reference_catalog(),policy_digest='sha256:policy')
    plan=plan_media_requirements(content,identity,author_requirements=author_media(content,raw),
        capabilities={'kinds':['drawing','animation'],'code_generation':'GeneratedMediaCodeV1'})
    reviewed_plan(content, plan)
    save(run,'lesson',{'content':content,'content_hash':digest_of(content)})
    audit=product(run,'text_audit')
    audit.update(content_hash=digest_of(content),content_identity=identity,approval={},
                 review_document={'reference_catalog':reference_catalog().as_dict()})
    save(run,'text_audit',audit)
    save(run,'media_plan',plan)
    manifest=product(run,'figures')
    manifest.update(content_hash=digest_of(content),requirements_hash=digest_of(plan))
    if has_media:
        request=plan['requests'][0]; row=manifest['requests'][0]
        row.update(request_id=request['request_id'],request_hash=digest_of(request),kind=request['kind'])
        if row.get('animation'): row['animation']['request_id']=request['request_id']
    else: manifest['requests']=[]
    save(run,'figures',manifest)


@pytest.mark.parametrize('damage', ['body', 'plan', 'media', 'request_hash', 'duplicate', 'bytes'])
def test_missing_or_invalid_media_never_hides_current_body(tmp_path, damage):
    run=run_fixture(tmp_path)
    intact_drawing(run)
    if damage in {'body','plan'}:
        stage='lesson' if damage=='body' else 'media_plan'
        (run/'work/products'/stage/'U.json').unlink()
    else:
        manifest=product(run,'figures')
        if damage=='media': manifest['requests'][0]['resources']=[]
        if damage=='request_hash': manifest['requests'][0]['request_hash']=digest_of('old request')
        if damage=='duplicate': manifest['requests'].append(deepcopy(manifest['requests'][0]))
        if damage=='bytes': (run/'work/media/R/figure.svg').write_text('changed')
        save(run,'figures',manifest)
    report=teaching(run,tmp_path/'reader')
    data=data_at(report)
    assert report['rendered_units']==([] if damage=='body' else ['U'])
    assert not data['figures']
    assert report['partial'] is (damage in {'body','media','bytes'})
    assert data['problem_summary'][0]['unit_id']=='U'
    assert report['production_accepted'] is False
    if damage in {'plan','request_hash','duplicate'}:
        assert report['missing']==[]
        assert data['problem_summary'][0]['status']=='unverified'
        assert any(row['kind']=='media_plan' for row in report['problems'])


def test_partial_teaching_html_contains_complete_unknown_unit_and_missing_summary(tmp_path):
    run=run_fixture(tmp_path); intact_drawing(run)
    report=teaching(run,tmp_path/'reader',['U','V'])
    data=data_at(report)
    assert report['rendered_units']==['U']
    assert report['render_mode']=='teaching'
    assert set(data['units'])=={'U'}
    assert data['scope']==['U']
    assert data['units']['U']['reader_document']
    assert data['units']['U']['review_outcome']['status']=='unverified'
    assert data['unavailable_units'][0]['unit_id']=='V'
    assert any(row['unit_id']=='V' and row['status']=='missing' for row in report['problem_summary'])
    assert 'reader-problem-summary' not in (Path(report['output_dir'])/'index.html').read_text()
    assert report['partial'] and report['production_accepted'] is False


@pytest.mark.parametrize('damage', [None, 'video', 'captions', 'poster'])
def test_animation_bytes_checked_before_teaching_html(tmp_path, damage):
    run,source,path=animation_fixture(tmp_path)
    current_plan(run,'animation')
    manifest=product(run,'figures'); row=manifest['requests'][0]
    row.update(request_hash=digest_of(product(run,'media_plan')['requests'][0]),render_profile='final')
    if damage:
        kind={'video':'mp4','captions':'captions','poster':'poster'}[damage]
        resource=next(r for r in row['resources'] if r['kind']==kind)
        file=source/resource['relative_path']; file.write_bytes(b'invalid media')
        resource.update(sha256=digest_file(file),bytes=file.stat().st_size)
        row['animation']['resources']=deepcopy(row['resources'])
    save(run,'figures',manifest)
    if damage:
        report=teaching(run,tmp_path/'reader')
        assert report['rendered_units']==['U'] and report['partial']
        assert not data_at(report)['animations']
    else:
        report=teaching(run,tmp_path/'reader')
        assert report['rendered_units']==['U']
        assert data_at(report)['animations']['U'][0]['missing']==[]


def test_explicit_no_media_plan_can_render_without_figures(tmp_path):
    run=run_fixture(tmp_path)
    current_plan(run,has_media=False)
    (run/'work/products/figures/U.json').unlink()
    assert teaching(run,tmp_path/'reader')['rendered_units']==['U']


@pytest.mark.parametrize('schema',[None,'MediaRequirementsV1','foreign'])
@pytest.mark.parametrize('reviewed',[False,True])
def test_legacy_empty_requests_are_not_author_no_media_decision(tmp_path,schema,reviewed):
    run=run_fixture(tmp_path)
    plan=product(run,'media_plan');plan.update(requests=[],decisions=[],schema_version=schema)
    if reviewed:
        # Preserve the full-mode boundary with an actual reconstructable
        # approval, not a handwritten verified label on the unreviewed fixture.
        from support.content_case import approved_inputs, issue
        inputs=approved_inputs()
        content,source,_,document,reports,math=inputs
        save(run,'lesson',{'content':content,'content_hash':digest_of(content)})
        save(run,'text_audit',{'content_hash':digest_of(content),'source_context':source,
            'review_document':document.as_dict(),
            'check_reports':[r.as_dict() if hasattr(r,'as_dict') else r for r in reports],
            'math_report':math,'approval':issue(inputs).as_dict(),
            'review_outcome':{'status':'verified','reasons':[]}})
        plan['content_hash']=digest_of(content)
    save(run,'media_plan',plan)
    report=teaching(run,tmp_path/'reader')
    assert report['rendered_units']==['U'] and report['partial'] is reviewed
    assert report['production_accepted'] is False
    assert report['problem_summary'][0]['status']=='unverified'
    assert any(row['kind']=='media_plan' for row in report['problems'])
    if reviewed:
        assert any(row['kind']=='media_plan' for row in report['missing'])
    else:
        assert report['missing']==[]
