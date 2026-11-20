"""契约里的枚举：**执行状态与质量状态分开**。

方案 §9 逐字：

> ```text
> execution_state = succeeded | cached | blocked | failed | cancelled | not_applicable
> quality_state   = accepted | needs_review | rejected | not_checked
> ```
>
> 审核成功写出"冻结"报告属于 succeeded + needs_review；生成了错误图属于 succeeded + rejected。
> required 阶段必须 succeeded/cached 且 accepted。not_applicable 只能由明确政策与证据产生，
> 不允许"缺工具/没运行"冒充。

为什么必须两个轴而不是一个状态机：一套 `ok/failed` 会把两件不同的事压成一件——
"流程跑完了但内容不合格"（该去返修）和"流程根本没跑成"（该去修环境）。
本项目历史上正是把这两者混在 `exit 1` 里，导致"产物其实已合格"也被判成整章失败。
"""
from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """字符串枚举：JSON 里就是字符串，比较/序列化都不用额外转换。"""

    def __str__(self) -> str:      # pragma: no cover - 只为打印好看
        return str(self.value)


class ExecutionState(StrEnum):
    SUCCEEDED = "succeeded"
    CACHED = "cached"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NOT_APPLICABLE = "not_applicable"

    @property
    def can_feed_required_stage(self) -> bool:
        """能不能作为 next stage 的输入。`blocked/cancelled/not_applicable` 都不行。"""
        return self in (ExecutionState.SUCCEEDED, ExecutionState.CACHED)


# Completion summaries include an explicit not-applicable result. Dependency
# admission still checks the consuming stage's policy separately.
SUCCESSFUL_EXECUTION_STATES = frozenset({ExecutionState.SUCCEEDED,
    ExecutionState.CACHED, ExecutionState.NOT_APPLICABLE})


class QualityState(StrEnum):
    ACCEPTED = "accepted"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"
    NOT_CHECKED = "not_checked"

    @property
    def blocks_release(self) -> bool:
        """正式发布只接受 `accepted`（方案 §9）。"""
        return self is not QualityState.ACCEPTED


class Profile(StrEnum):
    """运行档位（方案 §4 的表）。"""
    RELEASE = "release"
    REPLAY = "replay"
    SMOKE = "smoke"

    @property
    def allows_real_services(self) -> bool:
        #: replay 只取固定响应；smoke 用替身外部服务。**二者都不许回退真实服务。**
        return self is Profile.RELEASE

    @property
    def release_ready_possible(self) -> bool:
        return self is Profile.RELEASE


class Goal(StrEnum):
    """请求目标。`prepare` 完成基础准备也算成功，但 `release_ready` 恒为 false。"""
    PREPARE = "prepare"
    DELIVER = "deliver"

    @property
    def release_ready_possible(self) -> bool:
        return self is Goal.DELIVER


class SelectorKind(StrEnum):
    """范围选择器：`--chapters` / `--units` / `--all` **互斥且必须选一项**。"""
    CHAPTERS = "chapters"
    UNITS = "units"
    ALL = "all"


class PreparePolicy(StrEnum):
    """`aifs prepare` 的策略（方案 §4）。不把大量 skip 开关当正常生产控制方式。"""
    ENSURE = "ensure"
    REUSE = "reuse"
    REFRESH = "refresh"


class DeliveryFormat(StrEnum):
    """Only implemented delivery formats are accepted at every request boundary."""
    CHAPTER_HTML = "chapter-html"


__all__ = ["StrEnum", "ExecutionState", "QualityState", "Profile", "Goal",
           "SelectorKind", "PreparePolicy", "DeliveryFormat", "SUCCESSFUL_EXECUTION_STATES"]
