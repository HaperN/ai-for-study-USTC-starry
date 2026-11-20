#!/usr/bin/env python3
"""把 `concepts.json` 导成**人能直接读**的 Markdown（0 token，纯格式化）。

## 为什么要有它

`concepts.json` 是给程序吃的（字段多、嵌套 occurrences/source_quotes），
直接打开很难看。这个小工具把它按**书的顺序**（章 → 节）摊平成一页文档：
每个概念一段 —— 名字、类型、一句话、讲解、原文依据、出现在哪些节。

## 用法

```bash
python tools/show_concepts.py data/parse/<书> [--out <文件.md>] [--limit 20]
python tools/show_concepts.py data/parse/<书> --stats     # 只看统计
```
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
#: 与后端 `extract_concepts.TYPE_LABELS` **同一套**（2026-09-29 类型从 4 类扩到 7 类）
TYPE_CN = {"definition": "定义", "theorem": "定理", "law": "定律",
           "property": "性质", "proposition": "命题", "corollary": "推论",
           "lemma": "引理", "formula": "公式"}


def load(parse_dir: Path) -> tuple[dict, dict]:
    concepts = json.loads((parse_dir / "concepts.json").read_text(encoding="utf-8"))
    chapters_path = parse_dir / "chapters.json"
    chapters = (json.loads(chapters_path.read_text(encoding="utf-8"))
                if chapters_path.is_file() else {})
    return concepts, chapters


def _book_title(chapters: dict, parse_dir: Path) -> str:
    """书名：`chapters.json` 的 `book` 可能是字符串也可能是对象，都认。"""
    book = chapters.get("book")
    if isinstance(book, dict) and book.get("title"):
        return str(book["title"])
    if isinstance(book, str) and book.strip():
        return book.strip()
    return parse_dir.name


def unit_titles(chapters: dict) -> dict[str, str]:
    return {str(u["unit_id"]): str(u.get("title") or "") for u in chapters.get("lesson_units") or []}


def stats(concepts: dict) -> str:
    counts = concepts.get("counts") or {}
    rows = {k: v for k, v in counts.items()
            if k in ("concepts_total", "definitions", "theorems", "propositions", "corollaries",
                     "units_with_concepts", "units_total", "multi_occurrence")}
    return json.dumps(rows, ensure_ascii=False)


def render(parse_dir: Path, *, limit: int | None = None, title: str = "") -> str:
    concepts, chapters = load(parse_dir)
    titles = unit_titles(chapters)
    book = title or _book_title(chapters, parse_dir)
    rows = concepts.get("concepts") or []
    counts = concepts.get("counts") or {}
    by_type = Counter(TYPE_CN.get(str(c.get("type")), str(c.get("type"))) for c in rows)

    # 按"第一次出现的那一节"归组，节序按 chapters.json 的顺序（书的顺序，不是字母序）
    order = [str(u["unit_id"]) for u in chapters.get("lesson_units") or []]
    rank = {uid: i for i, uid in enumerate(order)}
    grouped: dict[str, list[dict]] = defaultdict(list)
    for c in rows:
        occ = c.get("occurrences") or []
        first = str((occ[0] or {}).get("unit_id") or "（无出处）")
        grouped[first].append(c)

    out: list[str] = []
    out.append(f"# 概念表 · {book}\n")
    out.append(f"共 **{len(rows)}** 张卡 ｜ " + "、".join(f"{k} {v}" for k, v in by_type.most_common())
               + f" ｜ 覆盖 {counts.get('units_with_concepts')}/{counts.get('units_total')} 个单元"
               + f"（另 {counts.get('multi_occurrence', 0)} 张在多节出现）\n")
    out.append("> **这一版只有「识别」**：名字 / 类型 / 原文逐字表述 / 出现在哪些节。\n"
               "> 给初学者的解释与配图**由教案阶段产出**（2026-09-29 定案：解释只有一个作者）。\n")
    out.append("\n---\n")

    emitted = 0
    for unit_id in sorted(grouped, key=lambda u: (rank.get(u, 10 ** 6), u)):
        cards = grouped[unit_id]
        out.append(f"\n## {unit_id}｜{titles.get(unit_id, '')}（{len(cards)} 张）\n")
        for c in cards:
            emitted += 1
            out.append(f"\n### 【{TYPE_CN.get(str(c.get('type')), c.get('type'))}】{c.get('name')}"
                       f"  `{c.get('id')}`")
            if c.get("name_note"):
                out.append(f"\n*名字里剥下来的括注：{c['name_note']}*")
            if c.get("formal"):
                out.append(f"\n> 原文依据（逐字）：{c['formal']}")
            occ = c.get("occurrences") or []
            if len(occ) > 1:
                out.append("\n*也出现在：" + "、".join(
                    f"{o.get('unit_id')}（{o.get('title')}）" for o in occ[1:]) + "*")
            if limit and emitted >= limit:
                out.append(f"\n\n---\n\n（只显示前 {limit} 张，全量见文件）")
                return "\n".join(out)
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="概念表 → 人能读的 Markdown")
    ap.add_argument("parse_dir")
    ap.add_argument("--out", help="输出文件（默认写到 文档/02-记录与报告/概念表-<书>.md）")
    ap.add_argument("--limit", type=int, help="只导前 N 张（预览用）")
    ap.add_argument("--stats", action="store_true", help="只打印统计")
    ap.add_argument("--title", help="显示用的书名")
    args = ap.parse_args(argv)

    parse_dir = Path(args.parse_dir)
    if not parse_dir.is_absolute():
        parse_dir = ROOT / parse_dir
    if not (parse_dir / "concepts.json").is_file():
        print(f"✗ 没有 {parse_dir/'concepts.json'}（先跑 blocks.parse.concepts.cli）", file=sys.stderr)
        return 2

    concepts, chapters = load(parse_dir)
    if args.stats:
        print(f"  {parse_dir.name}: {stats(concepts)}")
        return 0

    text = render(parse_dir, limit=args.limit, title=args.title or "")
    book = args.title or _book_title(chapters, parse_dir)
    out = Path(args.out) if args.out else (
        ROOT.parent / "文档" / "02-记录与报告" / f"概念表-{book}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"✓ {out}（{len(text):,} 字）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
