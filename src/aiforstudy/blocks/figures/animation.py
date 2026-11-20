"""`figures` 请求 → **AnimationResult** 分流（计划 §T08）。

## 为什么需要"分流"而不是"改造旧路"

已删除的旧路（`blocks/figures/declarative.py`，D10 清扫）返回含义曾是 **SVG 图规格 → 一个 SVG 资源**（`state`
枚举、`resources[].kind == "svg"`）。T07 引入了媒体的冻结契约 `AnimationResult`
（mp4／poster／关键帧／VTT／文字稿 + 数学报告 + 画面报告 + 技术报告）。把两者塞进
同一个返回值，会让"这张图到底交付了什么"变成调用方猜的语法 —— 而图与视频的失败语义
根本不同（图没有"每一帧都解码得出来"这件事）。所以：

* **只有显式声明是动画的请求**才走这条分流（`request_kind` 只认白名单字面量，
  绝不因为"这一步有动作"就猜成动画）；
* 绘图（SVG）请求**不再走那条旧路**（`declarative.py` 已删除）：现在统一由"模型写代码"产出，
  该能力写死在 `pipeline/media_stages.py:136`、分派在 `:804`；
* 每个显式动画请求**恰好一个** `AnimationResult`；身份重复直接拒绝，
  不许"静默多跑一次动画"（那既花钱又会让播放器拿到两份媒体）。

## 三条不许含糊的规矩（§5.3／§9／§15 A26）

1. **缺环境不能记 `not_applicable`**：`not_applicable` 的语义是"这件事按政策**不需要**做"，
   而"这台机器没装渲染环境"是 `blocked`。拿 `not_applicable` 记缺环境，会让"该出的视频
   一张没出"在报告上看起来完全正常 —— 所以 `render_environment()` 判不可用就直接
   `status="blocked"`，并带上缺哪几项能力。
2. **没有动画请求就不建视频**：`plan_media()` 只按**显式声明**产出动画清单；
   没有任何声明的单元就是空元组，渲染器一次都不会被调用。绝不为"所有章节各出一条视频"
   而生成。
3. **视觉评审必须是**真的能收图片的传输：`run_media_pipeline()` 在**任何渲染之前**
   做 `require_image_input()` 能力检查 —— 只吃 JSON 的假评审当场 `CapabilityError`，
   不静默退成"照着规格评审"。

## draft → 关键帧审核 → 必要局部 patch → final

`run_media_pipeline()` 把 §T08 的返修节奏写成代码：

* draft 渲染 → **对着真实关键帧**评审 → 有阻塞发现且还有内容轮次 → `patch()` 做
  **局部**修改（只许改被发现的 step／object，越界报错）→ 再渲染 draft → 再评审；
* 然后渲 **final**：规格与最后一次 draft **完全相同**，所以这是**编码档位变化**，
  `content_rounds_used` **不加**（内容返修批次不因改档位而消耗）；
* final **重新评审自己那批关键帧**（`FrameReport.frame_hashes` 是 final 的哈希）——
  draft 合格不能替 final 签字。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from ...core.digest import digest_of
from ...errors import CapabilityError, ContractError
from ..visual.frame_check import FrameFinding, FrameReport, verify_frames
from ..visual.result import AnimationResult, parse_animation_result

#: 显式声明动画请求的字面量（`kind` / `media` / `type` / `request_kind` 任一命中）
ANIMATION_REQUEST_KINDS = ("animation", "manim", "video")
#: 规格载体键（请求自带规格时）
ANIMATION_SPEC_KEYS = ("animation_spec", "spec")

#: ⚠️ **已停用**（D10 清扫，2026-10-11 标注）：绘图（SVG）请求不再经由这条路分流。
#: 旧渲染器 `blocks/figures/declarative.py` 已删除，绘图/动画请求现在统一由"模型写代码"
#: 产出（`pipeline/media_stages.py:136` 写死能力、`:804` 分派）。
#: 生产代码里**没有任何消费方**；保留它只是为了 `route_request` 的返回契约与既有路由断言。
#: 要彻底清掉，必须先改 `route_request` 的返回语义（含 `tests/test_animation_pipeline.py` 的路由断言）。
ROUTE_SVG = "svg"
ROUTE_ANIMATION = "animation"
#: `AnimationResult.status` → `FigureResult.state`（M5 图请求终态）
_TERMINAL_STATES = {"fulfilled": "fulfilled", "not_applicable": "not_applicable",
                    "blocked": "blocked", "failed": "failed", "cancelled": "failed",
                    "needs_review": "blocked"}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


# --------------------------------------------------------------------------- #
# 1. 分流
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FigureRoute:
    """一条图请求的**去向**：`svg`（旧路，语义不变）或 `animation`（T07 媒体契约）。"""
    request_id: str
    destination: str
    reason: str

    def as_dict(self) -> dict:
        return {"request_id": self.request_id, "destination": self.destination,
                "reason": self.reason}


def request_identity(request: Any, *, unit_id: str = "", scope: str = "",
                     index: int = 1) -> str:
    """请求身份：显式 `request_id` / `id` 优先，否则按 `unit:scope:index`，最后按内容指纹。

    与旧 `figures_stage` 的规则一致（它用 `request_id` → `id` → `f"{unit}:{scope}:{n}"`），
    这样分流前后**同一个请求还是同一个 id**，不会因为走了新路就换一个身份。
    """
    if isinstance(request, Mapping):
        for key in ("request_id", "id"):
            value = str(request.get(key) or "").strip()
            if value:
                return value
    if unit_id:
        if is_animation_request(request):
            return "vis-" + digest_of({"unit": unit_id, "scope": scope, "index": index})[7:31]
        return f"{unit_id}:{scope or 'request'}:{index}"
    return "request-" + digest_of(request if isinstance(request, Mapping) else str(request))[7:19]


def request_kind(request: Any) -> str:
    """显式媒体种类。**只认显式声明**，不做任何推断（§T08）。"""
    if not isinstance(request, Mapping):
        return ""
    for key in ("kind", "media", "type", "request_kind"):
        value = str(request.get(key) or "").strip().lower()
        if value in ANIMATION_REQUEST_KINDS:
            return ROUTE_ANIMATION
    if isinstance(request.get("animation_spec"), Mapping):
        return ROUTE_ANIMATION
    inline = request.get("spec")
    if isinstance(inline, Mapping) and all(key in inline for key in ("objects", "steps", "math_assertions")):
        return ROUTE_ANIMATION
    return ""


def is_animation_request(request: Any) -> bool:
    return request_kind(request) == ROUTE_ANIMATION


def request_spec(request: Any) -> dict | None:
    """请求自带的动画规格（T06 `VisualSpecV1` 的 dict 形态）。取不到返回 None。"""
    if not isinstance(request, Mapping):
        return None
    for key in ANIMATION_SPEC_KEYS:
        value = request.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    return None


def route_request(request: Any, *, unit_id: str = "", scope: str = "",
                  index: int = 1) -> FigureRoute:
    request_id = request_identity(request, unit_id=unit_id, scope=scope, index=index)
    if is_animation_request(request):
        declared = next((f"{key}={request.get(key)!r}" for key in
                         ("kind", "media", "type", "request_kind")
                         if str(request.get(key) or "").lower() in ANIMATION_REQUEST_KINDS),
                        "animation_spec")
        return FigureRoute(request_id, ROUTE_ANIMATION,
                           f"显式声明了动画请求（{declared}）→ 走 AnimationResult 分流")
    return FigureRoute(request_id, ROUTE_SVG,
                       "没有显式动画声明 → 保持旧 `draw→SVG` 的返回含义不变"
                       "（注意：该出口已**停用**，生产无消费方；见上方 ROUTE_SVG 注释）")


def route_requests(requests: Iterable[Any], *, unit_id: str = "",
                   scope: str = "") -> tuple[FigureRoute, ...]:
    """逐条分流。**身份重复直接拒绝**：每个请求恰好一个结果，不许静默多跑一次。"""
    routes: list[FigureRoute] = []
    seen: dict[str, int] = {}
    for index, request in enumerate(requests, 1):
        route = route_request(request, unit_id=unit_id, scope=scope, index=index)
        seen[route.request_id] = seen.get(route.request_id, 0) + 1
        routes.append(route)
    duplicated = sorted(rid for rid, count in seen.items() if count > 1)
    _require(not duplicated,
             f"显式请求的身份重复：{duplicated} —— 每个请求恰好一个结果，"
             f"重复身份会让同一条请求被渲染两次（既花钱，播放器也会拿到两份媒体）")
    return tuple(routes)


def animation_requests(requests: Iterable[Any], *, unit_id: str = "",
                       scope: str = "") -> tuple[tuple[FigureRoute, Any], ...]:
    """过滤出**显式**动画请求，返回 `(route, request)` 对（不含任何推断出来的）。"""
    out = []
    for index, request in enumerate(requests, 1):
        route = route_request(request, unit_id=unit_id, scope=scope, index=index)
        if route.destination == ROUTE_ANIMATION:
            out.append((route, request))
    return tuple(out)


def plan_media(requests_by_unit: Mapping[str, Sequence[Any]]) -> dict[str, tuple[str, ...]]:
    """**只按显式声明**产出每个单元的动画请求清单。没有声明的单元就是空元组。

    这条函数是"没有动画请求不强制所有章节创建视频"的机器可读形式：
    它不会因为"这一章有图"或"这一章是重点"就补一条动画。
    """
    plan: dict[str, tuple[str, ...]] = {}
    for unit_key, requests in (requests_by_unit or {}).items():
        plan[str(unit_key)] = tuple(
            route.request_id for route, _request in
            animation_requests(requests or (), unit_id=str(unit_key)))
    return plan


def units_needing_animation(requests_by_unit: Mapping[str, Sequence[Any]]) -> tuple[str, ...]:
    return tuple(unit for unit, ids in plan_media(requests_by_unit).items() if ids)


# --------------------------------------------------------------------------- #
# 2. 渲染环境（缺环境 → blocked，不是 not_applicable）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RenderEnvironment:
    available: bool
    detail: str
    backends: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"available": self.available, "detail": self.detail,
                "backends": list(self.backends), "missing": list(self.missing)}


def render_environment(capabilities: Any) -> RenderEnvironment:
    """按能力预检判"这台机器现在能不能真渲染"。

    `capabilities=None` 也是不可用 —— "没有预检"不等于"环境齐备"；
    把"没检查"当"可用"是本次改造要治的那类假绿。
    """
    if capabilities is None:
        return RenderEnvironment(False, "没有渲染能力预检结果（capabilities 缺省）"
                                       " —— 没有预检不等于环境齐备", (), ("capabilities",))
    backends = tuple(getattr(capabilities, "render_backends", lambda: [])() or ())
    missing: tuple[str, ...] = ()
    missing_for = getattr(capabilities, "missing_for", None)
    if callable(missing_for):
        # 带上 detail：只说"缺 render:manim"看不出是缺解释器还是缺隔离，
        # 而这两种的处置方式完全不同。
        missing = tuple(f"{cap.name}（{cap.detail}）" for cap in missing_for("render"))
    if not backends:
        detail = ("没有任何可用的动画渲染后端；缺：" +
                  ("、".join(missing) or "能力表里没有 `render_backend` 条目"))
        return RenderEnvironment(False, detail, (), missing)
    return RenderEnvironment(True, "可用渲染后端：" + "、".join(backends), backends, ())


def _hashes(request: Any, spec: Any, environment: RenderEnvironment) -> dict:
    """阻断结果也要有稳定指纹 —— 否则"这次为什么没做"无从复核。"""
    return {
        "spec_hash": digest_of(spec if isinstance(spec, Mapping) else {"spec": None}),
        "compiler_hash": digest_of({"compiler": "not-run",
                                    "request": request_identity(request)}),
        "environment_hash": digest_of({"backends": list(environment.backends),
                                       "available": environment.available}),
    }


def _terminal_result(*, request_id: str, status: str, reason: str, environment: dict,
                     hashes: dict, unit_id: str = "", kind: str = "environment"
                     ) -> AnimationResult:
    """阻断／失败结果的唯一构造口。**注意：这里没有 `not_applicable`。**

    `not_applicable` 的语义是"按政策**不需要**做"，只有上游拿着 policy + evidence
    才能下这个结论；缺环境／缺渲染器／缺评审都只能 `blocked`。
    """
    _require(status in ("blocked", "failed", "cancelled"),
             f"内部错误：_terminal_result 不支持 status={status!r}")
    return AnimationResult(
        request_id=request_id, status=status, spec_hash=hashes["spec_hash"],
        compiler_hash=hashes["compiler_hash"], environment_hash=hashes["environment_hash"],
        source_binding={"unit_id": unit_id, "request_id": request_id},
        resources=(), steps=(), math_report={"status": "not_run"}, frame_report=None,
        technical_report={"kind": kind, "reason": reason, "environment": environment,
                          "deferred_checks": ["visual_semantic_review(T08)"]})


def _parse_render_output(raw: Any, *, request_id: str) -> tuple[AnimationResult, str]:
    """渲染器返回值 → `(AnimationResult, base_dir)`。

    支持三种形态：`AnimationResult`、它的 dict（可带 `base_dir`）、`(结果, base_dir)` 元组。
    `base_dir` 是**帧文件所在目录**，画面评审要靠它把相对路径解成真实图片；
    它不进冻结契约（`AnimationResult` 的字段不许改），所以在这里单独交回。
    """
    base_dir = ""
    if isinstance(raw, tuple) and len(raw) == 2:
        raw, base_dir = raw
    elif isinstance(raw, Mapping):
        base_dir = str(raw.get("base_dir") or "")
    if isinstance(raw, AnimationResult):
        result = raw
    else:
        data = dict(raw)
        data.setdefault("request_id", request_id)
        data.pop("base_dir", None)                 # 不属于冻结契约，解析前丢掉
        result = parse_animation_result(data)
    _require(result.request_id == request_id,
             f"渲染结果的 request_id={result.request_id!r} 与本次请求 "
             f"{request_id!r} 不符 —— 结果不许串号（播放器会拿到别的单元的媒体）")
    return result, base_dir


def _result_view(result: AnimationResult, base_dir: str) -> dict:
    """给 `verify_frames` 的视图：冻结契约 + 解码根。不改 `AnimationResult` 本身的字段。"""
    view = result.as_dict()
    if base_dir:
        view["base_dir"] = base_dir
    return view


# --------------------------------------------------------------------------- #
# 3. 每个显式请求恰好一个结果
# --------------------------------------------------------------------------- #
def fulfill_animation_request(request: Any, *, renderer: Callable[..., Any] | None = None,
                              capabilities: Any = None, profile: str = "draft",
                              spec: Mapping | None = None, unit_id: str = "",
                              request_id: str | None = None,
                              require_spec: bool = False) -> AnimationResult:
    """把**一条**显式动画请求变成**恰好一个** `AnimationResult`。

    * 缺渲染环境 → `status="blocked"`（带缺哪几项），**绝不** `not_applicable`；
    * `renderer(profile, spec)`（协议与 `run_media_pipeline` 的 `render` **同一个**）
      返回渲染结果；
    * 渲染器抛 `CapabilityError` → 同样 blocked（能力不足是环境问题，不是产物问题）；
    * `require_spec=True` 时，没有受限场景规格（T06）也是 `blocked` ——
      一句话的需求不能凭空变成动画，**不许换一道题**。
    """
    unit_key = unit_id or (str(request.get("unit_id") or "") if isinstance(request, Mapping)
                           else "")
    rid = request_id or request_identity(request, unit_id=unit_key)
    request_spec_value = spec if spec is not None else request_spec(request)
    environment = render_environment(capabilities)
    hashes = _hashes(request, request_spec_value, environment)
    if not environment.available:
        return _terminal_result(
            request_id=rid, status="blocked", reason=environment.detail,
            environment={"available": False, "detail": environment.detail,
                         "missing": list(environment.missing)},
            hashes=hashes, unit_id=unit_key, kind="capability")
    if require_spec and not request_spec_value:
        return _terminal_result(
            request_id=rid, status="blocked",
            reason=("显式动画请求没有受限场景规格（T06 VisualSpecV1）—— "
                    "一句话的需求不许凭空变成动画，也不许换一道题"),
            environment={"available": True, "detail": environment.detail},
            hashes=hashes, unit_id=unit_key, kind="spec")
    if renderer is None:
        return _terminal_result(
            request_id=rid, status="blocked",
            reason="渲染环境可用但没有渲染执行器（renderer=None）—— 不假装完成",
            environment={"available": True, "detail": environment.detail},
            hashes=hashes, unit_id=unit_key, kind="renderer")
    try:
        raw = renderer(profile, request_spec_value)
    except CapabilityError as exc:
        return _terminal_result(
            request_id=rid, status="blocked", reason=str(exc),
            environment={"available": False, "detail": str(exc)},
            hashes=hashes, unit_id=unit_key, kind="capability")
    result, _base = _parse_render_output(raw, request_id=rid)
    return result


def fulfill_animation_requests(requests: Iterable[Any], *,
                               renderer: Callable[..., Any] | None = None,
                               capabilities: Any = None, profile: str = "draft",
                               specs: Mapping[str, Mapping] | None = None,
                               unit_id: str = "",
                               renderers: Mapping[str, Callable[..., Any]] | None = None,
                               require_spec: bool = False
                               ) -> tuple[AnimationResult, ...]:
    """逐条处理显式动画请求。**没有动画请求就返回空元组**（渲染器一次都不调用）。"""
    routes = route_requests(requests, unit_id=unit_id)
    pairs = [(route, request) for route, request in zip(routes, requests)
             if route.destination == ROUTE_ANIMATION]
    out: list[AnimationResult] = []
    for route, request in pairs:
        chosen_renderer = (renderers or {}).get(route.request_id, renderer)
        chosen_spec = (specs or {}).get(route.request_id)
        out.append(fulfill_animation_request(
            request, renderer=chosen_renderer, capabilities=capabilities, profile=profile,
            spec=chosen_spec, unit_id=unit_id, request_id=route.request_id,
            require_spec=require_spec))
    return tuple(out)


def manim_renderer(*, workspace_root: str | Path | None = None,
                   output_root: str | Path | None = None,
                   cache_dir: str | Path | None = None,
                   cancel: object | None = None, math_context=None) -> Callable[[str, Mapping], Any]:
    """生产渲染器：受限规格（T06）→ 可信编译 → 隔离 worker 真渲染 → `(结果, base_dir)`。

    ⚠️ 调用方**必须**先过 `render_environment(capabilities)`：本工厂不自己 probe，
    免得在"本来就不该渲染"的路径上多跑一次隔离自检（那是几秒的子进程）。

    输出目录按 **规格指纹 + 档位** 命名：同一份规格的 draft/final 各自成目录，
    所以 `final` 的帧是它**自己**渲染出来的（画面评审因此看得到真实差异），
    而目录名不依赖 run_id／时间（可复现、可对比）。
    """
    from pathlib import Path

    def render(profile: str, spec: Mapping) -> tuple[dict, str]:
        from ..visual.compiler import RenderProfile, compile_scene
        from ..visual.manim_adapter import (default_services, limits_for_profile,
                                            render_scene)
        from ..visual.spec import parse_visual_spec

        parsed = parse_visual_spec(dict(spec))
        scene = compile_scene(parsed, RenderProfile(profile), math_context=math_context, cancel=cancel)
        services = default_services(workspace_root=workspace_root,
                                    cache_dir=cache_dir, cancel=cancel)
        limits = limits_for_profile(services.worker_dir, profile)
        root = Path(output_root or services.scratch_root)
        output = root / f"{parsed.spec_hash.split(':')[1][:16]}-{profile}"
        result = render_scene(scene, limits, services, spec=parsed, profile=profile,
                              output_dir=output, math_context=math_context)
        return result, str(output)

    return render


# --------------------------------------------------------------------------- #
# 4. 图请求台账投影（旧 SVG 路与动画路在**同一个 ledger**里各有终态）
# --------------------------------------------------------------------------- #
def figure_result_from_animation(result: AnimationResult, *, unit_id: str = "",
                                 serves: Any = ()) -> Any:
    """`AnimationResult` → M5 的 `FigureResult`（终态语义对齐，不另发明状态机）。

    * `fulfilled` → `fulfilled`，资源、检查、绑定齐全（`FigureResult` 的构造期要求）；
    * `blocked` / `failed` / `cancelled` / `needs_review` → 如实映射成未完成态并写 reason
      （`needs_review` 映射成 `blocked`：没有资源可交付，需要人／下一轮复核）。
    """
    from .request import STATE_BLOCKED, STATE_FAILED, STATE_FULFILLED, FigureResource, FigureResult

    state = _TERMINAL_STATES.get(result.status, STATE_BLOCKED)
    reason = str((result.technical_report or {}).get("reason") or "")
    if state == STATE_FULFILLED:
        resources = tuple(FigureResource(
            kind=resource.kind, path=resource.relative_path, sha256=resource.sha256,
            spec_digest=result.spec_hash) for resource in result.resources)
        checks = {"ok": True, "math_status": (result.math_report or {}).get("status"),
                  "frame_status": (result.frame_report or {}).get("status"),
                  "ffmpeg_decode_ok": bool(result.technical_report.get("ffmpeg_decode_ok")),
                  "kind": "animation"}
        binding = {"unit_key": unit_id or str(result.source_binding.get("unit_id") or ""),
                   "request_id": result.request_id,
                   "serves": list(serves or ()), "spec_hash": result.spec_hash,
                   "environment_hash": result.environment_hash}
        return FigureResult(request_id=result.request_id, state=STATE_FULFILLED,
                            backend="manim", spec_summary={"kind": "animation",
                                                           "spec_hash": result.spec_hash},
                            resources=resources, checks=checks, binding=binding)
    if state == STATE_FAILED and not reason:
        reason = "动画渲染失败（结果里没有写原因 —— 下游只能重跑，所以这里补一句）"
    if result.status == "not_applicable":
        return FigureResult(request_id=result.request_id, state="not_applicable",
                            backend="manim", reason=reason,
                            policy=str(result.policy or ""),
                            evidence=str(result.evidence or ""))
    return FigureResult(request_id=result.request_id, state=state,
                        backend="manim" if result.status != "blocked" else "",
                        spec_summary={"kind": "animation", "animation_status": result.status},
                        reason=reason or f"动画未完成：status={result.status}",
                        checks={"ok": False, "animation_status": result.status})


def ledger_rows(results: Iterable[AnimationResult]) -> list[dict]:
    """逐请求台账行（含 `animation` 原文，供交付层直接取用）。"""
    rows = []
    for result in results:
        rows.append({
            "request_id": result.request_id,
            # `kind="animation"`：与 SVG 行在同一份 ledger 里区分开，下游按它找媒体
            "kind": "animation",
            "state": _TERMINAL_STATES.get(result.status, "blocked"),
            "animation_status": result.status,
            "spec_hash": result.spec_hash,
            "environment_hash": result.environment_hash,
            "resources": [resource.as_dict() for resource in result.resources],
            "reason": str((result.technical_report or {}).get("reason") or ""),
            "animation": result.as_dict()})
    return rows


def attachable_animations(results: Iterable[AnimationResult]) -> dict[str, dict]:
    """T09 `attach_animations` 的输入：`{unit_key: AnimationResult}`，**只收 fulfilled**。

    没完成的产物不进播放器载荷 —— 让播放器去显示一个 blocked 的结果，等于把
    "没做出视频"包装成"有视频但坏了"。
    """
    out: dict[str, dict] = {}
    for result in results:
        if result.status != "fulfilled":
            continue
        unit_key = str(result.source_binding.get("unit_id") or "")
        out.setdefault(unit_key, []).append(result.as_dict())
    return out


# --------------------------------------------------------------------------- #
# 5. draft → 关键帧审核 → 局部 patch → final
# --------------------------------------------------------------------------- #
@dataclass
class MediaReviewState:
    """媒体返修批次的状态（可持久化，供 T04/T10 与 run state 复用）。"""
    unit_id: str = ""
    content_rounds_used: int = 0
    max_content_rounds: int = 2
    rounds_by_profile: dict[str, int] = field(default_factory=dict)
    reviewed_frames: dict[str, list[str]] = field(default_factory=dict)
    spec_hashes: list[str] = field(default_factory=list)
    patches: list[dict] = field(default_factory=list)
    reviews: list[dict] = field(default_factory=list)
    stop_reason: str = ""

    def as_dict(self) -> dict:
        return {"unit_id": self.unit_id, "content_rounds_used": self.content_rounds_used,
                "max_content_rounds": self.max_content_rounds,
                "rounds_by_profile": dict(self.rounds_by_profile),
                "reviewed_frames": {k: list(v) for k, v in self.reviewed_frames.items()},
                "spec_hashes": list(self.spec_hashes), "patches": list(self.patches),
                "reviews": list(self.reviews), "stop_reason": self.stop_reason}


@dataclass(frozen=True)
class MediaOutcome:
    results: tuple[AnimationResult, ...]
    reports: dict[str, FrameReport]
    findings: tuple[FrameFinding, ...]
    state: MediaReviewState
    final_status: str
    accepted: bool
    notes: tuple[str, ...] = ()

    @property
    def final(self) -> AnimationResult:
        return self.results[-1]

    def as_dict(self) -> dict:
        return {"final_status": self.final_status, "accepted": self.accepted,
                "results": [r.as_dict() for r in self.results],
                "reports": {k: v.as_dict() for k, v in self.reports.items()},
                "findings": [f.as_dict() for f in self.findings],
                "state": self.state.as_dict(), "notes": list(self.notes)}


def apply_local_patch(spec: Mapping, findings: Iterable[FrameFinding],
                      patch: Mapping) -> dict:
    """只按**发现点名的** step／object 改规格；越界就报错。

    "局部返修"如果变成"顺手把整份规格重排一遍"，前一批的验收结论就全部失效，
    而且改动没人审。所以这里把"局部"变成构造期约束：patch 只允许出现
    `steps` / `objects` 两个段，键必须 ⊆ 失败发现里出现的 step／object。
    """
    findings = tuple(findings)
    in_scope_steps = {f.step_id for f in findings}
    in_scope_objects = {f.object_id for f in findings}
    patch = dict(patch or {})
    unknown = sorted(set(patch) - {"steps", "objects"})
    _require(not unknown,
             f"局部 patch 只允许 `steps` / `objects` 两段，实际多了 {unknown} —— "
             f"不许借返修重排整份规格")
    out = copy.deepcopy(dict(spec))
    for step_id, changes in (patch.get("steps") or {}).items():
        _require(str(step_id) in in_scope_steps,
                 f"patch 改了没有被发现点名的步骤 {step_id!r}（本次发现只在 "
                 f"{sorted(in_scope_steps)}）—— 局部返修不许顺手改别处")
        target = next((s for s in out.get("steps") or []
                       if str(s.get("step_id")) == str(step_id)), None)
        _require(target is not None, f"patch 指向的步骤 {step_id!r} 在规格里不存在")
        _require(isinstance(changes, Mapping), f"step {step_id!r} 的 patch 必须是对象")
        if not any(f.kind == "visual_math" and f.step_id == str(step_id) for f in findings):
            _require(not (set(changes) - {"start", "end", "checkpoints", "actions"}), "布局工单不能修改数学字幕或步骤身份")
            _require(all(a.get("action") in ("reveal", "hide", "highlight", "camera", "pause") for a in changes.get("actions", [])),
                     "布局工单不能修改数学 transform")
        target.update(copy.deepcopy(dict(changes)))
    for object_id, changes in (patch.get("objects") or {}).items():
        _require(str(object_id) in in_scope_objects,
                 f"patch 改了没有被发现点名的对象 {object_id!r}（本次发现只在 "
                 f"{sorted(in_scope_objects)}）—— 局部返修不许顺手改别处")
        target = next((o for o in out.get("objects") or []
                       if str(o.get("object_id")) == str(object_id)), None)
        _require(target is not None, f"patch 指向的对象 {object_id!r} 在规格里不存在")
        _require(isinstance(changes, Mapping), f"object {object_id!r} 的 patch 必须是对象")
        if not any(f.kind == "visual_math" and f.object_id == str(object_id) for f in findings):
            allowed = {"color"}
            if target.get("kind") == "label" and target.get("space") == "screen":
                allowed.add("at")
            _require(not (set(changes) - allowed), "布局工单不能修改数学表达式、坐标、标签内容或对象身份")
        _require(not (set(changes) & {"object_id", "kind"}), "补丁不得修改对象身份")
        target.update(copy.deepcopy(dict(changes)))
    return out


def _render_once(render: Callable[..., Any], profile: str, spec: Mapping,
                 request_id: str, unit_id: str,
                 state: MediaReviewState) -> tuple[AnimationResult, str]:
    raw = render(profile, spec)
    result, base_dir = _parse_render_output(raw, request_id=request_id)
    state.rounds_by_profile[profile] = state.rounds_by_profile.get(profile, 0) + 1
    if not state.spec_hashes or state.spec_hashes[-1] != result.spec_hash:
        state.spec_hashes.append(result.spec_hash)
    return result, base_dir


def run_media_pipeline(*, spec: Mapping, render: Callable[[str, Mapping], Any],
                       reviewer: Any = None, capabilities: Any = None,
                       patch: Callable[[Mapping, tuple[FrameFinding, ...]], Mapping] | None = None,
                       unit_id: str = "", request_id: str = "",
                       max_content_rounds: int = 2, max_images: int = 8,
                       state: MediaReviewState | None = None) -> MediaOutcome:
    """draft → 关键帧审核 → 必要局部 patch → final（§T08）。

    `render(profile, spec)` 返回 `AnimationResult`／它的 dict（可带 `base_dir`）／
    `(结果, base_dir)`。`reviewer` 必须显式接收 `images`（真实图片 bytes/hash），
    否则在**任何渲染之前**抛 `CapabilityError`。
    """
    state = state or MediaReviewState(unit_id=unit_id,
                                      max_content_rounds=max_content_rounds)
    state.max_content_rounds = state.max_content_rounds or max_content_rounds
    if reviewer is not None:
        # 能力检查放在渲染之前：只吃 JSON 的假评审不该先花掉一次渲染
        from ...providers.capabilities import require_image_input
        require_image_input(reviewer, purpose="visual_review")
    environment = render_environment(capabilities)
    if not environment.available:
        blocked = _terminal_result(
            request_id=request_id or "vis", status="blocked",
            reason=environment.detail,
            environment={"available": False, "detail": environment.detail,
                         "missing": list(environment.missing)},
            hashes=_hashes({}, spec, environment), unit_id=unit_id, kind="capability")
        return MediaOutcome(results=(blocked,), reports={}, findings=(),
                            state=state, final_status="blocked", accepted=False,
                            notes=("缺渲染环境 → blocked（不是 not_applicable）："
                                   + environment.detail,))
    if reviewer is None:
        return MediaOutcome(
            results=(), reports={}, findings=(), state=state,
            final_status="blocked", accepted=False,
            notes=("没有视觉评审传输 → 画面无法复核；缺能力要 blocked，"
                   "不许拿结构/像素检查冒充看过画面",))
    rid = request_id or "vis"
    current: dict = dict(spec)
    reports: dict[str, FrameReport] = {}
    outstanding: tuple[FrameFinding, ...] = ()
    last_draft: AnimationResult | None = None
    while True:
        draft, base_dir = _render_once(render, "draft", current, rid, unit_id, state)
        last_draft = draft
        report = verify_frames(current, _result_view(draft, base_dir), reviewer=reviewer,
                               max_images=max_images, profile="draft",
                               images_required=True)
        reports["draft"] = report
        state.reviewed_frames.setdefault("draft", []).extend(report.frame_hashes)
        state.reviews.append({"profile": "draft", "status": report.status,
                              "frame_hashes": list(report.frame_hashes),
                              "findings": [f.finding_id for f in report.findings],
                              "content_round": state.content_rounds_used})
        if report.status == "pass" or not report.findings:
            state.stop_reason = "draft 已通过" if report.status == "pass" else ""
            break
        outstanding = report.findings
        blocking = tuple(f for f in outstanding if f.blocking)
        if not blocking:
            state.stop_reason = "只有非阻塞发现（P2），不消耗内容轮次"
            break
        if patch is None:
            state.stop_reason = "有阻塞发现但没有 patch 执行器 —— 不空耗轮次"
            break
        if state.content_rounds_used >= state.max_content_rounds:
            state.stop_reason = (f"内容返修批次已用满（{state.content_rounds_used}/"
                                 f"{state.max_content_rounds}），保留候选与未过标准")
            break
        patched = patch(current, outstanding)
        _require(isinstance(patched, Mapping),
                 f"patch() 必须返回规格 dict，实际 {type(patched).__name__}")
        if dict(patched) == current:
            state.stop_reason = "局部 patch 没有改动任何内容（同一候选），提前停"
            break
        current = dict(patched)
        state.content_rounds_used += 1
        state.patches.append({"round": state.content_rounds_used,
                              "findings": [f.finding_id for f in outstanding],
                              "changed_steps": sorted((patched.get("steps") or {}).keys())
                              if isinstance(patched.get("steps"), Mapping) else [],
                              "changed_objects": sorted((patched.get("objects") or {}).keys())
                              if isinstance(patched.get("objects"), Mapping) else []})

    if reports["draft"].status != "pass":
        return MediaOutcome(results=(last_draft,), reports=reports,
                            findings=reports["draft"].findings, state=state,
                            final_status="needs_review", accepted=False,
                            notes=(state.stop_reason or "草稿逐步画面审核未通过；交协调器处理",))

    # ── final：**同一份规格**的编码档位变化，不另消耗内容返修批次
    expected_hash = state.spec_hashes[-1]
    rounds_before_final = state.content_rounds_used
    final, final_base = _render_once(render, "final", current, rid, unit_id, state)
    _require(final.spec_hash == expected_hash,
             "同规格的 final 只能是**编码档位变化**：final 的 spec_hash 与最后一次 draft "
             "不一致，说明内容被换了（那要重新审核内容，不能算改档位）")
    final_report = verify_frames(current, _result_view(final, final_base), reviewer=reviewer,
                                 max_images=max_images, profile="final",
                                 images_required=True)
    reports["final"] = final_report
    state.reviewed_frames.setdefault("final", []).extend(final_report.frame_hashes)
    state.reviews.append({"profile": "final", "status": final_report.status,
                          "frame_hashes": list(final_report.frame_hashes),
                          "findings": [f.finding_id for f in final_report.findings],
                          "content_round": state.content_rounds_used})
    _require(state.content_rounds_used == rounds_before_final,
             "改编码档位（draft→final）**不消耗内容返修批次** —— 同规格的 final 只是档位变化")
    accepted = (final.status == "fulfilled" and final_report.status == "pass")
    results = tuple(r for r in (last_draft, final) if r is not None)
    notes = [f"draft 内容轮次 {state.content_rounds_used}/{state.max_content_rounds}",
             f"final 档评审的关键帧 {len(final_report.frame_hashes)} 张"
             f"（{final_report.status}）",
             "final 用同一 spec_hash 重新渲染：只有编码档位变化，不另消耗内容返修批次"]
    if state.stop_reason:
        notes.append(f"停止原因：{state.stop_reason}")
    return MediaOutcome(results=results, reports=reports, findings=outstanding,
                        state=state, final_status=final_report.status,
                        accepted=accepted, notes=tuple(notes))


__all__ = ["ANIMATION_REQUEST_KINDS", "ANIMATION_SPEC_KEYS", "ROUTE_SVG", "ROUTE_ANIMATION",
           "FigureRoute", "RenderEnvironment", "MediaReviewState", "MediaOutcome",
           "request_identity", "request_kind", "is_animation_request", "request_spec",
           "route_request", "route_requests", "animation_requests", "plan_media",
           "units_needing_animation", "render_environment", "fulfill_animation_request",
           "fulfill_animation_requests", "manim_renderer", "figure_result_from_animation",
           "ledger_rows",
           "attachable_animations", "apply_local_patch", "run_media_pipeline"]
