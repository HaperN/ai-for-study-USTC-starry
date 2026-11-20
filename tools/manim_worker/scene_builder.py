"""受限场景 → Manim 对象与时间线：**纯校验 + 沙箱内执行**，不在本模块做数学。

分工（这是 T06/T07 的接缝，写清楚免得两边各算一半）：

| 谁 | 做什么 |
|---|---|
| `aiforstudy.blocks.visual.compiler`（T06） | 受限 AST → 数值采样 + 公式文本（**唯一的数学来源**） |
| `manim_adapter.py`（宿主，T07） | 校验可信源码、把 `transform` 的**新采样**算好放进计划、装配 `AnimationResult` |
| 本模块 | 结构白名单校验、时间线分配（绝对时刻必须与规格一致）、沙箱内建 Manim 对象并渲染 |

**本模块不 import manim**（除了 `render()` 内部的延迟 import）：宿主侧要能用它做
"计划是否可渲染"的静态校验与单测，而宿主 venv 里根本没有 Manim。
同样，本模块**不求值任何表达式** —— 采样数组由 T06 算好，`transform` 的新采样由
适配器算好，沙箱里只有几何搬运。模型文本只作为 `Text` 数据；MathTex 只接受
宿主从批准 AST 生成并重新编译核验的公式。模型不能提交 TeX、代码或路径。
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import time
from pathlib import Path

FORMAT = "visual-scene-1.0"
#: 载荷允许的键（多一个都拒绝：静默忽略字段=静默改变画面）
PAYLOAD_KEYS = {"format", "compiler_version", "profile", "parameters", "objects", "captions",
                "formulas", "steps", "math_values"}
OBJECT_KINDS = ("axes2d", "axes3d", "point", "arrow", "vector", "plane", "curve", "surface",
                "section", "label", "formula", "matrix2d")
ACTION_KINDS = ("reveal", "hide", "highlight", "transform", "camera", "pause")
COLORS = {"ink": "#1f2937", "blue": "#2563eb", "red": "#dc2626", "green": "#15803d",
          "gray": "#64748b"}
#: 只支持 transform 的对象类型（其余在计划期就拒绝，不做"跳过这个动作"）
TRANSFORMABLE = ("surface", "plane")
#: 3D 场景特征（含这些对象的规格走 ThreeDScene）
THREE_D_KINDS = ("axes3d", "surface", "plane")
#: 每种对象**允许**的键（= T06 编译器产物的形状 + 适配器注入的 color/label）。
#: 多一个键就报错：静默忽略字段等于静默改变画面。
OBJECT_KEYS = {
    "axes2d": {"kind", "x_range", "y_range", "x_label", "y_label"},
    "axes3d": {"kind", "x_range", "y_range", "z_range"},
    "point": {"kind", "space", "at"},
    "arrow": {"kind", "space", "start", "end"},
    "vector": {"kind", "space", "start", "end"},
    "matrix2d": {"kind", "rows"},
    "surface": {"kind", "x_values", "y_values", "u", "resolution"},
    "plane": {"kind", "x_values", "y_values", "u", "resolution"},
    "curve": {"kind", "form", "samples", "points2d", "points", "segments", "domain_evidence"},
    "section": {"kind", "fixed_variable", "fixed_value", "free_variable", "free_values",
                "u", "points2d", "points3d", "range", "samples"},
    "label": {"kind", "text", "at", "space"},
    "formula": {"kind", "text", "tex", "rendering"},
}
#: 这些键可选（由适配器从规格注入；缺失时用默认值）
OPTIONAL_OBJECT_KEYS = {"color", "label"}
#: 每个可动画部分的最短时长（秒）：再短就拆不出可看的动画，宁可报错
MIN_PART_SECONDS = 0.05
#: 场景单位与数学坐标的映射（画面尺度，不进数学）
MAX_AXIS_UNITS = 5.0
Z_AXIS_UNITS = 3.0


class ScenePlanError(ValueError):
    """计划期就发现"这个载荷渲染不出正确画面" —— 绝不静默跳过。"""


def _fail(path: str, message: str) -> None:
    raise ScenePlanError(f"{path}: {message}")


def _finite(value, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(path, "必须是有限数值")
    number = float(value)
    if not math.isfinite(number):
        _fail(path, "必须是有限数值")
    return number


def _coord(value, path: str) -> list[float]:
    if not isinstance(value, list) or len(value) not in (2, 3):
        _fail(path, "必须是 2 或 3 维坐标数组")
    return [_finite(item, f"{path}[{i}]") for i, item in enumerate(value)]


def _number_list(value, path: str, length: int | None = None) -> list[float]:
    if not isinstance(value, list):
        _fail(path, "必须是数组")
    if length is not None and len(value) != length:
        _fail(path, f"必须是 {length} 个元素")
    return [_finite(item, f"{path}[{i}]") for i, item in enumerate(value)]


def validate_payload(payload: dict, *, profile: str) -> None:
    """载荷结构白名单校验。**多字段、缺字段、未知对象／动作都当场报错。**"""
    if not isinstance(payload, dict):
        _fail("payload", "必须是对象")
    v2=payload.get("format")=="visual-scene-2.0"
    keys=PAYLOAD_KEYS | ({"math_binding","presentation_policy"} if v2 else set())
    extra = sorted(set(payload) - keys)
    if extra:
        _fail("payload", f"出现未定义字段 {extra}")
    missing = sorted(keys - set(payload))
    if missing:
        _fail("payload", f"缺少字段 {missing}")
    if payload["format"] not in (FORMAT,"visual-scene-2.0"):
        _fail("payload.format", f"只接受 {FORMAT!r}，实际 {payload['format']!r}")
    if payload["profile"] != profile:
        _fail("payload.profile", f"载荷档位 {payload['profile']!r} 与请求 {profile!r} 不一致")
    objects = payload["objects"]
    if not isinstance(objects, dict) or not objects:
        _fail("payload.objects", "必须是 object_id → 对象 的映射")
    steps = payload["steps"]
    if not isinstance(steps, list) or not steps:
        _fail("payload.steps", "必须是非空数组")
    captions = payload["captions"]
    if not isinstance(captions, list) or len(captions) != len(steps):
        _fail("payload.captions", "字幕条数必须与步骤数一致")
    for index, step in enumerate(steps):
        _validate_step(step, index, objects)
    for object_id, obj in objects.items():
        _validate_object(object_id, obj)


def _validate_object(object_id: str, obj: dict) -> None:
    path = f"objects.{object_id}"
    if not isinstance(obj, dict):
        _fail(path, "必须是对象")
    kind = obj.get("kind")
    if kind not in OBJECT_KINDS:
        _fail(f"{path}.kind", f"未知对象类型 {kind!r}")
    extra = sorted(set(obj) - OBJECT_KEYS[kind] - OPTIONAL_OBJECT_KEYS)
    if extra:
        _fail(path, f"对象 {kind!r} 出现未定义字段 {extra}")
    if kind in ("surface", "plane"):
        grid = obj.get("u")
        x_values, y_values = obj.get("x_values"), obj.get("y_values")
        if not isinstance(x_values, list) or not isinstance(y_values, list):
            _fail(path, "曲面必须带 x_values / y_values 采样")
        if len(x_values) < 2 or len(y_values) < 2:
            _fail(path, "曲面采样点数至少 2×2")
        if not isinstance(grid, list) or len(grid) != len(y_values):
            _fail(f"{path}.u", "网格行数必须等于 y 采样点数")
        for row_index, row in enumerate(grid):
            if not isinstance(row, list) or len(row) != len(x_values):
                _fail(f"{path}.u[{row_index}]", "网格列数必须等于 x 采样点数")
            for column, value in enumerate(row):
                _finite(value, f"{path}.u[{row_index}][{column}]")
    elif kind == "matrix2d":
        rows = obj.get("rows")
        if not isinstance(rows, list) or len(rows) != 2 or \
                any(not isinstance(row, list) or len(row) != 2 for row in rows):
            _fail(f"{path}.rows", "matrix2d 只支持 2×2")
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                _finite(value, f"{path}.rows[{r}][{c}]")
    elif kind == "section":
        if not isinstance(obj.get("points3d"), list) and not isinstance(obj.get("points2d"), list):
            _fail(path, "截面必须带 T06 的采样点")
    elif kind == "curve":
        if not isinstance(obj.get("points2d"), list) and not isinstance(obj.get("points"), list):
            _fail(path, "曲线必须带 T06 的采样点")


def _validate_step(step: dict, index: int, objects: dict) -> None:
    path = f"steps[{index}]"
    if not isinstance(step, dict):
        _fail(path, "必须是对象")
    allowed = {"step_id", "start", "end", "actions", "checkpoints", "caption",
               "expected_change", "kind", "goal_id", "claim_ids", "math_claim_refs", "object_ids",
               "observation", "caption_claim_ids"}
    extra = sorted(set(step) - allowed)
    if extra:
        _fail(path, f"步骤出现未定义字段 {extra}")
    for required in ("step_id", "start", "end", "actions", "caption"):
        if required not in step:
            _fail(f"{path}.{required}", "必填字段缺失")
    start = _finite(step["start"], f"{path}.start")
    end = _finite(step["end"], f"{path}.end")
    if start < 0 or end <= start:
        _fail(path, f"时段必须非负且 end > start（{start} → {end}）")
    if not isinstance(step["caption"], str) or not step["caption"].strip():
        _fail(f"{path}.caption", "字幕不能为空")
    actions = step["actions"]
    if not isinstance(actions, list):
        _fail(f"{path}.actions", "必须是数组")
    for position, action in enumerate(actions):
        _validate_action(action, f"{path}.actions[{position}]", objects)


def _validate_action(action: dict, path: str, objects: dict) -> None:
    if not isinstance(action, dict):
        _fail(path, "必须是对象")
    kind = action.get("action")
    if kind not in ACTION_KINDS:
        _fail(f"{path}.action", f"未知动作 {kind!r}")
    allowed = {"reveal": {"action", "object_id"}, "hide": {"action", "object_id"},
               "highlight": {"action", "object_id"},
               "transform": {"action", "object_id", "target_object_id", "u", "x_values", "y_values"},
               "camera": {"action", "zoom", "phi", "theta"}, "pause": {"action", "seconds"}}[kind]
    extra = sorted(set(action) - allowed)
    if extra:
        _fail(path, f"动作 {kind!r} 出现未定义字段 {extra}")
    if kind in ("reveal", "hide", "highlight", "transform"):
        object_id = action.get("object_id")
        if object_id not in objects:
            _fail(f"{path}.object_id", f"引用了不存在的对象 {object_id!r}")
    if kind == "transform" and "target_object_id" in action:
        if action["target_object_id"] not in objects or objects[action["target_object_id"]]["kind"]!=objects[action["object_id"]]["kind"]:
            _fail(path,"transform target_object_id 必须存在且类型相同")
    elif kind == "transform":
        target = objects[action["object_id"]]
        if target.get("kind") not in TRANSFORMABLE:
            _fail(f"{path}.object_id",
                  f"transform 只支持 {list(TRANSFORMABLE)}，{action['object_id']!r} 是 "
                  f"{target.get('kind')!r}")
        if "u" not in action:
            _fail(f"{path}.u", "transform 必须带适配器算好的新采样（沙箱内不求值）")
    if kind == "pause":
        _finite(action.get("seconds"), f"{path}.seconds")
    if kind == "camera":
        for key in ("zoom", "phi", "theta"):
            if key in action:
                _finite(action[key], f"{path}.{key}")


def plan_scene(payload: dict, *, profile: str) -> dict:
    """校验并分配时间线。产出的 `timeline` 每项都带**绝对**起止（与规格一致）。"""
    validate_payload(payload, profile=profile)
    objects = payload["objects"]
    three_d = any(isinstance(obj, dict) and obj.get("kind") in THREE_D_KINDS
                  for obj in objects.values())
    axes3d = next((obj for obj in objects.values()
                   if isinstance(obj, dict) and obj.get("kind") == "axes3d"), None)
    axes2d = next((obj for obj in objects.values()
                   if isinstance(obj, dict) and obj.get("kind") == "axes2d"), None)
    if three_d and axes3d is None:
        axes3d = {"kind": "axes3d", "x_range": [-2, 2], "y_range": [-2, 2], "z_range": [-4, 4]}
    if not three_d and axes2d is None:
        axes2d = {"kind": "axes2d", "x_range": [-5, 5], "y_range": [-5, 5]}

    timeline = []
    cursor = 0.0
    for step in payload["steps"]:
        start, end = float(step["start"]), float(step["end"])
        if abs(start - cursor) > 1e-9:
            _fail(f"steps[{step['step_id']}]",
                  f"时段必须从 {cursor} 开始且不重叠，实际 {start}")
        timeline.append(_plan_step(step, start, end, objects))
        cursor = end
    if payload.get("format")=="visual-scene-2.0" and not 0 < cursor <= payload["presentation_policy"]["max_seconds"]:
        _fail("total_seconds","超出宿主 presentation_policy 时长上限")
    if payload.get("format")=="visual-scene-2.0":
        _assign_formula_slots(timeline,objects,payload["presentation_policy"]["max_visible_formulas"])
    return {
        "format": payload["format"], "profile": profile,
        "math_binding": payload.get("math_binding"), "presentation_policy":payload.get("presentation_policy"), "compiler_version": payload["compiler_version"],
        "parameters": dict(payload["parameters"]), "objects": objects,
        "captions": list(payload["captions"]), "formulas": list(payload["formulas"]),
        "math_values": payload["math_values"], "three_d": three_d,
        "axes3d": axes3d, "axes2d": axes2d, "timeline": timeline,
        "total_seconds": cursor,
    }


def _assign_formula_slots(timeline, objects, capacity):
    """Reserve host layout cells for simultaneously visible formulas.

    A transform inherits its source cell; a later reveal of the source gets a
    free cell, so before/after comparison never stacks two formulas together.
    """
    active={}
    for step in timeline:
        for part in step['parts']:
            oid=part.get('object_id')
            if objects.get(oid,{}).get('kind')!='formula':
                continue
            action=part['action']
            if action=='reveal':
                free=next((slot for slot in range(capacity) if slot not in active.values()),None)
                if free is None:
                    _fail('formula layout',f'当前宿主最多同时呈现 {capacity} 个公式；请拆分教学步骤')
                active[oid]=free;part['formula_slot']=free
            elif action=='transform':
                slot=active.pop(oid);active[part['target_object_id']]=slot
                part['formula_slot']=slot
            elif action=='hide':
                active.pop(oid,None)


#: 单个动作的**默认**时长（秒）。为什么不是"把整步时间平分给所有动作"：
#: 规格里的 `checkpoints` 是**设计好的观察时刻**（例如"先看清 x、y、u 三个方向"），
#: 平分会让检查点落在某一个 reveal 的中间 —— 实测 4.0s 那张关键帧上曲面半透明、公式还没出现。
#: 正确节奏是：动作很快做完（0.5s 级），然后用 `hold` 把这一步剩下的时间**静止**展示。
ACTION_SECONDS = {"reveal": 0.5, "hide": 0.5, "highlight": 0.5, "transform": 0.8,
                  "camera": 1.2}
#: 字幕进出场时长
CAPTION_SECONDS = 0.35


def _plan_step(step: dict, start: float, end: float, objects: dict) -> dict:
    span = end - start
    pauses = [float(action["seconds"]) for action in step["actions"]
              if action["action"] == "pause"]
    pause_total = sum(pauses)
    available = span - pause_total
    if available <= 0:
        _fail(f"steps[{step['step_id']}]",
              f"停顿 {pause_total}s 已经吃满（甚至超过）本步 {span}s")
    flexible = (CAPTION_SECONDS * 2
                + sum(ACTION_SECONDS[action["action"]] for action in step["actions"]
                      if action["action"] != "pause"))
    scale = 1.0
    if flexible > available:
        scale = available / flexible
        smallest = CAPTION_SECONDS * scale * 0  # 只为可读性保留结构
        del smallest
    parts: list[dict] = []
    cursor = start

    def add(name: str, run_time: float, **extra) -> None:
        nonlocal cursor
        run_time = max(run_time, MIN_PART_SECONDS)
        parts.append({**extra, "action": name, "run_time": round(run_time, 6),
                      "start": round(cursor, 6), "end": round(cursor + run_time, 6)})
        cursor += run_time

    caption_run = CAPTION_SECONDS * scale
    if caption_run < MIN_PART_SECONDS:
        _fail(f"steps[{step['step_id']}]",
              f"本步只有 {span}s，连 {MIN_PART_SECONDS}s 的字幕都排不下")
    add("caption_in", caption_run)
    for action in step["actions"]:
        if action["action"] == "pause":
            add("pause", float(action["seconds"]))
            continue
        add(action["action"], ACTION_SECONDS[action["action"]] * scale,
            **{key: value for key, value in action.items() if key != "action"})
    remaining = (end - caption_run) - cursor
    if remaining < -1e-6:
        _fail(f"steps[{step['step_id']}]",
              f"动作排到了 {cursor:.4f}s，超过本步 end={end}")
    if remaining > 1e-9:
        add("hold", remaining)
    add("caption_out", caption_run)
    checkpoints = [{"at": float(item["at"]), "note": item.get("note", "")}
                   for item in step.get("checkpoints", [])]
    moments=[]
    for part in parts:
        if part["action"] in ("reveal","hide","highlight","transform"):
            common={"object_id":part["object_id"],"action":part["action"]}
            if "target_object_id" in part: common["target_object_id"]=part["target_object_id"]
            moments.extend([{**common,"at":part["start"],"phase":"before"},
                            {**common,"at":part["end"],"phase":"after"}])
            if part["action"]=="highlight": moments.append({**common,"at":(part["start"]+part["end"])/2,"phase":"during"})
    moments.append({"at":max(start,(start+end)/2),"phase":"observe"})
    return {"step_id": step["step_id"], "start": start, "end": end,
            **{k:step[k] for k in ("kind","goal_id","claim_ids","math_claim_refs","object_ids","observation","caption_claim_ids") if k in step},
            "evidence_moments": moments,
            "caption": step["caption"], "expected_change": step.get("expected_change", ""),
            "checkpoints": checkpoints, "parts": parts}


# ── 沙箱内执行 ─────────────────────────────────────────────────────────────
def _font_probe(font_family: str) -> dict:
    """字体证据：manimpango 认得的家族 + fontconfig 实际解析到的文件。**不许静默回退。**"""
    import manimpango
    families = list(manimpango.list_fonts())
    report = {"family": font_family, "in_manimpango": font_family in families,
              "family_count": len(families), "fc_match": ""}
    match = shutil.which("fc-match")
    if match:
        proc = subprocess.run([match, "--format", "%{file}", font_family],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)
        report["fc_match"] = proc.stdout.decode("utf-8", "replace").strip()
    if not report["in_manimpango"]:
        raise RuntimeError(f"字体家族 {font_family!r} 不在 manimpango 的字体列表里："
                           f"继续渲染就会静默回退到别的字体（§5.4 禁止）")
    return report


def _pinned_font_check(report: dict, pinned_file: str) -> None:
    resolved = report.get("fc_match") or ""
    if resolved and Path(resolved).resolve() != Path(pinned_file).resolve():
        raise RuntimeError(f"fontconfig 把 {report['family']!r} 解析到了 {resolved}，"
                           f"不是固定的 {pinned_file}（§5.4：不许静默字体回退）")


def _axes_units(ranges: list[list[float]]) -> list[float]:
    spans = [abs(high - low) for low, high in ranges]
    largest = max(spans) or 1.0
    return [MAX_AXIS_UNITS * span / largest for span in spans]


def _build_scene_class(plan: dict, *, font_family: str, pinned_font: str):
    """在**运行时**定义 Manim 场景类（本模块在宿主侧不 import manim）。"""
    import manim
    from manim import (DEGREES, DOWN, UL, Arrow, Axes, Dot, FadeIn, FadeOut, Indicate,
                       Line, MovingCameraScene, Surface, Text, MathTex, TexTemplate, ReplacementTransform, ThreeDAxes, ThreeDScene,
                       VGroup, VMobject, WHITE)

    import policy
    tex_template=TexTemplate(preamble=policy.MATH_TEX_PREAMBLE)
    colors = COLORS
    axis3d = plan["axes3d"]
    axis2d = plan["axes2d"]
    # ⚠️ 纯二维规格（向量／矩阵小样）没有 axis3d：第一版在这里无条件读 axis3d 的 range，
    # 2D 场景直接 `TypeError: 'NoneType' object is not subscriptable`（被单测当场抓住）。
    if plan["three_d"]:
        axes_ranges = [[axis3d["x_range"][0], axis3d["x_range"][1]],
                       [axis3d["y_range"][0], axis3d["y_range"][1]],
                       [axis3d["z_range"][0], axis3d["z_range"][1]]]
    else:
        axes_ranges = [[axis2d["x_range"][0], axis2d["x_range"][1]],
                       [axis2d["y_range"][0], axis2d["y_range"][1]]]
    units = _axes_units(axes_ranges)

    # 2D 用 `MovingCameraScene` 而不是 `Scene`：规格里的 `camera` 动作（zoom）需要
    # `camera.frame`，而普通 `Scene` 的 `Camera` 没有这个属性 ——
    # 实测 matrix-apply 小样直接 `AttributeError: 'Camera' object has no attribute 'frame'`。
    class VisualScene(ThreeDScene if plan["three_d"] else MovingCameraScene):
        """受限场景的 Manim 外壳：对象与时间线**完全**来自计划，不做任何自选动作。"""

        def construct(self) -> None:  # noqa: C901 - 动作分派，读起来比拆散清楚
            self.camera.background_color = WHITE
            if plan["three_d"]:
                self.set_camera_orientation(phi=70 * DEGREES, theta=-45 * DEGREES,
                                            distance=9.0)
            self.mobjects_by_id: dict[str, object] = {}
            self._text_cache: dict[str, object] = {}
            # 3D 场景里文字默认落在**世界坐标**上：相机一转，公式和注释就跑到画面外
            # （实测：s1 的关键帧里公式根本看不见）。文字类对象一律"固定于画面"。
            self.frame_fixed_ids: set[str] = {
                object_id for object_id, obj in plan["objects"].items()
                if obj.get("kind") in ("label", "formula")} if plan["three_d"] else set()
            self._build_axes()
            self._build_objects()
            caption = None
            rendered_frames=0
            for step in plan["timeline"]:
                for part in step["parts"]:
                    name = part["action"]
                    if plan["format"]=="visual-scene-2.0":
                        end_frame=round(part["end"]*manim.config.frame_rate)
                        run_time=(end_frame-rendered_frames)/manim.config.frame_rate
                        rendered_frames=end_frame
                    else:
                        run_time = max(part["run_time"], 0.01)
                    if name in ("caption_in", "caption_out"):
                        # Captions are shipped as VTT, controlled by the player.
                        # Burning the same text here creates two overlapping lines
                        # and prevents the learner from turning subtitles off.
                        self.wait(run_time)
                    else:
                        self._play_action(part, run_time)
            if caption is not None:
                self.remove(caption)

        # -- 文字 ---------------------------------------------------------
        def _text(self, value: str, *, size: float, color: str):
            key = f"{size}|{color}|{value}"
            if key not in self._text_cache:
                self._text_cache[key] = Text(value, font=font_family, font_size=size,
                                             color=color)
            return self._text_cache[key].copy()

        def _show_caption(self, caption) -> None:
            if plan["three_d"]:
                self.add_fixed_in_frame_mobjects(caption)
            else:
                self.add(caption)

        # -- 坐标轴 -------------------------------------------------------
        def _build_axes(self) -> None:
            if plan["three_d"]:
                self.axes3d = ThreeDAxes(
                    x_range=[axis3d["x_range"][0], axis3d["x_range"][1], (axis3d["x_range"][1] - axis3d["x_range"][0]) / 4],
                    y_range=[axis3d["y_range"][0], axis3d["y_range"][1], (axis3d["y_range"][1] - axis3d["y_range"][0]) / 4],
                    z_range=[axis3d["z_range"][0], axis3d["z_range"][1], (axis3d["z_range"][1] - axis3d["z_range"][0]) / 4],
                    x_length=units[0], y_length=units[1], z_length=units[2],
                    axis_config={"color": "#334155", "stroke_width": 2.5})
            self.axes2d = Axes(
                x_range=[axis2d["x_range"][0], axis2d["x_range"][1], (axis2d["x_range"][1] - axis2d["x_range"][0]) / 4],
                y_range=[axis2d["y_range"][0], axis2d["y_range"][1], (axis2d["y_range"][1] - axis2d["y_range"][0]) / 4],
                x_length=units[0], y_length=units[1],
                axis_config={"color": "#334155", "stroke_width": 2.5},
                tips=False)

        def _project(self, point: list[float]):
            if len(point) == 3:
                return self.axes3d.c2p(point[0], point[1], point[2])
            if plan["three_d"]:
                return self.axes3d.c2p(point[0], point[1], point[2] if len(point) > 2 else 0.0)
            return self.axes2d.c2p(point[0], point[1])

        # -- 对象 ---------------------------------------------------------
        def _build_objects(self) -> None:
            for object_id, obj in plan["objects"].items():
                kind = obj["kind"]
                if kind == "axes3d":
                    # 轴也是**对象**：动作可以 reveal/highlight 它，所以照样进注册表。
                    self.mobjects_by_id[object_id] = self.axes3d
                    continue
                if kind == "axes2d":
                    self.mobjects_by_id[object_id] = self.axes2d
                    continue
                self.mobjects_by_id[object_id] = self._make(object_id, obj)

        def _make(self, object_id: str, obj: dict):
            kind = obj["kind"]
            color = colors.get(obj.get("color", "ink"), colors["ink"])
            if kind in ("surface", "plane"):
                return self._surface(obj, color=color, opacity=0.75)
            if kind == "section":
                points = obj.get("points3d") or [
                    [p[0], obj.get("fixed_value", 0.0), p[1]] for p in obj["points2d"]]
                mob = VMobject(color=color, stroke_width=4)
                mob.set_points_as_corners([self._project(point) for point in points])
                return mob
            if kind == "curve" and "segments" in obj:
                curves=[]
                for points in obj["segments"]:
                    mob=VMobject(color=color,stroke_width=4)
                    mob.set_points_as_corners([self._project(point) for point in points])
                    curves.append(mob)
                return VGroup(*curves)
            if kind == "curve":
                points = obj.get("points2d") or obj.get("points")
                mob = VMobject(color=color, stroke_width=4)
                mob.set_points_as_corners([self._project(point) for point in points])
                return mob
            if kind == "point":
                return Dot(self._project(obj["at"]), color=color)
            if kind in ("vector", "arrow"):
                start = obj.get("start") or [0.0] * len(obj["end"])
                return Arrow(self._project(start), self._project(obj["end"]),
                             buff=0, color=color, stroke_width=5)
            if kind == "label":
                text = self._text(obj["text"], size=24, color=color)
                text.move_to(self._project(obj["at"]))
                return text
            if kind == "formula":
                if obj.get("rendering")=="trusted_mathtex":
                    # At 1280px output, 84 yields about 52px numeral ink: at
                    # 360px video width on a 390px page it remains at least 14px.
                    text=MathTex(obj["tex"],tex_template=tex_template,font_size=84,color=color)
                    if text.width>6: text.scale_to_fit_width(6)
                    if text.height>1.25: text.scale_to_fit_height(1.25)
                else:
                    text = self._text(obj["text"], size=28, color=color)
                # 公式放**左上角**而不是上边中点：曲面在 3D 里很高，上边中点会被它挡住
                # （实测海报上公式就压在曲面/坐标轴上）。左上角是空白区，字号仍 ≥24px。
                return text.to_corner(UL, buff=0.45)
            if kind == "matrix2d":
                rows = obj["rows"]
                numbers = VGroup(*[Text("  ".join(f"{value:g}" for value in row),
                                        font=font_family, font_size=24, color=color)
                                   for row in rows]).arrange((0, -1, 0), buff=0.15)
                left = Line(numbers.get_left() + [-0.18, 0.22, 0],
                            numbers.get_left() + [-0.18, -0.22, 0], color=color)
                right = Line(numbers.get_right() + [0.18, 0.22, 0],
                             numbers.get_right() + [0.18, -0.22, 0], color=color)
                return VGroup(left, numbers, right)
            raise RuntimeError(f"未支持的对象类型 {kind!r}（计划期应已拒绝）")

        def _surface(self, obj: dict, *, color: str, opacity: float):
            xs = [float(value) for value in obj["x_values"]]
            ys = [float(value) for value in obj["y_values"]]
            grid = [[float(value) for value in row] for row in obj["u"]]

            def value_at(u: float, v: float) -> float:
                i = _interp_index(ys, v)
                j = _interp_index(xs, u)
                return grid[i][j]

            return Surface(
                lambda u, v: self.axes3d.c2p(u, v, value_at(u, v)),
                u_range=[min(xs), max(xs)], v_range=[min(ys), max(ys)],
                resolution=(len(xs), len(ys)),
                fill_color=color, fill_opacity=opacity,
                stroke_color=color, stroke_width=0.8, stroke_opacity=0.55)

        # -- 动作 ---------------------------------------------------------
        def _play_action(self, part: dict, run_time: float) -> None:
            name = part["action"]
            if name in ("pause", "hold"):
                self.wait(run_time)
                return
            if name == "camera":
                self._camera(part, run_time)
                return
            mob = self.mobjects_by_id.get(part["object_id"])
            if mob is None:
                raise RuntimeError(f"对象 {part['object_id']!r} 尚未建立")
            if 'formula_slot' in part:
                slot=part['formula_slot']
                placed=self.mobjects_by_id[part.get('target_object_id',part['object_id'])]
                placed.to_corner(UL,buff=0.45).shift([6.8*(slot%2),-1.75*(slot//2),0])
            if name == "reveal":
                if part["object_id"] in self.frame_fixed_ids:
                    self.add_fixed_in_frame_mobjects(mob)
                self.play(FadeIn(mob, run_time=run_time))
            elif name == "hide":
                self.play(FadeOut(mob, run_time=run_time))
            elif name == "highlight":
                self.play(Indicate(mob, color=colors["blue"], scale_factor=1.08,
                                   run_time=run_time))
            elif name == "transform" and "target_object_id" in part:
                target=part["target_object_id"];replacement=self.mobjects_by_id[target]
                if target in self.frame_fixed_ids:
                    self.add_fixed_in_frame_mobjects(replacement)
                    self.remove(replacement)
                self.play(ReplacementTransform(mob,replacement),run_time=run_time)
            elif name == "transform":
                replacement = self._surface(part, color=COLORS.get("blue"), opacity=0.75)
                self.play(FadeOut(mob, run_time=run_time / 2),
                          FadeIn(replacement, run_time=run_time / 2))
                self.mobjects_by_id[part["object_id"]] = replacement
            else:  # pragma: no cover - 计划期已白名单
                raise RuntimeError(f"未支持的动作 {name!r}")

        def _camera(self, part: dict, run_time: float) -> None:
            zoom = float(part.get("zoom", 1.0) or 1.0)
            if plan["three_d"]:
                # ThreeDCamera（Cairo 渲染器）原生支持 zoom／phi／theta —— 直接传，
                # 不做"zoom 换算成 distance"的自创映射（Manim 0.21 的 `move_camera`
                # 参数是 `focal_distance`，不是 `distance`；传错会让 anims 为空并抛
                # "Called Scene.play with no animations"）。
                kwargs = {"zoom": max(0.1, zoom)}
                if "phi" in part:
                    kwargs["phi"] = float(part["phi"]) * DEGREES
                if "theta" in part:
                    kwargs["theta"] = float(part["theta"]) * DEGREES
                self.move_camera(run_time=run_time, **kwargs)
            else:
                self.play(self.camera.frame.animate.set(width=14.22 / max(zoom, 0.1)),
                          run_time=run_time)

    def _interp_index(values: list[float], target: float) -> int:
        best, best_distance = 0, None
        for index, value in enumerate(values):
            distance = abs(value - target)
            if best_distance is None or distance < best_distance:
                best, best_distance = index, distance
        return best

    return VisualScene


def render(plan: dict, *, scratch: str, output: str, font_family: str, pinned_font: str,
           pixel_width: int, pixel_height: int, frame_rate: int,
           poster_seconds: float, ffmpeg: str) -> dict:
    """沙箱内真正渲染：Manim 出片 → 重封装 faststart → poster 与每步关键帧。"""
    import manim
    from manim import config

    scratch_path = Path(scratch)
    output_path = Path(output)
    media = scratch_path / "media"
    for directory in (media, scratch_path / "tmp", scratch_path / "cache", scratch_path / "tex"):
        directory.mkdir(parents=True, exist_ok=True)
    config.media_dir = str(media)
    config.tex_dir = str(scratch_path / "tex")
    config.pixel_width = int(pixel_width)
    config.pixel_height = int(pixel_height)
    config.frame_rate = int(frame_rate)
    config.frame_size = (int(pixel_width), int(pixel_height))
    config.background_color = "#ffffff"
    config.disable_caching = True
    config.preview = False
    config.progress_bar = "none"
    config.verbosity = "ERROR"
    config.write_to_movie = True
    config.save_last_frame = False
    config.save_pngs = False
    config.output_file = "raw"
    config.movie_file_extension = ".mp4"
    if hasattr(config, "ffmpeg_executable"):
        config.ffmpeg_executable = ffmpeg

    font_report = _font_probe(font_family)
    _pinned_font_check(font_report, pinned_font)

    scene_class = _build_scene_class(plan, font_family=font_family, pinned_font=pinned_font)
    started = time.monotonic()
    scene = scene_class()
    scene.render()
    render_seconds = time.monotonic() - started

    raws = sorted(media.rglob("raw*.mp4"), key=lambda path: path.stat().st_mtime)
    if not raws:
        raise RuntimeError("Manim 没有写出 raw mp4")
    raw_video = raws[-1]

    video = output_path / "video.mp4"
    _ffmpeg(ffmpeg, ["-i", str(raw_video), "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy", "-c:a", "aac", "-ar", "24000", "-ac", "1",
                     "-movflags", "+faststart", str(video)])
    frames = []
    poster = output_path / "poster.png"
    _ffmpeg(ffmpeg, ["-ss", f"{poster_seconds:.3f}", "-i", str(video), "-frames:v", "1",
                     "-update", "1", str(poster)])
    frames.append({"name": "poster", "step_id": "", "requested": poster_seconds,
                   "path": "poster.png"})
    frame_dir = output_path / "keyframes"
    frame_dir.mkdir(parents=True, exist_ok=True)
    for step in plan["timeline"]:
        # ⚠️ 取 `start + 0.5` 而不是 `start`：步首那一帧正好落在入场动画的第 0 帧上，
        # 实测是一张空白背景（`frame_not_empty:s1-0.png` 失败）。规格里的 checkpoints
        # 才是"设计好的观察时刻"，所以两处都取，但步首让出 0.5s。
        # 偏移随步长缩放：0.75s 的小步里塞 0.5s 会直接跳到 caption_out 之后
        # （实测取到空白帧），但完全取边界帧又会撞上入场动画的第 0 帧。
        moments = evidence_moments(step, frame_rate=frame_rate) if plan['format']=='visual-scene-2.0' else [
            {'at':min(step['start']+0.5,step['end']-0.05),'note':'legacy start'},
            {'at':max(step['start'],step['end']-0.3),'note':'legacy end'},
            *step['checkpoints']]
        seen: set[float] = set()
        for index, moment in enumerate(moments):
            at = round(float(moment["at"]), 3)
            if at in seen and plan["format"]!="visual-scene-2.0":
                continue
            seen.add(at)
            name = f"{step['step_id']}-{index}.png"
            _ffmpeg(ffmpeg, ["-ss", f"{at:.3f}", "-i", str(video), "-frames:v", "1",
                             "-update", "1", str(frame_dir / name)])
            frames.append({**{k:v for k,v in moment.items() if k not in ("at","note")},
                           "name": name, "step_id": step["step_id"], "requested": at,
                           "note": moment.get("note", ""), "path": f"keyframes/{name}"})
    summary = _probe_video(ffmpeg.replace("ffmpeg", "ffprobe"), video)
    return {"video": "video.mp4", "raw_video_seconds": render_seconds,
            "frames": frames, "font": font_report,
            "video_info": summary, "peak_rss_kib": _peak_rss_kib()}


def _ffmpeg(ffmpeg: str, args: list[str]) -> None:
    """跑一次 ffmpeg 后处理。

    ⚠️ `-threads 1 -filter_threads 1` 不是可选项：worker 的 `RLIMIT_AS=2GiB` 是硬限制，
    而 ffmpeg 6.1 默认按 CPU 数（本机 24）开线程、每线程各自预留帧缓冲 —— 720p 上实测
    `Error while filtering: Cannot allocate memory`（退出码 244），**同一命令不限地址空间
    就成功**。抽出关键帧本来是几毫秒的事，却会整条渲染失败；钉住线程数后 2GiB 上限内稳定通过。
    """
    proc = subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                           "-threads", "1", "-filter_threads", "1", *args],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败（退出码 {proc.returncode}）："
                           f"{proc.stdout.decode('utf-8', 'replace')[-800:]}")


def _probe_video(ffprobe: str, video: Path) -> dict:
    proc = subprocess.run([ffprobe, "-v", "error", "-print_format", "json",
                           "-show_format", "-show_streams", str(video)],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe 失败：{proc.stdout.decode('utf-8', 'replace')[-400:]}")
    return json.loads(proc.stdout.decode("utf-8"))


def _peak_rss_kib() -> int:
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1])
    except (OSError, ValueError):  # pragma: no cover - 非 Linux 或读不到
        return 0
    return 0


__all__ = ["FORMAT", "PAYLOAD_KEYS", "OBJECT_KINDS", "ACTION_KINDS", "TRANSFORMABLE",
           "ScenePlanError", "validate_payload", "plan_scene", "render"]


def evidence_moments(step, *, frame_rate):
    """Sample each targeted action on both sides; highlights also need the peak."""
    frame=1/float(frame_rate);result=[]
    binding={k:step[k] for k in ('goal_id','claim_ids','object_ids','observation') if k in step}
    for moment in step['evidence_moments']:
        at=moment['at']
        if moment['phase']=='before': at-=frame
        if moment['phase']=='after': at+=frame
        at=min(step['end']-frame,max(step['start'],at))
        result.append({**binding,**moment,'at':at,'note':moment['phase']})
    result.extend({**binding,**c,'phase':'observe'} for c in step['checkpoints'])
    return sorted(result,key=lambda m:(m['at'],m['phase']))
