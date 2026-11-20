#!/usr/bin/env python3
"""**学习模板**：仿参考图（Academic Press 那种教材阅读页）的单文件页面。

版块 ↔ 数据来源（全部真数据，不造假）：

| 版块 | 数据 |
|---|---|
| 顶部导航 + 搜索 | 页内锚点 + 纯 JS 过滤（本单元概念/例题） |
| 左侧章节目录树 | `chapters.json` |
| 左侧**概念解析**（按类型分组） | `concepts.json`（definition/law/theorem/property/formula…） |
| 面包屑 | 书名 / 章 / 节（`chapters.json` + `lesson.anchor`） |
| 正文 + **引用出处行** | `lesson.body_markdown` + 书名与 `§anchor` |
| 大图 + 图注 | 手绘 SVG（网孔电流）+ `figures.json.images[].caption`（缺则退回 `图 <id>`） |
| **核心推导过程** | `lesson.explainables[].walkthrough[{step, claim, basis}]` |
| **注意**高亮块 | `lesson.annotations[].kind`（derive/intuition/note/pitfall） |
| **概念详解**折叠卡 | `concepts` + `explainables`（解释/配图/推导**三件可独立出现**） |
| 例题（逐步 + 依据） | `lesson.examples[]` |
| 页脚 + **导出为 PDF** | `@media print` + 打印前自动展开 `<details>` |

用法：
```bash
python tools/make_study_template.py --parse data/parse/circuit-analysis-1 \
    --lesson data/lesson/circuit-analysis-1/lessons/CH02-S01.json \
    --site data/site/circuit-analysis-1 --unit CH02-S01
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
sys.path.insert(0, str(ROOT / "tools"))
from pack_demo import pack                                      # noqa: E402

KIND_CN = {"definition": "定义", "theorem": "定理", "law": "定律", "property": "性质",
           "proposition": "命题", "corollary": "推论", "formula": "公式", "concept": "概念"}
#: 概念解析分组顺序（参考图那种"先定义、后定理、再公式"的阅读顺序）
GROUP_ORDER = ["definition", "law", "theorem", "property", "formula", "proposition"]
NOTE_CN = {"note": "边注", "derive": "推导", "pitfall": "易错", "intuition": "直觉"}

CSS = """
/* ══ 设计令牌（换肤只改这里）═════════════════════════════════════════
   字阶 1.250（Major Third 收敛版）｜间距 4·8·12·16·24·32·48 ｜行高 1.35/1.7/1.95
   颜色：中性灰阶 + 单一强调色（靛蓝），面=4~6% 透明，线=发丝 1px soft。
   ══════════════════════════════════════════════════════════════════ */
:root{
  /* 颜色 */
  --ink:#15181d; --ink-2:#3b4149; --ink-3:#6b7280; --ink-4:#9aa1ab;
  --line:#e8eaee; --line-2:#f1f3f6; --paper:#ffffff; --paper-2:#fcfcfd; --paper-3:#f7f8fa;
  --accent:#3b5bdb; --accent-ink:#2b46b8; --accent-tint:rgba(59,91,219,.07);
  --accent-line:rgba(59,91,219,.28);
  --c-text:#3b5bdb; --c-fig:#0e7c6b; --c-derive:#a35a09; --c-concept:#6d28d9;
  --danger:#d92d20; --info:#0b7285; --good:#0e7c6b;
  /* 字阶 */
  --fs-11:11px; --fs-12:12px; --fs-13:13px; --fs-14:14px; --fs-15:15px;
  --fs-17:17px; --fs-19:19px; --fs-23:23px; --fs-28:28px; --fs-34:34px;
  --font-serif:"Noto Serif SC","Source Han Serif SC","Songti SC",Georgia,"Times New Roman",serif;
  --font-sans:"Noto Sans SC","PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,-apple-system,sans-serif;
  --font-mono:ui-monospace,"SFMono-Regular",Consolas,"Liberation Mono",monospace;
  /* 间距与形状 */
  --s1:4px; --s2:8px; --s3:12px; --s4:16px; --s5:24px; --s6:32px; --s7:48px;
  --r-sm:6px; --r:10px; --r-lg:14px; --r-pill:999px;
  --measure:74ch; --sidebar:236px; --topbar:60px;
  /* 阴影层级（发丝+极浅投影，避免"塑料感"） */
  --sh-1:0 1px 2px rgba(16,24,40,.04);
  --sh-2:0 1px 2px rgba(16,24,40,.04), 0 8px 24px -14px rgba(16,24,40,.18);
  /* 动效 */
  --dur:.18s; --dur-2:.28s; --ease:cubic-bezier(.2,.7,.2,1);
  --serif:var(--font-serif); --sans:var(--font-sans);
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;scroll-behavior:smooth;scroll-padding-top:calc(var(--topbar) + 16px)}
body{margin:0;background:var(--paper);color:var(--ink);
  font:var(--fs-17)/2.02 var(--font-serif);
  font-kerning:normal;text-rendering:optimizeLegibility;
  -webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale}
a{color:var(--accent);text-decoration:none;text-underline-offset:2px}
a:hover{color:var(--accent-ink);text-decoration:underline;text-decoration-thickness:1px}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:var(--r-sm)}
code{font-family:var(--font-mono);font-size:.88em;background:var(--paper-3);
  border:1px solid var(--line);border-radius:var(--r-sm);padding:1px 5px;color:var(--ink-2)}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-thumb{background:#dfe3e9;border:3px solid transparent;background-clip:content-box;border-radius:99px}
::-webkit-scrollbar-thumb:hover{background:#cbd2db;background-clip:content-box}

/* ── 顶栏 ────────────────────────────────────────────────────────── */
header.top{position:sticky;top:0;z-index:40;height:var(--topbar);display:flex;align-items:center;
  gap:var(--s5);padding:0 var(--s5);background:rgba(255,255,255,.86);
  backdrop-filter:saturate(180%) blur(12px);border-bottom:1px solid var(--line)}
header.top .brand{font:600 var(--fs-15)/1.25 var(--font-sans);letter-spacing:.01em;white-space:nowrap}
header.top .brand small{display:block;margin-top:2px;font:400 var(--fs-11)/1 var(--font-sans);
  color:var(--ink-4);letter-spacing:.14em}
header.top nav{display:flex;gap:var(--s4);font:500 var(--fs-14)/1 var(--font-sans)}
header.top nav a{color:var(--ink-2);padding:6px 2px;border-bottom:2px solid transparent;
  transition:color var(--dur) var(--ease),border-color var(--dur) var(--ease)}
header.top nav a:hover{color:var(--ink);text-decoration:none}
header.top nav a.on{color:var(--accent);border-bottom-color:var(--accent)}
header.top .spacer{flex:1}
header.top input[type=search]{width:236px;height:34px;padding:0 var(--s4);border:1px solid var(--line);
  border-radius:var(--r-pill);font:var(--fs-14)/1 var(--font-sans);color:var(--ink);background:var(--paper-3);
  transition:border-color var(--dur) var(--ease),background var(--dur) var(--ease),box-shadow var(--dur) var(--ease)}
header.top input[type=search]::placeholder{color:var(--ink-4)}
header.top input[type=search]:focus{outline:none;background:#fff;border-color:var(--accent-line);
  box-shadow:0 0 0 3px var(--accent-tint)}
header.top button{height:34px;padding:0 var(--s4);border:1px solid var(--accent-line);border-radius:var(--r-pill);
  background:var(--accent-tint);color:var(--accent-ink);font:600 var(--fs-13)/1 var(--font-sans);cursor:pointer;
  transition:background var(--dur) var(--ease),transform var(--dur) var(--ease)}
header.top button:hover{background:rgba(59,91,219,.14)}
header.top button:active{transform:translateY(1px)}

/* ── 版心：左栏 + 正文（正文列宽控制在 --measure）────────────────── */
.grid{display:grid;grid-template-columns:var(--sidebar) minmax(0,1fr);gap:80px;
  max-width:1240px;margin:0 auto;padding:var(--s7) var(--s7) 120px}
aside.side{position:sticky;top:calc(var(--topbar) + var(--s5));align-self:start;
  max-height:calc(100vh - var(--topbar) - var(--s6));overflow:auto;padding-right:var(--s3);
  border-right:1px solid var(--line-2)}
aside.side h2{font:600 var(--fs-12)/1 var(--font-sans);color:var(--ink-4);letter-spacing:.12em;
  text-transform:uppercase;margin:var(--s5) 0 var(--s2);padding-left:2px}
aside.side h2:first-child{margin-top:0}
ol.tree{list-style:none;margin:0;padding:0;font:var(--fs-14)/1.5 var(--font-sans)}
ol.tree li{margin:1px 0}
ol.tree li.chapter{font-weight:600;color:var(--ink-2);margin:var(--s4) 0 var(--s2);padding-left:2px;
  font-size:var(--fs-13);letter-spacing:.01em}
ol.tree a{display:block;padding:7px var(--s2) 7px 12px;border-left:2px solid transparent;
  border-radius:0 var(--r-sm) var(--r-sm) 0;color:var(--ink-2);transition:all var(--dur) var(--ease)}
ol.tree a:hover{background:var(--paper-3);color:var(--ink);text-decoration:none}
ol.tree a.on{border-left-color:var(--accent);background:var(--accent-tint);color:var(--accent-ink);font-weight:600}
.cgroup{border-top:1px solid var(--line-2);padding:var(--s2) 0 2px}
.cgroup:first-of-type{border-top:0}
.cgroup>summary{cursor:pointer;list-style:none;font:600 var(--fs-13)/1.6 var(--font-sans);color:var(--ink-2);
  padding:2px 0;display:flex;align-items:center;gap:6px}
.cgroup>summary::-webkit-details-marker{display:none}
.cgroup>summary::before{content:"";width:6px;height:6px;border-right:1.5px solid var(--ink-4);
  border-bottom:1.5px solid var(--ink-4);transform:rotate(-45deg);transition:transform var(--dur) var(--ease);
  margin-left:2px;flex:none}
.cgroup[open]>summary::before{transform:rotate(45deg)}
.cgroup .item{display:flex;align-items:baseline;gap:6px;padding:8px var(--s2) 8px 12px;
  border-left:2px solid transparent;border-radius:0 var(--r-sm) var(--r-sm) 0;
  font:var(--fs-14)/1.5 var(--font-sans);color:var(--ink-2);transition:all var(--dur) var(--ease)}
.cgroup .item:hover{background:var(--paper-3);color:var(--ink);text-decoration:none}
.cgroup .item.on{border-left-color:var(--c-concept);background:rgba(109,40,217,.06);color:var(--c-concept)}
.cgroup .item .badge{margin-left:auto;font:500 var(--fs-11)/1 var(--font-sans);color:var(--ink-4);
  background:var(--paper-3);border:1px solid var(--line);border-radius:var(--r-pill);padding:2px 6px}

/* ── 正文 ────────────────────────────────────────────────────────── */
main{min-width:0;max-width:var(--measure)}
nav.crumb{font:var(--fs-13)/1.6 var(--font-sans);color:var(--ink-4);margin-bottom:var(--s5);
  display:flex;gap:6px;flex-wrap:wrap;align-items:center}
nav.crumb a{color:var(--ink-3)}
h1{font-size:var(--fs-34);line-height:1.32;letter-spacing:-.01em;margin:0 0 14px;font-weight:600}
h1 .eyebrow{display:inline-block;margin-right:10px;font:600 var(--fs-15)/1 var(--font-sans);
  color:var(--accent);letter-spacing:.02em;vertical-align:2px}
.subtitle{color:var(--ink-3);font-size:var(--fs-17);margin:0 0 var(--s5);padding-bottom:var(--s4);
  border-bottom:1px solid var(--line)}
p{margin:0 0 22px}
p:last-child{margin-bottom:0}
p.cite{color:var(--ink-4);font-size:var(--fs-13);line-height:1.8;margin:var(--s2) 0 var(--s5);
  padding-left:12px;border-left:2px solid var(--line)}

/* 四类模块：2px 左边框 + 小标签 + 极浅底色（不抢正文） */
.mod{position:relative;border-left:2px solid var(--line);padding:var(--s5) var(--s6) var(--s5) var(--s6);
  margin:var(--s6) 0;border-radius:0 var(--r-lg) var(--r-lg) 0;background:transparent;
  transition:background var(--dur) var(--ease)}
.mod>.tag{display:inline-flex;align-items:center;height:22px;padding:0 10px;margin-bottom:var(--s4);
  border-radius:var(--r-pill);font:600 var(--fs-11)/1 var(--font-sans);letter-spacing:.08em}
.mod-text{border-left-color:var(--accent)}
.mod-text>.tag{background:var(--accent-tint);color:var(--accent-ink)}
.mod-fig{border-left-color:var(--c-fig);background:rgba(14,124,107,.035)}
.mod-fig>.tag{background:rgba(14,124,107,.1);color:var(--c-fig)}
.mod-derive{border-left-color:var(--c-derive);background:rgba(163,90,9,.04)}
.mod-derive>.tag{background:rgba(163,90,9,.12);color:var(--c-derive)}
.mod-concept{border-left-color:var(--c-concept);background:rgba(109,40,217,.035)}
.mod-concept>.tag{background:rgba(109,40,217,.12);color:var(--c-concept)}

/* 图：白卡 + 发丝线 + 双层阴影；图注居中、编号用强调色 */
figure{margin:var(--s6) 0;text-align:center}
figure img,figure svg{max-width:min(640px,100%);background:#fff;border:1px solid var(--line);
  border-radius:var(--r);padding:10px;box-shadow:var(--sh-2)}
figure svg{padding:0}
figcaption{margin-top:14px;color:var(--ink-3);font:var(--fs-13)/1.75 var(--font-sans)}
figcaption b,figcaption strong{color:var(--accent-ink);font-weight:600}

/* 推导：编号在圆点里，步与步之间留呼吸 */
ol.derive-list{counter-reset:d;list-style:none;margin:var(--s3) 0 0;padding:0}
ol.derive-list li{counter-increment:d;position:relative;padding:14px 0 14px 44px;font-size:var(--fs-17);line-height:1.95}
ol.derive-list li+li{border-top:1px solid var(--line-2)}
ol.derive-list li::before{content:counter(d);position:absolute;left:0;top:17px;width:24px;height:24px;
  display:flex;align-items:center;justify-content:center;border-radius:50%;
  background:rgba(163,90,9,.1);color:var(--c-derive);font:600 var(--fs-12)/1 var(--font-sans)}
.basis{display:inline-block;margin-left:8px;padding:2px 9px;border-radius:var(--r-pill);
  background:var(--accent-tint);color:var(--accent-ink);font:500 var(--fs-12)/1.5 var(--font-sans);
  border:1px solid var(--accent-line);white-space:nowrap}

/* 注意块 */
aside.callout{position:relative;border-left:2px solid var(--line);border-radius:0 var(--r) var(--r) 0;
  padding:var(--s4) var(--s5);margin:var(--s4) 0;background:var(--paper-3);font-size:var(--fs-15);line-height:1.95}
aside.callout .k{display:block;margin-bottom:4px;font:600 var(--fs-11)/1 var(--font-sans);
  letter-spacing:.1em;color:var(--ink-4);text-transform:uppercase}
aside.callout.k-pitfall{border-left-color:var(--danger);background:rgba(217,45,32,.045)}
aside.callout.k-pitfall .k{color:var(--danger)}
aside.callout.k-note{border-left-color:var(--info);background:rgba(11,114,133,.045)}
aside.callout.k-note .k{color:var(--info)}
aside.callout.k-intuition{border-left-color:var(--good);background:rgba(14,124,107,.045)}
aside.callout.k-intuition .k{color:var(--good)}
aside.callout.k-derive{border-left-color:var(--c-derive);background:rgba(163,90,9,.045)}
aside.callout.k-derive .k{color:var(--c-derive)}

/* 概念详解卡：悬停微抬、展开箭头旋转 */
details.concept{background:#fff;border:1px solid var(--line);border-radius:var(--r-lg);
  padding:0 var(--s4);margin:var(--s3) 0;box-shadow:var(--sh-1);
  transition:box-shadow var(--dur-2) var(--ease),border-color var(--dur) var(--ease),transform var(--dur-2) var(--ease)}
details.concept:hover{box-shadow:var(--sh-2);border-color:#dfe3e9;transform:translateY(-1px)}
details.concept>summary{cursor:pointer;list-style:none;display:flex;align-items:center;gap:10px;
  padding:var(--s4) 0;font:600 var(--fs-17)/1.5 var(--font-serif);color:var(--ink)}
details.concept>summary::-webkit-details-marker{display:none}
details.concept>summary::after{content:"";width:8px;height:8px;margin-left:auto;flex:none;
  border-right:1.6px solid var(--ink-4);border-bottom:1.6px solid var(--ink-4);
  transform:rotate(45deg);transition:transform var(--dur-2) var(--ease)}
details.concept[open]>summary::after{transform:rotate(-135deg)}
details.concept[open]{box-shadow:var(--sh-2);border-color:#dfe3e9}
details.concept .body{padding:var(--s4) 0 var(--s5);border-top:1px solid var(--line-2);margin-top:0}
details.concept .body>*:last-child{margin-bottom:0}
dl.meta{margin:0 0 var(--s3);display:grid;grid-template-columns:64px 1fr;gap:4px var(--s3);
  font-size:var(--fs-15);line-height:1.8}
dl.meta dt{color:var(--ink-4);font:500 var(--fs-13)/1.9 var(--font-sans)}
dl.meta dd{margin:0;color:var(--ink-2)}
.missing{color:var(--ink-4);font-size:var(--fs-14)}
.warm{background:rgba(163,90,9,.07);border-left:2px solid var(--c-derive);
  padding:var(--s3) var(--s4);border-radius:0 var(--r) var(--r) 0;margin:var(--s3) 0;
  font-size:var(--fs-15);line-height:1.85;color:var(--ink-2)}
table.derive{width:100%;border-collapse:separate;border-spacing:0;font-size:var(--fs-15);
  margin:var(--s2) 0 var(--s3);border:1px solid var(--line);border-radius:var(--r);overflow:hidden}
table.derive th,table.derive td{padding:9px var(--s3);text-align:left;vertical-align:top;
  border-bottom:1px solid var(--line-2);line-height:1.8}
table.derive thead th{background:var(--paper-3);color:var(--ink-3);font:600 var(--fs-12)/1.6 var(--font-sans);
  letter-spacing:.04em;border-bottom:1px solid var(--line)}
table.derive td:first-child,table.derive th:first-child{width:34px;color:var(--ink-4);
  font:500 var(--fs-13)/1.8 var(--font-sans);text-align:center}
table.derive tbody tr:last-child td{border-bottom:0}
table.derive tbody tr:hover{background:var(--paper-2)}
.ex-chip{display:inline-block;border:1px dashed var(--accent-line);border-radius:var(--r-pill);
  padding:1px 8px;margin:0 2px;font-size:.86em;color:var(--accent-ink);background:var(--accent-tint);
  white-space:nowrap}
.cpt{color:var(--ink);border-bottom:1px solid rgba(109,40,217,.35);cursor:help;
  transition:background var(--dur) var(--ease)}
.cpt:hover{background:rgba(109,40,217,.08);border-bottom-color:transparent}
.katex{font-size:1.03em}
.katex-display{margin:var(--s4) 0;overflow-x:auto;overflow-y:hidden;padding:2px 0;text-align:center}

/* 页脚与版权 */
footer.note{margin-top:var(--s7);padding-top:var(--s4);border-top:1px solid var(--line);
  color:var(--ink-4);font:var(--fs-13)/1.85 var(--font-sans)}
footer.note b{color:var(--ink-3)}
.copyright{max-width:1180px;margin:0 auto;padding:var(--s4) var(--s5) var(--s6);border-top:1px solid var(--line);
  color:var(--ink-4);font:var(--fs-12)/1.7 var(--font-sans);display:flex;gap:var(--s5);flex-wrap:wrap}
@media (prefers-reduced-motion:reduce){*{transition:none !important;animation:none !important;scroll-behavior:auto !important}}
@media (max-width:1000px){
  .grid{grid-template-columns:1fr;gap:var(--s5);padding:var(--s5) var(--s4) 72px}
  aside.side{position:static;max-height:none;border-right:0;border-bottom:1px solid var(--line);
    padding-right:0;padding-bottom:var(--s3)}
  main{max-width:none}
  header.top nav{display:none}
  h1{font-size:var(--fs-28)}
}
/* ── 打印 / 导出 PDF：单栏、去底色、保留左边框与层级 ── */
@media print{
  :root{--ink:#000;--ink-2:#111;--ink-3:#333;--line:#bbb;--line-2:#ddd}
  header.top,aside.side,#to-pdf,#search,.copyright{display:none !important}
  body{background:#fff;font-size:10.8pt;line-height:1.75}
  .grid{display:block;max-width:none;padding:0}
  main{max-width:none}
  a{color:#000}
  .mod,aside.callout,details.concept,table.derive,figure{background:#fff !important;box-shadow:none !important}
  details.concept,.mod,aside.callout,figure,table.derive{break-inside:avoid}
  details.concept{border-color:#999;padding:0 8px}
  details.concept>summary{padding:6px 0}
  h1{font-size:17pt}
  figcaption{color:#333}
}
"""


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def md(text: str) -> str:
    """教案 markdown 片段 → 行内 HTML（公式交给 KaTeX）。"""
    out = esc(text)
    out = re.sub(r"\[\[FIGURE:([^\]]+)\]\]", "", out)
    out = re.sub(r"\[\[DIAGRAM:([^\]]+)\]\]", r"<span class='ex-chip'>\1 过程图</span>", out)
    out = re.sub(r"\[\[EXPLAIN:[^\]|]+\|([^\]]+)\]\]", r"<span class='ex-chip'>\1</span>", out)
    out = re.sub(r"\[\[CONCEPT:[^\]|]+\|([^\]]+)\]\]", r"<span class='cpt'>\1</span>", out)
    out = re.sub(r"\[\[NOTE:[^\]]+\]\]", "<sup>注</sup>", out)
    out = re.sub(r"\$\$(.+?)\$\$", lambda m: '<span class="tex block">%s</span>' % esc(m.group(1)),
                 out, flags=re.S)
    out = re.sub(r"\$(.+?)\$", lambda m: '<span class="tex">%s</span>' % esc(m.group(1)), out)
    return out


def svg_mesh() -> str:
    return """<svg viewBox="0 0 420 220" role="img" aria-label="两个网孔的网孔电流方向示意">
  <rect x="40" y="40" width="340" height="140" fill="none" stroke="#333" stroke-width="1.6"/>
  <line x1="210" y1="40" x2="210" y2="180" stroke="#333" stroke-width="1.6"/>
  <rect x="120" y="95" width="60" height="26" fill="#fff" stroke="#333"/>
  <rect x="250" y="95" width="60" height="26" fill="#fff" stroke="#333"/>
  <text x="150" y="113" text-anchor="middle" font-size="12">R₁</text>
  <text x="280" y="113" text-anchor="middle" font-size="12">R₂</text>
  <path d="M95 130 A 28 28 0 1 1 95 90" fill="none" stroke="#b45309" stroke-width="2"/>
  <polygon points="95,86 101,96 89,96" fill="#b45309"/>
  <text x="86" y="150" font-size="12" fill="#b45309">i_M1</text>
  <path d="M325 90 A 28 28 0 1 1 325 130" fill="none" stroke="#b45309" stroke-width="2"/>
  <polygon points="325,134 331,124 319,124" fill="#b45309"/>
  <text x="298" y="150" font-size="12" fill="#b45309">i_M2</text>
  <text x="210" y="205" text-anchor="middle" font-size="11" fill="#6b7280">
    两个网孔各设一个假想的网孔电流（沿网孔边界流动）</text>
</svg>"""


def crumb(book: str, chapter: str, unit_title: str, anchor: str) -> str:
    parts = [p for p in (book, chapter, ("%s %s" % (anchor, unit_title)).strip()) if p]
    return " / ".join(parts)


def build(parse_dir: Path, lesson_path: Path, unit: str, book_hint: str = "") -> tuple[str, dict]:
    lesson = json.loads(lesson_path.read_text(encoding="utf-8"))
    chapters = json.loads((parse_dir / "chapters.json").read_text(encoding="utf-8"))
    concepts = json.loads((parse_dir / "concepts.json").read_text(encoding="utf-8"))["concepts"]
    figs = json.loads((parse_dir / "figures.json").read_text(encoding="utf-8"))["images"]
    cap = {str(f.get("id")): (f.get("caption") or "") for f in figs}
    # 书名：**以 toc.json 的 book_title 为准**（chapters.json 的 book.title 是目录名/path 片段，
    # 直接拿来会印在面包屑上变成 "circuit-analysis-1"，实测踩过）
    book = str((chapters.get("book") or {}).get("title") or "")
    try:
        _bt = str(json.loads((parse_dir / "toc.json").read_text(encoding="utf-8")).get("book_title") or "")
        if _bt:
            book = _bt
    except (OSError, ValueError):
        pass
    # 书名兜底：产物里没有真书名（books.json 未登记这本、toc.book_title 为空、chapters 里是目录名），
    # 所以允许命令行传 `--book`（渲染站点时也是这么传的）。
    if (not book or book == parse_dir.name) and book_hint:
        book = book_hint

    ex_by_concept: dict[str, dict] = {}
    for item in lesson.get("explainables") or []:
        cid = str(item.get("concept_id") or "").upper()
        ex_by_concept.setdefault(cid, item)

    unit_concepts = [c for c in concepts
                     if any(o.get("unit_id") == unit for o in (c.get("occurrences") or []))]
    unit_concepts.sort(key=lambda c: (-len(c.get("occurrences") or []), c["name"]))
    groups: dict[str, list] = {}
    for c in unit_concepts:
        groups.setdefault(str(c.get("type")), []).append(c)

    # ── 左侧：章节目录树 ──
    chapter_title = {str(c.get("id")): c for c in (chapters.get("chapters") or [])}
    _c = chapter_title.get(str(lesson.get("chapter") or "")) or {}
    _chapter_label = (("第 %s 章 " % _c.get("anchor")) if str(_c.get("anchor") or "").isdigit() else "") \
        + str(_c.get("title") or lesson.get("chapter") or "")
    tree = ["<ol class='tree'>"]
    _open_chapters: list[str] = []
    cur_chapter = None
    for u in chapters.get("lesson_units", []):
        ch = u.get("chapter") or ""
        if ch != cur_chapter:
            cur_chapter = ch
            if len(tree) > 1:
                tree.append("</ol>")
            tree.append("<ol class='tree'>")
            # 章标题用**真标题**（`chapters[].title` + `anchor`），别把 `CH02` 这种 id 印给读者
            c = chapter_title.get(ch) or {}
            ctitle = c.get("title") or u.get("title") or ch
            canchor = str(c.get("anchor") or "")
            label = ("第 %s 章 " % canchor if canchor.isdigit() else "") + ctitle
            if cur_chapter == lesson.get("chapter"):
                tree.append("<li class='chapter'>%s</li>" % esc(label))
            else:
                # 非当前章：折叠起来（参考图里左栏只展开当前章 —— 全铺开就是一面墙）
                tree.append("<li class='chapter'><details><summary>%s</summary><ol class='tree'>"
                            % esc(label))
                _open_chapters.append(cur_chapter)
        if _open_chapters:
            # 新章开始 → 关掉上一个折叠章的内层列表
            tree.append("</ol></details></li>")
            _open_chapters.clear()
        cls = " class='on'" if u["unit_id"] == unit else ""
        tree.append("<li><a%s href='#unit' data-unit='%s'>%s %s</a></li>"
                    % (cls, esc(u["unit_id"]), esc(u.get("anchor") or ""), esc(u.get("title") or "")))
    tree.append("</ol>")

    # ── 正文（含出处行）──
    cite = "—— 引自《%s》§%s %s" % (book, lesson.get("anchor") or "", lesson.get("title") or "")
    body_html = []
    paras = [p.strip() for p in str(lesson.get("body_markdown") or "").split("\n\n") if p.strip()]
    pick = [p for p in paras if re.search(r"\[\[(FIGURE|CONCEPT|EXPLAIN|DIAGRAM):", p)][:6] or paras[:4]
    # 正文只用一个 section（每段都挂 "正文" 标签是纯噪声 —— 逐像素看出来的）
    body_html.append("<section class='mod mod-text' id='text'><span class='tag'>正文</span>")
    body_html.append("<p class='cite'>%s</p>" % esc(cite))
    for p in pick:
        body_html.append("<p>%s</p>" % md(p))
    body_html.append("</section>")

    # ── 配图（手绘 SVG + 教材原图）──
    fig_ids = [str(f.get("id")) for f in (lesson.get("figures") or []) if f.get("id")]
    fig_html = ["<section class='mod mod-fig' id='figs'><span class='tag'>配图</span>",
                "<figure>%s<figcaption>图 %s-1 网孔电流方向（本页手绘 SVG，可交互化见页脚）</figcaption></figure>"
                % (svg_mesh(), esc(lesson.get("anchor") or "2.1"))]
    if fig_ids:
        fid = fig_ids[0]
        c = cap.get(fid) or ""
        fig_html.append("<figure><img src='assets/figures/%s.png' alt='%s'>"
                        "<figcaption>%s%s</figcaption></figure>"
                        % (esc(fid), esc(fid), esc(c) if c else "图 %s" % esc(fid),
                           "" if c else " <span class='missing'>（教材未给图注）</span>"))
    fig_html.append("</section>")
    body_html.append("".join(fig_html))

    # ── 核心推导过程（取解释最全的那条 explainable）──
    best = None
    for item in lesson.get("explainables") or []:
        if (item.get("walkthrough") or []) and (best is None or
                                                len(item["walkthrough"]) > len(best["walkthrough"])):
            best = item
    if best:
        rows = ["<section class='mod mod-derive' id='derive'><span class='tag'>核心推导过程</span>",
                "<p><b>%s</b></p><ol class='derive-list'>" % esc(best.get("name") or "")]
        for st in best["walkthrough"]:
            rows.append("<li>%s%s</li>" % (md(st.get("claim")),
                                           ("<span class='basis'>依据：%s</span>" % md(st.get("basis")))
                                           if st.get("basis") else ""))
        rows.append("</ol></section>")
        body_html.append("".join(rows))

    # ── 注意块（批注）──
    notes = lesson.get("annotations") or []
    if notes:
        body_html.append("<section class='mod mod-text'><span class='tag'>注意</span>")
        for a in notes:
            kind = str(a.get("kind") or "note")
            body_html.append("<aside class='callout k-%s'><span class='k'>%s%s</span>%s</aside>"
                             % (esc(kind), esc(NOTE_CN.get(kind, kind)),
                                ("· " + esc(a.get("title"))) if a.get("title") else "",
                                md(a.get("markdown"))))
        body_html.append("</section>")

    # ── 例题 ──
    ex = (lesson.get("examples") or [None])[0]
    if ex:
        p = ["<section class='mod mod-derive' id='ex'><span class='tag'>例题</span><article>",
             "<h3>%s</h3><p>%s</p>" % (esc(ex.get("label")), md(ex.get("statement")))]
        if ex.get("thought"):
            p.append("<p><b>思路：</b>%s</p>" % md(ex["thought"]))
        if ex.get("warmup"):
            p.append("<p class='warm'><b>先看一个简单情形：</b>%s</p>" % md(ex["warmup"]))
        if ex.get("steps"):
            p.append("<table class='derive'><thead><tr><th>#</th><th>一步一步解</th><th>依据</th></tr></thead><tbody>")
            for st in ex["steps"]:
                p.append("<tr><td>%s</td><td>%s</td><td>%s</td></tr>"
                         % (esc(st.get("no")), md(st.get("markdown")),
                            ("<span class='basis'>%s</span>" % md(st.get("basis"))) if st.get("basis") else ""))
            p.append("</tbody></table>")
        if ex.get("conclusion"):
            p.append("<p><b>结论：</b>%s</p>" % md(ex["conclusion"]))
        p.append("</article></section>")
        body_html.append("".join(p))

    # ── 概念详解卡（解释/配图/推导 三件可独立出现）──
    cards = ["<section class='mod mod-concept' id='concepts'><span class='tag'>概念详解</span>"]
    fig_by_cpt: dict[str, str] = {}
    for f in (lesson.get("figures") or []):
        cid = str(f.get("concept_id") or "").upper()
        if cid:
            fig_by_cpt.setdefault(cid, str(f.get("id")))
    req_by_cpt = {str(r.get("concept_id") or "").upper(): r
                  for r in (lesson.get("figure_requests") or []) if isinstance(r, dict)}
    for c in unit_concepts:
        cid = c["id"]
        it = ex_by_concept.get(cid) or {}
        inner = []
        dd = []
        if c.get("formal"):
            dd.append("<dt>原文</dt><dd>%s</dd>" % md(c["formal"]))
        occ = [o.get("title") or o.get("unit_id") for o in (c.get("occurrences") or [])]
        if occ:
            dd.append("<dt>出现于</dt><dd>%s</dd>" % esc("、".join([x for x in occ if x][:4])))
        if dd:
            inner.append("<dl class='meta'>%s</dl>" % "".join(dd))
        if it.get("what") or it.get("why") or it.get("markdown") or it.get("warmup"):
            if it.get("what"):
                inner.append("<p><b>它是什么：</b>%s</p>" % md(it["what"]))
            if it.get("why"):
                inner.append("<p><b>为什么在这里用：</b>%s</p>" % md(it["why"]))
            if it.get("warmup"):
                inner.append("<p class='warm'><b>先看一个简单情形：</b>%s</p>" % md(it["warmup"]))
            if it.get("markdown"):
                inner.append("<div>%s</div>" % md(it["markdown"]))
        else:
            inner.append("<p class='missing'>这一条暂时没有解释（抽取阶段只登记了名字与原文表述）。</p>")
        fid = fig_by_cpt.get(cid)
        if fid:
            inner.append("<figure><img src='assets/figures/%s.png' alt='%s'>"
                         "<figcaption>%s 的配图</figcaption></figure>" % (esc(fid), esc(fid), esc(c["name"])))
        elif cid in req_by_cpt:
            r = req_by_cpt[cid]
            inner.append("<figure><figcaption>待补图（已提图请求）：%s<br>"
                         "<span class='missing'>原文摘录：%s</span></figcaption></figure>"
                         % (esc(r.get("what")), esc(r.get("quote"))))
        else:
            inner.append("<p class='missing'>这一条暂时没有配图。</p>")
        steps = it.get("walkthrough") or []
        if steps:
            inner.append("<table class='derive'><thead><tr><th>#</th><th>这一步在说什么</th>"
                         "<th>依据</th></tr></thead><tbody>")
            for st in steps:
                inner.append("<tr><td>%s</td><td>%s</td><td>%s</td></tr>"
                             % (esc(st.get("step")), md(st.get("claim")),
                                ("<span class='basis'>%s</span>" % md(st.get("basis"))) if st.get("basis") else ""))
            inner.append("</tbody></table>")
        else:
            inner.append("<p class='missing'>这一条暂时没有推导步骤。</p>")
        cards.append("<details class='concept' id='c-%s'><summary>"
                     "<span class='tag' style='background:rgba(109,40,217,.14);color:var(--c-concept)'>%s</span>"
                     " %s<span class='badge missing'>×%d 节</span></summary>"
                     "<div class='body'>%s</div></details>"
                     % (esc(cid), esc(KIND_CN.get(c.get("type"), c.get("type"))), esc(c["name"]),
                        len(c.get("occurrences") or []), "".join(inner)))
    cards.append("</section>")
    body_html.append("".join(cards))

    # ── 左侧：概念解析分组 ──
    cnav = []
    for t in GROUP_ORDER + [k for k in groups if k not in GROUP_ORDER]:
        items = groups.get(t)
        if not items:
            continue
        cnav.append("<details class='cgroup' open><summary>%s（%d）</summary>"
                    % (esc(KIND_CN.get(t, t)), len(items)))
        for c in items:
            cnav.append("<a class='item' href='#c-%s' data-cpt='%s' data-search='%s %s'>%s"
                        "<span class='badge'>×%d</span></a>"
                        % (esc(c["id"]), esc(c["id"]), esc(c["name"]), esc(KIND_CN.get(c.get("type"), "")),
                           esc(c["name"]), len(c.get("occurrences") or [])))
        cnav.append("</details>")

    page = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__PAGETITLE__</title>
<link rel="stylesheet" href="assets/katex.min.css">
<script src="assets/katex.min.js"></script>
<style>__CSS__</style></head><body>
<header class="top">
  <div class="brand">AIforStudy 解析中心<small>__BOOK__</small></div>
  <nav aria-label="页内导航">
    <a href="#unit" class="on">本节</a><a href="#figs">配图</a><a href="#derive">推导</a>
    <a href="#concepts">概念详解</a><a href="#ex">例题</a>
  </nav>
  <div class="spacer"></div>
  <input id="search" type="search" placeholder="搜索概念、公式或章节…" aria-label="搜索概念">
  <button id="to-pdf" type="button">导出为 PDF</button>
</header>
<div class="grid">
  <aside class="side">
    <h2>章节目录</h2>
    __TREE__
    <h2>概念解析</h2>
    __CNAV__
  </aside>
  <main id="unit">
    <nav class="crumb" aria-label="面包屑">__CRUMB__</nav>
    <h1><span class="eyebrow">§ __ANCHOR__</span>__TITLE__</h1>
    <p class="subtitle">__ONE_LINE__</p>
    __BODY__
    <footer class="note">
      <b>公式</b>：KaTeX（本文件已内联 JS/CSS/字体，离线可用）。<br>
      <b>展开</b>：<code>&lt;details&gt;</code> + <code>scrollIntoView({behavior:'smooth'})</code>；
      不用 <code>max-height</code> 过渡。<br>
      <b>导出 PDF</b>：点右上角按钮（打印前会自动展开所有折叠项；样式见 <code>@media print</code>）。<br>
      <b>换肤</b>：只改 <code>:root</code> 里的 CSS 变量（配色/圆角/字体栈）。<br>
      <b>中文字体</b>：系统字体栈（仓库不含中文字体）。想换霞鹜文楷：把
      <code>@font-face{font-family:'LXGW WenKai';src:url('LXGWWenKai-Regular.woff2')}</code>
      放进本文件，并把 <code>--serif</code> 首位改成 <code>'LXGW WenKai'</code>。<br>
      <b>手绘 SVG 交互化</b>：给 <code>&lt;path&gt;</code> 的半径/角度绑一个
      <code>input[type=range]</code>，在 <code>input</code> 事件里改 <code>d</code> 属性即可。
    </footer>
  </main>
</div>
<div class="copyright">
  <span>© AIforStudy · 教材解析演示</span><span>内容来自本仓库解析产物，仅供学习</span>
  <span>数学公式由 KaTeX 渲染</span>
</div>
<script>
function typesetAll(root){
  if(!window.katex) return;
  (root||document).querySelectorAll(".tex").forEach(function(el){
    if(el.dataset.done) return;
    try{ katex.render(el.textContent, el,
      {displayMode: el.classList.contains("block"), throwOnError:false}); el.dataset.done="1"; }
    catch(e){}
  });
}
document.addEventListener("DOMContentLoaded", function(){
  typesetAll();
  /* 展开：平滑定位 + 渲染新出现的公式 */
  document.querySelectorAll("details").forEach(function(d){
    d.addEventListener("toggle", function(){
      if(!d.open) return;
      typesetAll(d);
      d.scrollIntoView({behavior:"smooth", block:"nearest"});
    });
  });
  /* 左侧高亮：正文里出现的概念卡 / 例题 */
  var items = Array.prototype.slice.call(document.querySelectorAll(".cgroup .item"));
  var targets = items.map(function(a){ return document.querySelector(a.getAttribute("href")); });
  if(window.IntersectionObserver){
    var obs = new IntersectionObserver(function(es){
      es.forEach(function(en){
        if(!en.isIntersecting) return;
        items.forEach(function(a,i){ a.classList.toggle("on", targets[i]===en.target); });
      });
    }, {rootMargin:"-40% 0px -55% 0px"});
    targets.forEach(function(t){ if(t) obs.observe(t); });
  }
  /* 页内导航高亮 */
  var navlinks = Array.prototype.slice.call(document.querySelectorAll("header.top nav a"));
  var secs = navlinks.map(function(a){ return document.querySelector(a.getAttribute("href")); });
  if(window.IntersectionObserver){
    var o2 = new IntersectionObserver(function(es){
      es.forEach(function(en){
        if(!en.isIntersecting) return;
        navlinks.forEach(function(a,i){ a.classList.toggle("on", secs[i]===en.target); });
      });
    }, {rootMargin:"-30% 0px -60% 0px"});
    secs.forEach(function(s){ if(s) o2.observe(s); });
  }
  /* 搜索：过滤左侧概念与例题（本单元） */
  var box = document.getElementById("search");
  if(box) box.addEventListener("input", function(){
    var q = box.value.trim().toLowerCase();
    document.querySelectorAll(".cgroup").forEach(function(g){
      var any = false;
      g.querySelectorAll(".item").forEach(function(a){
        var hit = !q || (a.dataset.search||"").toLowerCase().indexOf(q) >= 0;
        a.style.display = hit ? "" : "none";
        if(hit) any = true;
      });
      g.style.display = any ? "" : "none";
      if(q && any) g.open = true;
    });
  });
  /* 导出为 PDF：先展开所有 details，再打印 */
  var btn = document.getElementById("to-pdf");
  if(btn) btn.addEventListener("click", function(){
    document.querySelectorAll("details").forEach(function(d){ d.open = true; });
    typesetAll();
    setTimeout(function(){ window.print(); }, 60);
  });
});
</script>
</body></html>"""
    _subs = {
        "pagetitle": "%s %s · 学习模板" % (lesson.get("anchor") or "", lesson.get("title") or ""),
        "css": CSS, "book": esc(book), "tree": "".join(tree), "cnav": "".join(cnav),
        # 面包屑也用章的真标题（原来印的是 `CH02` 这种 id，逐像素审出来的）
        "crumb": esc(crumb(book, _chapter_label, lesson.get("title") or "",
                           lesson.get("anchor") or "")),
        "anchor": esc(lesson.get("anchor") or ""), "title": esc(lesson.get("title") or ""),
        "one_line": esc(lesson.get("one_line") or ""), "body": "".join(body_html),
    }
    for _k, _v in _subs.items():
        page = page.replace("__%s__" % _k.upper(), str(_v))

    stats = {"body": len(pick), "concepts": len(unit_concepts), "groups": len(groups),
             "notes": len(notes), "derive": len((best or {}).get("walkthrough") or []),
             "examples": 1 if ex else 0, "figures": len(fig_ids) + 1}
    return page, stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成学习模板（单文件）")
    ap.add_argument("--parse", required=True)
    ap.add_argument("--lesson", required=True)
    ap.add_argument("--site", required=True)
    ap.add_argument("--unit", default="CH02-S01")
    ap.add_argument("--book", default="", help="书名（产物里没有真书名时用它）")
    args = ap.parse_args(argv)
    parse_dir, site = ROOT / args.parse, ROOT / args.site
    text, stats = build(parse_dir, ROOT / args.lesson, args.unit, args.book)
    raw = site / ("study-template-%s.html" % args.unit)
    raw.write_text(text, encoding="utf-8")
    print("  内容：正文 %d 段 ｜ 概念 %d 条（分 %d 组）｜ 推导 %d 步 ｜ 注意 %d 条 ｜ 例题 %d ｜ 图 %d"
          % (stats["body"], stats["concepts"], stats["groups"], stats["derive"],
             stats["notes"], stats["examples"], stats["figures"]))
    pack(raw)
    return 0


if __name__ == "__main__":
    sys.exit(main())
