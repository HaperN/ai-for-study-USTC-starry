"""Approved mathematical objects, semantic data, and pixels stay bound together."""
from copy import deepcopy
import importlib
import pytest
from aiforstudy.core.digest import digest_of
from aiforstudy.errors import ContractError
from test_media_decisions_v2 import math_request_case, Planner
from support.content_case import author_media
from aiforstudy.blocks.media.media_planning import plan_media_requirements


_UNSET=object()


def case(objects=None, symbols=None, conditions=None, kind='geometry'):
    from aiforstudy.blocks.math import parse_math_model
    c,a,raw,caps=math_request_case(kind)
    # ⚠️ 不能用 `symbols or []` —— 传入的符号表可能是**空列表**（合法：模型无符号），
    # 而空列表是假值，会被 `or` 静默丢掉，导致"显式声明的符号"失效、模型解析报未声明符号。
    m=deepcopy(c['math_models'][0]); m['objects']=objects or m['objects']
    m['symbols']=m['symbols'] if symbols is None else symbols
    m['conditions']=m['conditions'] if conditions is None else conditions
    oid=m['objects'][0]['math_object_id']; m['claims']=[{'claim_id':'MC','kind':'identity','math_object_id':oid,'lhs_object_id':oid,'rhs_object_id':oid}]
    model=parse_math_model(m); c['math_models']=[model.as_dict()]; a['content_hash']=digest_of(c,check=False)
    raw['requests'][0]['math_model_refs']=[{'model_id':'M','digest':model.digest}]
    raw['requests'][0]['object_refs']=[{'model_id':'M','object_id':o['math_object_id']} for o in m['objects']]
    r=plan_media_requirements(c,a,capabilities=caps,client=Planner(raw),author_requirements=author_media(c,raw))['requests'][0]
    return c,a,r


def context_case(**kw):
    module=importlib.import_module('aiforstudy.blocks.media.math_context')
    c,a,r=case(**kw)
    return module.ApprovedMathContext.from_approved_content(c,a,r),c,a,r


def const(v): return {'op':'const','value':v}
def vector(ident,values): return {'math_object_id':ident,'kind':'vector','entries':[const(x) for x in values]}
def matrix(ident,rows): return {'math_object_id':ident,'kind':'matrix','rows':[[const(x) for x in r] for r in rows]}
def ref(oid): return {'model_id':'M','object_id':oid}


def test_approved_context_exists_only_after_receipt_and_request_validation():
    import aiforstudy.blocks.media as media
    assert hasattr(importlib.import_module(media.__name__+'.execution'),'execute_static')
    assert importlib.util.find_spec(media.__name__+'.math_context') is not None, 'missing approved context factory'
    ctx,c,a,r=context_case()
    assert ctx.model_by_id('M').digest==r['math_model_refs'][0]['digest']
    assert ctx.goal_claim_ids('G')==('K',)
    assert ctx.claim_model_ids('K')==('M',)
    assert ctx.content_digest==digest_of(c,check=False)
    with pytest.raises(ContractError): type(ctx)(c,a,r)
    for broken in ({},dict(a,content_revision=99)):
        with pytest.raises(ContractError): type(ctx).from_approved_content(c,broken,r)
    c['summary']='mutated'
    with pytest.raises(ContractError): type(ctx).from_approved_content(c,a,r)


def test_context_nested_values_are_defensive_and_unbound_objects_rejected():
    assert importlib.util.find_spec('aiforstudy.blocks.media.math_context') is not None
    ctx,c,a,r=context_case(objects=[vector('a',[1,2]),vector('b',[3,4])])
    r['object_refs'].pop(); ctx=type(ctx).from_approved_content(c,a,r)
    ctx.request['object_refs'].clear()
    assert ctx.object_by_ref(ref('a'))['entries'][0]==const(1)
    with pytest.raises(ContractError): ctx.object_by_ref(ref('b'))
    with pytest.raises(ContractError): ctx.model_by_id('unknown')


def compiler(kind):
    name='aiforstudy.blocks.media.'+kind
    assert importlib.util.find_spec(name) is not None, 'missing semantic '+kind+' executor'
    return importlib.import_module(name)


def plot_spec():
    return {'schema_version':'MediaPlotV2','series':[{'id':'curve','object_ref':ref('f'),'variable':'x','domain':[-2,2]}], 'layout':{}}


def test_reciprocal_never_draws_a_segment_across_discontinuity():
    mod=compiler('plot')
    ctx,*_=context_case(kind='plot',objects=[{'math_object_id':'f','kind':'scalar','expr':{'op':'div','left':const(1),'right':{'op':'var','name':'x'}}}],symbols=[{'name':'x','domain':'real'}])
    product=mod.compile_plot(plot_spec(),ctx)
    curves=product['data']['series'][0]['segments']
    assert len(curves)==2 and all(not (min(p[0] for p in s)<0<max(p[0] for p in s)) for s in curves)
    assert product['data']['series'][0]['excluded_points']==[0.0]
    assert product['object_bindings'][0]['object_ref']==ref('f')
    assert '<svg' in product['svg'] and '1' in product['svg']


def test_plot_rejects_python_free_coordinates_and_formula_override():
    mod=compiler('plot'); ctx,*_=context_case(kind='plot')
    for bad in ({'code':'__import__("os")'},{'points':[[0,0],[1,5]]},{'expr':const(99)}):
        spec=plot_spec(); spec['series'][0].update(bad)
        with pytest.raises(ContractError): mod.compile_plot(spec,ctx)


def geom_spec():
    return {'schema_version':'MediaGeometryV2','construction':{'kind':'vector_sum','a':ref('a'),'b':ref('b'),'result':ref('c')},'layout':{}}


def test_vector_sum_reads_approved_values_and_rejects_false_relation():
    mod=compiler('geometry'); ctx,*_=context_case(objects=[vector('a',[2,1]),vector('b',[1,2]),vector('c',[3,3])])
    result=mod.compile_geometry(geom_spec(),ctx)
    assert result['data']['result']==[3,3]
    assert result['relations'][0]['status']=='pass'
    wrong,*_=context_case(objects=[vector('a',[2,1]),vector('b',[1,2]),vector('c',[4,3])])
    with pytest.raises(ContractError,match='content'): mod.compile_geometry(geom_spec(),wrong)


def test_parallel_affine_plane_cannot_be_called_a_subspace():
    mod=compiler('geometry'); ctx,*_=context_case(objects=[vector('n',[0,0,1]),{'math_object_id':'d','kind':'scalar','expr':const(2)}])
    spec={'schema_version':'MediaGeometryV2','construction':{'kind':'plane','normal':ref('n'),'offset':ref('d'),'classification':'subspace'},'layout':{}}
    with pytest.raises(ContractError,match='content'): mod.compile_geometry(spec,ctx)
    spec['construction']['classification']='affine'
    result=mod.compile_geometry(spec,ctx)
    assert result['data']['contains_origin'] is False
    assert all(abs(p[2]-2)<1e-12 for p in result['data']['vertices'])


def test_geometry_label_binding_cannot_be_swapped_or_hidden():
    mod=compiler('geometry'); ctx,*_=context_case(objects=[vector('a',[2,1]),vector('b',[1,2]),vector('c',[3,3])])
    for bad in ({'labels':{'a':{'object_ref':ref('b')}}},{'opacity':0},{'display':'none'}):
        spec=geom_spec(); spec['layout']=bad
        with pytest.raises(ContractError): mod.compile_geometry(spec,ctx)


def diagram_spec():
    return {'schema_version':'MediaDiagramV2','construction':{'kind':'row_operation','before':ref('A'),'after':ref('B'),'operation':{'kind':'add_multiple','source':0,'target':1,'factor':ref('k')}},'layout':{}}


def test_matrix_diagram_independently_checks_each_row_operation():
    mod=compiler('diagram'); ctx,*_=context_case(kind='diagram',objects=[matrix('A',[[1,2],[3,4]]),matrix('B',[[1,2],[0,-2]]),{'math_object_id':'k','kind':'scalar','expr':const(-3)}])
    result=mod.compile_diagram(diagram_spec(),ctx)
    assert result['data']['after']==[[1,2],[0,-2]]
    assert result['relations'][0]['rule_id']=='matrix.row_operation.v1'
    wrong,*_=context_case(kind='diagram',objects=[matrix('A',[[1,2],[3,4]]),matrix('B',[[1,2],[0,2]]),{'math_object_id':'k','kind':'scalar','expr':const(-3)}])
    with pytest.raises(ContractError,match='content'): mod.compile_diagram(diagram_spec(),wrong)


def test_execution_requires_approval_and_rejects_legacy_arbitrary_svg(tmp_path):
    from aiforstudy.blocks.media.execution import execute_static
    c,a,r=case(); r['spec']=geom_spec()
    row=execute_static(r,content=c,out_dir=tmp_path)
    assert row['state']=='failed' and 'approval' in row['reason'].lower()
    row=execute_static(dict(r,kind='svg'),content=c,approval=a,out_dir=tmp_path)
    assert row['state']=='failed' and 'kind' in row['reason']


def test_static_math_failure_routes_to_content_before_any_visual_review(tmp_path):
    from aiforstudy.blocks.media.execution import execute_static
    c,a,r=case(objects=[vector('a',[2,1]),vector('b',[1,2]),vector('c',[4,3])]); r['spec']=geom_spec()
    row=execute_static(r,content=c,approval=a,out_dir=tmp_path)
    assert row['state']=='failed' and row['failure_owner']=='content'
    assert row['review']['problems'][0]['target_artifact_kind']=='content'
    assert row['resources']==[]


def test_static_executor_renders_real_pixels_and_retains_semantic_evidence(tmp_path):
    from aiforstudy.blocks.media.execution import execute_static
    from aiforstudy.core.digest import digest_file
    c,a,r=case(objects=[vector('a',[2,1]),vector('b',[1,2]),vector('c',[3,3])]); r['spec']=geom_spec()
    class Reviewer:
        def chat_json(self,messages,*,tag,images):
            from PIL import Image
            with Image.open(images[0]) as im: assert im.size==(800,500)
            return {'data':{'accepted':True,'evidence':'test transport received real pixels; not independent semantic acceptance','problems':[]}}
    row=execute_static(r,content=c,approval=a,out_dir=tmp_path,reviewer=Reviewer())
    assert row['state']=='rendered',row
    assert row['static_product']['data']['result']==[3,3]
    assert row['display_evidence'][0]['image_hash']==digest_file(tmp_path/row['screenshot'])
    assert 'review' not in row, 'pixel production must not self-approve semantic criteria'
    assert {x['kind'] for x in row['resources']}=={'svg','screenshot','spec','math_evidence'}
    assert row['criteria_digest']==r['criteria_digest']


def test_plot_exact_rational_breakpoint_is_excluded_and_sampling_count_is_honored():
    mod=compiler('plot')
    denominator={'op':'sub','left':{'op':'var','name':'x'},'right':{'op':'div','left':const(1),'right':const(3)}}
    ctx,*_=context_case(kind='plot',objects=[{'math_object_id':'f','kind':'scalar','expr':{'op':'div','left':const(1),'right':denominator}}],symbols=[{'name':'x','domain':'real'}])
    data=mod.sample_series(ctx,ref('f'),'x',[-1,1],samples=17)
    assert data['excluded_points']==[float(1/3)]
    assert len(data['segments'])==2 and all(len(s)==17 for s in data['segments'])
    assert all(not (s[0][0]<=1/3<=s[-1][0]) for s in data['segments'])


def test_source_image_provenance_requires_registered_original_and_output_bytes(tmp_path):
    from aiforstudy.blocks.media.execution import _source_product
    from aiforstudy.core.digest import digest_file
    from PIL import Image
    path=tmp_path/'source.png'; Image.new('RGB',(80,60),'blue').save(path)
    asset={'asset_id':'IMG','hash':'sha256:registered','file_hash':digest_file(path)}
    r={'source_image_id':'IMG','source_refs':[{'kind':'asset','asset_id':'IMG','hash':asset['hash']}]}
    def copy(req,target):
        return {'asset_id':'IMG','asset_hash':asset['hash'],'source_file_hash':asset['file_hash'],'output_hash':digest_file(target),'origin':'textbook','transform':'copy'}
    product=_source_product(r,path,copy,{'source_images':[asset]})
    assert product['data']['source']['asset_id']=='IMG'
    assert product['data_hash']==digest_of(product['data'])
    with pytest.raises(ContractError): _source_product(r,path,lambda req,p:None,{'source_images':[asset]})
    with pytest.raises(ContractError): _source_product(r,path,copy,{'source_images':[dict(asset,file_hash='changed')]})


def test_complex_unknown_plot_domain_is_blocked_instead_of_visually_overridden():
    from aiforstudy.errors import CapabilityError
    mod=compiler('plot'); denominator={'op':'sin','arg':{'op':'var','name':'x'}}
    ctx,*_=context_case(kind='plot',objects=[{'math_object_id':'f','kind':'scalar','expr':{'op':'div','left':const(1),'right':denominator}}],symbols=[{'name':'x','domain':'real'}])
    with pytest.raises(CapabilityError,match='unknown plot domain'): mod.sample_series(ctx,ref('f'),'x',[-2,2])


def test_generated_svg_is_standalone_without_external_dtd():
    ctx,*_=context_case(objects=[vector('a',[2,1]),vector('b',[1,2]),vector('c',[3,3])])
    result=compiler('geometry').compile_geometry(geom_spec(),ctx)
    assert '<!DOCTYPE' not in result['svg'] and '<!ENTITY' not in result['svg']


def test_default_geometry_is_readable_at_actual_narrow_reader_size(tmp_path):
    from aiforstudy.blocks.media.validation import capture_display
    from aiforstudy.blocks.media.review_policy import DEFAULT_DISPLAY_PROFILES
    ctx,*_=context_case(objects=[vector('a',[2,1]),vector('b',[1,2]),vector('c',[3,3])])
    path=tmp_path/'vector.svg';path.write_text(compiler('geometry').compile_geometry(geom_spec(),ctx)['svg'])
    rows=capture_display(path,profiles=DEFAULT_DISPLAY_PROFILES,out_dir=tmp_path/'display')
    assert all(t['font_px']>=14 and not t['clipped'] for row in rows for t in row['labels'])


def test_reciprocal_fraction_label_is_readable_at_narrow_size(tmp_path):
    from aiforstudy.blocks.media.validation import capture_display
    from aiforstudy.blocks.media.review_policy import DEFAULT_DISPLAY_PROFILES
    ctx,*_=context_case(kind='plot',objects=[{'math_object_id':'f','kind':'scalar','expr':{'op':'div','left':const(1),'right':{'op':'var','name':'x'}}}],symbols=[{'name':'x','domain':'real'}])
    path=tmp_path/'reciprocal.svg';path.write_text(compiler('plot').compile_plot(plot_spec(),ctx)['svg'])
    rows=capture_display(path,profiles=DEFAULT_DISPLAY_PROFILES,out_dir=tmp_path/'display')
    assert all(t['font_px']>=14 and not t['clipped'] for row in rows for t in row['labels'])


def test_registered_textbook_image_and_controlled_library_paths_render(tmp_path):
    from aiforstudy.blocks.media.execution import execute_static,compile_static
    from aiforstudy.core.digest import digest_file
    from aiforstudy.blocks.media.math_context import ApprovedMathContext
    from PIL import Image
    c,a,r=case(); r.update(kind='source_image',source_image_id='IMG',math_model_refs=[],object_refs=[],claim_ids=[])
    r['acceptance_criteria'][0].update(kind='source',evidence_required=['source_provenance','rendered_frame']);r['criteria_digest']=digest_of(r['acceptance_criteria'])
    original=tmp_path/'original.png';Image.new('RGB',(100,80),'blue').save(original)
    asset={'asset_id':'IMG','hash':'sha256:registered','file_hash':digest_file(original)}
    r['source_refs'].append({'kind':'asset','asset_id':'IMG','hash':asset['hash']})
    def source(req,target):
        target.write_bytes(original.read_bytes())
        return {'asset_id':'IMG','asset_hash':asset['hash'],'source_file_hash':asset['file_hash'],'output_hash':digest_file(target),'origin':'textbook','transform':'copy'}
    result=execute_static(r,content=c,approval=a,out_dir=tmp_path/'source-result',source_image=source,source_assets={'source_images':[asset]})
    assert result['state']=='rendered' and result['source_provenance']['source_file_hash']==digest_file(original),result
    c,a,r=case(objects=[vector('a',[2,1]),vector('b',[1,2]),vector('c',[3,3])]);r.update(library_id='vector-sum',spec=geom_spec())
    asset={'asset_id':'library:vector-sum','hash':'sha256:library'};r['source_refs'].append({'kind':'asset','asset_id':asset['asset_id'],'hash':asset['hash']})
    context=ApprovedMathContext.from_approved_content(c,a,r); product=compile_static('geometry',r['spec'],context)
    def library(req,target):
        target.write_text(product['svg'])
        return {'ok':True,'asset_id':asset['asset_id'],'asset_hash':asset['hash'],'object_bindings':product['object_bindings']}
    result=execute_static(r,content=c,approval=a,out_dir=tmp_path/'library-result',library_source=library,source_assets={'library':[asset]})
    assert result['state']=='rendered' and result['static_product']['library_source']['ok'],result


def test_svg_active_content_is_never_rasterized(tmp_path):
    from aiforstudy.blocks.media.execution import _safe_svg
    path=tmp_path/'unsafe.svg'
    for unsafe in ('<script>alert(1)</script>','<image href="https://example.com/x.png"/>','<g onclick="x()"/>'):
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg">'+unsafe+'</svg>')
        with pytest.raises(ContractError): _safe_svg(path)
