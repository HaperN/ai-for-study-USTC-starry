"""Bounded correction preserves source/semantics and survives process restarts."""
from copy import deepcopy
import json

import pytest

from aiforstudy.errors import ContractError
from aiforstudy.blocks.content.content_policy import load_content_policy
from aiforstudy.blocks.content.generation import generate_content
from aiforstudy.blocks.content.source_inventory import (build_inventory_messages,
    extract_source_inventory, validate_source_inventory)
from support.content_case import teaching_case, authoring
from test_source_inventory import context, response


class SequenceClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def chat_json(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        assert self.responses, 'unexpected extra provider call'
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return {'data': deepcopy(value)}


def inventory_case():
    source = context()
    good = response(source)
    # Add a second genuine source obligation so one feedback must list both IDs.
    good['obligations'].append(dict(id='NEW-convention', kind='convention', level='core',
        concept_id='', conditions=[], conclusion='n 包括零。',
        source_excerpt='记号 n 表示非负整数，包括零。'))
    good['coverage'][-1]['obligation_ids'].append('NEW-convention')
    bad = deepcopy(good)
    for i, row in enumerate(bad['obligations'][-2:]):
        row['concept_id'] = 'NEW-C-' + str(i)
    return source, bad, good


def math_case():
    content, source = teaching_case()
    content['teaching_plan']['claims'][0]['math_model_refs'] = ['M']
    content['math_models'] = [dict(schema_version='1.0', model_id='M', symbols=[], objects=[],
        conditions=[], claims=[dict(claim_id='K', kind='abstract', math_object_id='n-object',
            reason='集合约定需要逐字来源审核，不能凭一次代入证明。')], source_refs=['SO'])]
    bad = authoring(content)
    good = deepcopy(bad)
    model = good['content']['math_models'][0]
    model['symbols'] = [dict(name='n', domain='integer', assumptions=['nonnegative'])]
    model['objects'] = [dict(math_object_id='n-object', kind='scalar', expr={'op':'var','name':'n'})]
    return source, bad, good


def test_inventory_prompt_separates_new_obligation_ids_from_closed_concept_ids():
    source, _, _ = inventory_case()
    messages = build_inventory_messages(source)
    data = json.loads(messages[-1]['content'])
    field = data['output_json_schema']['$defs']['InventoryObligationV1']['properties']['concept_id']
    assert field['enum'] == [''] + data['concept_ids']
    assert 'NEW-* 不能用于 concept_id' in messages[0]['content']


def test_inventory_reports_all_foreign_concepts_with_exact_paths():
    source, bad, _ = inventory_case()
    with pytest.raises(ContractError) as caught:
        validate_source_inventory(source, bad)
    message = str(caught.value)
    for i in range(len(bad['obligations']) - 2, len(bad['obligations'])):
        assert '/obligations/' + str(i) + '/concept_id' in message
    assert 'NEW-C-0' in message and 'NEW-C-1' in message


def test_inventory_rejects_foreign_concept_ids_once_with_actionable_feedback(tmp_path):
    """R3-1：内层取消 —— 非法 concept_id 只花一次调用，反馈写进 journal。"""
    source, bad, good = inventory_case()
    journal = tmp_path/'inventory.json'; saved = []
    client = SequenceClient(bad)
    with pytest.raises(ContractError):
        extract_source_inventory(source, client, saved.append, correction_journal=journal)
    assert len(client.calls) == 1, '内层不再重交'
    assert saved == [bad]
    state = json.loads(journal.read_text())
    assert len(state['attempts']) == 1
    feedback = state['merged_feedback']
    assert len(feedback['errors']) == 2
    assert all(p.endswith('/concept_id') for p in feedback['allowed_paths'])
    # 合法答复一次通过，并可重放（journal 定稿后不再调用）
    ok_journal = tmp_path/'inventory-ok.json'
    result = extract_source_inventory(source, SequenceClient(good), correction_journal=ok_journal)
    assert result.obligation_extraction_status == 'semantic_verified'
    replay = SequenceClient()
    assert extract_source_inventory(source, replay, correction_journal=ok_journal).digest == result.digest
    assert replay.calls == []


def test_inventory_correction_can_repair_coverage_outside_suggested_paths(tmp_path):
    source, bad, good = inventory_case()
    bad['coverage'] = []
    journal = tmp_path / 'inventory.json'
    client = SequenceClient(bad, good)

    # R3-1：一次调用即判；覆盖问题超出建议路径也要能被指出（反馈里保留真正出错的位置）
    with pytest.raises(ContractError):
        extract_source_inventory(source, client, correction_journal=journal)
    assert len(client.calls) == 1
    feedback = json.loads(journal.read_text())['merged_feedback']
    assert all(path.endswith('/concept_id') for path in feedback['allowed_paths'])
    # 合法答复一次通过
    result = extract_source_inventory(source, SequenceClient(good))
    assert result.obligation_extraction_status == 'semantic_verified'
    assert result.digest == validate_source_inventory(source, good).digest






def test_inventory_rejection_is_persistent_and_replays_without_a_call(tmp_path):
    """R3-1：内层纠正轮取消 —— 一次判负；journal 定稿后重放不再花调用。"""
    source, bad, _ = inventory_case()
    journal = tmp_path/'inventory.json'
    client = SequenceClient(bad)
    with pytest.raises(ContractError, match='逐条修正'):
        extract_source_inventory(source, client, correction_journal=journal)
    assert len(client.calls) == 1
    state=json.loads(journal.read_text())
    assert len(state['attempts']) == 1 and state['merged_feedback']['errors']
    replay = SequenceClient()
    with pytest.raises(ContractError, match='逐条修正'):
        extract_source_inventory(source, replay, correction_journal=journal)
    assert replay.calls == []


def test_author_abstract_object_binding_is_rejected_with_actionable_feedback(tmp_path):
    """R3-1：一次调用即判；反馈保留精确路径。合法答复另一次调用通过且可重放。"""
    source, bad, good = math_case(); saved=[]
    journal = tmp_path/'author.json'
    client = SequenceClient(bad)
    with pytest.raises(ContractError, match='逐条修正'):
        generate_content(source, load_content_policy(), client, saved.append, correction_journal=journal)
    assert saved == [bad] and len(client.calls) == 1
    feedback = json.loads(journal.read_text())['merged_feedback']
    # 反馈必须**带精确位置**（信封阶段的数学解析会给出 claim 上的对象引用路径）
    assert feedback['errors'][0]['path'].startswith('/content/math_models')
    assert 'math_object_id' in feedback['errors'][0]['path']
    ok_journal = tmp_path/'author-ok.json'
    result = generate_content(source, load_content_policy(), SequenceClient(good), correction_journal=ok_journal)
    assert result['content']['body_markdown'] == bad['content']['body_markdown']
    from aiforstudy.blocks.content.content_contracts import ContentAuthoringV1
    assert result['media_requirements'] == ContentAuthoringV1.model_validate(good).as_dict()['media_requirements']
    replay = SequenceClient()
    assert generate_content(source, load_content_policy(), replay, correction_journal=ok_journal) == result
    assert replay.calls == []


@pytest.mark.parametrize('mutation', ['body', 'condition', 'remove_claim', 'dummy_object', 'media'])
def test_math_correction_allows_teaching_edits_but_preserves_ids_and_rejects_placeholders(mutation):
    source, bad, good = math_case()
    model=good['content']['math_models'][0]
    if mutation == 'body': good['content']['body_markdown'] += '改变原文结论。'
    elif mutation == 'condition': model['conditions'] = [dict(op='eq',left={'op':'var','name':'n'},right={'op':'const','value':0})]
    elif mutation == 'remove_claim': model['claims'] = []
    elif mutation == 'dummy_object': model['objects'][0]['expr'] = {'op':'const','value':0}
    else: good['media_requirements']['decisions'][0]['reason'] = '改成别的媒体理由'
    # R3-1：只有一次答复机会 —— 用"只带这一处变异"的合法信封验证处置
    if mutation in {'body', 'condition', 'media'}:
        result = generate_content(source, load_content_policy(), SequenceClient(good))
        assert result['content']['body_markdown'] == good['content']['body_markdown']
        assert 'approval' not in result
    elif mutation == 'dummy_object':
        # 占位对象（常量顶替变量依赖）**每次校验都拦**，一次即判并给出可执行反馈
        client = SequenceClient(good)
        with pytest.raises(ContractError, match='placeholder'):
            generate_content(source, load_content_policy(), client)
        assert len(client.calls) == 1
    else:
        # 删掉被引用的 claim 会让数学回链站不住：报的是"重复/未知/自相矛盾的回链"
        with pytest.raises(ContractError, match='frozen|unauthorized|contradictory'):
            generate_content(source, load_content_policy(), SequenceClient(good))


def test_placeholder_object_is_rejected_with_actionable_feedback(tmp_path):
    """D16：复现真实 run-20261011-125024 的形状——模型先补常量占位，被拦。

    原来闸门抛普通 ContractError → lesson 首份就 blocked、2 轮配额只用了 1 轮。
    现在退回一轮并说明怎么改：补成真正依赖变量的对象就能通过。
    """
    source, bad, good = math_case()
    placeholder = deepcopy(bad)
    model = placeholder['content']['math_models'][0]
    model['symbols'] = [dict(name='n', domain='integer', assumptions=['nonnegative'])]
    model['objects'] = [dict(math_object_id='n-object', kind='scalar', expr={'op': 'const', 'value': 0})]
    client = SequenceClient(placeholder)
    journal = tmp_path/'placeholder.json'
    with pytest.raises(ContractError, match='逐条修正'):
        generate_content(source, load_content_policy(), client, correction_journal=journal)
    assert len(client.calls) == 1, '内层不再重交'
    feedback = json.loads(journal.read_text())['merged_feedback']
    assert feedback['errors'][0]['code'] == 'constant_placeholder_object'
    # 报错路径是 claim 路径：复检机制靠它在下轮重新读出同一个对象，
    # 所以模型把占位对象原样交回来也逃不掉（否则下一轮就没人再看它了）。
    assert feedback['errors'][0]['path'] == '/content/math_models/0/claims/0/math_object_id'
    assert feedback['append_only']
    # 合法答复一次通过
    result = generate_content(source, load_content_policy(), SequenceClient(good))
    assert result['content']['math_models'][0]['objects'][0]['expr'] == {'op': 'var', 'name': 'n'}


@pytest.mark.parametrize('valid',[True,False])
def test_concrete_relation_allows_constant_operand_and_still_gets_math_verification(valid):
    from aiforstudy.blocks.content.math_checks import check_content_math
    source,_,candidate=math_case()
    model=candidate['content']['math_models'][0]
    var={'op':'var','name':'n'}
    model['objects']=[{'math_object_id':'left','kind':'scalar',
        'expr':{'op':'sub','left':var,'right':var} if valid else var},
        {'math_object_id':'zero','kind':'scalar','expr':{'op':'const','value':0}}]
    model['claims']=[{'claim_id':candidate['content']['teaching_plan']['claims'][0]['id'],
        'kind':'identity','math_object_id':'left','lhs_object_id':'left','rhs_object_id':'zero'}]
    content=generate_content(source,load_content_policy(),SequenceClient(candidate))['content']
    checks=check_content_math(content).as_dict()['checks']
    assert checks[0]['status']==('pass' if valid else 'fail')


def test_math_syntax_error_is_never_silently_executed_or_removed(tmp_path):
    """D16 放宽：不支持的算子不再首份判死，而是**退回一轮**让模型改。

    保证没变：宿主**从不执行**表达式，也**不会**把这段关系悄悄删掉当作通过——
    它必须原样退回给模型，并附上精确原因。拒不改正、轮次用尽，仍然是致命错误。
    """
    source, _, good = math_case()
    bad = deepcopy(good)
    bad['content']['math_models'][0]['objects'][0]['expr'] = {'op': 'execute', 'code': 'bad'}
    client = SequenceClient(bad)
    journal = tmp_path/'syntax.json'
    with pytest.raises(ContractError, match='逐条修正'):
        generate_content(source, load_content_policy(), client, correction_journal=journal)
    assert len(client.calls) == 1, '一次即判，不是首份判死、也不是静默修正'
    feedback = json.loads(journal.read_text())['merged_feedback']
    reported = [row for row in feedback['errors'] if row.get('code') == 'math_model_invalid']
    assert reported and 'unsupported operator' in reported[0]['detail']
    # 宿主从不执行表达式，也不会把关系悄悄删掉：合法答复才能通过
    result = generate_content(source, load_content_policy(), SequenceClient(good))
    assert result['content']['math_models'][0]['objects'][0]['expr'] == {'op': 'var', 'name': 'n'}


def test_missing_math_object_keeps_precise_model_location(tmp_path):
    source, _, good = math_case()
    bad = deepcopy(good)
    second = deepcopy(bad['content']['math_models'][0])
    second['model_id'] = 'second-model'
    second['claims'][0]['math_object_id'] = 'missing-object'
    bad['content']['math_models'].append(second)
    journal = tmp_path / 'missing-object.json'
    with pytest.raises(ContractError, match='逐条修正'):
        generate_content(source, load_content_policy(), SequenceClient(bad), correction_journal=journal)
    errors = json.loads(journal.read_text())['merged_feedback']['errors']
    assert any(row['path'] == '/content/math_models/1/claims/0/math_object_id'
               and row['code'] == 'unknown_math_object_id' for row in errors)


@pytest.mark.parametrize('binding', ['cancelled_variable', 'existing_constant'])
def test_math_correction_rejects_disguised_or_preexisting_placeholder(binding):
    source, bad, good = math_case()
    if binding == 'cancelled_variable':
        good['content']['math_models'][0]['objects'][0]['expr'] = {
            'op': 'sub', 'left': {'op': 'var', 'name': 'n'},
            'right': {'op': 'var', 'name': 'n'}}
    else:
        bad['content']['math_models'][0]['objects'] = [
            dict(math_object_id='zero', kind='scalar', expr={'op': 'const', 'value': 0})]
        good = deepcopy(bad)
        good['content']['math_models'][0]['claims'][0]['math_object_id'] = 'zero'
    client = SequenceClient(good)
    if binding == 'cancelled_variable':
        # `n - n`：有变量但恒定 → 见证式检查拦下
        with pytest.raises(ContractError, match='placeholder|dependence'):
            generate_content(source, load_content_policy(), client)
        assert len(client.calls) == 1
    else:
        # `existing_constant`：该模型的符号表为空，常量是唯一可能的表达，
        # 占位判据**不适用**（避免把合法常量误判）。这里断言"不再报占位"。
        result = generate_content(source, load_content_policy(), client)
        assert result['content']['math_models'][0]['claims'][0]['math_object_id'] == 'zero'


def test_response_persisted_before_failing_sink_is_reused_on_restart(tmp_path):
    source, _, good = inventory_case(); journal=tmp_path/'inventory.json'
    def broken_sink(_): raise OSError('sink unavailable')
    with pytest.raises(OSError):
        extract_source_inventory(source, SequenceClient(good), broken_sink, correction_journal=journal)
    client=SequenceClient()
    assert extract_source_inventory(source, client, correction_journal=journal).obligation_extraction_status == 'semantic_verified'
    assert client.calls == []


def test_interrupted_call_keeps_reservation_and_does_not_restart_generation(tmp_path):
    source, bad, _=inventory_case(); journal=tmp_path/'inventory.json'
    client=SequenceClient(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt): extract_source_inventory(source, client, correction_journal=journal)
    assert len(json.loads(journal.read_text())['attempts']) == 1
    replay=SequenceClient()
    with pytest.raises(ContractError, match='incomplete.*attempt'):
        extract_source_inventory(source, replay, correction_journal=journal)
    assert replay.calls == []


def test_author_rejection_is_persistent_and_replays_without_a_call(tmp_path):
    """R3-1：一次答复即判；被拒也要持久，重放不再花调用。"""
    source, bad, _ = math_case(); journal = tmp_path/'author.json'; saved = []
    client = SequenceClient(bad)
    with pytest.raises(ContractError, match='逐条修正'):
        generate_content(source, load_content_policy(), client, saved.append, correction_journal=journal)
    assert saved == [bad] and len(client.calls) == 1
    replay = SequenceClient()
    with pytest.raises(ContractError, match='逐条修正'):
        generate_content(source, load_content_policy(), replay, correction_journal=journal)
    assert replay.calls == []


@pytest.mark.parametrize('corrupt', ['input', 'response'])
def test_journal_changed_input_or_response_cannot_reset_budget(tmp_path, corrupt):
    source, _, good = inventory_case(); journal = tmp_path/'inventory.json'
    extract_source_inventory(source, SequenceClient(good), correction_journal=journal)
    state = json.loads(journal.read_text())
    if corrupt == 'input': state['input_hash'] = 'different'
    else: state['attempts'][0]['response']['obligations'][0]['conclusion'] = 'different'
    journal.write_text(json.dumps(state))
    replay = SequenceClient()
    with pytest.raises(ContractError, match='frozen input|hash mismatch'):
        extract_source_inventory(source, replay, correction_journal=journal)
    assert replay.calls == []


def test_pipeline_inventory_recovers_last_corrected_response_without_recalling(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from aiforstudy.pipeline import content_stages as stages
    source, bad, good = inventory_case()
    monkeypatch.setattr(stages, '_structural_context', lambda ctx: source)
    monkeypatch.setattr(stages, '_root', lambda ctx: tmp_path)
    ctx = SimpleNamespace(node=SimpleNamespace(scope_key='U'))
    # R3-1：只有一次答复机会 —— 直接给合法答复，验证"journal 已接收但 current 头丢失"可恢复
    client = SequenceClient(good)
    first = stages._prepare_source_context(ctx, client)
    current = tmp_path/'source_context/U/current.json'
    assert json.loads(current.read_text())['response'] == good
    responses = sorted((current.parent/'responses').glob('*.json'))
    assert [json.loads(path.read_text())['response'] for path in responses] == [good]
    current.unlink()  # Simulate interruption between accepted journal and current head.
    replay = SequenceClient()
    assert stages._prepare_source_context(ctx, replay) == first
    assert json.loads(current.read_text())['response'] == good
    assert replay.calls == [] and sorted((current.parent/'responses').glob('*.json')) == responses


def test_prompt_contains_valid_complex_components_and_abstract_examples():
    from aiforstudy.blocks.content.prompt import build_content_messages
    from aiforstudy.blocks.math.contracts import parse_math_model
    _, source=teaching_case()
    payload=json.loads(build_content_messages('author', source, load_content_policy())[-1]['content'])
    examples=payload['math_representation_examples']
    assert {'complex_components','abstract_source_review'} <= set(examples)
    for value in examples.values(): parse_math_model(value['model'])
    assert examples['abstract_source_review']['model']['objects']


# --------------------------------------------------------------------------- #
# D16-1 补口：语法坏掉的 JSON 是**可纠正错误**，不是传输失败
# --------------------------------------------------------------------------- #
class _RawProtocolClient:
    """按次序交回"完整但语法坏掉"的答复；模拟网关 `protocol_errors=True` 的返回。"""

    def __init__(self, *texts):
        self.texts = list(texts)
        self.calls = []

    def chat_json(self, messages, *, tag='', protocol_errors=False, **kwargs):
        from aiforstudy.providers.gateway import UNPARSED_PROTOCOL
        self.calls.append((tag, protocol_errors))
        text = self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]
        if text == 'VALID':
            return {'data': {'ok': True}}
        return {UNPARSED_PROTOCOL: (text, "bad-json: Expecting ',' delimiter: line 1 column 9 (char 8)")}


def _syntax_validator(raw):
    from aiforstudy.blocks.content.generation_corrections import LocalCorrectionRequired
    if isinstance(raw, dict) and raw.get('ok'):
        return raw
    raise LocalCorrectionRequired('需要修正', errors=[{'path': '/', 'code': 'schema',
        'fix': 'x', 'passing_criterion': 'y'}], allowed_paths=['/'])


MESSAGES = [{'role': 'system', 'content': 's'}, {'role': 'user', 'content': '{}'}]
BAD_JSON = '{"content": {"a": 1 "b": 2}}'


def test_bad_json_is_a_correctable_error_not_a_fatal_model_call(tmp_path):
    """漏逗号 → 拿回原文当可纠正错误、并**真的**再要一轮；不是重发到判死整节。

    实测来源：run-20000101-000000-5d7b83 的 SS02 —— 模型三份答复都少一个逗号，
    旧行为是"网关重发同一请求两次 → ModelCallFailed → 整节 blocked"。
    """
    from aiforstudy.blocks.content.generation_corrections import run_local_generation
    # Syntax has its own five-correction allowance, even when field repair is 0.
    journal = tmp_path/'syntax.json'
    client = _RawProtocolClient(BAD_JSON)
    with pytest.raises(ContractError, match='逐条修正'):
        run_local_generation(MESSAGES, client, _syntax_validator, tag='content-repair:U',
                             correction_tag='content-repair-correction:U', correction_journal=journal)
    assert [tag for tag, _ in client.calls] == ['content-repair:U'] + ['content-repair-correction:U'] * 5
    assert all(flag for _, flag in client.calls), '交付物协议调用必须声明 protocol_errors=True'
    feedback = json.loads(journal.read_text())['merged_feedback']
    assert feedback['errors'][0]['code'] == 'json_syntax'
    # 合法答复一次通过
    out = run_local_generation(MESSAGES, _RawProtocolClient('VALID'), _syntax_validator,
                               tag='content-repair:U', correction_tag='content-repair-correction:U')
    assert out == {'ok': True}


def test_bad_json_feedback_carries_decoder_position_and_context_only(tmp_path):
    """纠正请求要带**精确解码位置与片段**，且语法轮不下发 preserve 清单（它根本不是 JSON）。"""
    from aiforstudy.blocks.content.generation_corrections import run_local_generation
    client = _RawProtocolClient(BAD_JSON)
    journal = tmp_path/'syntax.json'
    with pytest.raises(ContractError, match='逐条修正'):
        run_local_generation(MESSAGES, client, _syntax_validator,
                             tag='content-repair:U', correction_tag='content-repair-correction:U',
                             correction_journal=journal)
    feedback = json.loads(journal.read_text())['merged_feedback']
    error = feedback['errors'][0]
    assert error['code'] == 'json_syntax'
    # 位置取自 Python 自己的解码器，不写死常量：报的就是这一份原文的真实坏点。
    with pytest.raises(json.JSONDecodeError) as caught:
        json.loads(BAD_JSON)
    assert error['position']['char'] == caught.value.pos
    assert '⟪错误位置⟫' in error['context']
    assert error['context'].startswith('{') and error['context'].rstrip().endswith('}')
    assert error['passing_criterion']
    assert feedback['original_response'] == BAD_JSON
    # 完整返回 JSON 不再授意从头重写；错误行与外层指令都须保留无关内容。
    for instruction in (feedback['instruction'], error['fix']):
        assert '整份重写' not in instruction and '完整重写' not in instruction
        assert '语法' in instruction and '完整' in instruction
        assert '保留' in instruction or '保持原样' in instruction
    assert 'preserve' not in feedback, '语法坏掉时没有可比结构，preserve 清单不该下发'


def test_persistent_bad_json_stops_after_five_syntax_corrections():
    """连交坏 JSON 时，1 原始 + 5 语法纠正后停止，不无限重发。"""
    from aiforstudy.blocks.content.generation_corrections import run_local_generation
    client = _RawProtocolClient(BAD_JSON)
    with pytest.raises(ContractError, match='逐条修正'):
        run_local_generation(MESSAGES, client, _syntax_validator,
                             tag='content-repair:U', correction_tag='content-repair-correction:U')
    assert [tag for tag, _ in client.calls] == ['content-repair:U'] + ['content-repair-correction:U'] * 5
