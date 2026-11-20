"""CLI 与 HTTP 共用的只读装配和执行入口。

计划不发送请求。执行持有跨进程书籍锁；冷启动先形成 catalog，再在同一个
运行记录中固定 scope，随后完成全书概念索引及所选单元生成。
"""
from __future__ import annotations

from dataclasses import dataclass, replace, field
import json
import re
import time
import unicodedata
from pathlib import Path

from ..contracts.request import RunRequest
from ..contracts.enums import SUCCESSFUL_EXECUTION_STATES
from ..contracts.scope import ScopePlan
from ..core.registry import load_registry
from ..core.workspace import Workspace, open_workspace
from ..errors import (ContractError, AifsError, BudgetExceeded, Cancelled,
                      EXIT_USAGE, EXIT_BUDGET, EXIT_CANCELLED)
from ..core.jsonio import write_json_atomic
from ..core.digest import digest_file, digest_of
from ..providers.factory import configured_providers
from ..providers.capabilities import probe
from .execution_control import ExecutionControl, ExecutionPolicy, load_policy
from .graph import GRAPH_VERSION, ExecutionGraph, build_graph, PARSE_STAGES
from .planner import plan as build_plan, read_catalog
from .runner import RunResult, Runner
from .services import CancelToken, Providers, Services, StageRegistry
from .state import RunStore, book_runs_root
from .stubs import smoke_registry
from .registry import release_registry
from .events import KIND_PLANNED


@dataclass
class Engine:
    """一次运行的装配结果，便于测试与界面直接取用。"""
    workspace: Workspace
    request: RunRequest
    plan: ScopePlan
    graph: ExecutionGraph
    services: Services
    until: str | None = None
    scope_identity: dict = field(default_factory=dict)

    def plan_payload(self) -> dict:
        payload = dict(self.plan.as_dict())
        payload["_meta"] = {"file_type": "plan_view", "schema_version": "1.0"}
        payload["title"] = self.services.context.title
        payload["prepare_policy"] = self.request.prepare_policy.value
        payload["requests"] = self.request.as_dict()
        payload["graph"] = {"nodes": len(self.graph.nodes),
                            "order": list(self.graph.order()),
                            "truncated_at": self.graph.truncated_at}
        return payload


def policy_from(request: RunRequest, workspace: Workspace, parallelism: int | None = None) -> ExecutionPolicy:
    """`parallelism` overrides the request only for a stopped run's resume (plan E7)."""
    cfg=load_policy(workspace,request.profile.value,request.goal.value)
    return cfg.clamped(parallelism or request.parallelism)



def default_stage_registry(request: RunRequest, workspace: Workspace) -> StageRegistry:
    """按档位选阶段实现。**这是"哪些阶段现在真的能做"的唯一一处判断。**"""
    profile = request.profile.value
    if profile == "smoke":
        return smoke_registry()
    return release_registry()


def _graph_for_plan(plan: ScopePlan, until: str | None) -> ExecutionGraph:
    # An unresolved scope has only the book preparation prefix, never guessed units.
    if plan.resolved_scope is None:
        return build_graph(replace(plan, resolved_scope=()), until=(
            until if until in PARSE_STAGES else 'body'))
    return build_graph(plan, until=until)


def _source_tokens(text):
    return re.findall(r'\\[A-Za-z]+|[A-Za-z0-9_]+|[^\s]',
        re.sub(r'(?m)^#{1,6}[ \t]+', '', unicodedata.normalize('NFC', text)))


def _unit_identity(context, unit):
    """Host evidence allows only known formatting equivalence, not paraphrases."""
    path = (context.parse / unit.original_text).resolve()
    readable = path.is_relative_to(context.parse.resolve()) and path.is_file()
    text = unicodedata.normalize('NFC', path.read_text(encoding='utf-8-sig')) if readable else ''
    # Heading depth and whitespace are formatting. Preserve lexical word
    # boundaries, TeX commands, every number and mathematical punctuation.
    tokens = _source_tokens(text)
    return {'unit_id':unit.unit_id, 'chapter':unit.chapter,
            'title':unicodedata.normalize('NFC', unit.title.strip()),
            'anchor':unicodedata.normalize('NFC', unit.anchor.strip()),
            'source_path':unit.original_text,
            'source_sha256':digest_file(path) if readable else None,
            'source_tokens_sha256':digest_of(tokens) if readable and tokens else None}


def _capture_scope_identity(request, context):
    if request.selectors.kind.value != 'units':
        return {}
    catalog = read_catalog(context.parse)
    if catalog is None:
        return {}  # a genuine cold start has no older semantic identity to bind
    by_id = catalog.by_id()
    units = {key:_unit_identity(context, by_id[key]) if key in by_id else None
             for key in request.selectors.values}
    return {'book_id':context.book_id, 'catalog_hash':catalog.digest, 'units':units}


class ScopeIdentityError(ContractError):
    """The old selector cannot safely authorize the rebuilt textbook unit."""


def validate_scope_identity(identity, context):
    """Before concepts or content dispatch, prove a rebuilt ID still means the same source."""
    if not identity:
        return {}
    if identity.get('book_id') != context.book_id:
        raise ScopeIdentityError('请求单元来源身份跨书变化；请重新选择范围')
    catalog = read_catalog(context.parse)
    if catalog is None:
        raise ScopeIdentityError('请求单元来源身份无法核对：重建后仍无目录')
    units = catalog.by_id(); evidence = {}
    for key, expected in identity['units'].items():
        if expected is None or key not in units:
            raise ScopeIdentityError(f'请求单元 {key} 的来源身份消失或原目录未记录；请检查新目录并重新选择，未派发概念或讲解')
        actual = _unit_identity(context, units[key])
        for name in ('chapter', 'title', 'anchor'):
            if actual[name] != expected[name]:
                raise ScopeIdentityError(f'请求单元 {key} 的来源身份/锚点变化：{name} '
                    f'{expected[name]!r} → {actual[name]!r}；不能因编号相同静默换节，请重新选择')
        matches = [u for u in catalog.units if (u.chapter, unicodedata.normalize('NFC',u.title.strip()),
            unicodedata.normalize('NFC',u.anchor.strip())) == (actual['chapter'],actual['title'],actual['anchor'])]
        if len(matches) != 1 or not actual['title']:
            raise ScopeIdentityError(f'请求单元 {key} 的标题/锚点存在歧义，无法证明来源身份')
        if not expected['source_tokens_sha256'] or not actual['source_tokens_sha256']:
            raise ScopeIdentityError(f'请求单元 {key} 缺可核对的原文，来源身份无法证明；请复核范围')
        exact = actual['source_sha256'] == expected['source_sha256']
        if not exact and actual['source_tokens_sha256'] != expected['source_tokens_sha256']:
            raise ScopeIdentityError(f'请求单元 {key} 的原文语义符号发生变化，来源身份无法以精确格式等价证明；'
                                '请复核新原文并重新选择，未派发概念或讲解')
        evidence[key] = {'before':expected, 'after':actual,
                         'match':'exact_source_bytes' if exact else 'exact_source_tokens'}
    return evidence


def build_engine(request: RunRequest, workspace: Workspace | str | Path | None = None, *,
                 stage_registry: StageRegistry | None = None,
                 providers: Providers | None = None,
                 cancel: CancelToken | None = None,
                 until: str | None = None,
                 parallelism: int | None = None) -> Engine:
    """**只读装配**：建 Engine 不创建运行目录、不写任何产物。"""
    if getattr(stage_registry, "graph_version", GRAPH_VERSION) != GRAPH_VERSION:
        raise ContractError(f"新运行固定 graph {GRAPH_VERSION}；旧图仅可只读检查")
    request.validate()
    ws = workspace if isinstance(workspace, Workspace) else open_workspace(workspace)
    registry = load_registry(ws)
    from .preparation import prepared_context
    context = registry.resolve(request.book_id)
    if request.prepare_policy.value != "refresh":
        context = prepared_context(context)
    plan = build_plan(request, registry, context=context)
    # Unknown scope is preserved in the plan; the first graph contains book stages only.
    graph = _graph_for_plan(plan, until)

    services = Services(
        workspace=ws,
        registry=registry,
        context=context,
        capabilities=probe(ws),
        control=ExecutionControl(policy_from(request, ws, parallelism)),
        cancel=cancel or CancelToken(),
        providers=providers if providers is not None else configured_providers(request, ws),
        stages=stage_registry or default_stage_registry(request, ws),
    )
    identity = _capture_scope_identity(request, context) if plan.resolved_scope is None else {}
    return Engine(workspace=ws, request=request, plan=plan, graph=graph, services=services,
                  until=until, scope_identity=identity)


def execute(engine: Engine, *, resume_run: str | None = None,
            store: RunStore | None = None) -> tuple[RunResult, RunStore]:
    """Run under the shared book lease, optionally using the HTTP-created queued store."""
    from .lease import BookLease
    if engine.graph.graph_version != GRAPH_VERSION:
        raise ContractError(f"旧图只读；执行只允许 graph {GRAPH_VERSION}")
    root = book_runs_root(engine.workspace.root, engine.request.book_id)
    # Reject old graphs before even the lease metadata can be written.
    if resume_run:
        from ..core.jsonio import read_json_soft
        from .resource_binding import assert_resumable
        assert_resumable(state=read_json_soft(root / resume_run / "state.json"),
                         plan=read_json_soft(root / resume_run / "plan.json"),run_id=resume_run)
    with BookLease(root.parent):
        result, actual_store = _execute_locked(engine, resume_run=resume_run,
                                                store=store)
        if result.release_ready:
            try:
                from ..delivery.publish import publish_candidate
                def publish_checkpoint():
                    engine.services.cancel.checkpoint()
                    engine.services.control.check()
                publish_checkpoint()
                publication = publish_candidate(store=actual_store, context=engine.services.context,
                    scope=tuple(engine.plan.resolved_scope or ()),
                    scope_kind=engine.request.selectors.kind.value,
                    before_switch=publish_checkpoint)
                result.publication = publication
                state = actual_store.load_state()
                state['publication'] = publication
                actual_store.save_state(state)
                actual_store.emit('release.published', message='HTML 已验收并原子发布', data=publication)
            except (AifsError, OSError, ValueError) as exc:
                status, code = 'failed', EXIT_USAGE
                if isinstance(exc, Cancelled):
                    status, code = 'cancelled', EXIT_CANCELLED
                elif isinstance(exc, BudgetExceeded):
                    status, code = 'budget_stopped', EXIT_BUDGET
                result = replace(result, status=status, exit_code=code,
                                 scope_accepted=False, release_ready=False, publication={},
                                 failure_stage='publish', recovery_hint=str(exc))
                actual_store.set_status(status, exit_code=code, scope_accepted=False,
                    release_ready=False, publication={}, failure_stage='publish',
                    recovery_hint=str(exc), stop_reason=str(exc),
                    execution=engine.services.control.snapshot())
                actual_store.emit('release.failed', message=str(exc),
                                  data={'status': status, 'exit_code': code})
        # One terminal summary, after preparation and publication have settled.
        # Scope=None is meaningful when preparation never established a catalog.
        write_json_atomic(actual_store.root / 'final.json', {
            **result.as_dict(),
            'scope': (None if engine.plan.resolved_scope is None
                      else list(engine.plan.resolved_scope)),
            'scope_kind': engine.request.selectors.kind.value,
        })
        return result, actual_store


def _execute_locked(engine: Engine, *, resume_run=None,
                    store=None) -> tuple[RunResult, RunStore]:
    root = book_runs_root(engine.workspace.root, engine.request.book_id)
    frozen_scope = None
    saved_context = {}
    if resume_run:
        if store is not None and store.run_id != resume_run:
            raise ContractError('resume_run 与传入 store 不一致')
        # ── T05 挂点 2：缺 graph_version 的旧 run **可读、不可原地 resume**（§6.3）。
        #    判定必须在 `RunStore.open` **之前**做，而且只读（`read_json_soft`）——
        #    `open` 会追加事件／落盘，于是"拒绝升级"这句话就不再是零改动。
        from ..core.jsonio import read_json_soft
        from .resource_binding import LegacyRunError, resume_guard
        run_dir = root / resume_run
        decision = resume_guard(state=read_json_soft(run_dir / "state.json"),
                                plan=read_json_soft(run_dir / "plan.json"),
                                run_id=resume_run)
        if not decision.allowed:
            raise LegacyRunError(decision)
        store = store or RunStore.open(root, resume_run)
        previous = store.load_state()
        engine.scope_identity = previous.get('requested_unit_identity', engine.scope_identity)
        # Capture the frozen scope before resolving a mutable preparation copy.
        saved_plan = json.loads(store.plan_path.read_text())
        frozen_scope = previous.get('frozen_scope')
        if frozen_scope is None:
            frozen_scope = saved_plan.get('resolved_scope')
        if previous.get('request_digest') != engine.request.digest():
            raise ContractError('续跑请求指纹变化；请求变化必须新建运行')
    elif store is None:
        store = RunStore.create(root, engine.request, engine.plan, control=engine.services.control)
    elif store.load_state().get('request_digest') != engine.request.digest():
        raise ContractError('queued store 请求与 engine 不一致')
    engine.services.store = store
    if resume_run:
        from .preparation import source_fingerprint
        from ..core.jsonio import read_json_soft
        saved_context = read_json_soft(store.work / 'preparation-context.json') or {}
        if saved_context:
            original = engine.services.registry.resolve(engine.request.book_id)
            if saved_context.get('source_digest') != source_fingerprint(original):
                raise ContractError('原始解析输入已变化，不能续跑旧的准备副本；请新建运行')
            parse = Path(saved_context.get('parse', ''))
            if parse != store.work / 'prepared-parse' or not parse.is_dir():
                raise ContractError('续跑准备上下文路径无效')
            engine.services.context = replace(engine.services.context, parse=parse)
            prefix_complete = all((previous.get('nodes', {}).get(stage) or {}).get('execution_state')
                in SUCCESSFUL_EXECUTION_STATES for stage in PARSE_STAGES)
            plan = build_plan(engine.request, engine.services.registry, context=engine.services.context,
                              preparation_completed=prefix_complete)
            engine.plan = plan
            engine.graph = _graph_for_plan(plan, engine.until)
    if resume_run:
        if engine.plan.resolved_scope is not None:
            validate_scope_identity(engine.scope_identity, engine.services.context)
        if frozen_scope is not None:
            if engine.plan.resolved_scope is not None:
                if frozen_scope != list(engine.plan.resolved_scope):
                    raise ContractError('当前目录使已冻结范围变化，请新建运行并复核范围')
            else:
                # A rebuilt preparation copy may not have exported its catalog
                # yet. Unknown scope is not evidence of a changed known scope.
                # A completed prefix losing its catalog remains corruption.
                prefix = PARSE_STAGES
                previous_nodes = previous.get('nodes') or {}
                incomplete = any((previous_nodes.get(stage) or {}).get('execution_state')
                                 not in SUCCESSFUL_EXECUTION_STATES for stage in prefix)
                if not saved_context or not incomplete:
                    raise ContractError('当前目录使已冻结范围变化，请新建运行并复核范围')
        write_json_atomic(store.plan_path, engine.plan.as_dict())
        state = store.load_state()
        state['request_digest'] = engine.request.digest()
        state['plan_digest'] = engine.plan.digest()
        if frozen_scope is not None:
            # Keep this across repeated failed prefix retries even while the
            # current plan honestly records resolved_scope=None.
            state['frozen_scope'] = list(frozen_scope)
        store.save_state(state)
        store.emit(KIND_PLANNED, message='resume：核对当前输入后按节点指纹复用')
    elif engine.scope_identity:
        state = store.load_state()
        state['requested_unit_identity'] = engine.scope_identity
        store.save_state(state)
    store.emit(KIND_PLANNED, message='已固定请求与初始范围',
               data={'scope': engine.plan.resolved_scope, 'nodes': len(engine.graph.nodes)})
    runner = Runner(engine.services, engine.graph, store,
                    resume=bool(resume_run))
    result = runner.run()
    if engine.plan.resolved_scope is not None:
        return result, store
    # The store can still contain blocked downstream nodes from the first full
    # graph. Only failures in this preparation graph veto catalog resolution;
    # downstream nodes are retried after the frozen-scope check below.
    prefix_nodes = set(engine.graph.by_id())
    if ((set(result.failed_nodes) | set(result.blocked_nodes)) & prefix_nodes
            or result.status in {'cancelled', 'budget_stopped'}):
        return result, store
    if engine.until in {'mineru', 'toc', 'structure', 'export'}:
        return result, store
    try:
        resolved = build_plan(engine.request, engine.services.registry,
                              context=engine.services.context, preparation_completed=True)
        if resolved.resolved_scope is None:
            raise ContractError('准备阶段结束但未形成 chapters.json；不能继续生成')
        if frozen_scope is not None and list(resolved.resolved_scope) != frozen_scope:
            raise ContractError('新目录使已冻结范围变化，请新建运行并复核范围；未派发概念或内容生成')
        scope_evidence = validate_scope_identity(engine.scope_identity, engine.services.context)
        engine.plan = resolved
        engine.graph = build_graph(resolved, until=engine.until)
        write_json_atomic(store.plan_path, resolved.as_dict())
        state = store.load_state()
        state['plan_digest'] = resolved.digest()
        state['frozen_scope'] = list(resolved.resolved_scope)
        state['scope_identity_evidence'] = scope_evidence
        store.save_state(state)
        store.emit(KIND_PLANNED, message='全书目录已形成，已校验并固定所选范围',
                   data={'scope': list(resolved.resolved_scope), 'nodes': len(engine.graph.nodes)})
    except AifsError as exc:
        status = 'blocked' if isinstance(exc, ScopeIdentityError) else 'failed'
        result = replace(result, status=status, exit_code=EXIT_USAGE,
                         scope_accepted=False, release_ready=False,
                         failure_stage='scope', recovery_hint=str(exc))
        store.set_status(status, exit_code=EXIT_USAGE, scope_accepted=False,
                         release_ready=False, failure_stage='scope', recovery_hint=str(exc))
        store.emit('run.finished', message=str(exc), data=result.as_dict())
        return result, store
    # Prefix outputs stay in this run; only downstream stages are executed here.
    completed = set(runner.graph.by_id())
    prefix_seconds = result.elapsed_seconds
    result = Runner(engine.services, engine.graph, store, resume=True,
                    initial_completed=completed).run()
    result.elapsed_seconds += prefix_seconds
    return result, store


def run_request(request: RunRequest, workspace: Workspace | str | Path | None = None, *,
                until: str | None = None, stage_registry: StageRegistry | None = None,
                providers: Providers | None = None, cancel: CancelToken | None = None) -> tuple[RunResult, RunStore, Engine]:
    engine = build_engine(request, workspace, stage_registry=stage_registry,
                          providers=providers, cancel=cancel, until=until)
    result, store = execute(engine)
    return result, store, engine


def resume_request(request: RunRequest, run_id: str,
                   workspace: Workspace | str | Path | None = None, *,
                   until: str | None = None,
                   stage_registry: StageRegistry | None = None,
                   providers: Providers | None = None,
                   cancel: CancelToken | None = None,
                   parallelism: int | None = None) -> tuple[RunResult, RunStore, Engine]:
    """`parallelism`：修改计划 E7，停止后续跑可改并发；请求指纹不变，执行记录写交接。"""
    engine = build_engine(request, workspace, stage_registry=stage_registry,
                          providers=providers, cancel=cancel, until=until, parallelism=parallelism)
    result, store = execute(engine, resume_run=run_id)
    return result, store, engine


__all__ = ["Engine", "policy_from", "default_stage_registry", "build_engine", "execute",
           "run_request", "resume_request"]
