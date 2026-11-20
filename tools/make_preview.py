"""把解析块（阶段一）的 JSON 产物摊成给人看的文件。

    python3 tools/make_preview.py <parse_dir> [--out <预览目录>]

输入：解析块输出目录（含 chapters.json / original_text/ / images.json / concepts.json）
输出（默认 <parse_dir>/../预览-<书名>/）：
    00-先看这里.txt          这一份是什么、怎么读
    01-章节结构.txt          整本书的目录树 + 每个教案单元的 id
    02-概念表.md             概念清单（类型/名称/在哪一节/简要解释）
    03-图片清单.md           插图位置清单
    插图/IMG-xxxx.png        按 bbox 从整页图裁出来的单张插图
    样张-原文.md             抽两节原文看格式
纯本地生成，不调模型、不改任何输入文件。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def load(path: Path):
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def tree_text(chapters: dict) -> str:
    lines: list[str] = []
    book = chapters.get("book", {})
    lines.append(f"书名：{book.get('title')}")
    lines.append(f"原文指纹 sha256：{book.get('source_sha256')}")
    counts = {
        "章节": len(chapters["chapters"]),
        "节": sum(len(c["sections"]) for c in chapters["chapters"]),
        "小节": sum(len(s["subsections"]) for c in chapters["chapters"] for s in c["sections"]),
        "教案单元": len(chapters["lesson_units"]),
    }
    lines.append("规模：" + "，".join(f"{k} {v}" for k, v in counts.items()))
    lines.append("")
    lines.append("目录树（方括号里是节点 id；有正文的节点同时就是教案单元 id，")
    lines.append("下一步教案块就按这个一个一个生成）")
    lines.append("=" * 72)
    for c in chapters["chapters"]:
        lines.append(f"\n第{c['anchor']}章 {c['title']}    [{c['id']}]")
        for s in c["sections"]:
            lines.append(f"  §{s['anchor']} {s['title']}    [{s['id']}]")
            for ss in s["subsections"]:
                lines.append(f"    {ss['anchor']} {ss['title']}    [{ss['id']}]")
    return "\n".join(lines) + "\n"


def _cell(text: str) -> str:
    """表格单元格里 `|` 必须转义，不能换成别的字符（公式里有 `|a|` 这种）。"""
    return str(text).replace("|", "\\|").replace("\n", " ")


def _first_unit(concept: dict) -> str:
    occurrences = concept.get("occurrences") or []
    return occurrences[0].get("unit_id", "") if occurrences else ""


def concepts_text(concepts: dict | None) -> str:
    if concepts is None:
        return "（concepts.json 还没生成）\n"
    rows = concepts.get("concepts", [])
    lines = [f"共 {len(rows)} 个概念（只收定义和定理）", ""]
    lines.append("| id | 类型 | 名称 | 所属小节 | 简要解释 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for c in rows:
        brief = _cell(c.get("brief") or "")
        where = c.get("unit_id") or c.get("unit") or _first_unit(c)
        lines.append(
            f"| {c.get('id')} | {c.get('type')} | {_cell(c.get('name'))} "
            f"| {where} | {brief} |")
    lines.append("")
    lines.append("同一概念跨小节时只解释一次，其它小节只记出现位置（见 concepts.json 的 occurrences）。")
    return "\n".join(lines) + "\n"


def images_text(images: dict | None) -> str:
    if images is None:
        return "（images.json 还没生成）\n"
    rows = images.get("images", [])
    lines = [f"共 {len(rows)} 张插图", "",
             "bbox 是插图在整页图上的像素框（左上右下）；裁好的单图在 插图/ 目录。", "",
             "| id | 页码 | 说明 | 框来源 | 可信度 | 建议人工看 | 裁图 |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for x in rows:
        cap = _cell(x.get("caption") or "")
        crop = f"插图/{x['id']}.png" if x.get("_cropped") else ""
        lines.append(
            f"| {x.get('id')} | 第{x.get('page')}页 | {cap} | {x.get('bbox_source')} "
            f"| {x.get('confidence')} | {'是' if x.get('needs_review') else '否'} | {crop} |")
    lines.append("")
    lines.append("框来源：local=本地按留白几何算的；model=模型看图给的；")
    lines.append("        both=两个来源都框到了这一张图（不代表两个框一致）。")
    lines.append("可信度：high=两个框重叠；medium=只有一个来源；low=两个框差得远。")
    lines.append("medium/low 都会标\"建议人工看\"——没有客观答案的地方不编造，留给人抽查。")
    return "\n".join(lines) + "\n"


def crop_figures(parse_dir: Path, out_dir: Path, images: dict | None) -> None:
    if images is None:
        return
    from PIL import Image
    target = out_dir / "插图"
    for x in images.get("images", []):
        if x.get("source") != "page" or not x.get("bbox"):
            continue
        rel = x.get("page_image")
        if not rel:
            continue
        page_png = parse_dir / rel
        if not page_png.is_file():
            continue
        x0, y0, x1, y1 = x["bbox"]
        with Image.open(page_png) as im:
            w, h = im.size
            pad = 6
            box = (max(0, int(x0) - pad), max(0, int(y0) - pad),
                   min(w, int(x1) + pad), min(h, int(y1) + pad))
            target.mkdir(parents=True, exist_ok=True)
            im.crop(box).save(target / f"{x['id']}.png")
        x["_cropped"] = True


def render_placeholders(text: str, names: dict[str, str], captions: dict[str, str]) -> str:
    """把占位符换成读者会看到的样子，方便人眼检查（渲染层正式实现另说）。"""
    def concept(match):
        ref = match.group(2).strip()
        display = (match.group(3) or "").strip() or names.get(ref, ref)
        return f"**{display}**"

    def figure(match):
        ref = match.group(2).strip()
        return f"[插图 {ref}：{captions.get(ref, '')}]"

    text = re.sub(r"\[\[(CONCEPT)\s*:\s*([^\]|]*)\s*(?:\|\s*([^\]]*?)\s*)?\]\]",
                  concept, text)
    return re.sub(r"\[\[(FIGURE)\s*:\s*([^\]|]*)\s*(?:\|\s*([^\]]*?)\s*)?\]\]",
                  figure, text)


def default_lesson_dir(parse_dir: Path) -> Path:
    """默认从 data/parse/<书名> 推出 data/lesson/<书名>。"""
    parts = list(parse_dir.parts)
    if "parse" in parts:
        parts[parts.index("parse")] = "lesson"
        return Path(*parts)
    return parse_dir.parent / "lesson" / parse_dir.name


def lessons_text(parse_dir: Path, out_dir: Path, lesson_dir: Path | None = None) -> tuple[int, list[dict]]:
    """把教案块的讲解摊成人看的：一节一个文件 + 一个总览。"""
    lesson_dir = lesson_dir or default_lesson_dir(parse_dir)
    lessons_dir = lesson_dir / "lessons"
    if not lessons_dir.is_dir():
        return 0, []
    concepts = load(parse_dir / "concepts.json") or {}
    names = {c["id"]: c.get("name") or c["id"] for c in concepts.get("concepts", [])}
    images = load(parse_dir / "images.json") or {}
    captions = {i["id"]: (i.get("caption") or "") for i in images.get("images", [])}
    index = load(lesson_dir / "lessons.index.json") or {}
    rows = {r["unit_id"]: r for r in index.get("units", [])}

    target = out_dir / "教案"
    target.mkdir(parents=True, exist_ok=True)
    files = sorted(lessons_dir.glob("*.json"))
    overview = ["# 讲解总览", "",
                f"共 {len(files)} 节。原文一字未改；下面是模型新写的讲解。", "",
                "| 单元 | 标题 | 字数/原文 | 概念链接 | 状态 |", "| --- | --- | --- | --- | --- |"]
    made = 0
    for path in files:
        lesson = json.loads(path.read_text(encoding="utf-8"))
        unit_id = lesson["unit_id"]
        stats = (rows.get(unit_id) or {}).get("stats") or {}
        status = (rows.get(unit_id) or {}).get("status", "?")
        overview.append(
            f"| {unit_id} | {lesson.get('title')} | "
            f"{stats.get('讲解字数', '?')}/{stats.get('原文字数', '?')} | "
            f"{len(lesson.get('concepts') or [])} | {'✅' if status == 'done' else '⚠ ' + status} |")
        body = [f"# {lesson.get('anchor')} {lesson.get('title')}", "",
                f"单元 id `{unit_id}`　原文文件 `{lesson.get('original_text')}`", "",
                f"**一句话小结**：{lesson.get('one_line')}", "",
                "## 讲解（读者看到的样式，概念已换成名字）", "",
                render_placeholders(lesson.get("explanation_markdown") or "", names, captions), ""]
        if lesson.get("corrections"):
            body += ["## 讲解改掉的原文公式（原文本身没动，只登记）", "",
                     "| 原文 | 讲解 | 理由 |", "| --- | --- | --- |"]
            body += [f"| `{c.get('原文')}` | `{c.get('讲解')}` | {c.get('理由')} |"
                     for c in lesson["corrections"]]
            body.append("")
        if lesson.get("checks", {}).get("warnings"):
            body += ["## 校验提醒", ""] + [f"* {w}" for w in lesson["checks"]["warnings"]] + [""]
        original_path = parse_dir / (lesson.get("original_text") or "")
        if original_path.is_file():
            body += ["## 本节原文（一字未改，供对照）", "",
                     original_path.read_text(encoding="utf-8")]
        (target / f"{unit_id}.md").write_text("\n".join(body), encoding="utf-8")
        made += 1
    (target / "总览.md").write_text("\n".join(overview) + "\n", encoding="utf-8")
    return made, list(rows.values())


def sample_text(parse_dir: Path, chapters: dict, out_dir: Path, how_many: int = 2) -> None:
    units = chapters.get("lesson_units", [])[:how_many]
    parts = ["（原文一字未改，只有标题被规整成 markdown 井号；教案块读的就是这些文件）", ""]
    for u in units:
        rel = u.get("original_text") or u.get("text_path")
        if not rel:
            continue
        p = parse_dir / rel
        if not p.is_file():
            continue
        parts.append("=" * 72)
        parts.append(f"单元 {u['unit_id']}  {u.get('title')}   文件：{rel}")
        parts.append("=" * 72)
        parts.append(p.read_text(encoding="utf-8"))
    (out_dir / "样张-原文.md").write_text("\n".join(parts), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="解析块产物 → 人眼可看的预览")
    ap.add_argument("parse_dir")
    ap.add_argument("--out")
    ap.add_argument("--lessons", help="教案块输出目录（默认从 parse 目录推 data/lesson/<书名>）")
    args = ap.parse_args(argv)

    parse_dir = Path(args.parse_dir).resolve()
    chapters = load(parse_dir / "chapters.json")
    if chapters is None:
        print(f"没找到 {parse_dir/'chapters.json'}，先跑解析块。", file=sys.stderr)
        return 2
    title = (chapters.get("book") or {}).get("title") or parse_dir.name
    out_dir = Path(args.out).resolve() if args.out else parse_dir.parent / f"预览-{title}"
    out_dir.mkdir(parents=True, exist_ok=True)

    images = load(parse_dir / "images.json")
    concepts = load(parse_dir / "concepts.json")
    crop_figures(parse_dir, out_dir, images)
    lessons_made, _rows = lessons_text(
        parse_dir, out_dir, Path(args.lessons).resolve() if args.lessons else None)

    (out_dir / "01-章节结构.txt").write_text(tree_text(chapters), encoding="utf-8")
    (out_dir / "02-概念表.md").write_text(concepts_text(concepts), encoding="utf-8")
    (out_dir / "03-图片清单.md").write_text(images_text(images), encoding="utf-8")
    sample_text(parse_dir, chapters, out_dir)

    (out_dir / "00-先看这里.txt").write_text(
        f"""这是解析块的产物预览（{title}），给人看的版本。
原始数据在：{parse_dir}
    chapters.json   书的目录结构 + 教案单元清单
    original_text/  每一节一段原文，一字未改
    concepts.json   概念表（定义/定理的简要解释、正式表述、出处）
    images.json     插图位置清单（页码 + 框）
    manifest.json   所有产物的指纹，用来确认没被改过

这一份里的文件：
    01-章节结构.txt   目录树，方括号里是教案单元 id
    02-概念表.md      概念清单
    03-图片清单.md    插图清单，裁好的单图在 插图/
    样张-原文.md      随便抽两节原文，看格式
    教案/             教案块的讲解（一节一个文件，占位符已换成读者看到的样子）
""", encoding="utf-8")

    print(json.dumps({
        "预览目录": str(out_dir),
        "章节": len(chapters["chapters"]),
        "教案单元": len(chapters["lesson_units"]),
        "概念": len((concepts or {}).get("concepts", [])) if concepts else None,
        "插图": len((images or {}).get("images", [])) if images else None,
        "讲解": lessons_made or "（还没跑教案块）",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
