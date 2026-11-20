"""受限数学场景块（T06）。

三层各管一件事，互不越界：

| 模块 | 职责 | 不做 |
|---|---|---|
| `spec.py` | `VisualSpecV1` / `TeachingPlanV1` 的受限 AST 与结构校验 | 不算几何、不渲染 |
| `compiler.py` | 可信编译：数值采样 + 可信 Python 源码 | 不调用模型、不写文件 |
| `math_check.py` | **独立**多项式求导与数值交叉检查 | 不复用编译层的答案函数 |
| `frame_check.py` | **实际帧**检查（结构／像素／动作／字幕／视觉分开记） | 不假装看过画面：没注入视觉评审就只能是 needs_review |
| `result.py` | `AnimationResult` 冻结契约（§5.3 schema 1.0）与 `fulfilled` 前置条件 | 不读文件系统：字节与 ffprobe 交给 `checks/offline.py` |

边界来自计划 §5.3／§5.4 与调研 §1.3／§4.2：模型只给受限数学对象与分镜，
坐标系、采样、公式排版、导数验证都由可信层完成；能力不支持时明确
`unsupported`／blocked，**不换另一道题**。
"""
from __future__ import annotations

from .compiler import (COMPILER_VERSION, CompiledScene, RenderProfile, compile_scene,
                       eval_ast, fmt_number, print_formula)
from .frame_check import (FrameCheck, FrameFinding, FrameReport, accepts_images,
                          finding_from_verdict, image_inputs, is_camera_only,
                          owner_for_kind, select_frames, verify_frames)
from .result import (RESOURCE_KINDS, RESOURCE_MIMES, AnimationResource, AnimationResult,
                     collect_resources, load_animation_result, parse_animation_result,
                     validate_against_files)
from .math_check import MATH_TOLERANCE, MathCheck, MathReport, verify_math
from .spec import (FROZEN_FIELDS, SCHEMA_VERSION, TeachingPlanV1, UnsupportedVisualSpec,
                   VisualSpecV1, parse_teaching_plan, parse_visual_spec,
                   validate_spec_structure)

__all__ = [
    "SCHEMA_VERSION", "FROZEN_FIELDS", "MATH_TOLERANCE", "COMPILER_VERSION",
    "UnsupportedVisualSpec", "VisualSpecV1", "TeachingPlanV1", "parse_visual_spec",
    "parse_teaching_plan", "validate_spec_structure", "RenderProfile", "CompiledScene",
    "compile_scene", "eval_ast", "print_formula", "fmt_number", "MathCheck", "MathReport",
    "verify_math", "FrameCheck", "FrameReport", "FrameFinding", "verify_frames",
    "select_frames", "image_inputs", "accepts_images", "finding_from_verdict",
    "owner_for_kind", "is_camera_only",
    "AnimationResource", "AnimationResult", "RESOURCE_KINDS", "RESOURCE_MIMES",
    "parse_animation_result", "collect_resources", "validate_against_files",
    "load_animation_result",
]
