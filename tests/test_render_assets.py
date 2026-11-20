"""渲染层脚本的**静态**回归测试（不需要浏览器）。

来历（2026-10-01）：给正文加 `[[EXAMPLE:E-01]]` 时踩了两个坑 ——
  ① 分发里加了分支，但 `exampleInlineHtml` 读了一个**不存在的变量** `state.examples`
     （这文件里的单元上下文叫 `unitCtx`）→ 一渲染就抛异常，页面上锚点既不展开也不原样露出；
  ② 卡片里用了 `<ol>/<li>`，而锚点在某段话中间 —— 块级列表被 HTML 解析器**踢出**外层 `<span>`，
     表现是"卡片在、步骤没了"（实测 steps=0）。
所以这里把"**REF 里的每一种标记都得有落点**"钉成断言：加类型而忘了接分支，测试当场红。
"""
from __future__ import annotations

import re
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "blocks" / "render" / "assets"
#: 这些类型故意走兜底分支（`figureHtml`），不需要自己的 `if`
FALLBACK_OK = {"FIGURE"}


def _core() -> str:
    return (ASSETS / "render-core.js").read_text(encoding="utf-8")


def test_every_marker_type_in_ref_has_a_dispatch_branch():
    src = _core()
    marker = "var REF = /\\[\\[("
    assert marker in src, "没找到 REF 正则"
    tail = src.split(marker, 1)[1]
    types = set(tail.split(")", 1)[0].split("|"))
    types = {t.strip() for t in types if t.strip()}
    assert "EXAMPLE" in types, "EXAMPLE 必须注册进 REF（否则正文里的锚点不会被识别）"
    branches = set(re.findall(r'ref\.type === "(\w+)"', src))
    missing = types - branches - FALLBACK_OK
    assert not missing, f"这些标记类型没有分发分支：{sorted(missing)}"


def test_example_card_uses_inline_elements_only():
    """例题卡片是**行内**展开的：不许出现 `<ol>`/`<li>`/`<div>`，否则会被踢出外层 `<p>`。"""
    src = _core()
    start = src.index("function exampleInlineHtml(")
    rest = src[start:]
    nxt = rest.find("\n    function ", 10)          # 下一个函数定义 = 本函数的结束
    body = rest if nxt < 0 else rest[:nxt]
    # 注释里会提到"不能用 <ol>/<li>"（就是这条规则本身）→ 先剥掉行注释再判
    body = "\n".join(line.split("//")[0] for line in body.splitlines())
    for bad in ("<ol>", "<li>", "<div"):
        assert bad not in body, f"例题卡片里不许用 {bad}（块级元素会破坏所在段落）"
    assert "unitCtx && unitCtx.examples" in body, "例题要从 unitCtx（单元上下文）里取"
