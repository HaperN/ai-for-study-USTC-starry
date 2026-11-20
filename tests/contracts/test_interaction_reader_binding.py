"""Declared interactions must have one actual model and one authorized reader mount."""
from copy import deepcopy
import importlib
from pathlib import Path

import pytest

from aiforstudy.blocks.content.content_contracts import parse_content
from aiforstudy.blocks.media.interaction_adapter import verify_template_response
from aiforstudy.blocks.interaction.spec import validate_assignment_coverage
from aiforstudy.core.digest import digest_of
from aiforstudy.errors import ContractError
from test_content_v4_reader_contract import candidate, catalog


def interaction_case(unit_id='U'):
    content = candidate(); content['unit_id'] = unit_id
    content['body_markdown'] = '阅读解释，观察关系。[[EXPLAIN:F]]'
    content['explainables'][0].update(name='等值线正交', markdown='共轭调和函数的等值线：$f(z)=z^2$，$u=x^2-y^2$，$v=2xy$。拖动交点看切线。')
    content['teaching_plan']['goals'][0]['observable_task'] = '移动交点观察共轭调和等值线的正交关系'
    content['teaching_evidence'] = [{'goal_id':'G','purpose':'derivation','reader_pointer':'/explainables/0/markdown','claim_refs':['K']}]
    content = parse_content(content).as_dict()
    decision = {'target_id':'G','goal_id':'G','requirement':'required','handled_by':'interaction','request_ids':[],
        'reason':'移动交点观察变化','benefit':'观察正交关系','cost':'需要操作','text_alternative':'正文描述局部关系'}
    requirements = {'content_hash':digest_of(content), 'decisions':[decision], 'requests':[]}
    model = {'id':'M-01','template':'harmonic-contours','kind':'svg','title':'移动交点', 'binds_to':'G',
        'why_here':'观察目标中交点位置与两条切线之间的关系。', 'anchor_hint':'拖动交点看切线。',
        'params':{'initial_point':[1,1],'extent':3}, 'caption':'观察交点变化', 'fallback_text':'在非原点处两条切线正交。'}
    placement = {'target_pointer':'/explainables/0/markdown','owner_id':'F','step_id':'',
        'anchor_text':'拖动交点看切线。','position':'after'}
    result = {'schema_version':'InteractionResultV2','content_hash':digest_of(content),
        'requirements_hash':digest_of(requirements),'models':[model], 'results':[
            {'target_id':'G','state':'verified','model_id':'M-01','checks':verify_template_response(model),
             'spec':deepcopy(model),'placement':placement}]}
    return content, requirements, result


def bind(content, requirements, result):
    assert importlib.util.find_spec('aiforstudy.blocks.interaction.placements'), 'strict interaction placement module required'
    return importlib.import_module('aiforstudy.blocks.interaction.placements').bind_interaction_placements(content, requirements, result)


@pytest.mark.parametrize('damage', ['missing_model', 'changed_model', 'missing_id', 'extra_model', 'dead_state'])
def test_verified_assignment_cannot_claim_missing_or_different_models(damage):
    content, requirements, result = interaction_case()
    if damage == 'missing_model': result['models'] = []
    if damage == 'changed_model': result['models'][0]['params']['initial_point'] = [2,1]
    if damage == 'missing_id': result['results'][0].pop('model_id')
    if damage == 'extra_model': result['models'].append({**result['models'][0], 'id':'unused'})
    if damage == 'dead_state':
        result['models'][0].update(template='vector-combination', params={'vectors':[[0,0]], 'coefficients':[1]})
        result['results'][0]['spec'] = deepcopy(result['models'][0])
    assert not validate_assignment_coverage(requirements, result, digest_of(content))['ok']


def test_registered_model_mount_uses_goal_evidence_inside_folded_reader_field():
    content, requirements, result = interaction_case()
    mounts = bind(content, requirements, result)
    assert len(mounts) == 1
    assert mounts[0]['source_id'] == 'U.M-01'
    assert mounts[0]['target_pointer'] == '/explainables/0/markdown'
    assert mounts[0]['owner_id'] == 'F'
    assert mounts[0]['source_hash'] == digest_of(result['models'][0])


@pytest.mark.parametrize('damage', ['wrong_goal_field', 'wrong_owner', 'missing_anchor', 'ambiguous_anchor', 'stale_field'])
def test_interaction_placement_rejects_wrong_evidence_or_ambiguous_anchor(damage):
    content, requirements, result = interaction_case()
    p = result['results'][0]['placement']
    if damage == 'wrong_goal_field': p.update(target_pointer='/body_markdown',owner_id='U',anchor_text='阅读解释')
    if damage == 'wrong_owner': p['owner_id'] = 'different'
    if damage == 'missing_anchor': p['anchor_text'] = '不存在'
    if damage == 'ambiguous_anchor':
        content['explainables'][0]['markdown'] += '拖动交点看切线。'
        requirements['content_hash'] = result['content_hash'] = digest_of(content)
        result['requirements_hash'] = digest_of(requirements)
    if damage == 'stale_field': p.update(field_hash='stale', span=[0,1])
    with pytest.raises(ContractError): bind(content, requirements, result)


def test_target_templates_are_local_and_missing_transport_disables_capability():
    from aiforstudy.blocks.interaction import spec
    select = getattr(spec, 'interaction_capabilities', None)
    assert callable(select)
    content, _, _ = interaction_case()
    content['teaching_plan']['goals'].append({'id':'G2','observable_task':'改变电阻观察电流','claim_refs':['K'],'level':'core'})
    content['teaching_evidence'].append({'goal_id':'G2','purpose':'derivation','reader_pointer':'/summary','claim_refs':['K']})
    content['summary'] = '电阻增大时电流减小。'
    caps = select(content, transport_available=True)
    assert caps['targets']['G']['templates'] == ['harmonic-contours']
    assert caps['targets']['G2']['templates'] == []
    assert not select(content, transport_available=False)['available']


def test_assigned_generator_emits_model_id_bound_placement_and_v2_identity(tmp_path):
    from aiforstudy.pipeline.stage_io import _legacy
    content, requirements, result = interaction_case()
    class Client:
        def chat_json(self, messages, **kwargs):
            return {'data':{'models':[{**result['models'][0], 'target_id':'G', 'placement':result['results'][0]['placement']}]}}
    actual = _legacy('model_generate').generate_assigned_models(content,
        assignments=requirements['decisions'], client=Client(), unit_id='U', parse_dir=tmp_path,
        requirements_hash=digest_of(requirements))
    assert actual.get('schema_version') == 'InteractionResultV2'
    assert actual['results'][0]['state'] == 'verified', actual
    assert actual['results'][0]['model_id'] == 'M-01'
    assert actual['results'][0]['placement']['field_hash'] == digest_of(content['explainables'][0]['markdown'])
    assert bind(content, requirements, actual)[0]['source_id'] == 'U.M-01'


def historical_reader_fixture(unit_id='U'):
    """Explicit legacy renderer input, not a current approval/composer fixture."""
    from aiforstudy.blocks.content.reader_document import project_reader
    from aiforstudy.blocks.render.layout import layout_from_reader_media
    content, requirements, interaction = interaction_case(unit_id)
    from aiforstudy.pipeline.stage_io import _legacy
    supplied=deepcopy(interaction)
    class OfflineAssignmentClient:
        def chat_json(self,messages,**kwargs):
            return {'data':{'models':[{**supplied['models'][0],'target_id':'G',
                                      'placement':supplied['results'][0]['placement']}]}}
    interaction=_legacy('model_generate').generate_assigned_models(content,
        assignments=requirements['decisions'],client=OfflineAssignmentClient(),unit_id=unit_id,
        parse_dir=Path(__file__).parent,requirements_hash=digest_of(requirements))
    assert interaction['results'][0]['state']=='verified',interaction
    reader=project_reader(content,catalog()).as_dict()
    mounts=bind(content,requirements,interaction)
    view={**deepcopy(content),'reader_document':reader,'reader_media':[],
        'models':interaction['models'],'interaction_placements':mounts,
        'reader_layout':layout_from_reader_media(reader,[],interaction_mounts=mounts)}
    binding={'schema_version':'CompositionBindingV2','unit_id':unit_id,
        'content_hash':digest_of(content),'view_hash':digest_of(view),
        'reader_document_hash':digest_of(reader),'reader_layout_hash':digest_of(view['reader_layout']),
        'reader_media_hash':digest_of([]),'interaction_placements_hash':digest_of(mounts),
        **{field:'historical-renderer-input-only' for field in (
            'requirements_hash','manifest_hash','content_approval_hash','media_approval_hash','placements_hash')}}
    return content,{'view':view,'binding':binding}


def test_renderer_keeps_v4_body_and_requires_one_mount_per_model(tmp_path):
    import json
    from aiforstudy.pipeline.stage_io import _legacy
    from aiforstudy.core.jsonio import write_json_atomic
    content,bundle=historical_reader_fixture()
    write_json_atomic(tmp_path/'parse/chapters.json',{'book':{'title':'演示'},'chapters':[{'id':'U','title':'演示'}],
        'lesson_units':[{'unit_id':'U','title':'演示','original_text':'U.md'}]})
    write_json_atomic(tmp_path/'parse/concepts.json',{'concepts':[]})
    write_json_atomic(tmp_path/'lessons/lessons/U.json',bundle['view'])
    from support.reader_site import write_reader_fixture
    write_reader_fixture([content],catalog(),tmp_path/'out',interactions={'U':bundle['view']})
    data=json.loads((tmp_path/'out/data/data.js').read_text().split('=',1)[1].strip().rstrip(';'))
    assert data['units']['U']['body_markdown']==content['body_markdown']
    assert set(data['models'])=={'U.M-01'}


@pytest.mark.parametrize('damage', ['no_mount', 'no_model', 'duplicate_mount', 'wrong_unit'])
def test_composition_rejects_models_and_mounts_that_do_not_match(damage):
    from aiforstudy.blocks.interaction.placements import validate_model_mounts
    content,requirements,result=interaction_case()
    mounts=bind(content,requirements,result); models=result['models']
    if damage=='no_mount': mounts=[]
    if damage=='no_model': models=[]
    if damage=='duplicate_mount': mounts*=2
    if damage=='wrong_unit': mounts[0]['unit_id']='another-unit'
    with pytest.raises(ContractError): validate_model_mounts(models,mounts,'U')


def test_author_receives_capability_catalog_without_changing_frozen_source_identity():
    import json
    from aiforstudy.blocks.content.generation import generate_content
    from aiforstudy.blocks.content.content_policy import load_content_policy
    from aiforstudy.blocks.content.source_context import SourceContext
    from aiforstudy.blocks.interaction.spec import source_interaction_capabilities
    from support.content_case import teaching_case,authoring
    content,source=teaching_case(); original=deepcopy(source)
    capability=source_interaction_capabilities(source,transport_available=False)
    captured=[]
    class Client:
        def chat_json(self,messages,**kwargs):
            captured.append(json.loads(messages[1]['content']))
            return {'data':authoring(content)}
    generated=generate_content(source,load_content_policy(),Client(),interaction_capabilities=capability)
    assert captured[0]['frozen_context']['interaction_capabilities']==capability
    assert not capability['available']
    assert generated['content']['source_context_hash']==SourceContext.model_validate(source).digest
    assert source==original
