"""`RunRequest` —— 一次运行的**规范化请求**（方案 §6 契约表第一行）。

> | RunRequest | schema_version、book_id、selectors、goal、profile、prepare_policy、
>   parallelism、delivery_formats、resume/idempotency_key |

关键点：

* **`book_id` 必填。** 方案 §4：省略 `--book` 不退回历史默认书；
* **选择器三选一且互斥。** `--chapters` / `--units` / `--all` 必须选一项，
  避免漏参数意外跑整本；
* **`digest()` 只吃"计划相关"字段**，不含 `resume` / `idempotency_key` /
  工作区绝对路径。方案 §5：CLI 与 HTTP 的**等价请求必须生成相同 plan digest** ——
  把幂等键算进指纹，同一个请求重发一次就成了"另一个计划"。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.digest import digest_of
from ..errors import ContractError
from .enums import DeliveryFormat, Goal, PreparePolicy, Profile, SelectorKind

SCHEMA_VERSION = "2.0"
MAX_PARALLELISM = 32
DEFAULT_PARALLELISM = 2


@dataclass(frozen=True)
class Selectors:
    """范围选择器。`all` 的 values 必须为空；其余必须非空。"""
    kind: SelectorKind
    values: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SelectorKind):
            try:
                object.__setattr__(self, "kind", SelectorKind(str(self.kind)))
            except ValueError as exc:
                raise ContractError(
                    f"selectors.kind 必须是 {[k.value for k in SelectorKind]} 之一，"
                    f"实际是 {self.kind!r}") from exc
        vals = tuple(str(v).strip() for v in self.values)
        if any(not v for v in vals):
            raise ContractError("selectors.values 里不允许空字符串")
        if self.kind is SelectorKind.ALL and vals:
            raise ContractError("selectors.kind=all 时 values 必须为空")
        if self.kind is not SelectorKind.ALL and not vals:
            raise ContractError(
                f"selectors.kind={self.kind.value} 时 values 不能为空——"
                f"空范围会被误读成'跑整本'")
        object.__setattr__(self, "values", vals)

    @property
    def is_all(self) -> bool:
        return self.kind is SelectorKind.ALL

    def as_dict(self) -> dict:
        return {"kind": self.kind.value, "values": list(self.values)}


@dataclass(frozen=True)
class Budget:
    """Legacy value object for explicit budget-tool tests; not part of RunRequest."""
    max_tokens: int
    max_minutes: float

    def __post_init__(self) -> None:
        if not isinstance(self.max_tokens, int) or isinstance(self.max_tokens, bool):
            raise ContractError(f"budget.max_tokens 必须是整数，实际是 {self.max_tokens!r}")
        if self.max_tokens < 0:
            raise ContractError(f"budget.max_tokens 不能为负：{self.max_tokens}")
        try:
            minutes = float(self.max_minutes)
        except (TypeError, ValueError) as exc:
            raise ContractError(f"budget.max_minutes 必须是数字，实际是 {self.max_minutes!r}") from exc
        if minutes <= 0:
            raise ContractError(f"budget.max_minutes 必须为正：{minutes}")
        object.__setattr__(self, "max_minutes", minutes)

    def as_dict(self) -> dict:
        return {"max_tokens": self.max_tokens, "max_minutes": self.max_minutes}


@dataclass(frozen=True)
class RunRequest:
    book_id: str
    selectors: Selectors
    goal: Goal = Goal.DELIVER
    profile: Profile = Profile.RELEASE
    prepare_policy: PreparePolicy = PreparePolicy.ENSURE
    parallelism: int = DEFAULT_PARALLELISM
    delivery_formats: tuple[DeliveryFormat, ...] = (DeliveryFormat.CHAPTER_HTML,)
    schema_version: str = SCHEMA_VERSION
    resume: str | None = None
    idempotency_key: str | None = None

    # ── 校验
    def validate(self) -> "RunRequest":
        if self.schema_version != SCHEMA_VERSION:
            raise ContractError(
                f"schema_version 必须是 {SCHEMA_VERSION!r}，实际是 {self.schema_version!r}")
        if not str(self.book_id or "").strip():
            raise ContractError("book_id 必填（方案 §4：省略 --book 不退回历史默认书）")
        if not isinstance(self.parallelism, int) or isinstance(self.parallelism, bool):
            raise ContractError(f"parallelism 必须是整数，实际是 {self.parallelism!r}")
        if not 1 <= self.parallelism <= MAX_PARALLELISM:
            raise ContractError(
                f"parallelism 必须在 1..{MAX_PARALLELISM} 之间，实际是 {self.parallelism}"
                f"（方案 §11：不使用无界线程池）")
        if any(not isinstance(fmt, DeliveryFormat) for fmt in self.delivery_formats):
            raise ContractError("delivery_formats 只支持 chapter-html")
        if not self.delivery_formats:
            raise ContractError("delivery_formats 不能为空——不说清交付什么，等于没交付")
        return self

    # ── 序列化
    def as_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "book_id": self.book_id,
            "selectors": self.selectors.as_dict(),
            "goal": self.goal.value,
            "profile": self.profile.value,
            "prepare_policy": self.prepare_policy.value,
            "parallelism": self.parallelism,
            "delivery_formats": [f.value for f in self.delivery_formats],
            "resume": self.resume,
            "idempotency_key": self.idempotency_key,
        }

    def plan_inputs(self) -> dict:
        """**只含计划相关字段**（resume / idempotency_key / 工作区一律排除）。"""
        return {
            "schema_version": self.schema_version,
            "book_id": self.book_id,
            "selectors": self.selectors.as_dict(),
            "goal": self.goal.value,
            "profile": self.profile.value,
            "prepare_policy": self.prepare_policy.value,
            "parallelism": self.parallelism,
            "delivery_formats": sorted(f.value for f in self.delivery_formats),
        }

    def digest(self) -> str:
        return digest_of(self.plan_inputs())

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RunRequest":
        if not isinstance(payload, dict):
            raise ContractError(f"RunRequest 必须是对象，实际是 {type(payload).__name__}")
        unknown = set(payload) - {
            "schema_version", "book_id", "selectors", "goal", "profile", "prepare_policy",
            "parallelism", "delivery_formats", "resume", "idempotency_key"}
        if unknown:
            # 不认识就报错：静默忽略一个拼错的字段，等于让用户以为它生效了
            raise ContractError(f"RunRequest 里有不认识的字段：{sorted(unknown)}")

        raw_sel = payload.get("selectors")
        if not isinstance(raw_sel, dict):
            raise ContractError("selectors 必须是对象，形如 {\"kind\":\"chapters\",\"values\":[...]}")
        selectors = Selectors(kind=raw_sel.get("kind"), values=tuple(raw_sel.get("values") or ()))

        def enum_of(enum_cls, key, default):
            raw = payload.get(key, default.value if default is not None else None)
            try:
                return enum_cls(str(raw))
            except ValueError as exc:
                raise ContractError(
                    f"{key} 必须是 {[m.value for m in enum_cls]} 之一，实际是 {raw!r}") from exc

        formats_raw = payload.get("delivery_formats")
        if formats_raw is None:
            formats = (DeliveryFormat.CHAPTER_HTML,)
        elif not isinstance(formats_raw, list):
            raise ContractError("delivery_formats 必须是数组")
        else:
            try:
                formats = tuple(DeliveryFormat(str(f)) for f in formats_raw)
            except ValueError as exc:
                raise ContractError(
                    f"delivery_formats 里出现未知值：{exc}；"
                    f"合法值 {[f.value for f in DeliveryFormat]}") from exc

        req = cls(
            book_id=str(payload.get("book_id") or "").strip(),
            selectors=selectors,
            goal=enum_of(Goal, "goal", Goal.DELIVER),
            profile=enum_of(Profile, "profile", Profile.RELEASE),
            prepare_policy=enum_of(PreparePolicy, "prepare_policy", PreparePolicy.ENSURE),
            parallelism=payload.get("parallelism", DEFAULT_PARALLELISM),
            delivery_formats=formats,
            schema_version=str(payload.get("schema_version") or SCHEMA_VERSION),
            resume=(str(payload["resume"]) if payload.get("resume") else None),
            idempotency_key=(str(payload["idempotency_key"])
                             if payload.get("idempotency_key") else None),
        )
        return req.validate()


__all__ = ["SCHEMA_VERSION", "MAX_PARALLELISM", "DEFAULT_PARALLELISM",
           "Selectors", "Budget", "RunRequest"]
