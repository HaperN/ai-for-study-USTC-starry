"""T10 工作台反馈：**事件看得见、失败卡答得上、点"继续"不重置额度**。

三条判据（对应计划 §T10 的第一组复选框）：

1. 工作台显示 `text_audit` 与媒体"规划／draft／检查／final"的事件；
2. 每张失败卡显示八件事：卡点／方法／标准／责任方／当前版本／前次失败／已耗轮次／停下原因；
3. "继续"**不能**自行重置额度（预算只允许提高，轮次与用量不动）。

前两条在**真实 Chromium 渲染出来的 DOM** 上验（`tests/browser/workbench_feedback.browser.cjs`），
不是静态 grep `app.js` 里有没有那几个字 —— "函数写了但没接线"正是这类需求最常见的坏法。
第三条分两层验：HTTP 语义（服务端拒绝降预算）+ 真的点一次"继续"，看服务端收到的
请求体里**没有**被表单旧值重新写进去的额度。

缺浏览器 → `pytest.fail("blocked: …")`，**不 skip**。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))
sys.path.insert(0, str(ROOT / "tests" / "support"))

from test_m8_ui import Client, make_workspace            # noqa: E402
from test_p3_control_workbench import saved_run          # noqa: E402
from aiforstudy.blocks.audit.contracts import (Criterion, EvidenceSpan, RepairTicket,  # noqa: E402
                                               make_ticket_id, validate_ticket)
from aiforstudy.core.jsonio import write_json_atomic     # noqa: E402
from aiforstudy.ui.feedback import CARD_FIELDS, CARD_LABELS  # noqa: E402
from aiforstudy.ui.server import ControlServer           # noqa: E402

EVIDENCE = None  # 每次测试使用隔离临时目录，不覆盖历史验收证据。


@pytest.fixture(autouse=True)
def isolated_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "EVIDENCE", tmp_path / "browser-evidence")
CHROME = ROOT / ".devtools" / "chrome" / "chrome-linux64" / "chrome"
PROBE = ROOT / "tests" / "browser" / "workbench_feedback.browser.cjs"
UNIT = "CH01-S01"


def require_browser() -> str:
    node = shutil.which("node")
    if not node:
        pytest.fail("blocked: 没有 Node，工作台界面验收无法执行（不许用静态检查替代）")
    if not CHROME.is_file():
        pytest.fail(f"blocked: 缺 Chromium（{CHROME}）")
    return node


# ────────────────────────────────────────── 装置：一个"有工单、有轮次、有媒体事件"的运行

RULE = "u_xx_sign_source"


def _ticket() -> dict:
    target = {"artifact_id": f"lesson:{UNIT}", "artifact_hash": "sha256:" + "a" * 64,
              "rule": RULE, "json_pointer": "/body/3", "anchor_kind": "pointer"}
    ticket = RepairTicket(
        # ID 是**确定性**的（rule + unit + artifact + 锚点）：重复发现同一个问题必须
        # 拿到同一个 ID，否则"换个 ID 重获次数"就结构上成立了（§4.7）。
        ticket_id=make_ticket_id(rule=RULE, unit_id=UNIT,
                                 artifact_id=target["artifact_id"], location="p:/body/3"),
        unit_id=UNIT,
        kind="text_content",
        severity="P1",
        owner="lesson",
        status="ready",
        target=target,
        problem="正文只写了「和为 0」，没有说明两方向二阶变化的符号来源，初学者会把它当成结论背诵。",
        learner_obstacle="读者无法把 u_xx=2 与 u_yy=-2 的来源对到曲面截线上。",
        source_evidence=(EvidenceSpan(artifact_id=f"original:{UNIT}",
                                      artifact_hash="sha256:" + "b" * 64,
                                      json_pointer="/body/3", start=0, end=11,
                                      quote="u_xx+u_yy=0"),),
        repair_instructions={"locate": "第 3 段", "action": "补一句：u_xx 来自固定 y 的截线开口方向",
                             "rationale": "符号来源不写清楚，读者只会记住结论",
                             "expected_change": "读者能指出两个二阶偏导各自的来历"},
        allowed_paths=(f"lesson:{UNIT}",),
        preserve=("u_xx+u_yy=0=0 这一结论",),
        criteria=(Criterion(criterion_id="c1", version=1, required=True,
                            expected="正文出现 u_xx 与 u_yy 的符号来源各一句",
                            method="reviewer"),),
        previous_attempt={"why": "上一轮只加了「和为 0」的解释，没有写两个二阶偏导的来历",
                          "evidence_rejected": "证据只重贴了结论，没有出处",
                          "round": 1},
        history=({"round": 1, "verdict": "fail", "why": "只改了结论那句"},),
    )
    return validate_ticket(ticket).as_dict()


def _prepare_run(tmp_path: Path, *, extra_state: dict | None = None):
    workspace = make_workspace(tmp_path)
    store, _engine = saved_run(workspace, chapters=["CH01"])
    products = store.work / "products" / "text_audit"
    products.mkdir(parents=True, exist_ok=True)
    write_json_atomic(products / f"{UNIT}.json",
                      {"payload": {"tickets": [_ticket()]}})
    # 媒体/审核事件：正文审核 + 媒体规划 + draft + 画面检查 + final
    store.emit("stage.started", stage="text_audit", scope_key=UNIT, message="正文前置门开始")
    store.emit("stage.finished", stage="text_audit", scope_key=UNIT,
               message="正文审核完成，1 条 P1 待返修")
    store.emit("stage.started", stage="media_plan", scope_key=UNIT, message="媒体规划：生成视觉请求")
    store.emit("stage.finished", stage="render", scope_key=UNIT,
               message="draft 出片完成", render_profile="draft")
    store.emit("stage.finished", stage="audit", scope_key=UNIT,
               message="画面检查：关键帧与公式对齐", check="frame")
    store.emit("stage.finished", stage="render", scope_key=UNIT,
               message="final 出片完成", render_profile="final")
    state = store.load_state()
    state.setdefault("nodes", {})["render:CH01"] = {
        "stage": "render", "scope_key": UNIT, "execution_state": "succeeded",
        "quality_state": "needs_review", "outputs": []}
    state["repair_states"] = {UNIT: {
        "unit_id": UNIT, "content_rounds_used": 1,
        "evidence_attempts_by_verdict": {"c1": 1},
        "active_batch_id": None, "artifact_heads": {f"lesson:{UNIT}": "sha256:" + "a" * 64},
        "last_candidate_signature": "", "policy_digest": "",
        "history": [{"round": 1, "verdict": "fail", "why": "只改了结论那句"}],
        "stopped_reason": "内容返修 2 批已用完：局部未决，其余继续"}}
    state.update(extra_state or {})
    store.save_state(state)
    return workspace, store


@pytest.fixture()
def server(tmp_path):
    workspace, store = _prepare_run(tmp_path)
    control = ControlServer(workspace, port=0)
    httpd = control.make_server()
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield control, Client(control), store, workspace
    finally:
        httpd.shutdown()
        httpd.server_close()


# ────────────────────────────────────────── 1/2：服务端口径

def test_failure_card_answers_all_eight_questions(server):
    _control, client, store, _workspace = server
    payload = client.get(f"/api/runs/{store.run_id}")
    cards = payload["failure_cards"]
    assert len(cards) == 1, cards
    card = cards[0]
    assert card["unit_id"] == UNIT and card["severity"] == "P1"
    assert card["complete"] is True, card["missing"]
    assert set(card["fields"]) == set(CARD_FIELDS)
    for name in CARD_FIELDS:
        value = card["fields"][name]
        assert str(value).strip(), (name, card["fields"])
        assert not str(value).startswith("（未"), (name, value)
    assert CARD_LABELS["blocker"] == "卡点" and CARD_LABELS["stop_reason"] == "停下原因"
    assert "内容返修 2 批已用完" in card["fields"]["stop_reason"]
    assert "内容返修 1 轮" in card["fields"]["attempts"]
    assert "上限 2" in card["fields"]["attempts"]
    assert "第一次派发" not in card["fields"]["previous_failure"]
    assert "上一轮只加了" in card["fields"]["previous_failure"]
    assert card["fields"]["owner"] == "lesson"
    assert "sha256:aaaa" in card["fields"]["current_version"]


def test_media_timeline_labels_planning_draft_check_final_and_text_audit(server):
    _control, client, store, _workspace = server
    rows = client.get(f"/api/runs/{store.run_id}")["media_timeline"]
    phases = {row["media_phase"] for row in rows if row["media_phase"]}
    assert phases == {"planning", "draft", "check", "final"}, phases
    audit = [row for row in rows if row["stage"] == "text_audit"]
    assert audit, [row["stage"] for row in rows]
    assert all("text_audit" in row["label"] for row in audit)
    labels = {row["media_phase"]: row["media_label"] for row in rows if row["media_phase"]}
    assert labels["planning"] == "媒体规划" and labels["draft"] == "媒体 draft"


# ────────────────────────────────────────── 3：额度

def test_resume_refuses_a_lower_budget_and_keeps_attempts(server):
    _control, client, store, _workspace = server
    before = store.load_state()
    with pytest.raises(urllib.error.HTTPError) as failure:
        client.post(f"/api/runs/{store.run_id}/resume", {"budget_tokens": 10})
    assert failure.value.code == 400
    after = store.load_state()
    assert after["repair_states"] == before["repair_states"], "被拒的续跑不许动轮次账"
    request = json.loads(store.request_path.read_text(encoding="utf-8"))
    assert "budget" not in request


def test_resume_uses_frozen_request_without_resetting_attempts(server, monkeypatch):
    _control, client, store, _workspace = server
    seen = {}

    def spy(request, run_id, workspace, *, cancel=None):
        seen["request"] = request
        seen["run_id"] = run_id

    monkeypatch.setattr("aiforstudy.pipeline.engine.resume_request", spy)
    answer = client.post(f"/api/runs/{store.run_id}/resume",
                         {})
    assert answer["resumed"] is True
    for _ in range(200):
        if "request" in seen:
            break
        import time
        time.sleep(0.02)
    assert "budget" not in seen["request"].as_dict()
    state = store.load_state()
    assert state["repair_states"][UNIT]["content_rounds_used"] == 1, "续跑重置了轮次账"


# ────────────────────────────────────────── 1/2/3：真实浏览器里的 DOM

def _run_probe(node: str, config: dict, *, name: str) -> dict:
    config_path = EVIDENCE / f"workbench-{name}.config.json"
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    result = subprocess.run([node, str(PROBE), str(config_path)], cwd=ROOT,
                            text=True, capture_output=True, timeout=240)
    (EVIDENCE / f"workbench-{name}.log").write_text(
        f"$ node tests/browser/workbench_feedback.browser.cjs {config_path}\n"
        f"exit={result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}\n",
        encoding="utf-8")
    report_path = Path(config["out"]) / "workbench.json"
    assert report_path.is_file(), result.stdout + result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["_exit_code"] = result.returncode
    (EVIDENCE / f"workbench-{name}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def test_real_browser_dom_shows_cards_and_media_timeline(server):
    node = require_browser()
    _control, client, store, _workspace = server
    report = _run_probe(node, {"chrome": str(CHROME), "base": client.base,
                               "out": str(EVIDENCE / "workbench-dom"),
                               "run_id": store.run_id, "click_resume": False}, name="dom")
    assert report["_exit_code"] == 0, report
    assert not report["errors"], report["errors"]
    before = report["before"]
    assert len(before["cards"]) == 1, before["card_container_text"]
    card = before["cards"][0]
    assert card["unit"] == UNIT
    # 八个字段的**中文标签**都在卡上，且每一格都有实际内容
    for name, label in CARD_LABELS.items():
        assert label in card["labels"], (name, label, card["labels"])
        assert str(card["fields"].get(name) or "").strip(), (name, card["fields"])
    assert "内容返修" in card["fields"]["attempts"]
    assert "已用完" in card["fields"]["stop_reason"]
    assert "lesson" in card["fields"]["owner"]
    # 时间线：媒体四段 + text_audit 都在页面上
    phases = {group["phase"] for group in before["groups"]}
    assert {"planning", "draft", "check", "final", "text_audit"} <= phases, phases
    titles = {group["phase"]: group["title"] for group in before["groups"]}
    assert "text_audit" in titles["text_audit"]
    assert "媒体规划" in titles["planning"]
    # "继续"按钮的说明写清了额度口径
    assert "不会重置" in before["resume_hint"] and "每个对象" in before["resume_hint"]


def test_real_browser_resume_click_does_not_rewrite_the_quota(server, monkeypatch):
    node = require_browser()
    _control, client, store, _workspace = server
    seen = {}

    def spy(request, run_id, workspace, *, cancel=None):
        seen["request"] = request.as_dict()
        seen["run_id"] = run_id

    monkeypatch.setattr("aiforstudy.pipeline.engine.resume_request", spy)
    original = json.loads(store.request_path.read_text(encoding="utf-8"))
    report = _run_probe(node, {"chrome": str(CHROME), "base": client.base,
                               "out": str(EVIDENCE / "workbench-resume"),
                               "run_id": store.run_id, "click_resume": True},
                        name="resume")
    assert report["_exit_code"] == 0, report
    for _ in range(200):
        if "request" in seen:
            break
        import time
        time.sleep(0.02)
    assert "request" in seen, ("点了继续但服务端没收到续跑请求", report["before"]["resume_disabled"])
    # 表单里是旧值 → 界面**不发**预算字段 → 服务端沿用原上限（既没降也没被"重置"）
    assert seen["request"] == original
    assert "budget" not in seen["request"]
    state = store.load_state()
    assert state["repair_states"][UNIT]["content_rounds_used"] == 1, "点继续把轮次账清了"
    assert report["after_resume"] is not None


def test_missing_browser_is_blocked_not_a_static_pass(monkeypatch):
    """缺 Chromium：`pytest.fail`（blocked），不是 skip、不是"静态检查代替"。"""
    monkeypatch.setattr(sys.modules[__name__], "CHROME", Path("/nonexistent/chrome"))
    with pytest.raises(pytest.fail.Exception) as failure:
        require_browser()
    assert "blocked" in str(failure.value)
