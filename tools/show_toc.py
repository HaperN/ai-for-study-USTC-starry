#!/usr/bin/env python3
"""把**目录表**导成能直接读的 Markdown（0 token，纯格式化）。

## 导什么

1. **结论与规模**：目录层给的 verdict（能不能往下走）+ 章/节/层级统计；
2. **整棵目录树**：按 `parent` 挂链缩进展开（编号 + 标题），这是"书的骨架"；
3. **模型复核改了什么**：`toc.v2.json` 的 `revisions`（改前 → 改后 + 依据）；
4. **不确定 / 待裁决 / 目录有正文缺**：三张账本，一条不许藏；
5. **花费**：复核用了几张图、多少 token。

## 用法

```bash
python tools/show_toc.py data/parse/<书> [--out <文件.md>]
```
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _tree_lines(nodes: list[dict], *, indent: str = "") -> list[str]:
    """按 `parent` 挂链把目录摊成缩进列表。"""
    children: dict[str | None, list[dict]] = {}
    for node in nodes:
        children.setdefault(node.get("parent"), []).append(node)
    out: list[str] = []

    def walk(parent, depth: int) -> None:
        for node in children.get(parent, []):
            number = str(node.get("number") or "").strip()
            title = str(node.get("title") or "").strip()
            kind = str(node.get("kind") or "")
            mark = ""
            if kind == "non-teaching":
                mark = "  ← 非正文（前言/附录/习题集）"
            if node.get("confirmed") is False:
                mark += "  ← 不确定"
            label = f"**{number}** {title}".strip() if number else title
            out.append(f"{'  ' * depth}- {label}{mark}")
            walk(node.get("toc_id"), depth + 1)

    walk(None, 0)
    return out


def render(parse_dir: Path, title: str = "") -> str:
    toc = _load(parse_dir / "toc.v2.json") or {}
    preview = _load(parse_dir / "toc.preview.json") or {}
    body = _load(parse_dir / "body.json") or {}
    review = _load(parse_dir / "toc.review.json") or {}
    chapters = _load(parse_dir / "chapters.json") or {}
    book_obj = chapters.get("book")
    book = title or ((book_obj.get("title") if isinstance(book_obj, dict) else book_obj)
                     or toc.get("book_title") or parse_dir.name)
    nodes = toc.get("nodes") or []
    meta = toc.get("_meta") or {}
    shape = preview.get("shape") or {}

    out: list[str] = [f"# 目录表 · {book}", ""]
    if preview.get("verdict"):
        out += [f"> **结论**：{preview['verdict']}", ""]
    out += [
        f"规模：**{len(nodes)}** 个节点（章 {shape.get('chapters', '?')} ｜ 节 "
        f"{shape.get('sections', '?')} ｜ 非正文 {shape.get('non_teaching', '?')}）"
        f"｜ 层级 {shape.get('levels', '?')}",
        "",
        f"来源：{meta.get('sources_used')}｜confidence `{meta.get('confidence')}`"
        + (f"｜复核已应用 {meta['review_state']['reviewed']} 条"
           if meta.get("review_state") else "｜**复核未应用**"),
        "",
        "---",
        "",
        "## 目录树（书的骨架）",
        "",
    ]
    out += _tree_lines(nodes)

    # `revisions` 会**跨批次累加**（同一条被应用过两次就出现两遍）——
    # 按 `toc_id` 去重、保留最后一条；条数以 `review_state.reviewed` 为准。
    revisions = toc.get("revisions") or []
    by_id: dict = {}
    for batch in revisions:
        for row in batch.get("decisions") or []:
            by_id[str(row.get("toc_id") or len(by_id))] = row
    changed = list(by_id.values())
    applied = int(((meta.get("review_state") or {}).get("reviewed")) or len(changed))
    out += ["", "---", "", f"## 模型视觉复核改了什么（应用 {applied} 条，去重后 {len(changed)} 处）", ""]
    if not changed:
        out += ["（没有已应用的复核结论）"]
    for row in changed:
        before = row.get("before") or {}
        after = row.get("after") or {}
        out += [f"- **{after.get('number') or ''} {after.get('title') or ''}**".strip()]
        for field in ("title", "level", "number"):
            if before.get(field) != after.get(field):
                out.append(f"    - {field}：`{before.get(field)}` → `{after.get(field)}`")
        if row.get("reason"):
            out.append(f"    - 依据：{row['reason']}")
        out.append(f"    - 把握：`{row.get('confidence')}`")

    out += ["", "---", "", "## 账本（不确定的东西都在这儿）", ""]
    pending = [d for d in (toc.get("disagreements") or []) if d.get("arbiter") == "pending"]
    unconfirmed = [n for n in nodes if n.get("confirmed") is False]
    empty = body.get("empty_nodes") or []
    out.append(f"### 待裁决（{len(pending)}）")
    out += [f"- {d.get('why') or d.get('kind')}" for d in pending] or ["- （无）"]
    out += ["", f"### 标了不确定（{len(unconfirmed)}）"]
    out += [f"- `{n.get('number') or ''}` {n.get('title')}" for n in unconfirmed] or ["- （无）"]
    out += ["", f"### 目录里有、正文里没找到（{len(empty)}）"]
    out += [f"- {row.get('number') or ''} {row.get('title')} ｜ {row.get('reason')}"
            for row in empty] or ["- （无）"]

    cost = preview.get("cost") or {}
    if cost:
        out += ["", "---", "", "## 花费", "",
                f"- 复核：{cost.get('model_calls')} 次调用 ｜ {cost.get('images')} 张图 ｜ "
                f"{(cost.get('tokens') or {}).get('total', 0):,} token"
                + ("（降级：只送了可疑项所在页）" if cost.get("downgraded") else "")]
    if review.get("_meta", {}).get("tokens", {}).get("total"):
        out.append(f"- 复核产物记的用量：{review['_meta']['tokens']['total']:,} token")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="目录表 → 人能读的 Markdown")
    ap.add_argument("parse_dir")
    ap.add_argument("--out")
    ap.add_argument("--title", help="显示用的书名（清空重建后产物里的书名可能退化成目录名）")
    args = ap.parse_args(argv)
    parse_dir = Path(args.parse_dir)
    if not parse_dir.is_absolute():
        parse_dir = ROOT / parse_dir
    if not (parse_dir / "toc.v2.json").is_file():
        print(f"✗ 没有 {parse_dir/'toc.v2.json'}", file=sys.stderr)
        return 2
    text = render(parse_dir, title=args.title or "")
    book = args.title or (_load(parse_dir / "toc.v2.json") or {}).get("book_title") or parse_dir.name
    out = Path(args.out) if args.out else (
        ROOT.parent / "文档" / "02-记录与报告" / f"目录表-{book}.md")
    out.write_text(text, encoding="utf-8")
    print(f"✓ {out}（{len(text):,} 字）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
