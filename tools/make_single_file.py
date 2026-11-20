#!/usr/bin/env python3
"""把一份**构建好的网页目录**压成**一个自包含的 HTML 文件**（P-018）。

用户 2026-09-25：「演示文件 HTML 加上显示依赖也可以打包吗，然后发更直观」。
**背景**：网页目录里其实已经带齐依赖（`assets/`、`data/`），但**从 zip 里直接双击 `index.html` 打不开**
（浏览器读不了压缩包里的相对路径）——所以"发得更直观"的做法是**一个 .html 自带全部依赖**：

* CSS 全部内联；KaTeX 的字体已经是 base64（`vendor/katex-inlined.css`）；
* JS 全部内联，**包括 MathBox 那三件**（three.js / OrbitControls / mathbox）——
  提前内联之后 `models.js` 的按需加载会立刻命中，不会再去 `assets/mathbox/` 抓文件；
* 数据（`data/data.js`）内联。

用法：

    ../.venv-linux/bin/python tools/make_single_file.py <网页目录> <输出.html>

产物是**一个文件**，双击就开、断网也能用、可以直接发。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
VENDOR = ROOT / "blocks" / "render" / "vendor"

# 按这个顺序内联脚本（MathBox 要在 models.js 之前 —— models.js 里会检查全局是否就绪）
SCRIPT_ORDER = [
    "assets/mathbox/three.min.js",
    "assets/mathbox/OrbitControls.js",
    "assets/mathbox/mathbox.min.js",
    "assets/katex.min.js",
    "assets/marked.min.js",
    "assets/render-core.js",
    "assets/models.js",
    "data/data.js",
    "assets/app.js",
]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def inline_styles(page: Path, html: str) -> str:
    def repl(match: re.Match) -> str:
        href = match.group(1)
        if href.startswith(("http:", "https:", "data:")):
            return match.group(0)
        # KaTeX 的字体是相对路径 → 用已经打好 base64 的那份（否则没法内联）
        source = (VENDOR / "katex-inlined.css") if href.endswith("katex.min.css") else (page / href)
        if not source.is_file():
            print(f"  ⚠ 样式找不到，保留原样：{href}", file=sys.stderr)
            return match.group(0)
        css = read(source)
        # 保险：万一还有相对 url(...)，替换成 data URI 或去掉
        def fix_url(u: re.Match) -> str:
            rel = u.group(1).strip("'\"")
            if rel.startswith(("data:", "http")):
                return u.group(0)
            target = (page / rel)
            if not target.is_file():
                target = (page / "assets" / rel.lstrip("./"))
            if target.is_file() and target.suffix in (".woff2", ".woff", ".ttf"):
                import base64
                mime = {"woff2": "font/woff2", "woff": "font/woff", "ttf": "font/ttf"}[
                    target.suffix.lstrip(".")]
                data = base64.b64encode(target.read_bytes()).decode("ascii")
                return f"url(data:{mime};base64,{data})"
            return u.group(0)
        css = re.sub(r"url\(([^)]+)\)", fix_url, css)
        return "<style>\n" + css + "\n</style>"
    return re.sub(r'<link[^>]*rel="stylesheet"[^>]*href="([^"]+)"[^>]*>', repl, html)


def inline_scripts(page: Path, html: str) -> str:
    def repl(match: re.Match) -> str:
        src = match.group(1)
        if src.startswith(("http:", "https:", "data:")):
            return match.group(0)
        target = page / src
        if not target.is_file():
            print(f"  ⚠ 脚本找不到：{src}", file=sys.stderr)
            return match.group(0)
        code = read(target)
        return ('<script data-from="' + src + '">\n' + code + "\n</script>")
    return re.sub(r'<script src="([^"]+)"></script>', repl, html)


def add_missing_mathbox(page: Path, html: str) -> str:
    """页面里如果没写 MathBox 那三个 <script>（构建时是**按需加载**的），就补进去。

    不补的话，单文件版一打开会去抓 `assets/mathbox/…`（单文件里没有这些文件）→ 3D/画布模型起不来。
    """
    missing = [s for s in SCRIPT_ORDER[:3] if f'data-from="{s}"' not in html]
    if not missing:
        return html
    blocks = []
    for src in missing:
        target = page / src
        if target.is_file():
            blocks.append('<script data-from="' + src + '">\n' + read(target) + "\n</script>")
    if not blocks:
        return html
    return html.replace("</head>", "\n".join(blocks) + "\n</head>")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="把网页目录压成一个自包含 HTML（含字体与 3D 库）")
    ap.add_argument("page", help="构建好的网页目录（里面有 index.html）")
    ap.add_argument("out", help="输出的单个 .html")
    args = ap.parse_args(argv)

    page, out = Path(args.page).resolve(), Path(args.out)
    index = page / "index.html"
    if not index.is_file():
        print(f"✗ 找不到 {index}", file=sys.stderr)
        return 2
    html = read(index)
    html = add_missing_mathbox(page, html)
    html = inline_styles(page, html)
    html = inline_scripts(page, html)
    # 查"还有没有外部文件引用"必须**先把内联的 <script>/<style> 内容剥掉** ——
    # 否则会把 JS 代码里的字符串（`'<img src="' + …`、压缩变量 `${e=s}`）当成页面引用（实测误报过）。
    structure = re.sub(r"<script[^>]*>.*?</script>", "<script></script>", html, flags=re.S)
    structure = re.sub(r"<style[^>]*>.*?</style>", "<style></style>", structure, flags=re.S)
    left = re.findall(r'(?:src|href)="(?!data:|https?:|#)([^"]+)"', structure)
    out.write_text(html, encoding="utf-8")
    print(f"单文件已生成：{out}（{out.stat().st_size/1024/1024:.1f} MB）")
    if left:
        print(f"  ⚠ 还有 {len(left)} 处外部引用（应当清空）：{sorted(set(left))[:6]}", file=sys.stderr)
        return 1
    print("  ✓ 没有任何外部文件引用（字体已 base64、脚本已内联）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
