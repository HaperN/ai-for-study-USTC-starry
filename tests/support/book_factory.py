"""Pure offline book/transport data shared by production and retirement tests.

This module does not register, resume or execute a historical graph.
ScriptedTransport is a deterministic transport base, never a quality certificate.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from aiforstudy.contracts.enums import Goal, Profile, SelectorKind
from aiforstudy.contracts.request import RunRequest, Selectors
from aiforstudy.core.workspace import Workspace

ROOT = Path(__file__).resolve().parents[2]


REAL_CONFIG = ROOT / "config"


BOOK = "mini-book"


UNIT = "CH01-S01"


FIX_OLD = "甲句。"


FIX_NEW = "甲句（已补条件）。"


LESSON_BODY = (
    "甲句。乙句。本节把甲与乙各讲一遍：甲说的是第一种情形，乙说的是第二种情形，"
    "两种情形的条件不一样。先看甲。按照[[CONCEPT:C-0001|甲定义]]，甲只有在条件 A 成立时"
    "才给出结论，所以写甲的时候不能把条件 A 省掉，否则读者会以为它总是成立。"
    "再看乙。乙的条件是 B，与 A 不同；两者的适用范围不能互相套用，混在一起就会把只在 A 下"
    "成立的结论搬到 B 上，而 B 并不保证那一步。小结：甲与乙各自成立的条件不同，"
    "用之前先把条件核对一遍，再决定用哪一条。若顺序反过来先讲乙，结论不变，"
    "但条件的对照会更清楚。")


LESSON_PAYLOAD = {
    "body_markdown": LESSON_BODY,
    "annotations": [], "explainables": [], "diagrams": [], "examples": [],
    "figure_requests": [],
    # 声明本节概念（含 explained_where）：旧实现据此保留"这个术语在哪里解释过"，
    # 少了它校验器会报"引用了但没声明"的提醒。
    "concepts": [{"id": "C-0001", "name": "甲定义", "type": "definition",
                  "role": "introduced", "explained_where": "body"}],
    "figures": [], "corrections": [], "notation": [], "one_line": "甲与乙各是一种情形。",
}


P0_FINDING = {"code": "CONDITION_MISSING", "severity": "P0", "quote": FIX_OLD,
              "problem": "甲句把有条件的结论说成了总是成立。",
              "required_condition": "在甲句里补上成立条件。",
              "evidence": "甲句。乙句。",
              "learner_obstacle": "读者会误以为条件 A 可以省略。",
              "repair_instructions": {"locate": FIX_OLD, "action": "补充条件 A", "rationale": "明确适用范围", "expected_change": "结论带条件说明"},
              "criteria": [{"criterion_id": "condition-a", "version": 1, "required": True,
                            "expected": "甲句明确标注成立条件，不宣称无条件成立",
                            "method": "reviewer", "evidence_type": "text"}]}


def _copy_config(root: Path) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    for name in ("capabilities.json", "execution.json", "models.json"):
        (root / "config" / name).write_text((REAL_CONFIG / name).read_text(encoding="utf-8"),
                                            encoding="utf-8")


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_registry(root: Path, books: dict) -> None:
    _write_json(root / "config" / "books.json",
                {"_meta": {"file_type": "book_registry", "schema_version": "1.0"},
                 "workspace": ".", "books": books})


def _tree(root: Path) -> dict[str, str]:
    """目录内容凭据：相对路径 → 内容 sha256（只比内容，不比 mtime）。"""
    out: dict[str, str] = {}
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1 << 20), b""):
                digest.update(block)
        out[str(path.relative_to(root))] = digest.hexdigest()
    return out


def _mini_book(tmp_path: Path) -> Workspace:
    """一本形状合法的迷你书：确定性前缀能整段跑通，`lesson` 能真的生成一节教案。

    与 `test_m10_release_prefix._mini_book` 的关键差别：这里的 `lesson_units` **带
    `original_text`**，所以教案壳不会在"缺原文"上先阻断，而是真的走到模型访问那一步
    （那正是本模块要测的地方）。
    """
    from aiforstudy.blocks.parse.review import expected_inputs_digest

    root = tmp_path / "ws"
    _copy_config(root)
    parse = root / "book" / "parse"
    _write_json(parse / "toc.v2.json", {
        "_meta": {"file_type": "toc", "schema_version": "2.0", "pdf_sha256": "pdf-hash",
                  "document_sha256": "doc-hash", "sources_used": ["printed-toc"]},
        "book_title": "迷你教材",
        "nodes": [{"toc_id": "T-0001", "level": 1, "number": "第1章", "title": "第一章 甲",
                   "parent": None, "kind": "chapter", "confirmed": True},
                  {"toc_id": "T-0002", "level": 2, "number": "1.1", "title": "甲之一节",
                   "parent": "T-0001", "kind": "section", "confirmed": True}],
        "disagreements": [], "unresolved": [], "suspicious": [], "revisions": []})
    _write_json(parse / "body.json", {
        "_meta": {"file_type": "body", "schema_version": "2.0"},
        "units": [{"unit_id": "U-0000000001", "body_node": "CH01", "declared": True,
                   "level": "chapter", "anchor": "1", "title": "第一章 甲", "toc_node": "T-0001",
                   "content_sha256": "c-ch01", "source_lines": [1, 2], "confirmed": True},
                  {"unit_id": "U-0000000002", "body_node": "CH01-S01", "declared": True,
                   "level": "section", "anchor": "1.1", "title": "甲之一节",
                   "toc_node": "T-0002", "content_sha256": "c-ch01s01",
                   "source_lines": [3, 10], "confirmed": True}],
        "empty_nodes": [], "undeclared": [], "orphans": [], "unconfirmed": []})
    _write_json(parse / "chapters.json", {
        "_meta": {"file_type": "chapters", "schema_version": "2.0"},
        "book": {"title": "迷你教材"},
        "chapters": [{"id": "CH01", "title": "第一章 甲"}],
        "lesson_units": [{"unit_id": "CH01", "level": "chapter_intro", "chapter": "CH01",
                          "title": "第一章 甲", "original_text": "original_text/CH01.md"},
                         {"unit_id": UNIT, "level": "section", "chapter": "CH01",
                          "title": "甲之一节", "original_text": f"original_text/{UNIT}.md"}]})
    _write_json(parse / "concepts.json", {
        "_meta": {"file_type": "concepts", "schema_version": "1.0"},
        "concepts": [{"id": "C-0001", "name": "甲定义", "type": "definition", "formal": "甲",
                      "brief": "甲是什么", "occurrences": [{"unit_id": UNIT, "anchor": "1.1"}]}]})
    _write_json(parse / "concepts.parts" / "CH01.json", {
        "_meta": {"file_type": "concepts_part", "schema_version": "1.0",
                  "text_sha256": "sha256:" + "1" * 64},
        "unit_id": "CH01", "concepts": [], "coverage": {"完整": True}})
    _write_json(parse / "concepts.parts" / f"{UNIT}.json", {
        "_meta": {"file_type": "concepts_part", "schema_version": "1.0",
                  "text_sha256": "sha256:" + "2" * 64},
        "unit_id": UNIT,
        "concepts": [{"name": "甲定义", "type": "definition", "formal": "甲",
                      "anchor": "1.1", "role": "introduced"}],
        "coverage": {"完整": True}})
    (parse / "original_text").mkdir(parents=True, exist_ok=True)
    (parse / "original_text" / "CH01.md").write_text("第一章 甲\n", encoding="utf-8")
    (parse / "original_text" / f"{UNIT}.md").write_text(
        "甲句。乙句。本节把甲与乙各讲一遍：甲说的是第一种情形，乙说的是第二种情形。\n",
        encoding="utf-8")
    (parse / "document.md").write_text("第一章 甲\n\n甲之一节\n", encoding="utf-8")
    (parse / "mineru").mkdir(parents=True, exist_ok=True)
    (parse / "mineru" / "layout.json").write_text('{"pdf_info": []}', encoding="utf-8")
    layout_sha = hashlib.sha256((parse / "mineru" / "layout.json").read_bytes()).hexdigest()
    _write_json(parse / "ingest.json", {
        "_meta": {"file_type": "ingest", "schema_version": "1.0"},
        "route": "mineru", "via": "local-products",
        "mineru": {"dir": "mineru", "layout": "layout.json", "layout_sha256": layout_sha}})
    _write_json(parse / "toc.review.json", {
        "inputs_digest": expected_inputs_digest(parse), "decisions": []})
    _write_registry(root, {BOOK: {
        "title": "迷你教材", "revision": "parse-v2",
        "parse": "book/parse", "lessons": "book/lesson", "audit": "book/audit",
        "problems": "book/problems.json",
        "artifacts_root": "book/artifacts", "runs_root": "book/runs",
        "releases_root": "book/releases"}})
    from aiforstudy.core.source_materials import write_source_materials
    write_source_materials(parse)
    return Workspace(root=root)


def _request(*, profile: Profile = Profile.RELEASE) -> RunRequest:
    return RunRequest(book_id=BOOK,
                      selectors=Selectors(kind=SelectorKind.UNITS, values=(UNIT,)),
                      goal=Goal.DELIVER, profile=profile, parallelism=1).validate()


class ScriptedTransport:
    """假 transport：按**请求内容**作答，并把每一次真实调用记下来。

    为什么按内容而不是按调用顺序：`discover(runs=2)` 在旧实现里是**并行**发的
    （线程池），顺序不确定；按顺序脚本会让测试变成"偶尔挂"。
    """

    def __init__(self, *, closure_status: str = "closed") -> None:
        self.calls: list[dict] = []
        self.closure_status = closure_status

    # 假 transport 的返回值：每次真实调用都算 18 token（账本对账要用它算总数）
    TOKENS = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}

    def __call__(self, *, model: str, effort: str, messages: list[dict],
                 images, max_tokens: int, timeout: float):
        self.calls.append({"model": model, "effort": effort, "images": list(images or []),
                           "max_tokens": max_tokens,
                           "system": _system_of(messages),
                           "user": _user_of(messages)})
        text = self._answer(_system_of(messages), _user_of(messages))
        return text, dict(self.TOKENS)

    # ── 按系统提示词认路（四类请求各有各的固定系统提示词）
    def _answer(self, system: str, user: str) -> str:
        if "只做两个判断" in system:                       # CLOSURE_SYSTEM
            return json.dumps(self._closure(user), ensure_ascii=False)
        if "只输出替换清单" in system:                     # REPLACEMENT_SYSTEM
            return json.dumps({"replacements": [{"issue": 1, "old": FIX_OLD, "new": FIX_NEW}],
                               "notes": "补上成立条件"}, ensure_ascii=False)
        if "独立审核者" in system:                         # 发现阶段
            if self.closure_status == "closed" and FIX_NEW in user:
                return json.dumps({"findings": [], "no_finding_reason": "当前正文已补条件"})
            return json.dumps({"findings": [dict(P0_FINDING)], "no_finding_reason": ""},
                              ensure_ascii=False)
        if "本节：" in user:                                # 教案生成
            return json.dumps(LESSON_PAYLOAD, ensure_ascii=False)
        raise AssertionError(f"替身没准备这种请求：system={system[:60]!r}")

    def _closure(self, user: str) -> dict:
        """给每条工单一个结论。`no` 就是工单在提示词里的序号。"""
        count = sum(1 for line in user.splitlines() if line.strip()[:1].isdigit()
                    and "】" in line)
        verdicts = [{"no": index, "status": self.closure_status,
                     "quote": FIX_NEW if self.closure_status == "closed" else FIX_OLD,
                     "why": "核过了"}
                    for index in range(1, count + 1)]
        return {"verdicts": verdicts, "new_p0": []}


def _system_of(messages: list[dict]) -> str:
    return next((str(m.get("content") or "") for m in messages
                 if m.get("role") == "system"), "")


def _user_of(messages: list[dict]) -> str:
    return "\n".join(str(m.get("content") or "") for m in messages
                     if m.get("role") == "user")
