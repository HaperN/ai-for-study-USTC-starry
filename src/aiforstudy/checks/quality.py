"""质量报告与**交付声明**：问题汇总按当前 scope 自动重算。

方案 §6：

> 问题汇总：原 `problems` **并入质量报告与交付声明**，自动重算当前 scope，
> **不依赖是否已有 `problems.json`**。

方案 §9：

> 正式发布至少要求：……目标单元正文规则无硬问题；所有 P0/P1 内容工单关闭且独立验收 accepted；
> 必要配图终态达标；交互与教案同版；页面/资源/离线交付检查 accepted。
> **低优先级建议可保留，但在报告中列出，不能掩盖缺必需产物。**

"不依赖是否已有 problems.json"是一句很关键的话：它把"有没有那份文件"从判据里拿掉。
现实里那份文件会过期、会只覆盖上次的范围、会被漏生成 —— 任何一种情况下，
按它判断都会得出"没问题"，而**当前 scope 的真正问题根本没被算过**。
所以这里只吃**当次检查的结果**，`problems.json` 最多作为历史证据引用。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..errors import ContractError
from ..blocks.audit.contracts import SEVERITIES, BLOCKING_SEVERITIES

STATUS_OK = "accepted"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_FAILED = "failed"


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    scope_key: str
    why: str
    evidence: str = ""

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ContractError(f"严重度必须是 P0/P1/P2，实际 {self.severity!r}")
        if not self.why.strip():
            raise ContractError(f"{self.code}: 必须写 why")

    @property
    def blocking(self) -> bool:
        return self.severity in BLOCKING_SEVERITIES

    def as_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity, "scope_key": self.scope_key,
                "why": self.why, "evidence": self.evidence, "blocking": self.blocking}


@dataclass
class QualityReport:
    book_id: str
    release_id: str
    scope_units: tuple[str, ...]
    findings: tuple[Finding, ...] = ()
    checks: tuple[dict, ...] = ()
    stale_chapters: tuple[str, ...] = ()
    legacy_problems_ref: str = ""
    notes: tuple[str, ...] = ()

    # ── 判据
    @property
    def blocking(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.blocking)

    @property
    def suggestions(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if not f.blocking)

    def findings_outside_scope(self) -> tuple[Finding, ...]:
        """范围外的问题**列出来但不挡发布** —— 但要看得见（不许掩盖）。"""
        inside = set(self.scope_units)
        return tuple(f for f in self.findings if f.scope_key not in inside)

    def failed_checks(self) -> tuple[dict, ...]:
        return tuple(c for c in self.checks if not c.get("通过", c.get("accepted")))

    def blocked_checks(self) -> tuple[dict, ...]:
        return tuple(c for c in self.checks if c.get("状态") == "blocked"
                     or c.get("status") == "blocked")

    @property
    def status(self) -> str:
        if self.blocking or self.failed_checks():
            return STATUS_FAILED
        if self.suggestions or self.blocked_checks() or self.stale_chapters:
            return STATUS_NEEDS_REVIEW
        return STATUS_OK

    @property
    def release_ready(self) -> bool:
        """**交付声明**：能不能把这份产物当正式交付。"""
        return (self.status == STATUS_OK and not self.findings_outside_scope()
                and not self.blocked_checks())

    def as_dict(self) -> dict:
        return {"_meta": {"file_type": "quality_report", "schema_version": "1.0",
                          "generator": "aiforstudy.checks.quality",
                          "field_notes": {
                              "findings": "**按当前 scope 重算**，不依赖是否已有 problems.json",
                              "blocking": "P0/P1：挡发布",
                              "suggestions": "P2：可保留，但必须在报告里列出，不许掩盖缺必需产物",
                              "outside_scope": "范围外的问题：列出但不挡本次发布",
                              "release_ready": "**交付声明**",
                          }},
                "book_id": self.book_id, "release_id": self.release_id,
                "scope_units": list(self.scope_units),
                "status": self.status, "release_ready": self.release_ready,
                "findings": [f.as_dict() for f in self.findings],
                "blocking": [f.as_dict() for f in self.blocking],
                "suggestions": [f.as_dict() for f in self.suggestions],
                "outside_scope": [f.as_dict() for f in self.findings_outside_scope()],
                "checks": list(self.checks),
                "failed_checks": list(self.failed_checks()),
                "blocked_checks": list(self.blocked_checks()),
                "stale_chapters": list(self.stale_chapters),
                "legacy_problems_ref": self.legacy_problems_ref,
                "notes": list(self.notes)}


def build_quality_report(*, book_id: str, release_id: str, scope_units: tuple[str, ...],
                         findings: tuple[Finding, ...], checks: tuple[dict, ...] = (),
                         stale_chapters: tuple[str, ...] = (),
                         legacy_problems_path: str = "") -> QualityReport:
    """按**当前 scope** 重算质量报告与交付声明。

    `legacy_problems_path` 只作为**历史证据引用**记在报告里，不参与判据 ——
    方案 §6 原话是"不依赖是否已有 problems.json"。
    """
    deduped: dict[tuple[str, str], Finding] = {}
    for finding in findings:
        deduped.setdefault((finding.code, finding.scope_key), finding)
    notes = []
    if stale_chapters:
        notes.append(f"有 {len(stale_chapters)} 个章因基础版本变更而过期："
                     f"{list(stale_chapters)[:5]}。**明确记录 stale，不自动重生成**（§10）。")
    if legacy_problems_path:
        notes.append(f"历史问题汇总只作证据引用（{legacy_problems_path}）；"
                     f"本次判据全部来自**当前 scope 的重算结果**。")
    return QualityReport(book_id=book_id, release_id=release_id,
                         scope_units=tuple(scope_units),
                         findings=tuple(deduped.values()), checks=tuple(checks),
                         stale_chapters=tuple(stale_chapters),
                         legacy_problems_ref=legacy_problems_path, notes=tuple(notes))


__all__ = ["BLOCKING_SEVERITIES", "STATUS_OK", "STATUS_NEEDS_REVIEW", "STATUS_FAILED",
           "Finding", "QualityReport", "build_quality_report"]
