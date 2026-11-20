"""后端 `vector`：向量/几何图（matplotlib，仿射不变量可验）。

从 `library.py` **原样搬过来**（这一步是搬家，不是改写）：`draw()` / `draw_all()` /
`_to_current_color()` 的逻辑一个字没动，只是换了文件、补了 import。
"""
from __future__ import annotations

import json
from pathlib import Path

try:
    from ..spec import LIBRARY, by_name
except ImportError:                     # 顶层模块 import（sys.path 指向 blocks/figures 时）
    from spec import LIBRARY, by_name

def draw(spec: dict, out_dir: str | Path, *, show_png: bool = True) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Arc, FancyArrowPatch

    matplotlib.rcParams["svg.fonttype"] = "none"     # 标注留成 <text>，能被校验、能搜索
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = spec["name"]

    fig, ax = plt.subplots(figsize=(4.4, 3.1), dpi=160)
    ax.set_aspect("equal")
    ax.axis("off")

    def point(key):
        return spec["points"][key]

    for start, end, gid in spec["arrows"]:
        patch = FancyArrowPatch(point(start), point(end), arrowstyle="-|>",
                                mutation_scale=16, lw=2.0, color="black",
                                shrinkA=0, shrinkB=0)
        patch.set_gid(f"vec-{gid}")
        ax.add_patch(patch)
    for start, end in spec["helpers"]:
        line, = ax.plot([point(start)[0], point(end)[0]], [point(start)[1], point(end)[1]],
                        linestyle=(0, (5, 4)), lw=1.4, color="black")
        line.set_gid(f"helper-{start}{end}")
    for centre, radius, deg1, deg2 in spec.get("arcs", ()):
        arc = Arc(point(centre), 2 * radius, 2 * radius, theta1=deg1, theta2=deg2,
                  lw=1.4, color="black")
        arc.set_gid(f"arc-{centre}")
        ax.add_patch(arc)

    label_artists, label_gids = {}, {}
    for index, (text, (anchor, dx, dy)) in enumerate(spec["labels"].items()):
        x, y = point(anchor)
        artist = ax.text(x + dx, y + dy, text, fontsize=13)
        gid = f"label-{index}"
        artist.set_gid(gid)          # 数学标注会被渲成路径，靠 gid 证明它画出来了
        label_artists[text] = artist
        label_gids[text] = gid

    x0, x1, y0, y1 = spec["limits"]
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    fig.tight_layout(pad=0.3)
    # **必须先真的画一次**：tight_layout 会改坐标区大小，等比例（set_aspect）是在
    # 绘制时才真正应用的。不先 draw 就采样 transData，采到的是"还没等比"的那个变换
    # —— 实测因此算出 54.21° 而不是 57.5°（x/y 每单位像素差了 13%）。
    fig.canvas.draw()

    renderer = fig.canvas.get_renderer()
    canvas = [float(v) for v in fig.canvas.get_width_height()]
    label_boxes = {}
    for text, artist in label_artists.items():
        box = artist.get_window_extent(renderer)
        label_boxes[text] = [round(float(box.x0), 3), round(float(box.y0), 3),
                             round(float(box.x1), 3), round(float(box.y1), 3)]

    transform = ax.transData.transform
    display = {key: [float(v) for v in transform(point(key))] for key in spec["points"]}

    svg_path = out_dir / f"{name}.svg"
    fig.savefig(svg_path, format="svg", transparent=True)
    if show_png:
        fig.savefig(out_dir / f"{name}.png", format="png", transparent=True)
    plt.close(fig)

    svg_path.write_text(_to_current_color(svg_path.read_text(encoding="utf-8")),
                        encoding="utf-8")

    expected_ids = [f"vec-{gid}" for _, _, gid in spec["arrows"]]
    expected_ids += [f"helper-{a}{b}" for a, b in spec["helpers"]]
    expected_ids += [f"arc-{c}" for c, *_ in spec.get("arcs", ())]

    payload = {
        "_meta": {
            "file_type": "figure_geometry", "schema_version": "1.0",
            "generator": "aiforstudy.figures",
            "field_notes": {
                "points": "图形数据坐标（只写一次，图和校验都从这里取）",
                "display": "matplotlib 实际渲染用的显示坐标；仿射不变量在这里成立",
                "canvas": "画布像素尺寸 [宽, 高]",
                "label_boxes": "每个标注的实际外框；用来查「有没有画出画布」",
                "expected_ids": "这张图该有的图元 id",
                "label_gids": "每个标注的图元 id。**数学标注会被渲成路径**（不依赖字体，"
                              "任何浏览器都一样），所以靠 gid 而不是 <text> 来证明它画出来了；"
                              "普通文字标注则要求是 <text>（能搜索、能选中）",
                "closes[]": "首尾相接必须闭环的段：Σ(终点−起点) ≈ 0",
                "relations[]": "两对点之间的位移必须相等",
                "lengths[]": "两段长度的比值",
                "angles[]": "顶点处两段之间的夹角（度）",
                "directions[]": "两段方向之间的夹角（度）—— 不需要共用顶点，"
                                "用来查「方向相反」这类关系",
                "anchors": "每个标注挂在哪一点上（查「标注有没有挂错点」）",
            },
        },
        "name": name, "title": spec["title"], "concepts": spec["concepts"],
        "points": {k: list(v) for k, v in spec["points"].items()},
        "display": display, "canvas": canvas, "label_boxes": label_boxes,
        "labels": list(spec["labels"]), "label_gids": label_gids,
        "expected_ids": expected_ids,
        "closes": spec["closes"], "relations": spec["relations"],
        "lengths": spec["lengths"], "angles": spec["angles"],
        "directions": spec["directions"], "anchors": spec["anchors"],
    }
    (out_dir / f"{name}.geometry.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"name": name, "svg": str(svg_path), "png": str(out_dir / f"{name}.png"),
            "geometry": str(out_dir / f"{name}.geometry.json"), "display": display}


def draw_all(out_dir: str | Path) -> list[dict]:
    return [draw(spec, out_dir) for spec in LIBRARY]


def _to_current_color(svg_text: str) -> str:
    for token in ("#000000", "#000", "black", "rgb(0%,0%,0%)"):
        svg_text = svg_text.replace(token, "currentColor")
    return svg_text


__all__ = ["LIBRARY", "figure", "by_name", "draw", "draw_all"]
