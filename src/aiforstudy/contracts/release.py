"""`ReleaseManifest` —— 一次**范围交付**的清单（方案 §6 / §10 / §15）。

方案 §6：

> | ReleaseManifest | 书籍、范围、parse/context revision、各单元/章的 artifact refs、
>   资源清单、检查证据、scope accepted/full-book ready 状态 |

方案 §10 把它和"书级索引"分开：

> 具体区分两份清单：每个 ReleaseManifest 只描述**本次所选范围**与其冻结版本；
> `current.json` 是书级发布索引，按章引用不同范围 release 的产物。

方案 §15 给了交付的**集合判据**：

> requested teaching units = 最终教案集合 = 审核覆盖集合 = 交互状态集合（含合法不适用）
> = 页面集合 = 包内集合。

这条判据是这个项目里"少东西也不会报错"那一族问题的总闸。所以 `verify_sets()`
把它做成机器断言，而不是写在文档里等人记得。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..core.digest import digest_of
from ..errors import ContractError
from .artifact import ArtifactRef

#: 集合判据里必须一致的那几组（方案 §15）
SET_KINDS = ("lesson", "audit", "interaction", "page", "package")


@dataclass(frozen=True)
class ReleaseManifest:
    release_id: str
    book_id: str
    scope_kind: str                       # book | chapters | units
    selectors: dict
    parse_revision: str
    context_revision: str
    registry_revision: str = ""
    units: tuple[ArtifactRef, ...] = ()
    chapters: tuple[ArtifactRef, ...] = ()
    resources: tuple[ArtifactRef, ...] = ()
    checks: tuple[dict, ...] = ()
    scope_accepted: bool = False
    full_book_ready: bool = False
    run_id: str = ""
    notes: tuple[str, ...] = ()
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        if not str(self.release_id or "").strip():
            raise ContractError("ReleaseManifest.release_id 必填")
        if self.scope_kind not in ("book", "chapters", "units"):
            raise ContractError(
                f"scope_kind 必须是 book/chapters/units，实际是 {self.scope_kind!r}")
        if self.full_book_ready and self.scope_kind != "book":
            raise ContractError(
                f"scope_kind={self.scope_kind} 的 release 不能标 full_book_ready —— "
                f"方案 §10：单章成功可 scope_accepted=true，但不代表整本通过")
        if self.full_book_ready and not self.scope_accepted:
            raise ContractError("full_book_ready=true 必须先 scope_accepted=true")
        if self.scope_accepted and self.checks:
            failed = [c for c in self.checks if not c.get("通过", c.get("accepted"))]
            if failed:
                raise ContractError(
                    f"scope_accepted=true 但还有 {len(failed)} 条检查未通过："
                    f"{[c.get('名称') or c.get('id') or c.get('name') for c in failed][:5]}")

    # ── 集合判据
    def units_of_kind(self, kind: str) -> set[str]:
        return {r.logical_key for r in self.units if r.kind == kind}

    def verify_sets(self) -> dict:
        """核对方案 §15 的集合判据。返回核对结果；不一致 → 报错。"""
        wanted = {r.logical_key for r in self.units if r.kind == "requested"}
        if not wanted:
            raise ContractError(
                "manifest 里没有 kind='requested' 的单元引用 —— "
                "没有'要做什么'这一组，集合判据无从核对")
        groups = {"requested": wanted}
        for kind in SET_KINDS:
            groups[kind] = self.units_of_kind(kind)
        mismatches = {k: sorted(v ^ wanted) for k, v in groups.items() if v != wanted}
        if mismatches:
            detail = "；".join(f"{k} 差 {v}" for k, v in mismatches.items())
            raise ContractError(
                f"交付集合判据不成立（方案 §15：requested = 教案 = 审核 = 交互 = 页面 = 包）：{detail}")
        return {"requested": len(wanted), "groups": {k: len(v) for k, v in groups.items()}}

    def digest(self) -> str:
        return digest_of({"release_id": self.release_id, "book_id": self.book_id,
                          "scope_kind": self.scope_kind, "selectors": self.selectors,
                          "parse_revision": self.parse_revision,
                          "context_revision": self.context_revision,
                          "units": [u.identity() for u in self.units],
                          "chapters": [c.identity() for c in self.chapters],
                          "resources": [r.identity() for r in self.resources],
                          "checks": [c.get("名称") or c.get("id") or c.get("name")
                                     for c in self.checks]})

    def as_dict(self) -> dict:
        return {"schema_version": self.schema_version, "release_id": self.release_id,
                "book_id": self.book_id, "scope_kind": self.scope_kind,
                "selectors": dict(self.selectors), "parse_revision": self.parse_revision,
                "context_revision": self.context_revision,
                "registry_revision": self.registry_revision,
                "units": [u.as_dict() for u in self.units],
                "chapters": [c.as_dict() for c in self.chapters],
                "resources": [r.as_dict() for r in self.resources],
                "checks": list(self.checks), "scope_accepted": self.scope_accepted,
                "full_book_ready": self.full_book_ready, "run_id": self.run_id,
                "notes": list(self.notes)}


__all__ = ["SET_KINDS", "ReleaseManifest"]
