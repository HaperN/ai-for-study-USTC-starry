"""V2 teaching steps reject animation-shaped empty or unbound work."""
from copy import deepcopy
import pytest
from aiforstudy.blocks.visual.spec import parse_visual_spec
from aiforstudy.errors import ContractError


def visual_spec():
    return {'schema_version':'2.0','request_id':'anim-square','unit_id':'U1',
      'goal_ids':['G1'],'claim_ids':['K1'],
      'math_model_refs':[{'model_id':'square','digest':'sha256:'+'a'*64}],
      'objects':[{'object_id':'formula','kind':'formula','model_id':'square','math_object_id':'f'}],
      'steps':[{'step_id':'s1','kind':'change','goal_id':'G1','claim_ids':['K1'],
        'math_claim_refs':[{'model_id':'square','claim_id':'same'}],
        'object_ids':['formula'],'observation':'观察平方表达式的指数。','expected_change':'平方公式从隐藏变为可见。',
        'caption':'看公式中右上角的平方指数。','caption_claim_ids':['K1'],
        'start':0,'end':8,'actions':[{'action':'reveal','object_id':'formula'}],
        'checkpoints':[{'at':4,'note':'读出平方指数'}]}]}


def test_one_eight_second_change_is_valid():
    parsed=parse_visual_spec(visual_spec())
    assert parsed.schema_version=='2.0' and parsed.steps[0]['end']==8
    assert 'render_profile' not in parsed.as_dict()


@pytest.mark.parametrize('mutate,match',[
 (lambda x:x.update(steps=[]),'steps'),
 (lambda x:x['steps'][0].update(actions=[]),'change.*actions'),
 (lambda x:x['steps'][0].update(end=1000000),'90'),
 (lambda x:x.update(render_profile='draft'),'render_profile'),
 (lambda x:x['objects'][0].update(space='screen'),'space'),
 (lambda x:x['objects'][0].update(text='x^2'),'text'),
 (lambda x:x['steps'][0].update(caption_claim_ids=['wrong']),'caption_claim_ids'),
 (lambda x:x['steps'][0].update(goal_id='other'),'goal_id'),
 (lambda x:x['steps'][0].update(object_ids=['missing']),'object_ids'),
])
def test_rejects_invalid_teaching_spec(mutate,match):
    value=visual_spec();mutate(value)
    with pytest.raises(ContractError,match=match): parse_visual_spec(value)


def test_observe_requires_current_visible_object_and_reading_purpose():
    value=visual_spec();step=deepcopy(value['steps'][0]);step.update(step_id='s2',kind='observe',start=8,end=10,actions=[],checkpoints=[],expected_change='保持公式可见以阅读指数。')
    value['steps'].append(step)
    assert parse_visual_spec(value).steps[1]['kind']=='observe'
    value['steps'][1]['observation']=''
    with pytest.raises(ContractError,match='observation'):parse_visual_spec(value)


def test_observe_cannot_be_first_static_video_or_read_hidden_object():
    value=visual_spec();value['steps'][0].update(kind='observe',actions=[])
    with pytest.raises(ContractError,match='observe.*visible|observe.*可见'):parse_visual_spec(value)


def test_same_object_reveal_is_not_a_new_change():
    value=visual_spec();second=deepcopy(value['steps'][0]);second.update(step_id='s2',start=8,end=10,checkpoints=[]);value['steps'].append(second)
    with pytest.raises(ContractError,match='reveal.*visible|reveal.*可见'):parse_visual_spec(value)


def test_claimed_object_must_actually_be_visible_in_its_step():
    value=visual_spec();value['objects'].append({'object_id':'hidden','kind':'formula','model_id':'square','math_object_id':'other'})
    value['steps'][0]['object_ids'].append('hidden')
    with pytest.raises(ContractError,match='可见|visible'):parse_visual_spec(value)


def test_duplicate_action_cannot_reuse_an_earlier_evidence_pair():
    value=visual_spec();value['steps'][0]['actions'] += [{'action':'highlight','object_id':'formula'},{'action':'highlight','object_id':'formula'}]
    with pytest.raises(ContractError,match='重复|duplicate'):parse_visual_spec(value)
