"""Generated code retains frozen author intent without executing it on the host."""
from copy import deepcopy
import ast
import json

import pytest

from aiforstudy.errors import CapabilityError, ContractError
from aiforstudy.blocks.media.code_contracts import (
    GeneratedMediaCodeV1, MAX_CODE_CHARS, validate_media_code,
)
from aiforstudy.blocks.media.code_generation import generate_media_code, media_code_prompt


STATIC_CODE = """from manim import *
class GeneratedScene(Scene):
    def construct(self):
        self.add(Text("零", font="Noto Sans CJK SC"))
"""
ANIMATION_CODE = '''from manim import *
from aifs_media_runtime import media_step, load_context
class TranslationBase(Scene):
    def construct(self):
        context = load_context()
        arrow = Arrow(ORIGIN, RIGHT + UP)
        for step_id, shift in [("S1", RIGHT), ("S2", UP)]:
            with media_step(self, step_id):
                self.play(arrow.animate.shift(shift), run_time=1.2)
                self.wait(1)
class GeneratedScene(TranslationBase):
    pass
'''


def request(kind='drawing'):
    result = {'request_id':'MR-code', 'kind':kind,
              'object_refs':[{'model_id':'M','object_id':'X'}]}
    if kind == 'animation':
        result['animation'] = {'initial_state':'显示向量', 'steps':['平移向量','再向上平移'],
                               'key_pauses':['每步保留比较'], 'final_state':'显示平移结果'}
    return result


def program(kind='drawing'):
    return {'schema_version':'GeneratedMediaCodeV1', 'request_id':'MR-code',
            'backend':'manim',
            'code':STATIC_CODE if kind == 'drawing' else ANIMATION_CODE,
            'object_bindings':[{'display_id':'origin','model_id':'M','object_id':'X'}],
            'steps':[] if kind == 'drawing' else [
                {'step_id':f'S{i+1}','author_step_index':i,'caption':'保持向量方向与长度',
                 'observation':'观察起点改变','expected_change':'箭头平移但方向与长度不变'}
                for i in range(2)]}


def approved_case(kind='drawing'):
    from test_media_decisions_v2 import math_request_case
    from support.content_case import author_media
    from aiforstudy.blocks.media.author_requirements import project_author_requests
    content, approval, raw, _ = math_request_case('animation' if kind == 'animation' else 'geometry')
    record = author_media(content, raw)
    _, requests = project_author_requests(record['requirements'], content)
    req = requests[0]
    req['kind'] = kind
    if kind == 'animation':
        req['animation'] = request(kind)['animation']
    candidate = program(kind)
    candidate['request_id'] = req['request_id']
    return content, approval, req, candidate


class Client:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def chat_json(self, messages, **kwargs):
        self.calls.append((deepcopy(messages), kwargs))
        return {'data':deepcopy(self.result)}


def test_full_python_helpers_loops_and_custom_scene_inheritance_remain_open():
    for kind in ('drawing','animation'):
        source = program(kind)
        checked = validate_media_code(source, request(kind))
        assert checked == source
        checked['object_bindings'].clear()
        assert source['object_bindings']
        schema = GeneratedMediaCodeV1.model_json_schema()
        assert schema['additionalProperties'] is False


@pytest.mark.parametrize('damage', ['schema','request','backend','status','timestamps','blank','long','syntax','entry'])
def test_generated_program_rejects_wrong_identity_self_approval_or_bad_source(damage):
    row = program()
    if damage == 'schema': row['schema_version'] = 'old'
    if damage == 'request': row['request_id'] = 'other'
    if damage == 'backend': row['backend'] = 'unsupported'
    if damage == 'status': row['status'] = 'pass'
    if damage == 'timestamps': row['duration'] = 5
    if damage == 'blank': row['code'] = '  '
    if damage == 'long': row['code'] = STATIC_CODE + '#' * MAX_CODE_CHARS
    if damage == 'syntax': row['code'] = 'def build_figure(:'
    if damage == 'entry': row['code'] = 'def render(context):\n    return None\n'
    with pytest.raises(ContractError): validate_media_code(row, request())


def test_syntax_check_never_imports_or_executes_generated_code(tmp_path):
    marker = tmp_path/'host-executed'
    row = program()
    row['code'] = f'open({str(marker)!r}, "w").write("bad")\n' + STATIC_CODE
    validate_media_code(row, request())
    assert not marker.exists()


@pytest.mark.parametrize('damage', ['missing','outside','duplicate_display','extra_binding_field'])
def test_bindings_cover_exact_requested_objects_without_forged_bindings(damage):
    row = program()
    if damage == 'missing': row['object_bindings'] = []
    if damage == 'outside': row['object_bindings'][0]['object_id'] = 'invented'
    if damage == 'duplicate_display': row['object_bindings'] *= 2
    if damage == 'extra_binding_field': row['object_bindings'][0]['proof'] = 'passed'
    with pytest.raises(ContractError): validate_media_code(row, request())


@pytest.mark.parametrize('damage', ['missing','order','outside','bool','duplicate','timestamp','blank_caption'])
def test_storyboard_covers_author_steps_in_order_without_model_supplied_timing(damage):
    row = program('animation')
    if damage == 'missing': row['steps'].pop()
    if damage == 'order': row['steps'].reverse()
    if damage == 'outside': row['steps'][1]['author_step_index'] = 2
    if damage == 'bool': row['steps'][0]['author_step_index'] = False
    if damage == 'duplicate': row['steps'][1]['step_id'] = 'S1'
    if damage == 'timestamp': row['steps'][0]['start'] = 0
    if damage == 'blank_caption': row['steps'][0]['caption'] = ' '
    with pytest.raises(ContractError): validate_media_code(row, request('animation'))


def test_author_step_can_be_split_and_same_object_can_have_two_displays():
    row = program('animation')
    row['steps'].insert(1, {**row['steps'][0], 'step_id':'S1-detail'})
    row['object_bindings'].append({**row['object_bindings'][0], 'display_id':'translated-origin'})
    assert validate_media_code(row, request('animation')) == row
    row = program()
    row['steps'] = program('animation')['steps']
    with pytest.raises(ContractError): validate_media_code(row, request())


def test_binding_repair_error_names_missing_extra_and_allowed_frozen_objects():
    req = request('animation')
    req['object_refs'] = [{'model_id':'M','object_id':name}
                          for name in ('xsum','xresult','ysum','yresult')]
    row = program('animation')
    row['object_bindings'] = [{'display_id':name,'model_id':'M','object_id':name}
                              for name in ('xsum','xresult','ysum','a','b','c','sum')]
    original = deepcopy(row)
    with pytest.raises(ContractError) as caught:
        validate_media_code(row, req)
    message = str(caught.value)
    assert 'missing=' in message and 'extra=' in message and 'allowed=' in message
    assert '"object_id": "yresult"' in message.split('extra=')[0]
    extra = message.split('extra=')[1].split('allowed=')[0]
    for name in ('a','b','c','sum'):
        assert f'"object_id": "{name}"' in extra
    assert row == original


def test_two_author_steps_three_generated_indices_gives_actionable_repair():
    content, approval, req, row = approved_case('animation')
    row['steps'].append({**row['steps'][-1], 'step_id':'S3', 'author_step_index':2})
    original = deepcopy(row)
    with pytest.raises(ContractError) as caught:
        generate_media_code(req, content=content, approval=approval, client=Client(row))
    message = str(caught.value)
    assert 'steps[2].author_step_index' in message
    assert 'received=2' in message and 'allowed=[0, 1]' in message
    assert 'received_indices=[0, 1, 2]' in message
    assert '额外收尾' in message and '不能增加作者步骤' in message
    assert caught.value.candidate == original and row == original
    repaired = media_code_prompt(req, content=content, approval=approval,
                                 previous=row, feedback={'error':message})
    payload = json.loads(repaired[-1]['content'])
    assert payload['repair']['previous_program'] == original
    assert payload['repair']['feedback']['error'] == message
    assert payload['request']['animation']['steps'] == req['animation']['steps']


def test_animation_schema_and_payload_enumerate_only_current_request_authority():
    content, approval, req, _ = approved_case('animation')
    payload = json.loads(media_code_prompt(req, content=content, approval=approval)[-1]['content'])
    step_schema = payload['output_json_schema']['$defs']['MediaCodeStepV1']
    assert step_schema['properties']['author_step_index']['enum'] == [0, 1]
    assert payload['author_step_mapping'] == [
        {'author_step_index':i, 'author_step':step} for i, step in enumerate(req['animation']['steps'])]
    assert payload['allowed_object_bindings'] == req['object_refs']
    assert payload['allowed_author_step_indices'] == [0, 1]
    # A later prompt must get a fresh schema, not a shared enum mutated by a previous request.
    req['animation']['steps'].append('保留作者额外观察')
    second = json.loads(media_code_prompt(req, content=content, approval=approval)[-1]['content'])
    assert second['output_json_schema']['$defs']['MediaCodeStepV1']['properties']['author_step_index']['enum'] == [0, 1, 2]
    assert step_schema['properties']['author_step_index']['enum'] == [0, 1]
    assert 'enum' not in GeneratedMediaCodeV1.model_json_schema()['$defs']['MediaCodeStepV1']['properties']['author_step_index']


def test_generation_uses_existing_gateway_and_includes_focused_frozen_inputs():
    content, approval, req, row = approved_case()
    original = deepcopy((content, approval, req))
    client = Client(row)
    assert generate_media_code(req, content=content, approval=approval, client=client) == row
    assert (content, approval, req) == original
    assert len(client.calls) == 1
    messages, kwargs = client.calls[0]
    payload = json.loads(messages[-1]['content'])
    assert payload['request'] == req
    # Rule 2026-10-16: the model reads related excerpts + section outline, not the
    # whole lesson or the book-wide catalog; the host keeps both for validation.
    from aiforstudy.blocks.media.media_context import focused_media_context
    focused = focused_media_context(content, approval, req)
    assert payload['approved_content'] == {k: focused[k] for k in
                                           ('section_outline', 'related_excerpts', 'referenced_entries')}
    assert payload['approval'] == focused['approval_binding']
    assert 'reference_catalog' not in payload['approval']
    assert payload['approved_models'] == content['math_models']
    assert payload['acceptance_criteria'] == req['acceptance_criteria']
    assert payload['placement'] == req['placement']
    assert payload['output_json_schema']['additionalProperties'] is False
    assert kwargs['tag'].startswith('media-code:')


@pytest.mark.parametrize('mode', ['full', 'balanced', 'json_only'])
@pytest.mark.parametrize('kind', ['drawing', 'animation'])
def test_generation_uses_one_desktop_target_in_both_display_contracts(mode, kind):
    from aiforstudy.core.review_mode import review_mode
    content, approval, req, _ = approved_case(kind)
    with review_mode(mode):
        messages = media_code_prompt(req, content=content, approval=approval)
    payload = json.loads(messages[-1]['content'])
    for profiles in (payload['display_profiles'], payload['quality_contract']['display_profiles']):
        assert [(p['profile_id'], p['width'], p['content_width']) for p in profiles] == [
            ('wide', 1280, 450)]
    assert 'mobject.height * 450 / config.frame_width' in messages[0]['content']
    assert payload['request'] == req


def test_prompt_explains_real_teaching_pixels_runtime_steps_and_open_code():
    for kind in ('drawing','animation'):
        content, approval, req, _ = approved_case(kind)
        messages = media_code_prompt(req, content=content, approval=approval)
        joined = '\n'.join(m['content'] for m in messages)
        for term in ('数学','中文','颜色','450','14','验收','不','approved_models'):
            assert term in joined
        payload = json.loads(messages[-1]['content'])
        ast.parse(payload['example']['program']['code'])
        if kind == 'animation':
            for term in ('media_step','load_context','author_step_index','停顿','更新器','GeneratedScene'):
                assert term in joined
        else:
            assert 'GeneratedScene' in joined and 'steps=[]' in joined


def test_static_prompt_uses_subordinate_notes_and_real_still_output():
    content, approval, req, _ = approved_case()
    messages = media_code_prompt(req, content=content, approval=approval)
    system = messages[0]['content']
    assert '真实 PNG' in system and 'steps=[]' in system
    assert 'mobject.height * 450 / config.frame_width' in system
    assert '同一最小字号下限' not in system
    for term in ('补充说明', '小于公式', '详细推导留在正文', 'Axes.plot', '不跨渐近线连线'):
        assert term in system
    example = json.loads(messages[-1]['content'])['example']
    assert example['program']['backend'] == 'manim'
    assert example['context']['request']['kind'] == 'drawing'
    assert example['program']['steps'] == []
    ast.parse(example['program']['code'])


def test_animation_prompt_keeps_existing_step_protocol_and_frozen_authority():
    content, approval, req, _ = approved_case('animation')
    messages = media_code_prompt(req,content=content,approval=approval)
    system = messages[0]['content']
    payload = json.loads(messages[-1]['content'])
    assert payload['allowed_author_step_indices'] == [0, 1]
    step = payload['output_json_schema']['$defs']['MediaCodeStepV1']
    assert set(step['properties']) == {
        'step_id', 'author_step_index', 'caption', 'observation', 'expected_change',
        'narration_text', 'display_markdown', 'director_notes'}
    assert step['properties']['author_step_index']['enum'] == [0, 1]
    assert 'mobject.height / config.frame_height * display_video_css_height' in system
    assert 'mobject.height * 450 / config.frame_width' in system
    assert '最终 scale/arrange' in system and '缩小整个 group' in system
    assert '不把每个字形包围盒高度等同于14 CSS px字号门槛' in system
    assert payload['request']['animation'] == req['animation']


def test_repair_keeps_original_code_errors_and_original_acceptance_standards():
    content, approval, req, row = approved_case('animation')
    previous = deepcopy(row)
    previous['code'] = 'broken original code for repair'
    feedback = {'runtime_error':'NameError: vector', 'review_findings':[{'problem':'窄屏标签被裁切'}]}
    client = Client(row)
    generate_media_code(req, content=content, approval=approval, client=client,
                        previous=previous, feedback=feedback)
    payload = json.loads(client.calls[0][0][-1]['content'])
    assert payload['repair']['previous_program'] == previous
    assert payload['repair']['original_code'] == previous['code']
    assert payload['repair']['feedback'] == feedback
    assert payload['acceptance_criteria'] == req['acceptance_criteria']
    assert payload['request']['animation'] == req['animation']


@pytest.mark.parametrize('damage', ['approval','criteria','placement','model','previous_shape','unsupported_kind'])
def test_invalid_authority_stops_before_calling_model(damage):
    content, approval, req, row = approved_case()
    previous = None
    if damage == 'approval': approval = {}
    if damage == 'criteria': req['acceptance_criteria'][0]['statement'] = 'lower standard'
    if damage == 'placement': req['placement']['field_hash'] = 'stale'
    if damage == 'model': req['math_model_refs'][0]['digest'] = 'stale'
    if damage == 'previous_shape': previous = ['not an original program object']
    if damage == 'unsupported_kind': req['kind'] = 'plot'
    client = Client(row)
    with pytest.raises((ContractError, CapabilityError)):
        generate_media_code(req, content=content, approval=approval, client=client, previous=previous)
    assert not client.calls


@pytest.mark.parametrize('step_id', ['../S1','1-step','第一步','S1.name','S1\n','S' * 65])
def test_step_identifiers_match_worker_safe_filename_contract(step_id):
    row = program('animation')
    row['steps'][0]['step_id'] = step_id
    with pytest.raises(ContractError): validate_media_code(row, request('animation'))


def test_invalid_previous_identity_is_repair_evidence_and_never_overrides_frozen_request():
    content, approval, req, row = approved_case()
    previous = {**row,'request_id':'wrong-model-id','backend':'unsupported-backend'}
    client = Client(row)
    repaired = generate_media_code(req,content=content,approval=approval,client=client,
                                   previous=previous,feedback={'problem':'模型返回了错误身份与后端'})
    payload = json.loads(client.calls[0][0][-1]['content'])
    assert repaired == row
    assert payload['request'] == req
    assert payload['backend'] == 'manim'
    assert payload['repair']['previous_program'] == previous


def test_animation_example_explicit_text_contrast_matches_white_worker_background():
    content, approval, req, _ = approved_case('animation')
    messages = media_code_prompt(req,content=content,approval=approval)
    assert '白色背景' in messages[0]['content']
    source = json.loads(messages[-1]['content'])['example']['program']['code']
    for node in ast.walk(ast.parse(source)):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id == 'Text':
            assert any(arg.arg == 'color' and isinstance(arg.value,ast.Constant)
                       and arg.value.value == '#172033' for arg in node.keywords)


def test_animation_teaching_example_has_its_own_executable_context_without_changing_request():
    content, approval, req, _ = approved_case('animation')
    original = deepcopy((content, approval, req))
    payload = json.loads(media_code_prompt(req, content=content, approval=approval)[-1]['content'])
    example = payload['example']
    assert 'context' in example
    own_request = example['context']['request']
    assert own_request['request_id'] == example['program']['request_id'] != req['request_id']
    assert len(own_request['animation']['steps']) == 3
    assert [step['author_step_index'] for step in example['program']['steps']] == [0, 1, 2]
    validate_media_code(example['program'], own_request)
    assert example['context']['approved_content']['examples'][0]['id'] == 'EXAMPLE-CROSS-TERMS'
    assert payload['request'] == req
    assert payload['allowed_author_step_indices'] == [0, 1]
    assert (content, approval, req) == original


def test_unsupported_model_output_is_preserved_as_failure_without_fallback():
    content, approval, req, _ = approved_case()
    bad = {'status':'unsupported', 'reason':'cannot render'}
    client = Client(bad)
    with pytest.raises(ContractError) as caught:
        generate_media_code(req, content=content, approval=approval, client=client)
    assert caught.value.candidate == bad
    assert len(client.calls) == 1
    with pytest.raises(CapabilityError):
        generate_media_code(req, content=content, approval=approval, client=None)


def test_repair_attaches_real_frames_to_existing_multimodal_client():
    content, approval, req, row = approved_case()
    client = Client(row)
    feedback = {'images':['/tmp/actual-narrow.png','/tmp/actual-wide.png'],
                'problems':[{'problem':'窄屏字号过小','expected':'至少14 CSS px'}]}
    generate_media_code(req, content=content, approval=approval, client=client,
                        previous=row, feedback=feedback)
    messages, kwargs = client.calls[0]
    assert kwargs['images'] == feedback['images']
    repair = json.loads(messages[-1]['content'])['repair']
    assert repair['feedback'] == {'problems':feedback['problems']}
    assert repair['attached_image_count'] == 2
    assert '/tmp/actual-narrow.png' not in messages[-1]['content']


@pytest.mark.parametrize('mode', ['full', 'balanced'])
def test_repair_keeps_image_labels_aligned_with_actual_attachments(mode):
    from aiforstudy.core.review_mode import review_mode
    content, approval, req, row = approved_case('animation')
    feedback = {
        'images':['/tmp/step-one.png', '/tmp/step-two.png'],
        'image_labels':['步骤 S1，稳定状态，t=2.9s', '步骤 S2，稳定状态，t=5.9s'],
        'problem':'第二步公式缺共轭横线',
    }
    original = deepcopy(feedback)
    client = Client(row)
    with review_mode(mode):
        generate_media_code(req, content=content, approval=approval, client=client,
                            previous=row, feedback=feedback)
    messages, kwargs = client.calls[0]
    assert kwargs['images'] == feedback['images']
    assert messages[-1].get('image_labels') == feedback['image_labels']
    payload = json.loads(messages[-1]['content'])
    assert payload['repair']['feedback'] == {'problem':'第二步公式缺共轭横线'}
    assert payload['repair']['original_code'] == row['code']
    assert feedback == original


def test_optional_content_validation_checks_approved_model_digest():
    content, _, req, row = approved_case()
    assert validate_media_code(row, req, content) == row
    req['math_model_refs'][0]['digest'] = 'changed'
    with pytest.raises(ContractError): validate_media_code(row, req, content)
