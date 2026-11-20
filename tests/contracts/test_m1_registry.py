"""M1 书籍注册与上下文隔离回归。

守三件在方案里写死、但在代码里很容易悄悄退化的事：

1. **没有默认书**（§4）。留一个默认值就是留一条"跑错书但不报错"的路；
2. **注册表缺失/损坏/书不存在都明确失败**（§3）。不许静默退回旧表；
3. **同进程两本书不串状态**（M1 通过条件）。这是本项目"全局状态"那一族错误的形态：
   路径常量在 import 时固化，于是"这次查哪本书"取决于"哪次 import 先发生"。

对应验收项：A02（默认登记 A、规划 B、再切回 A，读写全部归目标书）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aiforstudy.core.registry import BookRegistry, load_registry
from aiforstudy.core.workspace import ENV_WORKSPACE, Workspace, find_workspace, open_workspace
from aiforstudy.errors import ConfigError


def _workspace(tmp_path: Path, books: dict, *, workspace_key: str = ".",
               raw: str | None = None) -> Workspace:
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    payload = raw if raw is not None else {"_meta": {}, "workspace": workspace_key,
                                           "books": books}
    (tmp_path / "config" / "books.json").write_text(
        payload if raw is not None else json.dumps(payload, ensure_ascii=False),
        encoding="utf-8")
    return Workspace(root=tmp_path)


BOOK_A = {"title": "A 教材", "parse": "data/parse/a", "lessons": "data/lesson/a",
          "audit": "data/audit/a"}
BOOK_B = {"title": "B 教材", "parse": "data/parse/b", "lessons": "data/lesson/b",
          "audit": "data/audit/b", "problems": "data/problems/b.json", "revision": "p2"}


# ────────────────────────────────────────── 没有默认书

def test_there_is_no_default_book(tmp_path):
    """方案 §4 的正题：省略 --book **不退回**历史默认书。"""
    registry = load_registry(_workspace(tmp_path, {"a": BOOK_A, "b": BOOK_B}))
    with pytest.raises(ConfigError, match="必须显式指定 --book"):
        registry.require(None)
    with pytest.raises(ConfigError, match="必须显式指定 --book"):
        registry.require("")


def test_unknown_book_lists_the_alternatives(tmp_path):
    registry = load_registry(_workspace(tmp_path, {"a": BOOK_A, "b": BOOK_B}))
    with pytest.raises(ConfigError) as exc:
        registry.require("nope")
    assert "nope" in str(exc.value) and "a" in str(exc.value) and "b" in str(exc.value)


def test_real_registry_has_no_default_key():
    """真实表里也不许有「默认」——留个字段就会有人去读它。"""
    root = Path(__file__).resolve().parents[2]
    payload = json.loads((root / "config" / "books.json").read_text(encoding="utf-8"))
    assert "默认" not in payload
    assert "default" not in payload


# ────────────────────────────────────────── 明确失败

def test_missing_registry_is_loud(tmp_path):
    """方案 §3：目标表缺失时**明确失败**，不许退回旧表。"""
    (tmp_path / "config").mkdir()
    with pytest.raises(ConfigError, match="注册表不存在"):
        BookRegistry.load(Workspace(root=tmp_path))


def test_broken_registry_is_loud(tmp_path):
    ws = _workspace(tmp_path, {}, raw="{ 这不是 json")
    with pytest.raises(ConfigError, match="不是合法 JSON"):
        BookRegistry.load(ws)
    ws = _workspace(tmp_path, {}, raw=json.dumps({"books": {}}))
    with pytest.raises(ConfigError, match="非空的 books"):
        BookRegistry.load(ws)


def test_entry_missing_required_field_is_loud(tmp_path):
    bad = dict(BOOK_A)
    bad.pop("lessons")
    with pytest.raises(ConfigError, match="缺必填字段"):
        BookRegistry.load(_workspace(tmp_path, {"a": bad}))


def test_declared_workspace_mismatch_is_loud(tmp_path):
    """注册表说的工作区跟实际不是一处 → 当场失败。

    两处不一致就是"跑错目录里的同名书"这类错误的入口，而且它不会以任何别的方式暴露。
    """
    ws = _workspace(tmp_path, {"a": BOOK_A}, workspace_key="/somewhere/else")
    with pytest.raises(ConfigError, match="workspace"):
        BookRegistry.load(ws)


def test_find_workspace_refuses_to_guess(tmp_path, monkeypatch):
    """**故意没有"从包所在位置向上找"这一步**：包装在哪跟这次处理哪个工作区无关。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_WORKSPACE, raising=False)
    with pytest.raises(ConfigError, match="找不到工作区"):
        find_workspace()
    # 显式给了但目录不对 → 也要报错，不许"差不多就用"
    with pytest.raises(ConfigError, match="不像一个 AIforStudy 工作区"):
        find_workspace(tmp_path)


def test_find_workspace_walks_up_from_cwd(tmp_path, monkeypatch):
    """在含 config/books.json 的目录**的子目录**下运行也要能找到（3 号给法）。"""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "books.json").write_text('{"books":{"a":{}}}', encoding="utf-8")
    deep = tmp_path / "data" / "parse" / "x"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)
    monkeypatch.delenv(ENV_WORKSPACE, raising=False)
    assert find_workspace() == tmp_path.resolve()


def test_env_workspace_is_honoured(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "books.json").write_text('{"books":{"a":{}}}', encoding="utf-8")
    monkeypatch.setenv(ENV_WORKSPACE, str(tmp_path))
    monkeypatch.chdir(Path("/"))
    assert open_workspace().root == tmp_path.resolve()


# ────────────────────────────────────────── 同进程两本书不串状态

def test_two_books_in_one_process_do_not_share_state(tmp_path):
    """M1 通过条件：同进程两本书不串状态。

    这条专门防"路径常量在 import 时固化"：注册表对象是**不可变值**，
    `resolve` 只根据传入的 book_id 算，不读任何模块级全局。
    """
    registry = load_registry(_workspace(tmp_path, {"a": BOOK_A, "b": BOOK_B}))
    a1 = registry.resolve("a")
    b = registry.resolve("b")
    a2 = registry.resolve("a")
    assert (a1.parse, b.parse) == (tmp_path / "data/parse/a", tmp_path / "data/parse/b")
    assert a1.lessons == tmp_path / "data/lesson/a"
    assert b.lessons == tmp_path / "data/lesson/b"
    assert b.problems == tmp_path / "data/problems/b.json"
    # 每个引用自带版本，跨书不共用
    assert a1.book_id == "a" and b.book_id == "b" and b.revision == "p2"
    # 解析同一个 id 两次给同一个结果（没有隐藏的"上次用了哪本"状态）
    assert a1 == a2


def test_registry_revision_is_the_file_content(tmp_path):
    """registry revision 是**内容**指纹：文件没变就稳定，改一个字就变。"""
    ws = _workspace(tmp_path, {"a": BOOK_A})
    first = BookRegistry.load(ws).revision
    assert first == BookRegistry.load(ws).revision          # 稳定
    payload = json.loads((tmp_path / "config" / "books.json").read_text(encoding="utf-8"))
    payload["books"]["a"]["title"] = "改个名"
    (tmp_path / "config" / "books.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert BookRegistry.load(ws).revision != first           # 变了


def test_absolute_paths_are_kept_verbatim(tmp_path):
    """方案 §3：外部绝对路径逐项核对 —— 解析时不得被拼到工作区下面。"""
    outside = tmp_path / "outside"
    entry = {**BOOK_A, "parse": str(outside / "parse")}
    registry = load_registry(_workspace(tmp_path, {"a": entry}))
    ctx = registry.resolve("a")
    assert ctx.parse == outside / "parse"
    assert registry.get("a").is_absolute


def test_page_root_is_scoped_per_book(tmp_path):
    """两本书的章名会撞（CH02 是同一个路径），所以页根必须按书收窄。"""
    registry = load_registry(_workspace(tmp_path, {"a": BOOK_A, "b": BOOK_B}))
    assert registry.resolve("a").page_root() != registry.resolve("b").page_root()
    assert registry.resolve("a").page_root().parent.name == "整本"


def test_sources_are_resolved_against_the_workspace(tmp_path):
    entry = {**BOOK_A, "sources": [{"kind": "pdf", "path": "data/raw/x.pdf",
                                    "sha256": None}]}
    registry = load_registry(_workspace(tmp_path, {"a": entry}))
    src = registry.resolve("a").sources[0]
    assert src.path == tmp_path / "data/raw/x.pdf" and src.sha256 is None
