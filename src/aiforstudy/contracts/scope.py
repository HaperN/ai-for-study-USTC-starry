"""`ScopePlan` —— **计划**（只读、零副作用）。

方案 §6：

> | ScopePlan | request digest、准备任务、已解析或待解析的范围、目标 unit keys、
>   读取依赖、写入目标、context ref、失效原因、估算；**章顺序固定** |

以及两条容易做错的：

> 冷启动的只读计划将 `resolved_scope` 标为 `null`、`requires_preparation=true`，
> 明确展示符号化选择与准备任务；run 在 catalog 形成后先校验并固定 scope，合法才继续。
> **不能为让计划好看而虚构单元数。**

> `plan` 只返回计划，**不创建运行目录、写 pdf.path、改 scope、修 doctor 记录或追加 usage**。

所以 `ScopePlan` 是**不可变值对象**，且"不知道"是一等公民：`resolved_scope=None`
配上 `requires_preparation=True` 才是诚实的冷启动计划；编造一个空的或猜的单元集合，
会让界面显示"0 个单元、费用 0"，然后真正跑起来花掉几百万 token。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.digest import digest_of
from ..errors import ContractError
from .enums import Goal, Profile
from .request import Selectors


@dataclass(frozen=True)
class PreparationTask:
    """为了能开始本次范围而必须先做的基础工作（全书解析/概念索引等）。"""
    stage: str
    reason: str
    scope: str = "book"          # book | chapter:<key> | unit:<key>
    produces: str = ""           # 产物的 kind
    note: str = ""

    def as_dict(self) -> dict:
        return {"stage": self.stage, "reason": self.reason, "scope": self.scope,
                "produces": self.produces, "note": self.note}


@dataclass(frozen=True)
class Invalidation:
    """为什么某个已有产物**不算新鲜**（文件存在 ≠ 缓存有效）。"""
    target: str                  # 逻辑键或阶段名
    reason: str                  # 人可读：哪个指纹变了
    caused_by: str = ""          # 变化的来源（原文/提示词/规则/模板…）

    def as_dict(self) -> dict:
        return {"target": self.target, "reason": self.reason, "caused_by": self.caused_by}


@dataclass(frozen=True)
class Estimate:
    """费用/时间估算。**无法估算的必须显式列出**，不许悄悄填 0。"""
    bucket: str                  # prepare | chapter | audit | figures | delivery
    tokens_low: int | None = None
    tokens_high: int | None = None
    minutes_low: float | None = None
    minutes_high: float | None = None
    basis: str = ""              # 估算依据（拿什么算出来的）
    unknown_reason: str | None = None

    def __post_init__(self) -> None:
        if self.tokens_low is None and not self.unknown_reason:
            raise ContractError(
                f"Estimate[{self.bucket}] 既没给 token 区间也没给 unknown_reason——"
                f"方案 §5 要求'展示费用估算范围和无法估算项'，不能悄悄填 0")
        if (self.tokens_low is not None and self.tokens_high is not None
                and self.tokens_high < self.tokens_low):
            raise ContractError(f"Estimate[{self.bucket}] 的 token 区间反了")

    def as_dict(self) -> dict:
        return {"bucket": self.bucket, "tokens_low": self.tokens_low,
                "tokens_high": self.tokens_high, "minutes_low": self.minutes_low,
                "minutes_high": self.minutes_high, "basis": self.basis,
                "unknown_reason": self.unknown_reason}


@dataclass(frozen=True)
class ScopePlan:
    request_digest: str
    book_id: str
    goal: Goal
    profile: Profile
    selectors: Selectors
    #: 目标教学单元的稳定键（按教材顺序）。**`None` = 冷启动，尚未解析**。
    resolved_scope: tuple[str, ...] | None = None
    requires_preparation: bool = False
    chapter_order: tuple[str, ...] = ()
    preparation: tuple[PreparationTask, ...] = ()
    read_dependencies: tuple[str, ...] = ()
    write_targets: tuple[str, ...] = ()
    context_ref: str | None = None
    invalidations: tuple[Invalidation, ...] = ()
    estimates: tuple[Estimate, ...] = ()
    notes: tuple[str, ...] = ()
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        if not str(self.book_id or "").strip():
            raise ContractError("ScopePlan.book_id 必填")
        if not self.request_digest.startswith("sha256:"):
            raise ContractError(
                f"ScopePlan.request_digest 必须是 sha256 指纹，实际是 {self.request_digest!r}")
        if self.resolved_scope is None and not self.requires_preparation:
            raise ContractError(
                "resolved_scope=None 时必须 requires_preparation=True——"
                "否则这是一份'没有范围、也不用准备'的计划，跑起来只能什么都不做")
        if self.resolved_scope is not None:
            if any(not str(u).strip() for u in self.resolved_scope):
                raise ContractError("resolved_scope 里不允许空单元键")
            if len(set(self.resolved_scope)) != len(self.resolved_scope):
                raise ContractError(
                    "resolved_scope 里有重复单元——范围必须先解析、去重、再固定（方案 §4）")

    @property
    def is_cold_start(self) -> bool:
        return self.resolved_scope is None

    @property
    def target_count(self) -> int | None:
        """目标单元数。冷启动返回 `None` —— **不返回 0**。"""
        return None if self.resolved_scope is None else len(self.resolved_scope)

    def as_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "request_digest": self.request_digest,
            "book_id": self.book_id,
            "goal": self.goal.value,
            "profile": self.profile.value,
            "selectors": self.selectors.as_dict(),
            "resolved_scope": (None if self.resolved_scope is None
                               else list(self.resolved_scope)),
            "requires_preparation": self.requires_preparation,
            "chapter_order": list(self.chapter_order),
            "preparation": [t.as_dict() for t in self.preparation],
            "read_dependencies": list(self.read_dependencies),
            "write_targets": list(self.write_targets),
            "context_ref": self.context_ref,
            "invalidations": [i.as_dict() for i in self.invalidations],
            "notes": list(self.notes),
            "target_count": self.target_count,
        }

    def identity(self) -> dict:
        """**进指纹的部分**：只有逻辑身份。

        ⚠️ `read_dependencies` / `write_targets` 里带的是绝对路径，**故意排除**：
        方案 §11 要求 digest 不含绝对工作目录——否则"把工作区挪个地方"会让所有缓存
        无效（那是纯粹自找的失效）。估算值也排除：它是给人看的区间，不是输入的一部分。
        """
        return {
            "schema_version": self.schema_version,
            "request_digest": self.request_digest,
            "book_id": self.book_id,
            "goal": self.goal.value,
            "profile": self.profile.value,
            "selectors": self.selectors.as_dict(),
            "resolved_scope": (None if self.resolved_scope is None
                               else list(self.resolved_scope)),
            "requires_preparation": self.requires_preparation,
            "chapter_order": list(self.chapter_order),
            "preparation": [t.as_dict() for t in self.preparation],
            "context_ref": self.context_ref,
            "invalidations": [i.as_dict() for i in self.invalidations],
        }

    def digest(self) -> str:
        """计划指纹：不含时间、run_id、工作区绝对路径。"""
        return digest_of(self.identity())

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ScopePlan":
        scope = payload.get("resolved_scope")
        return cls(
            request_digest=str(payload.get("request_digest") or ""),
            book_id=str(payload.get("book_id") or ""),
            goal=Goal(str(payload.get("goal") or Goal.DELIVER.value)),
            profile=Profile(str(payload.get("profile") or Profile.RELEASE.value)),
            selectors=Selectors(kind=(payload.get("selectors") or {}).get("kind"),
                                values=tuple((payload.get("selectors") or {}).get("values") or ())),
            resolved_scope=(None if scope is None else tuple(str(x) for x in scope)),
            requires_preparation=bool(payload.get("requires_preparation")),
            chapter_order=tuple(str(x) for x in (payload.get("chapter_order") or ())),
            preparation=tuple(PreparationTask(**t) for t in (payload.get("preparation") or ())),
            read_dependencies=tuple(str(x) for x in (payload.get("read_dependencies") or ())),
            write_targets=tuple(str(x) for x in (payload.get("write_targets") or ())),
            context_ref=payload.get("context_ref"),
            invalidations=tuple(Invalidation(**i) for i in (payload.get("invalidations") or ())),
            estimates=tuple(Estimate(**e) for e in (payload.get("estimates") or ())),
            notes=tuple(str(x) for x in (payload.get("notes") or ())),
            schema_version=str(payload.get("schema_version") or "1.0"),
        )


__all__ = ["PreparationTask", "Invalidation", "Estimate", "ScopePlan"]
