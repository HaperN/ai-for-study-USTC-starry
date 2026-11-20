"""T08 画面缺陷夹具：**故意遮挡** ／ **错误公式** ／ **错误高亮**。

三张 640×360（draft 档）PNG，各自把缺陷**真的画进像素**，而不是只在 JSON 里声明：

| 夹具 | 缺陷 | 独立可核验的像素事实 |
|---|---|---|
| `occlusion.png` | 公式区域被一块纯黑矩形完全盖住 | 该 bbox 内所有像素都是 `(0,0,0)` |
| `wrong-formula.png` | 公式带里的值与正确帧不同 | 公式 bbox 与 `correct.png` 逐像素不同 |
| `wrong-highlight.png` | 高亮框画在 `trace_x` 上（这一步该高亮 `trace_y`） | `trace_x` bbox 出现高亮色，而 `trace_y` bbox 没有 |

为什么要把"缺陷"和"位置"分开写：`build()` 返回的 manifest 里有每个缺陷的
`bbox` 与 `object_id`，测试**不依赖任何评审模型**就能验证夹具本身是真的坏的
（否则"评审发现了遮挡"这件事可能只是评审在复述我们喂给它的标签）。

所有绘制都是确定性的（同一 Pillow 版本下字节可复现）；`build(out_dir)` 直接写盘
并返回 manifest，测试在 `tmp_path` 里现场生成，不需要往仓库塞二进制夹具。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

#: draft 档尺寸（与 `frame_check.PROFILE_SIZES["draft"]` 一致）
SIZE = (640, 360)
#: 高亮色（醒目黄）
HIGHLIGHT = (255, 235, 59)
#: 遮挡色（纯黑）
OCCLUSION = (0, 0, 0)

# ── 画面元素的位置（bbox = (left, top, right, bottom)）
AXES_BBOX = (40, 30, 600, 330)
FORMULA_BBOX = (60, 44, 420, 74)
TRACE_Y_BBOX = (430, 100, 590, 140)
TRACE_X_BBOX = (430, 220, 590, 260)
#: 这一步应当高亮的对象
HIGHLIGHT_TARGET = "trace_y"
#: 正确的公式文字与"错误公式"夹具里画的那一行
CORRECT_FORMULA = "u_xx = 2, u_yy = -2"
WRONG_FORMULA = "u_xx = -2, u_yy = 2"


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _draw_scene(draw, *, formula: str, highlight: str | None) -> None:
    """画一张"正确"的曲面截线画面：坐标系 + 两条截线 + 公式带 +（可选）高亮。"""
    left, top, right, bottom = AXES_BBOX
    draw.rectangle(AXES_BBOX, outline=(51, 65, 85), width=2)
    draw.line([(left, bottom - 60), (right, top + 40)], fill=(37, 99, 235), width=2)
    draw.line([(left, top + 80), (right, bottom - 40)], fill=(220, 38, 38), width=2)
    if highlight is not None:
        box = TRACE_Y_BBOX if highlight == "trace_y" else TRACE_X_BBOX
        draw.rectangle(box, fill=HIGHLIGHT)
    draw.text((TRACE_Y_BBOX[0] + 4, TRACE_Y_BBOX[1] + 6), "trace_y", fill=(51, 65, 85))
    draw.text((TRACE_X_BBOX[0] + 4, TRACE_X_BBOX[1] + 6), "trace_x", fill=(51, 65, 85))
    draw.rectangle((FORMULA_BBOX[0] - 4, FORMULA_BBOX[1] - 4,
                    FORMULA_BBOX[2] + 4, FORMULA_BBOX[3] + 4),
                   fill=(241, 245, 249), outline=(148, 163, 184))
    draw.text((FORMULA_BBOX[0], FORMULA_BBOX[1] + 6), formula, fill=(15, 23, 42))


def _write(path: Path, *, formula: str, highlight: str | None,
           occlude: bool = False) -> str:
    from PIL import Image, ImageDraw

    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", SIZE, (248, 250, 252))
    draw = ImageDraw.Draw(image)
    _draw_scene(draw, formula=formula, highlight=highlight)
    if occlude:
        draw.rectangle(FORMULA_BBOX, fill=OCCLUSION)
    image.save(path, format="PNG")
    return _sha256(path)


def build(out_dir: str | Path) -> dict:
    """生成 4 张 PNG（1 张正确 + 3 张缺陷）并返回 manifest（含 bbox／sha256）。"""
    root = Path(out_dir)
    manifest: dict = {"size": list(SIZE), "formula_bbox": list(FORMULA_BBOX),
                      "trace_y_bbox": list(TRACE_Y_BBOX), "trace_x_bbox": list(TRACE_X_BBOX),
                      "highlight_rgb": list(HIGHLIGHT), "occlusion_rgb": list(OCCLUSION),
                      "correct_formula": CORRECT_FORMULA, "wrong_formula": WRONG_FORMULA,
                      "highlight_target": HIGHLIGHT_TARGET, "frames": {}}

    correct = root / "correct.png"
    manifest["frames"]["correct"] = {
        "path": str(correct), "sha256": _write(correct, formula=CORRECT_FORMULA,
                                              highlight=None),
        "step_id": "s1", "timestamp": 3.0, "object_id": "formula_u", "defect": ""}

    occlusion = root / "occlusion.png"
    manifest["frames"]["occlusion"] = {
        "path": str(occlusion),
        "sha256": _write(occlusion, formula=CORRECT_FORMULA, highlight=None, occlude=True),
        "step_id": "s1", "timestamp": 3.0, "object_id": "formula_u",
        "defect": "occlusion", "bbox": list(FORMULA_BBOX),
        "occluded_by": "surface_u"}

    wrong_formula = root / "wrong-formula.png"
    manifest["frames"]["wrong_formula"] = {
        "path": str(wrong_formula),
        "sha256": _write(wrong_formula, formula=WRONG_FORMULA, highlight=None),
        "step_id": "s2", "timestamp": 12.0, "object_id": "formula_u",
        "defect": "wrong_formula", "bbox": list(FORMULA_BBOX),
        "expected": CORRECT_FORMULA, "drawn": WRONG_FORMULA}

    wrong_highlight = root / "wrong-highlight.png"
    manifest["frames"]["wrong_highlight"] = {
        "path": str(wrong_highlight),
        "sha256": _write(wrong_highlight, formula=CORRECT_FORMULA, highlight="trace_x"),
        "step_id": "s2", "timestamp": 14.0, "object_id": HIGHLIGHT_TARGET,
        "defect": "wrong_highlight", "expected_highlight": HIGHLIGHT_TARGET,
        "highlighted": "trace_x", "bbox": list(TRACE_X_BBOX),
        "should_be_bbox": list(TRACE_Y_BBOX)}
    return manifest


def frame_resource(entry: dict) -> dict:
    """manifest 里的一条帧 → `AnimationResult.resources` 的 keyframe 条目。"""
    path = Path(entry["path"])
    return {"kind": "keyframe", "relative_path": path.name, "sha256": entry["sha256"],
            "bytes": path.stat().st_size, "mime": "image/png",
            "timestamp": entry["timestamp"]}


__all__ = ["SIZE", "HIGHLIGHT", "OCCLUSION", "FORMULA_BBOX", "TRACE_Y_BBOX",
           "TRACE_X_BBOX", "HIGHLIGHT_TARGET", "CORRECT_FORMULA", "WRONG_FORMULA",
           "build", "frame_resource"]
