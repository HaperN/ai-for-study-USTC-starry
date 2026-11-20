"""T07 适配器：`CompiledScene` → **受限** Manim 调用 → `AnimationResult`。

## 安全边界（这是本模块存在的第一理由）

适配器**只接受 T06 可信编译器产出的源码**，而且不是"看起来像"就接受：

1. `load_trusted_scene()` 用 `ast` 解析，只允许 T06 模板里那几条语句
   （docstring、`from __future__ import annotations`、三个字面量赋值、一个
   `construct` 占位函数）；出现 `import`／调用／属性访问／下标／f-string／多余语句一律拒绝。
2. `SCENE` 用 `ast.literal_eval` 取（**不 exec、不 import**），随后与
   `compiler._render_source(profile, SCENE)` 逐字节比对 —— 源码必须**恰好**是编译器
   会生成的那份。多一个空格、多一句注释都不接受。
3. 校验 `source_hash`（内容哈希）与 `spec_hash` 是否与 `CompiledScene` 一致。
4. 载荷再走一遍 `scene_builder.validate_payload` 白名单（多字段、未知对象／动作即拒绝）。

它**不接受文件路径**：没有"传一个脚本给我跑"的入口，也没有 `exec`／`eval`／`importlib`
加载模型内容的路径。中文文本只作为 Manim `Text` 的数据出现；V2 公式由批准模型的
闭合 AST 经共享格式化器生成 MathTex，并在进入 worker 前重新编译逐字节核验。

## 其余职责

* 把规格里的 `transform` 动作**在宿主侧**算好新采样（沙箱里不做数学，见 scene_builder）；
* 装配 §5.3 的 `AnimationResult`（资源 hash、步骤、数学报告、技术帧检查、环境指纹）；
* 写 UTF-8 WebVTT 与可见文字稿（纯文本，来自可信编译产物）。

语义级画面评审（模型看关键帧）属于 T08；本模块只做**技术帧检查**（可解码、非空、
字幕真的有墨、时间戳对得上），并在 `technical_report.deferred_checks` 里写明。
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...core.digest import digest_file, digest_of, digest_text
from ...errors import ContractError, ConfigError
from .compiler import COMPILER_VERSION, CompiledScene, RenderProfile, _render_source
from .math_check import verify_math
from .result import STATUSES
from .spec import VisualSpecV1, VisualSpecV2, parse_visual_spec, validate_spec_structure
from .worker import (AnimationCache, RenderLimits, WorkerServices, environment_hash,
                     file_resource, load_asset, max_observed_concurrency, probe_isolation,
                     resolve_worker_dir, run_worker_job, select_isolation, isolation_verified)

SCHEMA_VERSION = "1.0"
#: T06 可信源码模板允许的顶层语句
_ALLOWED_ASSIGNMENTS = ("COMPILER_VERSION", "PROFILE", "SCENE")
_ALLOWED_FUNCTION = "construct"


class UntrustedVisualScene(ContractError):
    """源码不是 T06 可信编译产物（或内容哈希不符）—— 拒绝执行，绝不放宽。"""


@dataclass(frozen=True)
class ManimServices:
    """宿主侧服务：worker 资产、解释器、临时根、缓存、可选取消与后端覆盖。"""

    worker_dir: Path
    interpreter: str
    scratch_root: Path
    cache_dir: Path | None = None
    ffmpeg: str = "ffmpeg"
    isolation: str | None = None
    cancel: object | None = None
    workspace_root: Path | None = None
    extra_read_only: tuple[str, ...] = field(default=())

    def worker_services(self) -> WorkerServices:
        return WorkerServices(worker_dir=self.worker_dir, interpreter=self.interpreter,
                              scratch_root=self.scratch_root, ffmpeg=self.ffmpeg,
                              isolation=self.isolation,
                              host_env={"TERM": os.environ.get("TERM", "dumb")})


def default_services(*, workspace_root: str | Path | None = None,
                     worker_dir: str | Path | None = None,
                     scratch_root: str | Path | None = None,
                     cache_dir: str | Path | None = None,
                     cancel: object | None = None,
                     isolation: str | None = None) -> ManimServices:
    """按 **显式参数 → 环境变量 → 工作区配置** 解析服务；缺项明确报错，不猜。"""
    resolved_worker = resolve_worker_dir(worker_dir, workspace_root)
    interpreter = os.environ.get("AIFORSTUDY_MANIM_PYTHON", "")
    if not interpreter and workspace_root:
        from ...providers.capabilities import resolve_manim_interpreter
        interpreter = resolve_manim_interpreter(Path(workspace_root)) or ""
    if not interpreter or not Path(interpreter).is_file():
        raise ConfigError(
            "找不到 Manim 渲染解释器（独立环境，**不是**项目 venv）。三种给法：\n"
            "  1) 显式传 interpreter\n"
            "  2) 环境变量 AIFORSTUDY_MANIM_PYTHON=<...>/.venv-manim-linux/bin/python\n"
            "  3) 在 config/capabilities.json 的 render_environment.interpreter_candidates "
            "里声明位置")
    ffmpeg = os.environ.get("AIFORSTUDY_FFMPEG") or shutil.which("ffmpeg") or ""
    if not ffmpeg:
        raise ConfigError("PATH 里没有 ffmpeg：渲染后的重封装／poster／关键帧都做不了")
    root = Path(scratch_root or os.environ.get("AIFORSTUDY_MANIM_SCRATCH")
                or (Path(os.environ.get("TMPDIR", "/tmp")) / "aiforstudy-manim"))
    cache = Path(cache_dir) if cache_dir else None
    return ManimServices(worker_dir=resolved_worker, interpreter=interpreter,
                         scratch_root=root, cache_dir=cache, ffmpeg=ffmpeg,
                         isolation=isolation, cancel=cancel,
                         workspace_root=Path(workspace_root) if workspace_root else None)


# ── 可信源码 ───────────────────────────────────────────────────────────────
def _literal(node: ast.AST, path: str) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError) as exc:
        raise UntrustedVisualScene(f"{path}: 只允许字面量，实际无法静态求值（{exc}）")


def _validate_module_shape(tree: ast.Module) -> None:
    """顶层结构白名单：T06 模板之外的一切都拒绝。"""
    body = list(tree.body)
    if not body:
        raise UntrustedVisualScene("源码为空")
    if not (isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        raise UntrustedVisualScene("第一条语句必须是模块 docstring")
    index = 1
    if index < len(body) and isinstance(body[index], ast.ImportFrom):
        node = body[index]
        if node.module != "__future__" or [alias.name for alias in node.names] != ["annotations"]:
            raise UntrustedVisualScene("只允许 `from __future__ import annotations`")
        index += 1
    seen: list[str] = []
    for node in body[index:]:
        if isinstance(node, ast.Assign):
            targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if len(targets) != 1 or targets[0] not in _ALLOWED_ASSIGNMENTS:
                raise UntrustedVisualScene(
                    f"只允许给 {list(_ALLOWED_ASSIGNMENTS)} 赋值，实际 {targets}")
            if targets[0] in seen:
                raise UntrustedVisualScene(f"{targets[0]} 重复赋值")
            seen.append(targets[0])
            _literal(node.value, targets[0])
            continue
        if isinstance(node, ast.FunctionDef):
            if node.name != _ALLOWED_FUNCTION:
                raise UntrustedVisualScene(f"只允许定义 {_ALLOWED_FUNCTION}()，实际 {node.name}()")
            if node.decorator_list or node.args.vararg or node.args.kwarg or \
                    node.args.defaults or node.args.kw_defaults or node.args.posonlyargs or \
                    node.args.kwonlyargs:
                raise UntrustedVisualScene(f"{_ALLOWED_FUNCTION}() 只允许一个位置参数 scene")
            if [arg.arg for arg in node.args.args] != ["scene"]:
                raise UntrustedVisualScene(
                    f"{_ALLOWED_FUNCTION}() 的参数必须恰好是 (scene)，"
                    f"实际 ({', '.join(arg.arg for arg in node.args.args)})")
            statements = node.body
            if len(statements) != 2 or not isinstance(statements[0], ast.Expr) or \
                    not isinstance(statements[0].value, ast.Constant):
                raise UntrustedVisualScene(f"{_ALLOWED_FUNCTION}() 只允许 docstring + 一条 raise")
            if not isinstance(statements[1], ast.Raise):
                raise UntrustedVisualScene(f"{_ALLOWED_FUNCTION}() 里只允许 raise 占位")
            seen.append(_ALLOWED_FUNCTION)
            continue
        raise UntrustedVisualScene(
            f"第 {node.lineno} 行出现不允许的语句 {type(node).__name__}："
            f"可信源码只含字面量与占位函数（禁止 import／调用／exec／任意脚本）")
    missing = [name for name in (*_ALLOWED_ASSIGNMENTS, _ALLOWED_FUNCTION) if name not in seen]
    if missing:
        raise UntrustedVisualScene(f"缺少 {missing}：这不是 T06 编译产物")


def load_trusted_scene(source: str, *, expected_source_hash: str | None = None,
                       profile: str | None = None) -> dict:
    """校验并取出 T06 可信载荷。**不 exec、不 import 任何模型内容。**"""
    if not isinstance(source, str) or not source.strip():
        raise UntrustedVisualScene("源码必须是字符串")
    if expected_source_hash and digest_text(source) != expected_source_hash:
        raise UntrustedVisualScene(
            f"源码内容哈希不符：期望 {expected_source_hash}，实际 {digest_text(source)}")
    tree = ast.parse(source)
    _validate_module_shape(tree)
    assignments: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            assignments[node.targets[0].id] = _literal(node.value, node.targets[0].id)
    if assignments.get("COMPILER_VERSION") != COMPILER_VERSION:
        raise UntrustedVisualScene(
            f"COMPILER_VERSION 不是本仓编译器版本 {COMPILER_VERSION!r}，"
            f"实际 {assignments.get('COMPILER_VERSION')!r}")
    try:
        version_profile = RenderProfile(str(assignments.get("PROFILE")))
    except ContractError as exc:
        raise UntrustedVisualScene(f"PROFILE 非法：{exc}") from None
    payload = assignments.get("SCENE")
    if not isinstance(payload, dict):
        raise UntrustedVisualScene("SCENE 必须是字面量字典")
    if profile is not None and version_profile.name != profile:
        raise UntrustedVisualScene(
            f"源码档位 {version_profile.name!r} 与请求 {profile!r} 不一致")
    canonical = _render_source(version_profile, payload)
    if canonical != source:
        raise UntrustedVisualScene(
            "源码与 T06 编译器对同一载荷生成的结果**不是逐字节相同**："
            "存在额外代码、注释或字段改动，拒绝执行")
    return payload


def compile_plan(scene: CompiledScene, spec: VisualSpecV1 | dict, *, worker_dir: str | Path,
                 profile: str | None = None, math_context=None, cancel=None) -> dict:
    """`CompiledScene` + 规格 → 可渲染计划（含 transform 的新采样）。"""
    if not isinstance(scene, CompiledScene):
        raise ContractError("compile_plan: 需要 CompiledScene")
    if isinstance(spec, dict):
        spec = parse_visual_spec(spec)
    spec = validate_spec_structure(spec)
    if spec.spec_hash != scene.spec_hash:
        raise ContractError(
            f"规格与编译产物不匹配：spec_hash {spec.spec_hash} ≠ {scene.spec_hash}")
    profile = profile or scene.profile
    payload = load_trusted_scene(scene.source, expected_source_hash=scene.source_hash,
                                 profile=profile)
    if payload.get("profile") != profile:
        raise ContractError(f"载荷档位 {payload.get('profile')!r} 与请求 {profile!r} 不一致")
    if isinstance(spec, VisualSpecV2):
        from .compiler import compile_scene
        canonical=compile_scene(spec,profile,math_context=math_context,cancel=cancel)
        if canonical.source != scene.source:
            raise UntrustedVisualScene("可信 V2 重新编译 canonical 字节不匹配")
    builder = load_asset(worker_dir, "scene_builder")
    plan = builder.plan_scene(payload, profile=profile)
    _enrich_objects(plan, spec)
    if not isinstance(spec, VisualSpecV2):
        _enrich_transforms(plan, spec, payload)
    return plan


def _enrich_objects(plan: dict, spec: VisualSpecV1) -> None:
    """把规格里的**颜色／标签**带进画面。

    T06 的编译产物只带几何（`compiler._compile_object` 有意丢掉样式），而画面要能区分
    "固定 y 的截线（红）"与"固定 x 的截线（绿）"。这里从**已校验的规格**取样式，
    仍然是数据搬运：不新增数学、不接受模型新文本。
    """
    for object_id, compiled in plan["objects"].items():
        try:
            source = spec.object_by_id(object_id)
        except ContractError:                                    # pragma: no cover
            continue
        if "color" in source:
            compiled["color"] = source["color"]
        if "label" in source:
            compiled["label"] = source["label"]


def _enrich_transforms(plan: dict, spec: VisualSpecV1, payload: dict) -> None:
    """把 `transform` 的新采样在宿主侧算好（沙箱内不做数学）。

    复用 T06 编译器的采样函数（`_sample_surface`）而不是另写一份：两份采样代码
    迟早会给出不同的画面，而画面就是教学内容。
    """
    from . import compiler as compiler_module

    steps_by_id = {step["step_id"]: step for step in payload["steps"]}
    for planned in plan["timeline"]:
        source_step = steps_by_id[planned["step_id"]]
        for position, action in enumerate(source_step["actions"]):
            if action["action"] != "transform":
                continue
            target = spec.object_by_id(action["object_id"])
            resolution = list(target.get("resolution") or [16, 16])
            domain = action.get("domain") or target["domain"]
            sampled = compiler_module._sample_surface(
                {"kind": target["kind"], "expr": action["expr"], "domain": domain,
                 "resolution": resolution}, spec.parameters)
            for part in planned["parts"]:
                if part.get("action") == "transform" and \
                        part.get("object_id") == action["object_id"]:
                    part.update({"u": sampled["u"], "x_values": sampled["x_values"],
                                 "y_values": sampled["y_values"]})
                    break
            del position


# ── 帧的技术检查（语义评审属 T08）──────────────────────────────────────────
def _ffmpeg_run(ffmpeg: str, args: list[str], timeout: float = 600.0) -> tuple[int, str]:
    proc = subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", *args],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")[-2000:]


def probe_pixels(ffmpeg: str, image: str | Path, *, band: tuple[float, float] | None = None,
                 threshold: int = 200) -> dict:
    """量一张 PNG 的墨迹：整幅与（可选）指定纵向区间的包围盒高度。

    用 ffmpeg 解码成灰度原始帧，纯 Python 统计 —— 不引入新的图像库依赖，
    也让"像素非空"这句话有一个可复核的数字。
    """
    path = Path(image)
    if not path.is_file():
        return {"ok": False, "reason": f"缺文件 {path}"}
    probe = subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-i", str(path), "-f",
                            "rawvideo", "-pix_fmt", "gray", "-"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if probe.returncode != 0:
        return {"ok": False, "reason": probe.stderr.decode("utf-8", "replace")[-200:]}
    dimensions = _image_size(ffmpeg, path)
    if not dimensions:
        return {"ok": False, "reason": "取不到图像尺寸"}
    width, height = dimensions
    raw = probe.stdout
    if len(raw) != width * height:
        return {"ok": False, "reason": f"灰度原始帧长度 {len(raw)} ≠ {width * height}"}
    low, high = 0, height
    if band:
        low = max(0, int(band[0] * height))
        high = min(height, int(band[1] * height))

    def ink(row: int, column: int) -> bool:
        return raw[row * width + column] < threshold

    rows = [row for row in range(low, high) if any(ink(row, column) for column in range(width))]
    columns = [column for column in range(width)
               if any(ink(row, column) for row in range(low, high))]
    ink_pixels = sum(1 for row in range(low, high) for column in range(width)
                     if ink(row, column))
    report = {"ok": True, "width": width, "height": height, "band": [low, high],
              "ink_pixels": ink_pixels, "ink_ratio": round(ink_pixels / max(1, width * (high - low)), 6)}
    if rows:
        report["ink_rows"] = [min(rows), max(rows)]
        report["ink_height"] = max(rows) - min(rows) + 1
    else:
        report["ink_rows"] = []
        report["ink_height"] = 0
    if columns:
        report["ink_columns"] = [min(columns), max(columns)]
        report["ink_width"] = max(columns) - min(columns) + 1
    else:
        report["ink_columns"] = []
        report["ink_width"] = 0
    return report


def _image_size(ffmpeg: str, path: Path) -> tuple[int, int] | None:
    probe = subprocess.run([ffmpeg, "-nostdin", "-i", str(path)],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
    text = probe.stdout.decode("utf-8", "replace")
    import re
    match = re.search(r",\s*(\d{2,5})x(\d{2,5})", text)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None


def faststart_ok(video: str | Path) -> bool:
    """`moov` 是否在 `mdat` 之前（faststart 的**实际**判据，不看命令行参数）。"""
    with Path(video).open("rb") as handle:
        head = handle.read(1 << 20)
    moov = head.find(b"moov")
    mdat = head.find(b"mdat")
    return moov != -1 and (mdat == -1 or moov < mdat)


def technical_frame_checks(*, video: Path, poster: Path, frames: list[dict],
                           ffmpeg: str, ffprobe: str, expected: dict) -> dict:
    """技术帧检查：可完整解码、编码参数、faststart、poster／关键帧非空、字幕有墨。"""
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str, **extra) -> None:
        checks.append({"check": name, "status": "pass" if ok else "fail", "detail": detail,
                       **extra})

    decode_code, decode_log = _ffmpeg_run(ffmpeg, ["-i", str(video), "-f", "null", "-"])
    check("full_decode", decode_code == 0,
          "ffmpeg -f null - 完整解码" if decode_code == 0 else f"解码失败：{decode_log[-400:]}")
    info = json.loads(subprocess.run(
        [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
         str(video)], stdout=subprocess.PIPE, timeout=120).stdout.decode("utf-8"))
    video_streams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    audio_streams = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    stream = video_streams[0] if video_streams else {}
    from ..media.narration import audio_stream_problems
    audio_errors = audio_stream_problems(audio_streams, video_seconds=float((info.get('format') or {}).get('duration') or 0))
    check("audio_policy", not audio_errors, "；".join(audio_errors) or f"合法可选音轨数 {len(audio_streams)}")
    check("codec", stream.get("codec_name") == "h264", f"codec={stream.get('codec_name')}")
    check("pix_fmt", stream.get("pix_fmt") == "yuv420p", f"pix_fmt={stream.get('pix_fmt')}")
    check("size", (stream.get("width"), stream.get("height")) ==
          (expected["pixel_width"], expected["pixel_height"]),
          f"{stream.get('width')}x{stream.get('height')} 期望 "
          f"{expected['pixel_width']}x{expected['pixel_height']}")
    rate = str(stream.get("r_frame_rate") or "")
    check("frame_rate", rate == f"{expected['frame_rate']}/1", f"r_frame_rate={rate}")
    with Path(video).open("rb") as handle:
        head = handle.read(1 << 20)
    check("faststart", faststart_ok(video),
          "moov 在 mdat 之前" if faststart_ok(video) else
          f"moov@{head.find(b'moov')} mdat@{head.find(b'mdat')}")
    seconds = float((info.get("format") or {}).get("duration") or 0.0)
    # 硬判据：成片时长必须与规格自己的时间线一致（± 一帧多一点）。
    check("video_seconds", expected["min_seconds"] <= seconds <= expected["max_seconds"],
          f"时长 {seconds}s 期望 [{expected['min_seconds']}, {expected['max_seconds']}]",
          value=seconds)
    from ..media.presentation_policy import MAX_ANIMATION_SECONDS
    # Encoding can round up by one frame; the authoritative timeline already
    # passed strict <=90 seconds validation before any worker execution.
    check("duration_resource_limit", 0 < seconds <= MAX_ANIMATION_SECONDS + 1 / expected["frame_rate"],
          f"时长 {seconds}s，宿主资源上限 {MAX_ANIMATION_SECONDS}s（编码一帧容差）")

    poster_report = probe_pixels(ffmpeg, poster)
    check("poster_not_empty", bool(poster_report.get("ok")) and
          poster_report.get("ink_pixels", 0) > 0,
          f"poster 墨迹像素 {poster_report.get('ink_pixels')}", detail_report=poster_report)
    distinct = set()
    for frame in frames:
        if frame["name"] == "poster":
            continue
        report = probe_pixels(ffmpeg, frame["path"])
        frame["ink"] = {"ink_pixels": report.get("ink_pixels"),
                        "ink_height": report.get("ink_height")}
        distinct.add(digest_file(frame["path"]))
        check(f"frame_not_empty:{frame['name']}",
              bool(report.get("ok")) and (report.get("ink_pixels", 0) > 0 or frame.get("phase")=="before"),
              f"墨迹像素 {report.get('ink_pixels')}")
        # Captions are a switchable external VTT track. Pixels in the lower
        # frame may be graph axes, so they cannot certify subtitle legibility.
        frame['caption_mode'] = 'external_vtt'
    checks.append({"check": "caption_presentation", "status": "note",
                   "detail": "external_vtt：字幕大小与开关由真实浏览器验收，不从图形墨迹推断"})
    check("frames_distinct", len(distinct) >= min(2, max(1, len(frames) - 1)),
          f"关键帧内容互不相同的有 {len(distinct)} 张")
    if expected.get("has_formula"):
        # 公式必须**真的在画面上**：3D 场景里文字不固定于画面时会飘到画外，
        # 而"字幕里有公式"和"画面上有公式"是两件事（后者才影响看懂）。
        top_band_ink = 0
        for frame in frames:
            if frame["name"] == "poster":
                continue
            band = probe_pixels(ffmpeg, frame["path"], band=(0.0, 0.22))
            top_band_ink = max(top_band_ink, int(band.get("ink_pixels") or 0))
        check("formula_on_screen", top_band_ink > 0,
              f"画面上半部墨迹像素 {top_band_ink}（公式应固定在画面上）")
    # `note` 只记录事实；只有 fail 才算技术检查不合格。
    failed = [item for item in checks if item["status"] == "fail"]
    return {"status": "pass" if not failed else "fail",
            "checks": checks, "failed": [item["check"] for item in failed],
            "review_status": "not_run",
            "review_owner": "T08",
            "note": "本阶段只做技术帧检查；语义画面评审（模型看关键帧）由 T08 的 frame_check 完成"}


# ── 字幕 / 文字稿 ──────────────────────────────────────────────────────────
def _stamp(seconds: float) -> str:
    total = max(0.0, float(seconds))
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    rest = total % 60
    return f"{hours:02d}:{minutes:02d}:{rest:06.3f}"


def write_vtt(path: Path, plan: dict) -> str:
    """UTF-8 WebVTT。一行一条字幕，时间与规格的 step 起止**逐字对应**。"""
    lines = ["WEBVTT", ""]
    for index, step in enumerate(plan["timeline"], start=1):
        lines.append(str(step["step_id"]))
        lines.append(f"{_stamp(step['start'])} --> {_stamp(step['end'])}")
        from ...checks.offline import webvtt_cue_text
        lines.append(webvtt_cue_text(str(step["caption"]).strip()))
        lines.append("")
    text = "\n".join(lines)
    path.write_text(text, encoding="utf-8")
    return text


def write_transcript(path: Path, plan: dict, *, request_id: str, profile: str) -> str:
    """可见文字稿：字幕全文 + 每步观察点 + 公式 + 数学值（屏幕外也能读全）。"""
    lines = [f"# {request_id}（{profile}）动画文字稿", "",
             f"总时长：{plan['total_seconds']:.1f} 秒", ""]
    if plan.get("formulas"):
        lines += ["## 画面公式", *[f"- {item}" for item in plan["formulas"]], ""]
    lines.append("## 分步字幕与观察点")
    for index, step in enumerate(plan["timeline"], start=1):
        lines.append(f"{index}. [{_stamp(step['start'])} → {_stamp(step['end'])}] "
                     f"{step['caption']}（步骤 {step['step_id']}）")
        if step.get("expected_change"):
            lines.append(f"   - 预期变化：{step['expected_change']}")
        for checkpoint in step.get("checkpoints", []):
            note = checkpoint.get("note") or "（无说明）"
            lines.append(f"   - 检查点 {checkpoint['at']}s：{note}")
    values = plan.get("math_values") or {}
    if values:
        lines += ["", "## 由受限 AST 算出的数学值（独立复核见 math_report）"]
        for key in sorted(values):
            lines.append(f"- {key} = {values[key]}")
    text = "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8")
    return text


# ── 主入口 ─────────────────────────────────────────────────────────────────
def limits_for_profile(worker_dir: str | Path, profile: str, *,
                       parent_deadline: float | None = None) -> RenderLimits:
    """§6.1 的上限来自 worker 资产里的 policy（**唯一一份数字**）。"""
    policy = load_asset(worker_dir, "policy")
    if profile not in policy.PROFILES:
        raise ContractError(f"profile: 只能是 {sorted(policy.PROFILES)}，实际 {profile!r}")
    quality = policy.PROFILES[profile]
    return RenderLimits(timeout_seconds=float(quality["timeout_seconds"]),
                        cpu_cores=int(policy.CPU_CORES),
                        memory_bytes=int(policy.MEMORY_BYTES),
                        output_bytes=int(policy.OUTPUT_BYTES),
                        parent_deadline=parent_deadline)


def build_source_binding(spec: VisualSpecV1, scene: CompiledScene) -> dict:
    if isinstance(spec, VisualSpecV2):
        return {"unit_id":spec.unit_id,"request_id":spec.request_id,"spec_hash":scene.spec_hash,
                "source_hash":scene.source_hash,"math_binding":scene.samples["math_binding"]}
    return {"unit_id": spec.unit_id, "request_id": spec.request_id,
            "lesson_anchor": spec.lesson_anchor,
            "source_refs": list(spec.source_refs),
            "source_claims": list(spec.source_claims),
            "dependency_paths": list(spec.dependency_paths),
            "spec_hash": scene.spec_hash, "source_hash": scene.source_hash}


def render_scene(scene: CompiledScene, limits: RenderLimits, services: ManimServices | None = None,
                 *, spec: VisualSpecV1 | dict, profile: str | None = None,
                 output_dir: str | Path | None = None, cache: AnimationCache | None = None,
                 require_visual_review: bool = False,
                 environment: dict | None = None, math_context=None) -> dict:
    """§5.3 的 `render_scene`：受限编译产物 → `AnimationResult`（dict，可 JSON 化）。

    `environment` 可传一次已探测的环境指纹（同一 run 里只探一次），
    不传就在本次渲染前探一次（隔离 + 工具版本 + 字体 + MathTex 自检）。
    """
    if isinstance(spec, dict):
        spec = parse_visual_spec(spec)
    spec = validate_spec_structure(spec)
    profile = profile or scene.profile
    services = services or default_services()
    worker_dir = Path(services.worker_dir)
    plan = compile_plan(scene, spec, worker_dir=worker_dir, profile=profile,
                        math_context=math_context, cancel=services.cancel)
    started = time.monotonic()

    if environment is None:
        environment = probe_isolation(services=services.worker_services(), limits=limits,
                                      collect_environment=True,
                                      probe_paths=[str(Path.home() / ".bashrc")],cancel=services.cancel)
    environment_digest = environment_hash(environment, profile=profile)
    isolation = environment.get("isolation") or {}
    base = {
        "schema_version": SCHEMA_VERSION, "request_id": spec.request_id,
        "spec_hash": scene.spec_hash,
        "compiler_hash": digest_of({"compiler_version": scene.compiler_version,
                                    "source_hash": scene.source_hash}),
        "environment_hash": environment_digest,
        "source_binding": build_source_binding(spec, scene),
        "math_report": ({"status":"pass", "schema_version":"VisualMathReportV2",
                         "reports":plan["math_binding"]["reports"],
                         "context_digest":plan["math_binding"]["context_digest"]}
                        if isinstance(spec, VisualSpecV2) else verify_math(spec).as_dict()),
        "resources": [], "steps": [],
    }
    probe_outcome=environment.get('outcome') or {}
    if probe_outcome.get('status') and probe_outcome['status']!='ok':
        # A timed-out/crashed probe has already been terminated by the same
        # worker monitor. Preserve that receipt instead of misreporting its
        # missing isolation payload as an unavailable isolation facility.
        status={'cancelled':'cancelled','blocked':'blocked'}.get(probe_outcome['status'],'failed')
        return _finalize(base,status,plan=plan,technical={
            'worker':probe_outcome,'isolation':isolation,'phase':'environment_probe',
            'kind':probe_outcome.get('kind') or 'environment_probe',
            'reason':probe_outcome.get('reason') or '动画环境预检未完成，未渲染或提交缓存',
            'deferred_checks':['visual_semantic_review(T08)']})
    if not isolation_verified(isolation):
        return _finalize(base, "blocked", plan=plan, technical={
            "kind": "isolation", "reason": isolation.get("reason") or "隔离设施不可用",
            "isolation": isolation, "deferred_checks": ["visual_semantic_review(T08)"]})

    typography=(environment.get('environment') or {}).get('math_tex') or {}
    if isinstance(spec, VisualSpecV2) and not (typography.get('ok') and typography.get('frame_hash')):
        return _finalize(base,'blocked',plan=plan,technical={'kind':'typography','reason':'混排实帧预检缺失，禁止启动动画渲染'})

    output = Path(output_dir or (Path(services.scratch_root) / "out"))
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    job_id = f"render-{digest_text(scene.source_hash)[7:19]}-{uuid.uuid4().hex[:12]}"
    quality = _quality(worker_dir, profile)
    policy = load_asset(worker_dir, "policy")
    job = {"schema": "manim-worker-job-1.0", "mode": "render", "profile": profile,
           "scratch": str(Path(services.scratch_root) / job_id / "scratch"),
           "output": str(output), "plan": plan,
           "limits": {"pixel_width": int(quality["pixel_width"]),
                      "pixel_height": int(quality["pixel_height"]),
                      "frame_rate": int(quality["frame_rate"])},
           "font": {"family": str(policy.FONT_FAMILY), "file": str(policy.FONT_FILE)},
           "ffmpeg": services.ffmpeg,
           # 海报取**第一个检查点**（规格里设计好的观察时刻），而不是固定 1.0s：
           # 1.0s 常落在入场动画中间，实测海报上只有坐标轴、没有曲面。
           "poster_seconds": _poster_seconds(plan),
           "host_netns": _netns()}
    outcome = run_worker_job(services=services.worker_services(), job=job, limits=limits,
                             job_id=job_id, cancel=services.cancel)
    technical: dict[str, Any] = {
        "worker": {key: outcome.get(key) for key in
                   ("status", "kind", "reason", "terminated_by", "orphans_remaining",
                    "wall_seconds", "peak_rss_bytes", "output_bytes", "log_path",
                    "concurrency_observed", "isolation", "cache_committed")},
        "isolation": isolation, "limits": {"timeout_seconds": limits.timeout_seconds,
                                           "cpu_cores": limits.cpu_cores,
                                           "memory_bytes": limits.memory_bytes,
                                           "output_bytes": limits.output_bytes},
        "max_worker_concurrency": max_observed_concurrency(),
        "environment": environment.get("environment", {}),
        "environment_fingerprint_path": environment.get("environment_path", ""),
        "deferred_checks": ["visual_semantic_review(T08)"]}

    if outcome["status"] != "ok":
        status = {"cancelled": "cancelled", "blocked": "blocked"}.get(outcome["status"], "failed")
        if outcome.get("terminated_by", "").startswith("cancelled"):
            status = "cancelled"
        return _finalize(base, status, plan=plan, technical=technical,
                         resources=_collect_resources(output, outcome))

    checks = technical_frame_checks(
        video=output / "video.mp4", poster=output / "poster.png",
        frames=_frame_entries(output, outcome), ffmpeg=services.ffmpeg,
        ffprobe=shutil.which("ffprobe") or "ffprobe",
        expected={"pixel_width": quality["pixel_width"], "pixel_height": quality["pixel_height"],
                  "frame_rate": quality["frame_rate"],
                  "min_seconds": max(0.0, plan["total_seconds"] - 0.5),
                  "max_seconds": plan["total_seconds"] + 0.8,
                  "has_formula": bool(plan.get("formulas")),
                  "font_min_pixels": quality["font_min_pixels"]})
    vtt_path = output / "captions.vtt"
    transcript_path = output / "transcript.txt"
    write_vtt(vtt_path, plan)
    write_transcript(transcript_path, plan, request_id=spec.request_id, profile=profile)
    resources = _collect_resources(output, outcome)
    technical["ffmpeg_decode_ok"] = next(c["status"] == "pass" for c in checks["checks"] if c["check"]=="full_decode")
    technical["size_ok"] = next(c["status"] == "pass" for c in checks["checks"] if c["check"]=="size")
    technical["duration_ok"] = all(c["status"] == "pass" for c in checks["checks"] if c["check"] in ("video_seconds","duration_resource_limit"))
    technical["video_info"] = _video_info(output / "video.mp4")
    technical["frame_checks"] = checks
    technical["wall_seconds_total"] = round(time.monotonic() - started, 3)
    math_report = base["math_report"]
    if checks["status"] != "pass":
        status = "failed"
    elif math_report.get("status") != "pass":
        status = "failed" if math_report.get("status") == "fail" else "needs_review"
    elif require_visual_review or isinstance(spec, VisualSpecV2):
        status = "needs_review"
    else:
        status = "fulfilled"
    result = _finalize(base, status, plan=plan, technical=technical, resources=resources,
                       frame_report=checks)
    if cache is not None and status == "fulfilled":
        key = AnimationCache.key(spec_hash=scene.spec_hash,
                                 compiler_hash=base["compiler_hash"],
                                 environment_hash=environment_digest, profile=profile)
        manifest = cache.commit(key, source_dir=output, resources=resources,
                                extra={"request_id": spec.request_id, "profile": profile,
                                       "status": status})
        result["technical_report"]["cache_committed"] = True
        result["technical_report"]["cache_key"] = key
        result["technical_report"]["cache_dir"] = manifest["cache_dir"]
    return result


def _poster_seconds(plan: dict) -> float:
    """海报时刻：第一步的第一个检查点，否则取第一步中点。"""
    steps = plan.get("timeline") or []
    if not steps:
        return 1.0
    first = steps[0]
    for checkpoint in first.get("checkpoints", []):
        return float(checkpoint["at"])
    return float(first["start"] + first["end"]) / 2.0


def _netns() -> str:
    try:
        return os.readlink("/proc/self/ns/net")
    except OSError:                                              # pragma: no cover
        return ""


def _quality(worker_dir: Path, profile: str) -> dict:
    policy = load_asset(worker_dir, "policy")
    quality = dict(policy.PROFILES[profile])
    quality["font_min_pixels"] = policy.FONT_MIN_PIXELS if quality["pixel_height"] >= 720 else 12
    return quality


def _frame_entries(output: Path, outcome: dict) -> list[dict]:
    entries = []
    result = outcome.get("result") or {}
    for frame in result.get("frames", []):
        path = output / str(frame["path"])
        entries.append({**frame, "path": path,
                        "sha256": digest_file(path) if path.is_file() else ""})
    return entries


def _collect_resources(output: Path, outcome: dict | None = None) -> list[dict]:
    resources: list[dict] = []
    frame_meta = {str(f["path"]): f for f in ((outcome or {}).get("result") or {}).get("frames", [])}
    video = output / "video.mp4"
    if video.is_file():
        resources.append(file_resource(video, kind="mp4"))
    poster = output / "poster.png"
    if poster.is_file():
        resources.append(file_resource(poster, kind="poster"))
    frame_dir = output / "keyframes"
    if frame_dir.is_dir():
        for frame in sorted(frame_dir.glob("*.png")):
            resource = file_resource(frame, kind="keyframe", relative_path=f"keyframes/{frame.name}")
            meta = frame_meta.get(resource["relative_path"])
            if meta is not None:
                resource.update(timestamp=float(meta["requested"]), step_id=meta["step_id"])
                resource.update({k:meta[k] for k in ("phase","object_id","target_object_id","action","goal_id","claim_ids","object_ids","observation") if k in meta})
            resources.append(resource)
    vtt = output / "captions.vtt"
    if vtt.is_file():
        resources.append(file_resource(vtt, kind="captions"))
    transcript = output / "transcript.txt"
    if transcript.is_file():
        resources.append(file_resource(transcript, kind="transcript"))
    return resources


def _video_info(video: Path) -> dict:
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    proc = subprocess.run([ffprobe, "-v", "error", "-print_format", "json", "-show_format",
                           "-show_streams", str(video)], stdout=subprocess.PIPE, timeout=120)
    return json.loads(proc.stdout.decode("utf-8"))


def _finalize(base: dict, status: str, *, plan: dict, technical: dict,
              resources: list[dict] | None = None, frame_report: dict | None = None) -> dict:
    if status not in STATUSES:                                   # pragma: no cover
        raise ContractError(f"未知状态 {status!r}")
    result = {**base, "status": status,
              "resources": resources if resources is not None else base.get("resources", []),
              "steps": _steps(plan, resources or []),
              "frame_report": frame_report or {"status": "not_run", "checks": [],
                                               "review_status": "not_run",
                                               "review_owner": "T08",
                                               "note": "渲染未完成，技术帧检查未运行"},
              "technical_report": technical}
    return result


def _steps(plan: dict, resources: list[dict]) -> list[dict]:
    by_relative = {item["relative_path"]: item for item in resources}
    steps = []
    for step in plan["timeline"]:
        keyframes = [item for item in resources
                     if item["kind"] == "keyframe" and
                     item["relative_path"].split("/")[-1].startswith(f"{step['step_id']}-")]
        steps.append({
            "step_id": step["step_id"], "start": step["start"], "end": step["end"],
            **{k:step[k] for k in ("kind","goal_id","claim_ids","math_claim_refs","object_ids","observation","caption_claim_ids","evidence_moments") if k in step},
            "caption": step["caption"], "expected_change": step.get("expected_change", ""),
            "checkpoints": step.get("checkpoints", []),
            "keyframes": [{"relative_path": item["relative_path"],
                           "sha256": by_relative[item["relative_path"]]["sha256"]}
                          for item in keyframes],
        })
    return steps


def load_spec_file(path: str | Path) -> VisualSpecV1:
    """读一份 T06 规格 JSON（测试与本地小样用；生产由上游直接把规格传进来）。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return parse_visual_spec(data)


def load_compiled_source(path: str | Path, *, expected_source_hash: str | None = None,
                         profile: str | None = None) -> dict:
    """读一份 T06 **可信源码文件**并校验（夹具用）。仍然只走 `load_trusted_scene`。"""
    source = Path(path).read_text(encoding="utf-8")
    return load_trusted_scene(source, expected_source_hash=expected_source_hash,
                              profile=profile)


__all__ = ["SCHEMA_VERSION", "STATUSES", "UntrustedVisualScene", "ManimServices",
           "default_services", "load_trusted_scene", "compile_plan", "probe_pixels",
           "faststart_ok", "technical_frame_checks", "write_vtt", "write_transcript",
           "limits_for_profile", "build_source_binding", "render_scene", "load_spec_file",
           "load_compiled_source", "select_isolation", "environment_hash"]
