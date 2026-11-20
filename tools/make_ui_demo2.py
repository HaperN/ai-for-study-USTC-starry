#!/usr/bin/env python3
"""生成三个**界面演示**（都用真数据：474 个概念 + 跨节 occurrences + 每处原文片段）。

* `demo-c-rail.html`      阅读页 + 右侧**概念轨道**（本节概念 ×N 角标）→ 点开**概念页**（含"全书出现位置"）
* `demo-d-conceptbar.html` 阅读页 + 底部**概念条**（悬停预览卡、点击进概念页）
* `demo-e-conceptmap.html` **全书概念图**（节点=跨节概念、边=同节共现）→ 点节点看概念页

为什么三个一起做：用户要比较"阅读时怎么带概念"（C/D）与"全局怎么看概念"（E）两类交互。
生成到站点目录里，直接复用 `assets/figures/*.png` 与 KaTeX。
"""
from __future__ import annotations

import argparse
import html
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIND_CN = {"definition": "定义", "theorem": "定理", "law": "定律", "property": "性质",
           "proposition": "命题", "corollary": "推论", "formula": "公式"}

CSS = """
:root{--ink:#1a1a1a;--muted:#6b7280;--line:#e5e7eb;--accent:#2c5f9e;--warm:#d9a441;--bg:#fbfaf7}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
     font:17px/1.85 "Noto Serif SC","Songti SC",Georgia,serif}
a{color:var(--accent)}
header.top{padding:16px 26px;border-bottom:1px solid var(--line);background:#fff}
header.top h1{margin:0 0 4px;font-size:19px}
header.top .hint{color:var(--muted);font-size:13px}
main{max-width:1240px;margin:0 auto;padding:22px 26px 120px}
p{margin:0 0 1.05em}
.fig{margin:1.1rem 0;text-align:center}
.fig img{max-width:min(500px,100%);border:1px solid var(--line);background:#fff;padding:6px}
.tex.block{display:block;text-align:center;margin:1rem 0}
.mono{font-family:Consolas,monospace;font-size:.92em;background:#f3f4f6;border-radius:4px;padding:1px 5px}
.clink{color:var(--accent);border-bottom:1px solid rgba(44,95,158,.35);cursor:pointer}
.chip{display:inline-flex;align-items:center;gap:5px;margin:3px 5px 3px 0;padding:2px 10px;
  border:1px solid rgba(44,95,158,.4);border-radius:14px;background:#f4f8fd;color:var(--accent);
  font-size:13.5px;cursor:pointer}
.chip .n{background:rgba(44,95,158,.14);border-radius:9px;padding:0 6px;font-size:12px}
.panel{border-left:3px solid var(--accent);background:#fff;border-radius:0 8px 8px 0;
  padding:14px 16px;box-shadow:0 1px 3px rgba(0,0,0,.05)}
.panel h4{margin:0 0 6px;font-size:15px}
.panel .k{color:var(--muted);font-size:12.5px;margin-top:8px}
.occ{border-top:1px dashed var(--line);padding:7px 0;font-size:14px}
.occ .where{color:var(--accent);font-weight:600}
.occ .quote{color:#374151}
#overlay{position:fixed;inset:0;background:rgba(20,20,20,.35);display:none;z-index:50}
#overlay.on{display:block}
#overlay .box{position:absolute;right:0;top:0;bottom:0;width:min(520px,92vw);background:#fff;
  padding:22px 24px;overflow:auto;box-shadow:-4px 0 18px rgba(0,0,0,.12)}
#overlay .close{float:right;border:0;background:#f3f4f6;border-radius:8px;padding:4px 10px;cursor:pointer}
.preview{position:absolute;max-width:330px;background:#fff;border:1px solid var(--line);
  border-radius:8px;padding:10px 12px;box-shadow:0 6px 18px rgba(0,0,0,.12);display:none;z-index:40;
  font-size:14px;line-height:1.7}
"""


# ── 数据准备 ───────────────────────────────────────────────────────────
def load_data(parse_dir: Path, lesson_path: Path | None):
    concepts = json.loads((parse_dir / "concepts.json").read_text(encoding="utf-8"))["concepts"]
    chapters = json.loads((parse_dir / "chapters.json").read_text(encoding="utf-8"))
    unit_title = {}
    for u in chapters.get("lesson_units", []):
        unit_title[u["unit_id"]] = u.get("title") or u["unit_id"]
    for c in chapters.get("chapters", []) if isinstance(chapters.get("chapters"), list) else []:
        unit_title.setdefault(c.get("unit_id") or c.get("id"), c.get("title"))
    lesson = json.loads(lesson_path.read_text(encoding="utf-8")) if lesson_path else None
    return concepts, unit_title, lesson


def concept_view(c: dict, unit_title: dict) -> dict:
    occ = []
    for o in c.get("occurrences") or []:
        uid = o.get("unit_id") or ""
        occ.append({"unit": uid, "title": unit_title.get(uid, uid),
                    "chapter": o.get("chapter") or ""})
    quotes = [q for q in (c.get("source_quotes") or []) if q]
    return {"id": c["id"], "name": c.get("name"), "type": KIND_CN.get(c.get("type"), c.get("type")),
            "formal": (c.get("formal") or "")[:400], "occ": occ, "quotes": [q[:180] for q in quotes[:6]],
            "count": len(occ)}


# ── 正文渲染（复用同一套标记）─────────────────────────────────────────
def render_body(body: str, concepts_by_id: dict, para_limit: int = 16) -> str:
    paras = [p.strip() for p in body.split("\n\n") if p.strip()][:para_limit]
    out = []
    for p in paras:
        p = html.escape(p)
        p = re.sub(r"\[\[FIGURE:(IMG-\d+)\]\]",
                   lambda m: f'</p><div class="fig"><img src="assets/figures/{m.group(1)}.png"'
                             f' alt="{m.group(1)}"></div><p>', p)
        p = re.sub(r"\[\[EXPLAIN:[^\|\]]+\|([^\]]+)\]\]",
                   lambda m: f'<span class="chip" style="border-style:dashed">{m.group(1)}</span>', p)
        p = re.sub(r"\[\[CONCEPT:(C-\d+)\|([^\]]+)\]\]",
                   lambda m: f'<span class="clink" data-concept="{m.group(1)}">{m.group(2)}</span>', p)
        p = re.sub(r"\[\[NOTE:[^\]]+\]\]", '<span style="color:#d9a441">◉</span>', p)
        p = re.sub(r"\$\$(.+?)\$\$",
                   lambda m: f'<span class="tex block">{html.escape(m.group(1))}</span>', p, flags=re.S)
        p = re.sub(r"\$(.+?)\$", lambda m: f'<span class="tex">{html.escape(m.group(1))}</span>', p)
        out.append(f"<p>{p}</p>")
    return "".join(out)


def head(title: str) -> str:
    return ("<meta charset='utf-8'><title>" + title + "</title>"
            f"<style>{CSS}</style>"
            "<script src='assets/katex.min.js'></script>"
            "<link rel='stylesheet' href='assets/katex.min.css'>"
            "<script>window.addEventListener('DOMContentLoaded',function(){"
            "document.querySelectorAll('.tex').forEach(function(el){try{"
            "katex.render(el.textContent,el,{displayMode:el.classList.contains('block'),"
            "throwOnError:false});}catch(e){}});});</script>")


PANEL_JS = """
<script>
var CONCEPTS = __DATA__;
function panelHtml(id){
  var c = CONCEPTS[id]; if(!c) return "";
  var h = "<h4>【"+c.type+"】"+c.name+"</h4>";
  if(c.formal) h += "<div class='k'>原文严格表述</div><div>"+c.formal+"</div>";
  h += "<div class='k'>在全书出现 "+c.count+" 处</div>";
  c.occ.forEach(function(o,i){
    h += "<div class='occ'><span class='where'>"+o.title+"</span>";
    var q = c.quotes[i] || c.quotes[0] || "";
    if(q) h += "<div class='quote'>"+q+"</div>";
    h += "</div>";
  });
  return h;
}
function openConcept(id){
  document.getElementById("boxbody").innerHTML = panelHtml(id);
  document.getElementById("overlay").classList.add("on");
}
document.addEventListener("click", function(ev){
  var t = ev.target.closest("[data-concept]");
  if(t){ openConcept(t.dataset.concept); return; }
  if(ev.target.id === "overlay" || ev.target.classList.contains("close"))
    document.getElementById("overlay").classList.remove("on");
});
</script>
<div id="overlay"><div class="box"><button class="close">关闭</button>
<div id="boxbody"></div></div></div>
"""


def build_c(concepts_view: dict, body_html: str, unit_concepts: list[dict]) -> str:
    chips = "".join(f'<span class="chip" data-concept="{c["id"]}">{c["name"]}'
                    f'<span class="n">×{c["count"]}</span></span>' for c in unit_concepts)
    return f"""<!doctype html><html lang="zh"><head>{head('演示 C · 概念轨道')}</head><body>
<header class="top"><h1>演示 C · 右侧「概念轨道」＋ 概念页（含全书出现位置）</h1>
<div class="hint">右栏不再是"本节的详解列表"，而是**本节涉及的概念**，角标 ×N = 它在全书出现几处；
点任意概念（右栏、或正文里带下划线的词）打开**概念页**：原文表述 + 每一处的原文片段。</div></header>
<main style="display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:34px">
  <article>{body_html}</article>
  <aside style="position:sticky;top:16px;align-self:start">
    <div class="k" style="color:#6b7280;font-size:12.5px;margin-bottom:6px">本节涉及的概念（{len(unit_concepts)}）</div>
    {chips}
  </aside>
</main>{PANEL_JS.replace('__DATA__', json.dumps(concepts_view, ensure_ascii=False))}</body></html>"""


def build_d(concepts_view: dict, body_html: str, unit_concepts: list[dict]) -> str:
    chips = "".join(f'<span class="chip" data-concept="{c["id"]}" data-preview="1">{c["name"]}'
                    f'<span class="n">×{c["count"]}</span></span>' for c in unit_concepts)
    hover = """
<script>
var PREV = document.getElementById("preview");
document.addEventListener("mouseover", function(ev){
  var t = ev.target.closest("[data-preview]");
  if(!t) return;
  var c = CONCEPTS[t.dataset.concept]; if(!c) return;
  PREV.innerHTML = "<b>【"+c.type+"】"+c.name+"</b><div style='color:#6b7280'>全书出现 "+c.count+
                   " 处</div><div>"+(c.quotes[0]||"")+"</div>";
  var r = t.getBoundingClientRect();
  PREV.style.left = Math.min(r.left, window.innerWidth-350) + "px";
  PREV.style.top = (r.top - 8) + "px";
  PREV.style.transform = "translateY(-100%)";
  PREV.style.display = "block";
});
document.addEventListener("mouseout", function(ev){
  if(ev.target.closest("[data-preview]")) PREV.style.display = "none";
});
</script>"""
    return f"""<!doctype html><html lang="zh"><head>{head('演示 D · 概念条')}</head><body>
<header class="top"><h1>演示 D · 底部「概念条」（悬停预览 + 点击进概念页）</h1>
<div class="hint">正文保持通栏、没有常驻右栏；页面**底部常驻一条概念条**：本节涉及的概念 + ×N 角标。
鼠标悬停出小卡（类型 / 全书出现几处 / 那一处的原文一句），点击打开完整概念页。</div></header>
<main>{body_html}</main>
<div style="position:fixed;left:0;right:0;bottom:0;background:#fff;border-top:1px solid #e5e7eb;
            padding:8px 26px;max-height:34vh;overflow:auto;box-shadow:0 -2px 10px rgba(0,0,0,.05)">
  <div style="color:#6b7280;font-size:12.5px;margin-bottom:4px">本节概念（{len(unit_concepts)}）· 悬停看预览，点击看全部出现位置</div>
  {chips}
</div>
<div class="preview" id="preview"></div>
{PANEL_JS.replace('__DATA__', json.dumps(concepts_view, ensure_ascii=False))}{hover}</body></html>"""


def _layout(nodes: list[str], edges: list[tuple[str, str]], *, w=1180, h=760, iters=260):
    """很土的力导向：够用就行（半径按出现次数、斥力+弹簧）。"""
    pos = {n: [w / 2 + 320 * math.cos(i * 2.399), h / 2 + 260 * math.sin(i * 2.399)]
           for i, n in enumerate(nodes)}
    for _ in range(iters):
        for i, a in enumerate(nodes):                       # 斥力
            for b in nodes[i + 1:]:
                dx, dy = pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]
                d2 = max(dx * dx + dy * dy, 25.0)
                f = 2600.0 / d2
                d = math.sqrt(d2)
                pos[a][0] += dx / d * f; pos[a][1] += dy / d * f
                pos[b][0] -= dx / d * f; pos[b][1] -= dy / d * f
        for a, b in edges:                                  # 弹簧
            dx, dy = pos[b][0] - pos[a][0], pos[b][1] - pos[a][1]
            d = max(math.sqrt(dx * dx + dy * dy), 1.0)
            f = (d - 130) * 0.012
            pos[a][0] += dx / d * f; pos[a][1] += dy / d * f
            pos[b][0] -= dx / d * f; pos[b][1] -= dy / d * f
        for n in nodes:                                     # 中心 + 边界
            pos[n][0] += (w / 2 - pos[n][0]) * 0.0035
            pos[n][1] += (h / 2 - pos[n][1]) * 0.0035
            pos[n][0] = min(max(pos[n][0], 70), w - 70)
            pos[n][1] = min(max(pos[n][1], 50), h - 50)
    return pos


def build_e(concepts_view: dict, cross: list[dict], edges: list[tuple[str, str]]) -> str:
    nodes = [c["id"] for c in cross]
    pos = _layout(nodes, edges)
    edge_svg = "".join(
        f'<line x1="{pos[a][0]:.0f}" y1="{pos[a][1]:.0f}" x2="{pos[b][0]:.0f}" y2="{pos[b][1]:.0f}"'
        f' stroke="#c9d6e5" stroke-width="1.2"/>' for a, b in edges)
    node_svg = "".join(
        f'<g class="node" data-concept="{c["id"]}" style="cursor:pointer">'
        f'<circle cx="{pos[c["id"]][0]:.0f}" cy="{pos[c["id"]][1]:.0f}" '
        f'r="{8 + min(c["count"], 8) * 1.6:.0f}" fill="#2c5f9e" fill-opacity=".18" '
        f'stroke="#2c5f9e" stroke-width="1.4"/>'
        f'<text x="{pos[c["id"]][0]:.0f}" y="{pos[c["id"]][1] - 16:.0f}" text-anchor="middle" '
        f'font-size="13" fill="#1a1a1a">{html.escape(str(c["name"])[:12])}'
        f'<tspan fill="#6b7280" font-size="11"> ×{c["count"]}</tspan></text></g>'
        for c in cross)
    return f"""<!doctype html><html lang="zh"><head>{head('演示 E · 全书概念图')}</head><body>
<header class="top"><h1>演示 E · 全书概念图（节点=概念，边=同一节里一起出现）</h1>
<div class="hint">只画**跨节出现**的概念（{len(cross)} 个，圆越大=出现越多）；连线=它们在某一节里同时出现，
可当作"读到这个多半也要用到那个"。点节点打开概念页（含每一处的原文片段）。</div></header>
<main>
  <svg viewBox="0 0 1180 760" style="width:100%;height:auto;background:#fff;border:1px solid #e5e7eb;border-radius:10px">
    {edge_svg}{node_svg}
  </svg>
</main>
{PANEL_JS.replace('__DATA__', json.dumps(concepts_view, ensure_ascii=False))}</body></html>"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成 C/D/E 三个界面演示")
    ap.add_argument("--parse", required=True)
    ap.add_argument("--lesson", help="用哪一节的正文做阅读页")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-nodes", type=int, default=42, help="概念图最多画几个节点")
    args = ap.parse_args(argv)
    parse_dir = ROOT / args.parse if not Path(args.parse).is_absolute() else Path(args.parse)
    out_dir = ROOT / args.out if not Path(args.out).is_absolute() else Path(args.out)
    concepts, unit_title, lesson = load_data(parse_dir, ROOT / args.lesson if args.lesson else None)
    views = {c["id"]: concept_view(c, unit_title) for c in concepts}
    body_html = render_body(str(lesson.get("body_markdown") or ""), {}) if lesson else "<p>（没有正文）</p>"
    unit_id = str(lesson.get("unit_id")) if lesson else ""
    unit_concepts = [views[c["id"]] for c in concepts
                     if any((o.get("unit_id") == unit_id) for o in (c.get("occurrences") or []))]
    unit_concepts.sort(key=lambda c: -c["count"])
    cross = sorted((v for v in views.values() if v["count"] >= 2), key=lambda v: -v["count"])
    cross = cross[: args.max_nodes]
    keep = {c["id"] for c in cross}
    edge_set: set[tuple[str, str]] = set()
    by_unit = defaultdict(list)
    for c in concepts:
        if c["id"] not in keep:
            continue
        for o in c.get("occurrences") or []:
            by_unit[o.get("unit_id")].append(c["id"])
    for _uid, ids in by_unit.items():
        ids = sorted(set(ids))
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                edge_set.add((a, b))
    paths = []
    paths.append(out_dir / "demo-c-rail.html")
    paths[-1].write_text(build_c(views, body_html, unit_concepts), encoding="utf-8")
    paths.append(out_dir / "demo-d-conceptbar.html")
    paths[-1].write_text(build_d(views, body_html, unit_concepts), encoding="utf-8")
    paths.append(out_dir / "demo-e-conceptmap.html")
    paths[-1].write_text(build_e(views, cross, sorted(edge_set)), encoding="utf-8")
    for p in paths:
        print(f"✓ {p}")
    print(f"  本节概念 {len(unit_concepts)} 个 ｜ 跨节概念 {len(cross)} 个 ｜ 概念图 {len(edge_set)} 条边")
    return 0


if __name__ == "__main__":
    sys.exit(main())
