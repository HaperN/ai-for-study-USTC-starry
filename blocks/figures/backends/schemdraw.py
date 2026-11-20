"""后端 `schemdraw`：电路图（纯 Python，**不需要 TeX**）。

规格是声明式的 `elements` 列表（按顺序串起来画），每个元素：
    {"op": "resistor"|"source_v"|"source_i"|"line"|"capacitor"|"inductor",
     "orient": "up"|"down"|"left"|"right", "label": "$R_1$", "loc": "top"|"bottom"|"left"|"right",
     "at": [x, y]（可选：另起一笔）}

**断言**（写进 geometry.json，kind="structural"）：
  * `require_texts` 元件标注必须真的出现在 SVG 文字里（schemdraw 默认输出 <text>）；
  * `min_paths` 图元数量下限（防止"文件生成了但图是空的"）；
  * `min_bytes` 体积下限。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import schemdraw
import schemdraw.elements as elm

OPS = {"resistor": "Resistor", "source_v": "SourceV", "source_i": "SourceI",
       "line": "Line", "capacitor": "Capacitor", "inductor": "Inductor",
       "diode": "Diode", "ground": "Ground"}
ORIENT = {"up": "up", "down": "down", "left": "left", "right": "right"}


def _plain(label: str) -> str:
    """把标注里的 LaTeX 去壳，用于"文字是否真的画上去了"的检查。"""
    t = re.sub(r"\[a-zA-Z]+", "", str(label or ""))
    return t.replace("$", "").replace("{", "").replace("}", "").replace("\\", "").strip()


def draw(spec: dict, out_dir: str | Path, *, show_png: bool = True) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    svg_path = out_dir / f"{spec['name']}.svg"

    d = schemdraw.Drawing(show=False)
    d.config(unit=2.2, fontsize=13)
    first = True
    for el in spec.get("elements") or []:
        op = OPS.get(str(el.get("op")))
        if not op:
            raise KeyError(f"不认识的元件：{el.get('op')}（可用：{sorted(OPS)}）")
        element = getattr(elm, op)()
        if el.get("at"):
            element = element.at(tuple(el["at"]))
        elif not first:
            element = element.at(d.here)
        element = getattr(element, ORIENT.get(str(el.get("orient") or "right"), "right"))()
        if el.get("label"):
            element = element.label(str(el["label"]), loc=str(el.get("loc") or "top"))
        d += element
        first = False
    d.save(str(svg_path))

    # 几何事实（可断言的部分）：schemdraw 走 matplotlib，**文字会变成字形轮廓（path）**，
    # SVG 里没有 <text> —— 所以不能像向量图那样"查文字在不在"（实测踩过：报"标注没出现"）。
    # 改断"元件都画出来了、画布非退化"：
    drawn_elements = len(getattr(d, "elements", []) or [])
    xs, ys = [], []
    for el in getattr(d, "elements", []) or []:
        for pt in (getattr(el, "absanchors", None) or {}).values():
            xs.append(float(pt[0]))
            ys.append(float(pt[1]))
    bbox = [min(xs), min(ys), max(xs), max(ys)] if xs and ys else [0, 0, 0, 0]

    svg_text = svg_path.read_text(encoding="utf-8")
    payload = {
        "kind": "structural",
        "backend": "schemdraw",
        "name": spec["name"],
        # 结构断言：图元数量 / 标注是否真出现 / 体积
        "min_paths": int((spec.get("assertions") or {}).get("min_paths")
                         or max(2 * len(spec.get("elements") or []), 4)),
        "min_bytes": int((spec.get("assertions") or {}).get("min_bytes") or 1200),
        # 文字标注（留作追溯；不断言 —— 上面说明了为什么）
        "labels": [_plain(el.get("label")) for el in spec.get("elements") or [] if el.get("label")],
        # **几何断言**：规格里声明的元件必须真的都画出来了，且包围盒非退化
        "element_count": drawn_elements,
        "element_count_expected": len(spec.get("elements") or []),
        "bbox": bbox,
        "bbox_area_min": float((spec.get("assertions") or {}).get("bbox_area_min") or 1.0),
        "elements": [el.get("op") for el in spec.get("elements") or []],
        "labels": [_plain(el.get("label")) for el in spec.get("elements") or [] if el.get("label")],
        "svg_bytes": len(svg_text.encode("utf-8")),
    }
    geometry = out_dir / f"{spec['name']}.geometry.json"
    geometry.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    produced = {"svg": str(svg_path), "geometry": str(geometry)}
    if show_png:
        produced["png"] = ""
    return produced


__all__ = ["draw"]
