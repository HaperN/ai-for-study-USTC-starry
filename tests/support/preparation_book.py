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

import shutil

ROOT = Path(__file__).resolve().parents[2]


REAL_CONFIG = ROOT / "config"


def _copy_config(root: Path) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    for name in ("capabilities.json", "execution.json", "models.json"):
        shutil.copy(REAL_CONFIG / name, root / "config" / name)


def _write_registry(root: Path, books: dict) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "books.json").write_text(
        json.dumps({"_meta": {"file_type": "book_registry", "schema_version": "1.0"},
                    "workspace": ".", "books": books}, ensure_ascii=False), encoding="utf-8")


def _request(book_id: str, *, profile: Profile = Profile.RELEASE,
             goal: Goal = Goal.DELIVER, kind: SelectorKind = SelectorKind.ALL,
             values=()) -> RunRequest:
    return RunRequest(book_id=book_id, selectors=Selectors(kind=kind, values=tuple(values)),
                      goal=goal, profile=profile).validate()


def _tree(root: Path) -> dict[str, str]:
    """目录的内容凭据：相对路径 → 内容 sha256（只比内容，不比 mtime）。"""
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


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _mini_book(tmp_path: Path, *, with_mineru: bool = True) -> Workspace:
    """一本**形状合法**的迷你书：确定性前缀能整段跑通（含冻结上下文）。

    为什么还要自造一本：真实书的概念索引没冻结（见模块 docstring），所以"索引完整时
    上下文快照确实被冻结"这条路径在真实书上走不到 —— 而它正是 §6/§7 要求的下一步输入。
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
                          "title": "第一章 甲",
                          # ⚠️ 单元必须带 `original_text`：教案阶段的壳只会调已跑通的实现，
                          #    不会替单元"编"一份原文；缺了它，`lesson` 只能如实 blocked。
                          #    形状合法的迷你书本来就不该缺这一格（同类夹具在
                          #    `tests/integration/test_m10_lesson_audit_shells.py` 里另有完整一份）。
                          "original_text": "original_text/CH01.md"},
                         {"unit_id": "CH01-S01", "level": "section", "chapter": "CH01",
                          "title": "甲之一节",
                          "original_text": "original_text/CH01-S01.md"}]})
    _write_json(parse / "concepts.json", {
        "_meta": {"file_type": "concepts", "schema_version": "1.0"},
        "concepts": [{"id": "C-0001", "name": "甲定义", "type": "definition", "formal": "甲",
                      "occurrences": [{"unit_id": "CH01-S01", "anchor": "1.1"}]}]})
    _write_json(parse / "concepts.parts" / "CH01.json", {
        "_meta": {"file_type": "concepts_part", "schema_version": "1.0",
                  "text_sha256": "sha256:" + "1" * 64},
        "unit_id": "CH01", "concepts": [], "coverage": {"完整": True}})
    _write_json(parse / "concepts.parts" / "CH01-S01.json", {
        "_meta": {"file_type": "concepts_part", "schema_version": "1.0",
                  "text_sha256": "sha256:" + "2" * 64},
        "unit_id": "CH01-S01",
        "concepts": [{"name": "甲定义", "type": "definition", "formal": "甲",
                      "anchor": "1.1", "role": "introduced"}],
        "coverage": {"完整": True}})
    (parse / "original_text").mkdir(parents=True, exist_ok=True)
    (parse / "original_text" / "CH01.md").write_text("第一章 甲", encoding="utf-8")
    (parse / "original_text" / "CH01-S01.md").write_text("甲之一节", encoding="utf-8")
    for unit in ('CH01', 'CH01-S01'):
        part_path = parse / 'concepts.parts' / f'{unit}.json'
        part = json.loads(part_path.read_text())
        part['_meta']['text_sha256'] = hashlib.sha256(
            (parse / 'original_text' / f'{unit}.md').read_bytes()).hexdigest()
        _write_json(part_path, part)
    (parse / "document.md").write_text("第一章 甲\n\n甲之一节\n", encoding="utf-8")
    if with_mineru:
        (parse / "mineru").mkdir(parents=True, exist_ok=True)
        (parse / "mineru" / "layout.json").write_text('{"pdf_info": []}', encoding="utf-8")
        layout_sha = hashlib.sha256((parse / "mineru" / "layout.json").read_bytes()).hexdigest()
        _write_json(parse / "ingest.json", {
            "_meta": {"file_type": "ingest", "schema_version": "1.0"},
            "route": "mineru", "via": "local-products",
            "mineru": {"dir": "mineru", "layout": "layout.json",
                       "layout_sha256": layout_sha}})
    # 复核产物**与输入指纹一致**：于是"按指纹应用"这条路径真的被走到（而不是永远过期）
    _write_json(parse / "toc.review.json", {
        "inputs_digest": expected_inputs_digest(parse), "decisions": []})
    _write_registry(root, {"mini-book": {
        "title": "迷你教材", "revision": "parse-v2",
        "parse": "book/parse", "lessons": "book/lesson", "audit": "book/audit",
        "problems": "book/problems.json",
        "artifacts_root": "book/artifacts", "runs_root": "book/runs",
        "releases_root": "book/releases"}})
    from aiforstudy.core.source_materials import write_source_materials
    write_source_materials(parse)
    return Workspace(root=root)
