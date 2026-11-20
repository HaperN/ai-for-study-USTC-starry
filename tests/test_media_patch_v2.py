from copy import deepcopy
import importlib
import pytest
from aiforstudy.errors import ContractError
from aiforstudy.core.digest import digest_of
from test_semantic_static_media import context_case,geom_spec,vector
from aiforstudy.pipeline.diagnostics import technical_ticket


def inputs():
    ctx,c,a,r=context_case(objects=[vector('a',[2,1]),vector('b',[1,2]),vector('c',[3,3])])
    spec=geom_spec(); spec['layout']={'font_size':16,'label_offsets':{'a':[10,12]}}
    ticket=technical_ticket(unit_id='U',artifact_id='media:'+r['request_id'],artifact_hash=digest_of(spec),problem='label overlaps vector',source=c['body_markdown'],owner='visual_renderer',kind='visual_layout',allowed_paths=('/spec/layout/label_offsets/a',),rule='LABEL_OVERLAP',action='move label a by 10 pixels',expected='label a remains bound and readable').as_dict()
    patch={'schema_version':'MediaPatchV2','request_id':r['request_id'],'base_spec_hash':digest_of(spec),'ticket_id':ticket['ticket_id'],'operations':[{'op':'replace','path':'/layout/label_offsets/a','value':[20,12]}]}
    return ctx,r,spec,ticket,patch


def patcher():
    assert importlib.util.find_spec('aiforstudy.blocks.media.media_patches') is not None,'missing authorized media patches'
    return importlib.import_module('aiforstudy.blocks.media.media_patches').apply_media_patch


def test_layout_patch_changes_only_authorized_request_and_keeps_mathematical_data():
    apply=patcher(); ctx,r,spec,ticket,patch=inputs(); original=deepcopy(spec)
    result=apply(r,spec,patch,ticket,math_context=ctx)
    from aiforstudy.blocks.media.geometry import compile_geometry
    assert result['layout']['label_offsets']['a']==[20,12]
    assert spec==original
    assert compile_geometry(result,ctx)['data']==compile_geometry(spec,ctx)['data']
    assert result['construction']==spec['construction']


@pytest.mark.parametrize('damage',['request','scope','math','stale','ticket','none','unready'])
def test_patch_rejects_unauthorized_identity_math_and_visibility(damage):
    apply=patcher();ctx,r,spec,ticket,patch=inputs(); old=deepcopy(spec)
    if damage=='request': patch['request_id']='OTHER'
    if damage=='scope': patch['operations'][0]['path']='/layout/font_size'
    if damage=='math': patch['operations'][0]['path']='/construction/result'
    if damage=='stale': patch['base_spec_hash']='old'
    if damage=='ticket': patch['ticket_id']='OTHER'
    if damage=='none': patch['operations'][0]['value']=None
    if damage=='unready': ticket['status']='needs_evidence'
    with pytest.raises(ContractError): apply(r,spec,patch,ticket,math_context=ctx)
    assert spec==old


def test_disjoint_layout_batch_uses_one_original_hash_and_rejects_overlap():
    from aiforstudy.blocks.media.media_patches import apply_media_patches
    ctx,r,spec,ticket,patch=inputs()
    second=technical_ticket(unit_id='U',artifact_id='media:'+r['request_id'],artifact_hash=digest_of(spec),
        problem='small label',source='approved source',owner='visual_planner',kind='visual_layout',
        allowed_paths=('/spec/layout/font_size',),rule='LABEL_SIZE',action='increase size',expected='readable at390').as_dict()
    patch2={**patch,'ticket_id':second['ticket_id'],'operations':[{'op':'replace','path':'/layout/font_size','value':26}]}
    result=apply_media_patches(r,spec,[patch,patch2],[ticket,second],math_context=ctx)
    assert result['layout']=={'font_size':26,'label_offsets':{'a':[20,12]}}
    patch2['operations']=deepcopy(patch['operations'])
    with pytest.raises(ContractError,match='overlapping'):
        apply_media_patches(r,spec,[patch,patch2],[ticket,second],math_context=ctx)
