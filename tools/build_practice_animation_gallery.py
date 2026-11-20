"""Build the portable animation index without loading the complete textbook in it.

The delivery builder already supplies the shared browser runtimes in ``assets``.
This module only writes the gallery page and its small textbook-animation dataset.
"""
from __future__ import annotations

import argparse
import sys
import html
import json
from pathlib import Path
import re
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from aiforstudy.practice.media import (
    public_media_display, public_media_path, public_media_playback, public_media_resources,
)


def _assignment(path: Path, name: str, fallback):
    if not path.is_file():
        return fallback
    text = path.read_text(encoding="utf-8")
    match = re.search(r"\bwindow\." + re.escape(name) + r"\s*=\s*", text)
    if match is None:
        raise ValueError(f"Missing window.{name} assignment in {path}")
    try:
        value, end = json.JSONDecoder().raw_decode(text[match.end():])
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON payload in {path}") from error
    if text[match.end() + end:].strip() not in ("", ";"):
        raise ValueError(f"Unexpected executable suffix in {path}")
    return value


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _local_asset(root, path, kind):
    if not public_media_path(path, kind):
        return False
    file = (root / path).resolve()
    return file.is_relative_to(root.resolve()) and file.is_file()


def _first_text(*values: object) -> str:
    return next((value for value in values if isinstance(value, str) and value.strip()), "")


def _textbook_records(data: dict, book: Path) -> list[dict]:
    records = []
    purposes = {(row.get("unit_id"), row.get("id")): row
                for row in data.get("media_purposes", {}).get("items", [])}
    for unit_id, animations in (data.get("animations") or {}).items():
        unit = (data.get("units") or {}).get(unit_id, {})
        plans = (unit.get("study_lesson") or {}).get("media", [])
        for animation in animations if isinstance(animations, list) else [animations]:
            plan = next((p for p in plans if p.get("request_id") == animation.get("request_id")), {})
            purpose = purposes.get((unit_id, plan.get("id")), {})
            record = public_media_display(animation)
            base = animation.get("base")
            if isinstance(base, str) and base.startswith("assets/") and public_media_path(base.rstrip("/")):
                record["base"] = base.rstrip("/") + "/"
                record["resources"] = {
                    kind: row for kind, row in public_media_resources(animation.get("resources", {})).items()
                    if _local_asset(book / "assets", (record["base"] + row["relative_path"])[len("assets/"):], kind)
                }
            playback = public_media_playback(animation.get("playback"))
            if playback and all(
                path.startswith("assets/") and _local_asset(book / "assets", path[len("assets/"):], kind)
                for path, kind in ((playback["video_src"], "picture"), (playback["audio"]["src"], "audio"))
            ):
                record["playback"] = playback
            unit_title = _first_text(unit.get("title"), unit_id, "教材单元")
            record.update({
                "id": "textbook-" + _first_text(plan.get("id"), record.get("request_id"), unit_id),
                "kind": "animation", "origin": "textbook", "asset_root": "教材",
                "title": _first_text(purpose.get("title"), plan.get("title"), record.get("title"), unit_title),
                "purpose": _first_text(purpose.get("purpose_markdown"), plan.get("purpose"),
                                       "结合教材对应段落观察本动画的步骤与变化。"),
                "unit_title": unit_title,
                "textbook_href": "教材/index.html#/" + quote(unit_id, safe=""),
            })
            records.append(record)
    return records


def build_gallery(out: Path | str, *, practice_only: bool = False) -> dict:
    """Write ``动画目录.html``; a paper-only bundle never loads textbook data."""
    out = Path(out)
    assets = out / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    practice_path = assets / "practice-media-data.js"
    practice = _assignment(practice_path, "PRACTICE_MEDIA", [])
    if not isinstance(practice, list):
        raise ValueError("window.PRACTICE_MEDIA must be an array")
    for item in practice:
        ident = item["id"]
        base = f"practice-media/{ident}/"
        resources = public_media_resources(item.get("resources", {}))
        public = {**public_media_display(item), "base": base, "resources": resources}
        playback = public_media_playback(item.get("playback"))
        if playback:
            public["playback"] = playback
        if item != public or not re.fullmatch(r"[A-Za-z0-9_-]+", ident) or any(
            "/" in row["relative_path"] or not _local_asset(assets, base + row["relative_path"], kind)
            for kind, row in resources.items()
        ) or (playback and (
            playback["video_src"] != base + resources.get("picture", {}).get("relative_path", "")
            or playback["audio"]["src"] != base + resources.get("audio", {}).get("relative_path", "")
        )):
            raise ValueError("Gallery requires projected public media. Rebuild the delivery first.")
    practice_count = sum(item.get("kind") == "animation" for item in practice)
    if not practice_path.exists():
        practice_path.write_text("window.PRACTICE_MEDIA=[];\n", encoding="utf-8")
    textbook = []
    textbook_path = assets / "textbook-animation-data.js"
    if practice_only:
        # This file is generated by this tool, never the original book dataset.
        textbook_path.unlink(missing_ok=True)
    else:
        book = _assignment(out / "教材/data/data.js", "__DATA__", {})
        if not isinstance(book, dict):
            raise ValueError("window.__DATA__ must be an object")
        textbook = _textbook_records(book, out / "教材")
        textbook_path.write_text("window.TEXTBOOK_ANIMATIONS=" + _json(textbook) + ";\n", encoding="utf-8")

    question_links = {}
    editions = _assignment(out / "教材/assets/practice-data.js", "PRACTICE_EDITIONS", {})
    for edition, page_name in (("custom", "定制版-习题集.html"), ("full", "完整版-习题集.html")):
        if (out / page_name).is_file():
            for question in editions.get(edition, []):
                ident = question["id"]
                question_links[ident] = page_name + "#practice-" + quote(ident, safe="")
    practice_empty = "" if practice_count else '<p class="gallery-empty">此交付暂未登记新习题动画。</p>'
    textbook_section = ""
    if not practice_only:
        empty = "" if textbook else '<p class="gallery-empty">此交付未包含正文动画数据。</p>'
        textbook_section = f'''<section aria-labelledby="textbook-heading"><h2 id="textbook-heading">原有正文动画 · {len(textbook)} 个</h2><p>按教材单元保存原步骤与用途说明。展开条目播放，也可跳回教材中的对应段落。</p>{empty}<div id="textbook-animation-gallery"></div></section>'''

    names = ["marked.min.js", "katex.min.js", "reader-markdown.js", "study-math.js", "render-core.js",
             "audible-audio.js", "animations.js", "practice-media-data.js"]
    if not practice_only:
        names.append("textbook-animation-data.js")
    names.append("practice-media.js")
    scripts = "".join('<script src="assets/' + name + '"></script>' for name in names)
    textbook_init = "" if practice_only else '''
      const books=window.TEXTBOOK_ANIMATIONS||[];
      gallery(document.getElementById('textbook-animation-gallery'),books,'assets');
      for(const item of books) appendLink(item, item.textbook_href, '回到教材：'+item.unit_id+' · '+item.unit_title);
    '''
    description = "纸卷配套的新习题动画，可逐段播放并查看静态对照。" if practice_only else "新习题动画与原有正文动画分别列出。点击标题展开，再播放当前步骤；声音与视频使用原播放器同步。"
    page = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>复变函数 A · 动画目录</title>
<link rel="stylesheet" href="assets/katex.min.css"><link rel="stylesheet" href="assets/practice-media.css">
<style>body{{font:17px/1.75 system-ui,sans-serif;margin:0;background:#f5f8fb;color:#233c52}}main{{max-width:1040px;margin:auto;padding:28px 24px 64px}}header{{padding:20px 0 25px;border-bottom:3px solid #a7c9df}}h1{{font-size:34px;margin:12px 0}}h2{{font-size:25px;margin-top:34px}}a{{color:#24638e}}.gallery-nav{{display:flex;flex-wrap:wrap;gap:18px}}.gallery-empty{{background:#fff;padding:20px;border:1px solid #d5e2ec}}.gallery-source-link{{display:inline-block;margin:10px 0}}.gallery-error{{color:#9b352b}}[hidden]{{display:none!important}}</style></head>
<body><main><header><nav class="gallery-nav"><a href="index.html">返回总目录</a><a href="#practice-heading">新习题动画</a>{'' if practice_only else '<a href="#textbook-heading">正文动画</a>'}</nav><h1>动画目录</h1><p>{description}</p></header>
<section aria-labelledby="practice-heading"><h2 id="practice-heading">新习题动画 · {practice_count} 个</h2><p>这些动画对应新补充的习题任务；展开后可按原有步骤播放。</p>{practice_empty}<div id="practice-animation-gallery"></div></section>
{textbook_section}<p id="gallery-error" class="gallery-error" role="alert" hidden></p></main>{scripts}
<script>(function(){{
  if(!window.AIFSPracticeMedia){{const e=document.getElementById('gallery-error');e.hidden=false;e.textContent='动画播放器尚未载入，请检查 assets 中的本地文件。';return;}}
  const gallery=window.AIFSPracticeMedia.gallery;
  const practice=(window.PRACTICE_MEDIA||[]).filter(item=>item.kind==='animation');
  const links={_json({item["question_id"]: question_links[item["question_id"]] for item in practice if item.get("question_id") in question_links})};
  function appendLink(item, href, text) {{
    const card=document.getElementById('media-'+item.id);
    if(!card || !href) return;
    const link=document.createElement('a');
    link.className='gallery-source-link';link.href=href;link.textContent=text;
    const host=card.querySelector('div');
    host.before(link);
  }}
  gallery(document.getElementById('practice-animation-gallery'),practice,'assets');
  for(const item of practice) appendLink(item, links[item.question_id], '回到习题：'+item.question_id);
  {textbook_init}
}})();</script></body></html>'''
    (out / "动画目录.html").write_text(page, encoding="utf-8")
    return {"page": str(out / "动画目录.html"), "practice_animations": practice_count,
            "textbook_animations": len(textbook), "practice_only": practice_only,
            "textbook_data": None if practice_only else str(textbook_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delivery", type=Path, required=True)
    parser.add_argument("--practice-only", action="store_true", help="仅列出新习题动画，不加载或导出教材动画数据")
    args = parser.parse_args()
    print(json.dumps(build_gallery(args.delivery, practice_only=args.practice_only), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
