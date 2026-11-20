#!/usr/bin/env python3
"""生成两个**界面演示页**（同一个单元、同一批内容，两种交互）：

* `demo-a-inline.html`：**正文内就地展开** + 右侧「本节目录」（点一下滚到那句话）；
* `demo-b-sidenote.html`：**Tufte 式边注**（讲解挂在正文外侧、随段落对齐）。

为什么单独写演示页、不动生产渲染层：先让用户看效果、挑一个，再改 `app.js`/`styles.css`。
演示页复用站点里的 `assets/figures/*.png` 与 `assets/katex.min.js`，所以必须生成到
`data/site/<书>/` 目录里。

用法：
```bash
python tools/make_ui_demo.py data/lesson/circuit-analysis-1/lessons/CH01-S04.json \
       --concepts data/parse/circuit-analysis-1/concepts.json \
       --out data/site/circuit-analysis-1
```
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
:root { --ink:#1a1a1a; --muted:#6b7280; --line:#e5e7eb; --accent:#2c5f9e;
        --warm:#d9a441; --bg:#fbfaf7; --card:#fff; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
       font:17px/1.85 "Noto Serif SC","Songti SC",Georgia,serif; }
a { color:var(--accent); }
header.top { padding:18px 28px; border-bottom:1px solid var(--line); background:#fff; }
header.top h1 { margin:0 0 4px; font-size:20px; }
header.top .hint { color:var(--muted); font-size:13px; }
main { max-width:1180px; margin:0 auto; padding:26px 28px 80px; }
p { margin:0 0 1.05em; }
.fig { margin:1.1rem 0; text-align:center; }
.fig img { max-width:min(520px,100%); border:1px solid var(--line); background:#fff; padding:6px; }
.math-block { margin:1rem 0; text-align:center; }
.mono { font-family:"SFMono-Regular",Consolas,monospace; font-size:.92em;
        background:#f3f4f6; border-radius:4px; padding:1px 5px; }
.explain-chip { display:inline-block; margin:0 2px; padding:0 7px; border-radius:11px;
  border:1px dashed var(--accent); color:var(--accent); background:#f4f8fd;
  font-size:.82em; cursor:pointer; }
.concept { border-bottom:1px dotted var(--muted); cursor:help; }
.note { color:var(--warm); font-weight:700; cursor:pointer; }
/* 详解块（两种版式共用外观） */
.detail { border-left:3px solid var(--accent); background:var(--card);
  border-radius:0 8px 8px 0; padding:12px 16px; margin:12px 0; }
.detail h4 { margin:0 0 8px; font-size:15px; }
.detail .k { color:var(--muted); font-size:13px; }
.detail .warm { border-left:3px solid var(--warm); background:rgba(217,164,65,.10);
  padding:6px 10px; border-radius:0 6px 6px 0; margin:8px 0; font-size:15px; }
.detail ol { margin:8px 0 0; padding-left:1.25rem; }
.detail ol li { margin:.3rem 0; }
.basis { display:inline-block; margin-left:6px; padding:0 7px; border-radius:10px;
  background:rgba(64,132,224,.12); color:#2c5f9e; font-size:.8em; }
"""

KASTEX = """
<script src="assets/katex.min.js"></script>
<link rel="stylesheet" href="assets/katex.min.css">
<script>
window.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll(".tex").forEach(function (el) {
    try { katex.render(el.textContent, el, {displayMode: el.classList.contains("block"),
                                            throwOnError: false}); } catch (e) {}
  });
});
</script>
"""


def _math_html(text: str) -> str:
    """把 `$$…$$` / `$…$` 换成 KaTeX 容器（渲染失败就留着源码）。"""
    def block(m):
        return f'<div class="math-block tex block">{html.escape(m.group(1))}</div>'
    text = re.sub(r"\$\$(.+?)\$\$", block, text, flags=re.S)
    text = re.sub(r"\$(.+?)\$", lambda m: f'<span class="tex">{html.escape(m.group(1))}</span>',
                  text, flags=re.S)
    return text


def _inline(text: str, explain_titles: dict, concept_names: dict) -> str:
    text = html.escape(text)
    text = re.sub(r"\[\[FIGURE:(IMG-\d+)\]\]",
                  lambda m: f'</p><div class="fig"><img src="assets/figures/{m.group(1)}.png"'
                            f' alt="{m.group(1)}"></div><p>', text)
    text = re.sub(r"\[\[EXPLAIN:([^\|\]]+)\|([^\]]+)\]\]",
                  lambda m: f'<span class="explain-chip" data-detail="{m.group(1)}">'
                            f'{m.group(2)}</span>', text)
    text = re.sub(r"\[\[CONCEPT:([^\|\]]+)\|([^\]]+)\]\]",
                  lambda m: f'<span class="concept" title="{m.group(2)}">{m.group(2)}</span>', text)
    text = re.sub(r"\[\[NOTE:([^\]]+)\]\]", '<span class="note">◉</span>', text)
    return text


def _detail_html(item: dict, anchor: str) -> str:
    kind = KIND_CN.get(str(item.get("kind")), str(item.get("kind")))
    out = [f'<div class="detail" id="{anchor}">',
           f'<h4>【{kind}】{html.escape(str(item.get("name") or ""))}</h4>']
    if item.get("what"):
        out.append(f'<div class="k">它是什么</div><div>{_math_html(html.escape(item["what"]))}</div>')
    if item.get("why"):
        out.append(f'<div class="k" style="margin-top:6px">为什么在这里用</div>'
                   f'<div>{_math_html(html.escape(item["why"]))}</div>')
    if item.get("warmup"):
        out.append(f'<div class="warm"><b>先用一个简单情形看明白</b>：'
                   f'{_math_html(html.escape(item["warmup"]))}</div>')
    steps = item.get("walkthrough") or []
    if steps:
        out.append("<ol>")
        for step in steps:
            basis = f'<span class="basis">依据：{html.escape(str(step.get("basis") or ""))}</span>'
            out.append(f'<li>{_math_html(html.escape(str(step.get("claim") or "")))}{basis}</li>')
        out.append("</ol>")
    if item.get("markdown"):
        out.append(f'<div style="margin-top:8px">{_math_html(html.escape(item["markdown"]))}</div>')
    out.append("</div>")
    return "".join(out)


def build(lesson: dict, out_dir: Path) -> tuple[Path, Path]:
    body = str(lesson.get("body_markdown") or "")
    explain = {str(x.get("id")): x for x in (lesson.get("explainables") or [])}
    # 演示只取前 16 段（够看出两种版式），并保证里面有图、有 chip、有公式
    paras = [p.strip() for p in body.split("\n\n") if p.strip()][:16]
    demo_ids = [fid for fid in ("F-0005", "F-0050", "F-01", "F-02") if fid in explain][:3]
    if not demo_ids:
        demo_ids = list(explain)[:3]

    body_html = "".join(f"<p>{_inline(p, {}, {})}</p>" for p in paras)
    body_html = _math_html(body_html)

    details = "".join(_detail_html(explain[fid], f"d-{fid}") for fid in demo_ids)
    toc = "".join(
        f'<li><a href="#d-{fid}">{html.escape(str(explain[fid].get("name") or fid))}'
        f'<span class="k"> · {KIND_CN.get(str(explain[fid].get("kind")), "")}</span></a></li>'
        for fid in demo_ids)

    # ⚠️ 不能对 head 用 `str.format`：CSS 里全是花括号（实测当场 KeyError）。
    head = ("<meta charset='utf-8'><title>@@T@@</title>"
            f"<style>{CSS}</style>{KASTEX}")

    # ── A：正文内就地展开 + 右侧「本节目录」 ──
    a_js = """
<script>
document.addEventListener("click", function (ev) {
  var chip = ev.target.closest(".explain-chip");
  if (!chip) return;
  var box = document.getElementById("d-" + chip.dataset.detail);
  if (!box) return;
  var host = chip.closest("p");
  var holder = host.querySelector(".detail-slot[data-for='" + chip.dataset.detail + "']");
  if (holder) { holder.remove(); return; }          // 再点一次收起
  holder = document.createElement("div");
  holder.className = "detail-slot"; holder.dataset.for = chip.dataset.detail;
  holder.innerHTML = box.outerHTML;
  host.appendChild(holder);
});
</script>
"""
    a = f"""<!doctype html><html lang="zh"><head>{head.replace('@@T@@', '演示 A · 就地展开')}</head>
<body><header class="top"><h1>演示 A · 正文内就地展开 ＋ 右侧「本节目录」</h1>
<div class="hint">点正文里的虚框胶囊（如「它到底是什么？」）→ 讲解**就地**展开在那一句下面，再点一次收起。
右侧只列本节的概念/定理锚点，点一下跳到正文里那条详解。</div></header>
<main style="display:grid;grid-template-columns:minmax(0,1fr) 250px;gap:36px">
  <article>{body_html}
    <div class="detail-slot-store" hidden>{details}</div>
  </article>
  <aside style="position:sticky;top:20px;align-self:start">
    <div class="k" style="margin-bottom:6px">本节目录（{len(demo_ids)} 条详解）</div>
    <ul style="list-style:none;padding:0;margin:0;line-height:2">{toc}</ul>
  </aside>
</main>{a_js}</body></html>"""

    # ── B：Tufte 式边注 ──
    b = f"""<!doctype html><html lang="zh"><head>{head.replace('@@T@@', '演示 B · 边注版')}</head>
<style>
/* Tufte 式边注：正文一列、边注一列，边注与它解释的段落**顶对齐** */
.sidenote-wrap {{ display:grid; grid-template-columns:minmax(0,1fr) 300px; gap:34px;
                  align-items:start; }}
.sidenote {{ font-size:14px; line-height:1.7; color:#374151; }}
.sidenote .detail {{ margin:0 0 18px; }}
@media (max-width:1000px) {{ .sidenote-wrap {{ grid-template-columns:1fr; }} }}
</style></head>
<body><header class="top"><h1>演示 B · Tufte 式边注（讲解挂在正文外侧）</h1>
<div class="hint">讲解不再塞进一个常驻列表，而是**贴着它解释的那一段**放在外侧窄栏，
随段落顶对齐；阅读时眼睛只横移一次，不用跳两个滚动条。</div></header>
<main>
  <div class="sidenote-wrap">
    <article>{body_html[: len(body_html) // 2]}</article>
    <aside class="sidenote">{details}</aside>
  </div>
  <div class="sidenote-wrap" style="margin-top:28px">
    <article>{body_html[len(body_html) // 2:]}</article>
    <aside class="sidenote"></aside>
  </div>
</main></body></html>"""

    out_dir = Path(out_dir)
    a_path = out_dir / "demo-a-inline.html"
    b_path = out_dir / "demo-b-sidenote.html"
    a_path.write_text(a, encoding="utf-8")
    b_path.write_text(b, encoding="utf-8")
    return a_path, b_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成两个界面演示页")
    ap.add_argument("lesson")
    ap.add_argument("--concepts", help="（预留）概念表")
    ap.add_argument("--out", required=True, help="站点目录（要有 assets/figures 与 katex）")
    args = ap.parse_args(argv)
    lesson = json.loads((ROOT / args.lesson).read_text(encoding="utf-8")
                        if not Path(args.lesson).is_absolute() else Path(args.lesson).read_text(encoding="utf-8"))
    a, b = build(lesson, ROOT / args.out if not Path(args.out).is_absolute() else Path(args.out))
    print(f"✓ {a}\n✓ {b}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
