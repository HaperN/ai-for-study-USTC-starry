"""「这是哪一本书」—— **过渡期只读适配器**（真正实现在 `aiforstudy.core.registry`）。

> 方案 §3：`config/books.json` 是目标**唯一**书籍注册表。迁移逐项保留现有
> `data/books.json` 的 book_id、标题、来源、旧产物路径，并登记新根映射；
> 相对路径按显式工作区解析，外部绝对路径逐项核对。**切换后旧表只作为迁移证据，
> 生产不双读、不退回旧默认书。**

M1 已完成这次切换：本模块现在读 `config/books.json`，**不再读 `data/books.json`**
（旧表留在 `data/_migration/legacy-books.json` 作迁移证据）。

**为什么还留这个模块**：现有 37 个测试、`tools/*.py` 里十来个入口都按 `import book`
拿路径。一次性改完会把 M1 变成一次大爆炸式重构；方案 §14 明确"保留新旧入口同时运行
**仅限迁移阶段**"。所以这里保留**旧形状的返回值**（含一个合成的「默认」），
把新语义（没有默认书、带 revision/sources）留给 `aiforstudy.core.registry`。

⚠️ 一处刻意的语义差异，必须在 M9 前收敛：本模块**有**默认书
（`book=None` → `linear-algebra-full`），新引擎**没有**（方案 §4：省略 `--book`
不退回历史默认书）。这是给旧命令的兼容，不是新引擎的语义。

## 历史（保留：它解释了为什么这里只该有一份解析）

用户 2026-09-26：「你要注意，我们的之后可能会输入其他的教材」。

当时 `linear-algebra-full` 在代码里**写死 36 处**（其中 `tools/doctor.py` 有三个
路径常量、`tools/make_delivery_package.py` 有 `SECTIONS`/`AUDIT_DIR`）。
后果不是"少打一个包"，而是**换一本教材时 doctor 会去查旧书、打包会打旧页**，
而且一句报错都没有。

做法：契约即文件。书的清单放在注册表里，**受版本控制**；代码只认这份契约。
加一本新书 = 加一项，不用改代码。

`resolve()` 是**唯一**入口：所有工具都从这里拿路径，不各写一份默认值
（"同一份信息写两处"就是这个项目 119 条错误的母体）。

不传 `book` 时用 `默认` 那一项 —— 旧命令的行为保持不变。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
#: 唯一权威（方案 §3）。旧表 `data/books.json` **不再被生产读取**。
REGISTRY = ROOT / "config" / "books.json"
#: 迁移证据（M0 归档的旧表）；只用于核对"哪些书/路径迁移过来了"，不参与解析。
LEGACY_REGISTRY = ROOT / "data" / "_migration" / "legacy-books.json"

# 契约文件读不出来时的兜底（宁可"还是那本旧书"，也不要让所有工具一起崩）
FALLBACK_ID = "linear-algebra-full"
FALLBACK = {
    "title": "线性代数",
    "parse": "data/parse/linear-algebra-full",
    "lessons": "data/lesson/linear-algebra-full",
    "audit": "data/audit/linear-algebra-full",
    "problems": "data/problems/linear-algebra-full.json",
}


class BookError(RuntimeError):
    """书清单本身有问题（文件坏了 / 要的书不在里面）。"""


@dataclass(frozen=True)
class BookContext:
    """一本书的四个位置。`problems` 是文件（不是目录）。"""
    book_id: str
    title: str
    parse: Path
    lessons: Path
    audit: Path
    problems: Path

    def as_dict(self) -> dict:
        return {"book_id": self.book_id, "title": self.title, "parse": str(self.parse),
                "lessons": str(self.lessons), "audit": str(self.audit),
                "problems": str(self.problems)}

    def pages_root(self) -> Path:
        """这本书"整本一章一页"的根目录：**按书收窄**。

        ⚠️ 为什么必须按书收窄（2026-09-27 全流程试跑实测抓出）：
        整本页原来固定写在 `delivery/整本/` —— 而**两本书的章名会撞**
        （线性代数的 `CH02` 和复变函数的 `CH02` 是同一个路径）。
        后果实测到了两件：① `make_delivery_package.SECTIONS = all_sections()` 派生默认书页面时
        **把新书（复变函数）的 17 个章页当成自己的**，拿去跟线性代数的教案/概念对照
        → 医生一口气报 60 条**假违规**；② 真跑起来会**互相覆盖**。
        """
        return ROOT / "delivery" / "整本" / self.book_id


def load_registry(registry: Path | None = None) -> dict:
    """读书清单，并**归一成旧形状**（保证有 `默认`）。

    两种形状都吃：

    * 旧形状：`{"默认": id, "books": {...}}`（`data/books.json`、测试里的临时清单）；
    * 新形状：`{"workspace": ".", "books": {...}}`（`config/books.json`，**故意没有默认书**）。

    新形状进来时补一个 `默认 = FALLBACK_ID`：那是**旧入口的兼容值**，不是新表的语义。
    新引擎（`aiforstudy.core.registry`）不这么做——它要求显式 `--book`。
    """
    path = Path(registry) if registry else REGISTRY
    if not path.is_file():
        return {"默认": FALLBACK_ID, "books": {FALLBACK_ID: dict(FALLBACK)}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise BookError(f"书清单 {path} 不是合法 JSON：{exc}") from exc
    books = payload.get("books")
    if not isinstance(books, dict) or not books:
        raise BookError(f"书清单 {path} 里没有 books 这一项")
    return {"默认": str(payload.get("默认") or FALLBACK_ID),
            "books": books,
            "_meta": payload.get("_meta") or {},
            "_source": str(path)}


def default_id(registry: Path | None = None) -> str:
    payload = load_registry(registry)
    return str(payload.get("默认") or next(iter(payload["books"])))


def list_books(registry: Path | None = None) -> list[str]:
    return sorted(load_registry(registry)["books"])


def resolve(book: str | None = None, *, registry: Path | None = None,
            root: Path | None = None) -> BookContext:
    """把书 id 解析成四个绝对路径。`book=None` → 用清单里的默认那本。

    要的书不在清单里 → **报错并列出有哪些书**（而不是悄悄用默认那本 ——
    那样"我要跑新教材"会变成"又跑了一遍旧教材"，且不报错）。
    """
    payload = load_registry(registry)
    books = payload["books"]
    book_id = str(book or payload.get("默认") or next(iter(books)))
    if book_id not in books:
        raise BookError(f"书清单里没有 {book_id!r}；有的是：{sorted(books)}")
    entry = {**FALLBACK, **books[book_id]} if book_id == FALLBACK_ID else dict(books[book_id])
    base = Path(root) if root else ROOT
    missing = [k for k in ("parse", "lessons", "audit") if not entry.get(k)]
    if missing:
        raise BookError(f"书 {book_id!r} 的清单项缺字段：{missing}")
    return BookContext(
        book_id=book_id,
        title=str(entry.get("title") or book_id),
        parse=base / entry["parse"],
        lessons=base / entry["lessons"],
        audit=base / entry["audit"],
        problems=base / (entry.get("problems") or f"data/problems/{book_id}.json"),
    )


__all__ = ["ROOT", "REGISTRY", "LEGACY_REGISTRY", "FALLBACK_ID", "BookError", "BookContext",
           "load_registry", "default_id", "list_books", "resolve"]
