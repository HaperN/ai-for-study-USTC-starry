#!/usr/bin/env python3
"""三份**教材解析型单文件 HTML 演示**（同一份真实内容 × 三种风格）。

内容来源（全部真数据，不手写假内容）：
* 正文/详解/例题：`data/lesson/<书>/lessons/<单元>.json`
* 概念类型与原文表述：`data/parse/<书>/concepts.json`
* 配图：`data/site/<书>/assets/figures/*.png`（打包时内联成 data URI）

三种风格（同时把"右栏要不要"这个维度一起比掉）：
* `style-a-plain.html`   学术极简 —— **无右栏**，概念/推导全部就地展开
* `style-b-bright.html`  明亮教学风 —— **右栏默认收起**（点按钮拉出本节目录/速览）
* `style-c-dark.html`    暗色玻璃态 —— **右栏常驻**

工程约定（用户 spec + 本仓现状）：
* 公式＝**KaTeX**（仓库已离线内置；打包时把 JS/CSS/字体全部内联，单文件不依赖 CDN）
* 展开＝`<details>/<summary>`；打开时 `scrollIntoView({behavior:'smooth'})`；**不用** max-height
* 中文字体＝系统字体栈（仓库无中文字体），留一行注释说明换霞鹜文楷的办法
* 四类模块（正文/配图/推导/概念）用「左边框色条 + 小标签 + 不同底色」区分
* 语义标签：`nav / article / section / figure+figcaption / dl-dt-dd / details`

用法：
```bash
python tools/make_style_demos.py --parse data/parse/circuit-analysis-1 \
    --lesson data/lesson/circuit-analysis-1/lessons/CH02-S01.json \
    --site data/site/circuit-analysis-1
# → data/site/circuit-analysis-1/style-{a,b,c}-standalone.html
```
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import mimetypes
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIND_CN = {"definition": "定义", "theorem": "定理", "law": "定律", "property": "性质",
           "proposition": "命题", "corollary": "推论", "formula": "公式", "concept": "概念"}

COMMON_CSS = """
/* ── 主题变量：换风格只改这一块（用户 spec：CSS 变量统一管理） ───────────── */
:root{
  --bg:#fbfaf7; --fg:#16181d; --muted:#6b7280; --line:#e6e8ec; --card:#fff;
  --accent:#2f6fd0; --accent-soft:rgba(47,111,208,.08);
  --c-text:#2f6fd0; --c-fig:#3f9e6a; --c-derive:#c07a1d; --c-concept:#8b5cf6;
  --radius:12px; --gap:1rem; --dur:.22s;
  --serif:"Noto Serif SC","Source Han Serif SC","Songti SC",Georgia,serif;
  --sans:"Noto Sans SC","PingFang SC","Microsoft YaHei",system-ui,sans-serif;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--fg);font:16.5px/1.85 var(--serif)}
a{color:var(--accent)}
code{font-family:ui-monospace,Consolas,monospace;font-size:.92em;background:var(--accent-soft);
  padding:1px 4px;border-radius:4px}
/* 四类模块：左边框色条 + 小标签 + 不同底色 */
.mod{border-left:3px solid var(--line);padding:.55rem .8rem;margin:1rem 0;border-radius:0 var(--radius) var(--radius) 0}
.mod > .tag{display:inline-block;font:600 11.5px/1.6 var(--sans);letter-spacing:.04em;
  padding:0 .45rem;border-radius:999px;margin-bottom:.3rem}
.mod-text{border-left-color:var(--c-text)}
.mod-text > .tag{background:var(--accent-soft);color:var(--c-text)}
.mod-fig{border-left-color:var(--c-fig);background:rgba(63,158,106,.05)}
.mod-fig > .tag{background:rgba(63,158,106,.12);color:var(--c-fig)}
.mod-derive{border-left-color:var(--c-derive);background:rgba(192,122,29,.06)}
.mod-derive > .tag{background:rgba(192,122,29,.14);color:var(--c-derive)}
.mod-concept{border-left-color:var(--c-concept);background:rgba(139,92,246,.06)}
.mod-concept > .tag{background:rgba(139,92,246,.14);color:var(--c-concept)}
figure{margin:.6rem 0;text-align:center}
figure img{max-width:min(430px,100%);background:#fff;border:1px solid var(--line);
  border-radius:8px;padding:4px}
figcaption{color:var(--muted);font-size:.86em;margin-top:.25rem}
table.derive{width:100%;border-collapse:collapse;font-size:.92em;margin-top:.3rem}
table.derive th,table.derive td{border-bottom:1px solid var(--line);padding:.24rem .35rem;text-align:left;vertical-align:top}
table.derive th{color:var(--muted);font-weight:600}
table.derive td:first-child{width:1.5rem;color:var(--muted)}
.basis{display:inline-block;padding:0 .45rem;border-radius:999px;background:var(--accent-soft);
  color:var(--accent);font-size:.85em}
details.concept{border:1px solid var(--line);border-radius:var(--radius);background:var(--card);
  padding:.35rem .7rem;margin:.5rem 0}
details.concept > summary{cursor:pointer;font-weight:600;list-style:none}
details.concept > summary::-webkit-details-marker{display:none}
details.concept > summary::before{content:"▸ ";color:var(--accent)}
details.concept[open] > summary::before{content:"▾ "}
details.concept .body{padding:.35rem 0 .5rem}
dl.meta{margin:.2rem 0 .5rem}
dl.meta dt{float:left;clear:left;width:5.2rem;color:var(--muted);font-size:.86em}
dl.meta dd{margin:0 0 .2rem 5.6rem;font-size:.92em}
.missing{color:var(--muted);font-size:.9em}
footer.note{margin-top:2.5rem;padding-top:.8rem;border-top:1px solid var(--line);
  color:var(--muted);font-size:.84em;line-height:1.7}
.warm{background:rgba(192,122,29,.1);border-left:3px solid var(--c-derive);
  padding:.3rem .6rem;border-radius:0 8px 8px 0;margin:.4rem 0;font-size:.94em}
.ex-chip{display:inline-block;border:1px dashed var(--accent);border-radius:11px;padding:0 6px;
  margin:0 2px;font-size:.85em;color:var(--accent);cursor:pointer;background:var(--accent-soft)}
.cpt{color:var(--c-concept);border-bottom:1px dotted var(--c-concept);cursor:pointer}
.toc a{display:block;padding:.18rem 0;color:var(--fg);text-decoration:none;font-size:.9em;
  border-left:2px solid transparent;padding-left:.5rem}
.toc a.on{border-left-color:var(--accent);color:var(--accent);font-weight:600}
.grid{display:grid;gap:calc(var(--gap) * 1.6);max-width:1180px;margin:0 auto;padding:1.2rem}
"""

STYLE_A = """
/* 学术极简：无右栏，两栏（目录 + 正文），大量留白、克制配色 */
.grid{grid-template-columns:210px minmax(0,1fr)}
.toc{position:sticky;top:1rem;align-self:start}
@media (max-width:900px){.grid{grid-template-columns:1fr}.toc{position:static}}
"""
STYLE_B = """
/* 明亮教学风：右栏默认收起（点按钮拉出），主色更亮、卡片化 */
:root{--accent:#2563eb;--accent-soft:rgba(37,99,235,.1);--bg:#f7fbff;--radius:14px}
.grid{grid-template-columns:200px minmax(0,1fr) 0}
.grid.rail-open{grid-template-columns:200px minmax(0,1fr) 280px}
.rail{overflow:hidden;position:sticky;top:1rem;align-self:start}
.rail .inner{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:.6rem .7rem;
  max-height:78vh;overflow:auto}
#rail-toggle{position:fixed;right:12px;bottom:56px;z-index:20;border:1px solid var(--line);
  background:var(--card);border-radius:999px;padding:.3rem .8rem;cursor:pointer;font:600 12.5px var(--sans)}
@media (max-width:1100px){.grid,.grid.rail-open{grid-template-columns:1fr}}
"""
STYLE_C = """
/* 暗色玻璃态：右栏常驻；深底 + 半透明卡片 + 细边框（公式对比度优先） */
:root{--bg:#0f1216;--fg:#e6e9ee;--muted:#98a2b3;--line:rgba(255,255,255,.14);
  --card:rgba(255,255,255,.06);--accent:#7aa2f7;--accent-soft:rgba(122,162,247,.16);
  --c-text:#7aa2f7;--c-fig:#7bd88f;--c-derive:#e0af68;--c-concept:#bb9af7}
body{background:radial-gradient(1200px 600px at 20% -10%,rgba(122,162,247,.12),transparent),var(--bg)}
.grid{grid-template-columns:200px minmax(0,1fr) 280px}
.toc,.rail{position:sticky;top:1rem;align-self:start}
.rail .inner,.toc{background:var(--card);backdrop-filter:blur(6px);border:1px solid var(--line);
  border-radius:var(--radius);padding:.6rem .7rem}
figure img{background:#fff}
code{background:rgba(255,255,255,.08)}
@media (max-width:1100px){.grid{grid-template-columns:1fr}}
"""


# ── 数据准备 ──────────────────────────────────────────────────────────────
def build_content(parse_dir: Path, lesson_path: Path, unit: str) -> dict:
    lesson = json.loads(lesson_path.read_text(encoding="utf-8"))
    concepts = json.loads((parse_dir / "concepts.json").read_text(encoding="utf-8"))["concepts"]
    by_id = {c["id"]: c for c in concepts}
    ex_by_concept: dict[str, dict] = {}
    for item in lesson.get("explainables") or []:
        cid = str(item.get("concept_id") or "").upper()
        if cid and cid not in ex_by_concept:
            ex_by_concept[cid] = item

    def render_md(text: str) -> str:
        out = html.escape(str(text or ""))
        out = re.sub(r"\[\[FIGURE:([^\]]+)\]\]", "", out)
        out = re.sub(r"\[\[DIAGRAM:([^\]]+)\]\]", r"<span class='ex-chip'>\1 过程图</span>", out)
        out = re.sub(r"\[\[EXPLAIN:[^\]|]+\|([^\]]+)\]\]", r"<span class='ex-chip'>\1</span>", out)
        out = re.sub(r"\[\[CONCEPT:[^\]|]+\|([^\]]+)\]\]", r"<span class='cpt'>\1</span>", out)
        out = re.sub(r"\[\[NOTE:[^\]]+\]\]", "<sup>注</sup>", out)
        out = re.sub(r"\$\$(.+?)\$\$", lambda m: f'<span class="tex block">{html.escape(m.group(1))}</span>',
                     out, flags=re.S)
        out = re.sub(r"\$(.+?)\$", lambda m: f'<span class="tex">{html.escape(m.group(1))}</span>', out)
        return out

    # 正文：取带标记的前几段（保证有图/概念/公式三种）
    paras = [p.strip() for p in str(lesson.get("body_markdown") or "").split("\n\n") if p.strip()]
    body = [p for p in paras if re.search(r"\[\[(FIGURE|CONCEPT|EXPLAIN|DIAGRAM):", p)][:6] or paras[:4]

    # 概念：本节出现过的，按"有详解"的优先级取前 4 条
    unit_cids = [c["id"] for c in concepts
                 if any(o.get("unit_id") == unit for o in (c.get("occurrences") or []))]
    picked = [cid for cid in unit_cids if cid in ex_by_concept][:4] or unit_cids[:4]

    figs = {str(f.get("id")): f for f in (lesson.get("figures") or [])}
    fig_by_concept: dict[str, str] = {}
    for fid, f in figs.items():
        cid = str(f.get("concept_id") or "").upper()
        if cid and cid not in fig_by_concept:
            fig_by_concept[cid] = fid
    reqs = [r for r in (lesson.get("figure_requests") or []) if isinstance(r, dict)]
    req_by_concept: dict[str, dict] = {}
    for r in reqs:
        cid = str(r.get("concept_id") or "").upper()
        if cid and cid not in req_by_concept:
            req_by_concept[cid] = r

    concept_rows = []
    for cid in picked:
        c = by_id.get(cid) or {}
        ex = ex_by_concept.get(cid) or {}
        concept_rows.append({
            "id": cid,
            "name": c.get("name") or ex.get("name") or cid,
            "type": KIND_CN.get(c.get("type"), c.get("type") or ex.get("kind") or ""),
            "explain": {"what": ex.get("what"), "why": ex.get("why"),
                        "markdown": ex.get("markdown"), "warmup": ex.get("warmup")},
            "formal": c.get("formal"),
            "figure": fig_by_concept.get(cid),
            "figure_request": req_by_concept.get(cid),
            "derive": ex.get("walkthrough") or [],
            "occurrences": [o.get("title") or o.get("unit_id") for o in (c.get("occurrences") or [])],
        })

    example = (lesson.get("examples") or [None])[0]
    return {"lesson": lesson, "body": body, "concepts": concept_rows,
            "example": example, "render_md": render_md, "figs": figs}


def svg_mesh() -> str:
    """手绘 SVG：两个网孔 + 网孔电流方向（真实 §2.1 的内容，不是占位图）。"""
    return """<svg viewBox="0 0 420 220" role="img" aria-label="两个网孔的网孔电流方向示意"
     style="max-width:420px;width:100%;background:#fff;border:1px solid #e6e8ec;border-radius:8px">
  <rect x="40" y="40" width="340" height="140" fill="none" stroke="#333" stroke-width="1.6"/>
  <line x1="210" y1="40" x2="210" y2="180" stroke="#333" stroke-width="1.6"/>
  <rect x="120" y="95" width="60" height="26" fill="#fff" stroke="#333"/>
  <rect x="250" y="95" width="60" height="26" fill="#fff" stroke="#333"/>
  <text x="150" y="113" text-anchor="middle" font-size="12">R₁</text>
  <text x="280" y="113" text-anchor="middle" font-size="12">R₂</text>
  <path d="M95 130 A 28 28 0 1 1 95 90" fill="none" stroke="#c07a1d" stroke-width="2"/>
  <polygon points="95,86 101,96 89,96" fill="#c07a1d"/>
  <text x="88" y="150" font-size="12" fill="#c07a1d">i_M1</text>
  <path d="M325 90 A 28 28 0 1 1 325 130" fill="none" stroke="#c07a1d" stroke-width="2"/>
  <polygon points="325,134 331,124 319,124" fill="#c07a1d"/>
  <text x="300" y="150" font-size="12" fill="#c07a1d">i_M2</text>
  <text x="210" y="205" text-anchor="middle" font-size="11" fill="#6b7280">
    两个网孔各设一个假想的网孔电流（沿网孔边界流动）</text>
</svg>"""


def page(content: dict, style: str, title: str, note: str) -> str:
    md = content["render_md"]
    # 左侧目录
    toc = ["<nav class='toc' aria-label='本节目录'><strong>网孔分析</strong>"]
    toc.append("<a href='#sec-body'>正文</a>")
    for c in content["concepts"]:
        toc.append(f"<a href='#cpt-{html.escape(c['id'])}'>{html.escape(c['name'])}</a>")
    if content["example"]:
        toc.append("<a href='#ex-1'>例题</a>")
    toc.append("</nav>")

    body_html = []
    for p in content["body"]:
        body_html.append(f"<section class='mod mod-text'><span class='tag'>正文</span><p>{md(p)}</p></section>")

    fig_html = [
        "<section class='mod mod-fig'><span class='tag'>配图</span>",
        "<figure>" + svg_mesh() + "<figcaption>图 2-1 网孔电流方向（本演示手绘 SVG，可交互化见页脚说明）</figcaption></figure>",
    ]
    fid = next(iter(content["figs"]), None)
    if fid:
        fig_html.append(f"<figure><img src='assets/figures/{fid}.png' alt='{fid}'>"
                        f"<figcaption>教材原图 {fid}（打包时内联）</figcaption></figure>")
    fig_html.append("</section>")
    body_html.append("".join(fig_html))

    cpt_html = ["<section class='mod mod-concept'><span class='tag'>概念</span>"]
    for c in content["concepts"]:
        cid = html.escape(c["id"])
        exp = c["explain"]
        inner = []
        dd = []
        if c["formal"]:
            dd.append(f"<dt>原文</dt><dd>{md(c['formal'])}</dd>")
        if c["occurrences"]:
            dd.append(f"<dt>出现于</dt><dd>{html.escape('、'.join([x for x in c['occurrences'] if x][:4]))}</dd>")
        if dd:
            inner.append("<dl class='meta'>" + "".join(dd) + "</dl>")
        # 三件可独立出现
        if exp.get("what") or exp.get("why") or exp.get("markdown"):
            inner.append("<div class='mod mod-derive' style='border-left-color:var(--c-text)'>"
                         "<span class='tag' style='background:var(--accent-soft);color:var(--c-text)'>解释</span>")
            if exp.get("what"):
                inner.append(f"<p><b>它是什么：</b>{md(exp['what'])}</p>")
            if exp.get("why"):
                inner.append(f"<p><b>为什么在这里用：</b>{md(exp['why'])}</p>")
            if exp.get("warmup"):
                inner.append(f"<p class='warm'><b>先看一个简单情形：</b>{md(exp['warmup'])}</p>")
            if exp.get("markdown"):
                inner.append(f"<div>{md(exp['markdown'])}</div>")
            inner.append("</div>")
        else:
            inner.append("<p class='missing'>这一条暂时没有解释（抽取阶段只登记了名字与原文表述）。</p>")
        if c["figure"]:
            inner.append("<figure><img src='assets/figures/%s.png' alt='%s'>"
                         "<figcaption>%s 的配图</figcaption></figure>"
                         % (html.escape(c["figure"]), html.escape(c["figure"]), html.escape(c["name"])))
        elif c["figure_request"]:
            r = c["figure_request"]
            inner.append("<figure><figcaption>待补图（已提图请求）：%s"
                         "<br><span class='missing'>原文摘录：%s</span></figcaption></figure>"
                         % (html.escape(str(r.get("what") or "")), html.escape(str(r.get("quote") or ""))))
        else:
            inner.append("<p class='missing'>这一条暂时没有配图。</p>")
        steps = c["derive"]
        if steps:
            inner.append("<table class='derive'><thead><tr><th>#</th><th>这一步在说什么</th><th>依据</th>"
                         "</tr></thead><tbody>")
            for st in steps:
                inner.append("<tr><td>%s</td><td>%s</td><td><span class='basis'>%s</span></td></tr>"
                             % (html.escape(str(st.get("step"))), md(st.get("claim")),
                                md(st.get("basis"))))
            inner.append("</tbody></table>")
        else:
            inner.append("<p class='missing'>这一条暂时没有推导步骤。</p>")
        cpt_html.append(
            f"<details class='concept' id='cpt-{cid}'><summary>"
            f"<span class='tag' style='background:rgba(139,92,246,.14);color:var(--c-concept)'>{html.escape(c['type'])}</span>"
            f" {html.escape(c['name'])}</summary><div class='body'>{''.join(inner)}</div></details>")
    cpt_html.append("</section>")
    body_html.append("".join(cpt_html))

    ex = content["example"]
    if ex:
        ex_parts = ["<section class='mod mod-derive' id='ex-1'><span class='tag'>例题</span>",
                    f"<article><h3>{html.escape(str(ex.get('label') or ''))}</h3>",
                    f"<p>{md(ex.get('statement'))}</p>"]
        if ex.get("thought"):
            ex_parts.append(f"<p><b>思路：</b>{md(ex['thought'])}</p>")
        if ex.get("warmup"):
            ex_parts.append(f"<p class='warm'><b>先看一个简单情形：</b>{md(ex['warmup'])}</p>")
        ex_parts.append("<table class='derive'><thead><tr><th>#</th><th>一步一步解</th><th>依据</th></tr></thead><tbody>")
        for st in (ex.get("steps") or []):
            ex_parts.append("<tr><td>%s</td><td>%s</td><td><span class='basis'>%s</span></td></tr>"
                            % (html.escape(str(st.get("no"))), md(st.get("markdown")), md(st.get("basis"))))
        ex_parts.append("</tbody></table>")
        if ex.get("conclusion"):
            ex_parts.append(f"<p><b>结论：</b>{md(ex['conclusion'])}</p>")
        ex_parts.append("</article></section>")
        body_html.append("".join(ex_parts))

    rail = ("<aside class='rail' aria-label='速览'><div class='inner'><strong>本节的公式 · 定理 · 概念</strong>"
            "<ul>" + "".join(f"<li>{html.escape(c['name'])}（{html.escape(c['type'])}）</li>"
                             for c in content["concepts"]) + "</ul>"
            "<p class='missing'>右栏只做速览；讲解在正文里就地展开。</p></div></aside>")
    toggle = ("<button id='rail-toggle' type='button' aria-expanded='false'>本节目录 ☰</button>")

    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>{COMMON_CSS}{style}</style>
</head><body>
<div class="grid" id="grid">
  {toc[0] + ''.join(toc[1:])}
  <main id="main">{''.join(body_html)}
    <footer class="note">
      {note}<br>
      <b>公式</b>：KaTeX（本文件已内联 JS/CSS/字体，离线可用；动态插入内容后需再调
      <code>katex.render</code> 或重新扫描，本页用统一扫描函数 <code>typesetAll()</code>）。<br>
      <b>展开</b>：<code>&lt;details&gt;</code>；打开时 <code>scrollIntoView({{behavior:'smooth'}})</code>，
      不用 <code>max-height</code> 过渡（避免动画末尾空转）。<br>
      <b>中文字体</b>：用系统字体栈（仓库不含中文字体）。想换霞鹜文楷：把
      <code>@font-face{{font-family:'LXGW WenKai';src:url('LXGWWenKai-Regular.woff2')}}</code>
      放进本文件，并把 <code>--serif</code> 首位改成 <code>'LXGW WenKai'</code>。<br>
      <b>切风格</b>：三份演示共用同一段 CSS 变量与模块类名 —— 换风格只需替换
      <code>:root</code> 那一块变量和 <code>.grid</code> 的列定义（本文件里已用注释标出）。
    </footer>
  </main>
  {rail if style is STYLE_C else ('' if style is STYLE_A else rail)}
</div>
{toggle if style is STYLE_B else ''}
<script>
/* KaTeX：先扫描整页（含 <details> 收起时的内容，渲染一次即可） */
function typesetAll(root) {{
  if (!window.katex) return;
  (root || document).querySelectorAll(".tex").forEach(function (el) {{
    if (el.dataset.done) return;
    try {{
      katex.render(el.textContent, el, {{displayMode: el.classList.contains("block"), throwOnError: false}});
      el.dataset.done = "1";
    }} catch (e) {{}}
  }});
}}
document.addEventListener("DOMContentLoaded", function () {{
  typesetAll();
  /* 展开后平滑定位（用户 spec：展开后 scrollIntoView） */
  document.querySelectorAll("details.concept").forEach(function (d) {{
    d.addEventListener("toggle", function () {{
      if (d.open) {{
        typesetAll(d);
        d.scrollIntoView({{behavior: "smooth", block: "nearest"}});
      }}
    }});
  }});
  /* 左目录当前章节高亮 */
  var links = Array.prototype.slice.call(document.querySelectorAll("nav.toc a"));
  var targets = links.map(function (a) {{ return document.querySelector(a.getAttribute("href")); }});
  if (window.IntersectionObserver) {{
    var obs = new IntersectionObserver(function (entries) {{
      entries.forEach(function (en) {{
        if (!en.isIntersecting) return;
        links.forEach(function (a, i) {{ a.classList.toggle("on", targets[i] === en.target); }});
      }});
    }}, {{rootMargin: "-40% 0px -55% 0px"}});
    targets.forEach(function (t) {{ if (t) obs.observe(t); }});
  }}
  /* 明亮的教学风：右栏默认收起，点按钮拉出 */
  var btn = document.getElementById("rail-toggle");
  if (btn) btn.addEventListener("click", function () {{
    var g = document.getElementById("grid");
    g.classList.toggle("rail-open");
    btn.setAttribute("aria-expanded", g.classList.contains("rail-open") ? "true" : "false");
  }});
}});
</script>
</body></html>"""


# ── 打包：把 KaTeX 与图片内联成单文件 ─────────────────────────────────────
def pack(html_text: str, out: Path, site: Path) -> Path:
    assets = site / "assets"
    css_path = assets / "katex.min.css"
    js_path = assets / "katex.min.js"
    if css_path.is_file():
        css = css_path.read_text(encoding="utf-8")
        css = re.sub(r",\s*url\([^)]*\.(?:woff|ttf|otf)\)[^,;}]*", "", css)   # 只留 woff2，省一半

        def _font(m):
            rel = m.group(1).strip("'\"")
            p = (assets / rel).resolve()
            if p.is_file():
                mime = mimetypes.guess_type(str(p))[0] or "font/woff2"
                return "url(data:%s;base64,%s)" % (mime, base64.b64encode(p.read_bytes()).decode())
            return m.group(0)
        css = re.sub(r"url\(([^)]+)\)", _font, css)
        html_text = html_text.replace("</head>", f"<style>{css}</style></head>", 1)
    if js_path.is_file():
        html_text = html_text.replace("</body>", f"<script>{js_path.read_text(encoding='utf-8')}</script></body>", 1)

    def _img(m):
        p = (site / m.group(2)).resolve()
        if not p.is_file():
            return m.group(0)
        mime = mimetypes.guess_type(str(p))[0] or "image/png"
        return '%s"data:%s;base64,%s"' % (m.group(1), mime, base64.b64encode(p.read_bytes()).decode())
    html_text = re.sub(r'(src=)"((?:assets)/[^"]+\.(?:png|jpg|jpeg|webp|gif|svg))"', _img, html_text)

    out.write_text(html_text, encoding="utf-8")
    left = re.findall(r'(?:src|href)="(assets/[^"]+)"', html_text)
    print("  ✓ %s  %.0f KB  ｜ 外部引用 %d" % (out.name, out.stat().st_size / 1024, len(set(left))))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成三份风格演示（单文件）")
    ap.add_argument("--parse", required=True)
    ap.add_argument("--lesson", required=True)
    ap.add_argument("--site", required=True)
    ap.add_argument("--unit", default="CH02-S01")
    args = ap.parse_args(argv)
    parse_dir, site = ROOT / args.parse, ROOT / args.site
    content = build_content(parse_dir, ROOT / args.lesson, args.unit)
    print("  内容：正文 %d 段 ｜ 概念 %d 条 ｜ 例题 %d 道 ｜ 图 %d 张"
          % (len(content["body"]), len(content["concepts"]),
             1 if content["example"] else 0, len(content["figs"])))
    styles = [
        ("style-a-plain", STYLE_A, "风格 A · 学术极简（无右栏，讲解全部就地展开）",
         "风格 A：学术极简 —— 两栏（目录 + 正文），无右栏；四类模块靠左侧色条与底色区分。"),
        ("style-b-bright", STYLE_B, "风格 B · 明亮教学风（右栏默认收起）",
         "风格 B：明亮教学风 —— 卡片化、主色更亮；右栏默认收起，点右下角按钮拉出速览。"),
        ("style-c-dark", STYLE_C, "风格 C · 暗色玻璃态（右栏常驻）",
         "风格 C：暗色玻璃态 —— 深底 + 半透明卡片；右栏常驻做速览，公式对比度优先。"),
    ]
    for name, css, title, note in styles:
        text = page(content, css, title, note)
        pack(text, site / f"{name}-standalone.html", site)
    return 0


if __name__ == "__main__":
    sys.exit(main())
