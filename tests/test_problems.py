"""问题汇总（`blocks/problems/`）的回归测试 —— 缺口 B 的机器判据。

这一块挡的是**声明过期**：交付包里给负责人看的数字以前是手写的，内容一重跑就对不上，
而且没人会记得改那句话。规矩是"包里的每个数字都必须能从产物里算出来"，
所以这里要锁住三件事：
  ① 汇总能跑（0 token，只读产物）；
  ② 声明**由产物生成**（改产物 → 声明跟着变）；
  ③ `verify` 能抓出"声明与产物对不上"。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "blocks" / "problems"))
sys.path.insert(0, str(ROOT / "lib"))

import collect as C  # noqa: E402


def _fake_book(tmp_path: Path, *, tokens=123456, models=2) -> Path:
    """一份最小产物：1 章 1 节 + 1 份逐章记录 + 1 份模型产物。"""
    book = tmp_path / "lesson"
    (book / "lessons").mkdir(parents=True)
    (book / "models").mkdir()
    (book / "chapter-runs").mkdir()
    (book / "lessons/CH01-S01.json").write_text(json.dumps({
        "_meta": {"file_type": "lesson"}, "unit_id": "CH01-S01", "title": "线性运算",
        "chapter": "CH01",
        "body_markdown": "正文 [[CONCEPT:C-0001]]。",
        "explainables": [{"id": "F-01", "name": "公式"}],
        "examples": [{"label": "例1", "steps": [
            {"no": 1, "markdown": "第一步。", "basis": "依据定义。"},
            {"no": 2, "markdown": "第二步。"}]}],
        "checks": {"problems": [], "warnings": [],
                   "stats": {"正文视觉长度": 900, "原文字数": 400, "正文倍数": 2.25,
                             "正文地板": 250, "套话": 0, "详解引用": 1}},
    }, ensure_ascii=False), encoding="utf-8")
    (book / "models/CH01-S01.json").write_text(json.dumps({
        "_meta": {"file_type": "models"},
        "models": [{"id": f"CH01-S01.M-0{i}", "unit_id": "CH01-S01"} for i in range(1, models + 1)],
    }, ensure_ascii=False), encoding="utf-8")
    (book / "chapter-runs/CH01.json").write_text(json.dumps({
        "_meta": {"file_type": "chapter_run"}, "chapter": "CH01", "总花费": tokens,
        "节": [{"unit_id": "CH01-S01", "steps": {
            "教案": {"code": 0, "tokens": tokens // 2},
            "回路": {"code": 0, "tokens": tokens // 2, "结论": "通过"}}}],
    }, ensure_ascii=False), encoding="utf-8")
    return book


def test_collect_reads_every_block_without_spending_tokens(tmp_path):
    book = _fake_book(tmp_path)
    payload = C.collect(lessons_dir=book)
    assert payload["problems"] == [] and payload["dropped"] == []
    assert payload["metrics"]["节数"] == 1
    assert payload["metrics"]["模型"] == 2, "模型数要从 models/ 产物数，不是教案 JSON"
    assert payload["metrics"]["token"]["逐章"] == {"CH01": 123456}


def test_claims_are_generated_from_products_not_written_by_hand(tmp_path):
    """改产物 → 声明跟着变。这一条就是"不许手写数字"的判据。"""
    book = _fake_book(tmp_path, models=3)
    first = C.claims(C.collect(lessons_dir=book))
    (book / "models/CH01-S01.json").write_text(json.dumps(
        {"models": [{"id": f"M-{i}", "unit_id": "CH01-S01"} for i in range(5)]},
        ensure_ascii=False), encoding="utf-8")
    second = C.claims(C.collect(lessons_dir=book))
    assert first != second
    assert any("5 个" in row["说"] for row in second)
    assert all(row.get("evidence") for row in second), "每条声明都要带证据路径"


def test_verify_catches_a_stale_claim(tmp_path):
    book = _fake_book(tmp_path)
    payload = C.collect(lessons_dir=book)
    assert C.verify(payload, lessons_dir=book) == []
    # 手改声明（模拟"手写数字过期了"）→ 复核必须报出来
    payload["claims"][0]["说"] = "共 99 节；套话 0 处"
    assert C.verify(payload, lessons_dir=book), "改过的声明不该通过复核"


def test_a_ticket_judged_by_a_retired_rule_is_not_a_problem(tmp_path):
    """旧规范判出来的工单会一直挂在产物里 —— 读的时候按现规范读，别冒充问题。"""
    book = _fake_book(tmp_path)
    audit = tmp_path / "audit"
    audit.mkdir()
    (audit / "CH01-S01.tickets.json").write_text(json.dumps({
        "_meta": {"unit_id": "CH01-S01"},
        "tickets": [
            {"id": "T-1", "severity": "P1", "status": "open",
             "quote": "正文是原文的 3.87 倍，超过上限 3.5（注水了，删掉重复和铺陈）",
             "requirement": "删掉重复"},
            {"id": "T-2", "severity": "P0", "status": "open",
             "quote": "这里把主元说错了", "requirement": "改对"},
        ]}, ensure_ascii=False), encoding="utf-8")
    payload = C.collect(lessons_dir=book, audit_dir=audit)
    assert [row["ticket"] for row in payload["problems"]] == ["T-2"]
    retired = [n for n in payload["notes"] if n.get("ticket") == "T-1"]
    assert retired and retired[0]["status"] == "retired"
    assert "已失效" in retired[0]["message"]


def test_frozen_sections_are_listed_with_reason_and_tokens(tmp_path):
    """决策 4：无进展/冻结的节**不进交付包**，但内部包要能查到原因和烧掉的 token。"""
    book = _fake_book(tmp_path, tokens=200000)
    run = json.loads((book / "chapter-runs/CH01.json").read_text(encoding="utf-8"))
    run["节"][0]["steps"]["回路"]["结论"] = "冻结（连续两轮工单没有净减少）"
    (book / "chapter-runs/CH01.json").write_text(json.dumps(run, ensure_ascii=False),
                                                 encoding="utf-8")
    payload = C.collect(lessons_dir=book)
    assert len(payload["dropped"]) == 1
    row = payload["dropped"][0]
    assert row["unit"] == "CH01-S01" and row["token"] == 200000
    assert "冻结" in row["原因"] and row["证据"]


def test_a_stale_freeze_does_not_drop_a_section_that_now_passes(tmp_path):
    """⚠️ 实测踩过：逐章跑批记录里的「冻结」是**那一次**的结论，重跑修好之后它不会更新。

    结果已经通过的节仍被当成冻结、被踢出交付包（声明里就变成「本包 6 节，另有 2 节没进包」）。
    判据必须看**当前**：以这一节自己的 `audit/<unit>.loop.json` 为准，且教案版本要对得上。
    """
    book = _fake_book(tmp_path)
    audit = tmp_path / "audit"
    audit.mkdir()
    run = json.loads((book / "chapter-runs/CH01.json").read_text(encoding="utf-8"))
    run["节"][0]["steps"]["回路"]["结论"] = "冻结（跑满轮数上限，剩余工单进 backlog）"
    (book / "chapter-runs/CH01.json").write_text(json.dumps(run, ensure_ascii=False),
                                                 encoding="utf-8")
    lesson = json.loads((book / "lessons/CH01-S01.json").read_text(encoding="utf-8"))
    lesson["revision"] = 9
    (book / "lessons/CH01-S01.json").write_text(json.dumps(lesson, ensure_ascii=False),
                                                encoding="utf-8")

    def loop_file(conclusion, revision, tickets=()):
        audit.joinpath("CH01-S01.loop.json").write_text(json.dumps({
            "_meta": {"file_type": "audit_rewrite_loop", "unit_id": "CH01-S01"},
            "结论": conclusion, "lesson_revision": revision, "工单": list(tickets),
        }, ensure_ascii=False), encoding="utf-8")

    # 逐章记录说冻结、这一节的回路记录说通过 → **不该踢**
    loop_file("通过（工单全部关闭，且没有新的 P0）", 9)
    assert C.collect(lessons_dir=book, audit_dir=audit)["dropped"] == []

    # 回路记录说冻结、版本对得上，**而且还有没关的工单** → 这才该踢出去
    # 冻结、版本对得上、**而且这份回路记录里还有没关的工单** → 才算真的该剔
    loop_file("冻结（连续两轮工单没有净减少）", 9,
              tickets=[{"id": "T-1", "severity": "P1", "status": "open",
                        "quote": "还有一处没改", "requirement": "改掉"}])
    assert [row["unit"] for row in C.collect(lessons_dir=book, audit_dir=audit)["dropped"]] \
        == ["CH01-S01"]

    # 冻结、但工单都关了、按当前规则也没有硬问题 → **不踢**
    # （那个"冻结"是按旧规则判的：实测两节冻在"what 太长"，而那条规则量的是 LaTeX 源码长度）
    loop_file("冻结（连续两轮工单没有净减少）", 9, tickets=[])
    assert C.collect(lessons_dir=book, audit_dir=audit)["dropped"] == []

    # 教案在回路之后又改过（版本对不上）→ 结论过期，不踢
    lesson["revision"] = 10
    (book / "lessons/CH01-S01.json").write_text(json.dumps(lesson, ensure_ascii=False),
                                                encoding="utf-8")
    assert C.collect(lessons_dir=book, audit_dir=audit)["dropped"] == []


def test_every_retired_rule_still_matches_its_own_sample():
    """作废规则表靠**字符串前缀**匹配，所以要盯住"前缀还认得出当年的工单"。

    上游消息措辞一改（比如以后写成「超过上限 3.5 倍」），前缀就失配 →
    真正作废的条目会**重新变成硬问题**，凭一条早已取消的规则挡住交付。
    这条测试让措辞漂移立刻可见（每条都带一条逐字来自历史产物的样例）。
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from blocks.problems import collect as C

    for row in C.RETIRED_RULES:
        assert row["样例"], f"{row['前缀']} 没有样例，这条护栏就形同虚设"
        assert C.retired_reason(row["样例"]), (
            f"作废规则「{row['前缀']}」已经认不出它自己的样例了：{row['样例']}")
        assert row["作废于"], "每条作废规则都要写作废日期（否则没人敢删）"
    # 反向：正常的问题不该被误判成"已作废"
    assert not C.retired_reason("CH06-S03-SS03: 记号「行列式」第一次出现时没有最小定义")


def test_collect_schema_checks_are_memoized_without_historical_quality_inheritance(tmp_path,monkeypatch):
    from support.content_case import teaching_case
    from aiforstudy.blocks.content import content_contracts
    from blocks.problems import collect as C
    lesson=teaching_case()[0]
    original=content_contracts.parse_content;calls=[]
    def counted(value):
        calls.append(value)
        return original(value)
    monkeypatch.setattr(content_contracts,'parse_content',counted)
    memo={}
    for _ in range(3):
        report=C._fresh_checks(tmp_path,lesson,memo)
        assert report['quality_verified'] is False and 'schema-only' in report['note']
    assert len(calls)==1
    for _ in range(2):C._fresh_checks(tmp_path,lesson)
    assert len(calls)==3
    old=C._fresh_checks(tmp_path,{'unit_id':'OLD','checks':{'problems':['historical issue']}})
    assert old['quality_verified'] is False and old['problems']==['historical issue']
