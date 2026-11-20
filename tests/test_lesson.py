"""V4 author/source regressions retained from the retired direct lesson writer.

Historical lesson generation is no longer imported or resurrected. Production
writes and repair loops are covered through the V5 engine integration tests.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from aiforstudy.blocks.content.content_contracts import content_digest, parse_content
from aiforstudy.blocks.content.content_policy import load_content_policy, check_content
from aiforstudy.blocks.content.generation import generate_content
from aiforstudy.blocks.content.prompt import build_content_messages
from aiforstudy.blocks.content.source_context import SourceContext, extract_source_examples, reference_catalog_from_context
from aiforstudy.errors import ContractError
from test_teaching_policy_v2 import teaching_case
from support.content_case import authoring

ROOT = Path(__file__).resolve().parents[1]


class OfflineAuthor:
    def __init__(self, content):
        self.content = deepcopy(content)
        self.calls = []

    def chat_json(self, messages, **kw):
        self.calls.append((deepcopy(messages), kw))
        return {'data': authoring(deepcopy(self.content)), 'cached': False}


def author_case(unit='U'):
    content, raw = teaching_case()
    content['unit_id'] = raw['unit_id'] = unit
    raw['original_text'] = unit + '.md'
    source = SourceContext.model_validate(raw)
    return content, source, load_content_policy(source.learner_profile)


@pytest.mark.parametrize('relative', ['blocks/lesson/generate.py', 'blocks/lesson/validate.py',
                                     'blocks/lesson/prompt.py', 'blocks/lesson/rubric.py'])
def test_retired_direct_writer_modules_are_absent(relative):
    assert not (ROOT / relative).exists(), 'do not restore a bypass around the current engine'


def test_prompt_survives_latex_braces_and_preserves_complete_source():
    _, source, policy = author_case()
    raw = source.as_dict()
    raw['source_text'] = r'出处全文 $\boldsymbol{x}=\frac{1}{2}$；摘录之外仍是合法出处。'
    messages = build_content_messages('author', raw, policy)
    from support.prompt_context import expand_prompt_material
    payload=json.loads(messages[1]['content'])
    assert expand_prompt_material(payload['frozen_context']['source_text'],payload) == raw['source_text']


def test_one_scoped_unit_calls_author_once_and_keeps_original_inputs():
    content, source, policy = author_case('CH01-S01')
    client = OfflineAuthor(content)
    before = source.as_dict()
    result = generate_content(source, policy, client)['content']
    assert len(client.calls) == 1
    assert client.calls[0][1]['tag'] == 'content-author:CH01-S01'
    assert result['unit_id'] == 'CH01-S01'
    assert result['original_text'] == source.original_text
    assert result['original_sha256'] == source.original_sha256
    assert result['source_context_hash'] == source.digest
    assert source.as_dict() == before and client.content == content


@pytest.mark.parametrize('field,value', [('unit_id', 'foreign'), ('book_id', 'foreign'),
                                         ('original_text', 'foreign.md'), ('original_sha256', 'old'),
                                         ('source_context_hash', 'old')])
def test_author_cannot_change_frozen_scope_or_source(field, value):
    content, source, policy = author_case()
    content[field] = value
    preserved = []
    # 冻结字段被改仍是**硬性质**，但处置已按 R2-3 改成"带精确位置的可执行反馈"
    # （`/content/unit_id: contract`，通过标准写明"冻结字段与宿主一致"），
    # 而不是抛一句无位置的 `frozen ...`。保证不变：永远不被接受。
    with pytest.raises(ContractError, match='冻结|frozen|逐条修正'):
        generate_content(source, policy, OfflineAuthor(content), response_sink=preserved.append)
    assert preserved == [authoring(content)], 'the invalid raw response remains inspectable'


@pytest.mark.parametrize('damage', ['no-version', 'v2', 'v3', 'media', 'missing-body'])
def test_old_or_malformed_author_response_is_retained_and_rejected(damage):
    content, source, policy = author_case()
    if damage == 'no-version': content.pop('_meta')
    if damage in ('v2', 'v3'): content['_meta']['schema_version'] = {'v2': '2.0', 'v3': '3.0'}[damage]
    if damage == 'media': content['figure_requests'] = [{'what': '绕过媒体阶段'}]
    if damage == 'missing-body': content.pop('body_markdown')
    preserved = []
    with pytest.raises(ContractError):
        generate_content(source, policy, OfflineAuthor(content), response_sink=preserved.append)
    # R3-1：内层纠正轮取消 —— 每份坏答复都只保留一次，不再重发。
    assert preserved == [authoring(content)]


def test_request_capacity_rejects_before_author_call_without_truncating_source(monkeypatch):
    # 这里测的是"**超限**即拒、且不截断来源"，不是"默认上限恰好是某个数"。
    # 默认上限已提到 8MB（见 content_policy.MAX_REQUEST_BYTES 的说明），所以把上限临时调小，
    # 让这条不变量与具体数值解耦：将来再调容量也不会把这条测试变成假绿。
    from aiforstudy.blocks.content import generation_corrections
    monkeypatch.setattr(generation_corrections, 'MAX_REQUEST_BYTES', 4096)
    content, source, policy = author_case()
    raw = source.as_dict()
    raw['source_text'] = '完整证据' * 100000
    client = OfflineAuthor(content)
    with pytest.raises(ContractError, match='exceeds'):
        generate_content(raw, policy, client)
    assert client.calls == []
    assert raw['source_text'].endswith('完整证据')


def test_revalidation_recomputes_current_checks_without_model_or_legacy_receipts():
    content, source, policy = author_case()
    content['teaching_plan']['claims'][0]['conditions'] = []
    failed = check_content(content, source, policy)
    assert any(row['rule_id'] == 'CONTENT-COVERAGE' for row in failed.blocking_issues)
    content['teaching_plan']['claims'][0]['conditions'] = ['n 是非负整数']
    fixed = check_content(content, source, policy)
    assert not fixed.blocking_issues
    assert fixed.decision == 'needs_review', 'local checks cannot manufacture review approval'
    assert content_digest(content) != failed.input_hash


def test_frozen_catalog_rename_changes_identity_of_source_context():
    _, source, _ = author_case()
    raw = source.as_dict()
    raw['concept_index'] = {'concepts': [{'id': 'C', 'name': '原名', 'formal': '定义'}]}
    old = reference_catalog_from_context(raw)
    raw['concept_index']['concepts'][0]['name'] = '新名'
    new = reference_catalog_from_context(raw)
    assert old.digest != new.digest
    assert old.entries[0].label == '原名', 'freezing must not retain a mutable live registry'


def test_extract_examples_handles_both_layouts():
    with_solution = "例1.1.1. 解方程组\n$$x=1$$\n解. 直接看出来。\n例1.1.2. 再解一个\n$$y=2$$\n解. 同理。"
    found = extract_source_examples(with_solution)
    assert [e["label"] for e in found] == ["例1.1.1", "例1.1.2"]
    assert found[0]["statement"].endswith("$$x=1$$")
    assert found[0]["book_solution"].startswith("解.")

    # 这本书常用的"变换链"排布：压根没有"解"字
    chain = "例2.2.1. 其矩阵形式如下:\n$$A$$\n$$B$$\n$$C$$"
    found = extract_source_examples(chain)
    assert found[0]["statement"] == "例2.2.1. 其矩阵形式如下:\n$$A$$"
    assert found[0]["book_solution"] == "$$B$$\n$$C$$"


def test_extract_examples_accepts_dash_numbering():
    """《电路分析基础》写的是 `例 2-1`（短横线）—— 老正则只认 `例2.1.1`，
    实测**整本书一道例题都抽不到**，例题阶段从来不跑。"""
    text = """例 2-1 用网孔分析求解图 2-3(a) 所示电路的各支路电流。
解 该电路有两个网孔,首先在每一个网孔内假设一个网孔电流。
第一网孔的自电阻: R11=25

例 2-2 电路如图 2-5 所示, 试求网孔电流 i2。
解 由图可列出两个方程。
"""
    rows = extract_source_examples(text)
    assert [r["label"] for r in rows] == ["例2-1", "例2-2"]
    # 题干是**逐字**的原文块（含"例 2-1"这行前缀），但**不含解答部分**
    assert "用网孔分析求解" in rows[0]["statement"]
    assert "假设一个网孔电流" not in rows[0]["statement"]
    assert "假设一个网孔电流" in rows[0]["book_solution"]


def test_extract_examples_still_accepts_dot_numbering():
    """老格式（`例2.1.1`）不能因为放宽而破掉。"""
    text = "例2.1.1 求矩阵的秩。\n解 化成阶梯形即可。\n"
    rows = extract_source_examples(text)
    assert len(rows) == 1 and rows[0]["label"] == "例2.1.1"
