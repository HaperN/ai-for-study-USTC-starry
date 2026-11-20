"""导出层：`normalized-source.jsonl` → 你方案里那份 `data/parse/` 目录。

输入（由规范层 `normalize-source` 产出，本项目不改它一行）：
  sources/normalized-source.jsonl   每行一个 block：
      block_id / block_type(chapter|section|subsection|toc) / anchor /
      raw_markdown / normalized_markdown / source_lines / content_sha256 / disposition

输出（全部带 `_meta`，见任务基线 G3）：
  data/parse/manifest.json      总索引：源绑定 + 每个产物的 sha256
  data/parse/chapters.json      章节树 + lesson_units（教案块要处理的最小单元清单）
  data/parse/original_text/*.md 每节原文（**逐字节等于 block 的 raw_markdown**）
  data/parse/images.json        配图索引（从 markdown 图片引用提取）
  data/parse/tables.json        来源层保留的表格 HTML、裁图和原页位置

两个关键设计：
  1. **原文零改写**：每个 .md 文件的内容就是某一个 block 的 `raw_markdown`（只补一个换行），
     所以"原文是否被改动"可以逐字节验证，不需要靠人工看。
  2. **`lesson_units`**：教案块按"小节"串行处理，但书里层级深浅不一
     （有的节有小节、有的节直接就是正文）。导出层在这里一次性算清
     "到底要处理哪些单元、每个单元对应哪个文件"，教案块不需要再判断层级。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_VERSION = "1.0"
GENERATOR = "aiforstudy.parse.export"
BLOCK_LINE_KEYS = ("block_id", "block_type", "anchor", "raw_markdown",
                   "normalized_markdown", "source_lines", "content_sha256")
IMAGE_REF = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
# 习题区标题（`习题一` / `练习 2` / `复习题三`）：这类节点**不进教案单元**，
# 题目由 `blocks/parse/exercises` 挖到 `exercises.json`，交给以后的习题块（方案 §七）。
#: 习题区标记的标题（`习题` / `习题一` / `习题 8.1` / `第 8 章综合习题` / `综合习题`）——
#: 它们是**区域标记**，不是教案单元（见 `build_lesson_units`）。
#: ⚠️ 判据必须与结构层 `structure.EXERCISE_MARK` **一模一样**（2026-09-27 实测：
#:   原来这里只认光秃秃的 `习题`/`习题一`，于是《数分讲义第二册》的 `习题11.1`
#:   被当成小节进了 `lesson_units`（`CH04-S02 习题11.1`）—— 白跑一个单元）。
EXERCISE_TITLE = re.compile(
    r"^\s*(?:(?:第\s*[一二三四五六七八九十百零〇\d]+\s*[章篇])\s*)?"
    r"(?:综合习题|习题|练习|复习题|总习题)\s*[\d.．\-—]*"
    r"[一二三四五六七八九十百零〇]*\s*$")


# ── 基础工具 ────────────────────────────────────────────────────────────
def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(Path(path).read_bytes())


def _meta(file_type: str, source_sha256: str, field_notes: dict) -> dict:
    return {
        "file_type": file_type,
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "source_sha256": source_sha256,
        "field_notes": field_notes,
    }


def load_blocks(normalized_source: Path) -> list[dict]:
    """读 normalized-source.jsonl，做最小校验。"""
    normalized_source = Path(normalized_source)
    if not normalized_source.is_file():
        raise ValueError(f"找不到 normalized-source.jsonl: {normalized_source}")
    blocks: list[dict] = []
    for number, line in enumerate(normalized_source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            block = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"normalized-source.jsonl 第 {number} 行不是合法 JSON") from exc
        if not isinstance(block, dict) or "block_id" not in block:
            raise ValueError(f"normalized-source.jsonl 第 {number} 行缺少 block_id")
        blocks.append(block)
    if not blocks:
        raise ValueError("normalized-source.jsonl 是空的")
    return blocks


# ── 目录树 ──────────────────────────────────────────────────────────────
@dataclass
class Node:
    id: str
    kind: str                 # chapter | section | subsection
    anchor: str | None
    title: str
    order: int
    blocks: list[dict] = field(default_factory=list)
    children: list["Node"] = field(default_factory=list)
    original_text: str | None = None


def _title_of(block: dict) -> str:
    """从 block 的第一行标题里剥掉 markdown 标记和编号，得到纯标题。

    规范层产出的标题形如：`## §1.1 向量的线性运算`。
    """
    text = str(block.get("normalized_markdown") or block.get("raw_markdown") or "")
    first = text.splitlines()[0] if text.splitlines() else ""
    first = re.sub(r"^#+\s*", "", first).strip()
    anchor = str(block.get("anchor") or "").strip()
    if anchor:
        first = re.sub(r"^§\s*" + re.escape(anchor) + r"\s*", "", first)
        first = re.sub(r"^" + re.escape(anchor) + r"\s*", "", first)
    first = re.sub(r"^第\s*\d+\s*章\s*", "", first)
    return first.strip(" ·-—:：") or (anchor or "（无标题）")


def _chapter_number(block: dict) -> int | None:
    """章的编号：**优先用书自己的编号**（锚点 `0` / `第0章 绪论` → 0）。

    ⚠️ 为什么不能一律按出现顺序编号（2026-09-27 实测《数值计算方法》）：
    它的 `绪论` 是**第 0 章**（节号 `0.1` `0.2` `0.3`）。按顺序编号的话它会变成 `CH01`，
    把书里的**第 1 章顶成 `CH02`** —— 交付出去的单元编号跟书对不上。
    """
    raw = str(block.get("anchor") or "").strip()
    if raw.isdigit():
        return int(raw)
    first = str(block.get("normalized_markdown") or block.get("raw_markdown") or "").splitlines()
    matched = re.match(r"^#+\s*第\s*(\d+)\s*[章篇]", (first[0] if first else "").strip())
    return int(matched.group(1)) if matched else None


def build_tree(blocks: list[dict]) -> tuple[list[Node], list[dict]]:
    """把扁平的 block 流还原成 `章 → 节 → 小节` 树。返回 (章节列表, 前置件)。"""
    chapters: list[Node] = []
    front: list[dict] = []          # 第一个章标题之前的内容（封面/目录）
    current: dict[str, Node | None] = {"chapter": None, "section": None, "subsection": None}
    order: dict[str, int] = {"chapter": 0, "section": 0, "subsection": 0}

    for block in blocks:
        kind = str(block.get("block_type"))
        if kind in {"toc", "paragraph_group"} or kind not in {"chapter", "section", "subsection"}:
            if current["chapter"] is None:
                front.append(block)
            else:
                # 没有标题的正文块挂到当前最深的节点上
                target = current["subsection"] or current["section"] or current["chapter"]
                if target is not None:
                    target.blocks.append(block)
            continue

        if kind == "chapter":
            order.update(chapter=order["chapter"] + 1, section=0, subsection=0)
            # 编号优先用书自己的（见 `_chapter_number`）；锚点缺失或撞号才退回顺序编号
            number = _chapter_number(block)
            if number is None or any(node.id == f"CH{number:02d}" for node in chapters):
                number = order["chapter"]
            node = Node(id=f"CH{number:02d}", kind="chapter",
                        anchor=block.get("anchor"), title=_title_of(block),
                        order=order["chapter"], blocks=[block])
            chapters.append(node)
            current.update(chapter=node, section=None, subsection=None)
            continue

        if current["chapter"] is None:      # 没有章标题就先遇到节标题
            order.update(chapter=order["chapter"] + 1, section=0, subsection=0)
            node = Node(id=f"CH{order['chapter']:02d}", kind="chapter", anchor=None,
                        title="（未命名章）", order=order["chapter"])
            chapters.append(node)
            current.update(chapter=node, section=None, subsection=None)

        if kind == "section":
            order.update(section=order["section"] + 1, subsection=0)
            node = Node(id=f"{current['chapter'].id}-S{order['section']:02d}",
                        kind=("exercise" if EXERCISE_TITLE.match(_title_of(block) or "") else "section"),
                        anchor=block.get("anchor"), title=_title_of(block),
                        order=order["section"], blocks=[block])
            current["chapter"].children.append(node)
            current.update(section=node, subsection=None)
            continue

        if kind == "subsection":
            if current["section"] is None:   # 没有节标题就先遇到小节标题
                order.update(section=order["section"] + 1, subsection=0)
                node = Node(id=f"{current['chapter'].id}-S{order['section']:02d}", kind="section",
                            anchor=None, title="（未命名节）", order=order["section"])
                current["chapter"].children.append(node)
                current.update(section=node, subsection=None)
            order["subsection"] += 1
            node = Node(id=f"{current['section'].id}-SS{order['subsection']:02d}", kind="subsection",
                        anchor=block.get("anchor"), title=_title_of(block),
                        order=order["subsection"], blocks=[block])
            current["section"].children.append(node)
            current["subsection"] = node

    return chapters, front


def _filename_for(node: Node) -> str:
    """节点 → 原文文件名：`CH01-S01-SS01` → `ch01_s01_ss01.md`。"""
    return node.id.lower().replace("-", "_") + ".md"


# ── 单元清单（教案块的输入） ─────────────────────────────────────────────
def _has_body(node: Node) -> bool:
    """这个节点**除了标题**之外还有没有正文？

    ⚠️ 判据不能写成 `if node.blocks:`（2026-09-27 实测抓出）：
    节点的 `blocks` 里**第一个就是它自己的标题块**，于是"只有标题"的节点也算"有正文"，
    成了**空单元** —— 实测《数值计算方法》`## §3.1 迭代法`（19 字节，只有标题）
    也进了 `lesson_units`，教案层会拿这 19 个字节去生成讲解（费 token 还容易编）。
    实测这类空单元：数值计算 14/87、线性代数 10/108、复变函数 6/79、数分讲义 0/38。

    两种数据形态都要认（实测都出现过）：
      * 标题块 + 正文块分开（`normalize-source` 的正常形态）→ 看第二个块起有没有内容；
      * 标题与正文在**同一个块**里（有些来源/夹具）→ 首行当标题，其余算正文。
    """
    texts = [str(b.get("raw_markdown", "")).rstrip("\n") for b in node.blocks]
    if len(texts) > 1:
        return any(text.strip() for text in texts[1:])
    if not texts:
        return False
    lines = texts[0].splitlines()
    return bool("\n".join(lines[1:]).strip())


def build_lesson_units(chapters: list[Node]) -> list[dict]:
    """算出教案块真正要逐个处理的单元。

    规则：**有正文的节点就是一个单元**，节下面有小节时节自己的正文算 `section_intro`。
      * 章有导言 → `chapter_intro` 一个单元
      * 节下面有小节 → 每个小节一个单元（节自己的引言另算，作为 `section_intro`）
      * 节下面没有小节 → 这个节自己就是一个单元

    踩过的坑：一开始只收"节/小节"，第 1 章的导言（6 行正文）被漏掉了 ——
    教案块根本不会处理它，等于正文丢了一段。
    第二个坑（2026-09-27）：`if node.blocks` 把**只有标题**的节点也算成有正文（见 `_has_body`）。
    """
    units: list[dict] = []
    for chapter in chapters:
        if EXERCISE_TITLE.match(chapter.title or ''):
            continue
        if _has_body(chapter):
            units.append({
                "unit_id": chapter.id, "level": "chapter_intro",
                "chapter": chapter.id, "section": None,
                "title": chapter.title, "anchor": chapter.anchor,
                "original_text": chapter.original_text,
            })
        for section in chapter.children:
            if EXERCISE_TITLE.match(section.title or ""):
                continue                     # 习题区：不进教案（见 EXERCISE_TITLE 的注释）
            if section.children:
                if _has_body(section):
                    units.append({
                        "unit_id": section.id, "level": "section_intro",
                        "chapter": chapter.id, "section": section.id,
                        "title": section.title, "anchor": section.anchor,
                        "original_text": section.original_text,
                    })
                for subsection in section.children:
                    if EXERCISE_TITLE.match(subsection.title or ''):
                        continue
                    units.append({
                        "unit_id": subsection.id, "level": "subsection",
                        "chapter": chapter.id, "section": section.id,
                        "title": subsection.title, "anchor": subsection.anchor,
                        "original_text": subsection.original_text,
                    })
            elif _has_body(section):
                units.append({
                    "unit_id": section.id, "level": "section",
                    "chapter": chapter.id, "section": section.id,
                    "title": section.title, "anchor": section.anchor,
                    "original_text": section.original_text,
                })
    return units


# ── 图片 ────────────────────────────────────────────────────────────────
def collect_images(blocks: list[dict], chapters: list[Node]) -> list[dict]:
    """从 markdown 图片引用里抽配图索引。

    说明：这里只能抽"markdown 里带 ![]() 的图"。
    PDF 里内嵌的插图要由抽取层（T1.5）按图提取后再补进来，本函数**不会**伪造。
    """
    owner: dict[str, tuple[str, str | None]] = {}
    for chapter in chapters:
        stack = [chapter]
        while stack:
            node = stack.pop()
            for block in node.blocks:
                owner[str(block.get("block_id"))] = (node.id, node.anchor)
            stack.extend(node.children)

    images: list[dict] = []
    seen: dict[str, int] = {}
    for block in blocks:
        text = str(block.get("normalized_markdown") or "")
        for match in IMAGE_REF.finditer(text):
            src = match.group("src")
            node_id, anchor = owner.get(str(block.get("block_id")), (None, None))
            if src in seen:
                images[seen[src]]["occurrences"].append(
                    {"block_id": block.get("block_id"), "unit_id": node_id, "anchor": anchor})
                continue
            seen[src] = len(images)
            images.append({
                "id": f"IMG-{len(images) + 1:04d}",
                "src": src,
                "alt": match.group("alt"),
                "file": None,          # 由 T1.5 提取后回填，未提取时为 null
                "sha256": None,
                "caption": match.group("alt") or None,
                "concepts": [],
                "occurrences": [{"block_id": block.get("block_id"),
                                 "unit_id": node_id, "anchor": anchor}],
            })
    return images


# ── 落盘 ────────────────────────────────────────────────────────────────
def export_parse(normalized_source: str | Path, out_dir: str | Path, *,
                 book_title: str | None = None) -> dict:
    normalized_source = Path(normalized_source)
    if not normalized_source.is_file():
        raise ValueError(f"找不到 normalized-source.jsonl: {normalized_source}")
    normalized_source = normalized_source.resolve()
    out_dir = Path(out_dir)
    text_dir = out_dir / "original_text"
    text_dir.mkdir(parents=True, exist_ok=True)
    source_bytes = normalized_source.read_bytes()
    source_sha = _sha256_bytes(source_bytes)
    blocks = load_blocks(normalized_source)
    chapters, front = build_tree(blocks)

    produced: list[dict] = []
    written_names: set[str] = set()

    def write_text_file(path: Path, content: str) -> None:
        data = content.encode("utf-8")
        path.write_bytes(data)
        written_names.add(path.name)
        produced.append({"path": str(path.relative_to(out_dir)).replace("\\", "/"),
                         "bytes": len(data), "sha256": _sha256_bytes(data),
                         "kind": "original_text"})

    def write_json_file(path: Path, payload: dict) -> None:
        data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        path.write_bytes(data)
        produced.append({"path": str(path.relative_to(out_dir)).replace("\\", "/"),
                         "bytes": len(data), "sha256": _sha256_bytes(data),
                         "kind": path.stem})

    # 1) 原文文件：一个 block 一个文件，内容 = raw_markdown + 换行（逐字节可比）
    def dump_node(node: Node) -> None:
        if node.blocks:
            raw = "\n\n".join(str(b.get("raw_markdown", "")).rstrip("\n") for b in node.blocks)
            name = _filename_for(node)
            write_text_file(text_dir / name, raw + "\n")
            node.original_text = f"original_text/{name}"
        for child in node.children:
            dump_node(child)

    for chapter in chapters:
        dump_node(chapter)

    if front:
        raw = "\n\n".join(str(b.get("raw_markdown", "")).rstrip("\n") for b in front)
        write_text_file(text_dir / "_frontmatter.md", raw + "\n")

    # 1.5) 清掉上一次导出留下的原文文件：章节结构变了（合并/拆开/改层级）之后，
    #      旧文件名会残留在 original_text/ 里，跟 chapters.json 对不上。
    #      只删本层自己产的 .md，不碰别的块的文件（structure.json / figures.json …）。
    stale = sorted(p for p in text_dir.glob("*.md") if p.name not in written_names)
    for path in stale:
        path.unlink()

    # 2) chapters.json
    def node_json(node: Node) -> dict:
        payload = {
            "id": node.id,
            "kind": node.kind,
            "anchor": node.anchor,
            "title": node.title,
            "order": node.order,
            "original_text": node.original_text,
            "block_ids": [b.get("block_id") for b in node.blocks],
            "source_lines": (node.blocks[0].get("source_lines") if node.blocks else None),
            "content_sha256": (node.blocks[0].get("content_sha256") if node.blocks else None),
        }
        # 子节点键**恒定存在**（没有子节点就是空数组）。
        # 契约要形状稳定：消费方不该写 `node.get("subsections", [])` 这种防御代码。
        child_key = "sections" if node.kind == "chapter" else "subsections"
        payload[child_key] = [node_json(child) for child in node.children]
        return payload

    lesson_units = build_lesson_units(chapters)
    chapters_payload = {
        "_meta": _meta("chapters", source_sha, {
            "chapters[].id": "章 id，形如 CH01",
            "chapters[].sections[].id": "节 id，形如 CH01-S01",
            "chapters[].sections[].subsections[].id": "小节 id，形如 CH01-S01-SS01",
            "original_text": "原文文件路径（相对 data/parse/）；为 null 表示这一层没有自己的正文",
            "content_sha256": "该节点正文的哈希，用于确认原文没被改动",
            "lesson_units[]": "教案块要逐个处理的单元清单（取有内容的最深一层）",
        }),
        "book": {"title": book_title or "（未命名）", "source_sha256": source_sha},
        "chapters": [node_json(chapter) for chapter in chapters],
        "lesson_units": lesson_units,
        "frontmatter": ("original_text/_frontmatter.md" if front else None),
    }
    write_json_file(out_dir / "chapters.json", chapters_payload)

    # 3) images.json —— 两种来源合并成一个数组，用 source 字段区分
    #    source="markdown"：markdown 里带 ![]() 的引用（网页教材常见）
    #    source="page"    ：从 PDF 页面提取的插图（整页图 + bbox，任务 T1.5 产出 figures.json）
    images = collect_images(blocks, chapters)
    for item in images:
        item["source"] = "markdown"

    figures_path = out_dir / "figures.json"
    page_figures: list[dict] = []
    if figures_path.is_file():
        payload = json.loads(figures_path.read_text(encoding="utf-8"))
        for item in payload.get("images", []):
            page_figures.append({
                "id": item.get("id"),
                "source": "page",
                "page": item.get("page"),
                "page_image": item.get("page_image"),
                "page_image_sha256": item.get("page_image_sha256"),
                "bbox": item.get("bbox"),
                "bbox_source": item.get("bbox_source"),
                "bbox_picked": item.get("bbox_picked"),
                "confidence": item.get("confidence"),
                "needs_review": item.get("needs_review"),
                "local_bbox": item.get("local_bbox"),
                "model_bbox": item.get("model_bbox"),
                "candidates": item.get("candidates", []),
                "kind": item.get("kind"),
                "caption": item.get("caption"),
                "file": item.get("file"),
                "sha256": item.get("sha256"),
                "concepts": item.get("concepts", []),
                "occurrences": item.get("occurrences", []),
            })

    write_json_file(out_dir / "images.json", {
        "_meta": _meta("images", source_sha, {
            "images[].id": "图片 id；教案正文用 [[FIGURE:IMG-0001]] 引用",
            "images[].source": "markdown=正文里的 ![]() 引用；page=从 PDF 页面提取的插图",
            "images[].page/bbox": "仅 source=page：插图所在页 + 在整页图上的像素框",
            "images[].bbox_source": "local=本地几何检出；model=模型检出；both=两个来源都框到同一张图",
            "images[].confidence": "high=两个来源重叠；medium=只有一个来源；low=两者差得远，建议人工看",
            "images[].needs_review": "true 表示建议人工抽查这个框",
            "images[].candidates": "这张图各来源给的原框，人工抽查时两个都能看",
            "images[].file": "已裁出的单图文件；null 表示展示层按 bbox 从整页图裁即可",
            "images[].concepts": "关联的概念 id（由教案块回填）",
        }),
        "note": ("两种来源：markdown 引用 + PDF 页面提取。"
                 "页面提取的框有置信度分级（见任务基线 T1.5 的实测结论），"
                 "needs_review=true 的框建议抽查。"),
        "sources": {
            "markdown_refs": len(images),
            "page_figures": len(page_figures),
            "figures_json": str(figures_path.name) if figures_path.is_file() else None,
        },
        "images": images + page_figures,
    })

    # 4) tables.json: preserve upstream recognition instead of discarding it.
    source_blocks_path = out_dir / 'source.blocks.json'
    source_blocks = (json.loads(source_blocks_path.read_text(encoding='utf-8'))
                     if source_blocks_path.is_file() else {})
    tables = [dict(asset) for asset in source_blocks.get('assets', [])
              if asset.get('kind') == 'table']
    write_json_file(out_dir / "tables.json", {
        "_meta": {**_meta("tables", source_sha, {
            "tables[]": "MinerU 来源表格索引；保留原始 HTML、裁图和块/页/框/行坐标",
        }), 'source_blocks_sha256': (_sha256_file(source_blocks_path)
                                     if source_blocks_path.is_file() else None)},
        "note": ("来源表格完整转存；题目归属见 exercises.json。" if source_blocks_path.is_file()
                 else "未提供 source.blocks.json 来源表格；空数组不代表原书没有表格。"),
        "tables": tables,
    })

    # 5) manifest.json（最后写，把自己排除在 produced 之外）
    manifest = {
        "_meta": _meta("manifest", source_sha, {
            "source": "本次导出读的输入文件及其哈希",
            "produced[]": "本次导出的每个产物（路径/字节/sha256），可用于完整性校验",
            "counts": "结构规模统计",
            "removed_stale[]": "本次清掉的过期原文文件（上一次导出留下、这一版结构里已经没有的）",
        }),
        "source": {"path": str(normalized_source), "sha256": source_sha,
                   "bytes": len(source_bytes)},
        "counts": {
            "blocks_total": len(blocks),
            "chapters": len(chapters),
            "sections": sum(len(c.children) for c in chapters),
            "subsections": sum(len(s.children) for c in chapters for s in c.children),
            "lesson_units": len(lesson_units),
            "original_text_files": sum(1 for p in produced if p["kind"] == "original_text"),
            "images": len(images) + len(page_figures),
            "tables": len(tables),
        },
        "produced": produced,
        "removed_stale": [p.name for p in stale],
    }
    write_json_file(out_dir / "manifest.json", manifest)
    return {"chapters": str(out_dir / "chapters.json"),
            "images": str(out_dir / "images.json"),
            "tables": str(out_dir / "tables.json"),
            "manifest": str(out_dir / "manifest.json"),
            "original_text_dir": str(text_dir),
            "removed_stale": manifest["removed_stale"],
            "counts": manifest["counts"]}


__all__ = ["export_parse", "load_blocks", "build_tree", "build_lesson_units",
           "collect_images", "Node", "SCHEMA_VERSION", "GENERATOR"]
