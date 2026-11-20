#!/usr/bin/env python3
"""把演示页打成**单文件自包含** HTML（图片、KaTeX 的 JS/CSS/字体全部内联成 data URI）。

为什么要这一步：演示页原来引用 `assets/figures/*.png`、`assets/katex.min.js` 这些**相对路径**，
一旦在**别处打开**（下载下来、或放进预览器/沙箱 iframe），同目录资源取不到 →
页面就成了"没有图、公式不出来"的空壳（用户实测就是这样）。

用法：
```bash
python tools/pack_demo.py data/site/circuit-analysis-1/demo-f-scrolly.html
# → 同目录 demo-f-scrolly-standalone.html
```
"""
from __future__ import annotations

import argparse
import base64
import mimetypes
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _inline_css(css: str, base: Path) -> str:
    """把 CSS 里的字体 URL 换成 data URI；顺手把 woff/ttf 那些分支删掉（只留 woff2，省一半体积）。"""
    css = re.sub(r",\s*url\([^)]*\.(?:woff|ttf|otf)\)[^,;}]*", "", css)   # 只留 woff2

    def repl(match):
        rel = match.group(1).strip("'\"")
        path = (base / rel).resolve()
        return f"url({_data_uri(path)})" if path.is_file() else match.group(0)

    return re.sub(r"url\(([^)]+)\)", repl, css)


def pack(html_path: Path, out_path: Path | None = None) -> Path:
    base = html_path.parent
    text = html_path.read_text(encoding="utf-8")

    # ① KaTeX 的 CSS（含字体）
    css_path = base / "assets" / "katex.min.css"
    if css_path.is_file():
        css = _inline_css(css_path.read_text(encoding="utf-8"), base / "assets")
        text = re.sub(r"<link[^>]*katex\.min\.css[^>]*>", f"<style>{css}</style>", text)

    # ② KaTeX 的 JS
    js_path = base / "assets" / "katex.min.js"
    if js_path.is_file() and "assets/katex.min.js" in text:
        text = text.replace("<script src='assets/katex.min.js'></script>",
                            f"<script>{js_path.read_text(encoding='utf-8')}</script>")
        text = text.replace('<script src="assets/katex.min.js"></script>',
                            f"<script>{js_path.read_text(encoding='utf-8')}</script>")

    # ③ 其余脚本与样式（站点页会有 app.js / render-core.js / models.js / marked / styles.css / data.js）
    for match in list(re.finditer(r'<script src="((?:assets|data)/[^"]+)"\s*></script>', text)):
        path = (base / match.group(1)).resolve()
        if path.is_file():
            text = text.replace(match.group(0),
                                f"<script>{path.read_text(encoding='utf-8')}</script>")
    for match in list(re.finditer(r'<link[^>]*href="(assets/[^"]+\.css)"[^>]*>', text)):
        path = (base / match.group(1)).resolve()
        if path.is_file():
            css = _inline_css(path.read_text(encoding="utf-8"), base / "assets")
            text = text.replace(match.group(0), f"<style>{css}</style>")

    # ④ 图片
    missing: list[str] = []

    def repl_img(match):
        rel = match.group(2)
        path = (base / rel).resolve()
        if not path.is_file():
            missing.append(rel)
            return match.group(0)
        return f'{match.group(1)}"{_data_uri(path)}"'

    text = re.sub(r'(src=)"((?:assets|data)/[^"]+\.(?:png|jpg|jpeg|webp|gif|svg))"', repl_img, text)

    out = out_path or html_path.with_name(html_path.stem + "-standalone.html")
    out.write_text(text, encoding="utf-8")
    left = re.findall(r'(?:src|href)="(assets/[^"]+)"', text)
    print(f"✓ {out.name}  {out.stat().st_size / 1024:.0f} KB"
          + (f"  ｜ ⚠️ 没找到: {missing}" if missing else "")
          + (f"  ｜ ⚠️ 还有外部引用: {sorted(set(left))}" if left else "  ｜ 外部引用 0"))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="演示页 → 单文件自包含 HTML")
    ap.add_argument("html", nargs="+")
    args = ap.parse_args(argv)
    for name in args.html:
        p = Path(name)
        if not p.is_absolute():
            p = ROOT / p
        pack(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
