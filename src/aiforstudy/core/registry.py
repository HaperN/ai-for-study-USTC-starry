"""书籍注册表：**唯一权威** + **故意没有默认书**。

方案 §3：

> `config/books.json` 是目标唯一书籍注册表。迁移逐项保留现有 `data/books.json` 的
> book_id、标题、来源、旧产物路径，并登记新根映射；相对路径按显式工作区解析，
> 外部绝对路径逐项核对。切换后旧表只作为迁移证据，**生产不双读、不退回旧默认书**。
> **目标表缺失、损坏或书籍不存在均明确失败。**

方案 §4：

> 省略 `--book` 不退回历史默认书。

这两条合起来决定了本模块最重要的一个设计：**`config/books.json` 里没有「默认」这一项，
`require(book_id)` 在 `book_id is None` 时直接报错。**

为什么这不是"少写一个便利功能"：项目历史上正是靠一个默认值，让"换一本教材"静默变成
"又跑了一遍旧教材"（`linear-algebra-full` 曾在 36 处写死）。留一个默认值，就是留一条
"跑错书但不报错"的路。旧命令的默认行为由过渡期的只读适配器 `lib/book.py` 负责，
那是**给旧入口的兼容**，不是新引擎的语义。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import ConfigError
from .digest import digest_file
from .workspace import Workspace

REQUIRED_FIELDS = ("title", "parse", "lessons", "audit")
OPTIONAL_FIELDS = ("problems", "artifacts_root", "runs_root", "releases_root",
                   "revision", "sources", "legacy")


@dataclass(frozen=True)
class SourceRef:
    """原始输入的一条来源。`sha256` 为 None 表示尚未冻结（M3 会回填）。"""
    kind: str
    path: Path
    sha256: str | None = None
    note: str = ""

    def as_dict(self) -> dict:
        return {"kind": self.kind, "path": str(self.path), "sha256": self.sha256,
                "note": self.note}


@dataclass(frozen=True)
class BookSpec:
    """注册表里的一本书（路径**未解析**，相对注册表所在的配置目录）。"""
    book_id: str
    title: str
    revision: str
    parse: str
    lessons: str
    audit: str
    problems: str
    artifacts_root: str
    runs_root: str
    releases_root: str
    sources: tuple[SourceRef, ...] = ()
    legacy: dict = field(default_factory=dict)

    @property
    def is_absolute(self) -> bool:
        """这本书是否登记了仓库外的绝对路径（方案 §3 要求逐项核对）。"""
        return any(Path(p).is_absolute()
                   for p in (self.parse, self.lessons, self.audit, self.problems))


@dataclass(frozen=True)
class BookContext:
    """一本书的**全部**位置与身份（方案 §6 的 BookContext）。

    与旧 `lib/book.py` 的 `BookContext` 的区别：多了 workspace、revision、
    registry revision、sources、以及 artifacts/runs/releases 三个新根。
    """
    workspace: Path
    book_id: str
    title: str
    revision: str
    registry_revision: str
    sources: tuple[SourceRef, ...]
    parse: Path
    lessons: Path
    audit: Path
    problems: Path
    artifacts_root: Path
    runs_root: Path
    releases_root: Path

    def as_dict(self) -> dict:
        return {
            "workspace": str(self.workspace),
            "book_id": self.book_id,
            "title": self.title,
            "revision": self.revision,
            "registry_revision": self.registry_revision,
            "sources": [s.as_dict() for s in self.sources],
            "parse": str(self.parse),
            "lessons": str(self.lessons),
            "audit": str(self.audit),
            "problems": str(self.problems),
            "artifacts_root": str(self.artifacts_root),
            "runs_root": str(self.runs_root),
            "releases_root": str(self.releases_root),
        }

    def page_root(self) -> Path:
        """整本章页的根目录 —— **按书收窄**。

        血泪教训（旧 `lib/book.py` 记着）：两本书的章名会撞（线性代数的 `CH02`
        和复变函数的 `CH02` 是同一个路径），不收窄就会互相覆盖，而且 doctor 会把
        另一本书的章页当成自己的、报一堆假违规。
        """
        return self.workspace / "delivery" / "整本" / self.book_id


def _str_field(entry: dict, key: str, book_id: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"书 {book_id!r} 的 {key!r} 必须是非空字符串，实际是 {value!r}")
    return value.strip()


def _parse_sources(entry: dict, book_id: str) -> tuple[SourceRef, ...]:
    raw = entry.get("sources") or []
    if not isinstance(raw, list):
        raise ConfigError(f"书 {book_id!r} 的 sources 必须是数组")
    out: list[SourceRef] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ConfigError(f"书 {book_id!r} 的 sources[{i}] 必须是对象")
        kind = str(item.get("kind") or "").strip()
        path = str(item.get("path") or "").strip()
        if not kind or not path:
            raise ConfigError(f"书 {book_id!r} 的 sources[{i}] 缺 kind 或 path")
        sha = item.get("sha256")
        out.append(SourceRef(kind=kind, path=Path(path),
                             sha256=str(sha) if sha else None,
                             note=str(item.get("note") or "")))
    return tuple(out)


class BookRegistry:
    """`config/books.json` 的只读视图。"""

    def __init__(self, *, path: Path, workspace: Path, revision: str,
                 books: dict[str, BookSpec]) -> None:
        self.path = path
        self.workspace = workspace
        self.revision = revision
        self._books = books

    # ── 构造
    @classmethod
    def load(cls, workspace: Workspace) -> "BookRegistry":
        path = workspace.registry_path
        if not path.is_file():
            raise ConfigError(
                f"书籍注册表不存在：{path}。方案 §3 要求 config/books.json 是唯一权威，"
                f"缺失时必须明确失败，**不许退回旧表或默认书**。")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ConfigError(f"书籍注册表 {path} 不是合法 JSON：{exc}") from exc
        if not isinstance(payload, dict):
            raise ConfigError(f"书籍注册表 {path} 顶层必须是对象")
        books_raw = payload.get("books")
        if not isinstance(books_raw, dict) or not books_raw:
            raise ConfigError(f"书籍注册表 {path} 里没有非空的 books 项")

        declared = payload.get("workspace", ".")
        if declared not in (".", "./", None):
            declared_root = (path.parent / str(declared)).resolve()
            if declared_root != workspace.root.resolve():
                raise ConfigError(
                    f"注册表声明的 workspace={declared!r}（解析为 {declared_root}）"
                    f"与实际工作区 {workspace.root} 不一致。"
                    f"两处不一致就是'跑错目录里的同名书'这类错误的入口，必须当场失败。")

        books: dict[str, BookSpec] = {}
        for book_id, entry in books_raw.items():
            if not isinstance(entry, dict):
                raise ConfigError(f"书 {book_id!r} 的登记项必须是对象")
            missing = [k for k in REQUIRED_FIELDS if not str(entry.get(k) or "").strip()]
            if missing:
                raise ConfigError(f"书 {book_id!r} 缺必填字段：{missing}")
            books[str(book_id)] = BookSpec(
                book_id=str(book_id),
                title=_str_field(entry, "title", str(book_id)),
                revision=str(entry.get("revision") or "unversioned"),
                parse=_str_field(entry, "parse", str(book_id)),
                lessons=_str_field(entry, "lessons", str(book_id)),
                audit=_str_field(entry, "audit", str(book_id)),
                problems=str(entry.get("problems") or f"data/problems/{book_id}.json"),
                artifacts_root=str(entry.get("artifacts_root")
                                   or f"data/books/{book_id}/artifacts"),
                runs_root=str(entry.get("runs_root") or f"data/books/{book_id}/runs"),
                releases_root=str(entry.get("releases_root")
                                  or f"data/books/{book_id}/releases"),
                sources=_parse_sources(entry, str(book_id)),
                legacy=dict(entry.get("legacy") or {}),
            )
        return cls(path=path, workspace=workspace.root, revision=digest_file(path),
                   books=books)

    # ── 查询
    def list_books(self) -> list[str]:
        return sorted(self._books)

    def __contains__(self, book_id: object) -> bool:
        return str(book_id) in self._books

    def get(self, book_id: str) -> BookSpec:
        if book_id not in self._books:
            raise ConfigError(
                f"书籍注册表里没有 {book_id!r}；有的是：{self.list_books()}")
        return self._books[book_id]

    def require(self, book_id: str | None) -> BookSpec:
        """**没有默认书**：`None` 直接报错（方案 §4）。"""
        if book_id is None or not str(book_id).strip():
            raise ConfigError(
                "必须显式指定 --book。方案 §4：省略 --book 不退回历史默认书——"
                "留一个默认值就是留一条'跑错书但不报错'的路。"
                f"注册表里有：{self.list_books()}")
        return self.get(str(book_id).strip())

    def resolve(self, book_id: str | None) -> BookContext:
        """书 id → `BookContext`（相对路径按 workspace 解析）。"""
        spec = self.require(book_id)
        ws = self.workspace

        def p(rel: str) -> Path:
            return Path(rel) if Path(rel).is_absolute() else (ws / rel)

        return BookContext(
            workspace=ws,
            book_id=spec.book_id,
            title=spec.title,
            revision=spec.revision,
            registry_revision=self.revision,
            sources=tuple(SourceRef(kind=s.kind, path=p(str(s.path)),
                                    sha256=s.sha256, note=s.note) for s in spec.sources),
            parse=p(spec.parse),
            lessons=p(spec.lessons),
            audit=p(spec.audit),
            problems=p(spec.problems),
            artifacts_root=p(spec.artifacts_root),
            runs_root=p(spec.runs_root),
            releases_root=p(spec.releases_root),
        )

    def as_dict(self) -> dict:
        return {"path": str(self.path), "workspace": str(self.workspace),
                "revision": self.revision, "books": self.list_books()}


def load_registry(workspace: Workspace) -> BookRegistry:
    return BookRegistry.load(workspace)


__all__ = ["REQUIRED_FIELDS", "OPTIONAL_FIELDS", "SourceRef", "BookSpec", "BookContext",
           "BookRegistry", "load_registry"]
