"""图**规格**（画什么）：声明式 dict + 图库清单。与"怎么画"解耦，渲染在 `backends/`。

为什么规格里要写 `concept_names`（概念**名**，不是 id）：id 是某一次概念抽取的号，
重抽之后号会**被复用** —— 实测 `C-0002` 从"相等向量"变成"同解方程组"，于是第二章的
概念抽屉里配上了第一章的向量图（P-020/P-032）。所以挂图一律按名字。

每张图还要带**它自己的数学断言**（`relations/lengths/angles/directions`），由 `verify_svg`
在显示坐标下验（matplotlib 的 `set_aspect("equal")` 保证是仿射不变量）。
"""
from __future__ import annotations

import json
from pathlib import Path

# ── 图库：新增图只需要在这里加一条 ──────────────────────────────────────
LIBRARY: list[dict] = []
DEFAULT_LIMITS = (-1.0, 4.6, -1.2, 2.8)


def figure(name: str, title: str, concepts: list[str], *, concept_names: list[str],
           backend: str = "vector",
           limits=DEFAULT_LIMITS,
           points: dict, arrows: list, helpers: list = (), labels: dict,
           anchors: dict, arcs: list = (), closes: list = (), relations: list = (),
           lengths: dict = (), angles: list = (), directions: list = ()) -> dict:
    """一张声明式的图。

    `concept_names` 是这张图服务哪些概念，**按名字声明**（稳定键）；`concepts` 是当初
    登记时的 id（历史快照，会被 `registry.rehang()` 按名字重算）。为什么必须按名字：
    id 是某一次概念抽取的号，重抽之后号会被重用 —— 实测 `C-0002` 从"相等向量"变成
    "同解方程组"，于是第二章的概念抽屉里配上了第一章的向量图（P-020/P-032）。
    """
    # `backend` 决定用哪个渲染后端画（vector=matplotlib 向量图；见 backends/）。
    # 新增后端只需在 backends/ 里加一个模块并在这里登记名字 —— 规格与渲染解耦。
    spec = {"name": name, "title": title, "concepts": concepts, "backend": backend,
            "concept_names": list(concept_names), "limits": list(limits),
            "points": points, "arrows": arrows, "helpers": list(helpers),
            "labels": labels, "anchors": anchors, "arcs": list(arcs),
            "closes": [list(c) for c in closes],
            "relations": [[list(a), list(b)] for a, b in relations],
            "lengths": [[list(a), list(b), ratio] for a, b, ratio in lengths],
            "angles": [list(a) for a in angles],
            "directions": [[list(a), list(b), deg] for a, b, deg in directions]}
    LIBRARY.append(spec)
    return spec


def by_name(name: str) -> dict:
    for spec in LIBRARY:
        if spec["name"] == name:
            return spec
    raise KeyError(f"图库里没有这张图：{name}（可用的：{[s['name'] for s in LIBRARY]}）")


# ── ① 向量的表示（有向线段）：评委点名最多的一张 ────────────────────────
figure(
    "vector-segment", "向量的表示（有向线段）", ["C-0001"],
    concept_names=["向量"],
    # 除了画出 O→A，再画一条**平移过去的同一个向量**：这正是"有向线段只是表示、
    # 向量本身与位置无关"的直观说明，也让这张图有了真正的数学断言（位移相等）。
    points={"O": (0.0, 0.0), "A": (2.6, 1.1),
            "O2": (0.6, -1.3), "A2": (3.2, -0.2)},
    arrows=[("O", "A", "a"), ("O2", "A2", "a2")],
    labels={r"$\boldsymbol{a}$": ("A", -0.10, 0.20), "O": ("O", -0.34, -0.30),
            "A": ("A", 0.12, 0.16), "O'": ("O2", -0.46, -0.30),
            "A'": ("A2", 0.12, -0.34)},
    anchors={r"$\boldsymbol{a}$": "A", "O": "O", "A": "A", "O'": "O2", "A'": "A2"},
    relations=[(("A", "O"), ("A2", "O2"))],
    limits=(-0.9, 4.1, -1.9, 1.9),
)

# ── ② 向量夹角：一眼就需要图的概念 ──────────────────────────────────────
figure(
    "vector-angle", "向量的夹角", ["C-0009"],
    concept_names=["向量夹角"],
    points={"O": (0.0, 0.0), "A": (3.0, 0.0), "B": (1.4, 2.2)},
    arrows=[("O", "A", "a"), ("O", "B", "b")],
    arcs=[("O", 0.9, 0.0, 57.5)],
    labels={r"$\boldsymbol{a}$": ("A", -0.36, -0.34), r"$\boldsymbol{b}$": ("B", 0.10, 0.16),
            "O": ("O", -0.32, -0.34), r"$\theta$": ("O", 0.72, 0.22)},
    anchors={r"$\boldsymbol{a}$": "A", r"$\boldsymbol{b}$": "B", "O": "O",
             r"$\theta$": "O"},
    angles=[("O", "A", "B", 57.5)],
    limits=(-0.8, 4.0, -0.9, 2.9),
)

# ── ③ 数乘：拉伸 / 压缩 / 掉头 ──────────────────────────────────────────
figure(
    "vector-scalar", "数乘的几何意义", ["C-0013"],
    concept_names=["向量的数乘"],
    # 四行分开画：它们本来就共线，全从同一点出发会叠在一起看不清（第一版就是那样）。
    # 对齐起点、分行排列，长度差别一眼就能比出来。
    points={"O": (0.0, 0.0), "A": (2.0, 0.0),
            "O2": (-2.4, 1.0), "B": (-1.4, 1.0),      # 0.5a：长度 1.0
            "O3": (-2.4, 2.0), "C": (0.8, 2.0),       # 1.6a：长度 3.2
            "D": (-2.4, -1.0), "E": (-4.4, -1.0)},    # −a：长度 2.0，方向相反
    arrows=[("O", "A", "a"), ("O2", "B", "half"), ("O3", "C", "big"), ("D", "E", "neg")],
    labels={r"$\boldsymbol{a}$": ("A", 0.14, -0.06),
            r"$0.5\boldsymbol{a}$": ("B", 0.14, -0.06),
            r"$1.6\boldsymbol{a}$": ("C", 0.14, -0.06),
            r"$-\boldsymbol{a}$": ("E", -1.02, -0.06)},
    anchors={r"$\boldsymbol{a}$": "A", r"$0.5\boldsymbol{a}$": "B",
             r"$1.6\boldsymbol{a}$": "C", r"$-\boldsymbol{a}$": "E"},
    lengths=[(("O2", "B"), ("O", "A"), 0.5), (("O3", "C"), ("O", "A"), 1.6),
             (("D", "E"), ("O", "A"), 1.0)],
    directions=[(("O", "A"), ("O2", "B"), 0.0), (("O", "A"), ("D", "E"), 180.0)],
    limits=(-5.2, 3.4, -1.7, 2.7),
)

# ── ④ 向量减法：从 b 的终点指向 a 的终点 ────────────────────────────────
figure(
    "vector-subtraction", "向量减法 $\\boldsymbol{a}-\\boldsymbol{b}$", ["C-0012"],
    concept_names=["向量减法"],
    points={"O": (0.0, 0.0), "A": (3.0, 0.6), "B": (1.0, 1.9)},
    arrows=[("O", "A", "a"), ("O", "B", "b"), ("B", "A", "diff")],
    helpers=[("A", "B")],
    labels={r"$\boldsymbol{a}$": ("A", -0.30, -0.36), r"$\boldsymbol{b}$": ("B", -0.34, 0.18),
            r"$\boldsymbol{a}-\boldsymbol{b}$": ("B", 0.35, 1.05),
            "O": ("O", -0.34, -0.32)},
    anchors={r"$\boldsymbol{a}$": "A", r"$\boldsymbol{b}$": "B",
             r"$\boldsymbol{a}-\boldsymbol{b}$": "B", "O": "O"},
    closes=[[("B", "A"), ("A", "O"), ("O", "B")]],
    limits=(-0.9, 4.0, -0.9, 2.7),
)

# ── ⑤ 三向量共面：对应原文"图1.2 三向量共面条件" ────────────────────────
figure(
    "three-coplanar", "三向量共面条件", ["C-0135", "C-0136"],
    concept_names=["三向量共面的充要条件", "线性相关"],
    points={"O": (0.0, 0.0), "A": (2.4, 0.35), "B": (1.0, 1.9),
            "C": (3.4, 2.25)},
    arrows=[("O", "A", "a"), ("O", "B", "b"), ("O", "C", "c")],
    helpers=[("A", "C"), ("B", "C")],
    labels={r"$\boldsymbol{a}$": ("A", 0.02, -0.42), r"$\boldsymbol{b}$": ("B", -0.36, 0.16),
            r"$\boldsymbol{c}$": ("C", 0.14, -0.40), "O": ("O", -0.34, -0.32),
            "A": ("A", 0.10, 0.14), "B": ("B", 0.10, 0.14), "C": ("C", 0.14, 0.12)},
    anchors={r"$\boldsymbol{a}$": "A", r"$\boldsymbol{b}$": "B", r"$\boldsymbol{c}$": "C",
             "O": "O", "A": "A", "B": "B", "C": "C"},
    closes=[[("O", "A"), ("A", "C"), ("C", "B"), ("B", "O")]],
    limits=(-0.9, 4.4, -1.0, 3.0),
)


# ── ⑥ 相等向量：同长同向才算相等（顺便给出"不等"的两个反例） ────────────
figure(
    "equal-vectors", "相等向量", ["C-0002"],
    concept_names=["相等向量"],
    # 四个角分开摆：a₄ 原来朝上会撞到 a₃ 的线段（看出来的），改成朝下
    points={"O1": (-3.2, 1.1), "A1": (-1.2, 1.9),      # a₁
            "O2": (-3.2, -0.3), "A2": (-1.2, 0.5),     # a₂ 与 a₁ 同长同向 → 相等
            "O3": (0.8, 1.2), "A3": (3.6, 2.32),       # a₃ 方向同、但更长 → 不相等
            "O4": (0.8, -0.8), "A4": (2.846, -1.473)}, # a₄ 同长、但转了 40° → 不相等
    arrows=[("O1", "A1", "a1"), ("O2", "A2", "a2"), ("O3", "A3", "a3"),
            ("O4", "A4", "a4")],
    labels={r"$\boldsymbol{a}_1$": ("A1", -0.15, 0.18), r"$\boldsymbol{a}_2$": ("A2", -0.15, -0.42),
            r"$\boldsymbol{a}_3$": ("A3", 0.12, -0.10), r"$\boldsymbol{a}_4$": ("A4", 0.12, -0.12)},
    anchors={r"$\boldsymbol{a}_1$": "A1", r"$\boldsymbol{a}_2$": "A2",
             r"$\boldsymbol{a}_3$": "A3", r"$\boldsymbol{a}_4$": "A4"},
    relations=[(("A1", "O1"), ("A2", "O2"))],              # 这两条相等
    lengths=[(("A3", "O3"), ("A1", "O1"), 1.4)],           # 这条更长 → 故意不相等
    directions=[(("O1", "A1"), ("O4", "A4"), 40.0)],       # 这条转了 40° → 故意不相等
    limits=(-4.0, 4.3, -2.2, 2.7),
)

# ── ⑦ 反向量：同长反向，加起来是零向量 ──────────────────────────────────
figure(
    "opposite-vectors", "反向量（负向量）", ["C-0003"],
    concept_names=["反向量"],
    points={"O": (0.0, 0.0), "A": (2.4, 1.0), "B": (-2.4, -1.0)},
    arrows=[("O", "A", "a"), ("O", "B", "neg")],
    labels={r"$\boldsymbol{a}$": ("A", 0.14, -0.12),
            r"$-\boldsymbol{a}$": ("B", -1.26, -0.12), "O": ("O", -0.10, -0.42)},
    anchors={r"$\boldsymbol{a}$": "A", r"$-\boldsymbol{a}$": "B", "O": "O"},
    lengths=[(("O", "B"), ("O", "A"), 1.0)],
    directions=[(("O", "A"), ("O", "B"), 180.0)],
    closes=[[("O", "A"), ("O", "B")]],                     # a + (−a) = 0
    limits=(-3.6, 3.6, -1.9, 1.9),
)

# ── ⑧ 平行（共线）：同向或反向都在同一条直线上 ──────────────────────────
figure(
    "parallel-collinear", "平行（共线）", ["C-0007", "C-0134"],
    concept_names=["平行", "两向量共线的充要条件"],
    points={"O": (0.0, 0.0), "A": (2.4, 0.0), "B": (3.6, 0.0), "C": (-1.6, 0.0),
            "D": (1.4, 2.0)},
    arrows=[("O", "A", "a"), ("O", "B", "b"), ("O", "C", "c"), ("O", "D", "d")],
    labels={r"$\boldsymbol{a}$": ("A", -0.18, 0.14), r"$\boldsymbol{b}$": ("B", -0.18, 0.14),
            r"$\boldsymbol{c}$": ("C", -0.18, -0.46), r"$\boldsymbol{d}$": ("D", 0.14, 0.10),
            "O": ("O", -0.12, -0.46)},
    anchors={r"$\boldsymbol{a}$": "A", r"$\boldsymbol{b}$": "B",
             r"$\boldsymbol{c}$": "C", r"$\boldsymbol{d}$": "D", "O": "O"},
    lengths=[(("O", "B"), ("O", "A"), 1.5), (("O", "C"), ("O", "A"), 2 / 3)],
    directions=[(("O", "A"), ("O", "B"), 0.0), (("O", "A"), ("O", "C"), 180.0),
                (("O", "A"), ("O", "D"), 55.01)],
    limits=(-2.4, 4.3, -1.2, 2.6),
)

# ── ⑨ 垂直（正交）：夹角 90° ────────────────────────────────────────────
figure(
    "perpendicular", "垂直（正交）", ["C-0008"],
    concept_names=["垂直"],
    points={"O": (0.0, 0.0), "A": (2.6, 0.0), "B": (0.0, 1.9)},
    arrows=[("O", "A", "a"), ("O", "B", "b")],
    arcs=[("O", 0.75, 0.0, 90.0)],
    labels={r"$\boldsymbol{a}$": ("A", -0.24, -0.42), r"$\boldsymbol{b}$": ("B", -0.20, 0.14),
            "O": ("O", -0.34, -0.34), r"$90^\circ$": ("O", 0.30, 0.36)},
    anchors={r"$\boldsymbol{a}$": "A", r"$\boldsymbol{b}$": "B", "O": "O",
             r"$90^\circ$": "O"},
    angles=[("O", "A", "B", 90.0)],
    limits=(-0.9, 3.4, -0.9, 2.4),
)

# ── ⑩ 线性组合：缩放之后首尾相接 ────────────────────────────────────────
figure(
    "linear-combination", r"线性组合 $1.5\boldsymbol{a}_1+0.8\boldsymbol{a}_2$", ["C-0017", "C-0133"],
    concept_names=["线性组合", "线性运算"],
    points={"O": (0.0, 0.0), "P": (3.3, 0.525), "C": (3.78, 1.965),
            "Q": (0.48, 1.44), "M": (3.54, 1.245),
            "N": (1.89, 0.98)},        # 对角线中点：给 c 的标注用（原来放尖端会和 0.8a₂ 叠）
    arrows=[("O", "P", "s1"), ("P", "C", "s2"), ("O", "C", "sum")],
    helpers=[("O", "Q"), ("Q", "C")],
    labels={r"$1.5\boldsymbol{a}_1$": ("P", -0.62, -0.42), r"$0.8\boldsymbol{a}_2$": ("M", 0.12, 0.06),
            r"$\boldsymbol{c}$": ("N", -0.34, -0.46), "O": ("O", -0.34, -0.32)},
    anchors={r"$1.5\boldsymbol{a}_1$": "P", r"$0.8\boldsymbol{a}_2$": "M",
             r"$\boldsymbol{c}$": "N", "O": "O"},
    closes=[[("O", "P"), ("P", "C"), ("C", "O")]],
    limits=(-0.9, 4.7, -1.1, 2.8),
)

# ── ⑪ 三角形法则：首尾相接 ──────────────────────────────────────────────
figure(
    "triangle-rule", "三角形法则", ["C-0139"],
    concept_names=["三角形法则"],
    points={"O": (0.0, 0.0), "A": (2.6, 0.2), "C": (3.5, 2.1),
            "M": (3.05, 1.15), "N": (1.75, 1.05)},   # N：对角线中点，给 a+b 的标注用
    arrows=[("O", "A", "a"), ("A", "C", "b"), ("O", "C", "sum")],
    labels={r"$\boldsymbol{a}$": ("A", -0.30, -0.40), r"$\boldsymbol{b}$": ("M", 0.12, 0.06),
            r"$\boldsymbol{a}+\boldsymbol{b}$": ("N", -0.30, -0.46), "O": ("O", -0.34, -0.32)},
    anchors={r"$\boldsymbol{a}$": "A", r"$\boldsymbol{b}$": "M",
             r"$\boldsymbol{a}+\boldsymbol{b}$": "N", "O": "O"},
    closes=[[("O", "A"), ("A", "C"), ("C", "O")]],
    limits=(-0.9, 4.4, -0.9, 2.9),
)


# ── 通用绘制 ────────────────────────────────────────────────────────────


# ── 全部图（向量 + 新图种）────────────────────────────────────────────
# 为什么分两个清单：向量图的验法是"仿射不变量"（要 expected_ids/labels/anchors），
# 电路/曲线图没有点对可比，只能验"图元齐不齐、标注在不在、数值对不对" —— 见 backends/。
try:                                    # 包内 import
    from .spec_extra import EXTRA_LIBRARY                    # noqa: E402
except ImportError:                     # 被当顶层模块 import（cli.py / tests 的用法）
    from spec_extra import EXTRA_LIBRARY                     # noqa: E402

ALL_FIGURES: list[dict] = LIBRARY + EXTRA_LIBRARY


def any_by_name(name: str) -> dict:
    for spec in ALL_FIGURES:
        if spec["name"] == name:
            return spec
    raise KeyError(f"没有这张图：{name}（可用：{[s['name'] for s in ALL_FIGURES]}）")
