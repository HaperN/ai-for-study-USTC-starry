#!/usr/bin/env python3
"""把**整本书**的各章页面合成**一个自包含 HTML**（一整本，双击就开）。

为什么需要（用户 2026-09-27 提的）：
「之前跑的，为什么不可以整本书弄成一个html给我看呢」

渲染层是**按章出页面**的（每章的教案是一份独立 scope，见 `blocks/render/build.py`），
交付目录里是 `CH01/`…`CH17/` 各自一个网页；`tools/make_single_file.py` 只压**一个页面**。
所以"整本一个文件"缺的只是**把各章的 `data/data.js` 合成一份**：

* `tree` / `scope` → 直接拼（顺序按章号）
* `units` / `concepts` / `figures` / `diagrams` / `models` / … → 取并集（id 不会撞）
* `counts` → 逐项相加

合成之后再走 `make_single_file`（字体 base64、脚本内联、MathBox 内联）。

用法：

    ../.venv-linux/bin/python tools/make_book_single_file.py <交付目录（含 CHxx/）> <输出.html> [--page-dir 临时目录]

自检：合成后直接跑 `node blocks/render/check_render.js <临时页面目录>`
（无浏览器也能验证每一节的 markdown 渲染得出来）。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import make_single_file as single  # noqa: E402  （复用它的内联逻辑，别抄第二份）

#: 这些字段是"id → 内容"的表，合成时取并集
DICT_FIELDS = ("units", "concepts", "figures", "diagrams", "models",
               "concept_figures", "number_refs")
#: 这些字段是书本级的标量，保留第一份
SCALAR_FIELDS = ("book", "generator", "schema_version")


def load_payload(path: Path) -> dict:
    """读一个 `data/data.js`（形如 `window.__DATA__ = {…};`）。"""
    text = path.read_text(encoding="utf-8")
    matched = re.search(r"window\.__DATA__\s*=\s*(\{.*\})\s*;?\s*$", text, re.S)
    if not matched:
        raise ValueError(f"认不出 data.js 的格式：{path}")
    return json.loads(matched.group(1))


def chapter_dirs(delivery: Path) -> list[Path]:
    """交付目录里的章节目录（按 `CHxx` 数字排，`CH02` 排在 `CH10` 前面）。"""
    rows = [d for d in delivery.iterdir()
            if d.is_dir() and (d / "data" / "data.js").is_file()]
    return sorted(rows, key=lambda d: (len(d.name), d.name))


def merge(payloads: list[dict]) -> dict:
    merged: dict = {field: payloads[0].get(field) for field in SCALAR_FIELDS}
    merged["scope"] = [uid for row in payloads for uid in (row.get("scope") or [])]
    merged["tree"] = [node for row in payloads for node in (row.get("tree") or [])]
    for field in DICT_FIELDS:
        table: dict = {}
        for row in payloads:
            table.update(row.get(field) or {})
        merged[field] = table
    counts: dict = {}
    for row in payloads:
        for key, value in (row.get("counts") or {}).items():
            if isinstance(value, (int, float)):
                counts[key] = counts.get(key, 0) + value
            else:
                counts.setdefault(key, value)
    counts["chapters"] = len(payloads)
    counts["units"] = len(merged["units"])
    merged["counts"] = counts
    return merged


def build(delivery: Path, out: Path, page_dir: Path | None = None) -> int:
    dirs = chapter_dirs(delivery)
    if not dirs:
        print(f"✗ {delivery} 里没有章节页面（应当有 CHxx/data/data.js）", file=sys.stderr)
        return 2
    payloads = [load_payload(d / "data" / "data.js") for d in dirs]
    merged = merge(payloads)

    temp = Path(page_dir) if page_dir else Path(tempfile.mkdtemp(prefix="整本-单文件-"))
    temp.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(dirs[0] / "index.html", temp / "index.html")
    if (temp / "assets").exists():
        shutil.rmtree(temp / "assets")
    shutil.copytree(dirs[0] / "assets", temp / "assets")
    # ⚠️ **插图是各章自己一份**（实测：CH01/assets/figures 是空的，CH07 有 15 张）——
    #    只拷第一章的 assets 会让后面所有章的插图"文件不存在"（实测 60 张全丢）。
    #    所以把每一章的 assets 树并进来，缺什么补什么。
    copied_figures = 0
    for directory in dirs[1:]:
        for source in (directory / "assets").rglob("*"):
            if not source.is_file():
                continue
            target = temp / "assets" / source.relative_to(directory / "assets")
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if "figures" in source.parts:
                copied_figures += 1
    (temp / "data").mkdir(exist_ok=True)
    (temp / "data" / "data.js").write_text(
        "/* 本文件由 tools/make_book_single_file.py 合成：整本 " + str(merged.get("book")) + " */\n"
        "window.__DATA__ = " + json.dumps(merged, ensure_ascii=False) + ";\n",
        encoding="utf-8")

    figures = len(list((temp / "assets" / "figures").glob("*"))) if (temp / "assets" / "figures").is_dir() else 0
    print(f"合成了 {len(dirs)} 章：{[d.name for d in dirs]}")
    print(f"  单元 {len(merged['units'])} 个｜概念 {len(merged['concepts'])} 条｜"
          f"图 {len(merged['figures'])} 张（文件 {figures} 个，补拷 {copied_figures} 个）｜"
          f"过程图 {len(merged['diagrams'])} 个")
    print(f"  页面目录：{temp}（可以用 `node blocks/render/check_render.js {temp}` 自检）")
    return single.main([str(temp), str(out)])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="把整本书的各章页面合成一个自包含 HTML")
    ap.add_argument("delivery", help="交付目录（里面是 CHxx/ 各章页面）")
    ap.add_argument("out", help="输出的单个 .html")
    ap.add_argument("--page-dir", help="合成用的页面目录（默认建在临时目录；留档时给它一个路径）")
    args = ap.parse_args(argv)
    return build(Path(args.delivery).resolve(), Path(args.out), args.page_dir)


if __name__ == "__main__":
    sys.exit(main())
