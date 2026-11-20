"""编排层（方案 §3 的 `pipeline/`：graph、planner、runner、state、budget、events）。

对外可用的入口（方案 §6）：

* `plan(request, registry) -> ScopePlan`   —— 只读计划，零副作用
* `execute(request, services) -> RunResult` —— 见 `engine.run_request`
* `check(release_ref, policy) -> CheckReport` —— M7 落地

内部的职责划分：

| 模块 | 管什么 | 不管什么 |
|---|---|---|
| `planner` | 请求 → 范围 + 准备任务 + 估算 | 不建目录、不写产物 |
| `graph` | 计划 → **一张**执行图（单/多/整本同图） | 不执行 |
| `runner` | 按图跑、结构化成败、失败沿依赖传播 | 不知道阶段内部在干什么 |
| `state` | 运行目录、原子状态、按指纹复用 | 不判断业务对错 |
| `budget` | 预约/实报/停止/收尾窗口 | 不决定质量 |
| `events` | 追加式事件流 + 短轮询游标 | 不做业务判断 |
| `services` | 注入外部边界（替身可换） | 不含业务逻辑 |
| `engine` | 装配以上一切 | 不含阶段逻辑 |
"""
from __future__ import annotations

from .execution_control import ExecutionControl, ExecutionPolicy, ExecutionScope, load_policy
from .engine import (Engine, build_engine, default_stage_registry, execute, policy_from,
                     resume_request, run_request)
from .events import Event, EventLog
from .graph import (CANONICAL_STAGES, STAGE_BY_NAME, ExecutionGraph, Node, StageSpec,
                    build_graph, graphs_equivalent_for_scope, stage)
from .planner import Catalog, Unit, expand_selector_tokens, plan, read_catalog
from .runner import Runner, RunResult
from .services import (CancelToken, Providers, Services, StageContext, StageRegistry,
                       not_implemented, stub_impls)
from .snapshot import (CONTEXT_MISSING, CONTEXT_TOO_LARGE, ContextBundle, RunSnapshot,
                       all_unit_contexts, freeze, unit_context)
from .state import NodeState, RunStore, book_runs_root, latest_run_id, read_state_summary

__all__ = [
    "ExecutionControl", "ExecutionPolicy", "ExecutionScope", "load_policy",
    "Engine", "build_engine", "default_stage_registry", "execute", "policy_from",
    "resume_request", "run_request",
    "Event", "EventLog",
    "CANONICAL_STAGES", "STAGE_BY_NAME", "ExecutionGraph", "Node", "StageSpec",
    "build_graph", "graphs_equivalent_for_scope", "stage",
    "Catalog", "Unit", "expand_selector_tokens", "plan", "read_catalog",
    "Runner", "RunResult",
    "CancelToken", "Providers", "Services", "StageContext", "StageRegistry",
    "not_implemented", "stub_impls",
    "CONTEXT_MISSING", "CONTEXT_TOO_LARGE", "ContextBundle", "RunSnapshot",
    "all_unit_contexts", "freeze", "unit_context",
    "NodeState", "RunStore", "book_runs_root", "latest_run_id", "read_state_summary",
]
