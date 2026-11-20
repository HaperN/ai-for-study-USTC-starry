"""HTTP 与 CLI 共用的请求校验器：**等价请求必须生成相同 plan digest**。

方案 §5：

> CLI 与 HTTP **共用请求校验器**，等价请求生成**相同 plan digest**。

方案 §15 A06 把它变成验收项：

> CLI 与 UI 提交等价请求；重复点击提交 → plan digest 一致；同 idempotency key 只创建一次运行。

## 为什么必须共用

两条路各写一份校验器，一定会漂移：UI 允许 `parallelism=16` 而 CLI 拒绝，
或者 UI 忘了校验"三选一"，于是"界面能提交、命令行报错"。
更隐蔽的是**默认值不同** —— 同一个意图在两条路上算出两个不同的计划，
而两边都显示"成功"。所以这里只有一份 `normalize_request()`，
CLI 的 argparse 与 HTTP 的 JSON 都收敛到它。

## 边界

本模块**不做任何 IO**、不建目录、不发请求。它只把"用户意图"变成**受检的 `RunRequest`**。
"""
from __future__ import annotations
import math

from ..contracts.enums import (DeliveryFormat, Goal, PreparePolicy, Profile, SelectorKind)
from ..contracts.request import RunRequest, Selectors, SCHEMA_VERSION
from ..errors import ContractError

#: 请求体里允许出现的键（多一个就报错 —— 静默忽略拼错的字段会让用户以为它生效了）
ALLOWED_KEYS = frozenset({
    "schema_version", "book_id", "selectors", "chapters", "units", "all", "goal", "profile",
    "prepare_policy", "parallelism",
    "delivery_formats", "resume", "idempotency_key",
})


def _selectors(payload: dict) -> Selectors:
    """三种写法都收：`selectors`（规范）、`chapters`/`units`/`all`（CLI 风格）。"""
    if payload.get("selectors"):
        if any(payload.get(name) for name in ("chapters", "units", "all")):
            raise ContractError("selectors 与 chapters/units/all 不能同时指定")
        raw = payload["selectors"]
        if not isinstance(raw, dict):
            raise ContractError("selectors 必须是对象")
        return Selectors(kind=raw.get("kind"), values=tuple(raw.get("values") or ()))
    chosen = [name for name in ("chapters", "units", "all") if payload.get(name)]
    if len(chosen) > 1:
        raise ContractError(
            f"--chapters / --units / --all 必须**且只能**选一项（方案 §4），现在选了 {chosen}")
    if payload.get("all"):
        return Selectors(kind=SelectorKind.ALL)
    if payload.get("chapters"):
        return Selectors(kind=SelectorKind.CHAPTERS, values=_split(payload["chapters"]))
    if payload.get("units"):
        return Selectors(kind=SelectorKind.UNITS, values=_split(payload["units"]))
    raise ContractError(
        "必须给出范围：selectors 或 chapters/units/all 之一（方案 §4：三者互斥且必须选一项，"
        "避免漏参数意外跑整本）")


def _split(value) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(v).strip() for v in value if str(v).strip())
    return tuple(part.strip() for part in str(value).split(",") if part.strip())


def _number(value, kind, field):
    try:
        if isinstance(value, bool) or (kind is int and isinstance(value, float) and not value.is_integer()):
            raise ValueError("不是整数")
        result = kind(value)
        if not math.isfinite(result):
            raise ValueError("非有限数字")
        return result
    except (ValueError, TypeError, OverflowError) as exc:
        raise ContractError(f"{field} 必须是有效的{'整数' if kind is int else '数字'}") from exc


def _profile_parallelism(workspace, payload) -> int:
    """修改计划 E7：界面未填并发时与 CLI 一致，按 profile 配置；没有工作区时沿用 2。"""
    if workspace is None:
        return 2
    from ..pipeline.execution_control import load_policy
    return load_policy(workspace, str(payload.get("profile") or "release"),
                       str(payload.get("goal") or "deliver")).parallelism


def normalize_request(payload: dict, workspace=None) -> RunRequest:
    """**唯一**的请求校验器。CLI 与 HTTP 都走它。"""
    if not isinstance(payload, dict):
        raise ContractError(f"请求体必须是对象，实际是 {type(payload).__name__}")
    unknown = sorted(set(payload) - ALLOWED_KEYS)
    if unknown:
        raise ContractError(f"请求体里有不认识的字段：{unknown}")

    formats_raw = payload.get("delivery_formats")
    if formats_raw is None:
        formats = (DeliveryFormat.CHAPTER_HTML,)
    else:
        try:
            formats = tuple(DeliveryFormat(str(f)) for f in formats_raw)
        except (TypeError, ValueError) as exc:
            raise ContractError("delivery_formats 必须是有效的交付格式列表") from exc

    def enum_of(enum_cls, key, default):
        raw = payload.get(key, default)
        try:
            return enum_cls(str(raw))
        except ValueError as exc:
            raise ContractError(
                f"{key} 必须是 {[m.value for m in enum_cls]} 之一，实际 {raw!r}") from exc

    request = RunRequest(
        book_id=str(payload.get("book_id") or "").strip(),
        selectors=_selectors(payload),
        goal=enum_of(Goal, "goal", Goal.DELIVER.value),
        profile=enum_of(Profile, "profile", Profile.RELEASE.value),
        prepare_policy=enum_of(PreparePolicy, "prepare_policy", PreparePolicy.ENSURE.value),
        parallelism=_number(payload["parallelism"] if payload.get("parallelism") is not None
                            else _profile_parallelism(workspace, payload), int, "parallelism"),
        delivery_formats=formats,
        schema_version=str(payload.get("schema_version") or SCHEMA_VERSION),
        resume=(str(payload["resume"]) if payload.get("resume") else None),
        idempotency_key=(str(payload["idempotency_key"])
                         if payload.get("idempotency_key") else None),
    )
    return request.validate()


def request_to_cli_args(request: RunRequest) -> list[str]:
    """把受检请求变回 CLI 参数 —— 用于证明"两条路等价"（测试与诊断用）。"""
    args: list[str] = ["run", "--book", request.book_id]
    if request.selectors.kind is SelectorKind.ALL:
        args.append("--all")
    elif request.selectors.kind is SelectorKind.CHAPTERS:
        args += ["--chapters", ",".join(request.selectors.values)]
    else:
        args += ["--units", ",".join(request.selectors.values)]
    args += ["--goal", request.goal.value, "--profile", request.profile.value,
             "--prepare", request.prepare_policy.value,
             "--parallel", str(request.parallelism)]
    for fmt in request.delivery_formats:
        args += ["--delivery", fmt.value]
    return args


def capabilities_payload(workspace) -> dict:
    """界面按钮的可用性由**服务返回的 capability** 决定，不在前端复制业务规则（§5）。"""
    from ..providers.capabilities import probe
    caps = probe(workspace)
    return {"available": caps.available_names(),
            "figure_backends": caps.figure_backends(),
            "missing_for": {purpose: [c.name for c in caps.missing_for(purpose)]
                            for purpose in ("figure", "render-check", "browser-acceptance")},
            "note": "按钮是否可用由服务端 capability 决定；前端不复制业务规则"}


__all__ = ["ALLOWED_KEYS", "normalize_request", "request_to_cli_args", "capabilities_payload"]
