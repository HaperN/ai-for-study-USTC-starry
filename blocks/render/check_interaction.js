/* 用 jsdom 真的点一遍页面 —— 这一层以前只能靠人眼，结果漏掉了
 * "分组标题看着能点、点了没反应"（用户实测反馈的）。
 *
 * 用法：
 *   NODE_PATH=<装了 jsdom 的目录>/node_modules node check_interaction.js <网页目录>
 *
 * 没有 jsdom 时以退出码 3 退出（表示"跳过"，不是失败）。
 * 装法：在工作区根 `npm install`（依赖声明见根 package.json）
 *
 * ⚠️ 一个坑：**jsdom 读 data.js 时按 cp1252 解码**，所以从 DOM 里读出来的中文
 * 是乱码（"教材原文表述" → "æ•™æ�..."）。这是 jsdom 的行为，不是页面的毛病 ——
 * 真浏览器按文档的 <meta charset> 继承编码，中文是正常的。
 * 所以这里的断言**不许用中文字面量比较**，要么比结构（h3 个数、blockquote 有没有），
 * 要么拿同一份数据里的字符串互相比（两边一样乱，比得出结果）。
 */
const fs = require("fs");
const path = require("path");

const out = path.resolve(process.argv[2] || ".");
let JSDOM;
try {
  ({ JSDOM } = require("jsdom"));
} catch (error) {
  console.error("没有 jsdom，跳过交互检查。装法见文件头。");
  process.exit(3);
}

/* 页面自己的 console 输出一律走 **stderr**：MathBox 报错时会打印整份着色器源码，
   混进 stdout 会把最后那段 JSON 冲掉 —— 自检结果必须是 stdout 里唯一的东西，
   否则调用方（pytest / 流水线）会因为"解析不了"而把一次通过当成失败。 */
function pageConsole() {
  const { VirtualConsole } = require("jsdom");
  const vc = new VirtualConsole();
  ["error", "warn", "info", "log", "debug"].forEach(function (level) {
    vc.on(level, function () {
      process.stderr.write("[页面:" + level + "] " +
        Array.prototype.map.call(arguments, String).join(" ").slice(0, 2000) + "\n");
    });
  });
  vc.on("jsdomError", function (error) {
    process.stderr.write("[页面:jsdomError] " + ((error && error.message) || error) + "\n");
  });
  return vc;
}

const problems = [];
function check(condition, message) {
  if (!condition) problems.push(message);
}

(async function main() {
  const dom = await JSDOM.fromFile(path.join(out, "index.html"), {
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
    virtualConsole: pageConsole(),
  });
  const { window } = dom;
  // jsdom 不实现滚动，会抛一堆 "Not implemented"，这里静音掉
  window.scrollTo = function () {};
  window.HTMLElement.prototype.scrollIntoView = function () {};

  await new Promise(function (resolve) {
    if (window.document.readyState === "complete") return resolve();
    window.addEventListener("load", resolve);
  });
  // 脚本是同步执行的，但给 KaTeX/marked 一点时间
  await new Promise(function (resolve) { setTimeout(resolve, 300); });

  const doc = window.document;
  const data = window.__DATA__;
  check(!!data, "window.__DATA__ 没有加载出来");

  // ① 目录：每一节都该有链接；分组标题也该能跳到下面第一节
  const unitLinks = Array.from(doc.querySelectorAll("a.toc-item"));
  check(unitLinks.length === data.scope.length,
    `目录里的可点节数 ${unitLinks.length} != 范围内节数 ${data.scope.length}`);
  // 分组标题（章/节）**只有在该节点没有自己的讲解时**才出现在目录里；
  // 整章都做完之后所有节点都是教案单元，就没有分组节点了 —— 所以这里不能假设"必须有"。
  const groupLinks = Array.from(doc.querySelectorAll("a.toc-group"));
  const groupNodes = (function count(nodes) {
    return (nodes || []).reduce(function (sum, node) {
      return sum + (node.unit ? 0 : 1) + count(node.children);
    }, 0);
  })(data.tree);
  check(groupLinks.length === groupNodes,
    `目录里不可点的分组节点有 ${groupNodes} 个，但只有 ${groupLinks.length} 个做成了可点链接`);
  groupLinks.forEach(function (link) {
    check(!!link.getAttribute("data-group"), "分组标题缺少 data-group，无法高亮");
  });

  // ② 点第一节 → 正文该是那一节
  const first = unitLinks[0];
  const firstId = first.getAttribute("data-unit");
  first.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true }));
  await new Promise(function (resolve) { setTimeout(resolve, 60); });
  check(window.location.hash === "#/" + firstId,
    `点目录第一项后 hash 是 ${window.location.hash}，期望 #/${firstId}`);
  check(doc.getElementById("content").textContent.indexOf(data.units[firstId].title) >= 0,
    "点了目录但正文没换成那一节");

  // ③ 点分组标题 → 应该跳到它下面第一节（就是用户报的那个问题）
  if (groupLinks.length) {
  window.location.hash = "#/" + data.scope[data.scope.length - 1];
  await new Promise(function (resolve) { setTimeout(resolve, 60); });
  const group = groupLinks[0];
  const expected = group ? group.getAttribute("data-group").split(",")[0] : null;
  if (group) {
  group.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true }));
  await new Promise(function (resolve) { setTimeout(resolve, 60); });
  check(window.location.hash === "#/" + expected,
    `点分组标题「${group.textContent.trim()}」后到了 ${window.location.hash}，期望 #/${expected}`);
  }
  }

  // ④ 点正文里的概念 → 抽屉要打开，而且内容是那个概念的通俗解释
  // 注意：不是每一节都有概念链接（章导言、纯引言类单元可能一个都没有），
  // 所以要**挑一节有链接的**来看，不能假设当前这节就有。
  const linkUnit = data.scope.filter(function (id) {
    return ((data.units[id] || {}).concepts || []).length > 0;
  })[0];
  check(!!linkUnit, "整章没有任何一节引用概念？数据不对");
  if (linkUnit) {
    window.location.hash = "#/" + linkUnit;
    await new Promise(function (resolve) { setTimeout(resolve, 80); });
  }
  const conceptLink = doc.querySelector(".markdown a.concept");
  check(!!conceptLink, `${linkUnit} 里没有概念链接`);
  if (conceptLink) {
    const conceptId = conceptLink.getAttribute("data-concept-id");
    conceptLink.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true }));
    await new Promise(function (resolve) { setTimeout(resolve, 60); });
    const drawer = doc.getElementById("drawer");
    check(drawer.classList.contains("open"), "点了概念链接，抽屉没打开");
    const text = doc.getElementById("drawer-body").textContent;
    const concept = data.concepts[conceptId];
    // 对比的是"同一份数据里的字符串"，两边编码一致，不受 jsdom 解码影响
    check(text.indexOf(concept.name) >= 0, "抽屉里没有概念名");
    check(!concept.explanation || text.indexOf(concept.explanation.slice(0, 12)) >= 0,
      "抽屉里没有通俗解释的内容");
    check(doc.getElementById("drawer-body").querySelectorAll("blockquote").length >= 1,
      "抽屉里没有教材原文表述（应该渲染成 blockquote）");
    check(doc.getElementById("drawer-body").querySelectorAll("h3").length >= 2,
      "抽屉的小标题少于 2 个（通俗解释 / 教材原文表述）");

    // ⑤ Esc 关掉
    doc.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await new Promise(function (resolve) { setTimeout(resolve, 60); });
    check(!drawer.classList.contains("open"), "按 Esc 抽屉没关掉");
  }

  // ⑥ 分享出去的 #concept= 链接：背后也得有内容，不能白屏
  const share = await JSDOM.fromFile(path.join(out, "index.html"), {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    virtualConsole: pageConsole(),
    url: "file://" + path.join(out, "index.html") + "#concept=" + Object.keys(data.concepts)[0],
  });
  share.window.scrollTo = function () {};
  await new Promise(function (resolve) { setTimeout(resolve, 400); });
  const shareDoc = share.window.document;
  check(!shareDoc.getElementById("content").textContent.includes("正在加载"),
    "用 #concept= 链接直接打开时，背后正文是空的");
  check(shareDoc.getElementById("drawer").classList.contains("open"),
    "用 #concept= 链接直接打开时，抽屉没打开");

  // ⑥·五 概念抽屉里的配图：生成的矢量图应该是**内联 <svg>**（不是 <img>）
  Object.keys(data.concept_figures || {}).forEach(function (conceptId) {
    const figs = data.concept_figures[conceptId] || [];
    figs.forEach(function (figureId) {
      const figure = data.figures[figureId];
      if (!figure) { problems.push(`概念 ${conceptId} 配的图 ${figureId} 不在 figures 里`); return; }
      if (!figure.svg) { problems.push(`概念配图 ${figureId} 不是内联 SVG（深色模式会看不见）`); }
    });
  });

  // ⑥·六 **每一个**"配了图的概念"都要点开看：抽屉里必须出现内联 SVG。
  // （只抽查第一个是不够的 —— 图多起来之后，某一张的登记可能漏了。）
  const withFigures = Object.keys(data.concept_figures || {}).filter(function (id) {
    return (data.concept_figures[id] || []).length && data.concepts[id];
  });
  for (const conceptId of withFigures) {
    window.location.hash = "#concept=" + conceptId;
    await new Promise(function (resolve) { setTimeout(resolve, 80); });
    const drawer = doc.getElementById("drawer-body");
    const drawn = drawer.querySelectorAll("svg").length;
    const wanted = (data.concept_figures[conceptId] || []).length;
    check(drawn >= wanted,
      `概念 ${conceptId} 配了 ${wanted} 张图，抽屉里只画出来 ${drawn} 张`);
    const svg = drawer.querySelector("svg");
    if (svg) {
      check(svg.innerHTML.indexOf("#000000") < 0,
        `概念 ${conceptId} 抽屉里的图颜色写死了，深色模式下会看不见`);
    }
  }
  const conceptsWithFigures = withFigures.length;

  // ⑥·七 **公式渲染总检查**：页面上任何地方都不许残留 `$...$` 原文。
  // 用户报过"一些公式仍然没有出来，尤其是定义的简单概念" —— 根因是 brief / formal /
  // 名称 / 图注 这些短字段当时用纯文本转义输出，只有 explanation 走了公式管线。
  // 这类"某个字段漏了"的毛病靠抽查发现不了，所以这里把页面上**所有概念卡**都翻一遍。
  const BARE_MATH = /\$[^$\n]{1,80}\$/;
  /** 把"故意字面显示"的区域（.raw，比如修正表里的原文列）排除掉再查。 */
  function literalText(node) {
    if (!node) return "";
    const clone = node.cloneNode(true);
    /* `.raw` = 故意按字面显示的（OCR 原文、修正表的"原文/理由"列）；
       `.warn` = **给作者看的校验提醒**（里面的公式片段是故意不渲染的 ——
       那些片段常常是被截断的 LaTeX，当公式渲染会报错，实测冒过一次红块）。
       两者都不是"学生该看到的内容"，所以查"有没有裸公式"时要排除它们
       （实测：§2.1 的公式覆盖提醒里带了 `$x_{1}$` 片段，被这条检查误报）。 */
    clone.querySelectorAll(".raw, .warn").forEach(function (el) { el.remove(); });
    return clone.textContent || "";
  }

  function bareMathIn(label, node) {
    const text = literalText(node);
    if (BARE_MATH.test(text)) {
      problems.push(`${label} 里有没渲染的公式：${(text.match(BARE_MATH) || [""])[0].slice(0, 40)}`);
    }
  }

  for (const conceptId of Object.keys(data.concepts)) {
    window.location.hash = "#concept=" + conceptId;
    await new Promise(function (resolve) { setTimeout(resolve, 60); });
    const drawer = doc.getElementById("drawer-body");
    bareMathIn(`概念 ${conceptId} 的抽屉`, drawer);
    const concept = data.concepts[conceptId];
    const wantsMath = ["brief", "explanation", "formal"]
      .some(function (f) { return (concept[f] || "").indexOf("$") >= 0; });
    if (wantsMath) {
      check(drawer.querySelectorAll(".katex").length > 0,
        `概念 ${conceptId} 的文本里有公式，但抽屉里一个公式都没渲染出来`);
    }
  }

  // 悬停提示（brief）
  window.location.hash = "#/" + data.scope[0];
  await new Promise(function (resolve) { setTimeout(resolve, 60); });
  const tipLink = doc.querySelector(".markdown a.concept");
  if (tipLink) {
    tipLink.dispatchEvent(new dom.window.MouseEvent("mouseover",
      { bubbles: true, cancelable: true }));
    await new Promise(function (resolve) { setTimeout(resolve, 60); });
    bareMathIn("悬停提示", doc.getElementById("tooltip"));
  }

  // 图注 / 概念小标签 / 讲解正文 / 侧边栏 / 翻页条（别留死角）
  doc.querySelectorAll("figcaption").forEach(function (node, index) {
    bareMathIn(`第 ${index + 1} 个图注`, node);
  });
  bareMathIn("讲解正文", doc.getElementById("content"));
  bareMathIn("侧边栏", doc.getElementById("sidebar"));
  bareMathIn("翻页条", doc.querySelector(".pager"));
  for (const conceptId of Object.keys(data.concepts)) {
    window.location.hash = "#concept=" + conceptId;
    await new Promise(function (resolve) { setTimeout(resolve, 40); });
  }
  window.location.hash = "#/" + data.scope[0];
  await new Promise(function (resolve) { setTimeout(resolve, 80); });
  bareMathIn("讲解正文", doc.getElementById("content"));

  // ⑦ 插图：要先把带图的那些节逐节点开数，不能只看当前这一节
  //   （第一版就犯了这个错：停在 1.1.1 上没有图，于是误报"插图没渲染"）
  Object.keys(data.figures).forEach(function (id) {
    const figure = data.figures[id];
    if (figure.svg) return;            // 生成的矢量图内联在页面里，没有文件
    check(figure.file && fs.existsSync(path.join(out, figure.file)),
      `插图 ${id} 的文件不在：${figure.file}`);
  });
  let figures = 0;
  for (const unitId of data.scope) {
    window.location.hash = "#/" + unitId;
    await new Promise(function (resolve) { setTimeout(resolve, 80); });
    // 数是"这一节里显示了几个图框"，不能只数 <img>（生成的矢量图是内联 <svg>）
    figures += doc.querySelectorAll("figure.figure").length;
  }
  const expectedFigures = Object.keys(data.units).reduce(function (sum, unitId) {
    return sum + (data.units[unitId].figures || []).length;
  }, 0);
  check(figures >= expectedFigures,
    `页面上渲染出来的插图 ${figures} 张，少于数据里的 ${expectedFigures} 张`);

  /* ── 缺口 C 的三条机器判据（2026-09-25 加）────────────────────────────────
     这三条对应的都是**用户实测报过的**现象，靠人眼看只能碰运气，所以钉成断言：
       ① 点概念胶囊 → 正文不许跳回第一节（P-019）；
       ② 换节要 mount/unmount 配平，不许留下上一节的模型（P-025-1）；
       ③ 数据坏了要有**看得见的说明**，不许永久"正在加载…"（P-025-2）。 */
  const unitHead = function () {
    const head = doc.querySelector(".unit-head h1");
    return head ? head.textContent : null;
  };
  if (data.scope.length >= 1) {
    const deepUnit = data.scope[data.scope.length - 1];
    window.location.hash = "#/" + deepUnit;
    await new Promise(function (resolve) { setTimeout(resolve, 120); });
    const before = unitHead();
    check(before && before.length > 0, `切到 ${deepUnit} 之后页面上没有这一节的标题`);

    // ① 点概念：当前节必须不变
    const chip = doc.querySelector(".markdown a.concept[data-concept-id]") ||
                 doc.querySelector("a.concept[data-concept-id]");
    if (chip) {
      chip.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true }));
      await new Promise(function (resolve) { setTimeout(resolve, 120); });
      check(unitHead() === before,
        `点概念之后正文换节了（${before} → ${unitHead()}）；P-019 又回来了`);
      check(decodeURIComponent(window.location.hash).indexOf("#concept=") >= 0,
        "点概念之后 hash 里没有 #concept=，抽屉分享链接会失效");
      // 关掉抽屉（顺手验一下关闭路径不会把读者踢回第一节）
      doc.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      await new Promise(function (resolve) { setTimeout(resolve, 60); });
      check(unitHead() === before, "关掉抽屉之后正文换节了");
    }

    // ② 换节 3 次：disposeAll 必须每次都调到，且页面上不残留别的节的模型
    const models = window.Models || {};
    check(typeof models.disposeAll === "function",
      "Models.disposeAll 不见了 —— 换节时画布不会被销毁（P-025-1）");
    let disposed = 0;
    if (typeof models.disposeAll === "function") {
      const original = models.disposeAll;
      models.disposeAll = function (root) { disposed += 1; return original.call(models, root); };
      /* 只数**真的换了节**的那些次：切到同一节是不重画的（P-019 就是这么定的），
         所以"请求次数"不等于"重画次数" —— 单节样张里一次都不该重画。 */
      let expected = 0, previous = null;
      for (let round = 0; round < 3; round++) {
        for (const unitId of data.scope) {
          if (previous !== unitId) expected += 1;
          previous = unitId;
          window.location.hash = "#/" + unitId;
          await new Promise(function (resolve) { setTimeout(resolve, 90); });
        }
      }
      models.disposeAll = original;
      check(disposed >= expected - 1,
        `换了 ${expected} 次节，只销毁了 ${disposed} 次画布（P-025-1）`);
      const current = data.scope[data.scope.length - 1];
      /* ⚠️ 数**模型框**（figure.model），不能数所有带 data-model-id 的元素：
         models.js 挂载时会给里面的 .model-stage 也打上同一个属性（自报日志要用），
         于是"1 个模型"会被数成 2 个 → 假报"上一节的模型没清掉"（实测踩过）。 */
      const shown = doc.querySelectorAll("#content figure.model[data-model-id]").length;
      const wanted = Object.keys(data.models || {}).filter(function (id) {
        return (data.models[id] || {}).unit_id === current;
      }).length;
      check(shown <= wanted,
        `当前这一节（${current}）的页面上有 ${shown} 个模型位，但这一节只有 ${wanted} 个 → 上一节的模型没清掉`);
    }
  }

  // ③ 数据坏了：要有看得见的说明（而不是永久"正在加载…"）
  try {
    const os = require("os");
    const broken = fs.mkdtempSync(path.join(os.tmpdir(), "aiforstudy-broken-"));
    fs.cpSync(out, broken, { recursive: true });
    fs.writeFileSync(path.join(broken, "data/data.js"),
      "/* 故意写坏：模拟「数据文件没跟着拷过来」 */\nwindow.__DATA__ = {};\n",
      "utf-8");
    const bad = await JSDOM.fromFile(path.join(broken, "index.html"), {
      runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
      virtualConsole: pageConsole(),
    });
    bad.window.scrollTo = function () {};
    await new Promise(function (resolve) { setTimeout(resolve, 600); });
    /* ⚠️ 断言只比**结构**，不比中文：jsdom 读 data.js 是按 cp1252 解的，
       中文在这个环境里本来就是乱码（见文件头那段说明），拿字面量比会假绿。 */
    const badDoc = bad.window.document;
    check(!badDoc.querySelector("#content p.empty"),
      "数据坏了之后页面还停在初始占位（「正在加载…」）上，读者不知道出了什么事（P-025-2）");
    const visible = badDoc.querySelector("#content .unit");
    check(!!visible, "数据坏了之后 #content 里没有任何说明区块（P-025-2）");
    check(!!badDoc.querySelector("#content code"),
      "说明里没有把「哪一步出错」写出来（P-025-2：要给读者一句可转发的话）");
    fs.rmSync(broken, { recursive: true, force: true });
  } catch (error) {
    problems.push("数据损坏这条自检没跑起来：" + (error && error.message));
  }

  console.log(JSON.stringify({
    units: unitLinks.length, groups: groupLinks.length,
    figures: figures.length, concepts: Object.keys(data.concepts).length,
    concepts_with_figures: conceptsWithFigures,
    problems: problems,
  }, null, 2));
  process.exit(problems.length ? 1 : 0);
})().catch(function (error) {
  console.error(error && error.stack || error);
  process.exit(2);
});
