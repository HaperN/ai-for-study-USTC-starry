"""Real renderer/Chromium integration; historical video is playback input only.

These fixtures do not issue a semantic MediaApproval. Production approval is
separately exercised by the fail-closed validation tests.
"""
from copy import deepcopy
import functools
import http.server
import importlib.util
import json
from pathlib import Path
import subprocess
import threading
import shutil
import pytest
from test_content_v4_reader_contract import candidate,catalog
from aiforstudy.blocks.content.content_contracts import parse_content
from aiforstudy.blocks.content.reader_document import project_reader
from support.reader_site import write_reader_fixture
from aiforstudy.blocks.media.media_requirements import resolve_bound_placement
from aiforstudy.blocks.media.evidence_digest import placements_digest
from aiforstudy.core.digest import digest_of,digest_file
from aiforstudy.errors import ContractError
from aiforstudy.core.review_mode import review_mode

ROOT=Path(__file__).resolve().parents[2]
VIDEO=ROOT.parent/'文档/02-记录与报告/Manim与审核闭环改造证据-20261007-0035/T07/media/saddle-a1-h0-k0-final'


def fixture_view(tmp_path):
    c=candidate();c['original_text']='U.md'
    c['body_markdown']+='\n\n第二段应当出现在同段两图与动画之后。'
    c=parse_content(c).as_dict();doc=project_reader(c,catalog()).as_dict()
    view=deepcopy(c);view.update(reader_document=doc,reader_media=[],figures=[])
    placements=[];figures=[];animations=[]
    svg=tmp_path/'figure.svg';svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 500"><rect width="800" height="500" fill="white"/><text x="200" y="150" font-size="40">x = 0</text></svg>')
    for rid,kind,pointer in [('F1','geometry','/body_markdown'),('F2','geometry','/body_markdown'),('A1','animation','/body_markdown'),('A2','animation','/explainables/0/markdown')]:
        block=next(b for b in doc['blocks'] if b['json_pointer']==pointer)
        anchor=block['raw_text'].split('\n\n')[0]
        p=resolve_bound_placement(c,{'target_pointer':pointer,'owner_id':block['owner_id'],'anchor_text':anchor,'span':[0,len(anchor)],'field_hash':digest_of(block['raw_text']),'position':'after'},legacy=True)
        p['anchor_id']='media-'+rid
        if kind=='animation':
            assert VIDEO.is_dir(),'real historical MP4 fixture required'
            a=json.loads((VIDEO/'animation_result.json').read_text());a['request_id']=rid
            animations.append({**a,'sources':VIDEO});resources=a['resources'];fid=None
        else:
            resources=[{'relative_path':svg.name,'kind':'svg','sha256':digest_file(svg)}];fid='FIG-'+rid
            view['figures'].append({'id':fid});figures.append({'id':fid,'title':rid,'svg':'figures/figure.svg','checks':{'ok':True}})
        view['reader_media'].append({'request_id':rid,'kind':kind,'figure_id':fid,'placement':deepcopy(p)})
        placements.append({'unit_id':'U','request_id':rid,'kind':kind,'figure_id':fid,'state':'fulfilled','content_hash':digest_of(c),'placement':p,'resources':resources})
    binding={'schema_version':'CompositionBindingV1','unit_id':'U','content_hash':digest_of(c),'view_hash':digest_of(view),
        'reader_document_hash':digest_of(doc),'reference_catalog_hash':doc['reference_catalog_hash'],'reader_media_hash':digest_of(view['reader_media']),
        'requirements_hash':'renderer-fixture-only','manifest_hash':'renderer-fixture-only','content_approval_hash':'renderer-fixture-only',
        'media_approval_hash':'renderer-fixture-only','placements_hash':placements_digest(placements),'placements':placements}
    return c,view,binding,figures,animations


@pytest.fixture(scope='module', params=['full','balanced','json_only'])
def actual_reader(tmp_path_factory, request):
    # Module fixtures run before function-scoped full_review_contracts. Bind
    # each mode explicitly so global config cannot silently choose the layout.
    with review_mode(request.param):
        return _actual_reader(tmp_path_factory)


def _actual_reader(tmp_path_factory):
    work=tmp_path_factory.mktemp('q09-reader');c,view,binding,figures,animations=fixture_view(work)
    spec=importlib.util.spec_from_file_location('q09_render_build',ROOT/'blocks/render/build.py')
    build=importlib.util.module_from_spec(spec);spec.loader.exec_module(build)
    parse=work/'parse';lessons=work/'lessons';figdir=work/'figures';out=work/'out'
    for d in (parse,lessons/'lessons',figdir):d.mkdir(parents=True,exist_ok=True)
    (parse/'chapters.json').write_text(json.dumps({'book':{'title':'阅读验收'},'chapters':[{'id':'U','title':'标题'}],'lesson_units':[{'unit_id':'U','title':'标题','original_text':'U.md'}]}))
    (parse/'concepts.json').write_text(json.dumps({'concepts':[{'id':'C','name':'冻结概念名','type':'definition'}]}))
    (parse/'U.md').write_text('原文')
    original=lessons/'approved-pure.json';original.write_text(json.dumps(c));before=original.read_bytes()
    (lessons/'lessons/U.json').write_text(json.dumps(view))
    (figdir/'figure.svg').write_bytes((work/'figure.svg').read_bytes());(figdir/'index.json').write_text(json.dumps({'figures':figures}))
    for placement in binding['placements']:
        if placement['kind']!='animation':
            for resource in placement['resources']: resource['path']=str(figdir/resource['relative_path'])
    binding['resolved_placements_hash']=placements_digest(binding['placements'])
    from aiforstudy.blocks.render.diagnostics import _attach_diagnostic_animation
    out.mkdir(exist_ok=True)
    attached=[]
    for animation in animations:
        paths={r['relative_path']:VIDEO/r['relative_path'] for r in animation['resources']}
        entry=_attach_diagnostic_animation(out,unit='U',book_id='test',animation=animation,sources=paths,profile='final')
        entry['placement']=next(p['placement'] for p in binding['placements'] if p['request_id']==animation['request_id'])
        assert not entry['missing'],entry['notes']
        attached.append(entry)
    inline={row['id']:{'id':row['id'],'source':'generated','svg':build.inline_svg((work/'figure.svg').read_text(),row['id'])} for row in figures}
    write_reader_fixture([c],catalog(),out,media={'U':view['reader_media']},figures=inline,animations={'U':attached})
    assert original.read_bytes()==before
    from test_m8_ui import make_workspace
    from test_p3_control_workbench import register_preview
    from test_animation_browser import _register_export
    from aiforstudy.ui.server import ControlServer
    workspace=make_workspace(work/'workspace');store=register_preview(workspace)
    chapter=store.work/'legacy/render/CH01';shutil.copytree(out,chapter,dirs_exist_ok=True)
    _register_export(store,chapter)
    control=ControlServer(workspace,port=0);server=control.make_server()
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    runtime=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    try:
        proc=subprocess.run([str(runtime/'bin/node'),str(ROOT/'tests/ui/reader_evidence_v5.cjs'),str(runtime/'node_modules/playwright'),
            str(ROOT/'.devtools/chrome/chrome/chrome-linux64/chrome'),f'http://127.0.0.1:{server.server_port}/artifacts/synth-book/{store.run_id}/CH01/index.html',str(work)],capture_output=True,text=True,timeout=60)
        assert proc.returncode==0,proc.stderr
        return json.loads(proc.stdout)
    finally:server.shutdown();server.server_close()


@pytest.mark.parametrize('width',['1280','390'])
def test_actual_reader_all_fields_media_positions_two_players_and_captions(actual_reader,width):
    r=actual_reader[width]
    assert r['viewport']==int(width)
    assert r['blocks']==r['expected'] and r['allVisible'] and r['noDuplicateBlocks']
    assert r['mediaOrder']==['F1','F2','A1']
    assert r['explanationAnimation'] and r['secondParagraphAfterMedia']
    assert r['smallestLabelPx']>=14
    assert all(abs(value- (780 if width=='1280' else 358))<1 for value in r['imageWidths'])
    assert r['allImagesLoaded'] and r['players']==2 and r['noOverflow']
    assert not r['errors'] and not r['externalRequests']
    for player in r['playback']:
        assert player['advanced'] and player['frameWidth']>0 and player['cues']>0 and player['seekError']<0.2


@pytest.mark.parametrize('width',['1280','390'])
def test_actual_reader_cards_have_inset_and_block_mounts(actual_reader,width):
    r=actual_reader[width]
    assert r['cardInsets'] and all(inset >= 13 for inset in r['cardInsets'])
    assert len(r['anchorTags']) == 4 and set(r['anchorTags']) == {'DIV'}
    assert not r['figureImageRole']
