/* 公式定界符的自检：`$...$` / `$$...$$` 的两种真实写法都要认对。
 *
 * 为什么要单独查这个（实测踩过两次）：
 *   ① `$$A$$ 与 $$B$$` —— 模型会在**一行里写两条显示公式**，必须都渲染成显示公式；
 *   ② `$A$$B$`         —— 两个相邻的行内公式撞出来的 `$$` 不是显示公式定界符，
 *                          认错的话中间整段被吞掉（一处渲染失败 + 13 个槽位泄漏到页面上）。
 *
 * 用法：node check_math.js        （有问题以非 0 退出）
 */
const path = require("path");
const here = __dirname;

const markedMod = require(path.join(here, "vendor/marked.min.js"));
const marked = markedMod.marked || markedMod;
const katex = require(path.join(here, "vendor/katex.min.js"));
const core = require(path.join(here, "assets/render-core.js"));

const renderer = core.createRenderer({ marked: marked, katex: katex, concepts: {}, figures: {} });

const CASES = [
  { name: "一行两条显示公式", text: "所以\n$$A$$ 与 $$B$$\n一般并不相等。",
    katex: 2, display: 2 },
  { name: "相邻两个行内公式（撞出 $$）", text: "映射:  $\\sigma(1)=2,$$\\sigma(3)=1$ .",
    katex: 2, display: 0 },
  { name: "普通行内公式", text: "设 $A$ 与 $B$ 是两个集合。", katex: 2, display: 0 },
  { name: "单条显示公式", text: "$$E = mc^2$$", katex: 1, display: 1 },
  { name: "多条显示公式各占一行", text: "$$\na\n$$\n\n$$\nb\n$$", katex: 2, display: 2 },
  { name: "跨行的显示公式", text: "$$\n\\begin{array}{c} a \\\\ b \\end{array}\n$$", katex: 1, display: 1 },
  { name: "没闭合的 $ 不能崩", text: "这里有个 $ 符号", katex: 0, display: 0 },
  { name: "行内与显示混排", text: "设 $A$ 则\n$$A = B$$\n成立。", katex: 2, display: 1 },
];

const problems = [];
CASES.forEach(function (item) {
  const html = renderer.renderMarkdown(item.text);
  const found = {
    katex: (html.match(/class="katex"/g) || []).length,   // 外层元素，一条公式一次
    display: (html.match(/katex-display/g) || []).length,
    slotLeak: (html.match(/\u0000/g) || []).length,
    mathErrors: (html.match(/class="math-error"/g) || []).length,
  };
  if (found.slotLeak) problems.push(`${item.name}: 内部槽位泄漏 ${found.slotLeak} 个`);
  if (found.mathErrors) problems.push(`${item.name}: ${found.mathErrors} 处公式渲染失败`);
  if (found.katex !== item.katex) {
    problems.push(`${item.name}: 渲染出 ${found.katex} 处公式，期望 ${item.katex}`);
  }
  if (found.display !== item.display) {
    problems.push(`${item.name}: 显示公式 ${found.display} 条，期望 ${item.display}`);
  }
});

console.log(JSON.stringify({ cases: CASES.length, problems: problems }, null, 2));
process.exit(problems.length ? 1 : 0);
