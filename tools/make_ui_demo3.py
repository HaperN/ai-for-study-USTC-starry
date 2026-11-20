#!/usr/bin/env python3
"""生成两个"酷炫但服务内容"的演示（照 Explorable Explanations / Distill 的规矩）。

* `demo-f-scrolly.html`  **图随文动**：右栏始终显示**当前这一段自己**的
  配图 / 它引用的详解（含分步与依据）/ 那一段的公式 / **这一段涉及的跨节概念（×N）**；
* `demo-g-reactive.html` **可动手的图**：拖电流、调电阻、切关联/非关联 →
  电路里的电流箭头、u–i 直线、数值、公式**全部实时真算**。

第一版的坑（都已修，留在这里当备忘）：
  1. 正文标记没渲染 → 页面上直接露 `[[CONCEPT:C-0050|欧姆定律]]`；
  2. 详解的 `basis` 里也可能带标记（模型真写了 `[[EXPLAIN:F-02|…]]`）；
  3. 右栏内容与左栏对不上（拿一张流程图硬凑）；
  4. 段落小标题取到了空串 → 显示成"…"；
  5. KaTeX 被渲染三遍（`textContent` + `katex.render` + 页面自动渲染同一元素）；
  6. JS 字符串里的 `\\,` 被吃掉 → 公式成了 `R,i(t)`；
  7. 关联方向下的**电流箭头画反了**（关联 = 电流从 + 端流入，电阻内部 A→B）。
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIND_CN = {"definition": "定义", "theorem": "定理", "law": "定律", "property": "性质",
           "proposition": "命题", "corollary": "推论", "formula": "公式", "concept": "概念"}

CSS = """
:root{--ink:#16181d;--muted:#6b7280;--line:#e6e8ec;--accent:#2f6fd0;--warm:#e0a33e;--bg:#faf9f6}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
     font:17px/1.9 "Noto Serif SC","Songti SC",Georgia,serif}
header.top{padding:16px 26px;border-bottom:1px solid var(--line);background:#fff}
header.top h1{margin:0 0 4px;font-size:19px}
header.top .hint{color:var(--muted);font-size:13px;line-height:1.75}
.k{color:var(--muted);font-size:12.5px}
.small{font-size:13.5px;color:#374151;line-height:1.75}
.chip{display:inline-block;margin:2px 4px 2px 0;padding:1px 9px;border-radius:11px;
  border:1px dashed rgba(47,111,208,.55);color:var(--accent);background:#f3f7fd;font-size:12.5px}
.clink{color:var(--accent);border-bottom:1px solid rgba(47,111,208,.35)}
.note{color:var(--warm);font-weight:700}
.fig{margin:8px 0;text-align:center}
.fig img{max-width:100%;border:1px solid var(--line);background:#fff;padding:4px;border-radius:6px}
.steps{margin:6px 0 0;padding-left:1.15rem;font-size:13.5px}
.steps li{margin:.28rem 0}
.basis{display:inline-block;margin-left:6px;padding:0 6px;border-radius:9px;
  background:rgba(47,111,208,.12);color:#2f6fd0;font-size:11.5px}
.warm{border-left:3px solid var(--warm);background:rgba(224,163,62,.10);padding:5px 9px;
  border-radius:0 6px 6px 0;margin:6px 0;font-size:13.5px}
/* ⚠️ **不要写 `align-items:start`**：那样右栏格子只有面板本身那么高，
   `position:sticky` 无处可滚 —— 面板会跟着第一屏直接滚走（实测：整页右栏中段全空，
   "图随文动"根本没发生）。默认的 stretch 让格子撑满整行，sticky 才有效。 */
.scrolly{display:grid;grid-template-columns:minmax(0,1fr) 460px;gap:40px;max-width:1300px;
         margin:0 auto;padding:22px 26px 120px}
.prose section{padding:10px 0 26px;border-bottom:1px dashed #eceef2;border-left:3px solid transparent;
                padding-left:12px;transition:border-color .25s,background .25s}
/* 当前段高亮：读者一眼知道右栏在指哪一段（用户 2026-10-01） */
.prose section.on{border-left-color:var(--accent);background:rgba(47,111,208,.035)}
.prose section:last-child{border-bottom:0}
.prose h3{margin:0 0 8px;font-size:16px;color:#0f172a}
.prose img{max-width:min(430px,100%)}
/* 面板**不再二次滚动**（用户 2026-10-01）：去掉 max-height/overflow —— 内容高度由帧自己决定，
   要翻就让整页翻，别在面板里再开一个滚动条。 */
.sticky{position:sticky;top:16px;background:#fff;border:1px solid var(--line);border-radius:12px;
        padding:14px 16px;box-shadow:0 6px 22px rgba(0,0,0,.05)}
.frame{display:none} .frame.on{display:block}
.bar{display:flex;gap:4px;margin-bottom:8px}
.bar i{flex:1;height:4px;border-radius:2px;background:#e8ebf0}
.bar i.on{background:var(--accent)}
.reactive{max-width:1080px;margin:0 auto;padding:22px 26px 110px}
.panelbox{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px}
.card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.ctrl{display:flex;align-items:center;gap:10px;margin:9px 0;font-size:14px}
.ctrl input[type=range]{flex:1;accent-color:var(--accent)}
.val{font-variant-numeric:tabular-nums;min-width:78px;text-align:right;font-weight:600}
.toggle{display:inline-flex;border:1px solid var(--line);border-radius:9px;overflow:hidden}
.toggle button{border:0;background:#fff;padding:5px 12px;cursor:pointer;font:inherit;font-size:14px}
.toggle button.on{background:var(--accent);color:#fff}
.readout{font-size:14.5px;line-height:1.95}
.readout b{color:var(--accent)}
#formula{margin-top:6px;min-height:2.2em}
"""

KASTEX = ("<script src='assets/katex.min.js'></script>"
          "<link rel='stylesheet' href='assets/katex.min.css'>"
          # ⚠️ 必须**页面级**自动渲染一遍：F 原来只在右栏帧里手写渲染，
          #    正文里的 $$...$$ 就没人管了 —— 实测页面上露出 `u(t) = -R i(t) \tag{1-20}` 源码。
          "<script>window.addEventListener('DOMContentLoaded',function(){"
          "document.querySelectorAll('.tex').forEach(function(el){try{katex.render("
          "el.textContent,el,{displayMode:el.classList.contains('block'),throwOnError:false});"
          "el.dataset.done='1';}catch(e){}});});</script>")


def _render_markers(text: str) -> str:
    """把教案标记**全部**渲染掉（第一版最大的 bug 就是漏了这一步）。"""
    text = html.escape(text)
    text = re.sub(r"\[\[FIGURE:([^\]]+)\]\]",
                  lambda m: f'<div class="fig"><img src="assets/figures/{m.group(1)}.png"'
                            f' alt="{m.group(1)}"></div>', text)
    text = re.sub(r"\[\[DIAGRAM:([^\]]+)\]\]",
                  lambda m: f'<span class="chip">{m.group(1)} 过程图</span>', text)
    text = re.sub(r"\[\[EXPLAIN:([^\]|]+)\|([^\]]+)\]\]",
                  lambda m: f'<span class="chip">{m.group(2)}</span>', text)
    text = re.sub(r"\[\[CONCEPT:([^\]|]+)\|([^\]]+)\]\]",
                  lambda m: f'<span class="clink">{m.group(2)}</span>', text)
    text = re.sub(r"\[\[NOTE:([^\]]+)\]\]", '<span class="note">◉</span>', text)
    text = re.sub(r"\$\$(.+?)\$\$",
                  lambda m: f'<span class="tex block">{html.escape(m.group(1))}</span>', text, flags=re.S)
    text = re.sub(r"\$(.+?)\$", lambda m: f'<span class="tex">{html.escape(m.group(1))}</span>', text)
    return text


def _plain(text: str, limit: int = 28) -> str:
    """给段落取小标题：先去标记再截断（第一版直接截 HTML → 空成"…"）。"""
    clean = re.sub(r"\[\[[^\]]+\]\]", "", text)
    clean = re.sub(r"\$+", "", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return (clean[:limit] + "…") if len(clean) > limit else (clean or "（这一段以图开头）")


def _occurrence_counts(parse_dir: Path) -> dict:
    data = json.loads((parse_dir / "concepts.json").read_text(encoding="utf-8"))
    return {str(c.get("name")): len(c.get("occurrences") or [])
            for c in (data.get("concepts") or [])}


def build_f(lesson: dict, body: str, out: Path, parse_dir: Path) -> None:
    explain = {str(x.get("id")): x for x in (lesson.get("explainables") or [])}      # name → 全书出现次数（下面多处要用）
    occ = _occurrence_counts(parse_dir)      # name → 全书出现次数（下面多处要用）
    # ⚠️ 教案里的 `concepts` 只有 id/role/**没有名字** —— 用它做兜底等于兜了个空。
    #    名字要从**概念表**按"本节出现"取（并带上全书出现次数排序）。
    _uid = str(lesson.get("unit_id") or "")
    _data = json.loads((parse_dir / "concepts.json").read_text(encoding="utf-8"))
    unit_concepts = sorted(
        [str(c.get("name")) for c in (_data.get("concepts") or [])
         if c.get("name") and any(o.get("unit_id") == _uid for o in (c.get("occurrences") or []))],
        key=lambda n: -occ.get(n, 1))[:8]
    paras = [p.strip() for p in body.split("\n\n") if p.strip()]
    picked = [p for p in paras if re.search(r"\[\[(FIGURE|EXPLAIN|DIAGRAM|CONCEPT):", p)][:6] or paras[:5]

    frames, sections = [], []
    for i, para in enumerate(picked):
        parts = []
        # ⚠️ **右栏不重复正文里已经显示的图**（2026-10-01 用户："正文与右栏重复显示同一张图"）：
        #    图一律留在正文流里，右栏只补"正文没有的东西"（详解/分步/warmup/跨节概念）；
        #    某段确实只有图、没有别的可补时，给一句说明，而不是把同一张图再画一遍。
        if re.search(r"\[\[FIGURE:", para):
            parts.append('<div class="small">（这一段的图在正文里，右栏只补讲解）</div>')
        # 这一段如果"除了图什么都没有"，右栏就退到**本段所属小节的概念**（带 ×N），
        # 别让读者面对一块空白（用户 2026-10-01："内容又没有图也没有一片空白"）。
        paras_names = re.findall(r"\[\[CONCEPT:[^\]|]+\|([^\]]+)\]\]", para)
        for eid in re.findall(r"\[\[EXPLAIN:([^\]|]+)", para)[:2]:
            item = explain.get(eid)
            if not item:
                continue
            parts.append(f'<div class="k" style="margin-top:8px">【{KIND_CN.get(str(item.get("kind")), "")}】'
                         f'{html.escape(str(item.get("name") or ""))}</div>')
            if item.get("what"):
                parts.append(f'<div class="small">{_render_markers(str(item["what"]))}</div>')
            if item.get("warmup"):
                parts.append(f'<div class="warm">先用一个简单情形：'
                             f'{_render_markers(str(item["warmup"]))}</div>')
            steps = item.get("walkthrough") or []
            if steps:
                parts.append("<ol class='steps'>" + "".join(
                    f'<li>{_render_markers(str(x.get("claim") or ""))}'
                    f'<span class="basis">{_render_markers(str(x.get("basis") or ""))}</span></li>'
                    for x in steps) + "</ol>")
        for m in re.findall(r"\$\$(.+?)\$\$", para, flags=re.S)[:2]:
            parts.append(f'<span class="tex block">{html.escape(m.strip())}</span>')
        names = re.findall(r"\[\[CONCEPT:[^\]|]+\|([^\]]+)\]\]", para)
        if names:
            parts.append('<div class="k" style="margin-top:8px">这一段涉及的概念（×N = 全书出现几处）</div>')
            parts.append("".join(f'<span class="chip">{html.escape(n)}'
                                 f'<span style="color:#6b7280"> ×{occ.get(n, 1)}</span></span>'
                                 for n in names))
        if not re.search(r"\[\[(EXPLAIN|CONCEPT):", para):
            # 这一段的标记里没有概念/详解（多半只有图）→ 补上本节的跨节概念
            names_here = re.findall(r"\[\[CONCEPT:[^\]|]+\|([^\]]+)\]\]", para)
            if not names_here and unit_concepts:
                parts.append('<div class="k" style="margin-top:8px">本节涉及的概念（×N = 全书出现几处）</div>')
                parts.append("".join(f'<span class="chip">{html.escape(c)}'
                                     f'<span style="color:#6b7280"> ×{occ.get(c, 1)}</span></span>'
                                     for c in unit_concepts[:8]))
        # **统一后置检查**（2026-10-01）：组装完发现"太空"就补本节概念 ——
        # 不再用"有没有某类标记"这种前判断（实测漏了只有 `[[DIAGRAM:…]]` 的段落，
        # 于是出现一帧只剩 18 个字）。
        joined = "".join(parts)
        if len(re.sub(r"<[^>]+>", "", joined).strip()) < 60 and unit_concepts:
            joined += '<div class="k" style="margin-top:8px">本节涉及的概念（×N = 全书出现几处）</div>'
            joined += "".join(f'<span class="chip">{html.escape(c)}'
                              f'<span style="color:#6b7280"> ×{occ.get(c, 1)}</span></span>'
                              for c in unit_concepts[:8])
        frames.append(joined or '<div class="small">（这一段没有图 / 详解 / 公式）</div>')
        sections.append(f'<section data-frame="{i}"><h3>{html.escape(_plain(para))}</h3>'
                        f'<p>{_render_markers(para)}</p></section>')

    panel = "".join(f'<div class="frame" data-frame="{i}">{frames[i]}</div>' for i in range(len(frames)))
    bar = "".join("<i></i>" for _ in frames)
    page = f"""<!doctype html><html lang="zh"><head><meta charset='utf-8'>
<title>演示 F · 图随文动</title><style>{CSS}</style>{KASTEX}</head><body>
<header class="top"><h1>演示 F · 图随文动</h1>
<div class="hint">右栏永远是<b>当前这一段自己</b>的东西：它的配图、它引用的那条详解（含分步与依据）、
那一段的公式、以及这一段涉及的<b>跨节概念（×N = 全书出现几处）</b>。往下滚，右栏跟着换；
JS 关掉也能正常读（右栏只是指路）。</div></header>
<main class="scrolly">
  <div class="prose">{''.join(sections)}</div>
  <div><div class="sticky">
    <div class="bar" id="bar">{bar}</div>
    <div class="k" style="margin-bottom:6px">当前段落 · 图 / 详解 / 公式 / 概念</div>
    {panel}
  </div></div>
</main>
<script>
var frames = Array.prototype.slice.call(document.querySelectorAll(".frame"));
var bars = Array.prototype.slice.call(document.querySelectorAll("#bar i"));
function activate(i){{
  frames.forEach(function(f,k){{ f.classList.toggle("on", k === i); }});
  bars.forEach(function(b,k){{ b.classList.toggle("on", k <= i); }});
  document.querySelectorAll(".prose section").forEach(function(s,k){{
    s.classList.toggle("on", k === i);          // 当前段高亮
  }});
  if (window.katex) {{
    frames[i].querySelectorAll(".tex").forEach(function(el){{
      if (el.dataset.done) return;
      try {{ katex.render(el.textContent, el, {{displayMode: el.classList.contains("block"),
                                               throwOnError: false}});
             el.dataset.done = "1"; }} catch(e) {{}}
    }});
  }}
}}
var obs = new IntersectionObserver(function(entries){{
  entries.forEach(function(e){{ if(e.isIntersecting) activate(parseInt(e.target.dataset.frame||"0",10)); }});
}}, {{rootMargin:"-35% 0px -55% 0px", threshold:0}});
document.querySelectorAll(".prose section").forEach(function(s){{ obs.observe(s); }});
activate(0);
</script></body></html>"""
    out.write_text(page, encoding="utf-8")
    print(f"✓ {out}（{len(frames)} 段，每段一帧；概念角标来自真数据）")


def build_g(out: Path) -> None:
    """可动手的欧姆定律。记号照书：u 的参考方向 A(+) → B(−)；
    电流**从 + 端流入 = 关联** → 电阻内部 A→B（向右），`u = +R i`（式 1-19）；
    从 − 端流入 = 非关联 → 内部 B→A，`u = −R i`（式 1-20）。"""
    f_assoc = "u(t) = R\\,i(t)"
    f_neg = "u(t) = -R\\,i(t)"
    page = f"""<!doctype html><html lang="zh"><head><meta charset='utf-8'>
<title>演示 G · 可动手的欧姆定律</title><style>{CSS}</style>{KASTEX}</head><body>
<header class="top"><h1>演示 G · 可动手的图（真算）</h1>
<div class="hint">拖电流、调电阻、切换<b>关联 / 非关联</b>参考方向：电阻内部的电流箭头、u–i 直线、
数值与公式同时变。可调的量只有这个例子本来就在讲的那三个 —— 没有多余控件，静态读也完整。</div></header>
<main class="reactive">
  <div class="panelbox">
    <div class="card">
      <div class="k">电路（u 的参考方向：A 为 +、B 为 −）</div>
      <svg viewBox="0 0 380 190" style="width:100%;height:auto">
        <line x1="60" y1="150" x2="330" y2="150" stroke="#333" stroke-width="1.6"/>
        <line x1="60" y1="150" x2="60" y2="80" stroke="#333" stroke-width="1.6"/>
        <line x1="60" y1="80" x2="140" y2="80" stroke="#333" stroke-width="1.6"/>
        <line x1="240" y1="80" x2="330" y2="80" stroke="#333" stroke-width="1.6"/>
        <line x1="330" y1="80" x2="330" y2="150" stroke="#333" stroke-width="1.6"/>
        <rect x="140" y="62" width="100" height="36" fill="#fff" stroke="#333" stroke-width="1.6"/>
        <text x="190" y="52" text-anchor="middle" font-size="13" fill="#2f6fd0">u</text>
        <text x="126" y="58" font-size="15" fill="#333">+</text>
        <text x="248" y="58" font-size="15" fill="#333">−</text>
        <text x="190" y="86" text-anchor="middle" font-size="13">R</text>
        <polygon id="ihead" points="176,74 188,80 176,86" fill="#e0a33e"/>
        <line id="iline" x1="150" y1="80" x2="182" y2="80" stroke="#e0a33e" stroke-width="2.4"/>
        <text id="ilabel" x="190" y="118" text-anchor="middle" font-size="12.5" fill="#b57f1f"></text>
      </svg>
      <div class="ctrl"><span>电流 i</span>
        <input id="cur" type="range" min="0" max="0.5" step="0.01" value="0.1">
        <span class="val" id="curv"></span></div>
      <div class="ctrl"><span>电阻 R</span>
        <input id="res" type="range" min="10" max="500" step="5" value="100">
        <span class="val" id="resv"></span></div>
      <div class="ctrl"><span>参考方向</span>
        <span class="toggle"><button id="pos" class="on">关联</button>
        <button id="neg">非关联</button></span></div>
    </div>
    <div class="card">
      <div class="k">u–i 关系（纵轴中点 = 0 V；直线斜率 = ±R）</div>
      <svg viewBox="0 0 380 280" style="width:100%;height:auto">
        <g id="grid" stroke="#eef1f5"></g>
        <line x1="46" y1="140" x2="366" y2="140" stroke="#9aa4b2"/>
        <line x1="46" y1="264" x2="46" y2="16" stroke="#9aa4b2"/>
        <g id="ticks" font-size="10.5" fill="#9aa4b2"></g>
        <line id="line" x1="46" y1="140" x2="330" y2="60" stroke="#2f6fd0" stroke-width="2"/>
        <circle id="dot" cx="110" cy="106" r="5" fill="#e0a33e"/>
        <text id="dotlabel" x="118" y="102" font-size="12" fill="#b57f1f"></text>
        <text x="368" y="156" font-size="11.5" text-anchor="end" fill="#6b7280">i / A</text>
        <text x="52" y="24" font-size="11.5" fill="#6b7280">u / V</text>
      </svg>
      <div class="readout" id="readout"></div>
    </div>
  </div>
  <div class="card" style="margin-top:16px">
    <div class="k">同一件事的三种写法（数值 / 公式 / 图 —— 多重表征）</div>
    <div class="readout" id="forms"></div>
    <div id="formula"></div>
  </div>
</main>
<script>
var cur = document.getElementById("cur"), res = document.getElementById("res");
var state = {{assoc: true}};
var IMAX = 0.5, UMAX = 250, SCALE = 110 / 250;      // px / V
var X0 = 46, Y0 = 140, XW = 284;
function fmt(x, d){{ return Number(x).toFixed(d === undefined ? 2 : d); }}
function drawAxes(){{
  var grid = document.getElementById("grid"), ticks = document.getElementById("ticks");
  var g = "", t = "";
  for (var u = -250; u <= 250; u += 50) {{
    var y = Y0 - u * SCALE;
    g += '<line x1="' + X0 + '" y1="' + y.toFixed(1) + '" x2="' + (X0 + XW) + '" y2="' + y.toFixed(1) + '"/>';
    if (u % 100 === 0) t += '<text x="' + (X0 - 6) + '" y="' + (y + 3.5).toFixed(1) +
                            '" text-anchor="end">' + u + '</text>';
  }}
  for (var i = 0; i <= IMAX + 1e-9; i += 0.1) {{
    var x = X0 + (i / IMAX) * XW;
    g += '<line x1="' + x.toFixed(1) + '" y1="' + (Y0 - 130) + '" x2="' + x.toFixed(1) +
         '" y2="' + (Y0 + 130) + '"/>';
    t += '<text x="' + x.toFixed(1) + '" y="' + (Y0 + 150) + '" text-anchor="middle">' +
         i.toFixed(1) + '</text>';
  }}
  grid.innerHTML = g; ticks.innerHTML = t;
}}
function setFormula(tex){{
  var el = document.getElementById("formula");
  el.innerHTML = "";                                  // 必须清空：否则 KaTeX 会叠加渲染
  if (!window.katex) {{ el.textContent = tex; return; }}
  try {{ katex.render(tex, el, {{displayMode: true, throwOnError: false}}); }}
  catch (e) {{ el.textContent = tex; }}
}}
function update(){{
  var i = parseFloat(cur.value), R = parseFloat(res.value);
  var sign = state.assoc ? 1 : -1, u = sign * R * i;
  document.getElementById("curv").textContent = fmt(i) + " A";
  document.getElementById("resv").textContent = fmt(R, 0) + " Ω";
  var head = document.getElementById("ihead"), line = document.getElementById("iline");
  if (state.assoc) {{          // 关联：电流从 + 端流入 → 电阻内部 A→B（**向右**）
    line.setAttribute("x1", 150); line.setAttribute("x2", 182);
    head.setAttribute("points", "176,74 188,80 176,86");   // ← apex 在右边
  }} else {{                   // 非关联：从 − 端流入 → 内部 B→A（**向左**）
    line.setAttribute("x1", 230); line.setAttribute("x2", 198);
    head.setAttribute("points", "204,74 192,80 204,86");   // ← apex 在左边
  }}
  document.getElementById("ilabel").textContent =
      state.assoc ? "i 从 + 端流入（A → B）" : "i 从 − 端流入（B → A）";
  var iEnd = IMAX, uEnd = sign * R * iEnd;            // 超出 ±250 V 就截断
  if (Math.abs(uEnd) > UMAX) {{ iEnd = UMAX / R; uEnd = sign * UMAX; }}
  var x2 = X0 + (iEnd / IMAX) * XW, y2 = Y0 - uEnd * SCALE;
  var ln = document.getElementById("line");
  ln.setAttribute("x2", x2.toFixed(1)); ln.setAttribute("y2", y2.toFixed(1));
  var px = X0 + (i / IMAX) * XW, py = Y0 - u * SCALE;
  var dot = document.getElementById("dot");
  dot.setAttribute("cx", px.toFixed(1)); dot.setAttribute("cy", py.toFixed(1));
  var lab = document.getElementById("dotlabel");
  lab.setAttribute("x", (px + 8).toFixed(1)); lab.setAttribute("y", (py - 8).toFixed(1));
  lab.textContent = "(" + fmt(i) + " A, " + fmt(u) + " V)";
  document.getElementById("readout").innerHTML =
    (state.assoc ? "关联参考方向" : "<b>非关联</b>参考方向") + "：u = " + (state.assoc ? "" : "−") +
    "R i = " + (state.assoc ? "" : "−") + fmt(R, 0) + " × " + fmt(i) + " = <b>" + fmt(u) + " V</b>" +
    // ⚠️ **吸收功率**要按参考方向说清楚：`p = u i` 只在**关联**方向下才是吸收功率；
    //    非关联时吸收功率 = −u i（否则会算出"电阻发出 1 W"这种反物理的数，实测踩过）
    "<br>吸收功率 " + (state.assoc ? "p = u i" : "p = −u i") + " = <b>" + fmt(R * i * i) + " W</b>" +
    (state.assoc ? "" : "（注意：非关联下 u i = " + fmt(u * i) + " W，取负号才是它吸收的）") +
    "　｜　直线斜率 = " + (state.assoc ? "+" : "−") + fmt(R, 0) + " V/A";
  document.getElementById("forms").innerHTML =
    "写成 R：R = u / i = <b>" + fmt(R, 0) + " Ω</b>　｜　写成 G：G = 1/R = <b>" + fmt(1 / R, 4) +
    " S</b>　｜　" + (state.assoc ? "u 与 i 成正比" : "u 与 i 差一个负号");
  setFormula(state.assoc ? {json.dumps(f_assoc)} : {json.dumps(f_neg)});
}}
document.getElementById("pos").onclick = function(){{
  state.assoc = true; this.classList.add("on");
  document.getElementById("neg").classList.remove("on"); update(); }};
document.getElementById("neg").onclick = function(){{
  state.assoc = false; this.classList.add("on");
  document.getElementById("pos").classList.remove("on"); update(); }};
cur.oninput = update; res.oninput = update;
drawAxes(); update();
</script></body></html>"""
    out.write_text(page, encoding="utf-8")
    print(f"✓ {out}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成 F（图随文动）/ G（可动手）演示")
    ap.add_argument("--lesson", required=True)
    ap.add_argument("--parse", default="data/parse/circuit-analysis-1")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    lesson_path = ROOT / args.lesson if not Path(args.lesson).is_absolute() else Path(args.lesson)
    lesson = json.loads(lesson_path.read_text(encoding="utf-8"))
    out_dir = ROOT / args.out if not Path(args.out).is_absolute() else Path(args.out)
    parse_dir = ROOT / args.parse if not Path(args.parse).is_absolute() else Path(args.parse)
    build_f(lesson, str(lesson.get("body_markdown") or ""), out_dir / "demo-f-scrolly.html", parse_dir)
    build_g(out_dir / "demo-g-reactive.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
