"""后端 `plot`：函数曲线（matplotlib 采样 + **数值断言**）。

规格：
    {"x": {"label": "i", "unit": "A", "range": [-2, 2], "samples": 240},
     "y": {"label": "u", "unit": "V"},
     "curves": [{"expr": "R*i", "params": {"R": 1.0}, "label": "R = 1 Ω"}],
     "key_points": [{"x": 1.0, "curve": 0}]}

**为什么曲线要"数值断言"**：曲线图的错法是"公式对、画出来不对"（采样范围错、参数没代进去）。
所以对每个 `key_points`：用同一个 `expr` **独立算一遍**期望值，再和真正交给 matplotlib
的那个数组里的值比 —— 两边一致才算过（写进 geometry.json 的 `numeric_checks`）。
另外还有结构断言：路径数量、坐标轴标注文字必须在。
"""
from __future__ import annotations

import json
import re
from pathlib import Path


def _safe_eval(expr: str, x, params: dict):
    """求值曲线表达式 —— 走**AST 白名单**，不再用 `eval`。

    ⚠️ 2026-10-05（M5）实测评析：这里原来是

        eval(expr, {"__builtins__": {}}, allowed)

    旁边的注释写着"我们自己的规格文件，不是用户输入"。**那条假设已经站不住了**：
    方案 §8 明确允许"新增规格生成可以使用模型"。规格一旦来自模型，`expr` 就是模型输出，
    而清空 `__builtins__` **不是沙箱** —— `().__class__.__bases__[0].__subclasses__()`
    这类属性链逃逸照样能用，拿到任意类之后就是任意代码执行。

    现在改为把表达式解析成 AST、逐节点按白名单放行：
    只认算术运算、只认显式列出的函数名、**整类禁掉属性访问与下标**。
    与旧写法的兼容差异只有一处：规格里不再写 `np.sin(x)`，要写 `sin(x)`
    （属性访问被禁掉了；现有图库用的是 `R*i` 这类算术式，不受影响）。
    """
    from aiforstudy.blocks.figures.safe_eval import evaluate, numpy_functions

    variables = {str(k): float(v) for k, v in (params or {}).items()}
    variables["x"] = x
    variables["i"] = x
    return evaluate(expr, variables, numpy_functions())


def draw(spec: dict, out_dir: str | Path, *, show_png: bool = True) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    matplotlib.rcParams["svg.fonttype"] = "none"      # 文字留成 <text>，可搜索、可断言

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    svg_path = out_dir / f"{spec['name']}.svg"

    xr = (spec.get("x") or {}).get("range") or [-1, 1]
    samples = int((spec.get("x") or {}).get("samples") or 240)
    x = np.linspace(float(xr[0]), float(xr[1]), samples)

    fig, ax = plt.subplots(figsize=(4.6, 3.1), dpi=140)
    curves = []
    for curve in spec.get("curves") or []:
        y = _safe_eval(str(curve["expr"]), x, curve.get("params") or {})
        y = np.asarray(y, dtype=float)
        ax.plot(x, y, lw=1.9, label=str(curve.get("label") or curve["expr"]))
        curves.append({"expr": str(curve["expr"]), "params": dict(curve.get("params") or {}),
                       "x": x.tolist(), "y": y.tolist()})
    ax.axhline(0, color="#9aa1ab", lw=.8)
    ax.axvline(0, color="#9aa1ab", lw=.8)
    ax.set_xlabel("%s / %s" % ((spec.get("x") or {}).get("label") or "x",
                               (spec.get("x") or {}).get("unit") or ""))
    ax.set_ylabel("%s / %s" % ((spec.get("y") or {}).get("label") or "y",
                               (spec.get("y") or {}).get("unit") or ""))
    if any(c.get("label") for c in spec.get("curves") or []):
        ax.legend(frameon=False, fontsize=9)
    ax.grid(alpha=.18, lw=.6)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    fig.tight_layout()
    fig.savefig(svg_path, format="svg")
    plt.close(fig)

    # ── 数值断言：关键点上"独立算一遍" vs "真正画的那个数组" ──
    checks, problems = [], []
    for kp in spec.get("key_points") or []:
        idx = int(kp.get("curve") or 0)
        if idx >= len(curves):
            problems.append(f"key_points 指向了第 {idx} 条曲线，但只画了 {len(curves)} 条")
            continue
        curve = curves[idx]
        xs = np.asarray(curve["x"], dtype=float)
        ys = np.asarray(curve["y"], dtype=float)
        xv = float(kp["x"])
        # ⚠️ 关键点**多半不落在采样点上**（240 点均分 [-2,2] 时 x=-1.5 就落在两点之间）。
        # 拿"最近采样点"当期望值比会差一个采样步长（实测差 0.004 被判失败）——
        # 正确做法是**在当前折线上插值**：线性曲线插值精确，非线性曲线误差是 O(h²)，
        # 所以容差按量级给（1e-6·|y|），既抓得住"公式没代进去"，也不会被离散化误伤。
        expected = float(_safe_eval(curve["expr"], np.asarray([xv]), curve["params"])[0])
        drawn = float(np.interp(xv, xs, ys))
        tol = 1e-6 * max(1.0, abs(expected))
        ok = abs(expected - drawn) <= tol
        checks.append({"x": xv, "y_expected": expected, "y_drawn": drawn,
                       "tolerance": tol, "ok": bool(ok)})
        if not ok:
            problems.append(f"曲线 {idx} 在 x={xv} 处：期望 {expected}，实际画出 {drawn}")

    svg_text = svg_path.read_text(encoding="utf-8")
    payload = {
        "kind": "structural",              # 结构断言（路径/文字）在 verify_svg 里做
        "numeric": True,                   # 并且带上数值断言结果
        "backend": "plot",
        "name": spec["name"],
        "min_paths": int((spec.get("assertions") or {}).get("min_paths") or 1),
        "min_bytes": int((spec.get("assertions") or {}).get("min_bytes") or 1200),
        "require_texts": [str(c.get("label")) for c in spec.get("curves") or [] if c.get("label")]
                          + [f"{(spec.get('x') or {}).get('label') or 'x'} / "
                             f"{(spec.get('x') or {}).get('unit') or ''}"],
        "numeric_checks": checks,
        "numeric_problems": problems,
        "curves": [{"expr": c["expr"], "params": c["params"]} for c in curves],
        "svg_bytes": len(svg_text.encode("utf-8")),
    }
    geometry = out_dir / f"{spec['name']}.geometry.json"
    geometry.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    produced = {"svg": str(svg_path), "geometry": str(geometry)}
    if show_png:
        produced["png"] = ""
    return produced


__all__ = ["draw"]
