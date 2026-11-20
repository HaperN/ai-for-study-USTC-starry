"""Diagnostic URLs retain complete, runnable, immutable reader snapshots."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from aiforstudy.blocks.render.diagnostics import build_diagnostic_preview
from aiforstudy.core.digest import digest_file, digest_of
from aiforstudy.core.jsonio import write_json_atomic
from aiforstudy.errors import ContractError
from aiforstudy.pipeline.stage_io import _legacy
from test_diagnostic_preview_boundary import run_fixture


def data_at(report):
    return json.loads((Path(report['output_dir'])/'data/data.js').read_text().split('=',1)[1].strip().removesuffix(';'))


def test_distinct_broken_json_has_distinct_snapshot_and_same_bytes_reuse(tmp_path):
    run=run_fixture(tmp_path);bad=run/'work/products/lesson/V.json'
    bad.write_text('{"x":?}')
    first=build_diagnostic_preview(run,unit_ids=['U','V'],out_dir=tmp_path/'preview')
    saved={str(p.relative_to(Path(first['output_dir']))):digest_file(p)
           for p in Path(first['output_dir']).rglob('*') if p.is_file()}
    assert first['source_hashes']['work/products/lesson/V.json']==digest_file(bad)
    assert build_diagnostic_preview(run,unit_ids=['U','V'],out_dir=tmp_path/'preview')==first
    bad.write_text('{"y":?}')
    second=build_diagnostic_preview(run,unit_ids=['U','V'],out_dir=tmp_path/'preview')
    assert second['snapshot_hash']!=first['snapshot_hash']
    assert saved=={str(p.relative_to(Path(first['output_dir']))):digest_file(p)
                  for p in Path(first['output_dir']).rglob('*') if p.is_file()}
    assert data_at(second)['units']['U']['reader_document']


def test_undelegated_broken_interaction_sidecar_is_not_a_reader_dependency(tmp_path):
    run=run_fixture(tmp_path);bad=run/'work/products/interaction/U.json'
    bad.parent.mkdir();bad.write_text('{broken}')
    before={str(p.relative_to(run)):digest_file(p) for p in run.rglob('*') if p.is_file()}
    report=build_diagnostic_preview(run,unit_ids=['U'],out_dir=tmp_path/'preview')
    assert data_at(report)['units']['U']['reader_document']
    assert not any(p.get('kind')=='interaction' for p in report['problems'])
    assert 'work/products/interaction/U.json' not in report['source_hashes']
    assert before=={str(p.relative_to(run)):digest_file(p) for p in run.rglob('*') if p.is_file()}


def test_required_broken_interaction_is_local_and_keeps_other_unit(tmp_path):
    from aiforstudy.blocks.content.reader_document import project_reader
    run=canvas_run(tmp_path);products=run/'work/products'
    content=json.loads((products/'lesson/U.json').read_text())['payload']['content']
    content['unit_id']='V'
    catalog=json.loads((products/'text_audit/U.json').read_text())['payload']['review_document']['reference_catalog']
    for stage,payload in {
        'lesson':{'content':content,'content_hash':digest_of(content)},
        'text_audit':{'review_document':project_reader(content,catalog).as_dict()},
        'media_plan':{'content_hash':digest_of(content),'requests':[],
                      'decisions':[{'handled_by':'interaction'}]},
    }.items():write_json_atomic(products/stage/'V.json',{'payload':payload})
    bad=products/'interaction/V.json';bad.write_text('{broken}')
    before={str(p.relative_to(run)):digest_file(p) for p in run.rglob('*') if p.is_file()}
    report=build_diagnostic_preview(run,unit_ids=['U','V'],out_dir=tmp_path/'preview')
    data=data_at(report)
    assert data['units']['U']['reader_document'] and data['models']['U.M-01']
    assert data['units']['V']['reader_document']
    assert any(p.get('unit_id')=='V' and p.get('kind')=='interaction' for p in report['missing'])
    assert report['source_hashes']['work/products/interaction/V.json']==digest_file(bad)
    assert before=={str(p.relative_to(run)):digest_file(p) for p in run.rglob('*') if p.is_file()}


def canvas_run(tmp_path):
    from test_interaction_reader_binding import interaction_case
    from test_content_v4_reader_contract import catalog
    from aiforstudy.blocks.content.reader_document import project_reader
    from aiforstudy.blocks.media.interaction_adapter import verify_template_response
    content,requirements,interaction=interaction_case()
    text='改变系数观察二维向量的线性组合。'
    content['explainables'][0].update(name='向量线性组合',markdown=text)
    content['teaching_plan']['goals'][0]['observable_task']=text
    model=interaction['models'][0]
    model.update(template='vector-combination',kind='canvas',title='向量相加',anchor_hint=text,
        why_here='改变系数，观察两个向量与合向量的关系。',
        params={'vectors':[[2,1],[1,2]],'coefficients':[1,1]},caption='合向量随系数变化')
    requirements['content_hash']=digest_of(content)
    interaction.update(content_hash=digest_of(content),requirements_hash=digest_of(requirements))
    interaction['results'][0].update(spec=deepcopy(model),checks=verify_template_response(model),
        placement={'target_pointer':'/explainables/0/markdown','owner_id':'F','anchor_text':text,'position':'after'})
    run=tmp_path/'run-canvas';products=run/'work/products'
    write_json_atomic(run/'state.json',{'run_id':'run-canvas','book_id':'test-book','status':'needs_review'})
    reader=project_reader(content,catalog()).as_dict()
    for stage,payload in {
        'lesson':{'content':content,'content_hash':digest_of(content)},
        'text_audit':{'approval':{'decision':'accepted','content_hash':digest_of(content),
            'reader_document_hash':digest_of(reader)},'review_document':reader},
        'media_plan':requirements,'interaction':interaction,
    }.items():write_json_atomic(products/stage/'U.json',{'payload':payload})
    return run


def test_canvas_diagnostic_copies_required_runtime_from_registered_models(tmp_path):
    report=build_diagnostic_preview(canvas_run(tmp_path),unit_ids=['U'],out_dir=tmp_path/'preview')
    data=data_at(report)
    assert data['counts']['canvas_models']==1
    assert not report['missing']
    assert report['review_outcome']['status']=='unverified'
    assert all(p.get('kind')=='content' for p in report['problems'])
    for name in ('three.min.js','OrbitControls.js','mathbox.min.js','mathbox.css'):
        assert (Path(report['output_dir'])/'assets/mathbox'/name).is_file(),name


@pytest.mark.parametrize('damage',['directory','mathbox.min.js'])
def test_canvas_missing_runtime_is_a_recorded_local_failure(tmp_path,monkeypatch,damage):
    renderer=_legacy('build'); vendor=tmp_path/'vendor';shutil.copytree(renderer.VENDOR,vendor)
    if damage=='directory':shutil.rmtree(vendor/'mathbox')
    else:(vendor/'mathbox'/damage).unlink()
    monkeypatch.setattr(renderer,'VENDOR',vendor)
    report=build_diagnostic_preview(canvas_run(tmp_path),unit_ids=['U'],out_dir=tmp_path/'preview')
    assert any(p.get('model_id')=='U.M-01' and 'mathbox' in p['reason'] for p in report['missing'])
    assert any(p.get('kind')=='interaction' for p in report['problems'])
    assert data_at(report)['models']['U.M-01']['diagnostic_unavailable']


@pytest.mark.parametrize('changed',['template','vendor','font','layout'])
def test_renderer_input_change_creates_new_snapshot_without_rewriting_old(tmp_path,monkeypatch,changed):
    renderer=_legacy('build')
    templates=tmp_path/'templates';vendor=tmp_path/'vendor'
    shutil.copytree(renderer.TEMPLATES,templates);shutil.copytree(renderer.VENDOR,vendor)
    monkeypatch.setattr(renderer,'TEMPLATES',templates);monkeypatch.setattr(renderer,'VENDOR',vendor)
    run=run_fixture(tmp_path)
    first=build_diagnostic_preview(run,unit_ids=['U'],out_dir=tmp_path/'preview')
    old=Path(first['output_dir'])
    before={str(p.relative_to(old)):(digest_file(p),p.stat().st_mtime_ns) for p in old.rglob('*') if p.is_file()}
    if changed=='layout':
        from aiforstudy.blocks.render import diagnostics,layout
        real=diagnostics.digest_file
        monkeypatch.setattr(diagnostics,'digest_file',lambda p: digest_of('changed layout implementation') if Path(p)==Path(layout.__file__) else real(p))
    else:
        path={'template':templates/'index.html','vendor':vendor/'marked.min.js',
              'font':vendor/'fonts/KaTeX_Main-Regular.woff2'}[changed]
        path.write_bytes(path.read_bytes()+b'\nchanged-input')
    second=build_diagnostic_preview(run,unit_ids=['U'],out_dir=tmp_path/'preview')
    assert second['snapshot_hash']!=first['snapshot_hash']
    assert second['output_dir']!=first['output_dir']
    assert before=={str(p.relative_to(old)):(digest_file(p),p.stat().st_mtime_ns) for p in old.rglob('*') if p.is_file()}


def test_same_snapshot_is_reused_without_writes_and_corruption_is_not_overwritten(tmp_path):
    run=run_fixture(tmp_path)
    first=build_diagnostic_preview(run,unit_ids=['U'],out_dir=tmp_path/'preview');old=Path(first['output_dir'])
    before={str(p.relative_to(old)):(digest_file(p),p.stat().st_mtime_ns) for p in old.rglob('*') if p.is_file()}
    assert build_diagnostic_preview(run,unit_ids=['U'],out_dir=tmp_path/'preview')==first
    assert before=={str(p.relative_to(old)):(digest_file(p),p.stat().st_mtime_ns) for p in old.rglob('*') if p.is_file()}
    (old/'index.html').write_text('tampered')
    with pytest.raises(ContractError,match='snapshot'):
        build_diagnostic_preview(run,unit_ids=['U'],out_dir=tmp_path/'preview')
    assert (old/'index.html').read_text()=='tampered'


@pytest.mark.parametrize('name,content',[('bad.svg',b'<svg><script>bad()</script></svg>'),
    ('malformed.svg',b'<svg'),('broken.png',b'not a PNG')])
def test_unsafe_static_resource_preserves_placeholder_and_other_unit(tmp_path,name,content):
    from aiforstudy.blocks.content.reader_document import project_reader
    from test_content_v4_reader_contract import catalog
    run=run_fixture(tmp_path);products=run/'work/products'
    # A second valid unit proves a bad resource cannot abort the selection.
    lesson=json.loads((products/'lesson/U.json').read_text())['payload'];c=lesson['content'];c['unit_id']='V'
    doc=project_reader(c,catalog()).as_dict()
    for stage,payload in {'lesson':{'content':c,'content_hash':digest_of(c)},
        'text_audit':{'approval':{'decision':'accepted','content_hash':digest_of(c),'reader_document_hash':digest_of(doc)},'review_document':doc},
        'media_plan':{'content_hash':digest_of(c),'requests':[],'decisions':[]}}.items():
        write_json_atomic(products/stage/'V.json',{'payload':payload})
    resource=run/'work/media/R'/name;resource.parent.mkdir(parents=True);resource.write_bytes(content)
    manifest_path=products/'figures/U.json';manifest=json.loads(manifest_path.read_text())
    manifest['payload']['requests'][0].update(source_dir=str(resource.parent),resources=[{
        'kind':'svg' if name.endswith('.svg') else 'image','relative_path':name,'sha256':digest_file(resource)}])
    write_json_atomic(manifest_path,manifest)
    report=build_diagnostic_preview(run,unit_ids=['U','V'],out_dir=tmp_path/'preview')
    assert any(p['unit_id']=='U' and p['request_id']=='R' and p['reason'] for p in report['missing'])
    data=data_at(report)
    assert data['units']['U']['reader_layout']['mounts']
    assert data['units']['V']['reader_document']
    assert not data['figures']
    assert '静态媒体安全校验失败' in data['units']['U']['reader_media'][0]['diagnostic_reason']
    assert not any(p.get('unit_id')=='V' for p in report['missing'])
    assert data['units']['V']['review_outcome']['status']=='unverified'
