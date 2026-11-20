"""画「向量加法的平行四边形法则」。

为什么用代码画而不是让 AI 生成图片：外部调研查到的硬证据说得很清楚 ——
文生图只会"长得像"数学图。XSCT Bench 实测数学坐标系函数图，通义 qwen-image-max
61.8 分、万相 wan2.7-image 56.0 分，评审记录到"刻度正负缺失、x/y 轴标签互换、
直线画成曲线、两套坐标系叠加"；StructBench（ICLR 2026, arXiv:2510.05091）评测 15 个
模型后判定"即使头部闭源系统也远不令人满意"，而那篇论文自己就是**用可执行绘图程序**
合成百万图文对来训练的。

所以这里走"**代码画 → 渲成 SVG → 用断言校验**"：
  * 画出来的图是矢量，能放大、能改色（描边写成 currentColor，跟着页面深浅色走）；
  * 几何关系由 `verify_svg.py` 用坐标断言检查，**不靠眼睛看**。

产物：
  <out>/vector-parallelogram.svg            矢量图（交付用）
  <out>/vector-parallelogram.png            位图（只为人工目视一次）
  <out>/vector-parallelogram.geometry.json  画图用的坐标（校验时读它）
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# 登记用：图题、服务的概念（讲解里 [[FIGURE:FIG-parallelogram]] 会引用它）
TITLE = "向量加法的平行四边形法则"
# 概念**按名字声明**（稳定键）：`CONCEPTS` 只是当年的 id 快照，会被 registry.rehang() 重算。
# 老模块一开始只有 id —— 这正是 P-020/P-032 的病根：id 在概念表重抽后被重用，图就配错了概念。
CONCEPT_NAMES = ["向量加法"]
CONCEPTS = ["C-0011"]          # 向量加法（历史快照，别当唯一依据）

# 图形数据（**只写一次**，图和校验都从这里取）
O = (0.0, 0.0)
A = (3.0, 0.0)          # 向量 a 的端点
B = (1.0, 2.0)          # 向量 b 的端点
C = (A[0] + B[0], A[1] + B[1])       # a+b 的端点：平行四边形的对角线

LABELS = {"a": A, "b": B, "a+b": C}


def geometry() -> dict:
    return {"O": list(O), "A": list(A), "B": list(B), "C": list(C),
            "labels": {k: list(v) for k, v in LABELS.items()}}


def draw(out_dir: str | Path, *, show_png: bool = True) -> dict:
    """画出来并落盘。返回产物路径与坐标（含 display 坐标，供校验用）。"""
    import matplotlib
    matplotlib.use("Agg")            # 无界面环境必须用 Agg
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyArrowPatch
    # 标注保留成 <text>（默认会转成路径）：这样文字能搜索、能选中，也能被校验器查到
    matplotlib.rcParams["svg.fonttype"] = "none"

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(4.2, 3.0), dpi=160)
    ax.set_aspect("equal")
    ax.axis("off")

    def arrow(start, end, gid):
        """画一支带箭头的向量，并给它一个 id（校验时靠 id 找它）。

        实测踩过：`ax.annotate(..., arrowprops=...)` 的 gid **不会**落成 SVG 的 id
        （输出的是 matplotlib 自己生成的 p9d6356621f 这种），校验器因此找不到向量。
        换成 FancyArrowPatch 就正常了。
        """
        patch = FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=16,
                                lw=2.0, color="black", shrinkA=0, shrinkB=0)
        patch.set_gid(f"vec-{gid}")
        ax.add_patch(patch)
        return patch

    arrow(O, A, "a")
    arrow(O, B, "b")
    # a+b 用对角线表示
    arrow(O, C, "ab")

    # 平行四边形另外两条边画虚线（辅助线，不是向量）
    dashed, = ax.plot([A[0], C[0]], [A[1], C[1]], linestyle=(0, (5, 4)),
                      lw=1.4, color="#000000")
    dashed.set_gid("helper-ac")
    dashed2, = ax.plot([B[0], C[0]], [B[1], C[1]], linestyle=(0, (5, 4)),
                       lw=1.4, color="#000000")
    dashed2.set_gid("helper-bc")

    # 三个标注。位置要留够余量：实测第一版把 "a+b" 顶到画布右边缘外，
    # PNG 里只剩 "a+"（几何全对、断言全绿，但图是废的）。
    label_artists = {}
    for text, point in LABELS.items():
        dx, dy = (0.14, -0.34) if text == "a" else (0.14, 0.16)
        if text == "a+b":                 # 对角线端点在右上角，标注放它左下侧
            dx, dy = (-1.05, 0.16)
        label_artists[text] = ax.text(point[0] + dx, point[1] + dy, text, fontsize=13)

    ax.set_xlim(-0.8, 4.4)
    ax.set_ylim(-0.8, 3.0)
    fig.tight_layout(pad=0.3)
    fig.canvas.draw()          # 先画一次，等比例才真正生效（否则采样到的变换不是渲染用的）

    # display 坐标：就是渲染器实际用的那个变换，校验"画出来的是不是平行四边形"要用它
    transform = ax.transData.transform
    # **不要四舍五入**：舍到 1e-6 会让 C−A 与 B−O 差出 1e-6，把平行四边形断言顶掉
    display = {name: [float(v) for v in transform(point)]
               for name, point in (("O", O), ("A", A), ("B", B), ("C", C))}

    # 记下每个标注的**实际外框**和画布大小 —— 校验器要用它们判断"有没有画出界"
    renderer = fig.canvas.get_renderer()
    canvas = [float(v) for v in fig.canvas.get_width_height()]
    label_boxes = {}
    for name, artist in label_artists.items():
        box = artist.get_window_extent(renderer)
        label_boxes[name] = [round(float(box.x0), 3), round(float(box.y0), 3),
                             round(float(box.x1), 3), round(float(box.y1), 3)]

    svg_path = out_dir / "vector-parallelogram.svg"
    fig.savefig(svg_path, format="svg", transparent=True)
    if show_png:
        fig.savefig(out_dir / "vector-parallelogram.png", format="png",
                    transparent=True)
    plt.close(fig)

    # 描边/填充改成 currentColor，这样页面切成深色模式时图也跟着变
    svg_path.write_text(_to_current_color(svg_path.read_text(encoding="utf-8")),
                        encoding="utf-8")

    payload = {
        "_meta": {
            "file_type": "figure_geometry",
            "schema_version": "1.0",
            "generator": "aiforstudy.figures",
            "field_notes": {
                "points": "图形数据坐标（只写一次，图和校验都从这里取）",
                "display": "matplotlib 实际渲染用的显示坐标；校验平行四边形的两条关系用它",
                "labels": "三个标注文字及其位置",
                "expected_ids": "该有的图元 id",
                "relations": "两对点位移必须相等（平行四边形：C−A == B−O 等）",
                "anchors": "每个标注挂在哪一点上（查标注有没有挂错顶点）",
                "canvas": "画布像素尺寸 [宽, 高]",
                "label_boxes": "每个标注的**实际外框** [x0,y0,x1,y1]（像素）。"
                               "校验器用它判断标注有没有被画到画布外 —— 几何全对但标注"
                               "出界的图是废的，实测第一版就这样。",
            },
        },
        "name": "vector-parallelogram",
        "title": "向量加法的平行四边形法则",
        "points": geometry(),
        "display": display,
        "canvas": canvas,
        "label_boxes": label_boxes,
        "labels": list(LABELS),
        # 这张图自带的数学断言（校验器通用地判它们）
        "expected_ids": ["vec-a", "vec-b", "vec-ab", "helper-ac", "helper-bc"],
        "relations": [[["C", "A"], ["B", "O"]], [["C", "B"], ["A", "O"]]],
        "anchors": {"a": "A", "b": "B", "a+b": "C"},
    }
    (out_dir / "vector-parallelogram.geometry.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"svg": str(svg_path), "png": str(out_dir / "vector-parallelogram.png"),
            "geometry": str(out_dir / "vector-parallelogram.geometry.json"),
            "display": display}


def _to_current_color(svg_text: str) -> str:
    """把写死的黑色换成 currentColor（深浅色主题都能看）。

    不要只匹配 `stroke="#000000"` 这一种写法 —— 实测 matplotlib 3.11 输出的是
    `style="stroke: #000000"`（冒号、带空格），按属性写法替换会漏掉。
    这里直接把颜色值本身换掉，属性名和空格怎么写都不影响。
    """
    for token in ("#000000", "#000", "black", "rgb(0%,0%,0%)"):
        svg_text = svg_text.replace(token, "currentColor")
    return svg_text


__all__ = ["draw", "geometry", "O", "A", "B", "C", "LABELS", "TITLE", "CONCEPTS"]
