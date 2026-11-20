"""统一契约（方案 §6）。

> 公共服务接口定为 `plan(request, registry) -> ScopePlan`、
> `execute(request, services) -> RunResult`、`check(release_ref, policy) -> CheckReport`。
> 阶段实现统一 `run(context, task, inputs, services) -> StageResult`。
> 计划不调用 execute；阶段不自行查找另一份书籍默认值或读取无版本的全局最新文件。

本包只放**数据契约**（不可变、可序列化、构造即校验），不放编排逻辑。
接口本身（`plan`/`execute`/`check`）在 M2 的 `pipeline/` 里落地。
"""
from __future__ import annotations

from .artifact import ArtifactRef
from .enums import (DeliveryFormat, ExecutionState, Goal, PreparePolicy, Profile,
                    QualityState, SelectorKind, StrEnum)
from .release import SET_KINDS, ReleaseManifest
from .request import (DEFAULT_PARALLELISM, MAX_PARALLELISM, SCHEMA_VERSION, Budget,
                      RunRequest, Selectors)
from .scope import Estimate, Invalidation, PreparationTask, ScopePlan
from .stage import StageResult

__all__ = [
    "ArtifactRef",
    "StrEnum", "ExecutionState", "QualityState", "Profile", "Goal", "SelectorKind",
    "PreparePolicy", "DeliveryFormat",
    "ReleaseManifest", "SET_KINDS",
    "SCHEMA_VERSION", "MAX_PARALLELISM", "DEFAULT_PARALLELISM",
    "Selectors", "Budget", "RunRequest",
    "Estimate", "Invalidation", "PreparationTask", "ScopePlan",
    "StageResult",
]
