"""Independent integration regressions for media generation/review separation."""
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import pytest

from aiforstudy.core.digest import digest_file
from aiforstudy.pipeline.services import CancelToken


def test_animation_cache_retains_current_review_identity_and_discards_old_verdict(monkeypatch,tmp_path):
    from aiforstudy.pipeline import media,stage_io
    from aiforstudy.blocks.visual.result import AnimationResult,AnimationResource
    from aiforstudy.blocks.visual.spec import parse_visual_spec
    from test_animation_teaching_bindings import derivative_animation_case,ROOT
    context,spec=derivative_animation_case();data=context._data();request=context.request
    request['spec']=spec
    ctx=SimpleNamespace(workspace=SimpleNamespace(root=ROOT),node=SimpleNamespace(stage='figures'),
        services=SimpleNamespace(cancel=CancelToken(),control=SimpleNamespace(check=lambda:None,work_scope=lambda scope:nullcontext()),
            store=SimpleNamespace(work=tmp_path,run_id='independent-review')))
    identity={'value':'review-old'};renders=[]
    monkeypatch.setattr(media,'_animation_runtime_identity',lambda ctx:{'environment':'test'})
    monkeypatch.setattr(media,'review_identity',lambda *a:identity['value'])
    monkeypatch.setattr(stage_io,'_gateway_client',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('no model transport expected')))
    def factory(**kw):
        def render(profile,planned):
            renders.append(profile);base=tmp_path/profile;base.mkdir()
            file=base/'video.mp4';file.write_bytes(b'render-boundary')
            # This test only exercises reuse of a renderer receipt. Media
            # validation separately rejects these deliberately non-video bytes.
            result=AnimationResult(request_id=request['request_id'],status='needs_review',
                spec_hash=parse_visual_spec(spec).spec_hash,compiler_hash='sha256:'+'a'*64,environment_hash='sha256:'+'b'*64,
                math_report={'status':'pass'},technical_report={'ffmpeg_decode_ok':True},
                resources=(AnimationResource(kind='mp4',relative_path=file.name,sha256=digest_file(file),
                    bytes=file.stat().st_size,mime='video/mp4'),))
            return result,str(base)
        return render
    monkeypatch.setattr(media.animation,'manim_renderer',factory)
    pairs=[('body',SimpleNamespace(request_id=request['request_id']),request)]
    caps=SimpleNamespace(render_backends=lambda:['manim'])
    rows,*_=media.fulfill(ctx,pairs,data['content'],{},caps,content_approval=data['approval'])
    rows[0]['validation']={'old_validation_sentinel':True}
    original=deepcopy(rows)
    identity['value']='review-current'
    cached,*_=media.fulfill(ctx,pairs,data['content'],{'requests':rows},caps,content_approval=data['approval'])
    assert cached[0]['cache_hit'] and renders==['draft','final']
    assert cached[0]['review_dependency_hash']=='review-current'
    assert 'validation' not in cached[0]
    assert cached[0]['resources']==original[0]['resources'] and rows==original


@pytest.mark.parametrize('asset,invalidates_review', [('media-surface.css',True),('styles.css',False)])
def test_shared_geometry_invalidates_review_but_decoration_does_not(monkeypatch,tmp_path,asset,invalidates_review):
    from aiforstudy.blocks.media.dependencies import resource_dependencies
    from aiforstudy.pipeline import stage_io
    css=tmp_path/'blocks/render/assets'/asset;css.parent.mkdir(parents=True)
    ctx=SimpleNamespace(workspace=SimpleNamespace(root=tmp_path),services=SimpleNamespace())
    request={'request_id':'R','kind':'geometry','acceptance_criteria':[]}
    monkeypatch.setattr(stage_io,'_repo_root',lambda:tmp_path)
    css.write_text('.reader-media{width:100%}')
    before=resource_dependencies(ctx,request,{})
    css.write_text('.reader-media{width:100px}')
    after=resource_dependencies(ctx,request,{})
    assert before['generation_dependency_hash']==after['generation_dependency_hash']
    assert (before['review_dependency_hash']!=after['review_dependency_hash']) is invalidates_review
    # Functionally harmful styles.css clipping is rejected by the real production-browser
    # tests; unrelated decoration does not make approved media regenerate.


def test_static_evidence_supplement_reuses_exact_existing_resources(monkeypatch,tmp_path):
    from aiforstudy.blocks.media import execution
    from test_semantic_static_media import context_case,vector,geom_spec
    _,content,approval,request=context_case(objects=[vector('a',[2,1]),vector('b',[1,2]),vector('c',[3,3])])
    request['spec']=geom_spec()
    first=execution.execute_static(request,content=content,approval=approval,out_dir=tmp_path)
    assert first['state']=='rendered',first['reason']
    original=deepcopy(first)
    def forbidden(*args,**kwargs):
        raise AssertionError('evidence-only must not regenerate mathematical resources')
    monkeypatch.setattr(execution,'rasterize_svg',forbidden)
    second=execution.execute_static(request,content=content,approval=approval,out_dir=tmp_path,
        previous=first,evidence_only=True)
    assert second['state']=='rendered',second['reason']
    assert second['resources']==first['resources'] and first==original
    assert all(digest_file(Path(second['source_dir'])/r['relative_path'])==r['sha256'] for r in second['resources'])
