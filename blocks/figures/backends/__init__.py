"""图的渲染后端：**一处入口 + 多后端**（一个后端 = 一类图怎么画 + 自带什么断言）。

为什么要有这层：图的种类差别太大 —— 向量图能用仿射不变量验（matplotlib），
电路图只能验"该有的元件与标注在不在"（schemdraw/TeX），曲线图要验"画出来的数据
是否真的满足公式"（matplotlib 采样）。把这些硬塞进一个 `draw()` 只会越写越糊。

对外只有一个函数 `render(spec, out_dir)`，按 `spec["backend"]` 选后端；
每个后端模块都必须提供 `draw(spec, out_dir) -> {"svg":…, "geometry":…, …}`。
"""
from __future__ import annotations

import importlib
from pathlib import Path

#: backend 名 → 模块名（都在本包里）。加新后端只需在这里加一行 + 写一个模块。
BACKENDS = {
    "vector": "vector",            # 向量/几何（matplotlib + 仿射不变量）
    "schemdraw": "schemdraw",      # 电路（纯 Python，免 TeX）
    "circuitikz": "circuitikz",    # 电路（TeX：pdflatex → pdftocairo -svg）
    "plot": "plot",                # 函数曲线（matplotlib 采样 + 数值断言）
    "three": "three",              # 3D / glTF（three.js·MathBox 运行时）
}


def backend_of(spec: dict) -> str:
    name = str(spec.get("backend") or "vector")
    if name not in BACKENDS:
        raise KeyError(f"没有这个渲染后端：{name}（可用：{sorted(BACKENDS)}）")
    return name


def load(name: str):
    """按名字取后端模块（懒加载：没装 schemdraw 也不该影响向量图）。"""
    return importlib.import_module(f".{BACKENDS[name]}", __name__)


def render(spec: dict, out_dir: str | Path, **kwargs) -> dict:
    return load(backend_of(spec)).draw(spec, out_dir, **kwargs)


__all__ = ["BACKENDS", "backend_of", "load", "render"]
