"""**新图种**的清单（电路 / 曲线 / 3D）—— 与 `spec.LIBRARY`（向量图）分开放。

为什么分开：`tests/test_figures.py` 会对 `LIBRARY` 里**每张图**要求
`expected_ids/labels/anchors + 至少一条仿射断言`（那是向量图的验法）。
电路图没有"点对"，那些字段对它没意义 —— 与其编造，不如各立清单、各用各的断言。
`ALL_FIGURES = LIBRARY + EXTRA_LIBRARY` 供 CLI 与流水线统一取用。
"""
from __future__ import annotations

EXTRA_LIBRARY: list[dict] = []


def add(spec: dict) -> dict:
    EXTRA_LIBRARY.append(spec)
    return spec


# ① 电路：两个网孔（§2.1 的真实拓扑，`i_M1/i_M2` 为网孔电流）
add({
    "name": "mesh-2loop", "title": "两网孔电路（网孔电流）", "backend": "schemdraw",
    "concepts": [], "concept_names": ["网孔电流", "网孔", "网孔分析"],
    "elements": [
        {"op": "source_v", "orient": "up", "label": "$u_{S1}$", "loc": "left"},
        {"op": "resistor", "orient": "right", "label": "$R_1$", "loc": "top"},
        {"op": "resistor", "orient": "down", "label": "$R_3$", "loc": "right"},
        {"op": "line", "orient": "left"},
        {"op": "resistor", "orient": "right", "label": "$R_2$", "loc": "top"},
        {"op": "source_v", "orient": "down", "label": "$u_{S4}$", "loc": "right"},
        {"op": "line", "orient": "left"},
    ],
    "assertions": {"min_paths": 6, "min_bytes": 1200,
                   "require_texts": ["R_1", "R_2", "R_3", "u_S1", "u_S4"]},
})

# ② 曲线：欧姆定律的伏安特性（三条 R 的直线）
add({
    "name": "ui-ohmic", "title": "欧姆定律的伏安特性（$u=Ri$）", "backend": "plot",
    "concepts": [], "concept_names": ["欧姆定律", "线性电阻元件", "伏安特性曲线"],
    "x": {"label": "i", "unit": "A", "range": [-2, 2], "samples": 240},
    "y": {"label": "u", "unit": "V"},
    "curves": [
        {"expr": "R*i", "params": {"R": 1.0}, "label": "R = 1 Ω"},
        {"expr": "R*i", "params": {"R": 2.0}, "label": "R = 2 Ω"},
        {"expr": "R*i", "params": {"R": 0.5}, "label": "R = 0.5 Ω"},
    ],
    # 数值断言：这些点上"独立算一遍"必须等于"真正画出去的数据"
    "key_points": [{"x": 1.0, "curve": 0}, {"x": -1.5, "curve": 1}, {"x": 2.0, "curve": 2}],
    "assertions": {"min_paths": 1, "min_bytes": 1500},
})

# ③ TeX 级电路：同一拓扑，用 CircuiTikZ 画（印刷级样式）
add({
    "name": "mesh-2loop-tex", "title": "两网孔电路（CircuiTikZ 印刷版）", "backend": "circuitikz",
    "concepts": [], "concept_names": ["网孔电流", "网孔分析", "网孔方程"],
    "tikz": r"""\draw (0,0) to[V,l=$u_{S1}$] (0,3)
        to[R,l=$R_1$] (3,3)
        to[R,l=$R_3$] (3,0) -- (0,0);
      \draw (3,3) to[R,l=$R_5$] (6,3)
        to[V,l_=$u_{S4}$] (6,0) -- (3,0);
      \draw[->,>=latex,orange] (1.5,1.5) arc (150:-150:1.1);
      \node[orange] at (1.45,0.85) {$i_{M1}$};
      \draw[->,>=latex,orange] (4.5,1.5) arc (30:-30:-1.1);
      \node[orange] at (4.55,0.85) {$i_{M2}$};""",
    "assertions": {"min_paths": 8, "min_bytes": 1500, "canvas_min_pt": 60.0,
                   "labels": ["u_{S1}", "R_1", "R_3", "R_5", "u_{S4}", "i_{M1}", "i_{M2}"]},
})

# ④ 3D：两个网孔的三维示意（Blender 无头导出 glTF + 等轴测贴片）
add({
    "name": "mesh-3d", "title": "两个网孔的三维示意（glTF）", "backend": "three",
    "concepts": [], "concept_names": ["网孔电流", "网孔分析"],
    "objects": [
        {"name": "mesh-1", "kind": "plane", "at": [-1.1, 0, 0], "size": [2.0, 2.0, 0.04]},
        {"name": "mesh-2", "kind": "plane", "at": [1.1, 0, 0], "size": [2.0, 2.0, 0.04]},
        {"name": "i_M1", "kind": "cylinder", "at": [-1.1, 0, 0.35], "size": [1.2, 1.2, 0.1]},
        {"name": "i_M2", "kind": "cylinder", "at": [1.1, 0, 0.35], "size": [1.2, 1.2, 0.1]},
    ],
    "camera": {"location": [6.5, -6.5, 5.5], "look_at": [0, 0, 0]},
    "assertions": {"min_paths": 4},
})
