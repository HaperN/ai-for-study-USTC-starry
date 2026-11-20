"""渲染层回归测试。

这一层最麻烦的地方是**没法用浏览器自动验**（这台机器上没有浏览器）。
所以验证分成两半：
  * Python 侧：产物结构、自检、裁剪的图、index.html 不引外网资源；
  * node 侧：加载**与浏览器同一份** vendor 库和 render-core.js，把真实数据
    过一遍渲染管线，检查"该出现的东西在不在、不出现的东西有没有"
    （没展开的占位符、找不到的引用、渲染失败的公式）。这些是页面白屏或者
    显示成 `[[CONCEPT:C-0001]]` 的直接原因。
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "blocks" / "render"))
sys.path.insert(0, str(ROOT / "lib"))

import build as B          # noqa: E402

NODE = shutil.which("node")






JSDOM_HOME = ROOT / "node_modules"


def _jsdom_available() -> bool:
    if NODE is None:
        return False
    probe = subprocess.run([NODE, "-e", "require(\"jsdom\")"],
                           env={**__import__("os").environ,
                                "NODE_PATH": str(JSDOM_HOME)},
                           capture_output=True)
    return probe.returncode == 0


# ── 生成的矢量图（2026-09-23）：内联 SVG、id 加前缀、概念反查 ────────────




def test_inline_svg_strips_prolog_and_namespaces(tmp_path):
    raw = ('<?xml version="1.0"?>\n<!DOCTYPE svg PUBLIC "x" "y">\n'
           '<svg><g id="aa"><use href="#aa"/><use xlink:href="#aa"/></g></svg>')
    out = B.inline_svg(raw, "FIG-x")
    assert "<?xml" not in out and "DOCTYPE" not in out
    assert out.count('id="FIG-x-aa"') == 1
    assert 'href="#FIG-x-aa"' in out
    assert 'xlink:href="#FIG-x-aa"' in out


@pytest.mark.skipif(NODE is None, reason="没有 node，跳过")
def test_render_inline_renders_math_and_escapes_html(tmp_path):
    """短字段（brief / formal / 名称 / 图注）必须走 renderInline：
    公式要渲染出来，HTML 要转义掉。踩过的坑：这些字段原来用纯文本转义输出，
    里面的 `$...$` 就**原样显示成美元符**——"定义的简单概念"最容易中招。"""
    script = """
    const path = require("path");
    const root = process.argv[1];
    const marked = require(path.join(root, "vendor/marked.min.js"));
    const katex = require(path.join(root, "vendor/katex.min.js"));
    const core = require(path.join(root, "assets/render-core.js"));
    const r = core.createRenderer({ marked: marked.marked || marked, katex: katex,
                                    concepts: {}, figures: {} });
    const html = r.renderInline("模记作 $|\\boldsymbol{a}|$ <b>粗</b>");
    console.log(JSON.stringify({
      katex: /class="katex/.test(html),
      escaped: html.indexOf("&lt;b&gt;") >= 0,
      bareDollar: /\\$/.test(html.replace(/<annotation[^>]*>[^<]*<\\/annotation>/g, "")),
      wrappedInP: /<p[ >]/.test(html),
    }));
    """
    import os
    result = subprocess.run(
        [NODE, "-e", script, str(ROOT / "blocks/render")],
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["katex"] is True, report
    assert report["escaped"] is True, report       # HTML 必须转义（安全）
    assert report["wrappedInP"] is False, report   # 短字段不该被套 <p>


@pytest.mark.skipif(NODE is None, reason="没有 node，跳过")
def test_math_delimiters_survive_both_real_world_spellings():
    """公式定界符的两种真实写法都要认对（实测踩过两次）：

    ① 模型会在**一行里写两条显示公式**（`$$A$$ 与 $$B$$`）—— 必须都当显示公式；
    ② 两个相邻的行内公式会撞出 `$A$$B$` —— 中间那个 `$$` **不是**显示公式定界符；
       认错就会把中间整段吞掉（一处公式渲染失败 + 13 个内部槽位泄漏到页面上）。
    """
    result = subprocess.run(
        [NODE, str(ROOT / "blocks/render/check_math.js")],
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout[result.stdout.find("{"):])
    assert report["problems"] == [], report


@pytest.mark.skipif(NODE is None, reason="没有 node，跳过")
def test_diagram_templates_render_without_a_browser():
    """过程图（把一步步的过程画出来）：模型只给结构化数据，渲染层按模板画。

    实测踩过：`var diagrams` 那行没加进去（替换锚点少了 `var`），
    结果 `[[DIAGRAM:…]]` 一渲染就 `ReferenceError` —— 所以这里必须真跑一遍。
    """
    script = r"""
    const path = require("path");
    const root = path.join(process.argv[1], "blocks/render");
    const markedMod = require(path.join(root, "vendor/marked.min.js"));
    const katex = require(path.join(root, "vendor/katex.min.js"));
    const core = require(path.join(root, "assets/render-core.js"));
    const diagrams = {
      "U.D-01": {template: "matrix-flow", title: "消元过程", steps: [
        {math: "$$A$$", op: "r_2-2r_1", note: "消去 x_1"}, {math: "$$B$$"}]},
      "U.D-02": {template: "process", title: "思路", steps: [
        {label: "化三角形"}, {label: "回代"}]},
    };
    const r = core.createRenderer({marked: markedMod.marked || markedMod, katex: katex,
                                   concepts: {}, figures: {}, diagrams: diagrams});
    const html = r.renderMarkdown("看看：\n\n[[DIAGRAM:U.D-01]]\n\n[[DIAGRAM:U.D-02]]\n\n完了。");
    console.log(JSON.stringify({
      flow: /class="diagram matrix-flow"/.test(html),
      arrows: (html.match(/diagram-arrow/g) || []).length,
      op: html.indexOf("r_2-2r_1") >= 0,
      process: /class="diagram process"/.test(html),
      nodes: (html.match(/class="node"/g) || []).length,
      leaked: /\[\[DIAGRAM/.test(html),
    }));
    """
    result = subprocess.run([NODE, "-e", script, str(ROOT)],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["flow"] and report["process"], report
    assert report["arrows"] >= 1 and report["op"], report
    assert report["nodes"] == 2, report
    assert not report["leaked"], report


# ── P-008：模型落点（方案 A）——————————————————————————————————————


def test_math_inside_a_ref_display_text_does_not_leak_a_slot(tmp_path):
    """P-015：`[[EXPLAIN:F-01|换行让 $a_{11}$ 非零]]` 这种"显示文字里带公式"的引用，
    以前会在页面上漏出内部槽位（整章跑出来的 §2.3.1 就漏了两处）。

    根因：mask() 先收公式、后收引用 → 公式槽位跑进引用的显示文字里，展开时跨了仓库，谁也展不开。
    """
    import subprocess
    # 注意：必须是 **raw 字符串** —— 下面的 JS 里有 `\u0000` 这样的转义，
    # 普通字符串会被 Python 先解开成一个真的 NUL 字符，而 execve 不许参数里带 NUL
    # （实测：报 ValueError，看着像 node 找不到）。
    js = r"""
    const core = require(process.argv[1]);
    const marked = (require(process.argv[2]).marked) || require(process.argv[2]);
    const katex = require(process.argv[3]);
    const r = core.createRenderer({marked: marked, katex: katex, concepts: {}, figures: {},
                                   diagrams: {}, models: {}});
    r.beginUnit({notes: {}, explain: {"F-01": {id: "F-01", name: "换行",
                  what: "让 $a_{11}$ 不为零", why: "否则消元做不下去"}}});
    const html = r.renderMarkdown("看这里 [[EXPLAIN:F-01|换行让 $a_{11}$ 非零]] 结束。");
    console.log(JSON.stringify({slot: html.split(String.fromCharCode(0)).length - 1,
                                katex: (html.match(/class="katex/g) || []).length,
                                chip: (html.match(/class="fchip"/g) || []).length}));
    """
    out = subprocess.run([NODE, "-e", js,
                          str(ROOT / "blocks/render/assets/render-core.js"),
                          str(ROOT / "blocks/render/vendor/marked.min.js"),
                          str(ROOT / "blocks/render/vendor/katex.min.js")],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout.strip().splitlines()[-1])
    assert result["slot"] == 0, result          # 不再漏内部槽位
    assert result["chip"] == 1 and result["katex"] >= 1, result   # 胶囊在、公式也渲染了


def test_check_render_counts_every_placeholder_kind():
    """**跨语言一致性护栏**：前端自检数的占位符种类，必须与 `lib/refs.py` 的 kind 集合一致。

    为什么要有它：`check_render.js` 原来只数 `CONCEPT|FIGURE` 两种，于是页面上漏出
    `[[NOTE:…]]` / `[[EXPLAIN:…]]` / `[[DIAGRAM:…]]` / `[[MODEL:…]]` 时**自检说没问题** ——
    而那正是读者看到的方括号乱码。Python 和 JS 没法互相 import，所以用一条测试盯住。
    """
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "lib"))
    from refs import PLACEHOLDER_KINDS
    js = (root / "blocks/render/check_render.js").read_text(encoding="utf-8")
    m = re.search(r"unexpanded: \(html\.match\(/\\\[\\\[\(([^)]+)\)", js)
    assert m, "找不到 check_render.js 里数未展开占位符的那一行（结构变了，请更新这条护栏）"
    kinds_js = {k.strip() for k in m.group(1).split("|")}
    kinds_py = set(PLACEHOLDER_KINDS)
    assert kinds_js == kinds_py, (
        f"前端自检与 lib/refs.py 的占位符种类不一致：js={sorted(kinds_js)} py={sorted(kinds_py)}"
        "（漏掉的那些种类在页面上漏出来时，自检不会报）")
