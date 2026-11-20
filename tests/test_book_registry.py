"""「这是哪一本书」：书清单契约（规格 §0.5 **R1**，多教材的地基）。

## 为什么需要这一层

用户 2026-09-26：「你要注意，我们的之后可能会输入其他的教材」。
而当时 `linear-algebra-full` 在代码里写死 **36 处**（`doctor.py` 三个路径常量、
`make_delivery_package.py` 的 `SECTIONS`/`AUDIT_DIR`…）。

写死的后果不是"少打一个包"，而是**换一本教材时 doctor 会去查旧书、打包会打旧页**，
而且一句报错都没有 —— 这正是本项目"同一份信息写两处 / 存了一份会过期的副本"那一族。

## 这个文件守什么

1. **唯一来源**：路径只从 `data/books.json` 来，工具不再各写一份默认值；
2. **说清楚**：要的书不存在 → 报错并列出有哪些书（不许悄悄退回默认那本
   —— 那会把"跑新教材"变成"又跑了一遍旧教材"而不报错）；
3. **向后兼容**：不指定书时用「默认」那本，现有行为逐字节不变。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import book as B          # noqa: E402


def test_default_book_resolves_to_the_current_paths():
    """不带参数时，解析出来的就是现在这四个位置（现有命令行为不变）。"""
    ctx = B.resolve(None)
    assert ctx.book_id == B.default_id()
    assert ctx.parse == ROOT / "data/parse/linear-algebra-full"
    assert ctx.lessons == ROOT / "data/lesson/linear-algebra-full"
    assert ctx.audit == ROOT / "data/audit/linear-algebra-full"
    assert ctx.problems.suffix == ".json"


def test_registry_includes_the_default_book():
    assert B.FALLBACK_ID in B.list_books()
    payload = B.load_registry()
    assert payload["默认"] in payload["books"]


def test_a_second_book_is_resolved_from_the_registry_alone(tmp_path):
    """**R1 的正题**：加一本新教材只改清单，不用改任何工具代码。"""
    registry = tmp_path / "books.json"
    registry.write_text(json.dumps({
        "默认": "book-a",
        "books": {
            "book-a": {"title": "A 教材", "parse": "d/a/parse", "lessons": "d/a/lesson",
                       "audit": "d/a/audit"},
            "book-b": {"title": "B 教材", "parse": "d/b/parse", "lessons": "d/b/lesson",
                       "audit": "d/b/audit", "problems": "d/b/problems.json"},
        }}, ensure_ascii=False), encoding="utf-8")

    a = B.resolve(None, registry=registry, root=tmp_path)
    b = B.resolve("book-b", registry=registry, root=tmp_path)
    assert (a.book_id, a.title) == ("book-a", "A 教材")
    assert b.lessons == tmp_path / "d/b/lesson"
    assert b.problems == tmp_path / "d/b/problems.json"
    assert B.list_books(registry=registry) == ["book-a", "book-b"]
    # 没写 problems 的书给一个稳定的默认位置（不是 None —— 那样下游会崩在别处）
    assert a.problems == tmp_path / "data/problems/book-a.json"


def test_asking_for_a_book_that_does_not_exist_is_loud(tmp_path):
    """**不许静默退回默认那本** —— 否则"跑新教材"会变成"又跑了旧教材"且不报错。"""
    registry = tmp_path / "books.json"
    registry.write_text(json.dumps({"默认": "only", "books": {
        "only": {"parse": "p", "lessons": "l", "audit": "a"}}}), encoding="utf-8")
    with pytest.raises(B.BookError) as exc:
        B.resolve("nope", registry=registry, root=tmp_path)
    assert "nope" in str(exc.value) and "only" in str(exc.value)


def test_a_broken_registry_is_loud_too(tmp_path):
    """清单本身坏了 → 报错，不许静默当成"没有书"（那是"不报错的错"）。"""
    bad = tmp_path / "books.json"
    bad.write_text("{ 这不是 json", encoding="utf-8")
    with pytest.raises(B.BookError):
        B.load_registry(bad)
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"books": {}}), encoding="utf-8")
    with pytest.raises(B.BookError):
        B.load_registry(empty)


def test_missing_registry_file_falls_back_instead_of_crashing(tmp_path):
    """契约文件**不在**时退回当前这本（宁可"还是那本旧书"，也不要全部工具一起崩）。"""
    ctx = B.resolve(None, registry=tmp_path / "没有这个文件.json", root=tmp_path)
    assert ctx.book_id == B.FALLBACK_ID
    assert ctx.parse == tmp_path / "data/parse/linear-algebra-full"


def test_current_production_commands_require_an_explicit_book():
    from aiforstudy.cli import build_parser
    parser=build_parser()
    for command in ('plan','run'):
        args=parser.parse_args([command,'--book','book-b','--units','CH01-S01'])
        assert args.book=='book-b' and args.units=='CH01-S01'
        with pytest.raises(SystemExit) as exc:
            parser.parse_args([command,'--units','CH01-S01'])
        assert exc.value.code==2



def test_current_prepare_command_requires_an_explicit_book():
    from aiforstudy.cli import build_parser
    parser=build_parser()
    assert parser.parse_args(['prepare','--book','book-b']).book=='book-b'
    with pytest.raises(SystemExit) as exc:parser.parse_args(['prepare'])
    assert exc.value.code==2
