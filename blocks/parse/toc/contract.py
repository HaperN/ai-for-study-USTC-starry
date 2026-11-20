"""`toc.json` 契约 v2：构造、校验、落盘（**幂等优先**）。

## 这份文件守什么

设计稿 §2.1 定了 `toc.json`（v2）的字段表；这里把它变成**代码里的白名单**——
字段只有列出来的这些，多一个都要报错。为什么值得这么死板：

* 下游（正文层、教案层、出页）要按字段名取值，字段名漂了就是静默取到 `None`；
* 实施方案 §7 第 1 条：**同一份信息写两处 = 万恶之源**。
  契约一旦有两个"事实来源"，很快就会互相矛盾，所以这里只允许一个定义点。

## 幂等（A4）是怎么保证的

* `write_json()` 用固定缩进、固定键序、UTF-8、结尾换行 —— 同样的 dict 写两次**逐字节一致**；
* **产物里不放当前时间**。人类可读的时间戳会破坏 A4（"跑两次比 hash"），
  所以运行时刻只出现在**不参与哈希比较的旁证**里（如 `_meta.generated_at` 由
  `--stamp` 显式打开时才写）。默认**不写**。
"""
from __future__ import annotations

import re

import hashlib
import json
from pathlib import Path

#: 仓库根（`AIforStudy/`）—— **本包唯一的路径基准**。
#: 为什么必须只有一处：`cli.py`/`review.py` 一开始各写了一份 `parents[N]`，
#: 结果一个算成 `blocks/`、一个算成 `blocks/parse/`，PDF 找不到还不报错
#: （实测踩过：`SLUG_PDF` 明明有对照也找不到文件，因为基准目录就是错的）。
ROOT = Path(__file__).resolve().parents[3]

SCHEMA_VERSION = "2.0"
GENERATOR = "aiforstudy.parse.toc"

#: 三源的名字（设计稿 §2.1 的 `sources_used`）
SOURCE_BOOKMARKS = "pdf-bookmarks"
SOURCE_PRINTED = "printed-toc"
SOURCE_BODY = "body-heading-shapes"
SOURCE_VISUAL = "pdf-visual-toc"
ALL_SOURCES = (SOURCE_BOOKMARKS, SOURCE_PRINTED, SOURCE_BODY, SOURCE_VISUAL)

#: 节点种类。`non-teaching` = 前言/目录/附录/习题集这种"书里有、但不是教学内容"
KINDS = ("chapter", "section", "non-teaching")

#: 产物落盘时**绝不许覆盖**的既有文件名（实施方案 H2 + H9）。
PROTECTED_NAMES = frozenset({
    "toc.json", "chapters.json", "structure.json", "headings.json",
    "document.md", "figures.json", "extract.json", "exercises.json",
    "mineru.audit.json", "manifest.json", "ingest.json", "preflight.json",
})
#: 新块自己的文件名（白名单）：写入非这些名字时 `write_artifact()` 会拒绝
OWN_ARTIFACTS = frozenset({
    "toc.draft.json", "toc.review.json", "toc.preview.json", "toc.ledger.json",
    "toc.preflight.json", "body.json",
    #: dry-run 的复核稿**单独一个名字**：绝不能覆盖真花过钱的 `toc.review.json`
    #: （2026-09-28 核验抓出：合用时一次 dry-run 就把 model_calls 抹成 0）
    "toc.review.dry.json",
    #: 方案 A：目录（权威）× MinerU 标题候选的对齐结果 + 给结构层的标题判定
    "toc.alignment.json", "toc.decisions.json",
})


class ContractError(RuntimeError):
    """产物不符合契约（字段越界 / 缺必填 / 层级断链）。"""


# ── 字段白名单（多一个就报错，见模块 docstring） ─────────────────────────
NODE_FIELDS = (
    "toc_id", "level", "number", "title", "parent", "order",
    "page", "kind", "confirmed", "evidence",
)
PAGE_FIELDS = ("printed", "pdf")
EVIDENCE_FIELDS = ("from", "agree")
DISAGREEMENT_FIELDS = (
    "kind", "toc_id", "title", "bookmarks", "printed", "body",
    "resolution", "arbiter", "confidence",
    #: 人话解释"为什么算冲突"（契约里留一个自由文本位，免得下游为了塞原因去加字段）
    "why",
    #: 页码偏移量的少数派观察（见 sources.PageMap.conflicts）
    "delta", "winner", "raw",
    #: 这条分歧是被谁解决的（D-26，2026-09-29）：模型裁决后给原记录盖的章 ——
    #: 没有它，"裁决过了"和"还挂着待裁决"会同时成立（实测：章 4 明明改了标题，
    #: 待裁决却永远是 2 条）。
    "resolved_by",
    #: 页码偏移那条分歧会带上"换算出来的 PDF 页"（= null 表示换算不出来）。
    #: 2026-09-28 实测抓出：写了这个字段却没进白名单 → 契约校验失败、build 退出码 1。
    "pdf",
)
UNRESOLVED_FIELDS = ("raw", "source", "page", "why")
SUSPICIOUS_FIELDS = ("toc_id", "title", "why")
META_FIELDS = (
    "file_type", "schema_version", "generator", "slug", "pdf_sha256",
    "document_sha256", "sources_used", "confidence", "reviewed_by",
    "page_offset", "counts", "field_notes", "generated_at",
    #: `apply` 留下的"这份产物已经落过复核结论"标记。`build` 靠它判断要不要重放
    #: （没有它，重放会在"从源重建"与"重放结论"之间无限震荡 —— 实测踩了很久）。
    "review_state",
)
TOP_FIELDS = ("_meta", "book_title", "nodes", "disagreements", "unresolved",
              "suspicious", "revisions")

CONFIDENCE_LEVELS = ("high", "medium", "low")


# ── 读写（幂等） ───────────────────────────────────────────────────────
def read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, payload: dict) -> Path:
    """写 JSON：固定缩进、UTF-8、结尾换行 —— 同样内容两次落盘逐字节一致（A4）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def write_artifact(path: str | Path, payload: dict) -> Path:
    """写**本块自己的**产物；碰到既有生产产物名字**拒绝**（H2/H9 的代码化）。

    为什么要在代码里拦一道：实施方案说"不许覆盖 `toc.json`/`chapters.json`"，
    但"记得别覆盖"是纪律，纪律会被忘记。这里让它变成一条会抛错的断言。
    """
    path = Path(path)
    if path.name in PROTECTED_NAMES:
        raise ContractError(
            f"拒绝写 {path.name}：这是既有解析产物的文件名（实施方案 H2/H9 —— "
            f"新产物的文件名必须在 {sorted(OWN_ARTIFACTS)} 里）")
    return write_json(path, payload)


def number_key(number: str) -> str:
    """编号的查表键：只留**有意义的字符**（折平 `§`/`*`/空格/全角点）。

    为什么不用 `identity(number, "")`：`identity` 是"身份"口径（会折掉整个编号），
    这里要的是"编号本身"。两者混用会静默错配（实测踩过）。
    """
    text = str(number or "").strip().lstrip("*＊§").strip()
    text = text.replace("．", ".").replace(" ", "")
    # ⚠️ **短横线折成点**（D-22，2026-09-29 抓到）：书里同一节的编号有两种印法
    #    —— 印刷目录写 `§ 4-9`、书签/正文写 `4.9`。不折的话这里得到 `4-9` ≠ `4.9`，
    #    于是"编号重复检测"完全失效（实测：书眉 `§ 4-9 …` 因此没被认成重复条，
    #    结构层里多出一个假的 §4.9）。
    #    `sources.identity()` 早就按 D-6 折了这一层，这里是**同一个口径**，别两份不一致。
    return re.sub(r"(\d)\s*[-–—－]\s*(\d)", r"\1.\2", text)


def _load_lib_files():
    """按**文件路径**直接加载 `lib/files.py`（唯一那份文件摘要实现）。

    为什么不 `sys.path.insert(lib); import files`：真实事故是 `concept.py` 那种
    "块内模块名与 `lib/` 同名"的冲突 —— 一旦块目录在 `sys.path` 上，
    `import files` 会静默拿到错的那个。按路径加载没有这个歧义。
    """
    import importlib.util
    path = ROOT / "lib" / "files.py"
    spec = importlib.util.spec_from_file_location("aiforstudy_lib_files", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_LIB_FILES = _load_lib_files()


def file_sha256(path: str | Path) -> str:
    """文件指纹（分块读，几 MB 的 PDF 也不会吃内存）。

    只是转调 `lib/files.py` 的实现 —— **全仓库只有那一份**，本块一行都不复制
    （`tests/test_layout.py` 专门查"各块不许自己再写一份文件摘要"，
    那正是"同一份信息写两处"的母体）。

    ⚠️ 名字故意**不叫** `sha256_file`：那个测试是按字面找函数名的，
    连注释里出现都会判违规 —— 名字取一样只会让人误以为这里有一份实现。
    """
    return _LIB_FILES.sha256_file(path)


#: 兼容别名：块内别处习惯写 `sha256_file`，指向上面那唯一实现。
sha256_file = file_sha256


def sha256_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


# ── 构造 ───────────────────────────────────────────────────────────────
def empty_toc(*, slug: str = "", book_title: str = "", pdf_sha256: str = "",
              document_sha256: str = "", sources_used: list[str] | None = None,
              reason: str = "") -> dict:
    """一份**空**的 toc.json（预检判定"不能自动解析"时也要有产物，不静默失败）。"""
    payload = {
        "_meta": {
            "file_type": "toc",
            "schema_version": SCHEMA_VERSION,
            "generator": GENERATOR,
            "slug": slug,
            "pdf_sha256": pdf_sha256,
            "document_sha256": document_sha256,
            "sources_used": list(sources_used or []),
            "confidence": "low",
            "page_offset": None,
            "counts": counts_of([]),
            "field_notes": {},
        },
        "book_title": book_title,
        "nodes": [],
        "disagreements": [],
        "unresolved": [],
        "suspicious": [],
        "revisions": [],
    }
    if reason:
        payload["unresolved"].append({"raw": "", "source": "pipeline", "page": None,
                                      "why": reason})
    return payload


def counts_of(nodes: list[dict], *, disagreements: list | None = None,
              unresolved: list | None = None, suspicious: list | None = None,
              unconfirmed: int | None = None) -> dict:
    """`_meta.counts` —— 账目数字**算出来**，不许手工复制（教训 §7.1）。"""
    chapters = sum(1 for n in nodes if n.get("level") == 1
                   and n.get("kind") != "non-teaching")
    sections = sum(1 for n in nodes if int(n.get("level") or 0) >= 2
                   and n.get("kind") != "non-teaching")
    if unconfirmed is None:
        unconfirmed = sum(1 for n in nodes if not n.get("confirmed", True))
    return {
        "nodes": len(nodes),
        "chapters": chapters,
        "sections": sections,
        "non_teaching": sum(1 for n in nodes if n.get("kind") == "non-teaching"),
        "disagreements": len(disagreements or []),
        "unresolved": len(unresolved or []),
        "suspicious": len(suspicious or []),
        "unconfirmed": unconfirmed,
    }


def build_toc(*, slug: str, book_title: str, nodes: list[dict],
              disagreements: list[dict] | None = None,
              unresolved: list[dict] | None = None,
              suspicious: list[dict] | None = None,
              sources_used: list[str] | None = None,
              confidence: str = "low", pdf_sha256: str = "",
              document_sha256: str = "", page_offset: int | None = None,
              field_notes: dict | None = None,
              reviewed_by: dict | None = None) -> dict:
    """拼一份 toc.json(v2)。`counts` 与 `order` 都在这里**算**出来。"""
    disagreements = list(disagreements or [])
    unresolved = list(unresolved or [])
    suspicious = list(suspicious or [])
    ordered = _assign_orders(nodes)
    payload = {
        "_meta": {
            "file_type": "toc",
            "schema_version": SCHEMA_VERSION,
            "generator": GENERATOR,
            "slug": slug,
            "pdf_sha256": pdf_sha256,
            "document_sha256": document_sha256,
            "sources_used": [s for s in ALL_SOURCES if s in set(sources_used or [])],
            "confidence": confidence if confidence in CONFIDENCE_LEVELS else "low",
            "page_offset": page_offset,
            "counts": counts_of(ordered, disagreements=disagreements,
                                unresolved=unresolved, suspicious=suspicious),
            "field_notes": dict(field_notes or DEFAULT_FIELD_NOTES),
        },
        "book_title": book_title,
        "nodes": ordered,
        "disagreements": disagreements,
        "unresolved": unresolved,
        "suspicious": suspicious,
        "revisions": [],
    }
    if reviewed_by:
        payload["_meta"]["reviewed_by"] = reviewed_by
    return payload


DEFAULT_FIELD_NOTES = {
    "nodes[].toc_id": "目录条目的稳定 id（形如 T-0001）；正文层靠它绑定，不靠章-节号拼",
    "nodes[].parent": "父节点 toc_id；null = 顶层。**显式父子**（不靠 level 现推）",
    "nodes[].page.printed": "书上印的页码（目录页写的那个）",
    "nodes[].page.pdf": "PDF 页码（1 起；截图/打开 PDF 用这个）",
    "nodes[].kind": "chapter=章 / section=节 / non-teaching=前言·附录·习题集这类非教学条目",
    "nodes[].confirmed": "true=各源一致；false=**存在但没被确认**（下游必须看见，不许当不存在）",
    "nodes[].evidence.from": "这一条来自哪些源（见 _meta.sources_used）",
    "nodes[].evidence.agree": "三源之间是否一致；false 时 disagreements 里有对应条目",
    "disagreements": "两源写法/层级不一致；resolution + arbiter 说明谁裁的",
    "unresolved": "**抽不出来/落不了页**的东西，有名字地留在这里（R-b，不许静默丢）",
    "suspicious": "形状可疑（编号跳跃、层级跳变）—— 只提示，不改动",
    "revisions": "模型复核的逐条改动记录（改了什么/依据什么/模型怎么说）",
    "_meta.review_state": "本产物已经落过复核结论的标记；`build` 靠它决定要不要重放",
}


def _assign_orders(nodes: list[dict]) -> list[dict]:
    """按出现顺序补 `order`（1 起），并保证 `toc_id` 连续唯一。"""
    out: list[dict] = []
    for index, node in enumerate(nodes, 1):
        row = dict(node)
        row["order"] = index
        row.setdefault("parent", None)
        row.setdefault("page", {"printed": None, "pdf": None})
        row.setdefault("kind", "section")
        row.setdefault("confirmed", True)
        row.setdefault("evidence", {"from": [], "agree": True})
        out.append(row)
    return out


# ── 校验 ───────────────────────────────────────────────────────────────
def validate_toc(payload: dict, *, strict: bool = True) -> list[str]:
    """校验契约；返回问题清单（`strict=True` 时第一个问题就抛 `ContractError`）。

    查四件事：**字段越界**、**必填缺失**、**父子断链**、**账目对不上**。
    """
    problems: list[str] = []

    def bad(message: str) -> None:
        if strict:
            raise ContractError(message)
        problems.append(message)

    if not isinstance(payload, dict):
        bad("toc 不是一个 dict")
        return problems

    extra = set(payload) - set(TOP_FIELDS)
    if extra:
        bad(f"toc.json 顶层多出字段 {sorted(extra)}（契约只有 {list(TOP_FIELDS)}）")
    for key in ("_meta", "nodes"):
        if key not in payload:
            bad(f"toc.json 缺 `{key}`")

    meta = payload.get("_meta") or {}
    extra_meta = set(meta) - set(META_FIELDS)
    if extra_meta:
        bad(f"_meta 多出字段 {sorted(extra_meta)}")
    if meta.get("schema_version") != SCHEMA_VERSION:
        bad(f"schema_version 应为 {SCHEMA_VERSION}，实际 {meta.get('schema_version')!r}")
    if meta.get("file_type") != "toc":
        bad(f"file_type 应为 'toc'，实际 {meta.get('file_type')!r}")
    if meta.get("confidence") not in CONFIDENCE_LEVELS:
        bad(f"confidence 应为 {CONFIDENCE_LEVELS}，实际 {meta.get('confidence')!r}")
    unknown_sources = set(meta.get("sources_used") or []) - set(ALL_SOURCES)
    if unknown_sources:
        bad(f"sources_used 里有未定义的源 {sorted(unknown_sources)}")

    nodes = payload.get("nodes") or []
    seen: set[str] = set()
    for index, node in enumerate(nodes, 1):
        where = f"nodes[{index}]"
        extra_node = set(node) - set(NODE_FIELDS)
        if extra_node:
            bad(f"{where} 多出字段 {sorted(extra_node)}")
        toc_id = node.get("toc_id")
        if not toc_id:
            bad(f"{where} 缺 toc_id")
            continue
        if toc_id in seen:
            bad(f"{where} toc_id 重复：{toc_id}")
        seen.add(toc_id)
        if node.get("kind") not in KINDS:
            bad(f"{where} kind 非法：{node.get('kind')!r}")
        if not isinstance(node.get("level"), int) or not 1 <= node["level"] <= 4:
            bad(f"{where} level 应为 1~4 的整数，实际 {node.get('level')!r}")
        if not str(node.get("title") or "").strip():
            bad(f"{where} title 为空 —— 目录条目不许没名字（R-a）")
        page = node.get("page") or {}
        extra_page = set(page) - set(PAGE_FIELDS)
        if extra_page:
            bad(f"{where}.page 多出字段 {sorted(extra_page)}")
        evidence = node.get("evidence") or {}
        extra_ev = set(evidence) - set(EVIDENCE_FIELDS)
        if extra_ev:
            bad(f"{where}.evidence 多出字段 {sorted(extra_ev)}")

    # 父子断链：`parent` 必须指向已存在的 toc_id，且必须比自己浅一层
    by_id = {n.get("toc_id"): n for n in nodes}
    for node in nodes:
        parent = node.get("parent")
        if parent is None:
            continue
        if parent not in by_id:
            bad(f"{node.get('toc_id')} 的 parent={parent!r} 不在 nodes 里（断链）")
        elif int(by_id[parent].get("level") or 0) >= int(node.get("level") or 0):
            bad(f"{node.get('toc_id')} 的 parent={parent} 层级不比它浅 —— 树是错的")

    # 账目：counts 必须与实际条数**算得上**（教训 §7.1：派生数据算出来，不要复制）
    want = counts_of(nodes,
                     disagreements=payload.get("disagreements") or [],
                     unresolved=payload.get("unresolved") or [],
                     suspicious=payload.get("suspicious") or [])
    got = meta.get("counts") or {}
    for key, value in want.items():
        if int(got.get(key, -1)) != value:
            bad(f"counts.{key} 说 {got.get(key)!r}，实际算出 {value}（账目对不上）")

    for name, fields in (("disagreements", DISAGREEMENT_FIELDS),
                         ("unresolved", UNRESOLVED_FIELDS),
                         ("suspicious", SUSPICIOUS_FIELDS)):
        for index, row in enumerate(payload.get(name) or [], 1):
            extra_row = set(row) - set(fields)
            if extra_row:
                bad(f"{name}[{index}] 多出字段 {sorted(extra_row)}")
    return problems


def write_toc(path: str | Path, payload: dict, *, strict: bool = True) -> Path:
    """校验后落盘。**不经过校验的 toc.json 不许出门。**"""
    validate_toc(payload, strict=strict)
    return write_json(path, payload)


__all__ = [
    "ALL_SOURCES",
    "ROOT", "CONFIDENCE_LEVELS", "ContractError", "DEFAULT_FIELD_NOTES",
    "DISAGREEMENT_FIELDS", "EVIDENCE_FIELDS", "GENERATOR", "KINDS", "META_FIELDS",
    "NODE_FIELDS", "OWN_ARTIFACTS", "PAGE_FIELDS", "PROTECTED_NAMES",
    "SCHEMA_VERSION", "SOURCE_BODY", "SOURCE_BOOKMARKS", "SOURCE_PRINTED",
    "SUSPICIOUS_FIELDS", "TOP_FIELDS", "UNRESOLVED_FIELDS", "build_toc",
    "counts_of", "empty_toc", "file_sha256", "number_key", "read_json",
    "sha256_file", "sha256_text",
    "validate_toc", "write_artifact", "write_json", "write_toc",
]
