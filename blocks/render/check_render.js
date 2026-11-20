/* 在没有浏览器的情况下验证"网页真的能渲染出东西"。
 *
 * 做法：用 node 加载与浏览器**同一份** vendor 库和 render-core.js，
 * 把真实数据里的讲解/原文过一遍渲染管线，检查产物里该有的东西在不在、
 * 不该有的东西（没展开的占位符、找不到的引用）有没有。
 *
 * 用法：node check_render.js <网页目录>
 */
const fs = require("fs");
const path = require("path");

const here = __dirname;                       // blocks/render
const out = path.resolve(process.argv[2] || ".");

const markedMod = require(path.join(here, "vendor/marked.min.js"));
const marked = markedMod.marked || markedMod;
const katex = require(path.join(here, "vendor/katex.min.js"));
const core = require(path.join(here, "assets/render-core.js"));

global.window = {};
require(path.join(out, "data/data.js"));
const D = global.window.__DATA__;

const renderer = core.createRenderer({
  marked: marked, katex: katex, concepts: D.concepts, figures: D.figures,
  diagrams: D.diagrams || {},          // 过程图也要给（不给的话会误报"引用找不到"）
});

const report = { units: {}, problems: [], totals: { katex: 0, concepts: 0, figures: 0 } };

function check(label, text, expectRefs = true) {
  const html = renderer.renderMarkdown(text || "");
  const found = {
    katex: (html.match(/class="katex/g) || []).length,
    conceptLinks: (html.match(/class="concept"/g) || []).length,
    // 生成的图 class 是 "figure generated"，所以按前缀数，别要求精确等于 figure
    figureTags: (html.match(/<figure class="figure/g) || []).length,
    missingRefs: (html.match(/class=["']missing-ref["']/g) || []).length,
    mathErrors: (html.match(/class=["']math-error["']/g) || []).length,
    // P-002/P-006：边注小圆点、公式详解胶囊、例题依据胶囊（它们都是"可指向"的入口）
    noteMarks: (html.match(/class=["']mk mk-/g) || []).length,
    explainChips: (html.match(/class=["']fchip["']/g) || []).length,
    basisChips: (html.match(/class=["']basis["']/g) || []).length,
    // ⚠️ 必须数**全部六种**占位符（2026-09-26 修）：原来只数 CONCEPT|FIGURE，
    //    于是页面上漏出 `[[NOTE:…]]` / `[[EXPLAIN:…]]` / `[[DIAGRAM:…]]` / `[[MODEL:…]]` 时
    //    **自检说没问题** —— 而那正是读者看到的方括号乱码。
    //    kind 集合的唯一来源是 `lib/refs.py#PLACEHOLDER_KINDS`，
    //    由 `tests/test_render.py::test_check_render_counts_every_placeholder_kind` 盯着两边一致。
    unexpanded: (html.match(/\[\[(CONCEPT|EXPLAIN|NOTE|DIAGRAM|FIGURE|MODEL|EXAMPLE)\s*:/g) || []).length,
    slotLeak: (html.match(/\u0000/g) || []).length,
    exampleCards: (html.match(/class=["']example-inline["']/g) || []).length,
    diagramTags: (html.match(/<figure class="diagram/g) || []).length,
  };
  if (found.unexpanded) report.problems.push(`${label}: 有 ${found.unexpanded} 个占位符没展开`);
  if (found.slotLeak) report.problems.push(`${label}: 内部占位符 ${found.slotLeak} 个泄漏到页面上`);
  if (found.missingRefs) report.problems.push(`${label}: 有 ${found.missingRefs} 个引用找不到（概念表/插图里没有）`);
  const generated = (html.match(/<figure class="figure generated"/g) || []).length;
  found.generatedFigures = generated;
  if (found.mathErrors) report.problems.push(`${label}: 有 ${found.mathErrors} 处公式渲染失败`);
  if (expectRefs && text && text.indexOf("[[CONCEPT") >= 0 && !found.conceptLinks) {
    report.problems.push(`${label}: 原文里有概念占位符，但一个链接都没生成`);
  }
  return { html: html, found: found };
}

/* ── 合成样例（2026-10-01）────────────────────────────────────────────
   把**七种标记**放进同一段文字过一遍渲染管线，逐个断言"真的变成了页面元素"。
   为什么需要它：真实教案里某种标记可能一条都没有（例如 [[EXAMPLE:…]] 要等重跑才有锚点），
   那条通道就等于**没人看着**；这条样例不依赖真实数据，专门挡"加了类型却渲染不出来"的漂移。
   数据绑定的三种（CONCEPT/FIGURE/DIAGRAM）用 D 里**真实存在**的 id，其余用当场造的上下文。 */
(function syntheticSample() {
  var cid = Object.keys(D.concepts || {})[0];
  var fid = (D.figures && Object.keys(D.figures)[0]) || "";
  var did = (D.diagrams && Object.keys(D.diagrams)[0]) || "";
  var ctx = {
    notes: { "N-9": { id: "N-9", kind: "note", title: "样例批注", markdown: "样例内容" } },
    explain: { "F-9": { id: "F-9", kind: "formula", name: "样例公式", what: "它是什么" } },
    examples: [{ id: "E-9", label: "例9-9", statement: "题干一行", thought: "思路一行",
                 warmup: "先看一个简单情形：两个未知数",
                 steps: [{ no: 1, markdown: "第一步", basis: "依据：定义" }],
                 conclusion: "结论一行" }],
  };
  renderer.beginUnit(ctx);
  // ⚠️ **数据绑定的那几种，没有对应数据就不检**（fixture 站点可能一个概念/图都没有）——
  //    否则样例会在小站点上误报"概念链接没渲染出来"（实测把两条单测弄红了）。
  var parts = [], wants = [];
  if (cid) { parts.push("[[CONCEPT:" + cid + "|概念]]"); wants.push(["conceptLinks", "概念链接"]); }
  if (fid) { parts.push("[[FIGURE:" + fid + "]]"); wants.push(["figureTags", "插图"]); }
  parts.push("[[NOTE:N-9]]"); wants.push(["noteMarks", "批注圆点"]);
  parts.push("[[EXPLAIN:F-9|详解]]"); wants.push(["explainChips", "详解胶囊"]);
  if (did) { parts.push("[[DIAGRAM:" + did + "]]"); wants.push(["diagramTags", "过程图"]); }
  parts.push("[[EXAMPLE:E-9]]"); wants.push(["exampleCards", "例题卡片"]);
  var got = check("自检样例（七种标记）", "一段话 " + parts.join(" ") + " 结束。").found;
  wants.forEach(function (pair) {
    if (!got[pair[0]]) {
      report.problems.push("自检样例: " + pair[1] + "（" + pair[0] + "）没渲染出来");
    }
  });
})();

Object.keys(D.units).forEach(function (id) {
  const unit = D.units[id];
  /* P-002/P-006：正文里的 [[NOTE:…]] / [[EXPLAIN:…]] 要**先有上下文**才能展开成
     "小圆点 + 就地批注 / 详解胶囊"。渲染器不知道本节有哪些批注与详解的话，
     会一律当成"引用找不到"（实测：升级后自检立刻报了 4 条假问题）。 */
  const noteMap = {};
  (unit.annotations || []).forEach(function (a) { if (a && a.id) noteMap[a.id] = a; });
  const explainMap = {};
  (unit.explainables || []).forEach(function (x) { if (x && x.id) explainMap[x.id] = x; });
  // 注意：`examples` 也必须喂（2026-10-01）—— 不喂的话 [[EXAMPLE:…]] 只会渲染成「例题缺」，
  // 自检就看不见「例题能不能就地展开」，又是一处生产者→消费者缺口。
  renderer.beginUnit({ notes: noteMap, explain: explainMap, examples: unit.examples || [] });
  const explain = check(id + " 讲解", unit.body_markdown || unit.explanation_markdown);
  // 例题（一步步解 + 每一步的依据）也是渲染核心产出的，要一起过一遍
  const exHtml = (unit.examples || []).length ? renderer.examplesHtml(unit.examples) : "";
  explain.found.basisChips = (exHtml.match(/class=["']basis["']/g) || []).length;
  explain.found.stepCount = (exHtml.match(/<li>/g) || []).length;
  const cleaned = check(id + " 原文(清洗)", unit.original.cleaned, false);
  const raw = unit.original.raw ? check(id + " 原文(OCR)", unit.original.raw, false) : null;
  if ((unit.annotations || []).length && !explain.found.noteMarks) {
    report.problems.push(`${id}: 有 ${(unit.annotations || []).length} 条批注，但正文里一个编号圆点都没生成`);
  }
  if ((unit.explainables || []).length && !explain.found.explainChips) {
    report.problems.push(`${id}: 有 ${(unit.explainables || []).length} 条公式/定理详解，但正文里一个详解胶囊都没生成`);
  }
  if ((unit.examples || []).length) {
    const needBasis = (unit.examples || []).reduce(function (n, ex) {
      return n + (ex.steps || []).filter(function (s) { return s && s.basis; }).length;
    }, 0);
    if (needBasis && !explain.found.basisChips) {
      report.problems.push(`${id}: 例题里有 ${needBasis} 处依据，但没生成「这一步的依据」按钮`);
    }
  }
  report.units[id] = {
    title: unit.title,
    explanation: explain.found,
    cleaned: cleaned.found,
    raw: raw ? raw.found : null,
  };
  // "原文(OCR)"那一栏也是页面的一部分，公式要一起数 —— 不数的话，
  // 没有清洗产物的版本（MinerU 版）会被少算一半公式，看着像"公式变少了"（实测踩过）。
  report.totals.katex += explain.found.katex + cleaned.found.katex + (raw ? raw.found.katex : 0);
  report.totals.concepts += explain.found.conceptLinks;
  report.totals.figures += explain.found.figureTags + cleaned.found.figureTags
      + (raw ? raw.found.figureTags : 0);
});

// 概念抽屉：每个概念都要能渲染出解释
Object.keys(D.concepts).forEach(function (id) {
  const c = D.concepts[id];
  ["brief", "explanation", "formal"].forEach(function (field) {
    if (!c[field]) return;
    const r = check(`概念 ${id}.${field}`, c[field], false);
    if (r.found.unexpanded || r.found.slotLeak) {
      report.problems.push(`概念 ${id}.${field} 渲染有问题`);
    }
  });
});

// 图要么有文件（扫描裁图），要么内联了 SVG（生成的矢量图）
Object.keys(D.figures).forEach(function (id) {
  const figure = D.figures[id];
  if (figure.svg) {
    // 生成的图：必须是内联 SVG（<img> 里的 currentColor 会变黑，深色模式看不见）
    if (!/<svg[\s>]/.test(figure.svg)) {
      report.problems.push(`插图 ${id} 的 svg 字段里没有 <svg> 标签`);
    }
    if (!/currentColor/.test(figure.svg)) {
      report.problems.push(`插图 ${id} 的颜色写死了，深色模式下会看不见`);
    }
    return;
  }
  if (!figure.file) { report.problems.push(`插图 ${id} 既没有文件也没有内联 SVG`); return; }
  const full = path.join(out, figure.file);
  if (!fs.existsSync(full)) report.problems.push(`插图 ${id} 的文件不存在：${figure.file}`);
});

// index.html 引用的本地资源是否都存在
const html = fs.readFileSync(path.join(out, "index.html"), "utf8");
(html.match(/(?:src|href)="([^"]+)"/g) || []).forEach(function (attr) {
  const rel = attr.replace(/^(?:src|href)="/, "").replace(/"$/, "");
  if (/^https?:/.test(rel)) { report.problems.push(`index.html 引用了外网资源：${rel}`); return; }
  if (!fs.existsSync(path.join(out, rel))) report.problems.push(`index.html 引用的文件不存在：${rel}`);
});

console.log(JSON.stringify(report, null, 2));
process.exit(report.problems.length ? 1 : 0);
