#!/usr/bin/env python3
"""把**一节教案**导成能直接读的 Markdown（0 token，纯格式化）。

给谁看：想看"这一步讲解到底长什么样"的人（正文、逐条详解的分步与依据、配图、批注、校验）。

用法：

```bash
python tools/show_lesson.py data/lesson/<书>/lessons/<单元>.json [--out <文件.md>]
```
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

KIND_CN = {"definition": "定义", "theorem": "定理", "law": "定律", "property": "性质",
           "proposition": "命题", "corollary": "推论", "lemma": "引理", "formula": "公式",
           "concept": "概念"}
NOTE_CN = {"note": "边注", "derive": "推导", "pitfall": "易错", "intuition": "直觉"}


def render(lesson: dict) -> str:
    body = str(lesson.get("body_markdown") or "")
    ex = lesson.get("explainables") or []
    figs = lesson.get("figures") or []
    out: list[str] = [
        f"# {lesson.get('title') or lesson.get('unit_id')} · 教案",
        "",
        f"单元 `{lesson.get('unit_id')}` ｜ 锚点 {lesson.get('anchor')} ｜ "
        f"正文 **{len(body)}** 字 ｜ 详解 **{len(ex)}** 条 ｜ 图 {len(figs)} 张 "
        f"｜ 例题 {len(lesson.get('examples') or [])} ｜ 批注 {len(lesson.get('annotations') or [])}",
        "",
    ]
    if lesson.get("one_line"):
        out += [f"> **一句话**：{lesson['one_line']}", ""]
    checks = lesson.get("checks") or {}
    if checks:
        out += [f"> 校验：硬问题 **{len(checks.get('problems') or [])}** ｜ "
                f"提醒 {len(checks.get('warnings') or [])}", ""]

    out += ["---", "", "## 正文（读者主要读的就是它）", "", body, ""]

    out += ["---", "", f"## 详解（{len(ex)} 条：每条都有分步与依据）", ""]
    for item in ex:
        kind = KIND_CN.get(str(item.get("kind")), str(item.get("kind")))
        own = f" `{item.get('concept_id')}`" if item.get("concept_id") else ""
        out += [f"### 【{kind}】{item.get('name')}{own}", ""]
        if item.get("what"):
            out += [f"**它是什么**：{item['what']}", ""]
        if item.get("why"):
            out += [f"**为什么在这里用**：{item['why']}", ""]
        if item.get("warmup"):
            out += [f"> **先用一个简单情形看明白**：{item['warmup']}", ""]
        steps = item.get("walkthrough") or []
        if steps:
            out.append("| # | 这一步在说什么 | 依据 |")
            out.append("|---|---|---|")
            for step in steps:
                out.append("| %s | %s | %s |" % (
                    step.get("step"), step.get("claim") or "", step.get("basis") or ""))
            out.append("")
        if item.get("figure"):
            out += [f"配图：`{item['figure']}`", ""]
        if item.get("markdown"):
            out += [item["markdown"], ""]

    if lesson.get("examples"):
        out += ["---", "", "## 例题", ""]
        for row in lesson["examples"]:
            out += [f"### {row.get('label')}", "", f"> {row.get('statement')}", ""]
            if row.get("thought"):
                out += [f"**思路**：{row['thought']}", ""]
            if row.get("warmup"):
                out += [f"> **先用一个简单情形看明白**：{row['warmup']}", ""]
            for step in row.get("steps") or []:
                out.append(f"{step.get('no')}. {step.get('markdown')}")
                if step.get("basis"):
                    out.append(f"    - 依据：{step['basis']}")
            out.append("")

    if figs or lesson.get("diagrams") or lesson.get("figure_requests"):
        out += ["---", "", "## 图", ""]
        for fig in figs:
            served = fig.get("concept_id") or fig.get("serves") or "**没声明服务对象**"
            by = f"（程序按{'图题' if fig.get('bound_by') == 'caption' else '索引'}绑定）" \
                if fig.get("bound_by") else ""
            out.append(f"- `{fig.get('id')}` → 服务 {served} {by}")
        for dia in lesson.get("diagrams") or []:
            labels = " → ".join(str(s.get("label") or s.get("math") or "")[:16]
                                for s in (dia.get("steps") or []))
            out.append(f"- 流程图 `{dia.get('id')}`（{dia.get('template')}）：{labels}")
        for req in lesson.get("figure_requests") or []:
            out.append(f"- 待补图：**{req.get('what')}**（{req.get('where')}）"
                       f"｜服务于 `{req.get('concept_id')}`｜原文摘录：{req.get('quote')}")
        out.append("")

    if lesson.get("annotations"):
        out += ["---", "", "## 批注", ""]
        for note in lesson["annotations"]:
            out.append(f"- **[{NOTE_CN.get(note.get('kind'), note.get('kind'))}] "
                       f"{note.get('title') or ''}**：{note.get('markdown')}")
        out.append("")

    if checks:
        out += ["---", "", "## 校验明细", ""]
        for row in checks.get("problems") or []:
            out.append(f"- ✗ {row}")
        for row in checks.get("warnings") or []:
            out.append(f"- ! {row}")
        out.append("")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="教案 → 人能读的 Markdown")
    ap.add_argument("lesson", help="lessons/<单元>.json")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    src = Path(args.lesson)
    if not src.is_absolute():
        src = ROOT / src
    lesson = json.loads(src.read_text(encoding="utf-8"))
    text = render(lesson)
    book = src.parent.parent.name
    out = Path(args.out) if args.out else (
        ROOT.parent / "文档" / "02-记录与报告" / f"教案-{book}-{lesson.get('unit_id')}.md")
    out.write_text(text, encoding="utf-8")
    print(f"✓ {out}（{len(text):,} 字）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
