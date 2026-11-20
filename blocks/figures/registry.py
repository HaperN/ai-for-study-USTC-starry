"""图形登记表：把画好的图登记成渲染层能读的清单（`figures/index.json`）。

一张图登记什么：
  id            渲染层和讲解正文用的引用名（`[[FIGURE:FIG-parallelogram]]`）
  title         图题（渲染层当 figcaption）
  svg / geometry  产物路径（相对项目根，渲染层会读 SVG 并**内联**进页面）
  concept_names 这张图**服务哪些概念**（按名字声明，用于概念抽屉里配图）—— 由画图的人声明，
                不是猜的。**名字才是稳定的那个键**，见下面这条血泪教训。
  concepts      名字在当前概念表里**解析出来的 id**（缓存，可随时重算）
  checks        断言结果（画完当场校验，不合格的图不该登记）

为什么 SVG 要内联而不是 `<img src>`：`<img>` 里的 `currentColor` 会解析成黑色，
**页面切深色模式时线条就看不见了**。内联之后颜色跟着 CSS 变量走。

⚠️ **为什么改成按名字登记**（2026-09-25，P-020/P-032 的根治）：
原来只登记 id。而 id 是**某一次概念抽取的快照**：这 12 张向量图的 `C-0002` 来自
`linear-algebra-ocr` 那一版（那时 C-0002 = 相等向量）；后来概念表重抽，`linear-algebra-full`
**把号重用了**（C-0002 = 同解方程组）。于是第二章的概念抽屉里配上了第一章的向量图 ——
一路走到交付页才被用户看见。**id 会变，名字不会**，所以：
  · 登记表按 `concept_names` 声明；
  · `concepts` 只是用名字在**当前**概念表里查出来的缓存；
  · 查不到就**不挂**（写进 `unresolved`），宁可不配图，也不能配错图；
  · 重抽概念之后跑 `rehang()`，它会把"哪张图的哪个名字对不上了"打印出来。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "lib"))
from files import write_json                       # noqa: E402

GENERATOR = "aiforstudy.figures"
SCHEMA_VERSION = "1.0"


def write_index(root: str | Path, entries: list[dict]) -> Path:
    """写 `figures/index.json`。`root` 是项目的 figures 目录。"""
    root = Path(root)
    payload = {
        "_meta": {
            "file_type": "figure_index",
            "schema_version": SCHEMA_VERSION,
            "generator": GENERATOR,
            "field_notes": {
                "figures[].id": "渲染层引用名；讲解正文写 [[FIGURE:FIG-parallelogram]]",
                "figures[].title": "图题（渲染层当 figcaption）",
                "figures[].svg": "矢量图路径（相对项目根）；渲染层会内联它，不用 <img>",
                "figures[].concepts": "这张图服务哪些概念（用于概念抽屉里配图），由画图的人声明",
                "figures[].checks": "画完当场断言的校验结果；不合格的图不该登记",
                "说明": "扫描裁图（IMG-xxxx）不在这里，它们在解析块的 images.json 里；"
                        "两种图在渲染层共存",
            },
        },
        "figures": entries,
    }
    return write_json(root / "index.json", payload) or (root / "index.json")


def entry(name: str, module, produced: dict, checks: dict) -> dict:
    """从画图模块（或图库里的声明）和校验结果组装一条登记。

    `concept_names` 是画图的人声明的**概念名**（稳定键）；`concepts` 由 `rehang()` 按名字
    在当前概念表里解析出来。老的画图模块（比如 `vector_parallelogram`）只声明了 id，
    这里兼容地留空 `concept_names`（并在 `rehang` 时原样保留它的 id）。
    """
    title = getattr(module, "TITLE", None) or (
        module.get("title") if isinstance(module, dict) else None) or name
    names = list(getattr(module, "CONCEPT_NAMES", None) or (
        module.get("concept_names") if isinstance(module, dict) else None) or [])
    legacy_ids = list(getattr(module, "CONCEPTS", None) or (
        module.get("concepts") if isinstance(module, dict) else None) or [])
    # `backend` 是**只增**字段：渲染层/教案层可据此区分"程序生成的矢量图"与"教材扫描图"。
    _backend = (module.get("backend") if isinstance(module, dict)
                else getattr(module, "BACKEND", None)) or "vector"
    # 3D 图（backend=three）**按约定**带两个资产名：scene.json（页面端 three.js 照它画）、
    # glb（归档资产）。⚠️ 不要用"相对 CWD 的存在性判断" —— 输出目录是调用方给的
    # （`--out /tmp/…`），`figures/x.scene.json` 这种相对路径必然误判成不存在（实测踩过）。
    # 这里直接按约定写名字：图能登记进来，就说明后端已经把这两个文件写出来了。
    extra = {}
    if _backend == "three":
        extra = {"scene": f"figures/{name}.scene.json", "gltf": f"figures/{name}.glb"}

    return {
        "id": f"FIG-{name}",
        "name": name,
        "backend": _backend,
        "title": title,
        "svg": f"figures/{name}.svg",
        "geometry": f"figures/{name}.geometry.json",

        "concept_names": names,
        "concepts": legacy_ids if not names else [],
        "checks": {"ok": bool(checks.get("ok")), "problems": checks.get("problems") or []},
        "canvas": (checks.get("checks") or {}).get("画布"),
        **extra,
    }


def rehang(entries: list[dict], concepts: list[dict] | dict,
           *, apply_ids: bool = True) -> dict:
    """按**概念名**把"图 → 概念"重挂到当前概念表上，并给出 diff。

    这是概念重抽之后必须跑的一步（在此之前，登记表里的 id 是**上一版**的快照）。
    返回：`{"figures": 重挂后的登记, "diff": {...}, "unresolved": {...}}`
      · 名字在当前表里查得到 → 挂上新 id
      · 查不到 → **不挂**，名字进 `unresolved`（宁可不配图，也不能配错图）
      · 没有 `concept_names` 的老登记（只声明过 id）→ 原样保留，但**记一条提醒**：
        只要它的 id 在新表里对应了另一个名字，就说明它已经错配了。
    """
    rows = concepts.get("concepts") if isinstance(concepts, dict) else concepts
    by_name = {str(c.get("name") or "").strip(): str(c["id"]) for c in (rows or [])}
    out, diff, unresolved, legacy = [], {}, {}, []
    for row in entries or []:
        row = dict(row)
        names = [str(x).strip() for x in (row.get("concept_names") or []) if str(x).strip()]
        old_ids = list(row.get("concepts") or [])
        if not names:
            if old_ids:
                stale = [i for i in old_ids if i not in {v for v in by_name.values()}]
                legacy.append({"id": row.get("id"), "concepts": old_ids,
                               "对不上的 id": stale})
            out.append(row)
            continue
        new_ids, missing = [], []
        for name in names:
            found = by_name.get(name)
            if found:
                new_ids.append(found)
            else:
                missing.append(name)
        if apply_ids:
            row["concepts"] = new_ids
            row["unresolved"] = missing
        if old_ids != new_ids or missing:
            diff[str(row.get("id"))] = {"旧 id": old_ids, "新 id": new_ids, "对不上的名字": missing}
        if missing:
            unresolved[str(row.get("id"))] = missing
        out.append(row)
    return {"figures": out, "diff": diff, "unresolved": unresolved, "legacy": legacy}


__all__ = ["write_index", "entry", "rehang", "GENERATOR", "SCHEMA_VERSION"]
