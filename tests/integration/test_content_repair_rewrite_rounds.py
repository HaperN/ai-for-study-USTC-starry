"""正文整份重写：内层零重试，拒绝反馈持久化到对应外层批次。

无效候选不得发布；原始答复、字段位置与通过标准供下一轮重写使用。
"""
from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

from test_content_generation_v3 import context as stage_context
from test_teaching_policy_v2 import teaching_case
from test_source_inventory import response as inventory_response
from aiforstudy.blocks.content.source_context import SourceContext
from aiforstudy.pipeline import content_stages as stages, stage_io as T

UNIT = 'U'
REPAIR_TAG = f'content-repair:{UNIT}'
REPAIR_CORRECTION_TAG = f'content-repair-correction:{UNIT}'
MAX_CORRECTIONS = 2


class ScriptedRepair:
    """按 tag 分派答复，并把每条请求原文留档供断言检查。

    * `replies`：`{tag: [模式, ...]}`，同一 tag 的第 N 次取第 N 项，**末项重复**；
    * 模式：`'author'` 交带确定性错误的首份正文、`'repair'` 交合格的重写正文、
      `'incomplete'` 交结构完整但过不了校验的正文、`'freeze'` 交改动冻结身份的正文；
    * 来源盘点与独立复核不查 `replies`：前者由 `response()` 生成，后者按当前批次判词。
    """

    def __init__(self, source, candidate, replies, *, reject=True):
        self.source = source
        self.candidate = candidate
        self.replies = {tag: list(items) for tag, items in replies.items()}
        self.calls = []
        self.requests = []
        self.reject = reject
        self.repaired = False

    def _verdicts(self, payload):
        """首份正文把"非负"说成"正"，独立复核必须判它不过 —— 这才有真工单。"""
        rows = []
        for item in payload['criteria']:
            refs = [{'evidence_id': eid, 'quote': ev['text']}
                    for kind in item['required_evidence_kinds']
                    for eid, ev in payload['evidence_catalog'].items()
                    if ev['kind'] == kind and item['subject_id'] in ev['subject_ids']]
            bad = (self.reject and not self.repaired
                   and item['criterion_id'] == 'SOURCE-FIDELITY:SO')
            rows.append({'criterion_id': item['criterion_id'], 'version': item['version'],
                'status': 'fail' if bad else 'pass',
                'reason': '正文把非负说成正，漏掉零；改为非负且包括零。' if bad
                          else '来源条件和当前对应读者文字相符。',
                'evidence_refs': refs})
        return rows

    def _reply(self, mode):
        if mode == 'author':
            # 首份正文：刻意带上确定性错误（"正整数"漏掉零），好让审核产出真工单。
            value = self._authoring(self.candidate)
            value['content']['body_markdown'] = 'n 表示正整数，不含零。'
            return value
        if mode == 'freeze':
            value = self._authoring(self.candidate)
            value['content']['unit_id'] = 'TAMPERED'
            return value
        if mode == 'repair':
            self.repaired = True
            value = self._authoring(self.candidate)
            value['content']['body_markdown'] = (
                self.source.source_text + ' 请判断 n=0 是否允许，并说明理由。')
            return value
        if mode == 'incomplete':
            # 不合格但**结构完整**：summary 给了数字。
            # 信封版本与 content 两条前置硬校验先过，之后 `_collect_author_errors`
            # 报出带精确 path 的错误 —— 那才是"可纠正"的通路。
            # 写错 `_meta.schema_version` 会先撞上前置硬校验，直接判死，测不到轮次。
            value = self._authoring(self.candidate)
            value['content']['summary'] = 123
            return value
        raise AssertionError('unknown scripted reply ' + repr(mode))

    @staticmethod
    def _authoring(content):
        from support.content_case import authoring
        return authoring(deepcopy(content))

    def chat_json(self, messages, **kw):
        tag = kw.get('tag', '')
        self.calls.append(tag)
        self.requests.append(messages)
        if tag.startswith('content-source-inventory:'):
            return {'data': inventory_response(self.source, extra=False)}
        if tag.startswith('content-review:'):
            # 独立复核判词按当前批次返回一次；criteria/evidence_catalog 在 frozen_context 里。
            request = json.loads(messages[1]['content'])['frozen_context']
            return {'data': {'verdicts': self._verdicts(request)}}
        queue = self.replies.get(tag)
        if not queue:
            raise AssertionError('unexpected provider call ' + repr(tag))
        mode = queue.pop(0) if len(queue) > 1 else queue[0]
        return {'data': self._reply(mode)}


def setup(tmp_path, monkeypatch, replies):
    candidate, raw = teaching_case()
    candidate['body_markdown'] = 'n 表示正整数，不含零。'
    source = SourceContext.model_validate(raw).model_copy(
        update={'obligation_extraction_status': 'structural_only'})
    source = source.model_copy(update={'source_context_hash': source.digest})
    monkeypatch.setattr(stages, '_structural_context', lambda ctx: source)
    client = ScriptedRepair(source, candidate, replies)
    monkeypatch.setattr(T, '_gateway_client', lambda *a, **k: (client, 'scripted-offline'))
    return client, candidate


def _request_payload(messages):
    """取宿主交给模型的请求体（第 2 条消息就是 JSON 请求）。"""
    return json.loads(messages[1]['content'])


def _last_payload(messages):
    """取最后一条消息：纠正轮把反馈**追加**在原始请求之后。"""
    return json.loads(messages[-1]['content'])


def _reason(result):
    error = result.error
    return error if isinstance(error, str) else str((error or {}).get('reason'))


def open_repair_batch(tmp_path, monkeypatch, replies):
    """跑完首份正文 + 审核，造出真实工单，再把上下文停在"等待返修"上。"""
    client, candidate = setup(tmp_path, monkeypatch, replies)
    ctx = stage_context(tmp_path)
    first = stages.lesson_stage(ctx)
    assert first.execution_state.value == 'succeeded', first.error
    ctx.node.stage = 'text_audit'
    audited = stages.text_audit_stage(ctx)
    assert audited.execution_state.value == 'succeeded', audited.error
    payload = json.loads((stages._root(ctx) / f'text_audit/{UNIT}.json').read_text())['payload']
    tickets = [t for t in payload['tickets'] if t['status'] == 'ready']
    assert tickets, '首份有确定性错误的正文必须产出可执行工单'
    ctx.repair_batch = SimpleNamespace(jobs=[SimpleNamespace(
        owner='lesson', kind='text_content', ticket_ids=[t['ticket_id'] for t in tickets])])
    ctx.node.stage = 'lesson'
    return ctx, client, tickets


def test_repair_prompt_carries_previous_content_as_draft(tmp_path, monkeypatch):
    """第 1 条：返修请求里是**上一次正文**和工单，不是补丁授权。"""
    # 返修走 `repair` 角色提示词（不是 author），所以第一次返修调用回 'repair' 模板。
    replies = {f'content-author:{UNIT}': ['author'], REPAIR_TAG: ['repair']}
    ctx, client, tickets = open_repair_batch(tmp_path, monkeypatch, replies)
    result = stages.lesson_stage(ctx)
    assert result.execution_state.value == 'succeeded', result.error

    repair_calls = [m for tag, m in zip(client.calls, client.requests) if tag == REPAIR_TAG]
    assert len(repair_calls) == 1, client.calls
    request = _request_payload(repair_calls[0])
    assert request['role_task'] == 'repair'
    frozen = request['frozen_context']
    assert frozen['content']['body_markdown'] == 'n 表示正整数，不含零。', (
        '底稿必须是上一次正文（正是被打回的那一版）')
    assert frozen['content']['unit_id'] == UNIT
    assert [t['ticket_id'] for t in frozen['repair_tickets']] == [t['ticket_id'] for t in tickets]
    # 补丁协议的痕迹不能出现在返修请求里：整份重写没有"授权路径"这回事。
    # 提示词里的交付物契约必须与校验器一致，否则模型每次都交回补丁（不合规）。
    assert 'patch_contract' not in frozen
    assert request['output_contract']['schema_version'] == 'ContentAuthoringV1', (
        request['output_contract'])
    assert '完整正文' in request['output_contract']['instruction']
    published = json.loads((stages._root(ctx) / f'lesson/{UNIT}.json').read_text())['payload']
    assert published['content']['body_markdown'].endswith('请判断 n=0 是否允许，并说明理由。')
    assert published['content']['revision'] == 2


def test_bad_repair_output_is_rejected_once_with_actionable_feedback(tmp_path, monkeypatch):
    """R3-1：内层纠正轮取消 —— 一次答复即判，可执行反馈落在 journal 的 merged_feedback 里。

    旧断言是"原始返修 + 1 次纠正"两次调用；现在只有一次，反馈改从 journal 读。
    """
    replies = {f'content-author:{UNIT}': ['author'], REPAIR_TAG: ['incomplete']}
    ctx, client, _ = open_repair_batch(tmp_path, monkeypatch, replies)
    result = stages.lesson_stage(ctx)
    assert result.execution_state.value == 'blocked', result.as_dict()

    assert client.calls.count(REPAIR_TAG) == 1, client.calls
    assert client.calls.count(REPAIR_CORRECTION_TAG) == 0, '内层不再重交'
    journal = json.loads(ctx.active_generation_journal.read_text())
    feedback = journal['merged_feedback']
    assert feedback['role_task'] == 'local_generation_correction'
    assert feedback['original_response'], '反馈必须带上被判不合格的那一份答复'
    paths = {row['path'] for row in feedback['errors']}
    assert paths, feedback['errors']
    # 允许反馈附带更精确的字段路径（`content.summary` 这类信封校验）：
    # 关键是**每条错误都带位置**，且授权面覆盖它们。
    assert set(feedback['allowed_paths']) == paths
    assert any(row.get('passing_criterion') for row in feedback['errors'])
    # 拒绝只保留诊断；不能发布无效候选。
    published = json.loads((stages._root(ctx) / f'lesson/{UNIT}.json').read_text())['payload']
    assert published['content']['revision'] == 1


def test_repair_rejection_is_decided_once_and_blocks(tmp_path, monkeypatch):
    """R3-1：一次答复即判（不再有 1 原始 + 2 纠正）；被判死时仍 blocked。"""
    replies = {f'content-author:{UNIT}': ['author'], REPAIR_TAG: ['incomplete']}
    ctx, client, _ = open_repair_batch(tmp_path, monkeypatch, replies)
    before = list(client.calls)
    result = stages.lesson_stage(ctx)

    assert result.execution_state.value == 'blocked', result.as_dict()
    attempts = [tag for tag in client.calls if tag.startswith('content-repair')]
    assert attempts == [REPAIR_TAG], attempts
    assert client.calls == before + attempts, '不得再多花模型调用'
    assert '逐条修正' in _reason(result) or 'local correction' in _reason(result), result.error
    # 被判死的返修不该发布新版本：最后一份合格正文仍是 revision 1。
    published = json.loads((stages._root(ctx) / f'lesson/{UNIT}.json').read_text())['payload']
    assert published['content']['revision'] == 1, '轮次用尽时不得发布未通过校验的正文'


def test_frozen_identity_rejection_has_feedback_without_inner_retry(tmp_path, monkeypatch):
    """冻结身份不接受篡改；生成阶段给外层反馈，不增加内层调用。"""
    replies = {f'content-author:{UNIT}': ['author'], REPAIR_TAG: ['freeze']}
    ctx, client, _ = open_repair_batch(tmp_path, monkeypatch, replies)
    result = stages.lesson_stage(ctx)
    assert result.execution_state.value == 'blocked', result.as_dict()
    assert 'frozen unit_id' in _reason(result) or '逐条修正' in _reason(result), result.error
    assert client.calls.count(REPAIR_CORRECTION_TAG) == 0, '内层纠正轮已取消'
