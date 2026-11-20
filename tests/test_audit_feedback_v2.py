"""T02：**反馈传递**与**返修角色**的行为测试（§5.2）。

三件事必须被钉死：

1. `test_generator_receives_previous_failure_and_source` —— 用 **spy transport** 断言
   责任生成器**实际收到**了：教材依据、逐条 criterion、前次 `verdict.why`（完整）、
   上一次证据被拒的原因、上一次补丁被拒的原因、以及那一格旧的 `required_condition`
   （**两格分开，不许互相覆盖** —— 那正是调研 §1.2 记的病）。
2. `test_lesson_repairs_do_not_use_judge_role` —— 正文返修走 **lesson 生成角色**；
   judge 只做核验。账本上写的就是这次调用算在哪个角色上。
3. `test_full_repair_replay_is_recorded` —— 一次完整的
   「发现 → 生成反馈 → 补丁 → 核验」回放，并把 JSON 写进本轮证据目录。

离线：只用替身 transport（`tests/support/offline_guard.py` 已装会话级守卫）。
计划依据：主计划 §5.2；验收 A02、A03。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import pytest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
# 旧树（`blocks/lesson/generate.py` 等）按顶层模块名导入 —— 与既有测试同一套做法。
for _legacy_dir in ("blocks/lesson", "blocks/audit", "lib"):
    _entry = str(ROOT / _legacy_dir)
    if _entry not in sys.path:
        sys.path.append(_entry)

from aiforstudy.blocks.audit.contracts import (  # noqa: E402
    ArtifactRevision,
    Criterion,
    CriterionVerdict,
    EvidenceSpan,
    PatchOperation,
    PatchSet,
    legacy_finding_to_ticket,
)
from aiforstudy.blocks.audit.evidence import locate_evidence  # noqa: E402
from aiforstudy.blocks.audit.feedback import (  # noqa: E402
    build_repair_payload,
    evaluate_closure,
    owner_role,
)
from aiforstudy.blocks.audit.loop import Finding  # noqa: E402
from aiforstudy.blocks.audit.patches import apply_patchset  # noqa: E402
from aiforstudy.errors import ContractError
from aiforstudy.core.usage import UsageLedger  # noqa: E402
from aiforstudy.pipeline.execution_control import ExecutionControl, ExecutionPolicy  # noqa: E402
from aiforstudy.pipeline.services import CancelToken  # noqa: E402
from aiforstudy.providers.gateway import Gateway  # noqa: E402

UNIT = "CH04-S01"
ARTIFACT_ID = f"lesson:{UNIT}"
POINTER = "/body_markdown"
BODY = ("例 2　已知 $v=2x^{2}-2y^{2}+x$，求以它为虚部的解析函数 $f(z)$。\n\n"
        "先验证 $v$ 是调和函数：$\\frac{\\partial^{2} v}{\\partial x^{2}}=4$，"
        "$\\frac{\\partial^{2} v}{\\partial y^{2}}=-4$，相加为 0。\n\n"
        "接下来就用 (4.5) 把 $u$ 积出来，并把满足 $f(0)=1$ 的那一个 $f(z)$ 定到常数为止。\n")
HASH = "sha256:" + hashlib.sha256(BODY.encode("utf-8")).hexdigest()
REVISION = ArtifactRevision(artifact_id=ARTIFACT_ID, unit_id=UNIT, revision=3, hash=HASH)
OLD_SENTENCE = "接下来就用 (4.5) 把 $u$ 积出来"
PRESERVE = "先验证 $v$ 是调和函数"
SOURCE_TEXT = ("教材 §4.5：若已给单连通域内的调和函数 $v(x,y)$，则由线积分所确定的函数 "
               "$$u(x,y)=\\int_{(x_0,y_0)}^{(x,y)}\\frac{\\partial v}{\\partial y}dx"
               "-\\frac{\\partial v}{\\partial x}dy+C_1$$ "
               "使得 $f(z)=u+\\mathrm{i}v$ 在区域内解析，其中 $C_1$ 是实常数。")
SOURCE_QUOTE = "由线积分所确定的函数"
SOURCE_START = SOURCE_TEXT.index(SOURCE_QUOTE)
REQUIRED_CONDITION = "补出折线路线、$u=-4xy-y+C$ 的积分过程与 $C=1$ 的代入步骤"
EXPECTED = "折线路线、u=-4xy-y+C、f(z)=2i z²+i z+1 三处逐字出现在该段内"
LONG_WHY = ("折线路径、u 的计算与 C=1 的代入都已经在成品里逐字出现，链条完整；"
            "这一段的核对意见特意写得比一百六十字长，用来证明旧实现「砍到 160 字」的截断"
            "不会再发生，生成器拿到的是完整解释。" * 3)
EVIDENCE_REJECTION_REASON = "第 2 段引用的是 $u(x,y)=…$，而成品里是 $$u(x,y)=…$$ —— 符号不同，不能按展示空白归一"
PATCH_REJECTION_REASON = "BASE_HASH_MISMATCH：补丁基于旧 hash，artifact 未被修改"

EVIDENCE_DIR = Path(os.environ.get(
    "AIFORSTUDY_T02_EVIDENCE_DIR",
    str(ROOT.parent / "文档" / "02-记录与报告" / "Manim与审核闭环改造证据-20261007-0035" / "T02")))


# --------------------------------------------------------------------------- #
# 装置：spy transport + 真 Gateway + 假 StageContext
# --------------------------------------------------------------------------- #
class SpyTransport:
    """替身传输：记下**真正出去的**请求，并按脚本回一个合法 JSON。"""

    def __init__(self, replies: list[str] | None = None) -> None:
        self.requests: list[dict] = []
        self.replies = list(replies or [])

    def __call__(self, *, model, effort, messages, images, max_tokens, timeout):
        self.requests.append({"model": model, "effort": effort, "messages": messages,
                              "images": images, "max_tokens": max_tokens,
                              "timeout": timeout})
        text = self.replies.pop(0) if self.replies else json.dumps(
            {"replacements": [], "notes": "（替身：本轮不产出替换）"}, ensure_ascii=False)
        return text, {"total_tokens": 7}

    @property
    def wire(self) -> str:
        return "\n".join(json.dumps(item["messages"], ensure_ascii=False)
                         for item in self.requests)


ROLES = {"lesson": {"model": "spy-lesson", "effort": "low", "max_tokens": 512},
         "judge": {"model": "spy-judge", "effort": "low", "max_tokens": 512}}


def make_gateway(spy: SpyTransport,tmp_path: Path) -> Gateway:
    control=ExecutionControl(ExecutionPolicy(parallelism=1));control.bind(tmp_path/'execution.json',run_id='t02-run')
    return Gateway(transport=spy,roles=ROLES,control=control,cache_dir=None,run_id='t02-run',book_id='complex-analysis-v2')


def make_context(spy: SpyTransport, tmp_path: Path):
    workspace = SimpleNamespace(root=str(tmp_path),
                                config_json=lambda name: {"roles": ROLES})
    services = SimpleNamespace(providers=SimpleNamespace(llm=spy), store=SimpleNamespace(run_id="t02-run", usage_path=tmp_path / "usage.jsonl", work=tmp_path, update_budget=lambda _snapshot: None),
                               control=ExecutionControl(ExecutionPolicy(parallelism=1)),
                               cancel=CancelToken())
    node = SimpleNamespace(stage="audit", scope_key=UNIT, node_id=f"audit:{UNIT}")
    context = SimpleNamespace(book_id="complex-analysis-v2", parse=str(tmp_path),
                              title="复变函数")
    request = SimpleNamespace(profile=SimpleNamespace(value="release"))
    return SimpleNamespace(workspace=workspace, services=services, node=node,
                           context=context, request=request,
                           log=lambda *_a, **_k: None)


def make_ticket():
    """一条**能派出去**的工单：六件事齐全，同时带着旧链路那一格的原文。"""
    finding = Finding(code="LOGIC_GAP", severity="P0", unit_key=UNIT,
                      why="例 2 只写到「验证调和」就止住。", evidence=REQUIRED_CONDITION,
                      where=OLD_SENTENCE)
    ticket = legacy_finding_to_ticket(finding, artifact_id=ARTIFACT_ID,
                                      artifact_hash=HASH, unit_id=UNIT)
    return replace(
        ticket, status="ready",
        source_evidence=(EvidenceSpan(artifact_id="parse:CH04-S01",
                                      artifact_hash="sha256:" + "b" * 64,
                                      json_pointer="/body/§4.5", start=SOURCE_START,
                                      end=SOURCE_START + len(SOURCE_QUOTE),
                                      quote=SOURCE_QUOTE),),
        criteria=(Criterion(criterion_id="C-LOGIC-1", version=1, required=True,
                            expected=EXPECTED, method="reviewer", evidence_type="text"),),
        repair_instructions={"locate": f"{POINTER} 末段",
                             "action": "补出折线路线与 C=1 的代入步骤",
                             "rationale": "教材 §4.5 给出由 v 反求 u 的线积分公式",
                             "expected_change": "出现 u=-4xy-y+C 与 f(z)=2i z²+i z+1"},
        allowed_paths=(POINTER,), preserve=(PRESERVE,),
        previous_attempt={"attempt": 1, "verdict_why": LONG_WHY,
                          "evidence_rejection": {"code": "EVIDENCE_INVALID",
                                                 "reason": EVIDENCE_REJECTION_REASON},
                          "patch_rejection": {"code": "BASE_HASH_MISMATCH",
                                              "reason": PATCH_REJECTION_REASON},
                          "verdicts": [{"criterion_id": "C-LOGIC-1", "status": "fail"}]},
    )


def source_context(current_text: str = BODY) -> dict:
    return {"current_text": current_text,
            "source_text": SOURCE_TEXT,
            "toc": [{"unit_id": "CH04-S01", "title": "柯西积分公式"},
                    {"unit_id": "CH04-S02", "title": "调和函数极值原理"}],
            "concept_index": [{"text": "C-0215 柯西积分公式"}, {"text": "C-0229 泊松积分公式"}],
            "source_excerpts": [{"anchor": "§4.5", "text": SOURCE_TEXT}],
            "related_units": [{"unit_id": "CH04-S02"}]}


# --------------------------------------------------------------------------- #
# A02：provider 实际收到了什么
# --------------------------------------------------------------------------- #
def test_generator_receives_previous_failure_and_source(tmp_path):

    ticket = make_ticket()
    payload = build_repair_payload(ticket, REVISION, source_context())
    # 载荷本身是普通 JSON，且"教材依据 / 旧 required_condition"是两格
    assert json.loads(json.dumps(payload, ensure_ascii=False)) == payload
    assert payload["source_evidence"][0]["quote"] == SOURCE_QUOTE
    assert payload["source_evidence"][0]["verified"] is True
    assert payload["required_condition"] == REQUIRED_CONDITION
    assert SOURCE_QUOTE != REQUIRED_CONDITION
    assert payload["criteria"]["all"][0]["criterion_id"] == "C-LOGIC-1"
    assert payload["criteria"]["failed"] == ["C-LOGIC-1"]
    # 完整的 why（旧实现砍到 160 字）
    assert payload["previous_failure"]["verdict_why"] == LONG_WHY
    assert payload["previous_failure"]["verdict_why_chars"] > 160

    spy = SpyTransport()
    gateway = make_gateway(spy, tmp_path)
    from aiforstudy.pipeline.gateway_client import GatewayClient
    client = GatewayClient(gateway, role="lesson", model="spy-lesson", effort="low",
                           stage="audit", unit_key=UNIT, bucket="chapter")
    from aiforstudy.blocks.content.prompt import build_content_messages
    from aiforstudy.blocks.content.content_policy import load_content_policy
    from test_teaching_policy_v2 import teaching_case
    _, current_source = teaching_case()
    messages = build_content_messages('repair',
        {'source_context':current_source,'repair_payload':payload},
        load_content_policy(current_source['learner_profile']),tickets=[ticket])
    client.chat_json(messages, tag='content-patch:'+UNIT)

    assert len(spy.requests) == 1               # 只发了一次请求，没有隐藏重试
    wire = spy.wire
    for expected in (SOURCE_QUOTE, "C-LOGIC-1", EXPECTED, LONG_WHY,
                     EVIDENCE_REJECTION_REASON, PATCH_REJECTION_REASON,
                     REQUIRED_CONDITION, "柯西积分公式", "C-0215", OLD_SENTENCE):
        assert expected in wire, expected
    assert payload["source_evidence"][0]["quote"] in wire
    # 修复建议不构成字段白名单；系统提示仍要求保留身份和来源并全量复验。
    assert (spy.requests[0]["messages"][0]["role"],
            spy.requests[0]["messages"][1]["role"]) == ("system", "user")
    system_prompt = spy.requests[0]["messages"][0]["content"]
    assert '完整 LessonContentV4' in system_prompt
    assert '不是修改白名单' in system_prompt
    assert '全部问题' in system_prompt
    assert 'C-LOGIC-1' in spy.requests[0]['messages'][1]['content']


def test_payload_crops_current_text_but_keeps_toc_and_concept_refs():
    long_body = ("前面的铺垫。" * 400) + "\n\n" + OLD_SENTENCE + "\n\n" + ("后面的收尾。" * 400)
    payload = build_repair_payload(make_ticket(), REVISION,
                                   source_context(current_text=long_body))
    crop = payload["current_text"]["crop"]
    assert crop["cropped"] is True
    assert crop["kept_chars"] < crop["original_chars"]
    assert OLD_SENTENCE in payload["current_text"]["text"]
    # 上下文引用一格都不裁
    assert payload["curriculum_context"]["toc"] == source_context()["toc"]
    assert payload["curriculum_context"]["concept_index"] == source_context()["concept_index"]
    assert payload["curriculum_context"]["source_excerpts"]


# --------------------------------------------------------------------------- #
# A02：正文返修不用 judge 角色
# --------------------------------------------------------------------------- #
def test_lesson_repairs_do_not_use_judge_role(tmp_path, monkeypatch):
    from aiforstudy.pipeline import stage_io
    from support.runner_v5 import TeachingTransport, run_book
    observed=[]
    original=stage_io._gateway_client
    def recording_client(ctx, *, role, bucket):
        client, source = original(ctx, role=role, bucket=bucket)
        if client is not None:
            call=client.chat_json
            def recording_call(messages, **kwargs):
                observed.append((role,kwargs.get('tag','')))
                return call(messages,**kwargs)
            client.chat_json=recording_call
        return client,source
    monkeypatch.setattr(stage_io,'_gateway_client',recording_client)
    result,store,_,transport=run_book(tmp_path,transport=TeachingTransport(reject_first=True))
    assert result.status=='succeeded', result.recovery_hint
    # D16-5：正文返修已改成整份重写，走 lesson 角色的 content-repair 通道，
    # 不再有 content-patch（那个标签随 ContentPatchV1 一起退役）。
    repairs=[role for role,tag in observed if tag.startswith('content-repair:')]
    reviews=[role for role,tag in observed if tag.startswith('content-review:')]
    # 2026-10-16: prose review uses its own role (deepseek-v4-pro), media review keeps judge.
    assert repairs==['lesson'] and reviews and set(reviews)=={'text_judge'}
    assert not store.usage_path.exists()
    # 正文返修的模型调用全部记在 lesson 名下；patch_calls 是"发生过一次正文返修"的计数。
    assert sum(role=="lesson" for role,tag in observed)==transport.author_calls+transport.patch_calls


# --------------------------------------------------------------------------- #
# 完整回放：发现 → 生成反馈 → 补丁 → 核验
# --------------------------------------------------------------------------- #
def test_full_repair_replay_is_recorded(tmp_path):

    replay: dict = {"schema": "t02-replay@1", "unit_id": UNIT}

    # ── 1) 发现：旧 Finding → 候选工单（只读适配，不编造标准）
    finding = Finding(code="LOGIC_GAP", severity="P0", unit_key=UNIT,
                      why="例 2 只写到「验证调和」就止住。", evidence=REQUIRED_CONDITION,
                      where=OLD_SENTENCE)
    candidate = legacy_finding_to_ticket(finding, artifact_id=ARTIFACT_ID,
                                        artifact_hash=HASH, unit_id=UNIT)
    replay["discover"] = {"finding": finding.as_dict(),
                          "candidate_ticket": candidate.as_dict()}
    assert candidate.status == "needs_evidence"      # 旧工单没有依据与标准
    assert candidate.legacy_required_condition == REQUIRED_CONDITION
    assert candidate.source_evidence == ()           # **没有**把 required_condition 当依据

    # ── 2) 反馈：一条完整的 ready 工单 → 给生成器的载荷
    ticket = make_ticket()
    payload = build_repair_payload(ticket, REVISION, source_context())
    replay["feedback"] = {"ticket": ticket.as_dict(), "payload": payload}

    # ── 3) 补丁：原子应用（base_hash 绑定 revision；改动落在白名单里）
    artifact = {"artifact_id": ARTIFACT_ID, "hash": HASH, "body_markdown": BODY,
                "ticket": ticket.as_dict()}
    start = BODY.index(OLD_SENTENCE)
    end = BODY.index("\n", start)
    new_text = (OLD_SENTENCE.replace("积出来", "积出来：先沿 $x$ 轴走到 $(x,0)$，"
                                         "得 $u=-4xy-y+C$；再由 $f(0)=1$ 定出 $C=1$，"
                                         "于是 $f(z)=2\\mathrm{i}z^{2}+\\mathrm{i}z+1$"))
    patch = PatchSet(ticket_ids=(ticket.ticket_id,), base_hash=HASH,
                     criterion_ids=("C-LOGIC-1",),
                     operations=(PatchOperation(op="replace", path=POINTER, start=start,
                                                end=end, text=new_text),),
                     explanation="补出折线路线、u 与 C=1")
    outcome = apply_patchset(patch, REVISION, artifact)
    assert outcome.applied is True, outcome.reason
    replay["patch"] = {"patchset": patch.as_dict(), "outcome": outcome.as_dict()}
    patched_text = artifact["body_markdown"]
    assert "$u=-4xy-y+C$" in patched_text
    assert PRESERVE in patched_text
    new_revision = ArtifactRevision(artifact_id=ARTIFACT_ID, unit_id=UNIT,
                                    revision=outcome.result_revision,
                                    hash=artifact["hash"], parent_hash=HASH)

    # ── 3b) 证据定位：复核给的是**待验证的声明 span**，定位由 evidence.py 做
    quote = "先沿 $x$ 轴走到 $(x,0)$"

    # Current evidence contracts require the frozen identity/version explicitly.
    # The retired numbered verdict bridge must not guess those identities.
    quote = "先沿 $x$ 轴走到 $(x,0)$"
    declared = CriterionVerdict(
        ticket_id=ticket.ticket_id, criterion_id='C-LOGIC-1', criteria_version=1,
        artifact_hash=artifact['hash'], status='pass', why='折线路线、u、C=1 三处逐字出现',
        spans=(EvidenceSpan(artifact_id=ARTIFACT_ID, artifact_hash=artifact['hash'],
            json_pointer=POINTER, start=0, end=len(quote), quote=quote),),
        verifier={'role':'judge','model':'spy-judge','version':'replay'})
    with pytest.raises(ContractError, match="真实 span"):
        replace(declared, spans=())
    unsupported = replace(declared, status="needs_evidence", spans=())
    refused = evaluate_closure(replace(ticket,target={**ticket.target,'artifact_hash':artifact['hash']}),
        new_revision,[unsupported])
    assert refused.closed is False
    resolution = locate_evidence(declared, artifact)
    assert resolution.status == "valid", resolution.reason
    located = declared.__class__(
        ticket_id=declared.ticket_id, criterion_id=declared.criterion_id,
        criteria_version=declared.criteria_version, artifact_hash=declared.artifact_hash,
        status="pass", why=declared.why, spans=resolution.spans,
        verifier=declared.verifier)
    replay["verify"] = {"declared": declared.as_dict(),
                        "missing_evidence_refused": refused.as_dict(),
                        "resolution": resolution.as_dict(),
                        "verdict": located.as_dict()}

    # ── 4) 核验：全部 required criterion 有独立有效证据 → 关单
    decision = evaluate_closure(replace(ticket, target={**ticket.target,
                                                       "artifact_hash": artifact["hash"]}),
                                new_revision, [located])
    replay["closure"] = {"revision": new_revision.as_dict(),
                         "decision": decision.as_dict()}
    assert decision.closed is True, decision.reason

    # ── 5) 反例：旧 hash 的通过不能关新版本的工单（判词与 span 都绑在补丁前的 hash 上）
    stale_verdict = CriterionVerdict(
        ticket_id=ticket.ticket_id, criterion_id="C-LOGIC-1", criteria_version=1,
        artifact_hash=HASH, status="pass", why="折线路线、u、C=1 三处逐字出现",
        spans=(EvidenceSpan(artifact_id=ARTIFACT_ID, artifact_hash=HASH,
                            json_pointer=POINTER, start=0, end=len(quote), quote=quote),),
        verifier={"role": "judge", "model": "spy-judge", "version": "replay"})
    stale = evaluate_closure(replace(ticket, target={**ticket.target,
                                                    "artifact_hash": artifact["hash"]}),
                             new_revision, [stale_verdict])
    assert stale.closed is False
    replay["closure_stale_hash"] = stale.as_dict()

    # ── 6) 真实夹具里的 S01／S02 证据定位（只修证据，0 次正文调用）
    fixture_summary = []
    fixtures = ROOT / "tests" / "fixtures" / "audit_feedback"
    for case in ("s01", "s02"):
        analysis = json.loads((fixtures / f"{case}_analysis.json").read_text("utf-8"))
        raw = (fixtures / f"{case}_current_text_window.md").read_text("utf-8")
        window = raw[:-1] if raw.endswith("\n") else raw
        window_artifact = {"artifact_id": f"lesson:{analysis['unit']}",
                           "hash": "sha256:" + hashlib.sha256(window.encode()).hexdigest(),
                           "body_markdown": window}
        responses = json.loads((fixtures / f"{case}_reviewer_responses.json")
                               .read_text("utf-8"))
        for response in responses:
            for raw_verdict in response["verdicts"]:
                quote = raw_verdict["quote"]
                row = CriterionVerdict(
                    ticket_id=f"ticket:{case}", criterion_id="C-EVIDENCE-1",
                    criteria_version=1, artifact_hash=window_artifact["hash"],
                    status="pass", why=raw_verdict["why"],
                    spans=(EvidenceSpan(artifact_id=window_artifact["artifact_id"],
                                        artifact_hash=window_artifact["hash"],
                                        json_pointer=POINTER, start=0, end=len(quote),
                                        quote=quote),),
                    verifier={"role": "judge"})
                result = locate_evidence(row, window_artifact)
                fixture_summary.append({"case": case, "no": raw_verdict["no"],
                                        "cache": response["_upstream_cache_sha256"][:8],
                                        "status": result.status,
                                        "span_count": len(result.spans),
                                        "matched_offsets": [list(o) for o in
                                                            result.matched_offsets],
                                        "reason": result.reason})
    replay["fixtures"] = fixture_summary
    assert any(item["status"] == "valid" and item["span_count"] == 2
               for item in fixture_summary)         # S02 #5：省略号拆成两个真实 span
    assert any(item["status"] == "invalid" and item["span_count"] >= 2
               for item in fixture_summary)         # S01 #1：定位到 2 段但整条不通过

    target = tmp_path / "replay-full-cycle.json"
    target.write_text(json.dumps(replay, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    assert target.is_file() and target.stat().st_size > 0
    assert json.loads(target.read_text("utf-8"))["closure"]["decision"]["closed"] is True


def test_conflicting_verdicts_cannot_close_one_criterion():
    ticket = make_ticket()
    quote = BODY[:12]
    verdict = CriterionVerdict(ticket_id=ticket.ticket_id, criterion_id='C-LOGIC-1',
        criteria_version=1, artifact_hash=HASH, status='pass', why='独立检查过这段',
        spans=(EvidenceSpan(artifact_id=ARTIFACT_ID, artifact_hash=HASH,
            json_pointer=POINTER, start=0, end=len(quote), quote=quote),), verifier={'role':'judge'})
    assert evaluate_closure(ticket, REVISION, [verdict]).closed
    result = evaluate_closure(ticket, REVISION, [verdict, replace(verdict, status='fail', why='同一标准尚未满足')])
    assert not result.closed
