"""执行器：按图跑，**结构化地**成功与失败。

方案 §9 是这一模块的规格书：

> `execution_state = succeeded | cached | blocked | failed | cancelled | not_applicable`
> `quality_state   = accepted | needs_review | rejected | not_checked`
> …… required 阶段必须 succeeded/cached 且 accepted。
> `not_applicable` 只能由明确政策与证据产生，不允许"缺工具/没运行"冒充。

> final.json 始终包含 status、goal、profile、scope、release_ready、失败阶段与恢复建议。
> **同一阶段失败不得继续拿上趟输出作为本次输入**；无依赖关系的其他所选章可以继续完成
> 候选产物，但整体范围不能发布。**失败时不自动缩小用户指定的范围。**

最后这三句决定了两件事：

1. **失败要沿依赖传播**：一个单元挂了，它的下游标 `blocked`，**不能**拿旧输出接着跑；
2. **失败不要一刀切**：与它无依赖关系的其他章照常完成候选产物（这样才能"部分可恢复"），
   但整体 `release_ready` 必须是 false。

并行度、取消检查点、预算护栏都在这里收口；阶段实现本身不碰这些。
"""
from __future__ import annotations

import json as _json
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import deepcopy
from contextvars import copy_context
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..contracts.artifact import ArtifactRef
from ..contracts.enums import ExecutionState, QualityState
from ..blocks.audit.contracts import PatchOutcome
from ..contracts.stage import StageResult
from ..core.digest import digest_file, digest_of
from ..core.review_mode import (configured_review_mode, review_mode, reviews_frozen,
                               substantive_reviews_enabled)
from ..errors import (EXIT_BUDGET, EXIT_CANCELLED, EXIT_OK, EXIT_QUALITY, EXIT_USAGE,
                      AifsError, BudgetExceeded, Cancelled, ContractError)
from .events import (KIND_BUDGET_STOP, KIND_CANCELLED, KIND_RUN_FINISHED, KIND_RUN_STARTED,
                     KIND_STAGE_BLOCKED, KIND_STAGE_CACHED, KIND_STAGE_FAILED,
                     KIND_STAGE_FINISHED, KIND_STAGE_SKIPPED, KIND_STAGE_STARTED)
from .events import KIND_NOTE
from .network_gate import NETWORK_PAUSE_REASON
from .graph import GRAPH_VERSION, ExecutionGraph, Node, dispatch_quality_gate
from .execution_control import ExecutionScope
from .repair import RepairBatch, RepairCoordinator, StopDecision, UnitRepairState
from .services import Services, StageContext
from .state import NodeState, RunStore

#: 一个节点的输入指纹版本：阶段实现或输入口径变了就要动它，否则会错误复用
RUN_USAGE_STAGE = "__run__"          # 运行汇总在账本里的 stage 名（与各阶段的模型调用区分开）

STAGE_VERSION = "2.1"


@dataclass
class RunResult:
    run_id: str
    book_id: str
    goal: str
    profile: str
    status: str
    exit_code: int
    scope_accepted: bool = False
    release_ready: bool = False
    publication: dict = field(default_factory=dict)
    preview_available: bool = False
    partial: bool = False
    execution_counts: dict[str, int] = field(default_factory=dict)
    quality_counts: dict[str, int] = field(default_factory=dict)
    quality_overrides: dict[str, dict] = field(default_factory=dict)
    failed_nodes: list[str] = field(default_factory=list)
    blocked_nodes: dict[str, str] = field(default_factory=dict)
    truncated_at: str | None = None
    failure_stage: str | None = None
    recovery_hint: str = ""
    elapsed_seconds: float = 0.0
    #: 这次运行**用的是哪一份**返修／预算策略（`config/execution.json` 的路径，
    #: 或 `"builtin-defaults"`），以及它的内容摘要。
    #
    # ⚠️ 为什么要进运行记录：验收与复现时最该能回答的问题之一就是
    # "这次跑的是**受版本控制的策略文件**，还是**代码里的兜底默认值**"。
    # 以前这一条只能靠"看工作区里有没有那个文件"去推断，**运行记录本身不作证**。
    # `budget.py` 早就把 `policy_source` 设在策略对象上了，只是没有出口。
    policy_source: str = ""
    policy_digest: str = ""
    review_mode: str = "full"
    review_status: str = "full"

    def as_dict(self) -> dict:
        return {"run_id": self.run_id, "book_id": self.book_id, "goal": self.goal,
                "profile": self.profile, "status": self.status, "exit_code": self.exit_code,
                "scope_accepted": self.scope_accepted, "release_ready": self.release_ready,
                "publication": dict(self.publication),
                "execution_counts": dict(self.execution_counts),
                "quality_counts": dict(self.quality_counts),
                "quality_overrides": dict(self.quality_overrides),
                'preview_available': self.preview_available, 'partial': self.partial,
                "failed_nodes": list(self.failed_nodes),
                "blocked_nodes": dict(self.blocked_nodes),
                "truncated_at": self.truncated_at, "failure_stage": self.failure_stage,
                "recovery_hint": self.recovery_hint,
                "elapsed_seconds": round(self.elapsed_seconds, 3),
                "policy_source": self.policy_source, "policy_digest": self.policy_digest,
                "review_mode": self.review_mode, "review_status": self.review_status}


def _satisfies(node: Node, state: NodeState | None, *, consumer: Node | None = None) -> bool:
    """一个依赖是否"满足"。**不满足就传播阻断**，不拿旧输出接着跑。"""
    if state is None:
        return False
    from .graph import stage
    if (consumer is not None and consumer.stage == 'render'
            and node.stage in stage('render').requires
            and state.execution_state in (ExecutionState.FAILED, ExecutionState.BLOCKED)):
        # Terminal local failure is data for the reader's missing-object list.
        # It does not authorize generation from the failed content.
        return True
    if state.execution_state in (ExecutionState.SUCCEEDED, ExecutionState.CACHED):
        return True
    # 只有声明允许的阶段才能用"经政策认可的 not_applicable"当合法终态
    return (state.execution_state is ExecutionState.NOT_APPLICABLE
            and node.allow_not_applicable)


class Runner:
    """执行一张图。**同一个 Runner 服务单章/多章/整本**（差异只在图里有多少节点）。"""

    def __init__(self, services: Services, graph: ExecutionGraph, store: RunStore, *,
                 resume: bool = False,
                 initial_completed: set[str] | None = None) -> None:
        if graph.graph_version != GRAPH_VERSION:
            raise ContractError(f"旧图只读；Runner 只执行 graph {GRAPH_VERSION}")
        self.services = services
        self.graph = graph
        self.store = store
        # 修改计划 F1：本进程正在执行的运行目录；复制过的运行据此重新挂载旧的媒体绝对路径。
        from ..core.resource_files import set_current_run_root
        set_current_run_root(Path(store.work).parent)
        self.resume = resume
        recorded = store.load_state()
        self._review_mode = recorded.get('review_mode') or (
            'full' if resume else configured_review_mode(services.workspace))
        if not resume and 'review_mode' not in recorded:
            store.save_state({**recorded, 'review_mode': self._review_mode})
        from uuid import uuid4
        self.services.media_review_session_id = uuid4().hex
        self.initial_completed = set(initial_completed or ())
        #: 派发前的额度预约估算。默认 0（真正的估算在 M6 接账本后给出）
        self.services.store = store
        self.services.control.bind(store.root / "execution.json", run_id=store.run_id,
                                   require_existing=resume)
        gate = getattr(self.services.control, 'network_gate', None)
        if gate is not None and gate.observer is None:
            gate.observer = self._network_event
        #: §4.2：返修的**唯一**入口。阶段只交 `repair_requests`，由它决定排不排批次。
        self._repair = RepairCoordinator(self.services.control.policy,
            max_content_rounds=2 if self._review_mode == 'balanced' else None)
        #: 每个单元的返修账（跨阶段、跨 resume 共用同一份）
        self._repair_states: dict[str, UnitRepairState] = {}
        self._repair_resume_rerun: set[str] = set()
        #: 本进程这一轮要执行的返修批次：unit_id → (batch, 触发它的 directives)
        self._repair_queue: dict[str, tuple[RepairBatch, list]] = {}
        #: `(unit_id, stage)` → 让这个节点重跑的批次。
        #: ⚠️ 这一格是**必需**的：返修改完之后，被失效重排的节点必须知道"我是因为哪一批
        #    改动而在重跑"，`audit` 才知道该核验哪些工单的关闭。第一版只把批次传给作业
        #    **直接**跑的那个阶段，于是重排的 audit 拿不到批次、不做关闭核验、
        #    重复发现同一个问题 —— 单元永远停在 needs_review。
        self._pending_batch: dict[tuple[str, str], RepairBatch] = {}
        #: 本次运行里**真的重新执行过**的节点（不是复用来的）。
        #: 为什么需要它：内容寻址的复用只比"字节是否一致"。当上游产物被人改过、
        #: 而它自己的阶段又把文件**重新生成回同样的字节**时，下游看到的输入与上次
        #: 完全一致，于是会被判为可复用 —— 但"上游刚刚真的重跑过"这件事本身
        #: 就是下游必须重新核验的理由（§9：最终证据不能掩盖变化的输入或产物）。
        #: 实测抓到的就是这一条：改 `products/figures/<单元>.json` 之后 figures 自己
        #: 重跑并写回原内容，audit 随即被判 `cached`（`assert 'cached' != 'cached'`）。
        self._rerun_nodes: set[str] = set()
        #: 失效重排时被 pop 掉的节点，原有 attempt 次数在此暂存（见重排处的说明）
        self._attempt_carry: dict[str, int] = {}
        self._lock = threading.RLock()
        self._states: dict[str, NodeState] = {}
        self._blocked_reason: dict[str, str] = {}
        self._cancelled_nodes: list[str] = []
        #: 请求与计划在开跑时**读一次**：阶段拿到的必须是这一次冻结的那一份，
        #: 不能让每个阶段各自去读文件（那会在并行下读到不同的东西）。
        self._request_obj = None
        self._plan_obj = None
        self._input_snapshot: dict = {}
        self._generations: dict[str, int] = {}
        self._repair_routes: dict[str, dict] = {}

    # ── 输入指纹：变化就该重跑
    @staticmethod
    def _tree_digest(path: Path) -> str:
        """Hash file contents and relative names; missing inputs remain explicit."""
        path = Path(path)
        if path.is_file():
            return digest_file(path)
        if not path.is_dir():
            return "missing"
        return digest_of({str(p.relative_to(path)): digest_file(p)
                          for p in sorted(path.rglob("*")) if p.is_file()
                          and "__pycache__" not in p.parts
                          and p.suffix not in (".pyc", ".pyo")})

    @staticmethod
    def _runtime_tree_digest(root: Path) -> str:
        """Hash shipped code/assets by relative name, excluding generated build/cache files."""
        skipped = {'__pycache__', '.git', '.cache', '.pytest_cache', '.mypy_cache',
                   '.ruff_cache', 'build', 'dist', 'node_modules', '_legacy_tree'}
        suffixes = {'.py', '.pyi', '.json', '.toml', '.cfg', '.ini', '.md', '.txt',
                    '.js', '.mjs', '.cjs', '.css', '.html', '.svg', '.png', '.jpg',
                    '.jpeg', '.webp', '.gif', '.woff', '.woff2', '.ttf', '.otf',
                    '.wasm', '.glsl', '.vert', '.frag'}
        root = Path(root)
        if not root.is_dir():
            return 'missing'
        return digest_of({p.relative_to(root).as_posix(): digest_file(p)
            for p in sorted(root.rglob('*')) if p.is_file() and p.suffix.lower() in suffixes
            and not any(part in skipped or part.endswith(('.egg-info', '.dist-info'))
                        for part in p.relative_to(root).parts)})

    def _runtime_identity(self) -> dict:
        """The workspace contains book data; the active install owns implementations.

        Hash the whole small runtime conservatively: audit consumes lesson validators,
        preparation calls parse helpers, and stages share lib/vendor code. Keeping three
        logical roots also makes equivalent checkout and wheel layouts comparable.
        """
        from importlib.metadata import PackageNotFoundError, version
        from .stage_io import _repo_root
        adapters = _repo_root()
        package = Path(__file__).resolve().parents[1]
        dependencies = {}
        for name in ('numpy', 'sympy', 'matplotlib', 'pillow', 'pymupdf', 'pypdf',
                     'pdfplumber', 'pydantic', 'jsonschema', 'schemdraw'):
            try:
                dependencies[name] = version(name)
            except PackageNotFoundError:
                dependencies[name] = 'unavailable'
        return {'aiforstudy': self._runtime_tree_digest(package),
                'adapters.blocks': self._runtime_tree_digest(adapters / 'blocks'),
                'adapters.lib': self._runtime_tree_digest(adapters / 'lib'),
                'python': {'implementation': sys.implementation.name,
                           'version': list(sys.version_info[:3])},
                'dependencies': dependencies}

    @staticmethod
    def _runtime_package_root() -> Path:
        return Path(__file__).resolve().parents[1]

    def _stage_runtime_identity(self, stage_name: str) -> dict:
        from .runtime_dependencies import COMMON, STAGE_PACKAGE_PATHS, STAGE_ADAPTER_PATHS
        from .stage_io import _repo_root
        global_env = self._runtime_identity()
        package, adapters = self._runtime_package_root(), _repo_root()
        paths = COMMON + STAGE_PACKAGE_PATHS.get(stage_name, ())
        # Declared implementation inputs are mandatory shipped code, unlike
        # optional book artifacts. A missing file must never become a stable
        # "missing" cache key and silently bless results from a broken install.
        for kind, root, names in (('package', package, paths),
                ('adapters', adapters, STAGE_ADAPTER_PATHS.get(stage_name, ()))):
            for name in names:
                if not (root / name).exists():
                    raise ContractError(f'missing declared runtime component for {stage_name}: {kind}/{name}')
        return {
            "package": {p: (self._runtime_tree_digest(package / p) if (package / p).is_dir()
                           else self._tree_digest(package / p)) for p in paths},
            "adapters": {p: self._runtime_tree_digest(adapters / p) if (adapters / p).is_dir()
                            else self._tree_digest(adapters / p)
                         for p in STAGE_ADAPTER_PATHS.get(stage_name, ())},
            "python": global_env["python"], "dependencies": global_env["dependencies"]}

    def _stage_snapshot(self, node: Node, *, candidate_generation=False) -> dict:
        from .runtime_dependencies import STAGE_ROLES
        snapshot = {k:v for k,v in self._input_snapshot.items() if k not in ("runtime", "configuration")}
        from ..core.jsonio import read_json_soft
        def config(name):
            return read_json_soft(self.services.workspace.config / name) or {}
        models = config("models.json")
        roles = STAGE_ROLES.get(node.stage, ())
        snapshot["configuration"] = {
            "models": ({**{k:v for k,v in models.items() if k != "roles"},
                       "roles": {role:(models.get("roles") or {}).get(role) for role in roles}}
                       if roles else {k:v for k,v in models.items() if k != "roles"}),
            "capabilities": (config("capabilities.json")
                             if node.stage in ("media_plan","figures","audit","interaction","render") else {}),
            "repair_policy": (config("execution.json")
                              if node.stage in ("lesson","text_audit","media_plan","figures","audit","interaction","render") else {})}
        snapshot["runtime"] = self._stage_runtime_identity(node.stage)
        if node.stage in ('lesson', 'text_audit', 'media_plan', 'figures', 'audit',
                          'interaction', 'render', 'delivery'):
            snapshot['configuration']['review_mode'] = self._review_mode
        from .runtime_dependencies import INTERACTION_CAPABILITY_CONSUMERS
        if node.stage in INTERACTION_CAPABILITY_CONSUMERS:
            from .interaction_runtime import interaction_runtime_inputs
            capability_ctx = StageContext(node=node, request=self._request_obj, plan=self._plan_obj,
                workspace=self.services.workspace, context=self.services.context, services=self.services)
            snapshot["interaction_runtime"] = interaction_runtime_inputs(capability_ctx)
        if node.stage in ("lesson", "text_audit", "media_plan"):
            from ..blocks.content.content_policy import PROFILE_PATH
            snapshot["learner_profile_resource"] = self._tree_digest(PROFILE_PATH)
        if candidate_generation and node.stage == "lesson":
            # Rule implementation changes trigger a free candidate recheck; the
            # actual author policy and prompt data still invalidate generation.
            from ..blocks.content.content_policy import load_content_policy, prompt_rules
            policy=load_content_policy()
            snapshot["runtime"]["package"].pop("blocks/content",None)
            content_root=self._runtime_package_root()/"blocks/content"
            snapshot["runtime"]["package"]["content_generation_components"] = {
                p.name:self._tree_digest(p) for p in sorted(content_root.glob("*.py"))
                if p.name not in ("content_policy.py","review.py","review_aliases.py",
                                  "review_partial.py","repair.py")}
            from .runtime_dependencies import CONTENT_AUTHOR_ASSETS
            snapshot["runtime"]["package"]["content_author_assets"] = {
                name:self._tree_digest(self._runtime_package_root()/name) for name in CONTENT_AUTHOR_ASSETS}
            snapshot["runtime"]["author_policy"] = prompt_rules(policy,"author")
        if node.stage == "figures" and self.graph.graph_version == GRAPH_VERSION:
            snapshot["media_resources"] = self._media_resource_identities(node)
        return snapshot

    def _media_resource_identities(self, node: Node) -> dict:
        """Resolve renderer identities before the outer node can be cached.

        Reuse the backend's real per-resource identities, including browser/fonts
        and the animation worker's interpreter/native-tool/isolation probe. A
        failed probe changes the input identity; the stage then reports its normal
        structured blocked result instead of inheriting yesterday's approval.
        """
        from ..core.jsonio import read_json_soft
        from . import media_stages, media
        from .services import reraise_control_error
        path = self._store_root() / "products/media_plan" / f"{node.scope_key}.json"
        plan = (read_json_soft(path) or {}).get("payload") or {}
        requests = plan.get("requests") or []
        if not requests:
            return {}
        ctx = StageContext(node=node, request=self._request_obj, plan=self._plan_obj,
                           workspace=self.services.workspace, context=self.services.context,
                           services=self.services)
        identities = {}
        try:
            capabilities = media_stages._capabilities(ctx)
            content = None
            for request in requests:
                request_id = request["request_id"]
                code_backend=(capabilities.get('code_generation')=='GeneratedMediaCodeV1'
                              and request['kind'] in ('drawing','animation'))
                if code_backend or request["kind"] != "animation":
                    identities[request_id] = media_stages.resource_dependencies(ctx, request, capabilities)
                    continue
                if content is None:
                    content, approval = media_stages._load_content(ctx)
                from ..blocks.media.math_context import ApprovedMathContext
                math_context=ApprovedMathContext.from_content_binding(content,approval,request)
                from .stage_io import _unit_definition
                unit = _unit_definition(ctx, str(node.scope_key)) or {}
                source = Path(ctx.context.parse) / str(unit.get("original_text") or "")
                identities[request_id] = {
                    "generation_dependency_hash": media.cache_identity(
                        ctx, request, content, source, self.services.capabilities,math_context=math_context),
                    "review_dependency_hash": media.review_identity(ctx, request)}
        except Exception as exc:
            reraise_control_error(exc)
            identities["unavailable"] = {"type": type(exc).__name__, "reason": str(exc)}
        return identities

    def _dependent_closure(self, roots: set[str]) -> set[str]:
        found, frontier = set(), set(roots)
        while frontier:
            next_frontier = {key for key in frontier for key in self.graph.dependents(key)} - found - roots
            found |= next_frontier
            frontier = next_frontier
        return found

    def _snapshot_inputs(self) -> dict:
        context = self.services.context
        # Preparation replaces context.parse with a derived run-local copy.
        # Imported input identity must stay stable across that transition;
        # stage input_files and upstream output digests track the derived bytes.
        imported = self.services.registry.resolve(context.book_id)
        return {
            "book_id": context.book_id, "book_revision": context.revision,
            "sources": [{"kind": source.kind, "declared_hash": source.sha256,
                         "actual_hash": self._tree_digest(source.path)}
                        for source in context.sources],
            "parse": self._tree_digest(imported.parse),
            "prior_lessons": self._tree_digest(context.lessons),
            "prior_audit": self._tree_digest(context.audit),
            "problems": self._tree_digest(context.problems),
            "configuration": self._tree_digest(self.services.workspace.config),
            "runtime": self._runtime_identity(),
        }

    def _cache_plan_identity(self) -> dict:
        identity = self._plan_obj.identity()
        # Preparation tasks describe remaining work, not generation inputs.
        # Completing a concept shard must not invalidate the stages that made it.
        for name in ('request_digest', 'requires_preparation', 'preparation'):
            identity.pop(name, None)
        return identity

    def inputs_digest(self, node: Node, *, recorded_upstream: bool = False,
                      exclude_deps: tuple = (), candidate_generation=False) -> str:
        impl = self.services.stages.get(node.stage)
        module_name=getattr(impl,"__module__",type(impl).__module__)
        module_path=getattr(sys.modules.get(module_name),"__file__",None)
        implementation={"module":module_name,
                        "name":getattr(impl,"__qualname__",type(impl).__qualname__),
                        "cache_version":getattr(impl,"cache_version",None)}
        upstream = {}
        for dep in node.requires:
            if dep in exclude_deps:
                continue
            state = self._states.get(dep)
            upstream[dep] = None if state is None else {
                "inputs_digest": state.inputs_digest,
                "outputs": [{"kind": raw.get("kind"),
                             "logical_key": raw.get("logical_key"),
                             "schema_version": raw.get("schema_version"),
                             "producer_stage": raw.get("producer_stage"),
                             "content_digest": (raw.get("content_digest") if recorded_upstream
                                                else self._tree_digest(Path(raw["path"])))}
                            for raw in state.outputs],
            }
        plan_identity = self._cache_plan_identity()
        generation_request = self._request_obj.as_dict()
        for name in ("budget", "parallelism", "resume", "idempotency_key"):
            generation_request.pop(name, None)
        return self.store.node_inputs_digest(
            node.node_id, stage_version=STAGE_VERSION,
            inputs={"plan_digest": digest_of(plan_identity),
                    "generation_request": generation_request,
                    "source_snapshot": self._stage_snapshot(node, candidate_generation=candidate_generation),
                    "scope_key": node.scope_key, "upstream": upstream,
                    "implementation": implementation,
                    "implementation_module": (self._tree_digest(Path(module_path))
                                              if module_path else "unavailable")})

    def _store_root(self) -> Path:
        from .work_paths import store_root
        return store_root(getattr(self.services,'workspace',None),self.store)

    def _logic_fingerprint(self, node: Node) -> str:
        """"跟审核逻辑有关"的输入指纹：**不含上游产物**。

        用途见 `NodeState.logic_digest`：证书有效性由它判定，而不是事后重算
        `inputs_digest`（后者连同当时的上游集合一起固化，批次重排后必然对不上）。
        """
        impl = self.services.stages.get(node.stage)
        module_name=getattr(impl,"__module__",type(impl).__module__)
        module_path=getattr(sys.modules.get(module_name),"__file__",None)
        implementation={"module":module_name,
                        "name":getattr(impl,"__qualname__",type(impl).__qualname__),
                        "cache_version":getattr(impl,"cache_version",None)}
        plan_identity = self._cache_plan_identity()
        generation_request = self._request_obj.as_dict()
        for name in ("budget", "parallelism", "resume", "idempotency_key"):
            generation_request.pop(name, None)
        return digest_of({
            "stage_version": STAGE_VERSION,
            "implementation": implementation,
            "implementation_module": (self._tree_digest(Path(module_path))
                                      if module_path else "unavailable"),
            "plan_digest": digest_of(plan_identity),
            "generation_request": generation_request,
            "source_snapshot": self._stage_snapshot(node),
            "scope_key": node.scope_key})

    def _resolve_declared(self, rel: str) -> Path:
        path = Path(rel)
        return path if path.is_absolute() else self._store_root() / path

    def _declared_inputs_unchanged(self, node: Node) -> bool:
        """阶段声明的"实际读过的文件"是否还是当初那一版（重新读盘核对）。"""
        state = self._states.get(node.node_id)
        if state is None:
            return True
        recorded = dict(getattr(state, "input_digests", None) or {})
        if not recorded:
            return True
        for rel, digest in recorded.items():
            if self._tree_digest(self._resolve_declared(rel)) != digest:
                return False
        return True

    def _record_declared_inputs(self, node: Node, result) -> None:
        """记下阶段声明的"实际读过的文件"，以及**证书相关指纹**（供 resume 时校验）。"""
        state = self._states.get(node.node_id)
        if state is None:
            return
        # 证书相关指纹：**每次跑完都记**（与有没有声明输入文件无关）
        state.logic_digest = self._logic_fingerprint(node)
        if node.stage == "lesson":
            state.candidate_generation_digest = (
                self.inputs_digest(node, candidate_generation=True) if state.finished_ok() else "")
            state.policy_identity = self._content_policy_identity() if state.finished_ok() else ""
        declared = tuple(str(x) for x in (getattr(result, "input_files", ()) or ()))
        if not declared:
            self.store.update_node(state)
            return
        state.input_files = declared
        # 摘要也一并记下：比对发生在**复用判断**里（见 `_declared_inputs_unchanged`），
        # 而**不**进主输入指纹 —— 第一次跑的时候阶段还没声明，指纹里加它就等于
        # "每次 resume 都算不一致"，那会把正常的复用全部打掉（实测：6 → 9 failed）。
        state.input_digests = {rel: self._tree_digest(self._resolve_declared(rel))
                               for rel in declared}
        self.store.update_node(state)

    def _content_policy_identity(self) -> str:
        return self._tree_digest(self._runtime_package_root() / "blocks/content/content_policy.py")

    def _policy_recheck_allowed(self, node: Node) -> bool:
        """Only an otherwise valid, proven candidate may survive a policy change."""
        previous = self._states.get(node.node_id)
        if (not self.resume or self.graph.graph_version != GRAPH_VERSION or node.stage != "lesson"
                or previous is None or not previous.candidate_generation_digest
                or not previous.policy_identity or previous.policy_identity == "missing"
                or self._pending_batch.get((node.scope_key, node.stage)) is not None
                or node.scope_key in self._repair_queue
                or any(dep in self._rerun_nodes for dep in node.requires)):
            return False
        repair_state = (self.store.load_state().get("repair_states") or {}).get(node.scope_key) or {}
        if repair_state.get("active_batch_id"):
            return False
        current_policy = self._content_policy_identity()
        if current_policy in ("missing", previous.policy_identity):
            return False
        if (not previous.input_files or set(previous.input_files) != set(previous.input_digests)
                or any(value == "missing" for value in previous.input_digests.values())
                or not self._declared_inputs_unchanged(node)):
            return False
        required = {"lesson.content", "lesson.content_head", "lesson.unit"}
        if not required <= {row.get("kind") for row in previous.outputs}:
            return False
        return (self.store.reusable(previous, inputs_digest=previous.inputs_digest)
                and previous.candidate_generation_digest == self.inputs_digest(node, candidate_generation=True))

    def _reader_cache_valid(self, node: Node) -> bool:
        if reviews_frozen() or not self.resume or node.stage not in ('render', 'delivery'):
            return True
        from .delivery_stage import validate_reader_delivery
        ctx = StageContext(node=node, request=self._request_obj, plan=self._plan_obj,
            workspace=self.services.workspace, context=self.services.context, services=self.services)
        try:
            validate_reader_delivery(ctx, chapter_key=node.scope_key if node.stage == 'render' else None)
            return True
        except (ContractError, ValueError, KeyError, OSError):
            return False

    def _finalized_resume_evidence(self, node: Node) -> dict | None:
        """Reuse an audited repair without rewriting its initial stage history.

        Audit may replace the figures ledger it consumed. Only its verified final SHA
        can certify that one replacement; all other outputs still require their original
        SHA. Recompute the original input identities against today's source/config/code
        before accepting that evidence, and require already-reused direct dependencies.
        """
        if self.graph.graph_version == GRAPH_VERSION:
            return None
        if not self.resume or node.stage not in ('figures', 'audit'):
            return None
        if any(self._states.get(dep) is None or
               self._states[dep].execution_state is not ExecutionState.CACHED
               for dep in node.requires):
            return None
        unit = node.scope_key
        figure_id, audit_id = f'figures:{unit}', f'audit:{unit}'
        proof = self._final_audit_evidence(include_needs_review=True).get(figure_id)
        if proof is None or proof.get('evidence_node') != audit_id:
            return None
        nodes = self.graph.by_id()
        ids = (f'lesson:{unit}', figure_id, audit_id)
        try:
            for key in ids:
                state = self._states[key]
                if not state.finished_ok() or not state.outputs or key not in nodes:
                    return None
                if key == audit_id:
                    # audit 这一条看 `logic_digest`（实现 + 请求 + 计划 + 原文快照，
                    # **不含上游**），而不是事后重算 `inputs_digest`。
                    # 理由：`inputs_digest` 是"派发那一刻"连同当时上游一起固化的，
                    # 批次失效重排会让上游在之后变化 —— 事后**没有任何一种重算**能等于它，
                    # 证书就永远失效（实测：audit 每次都重跑）。
                    # `logic_digest` 在阶段跑完时记下、现在按同一口径重算比对，于是
                    # "实现/配置/原文变了要重跑"仍成立，而"图被换过"由声明摘要管。
                    if (not state.logic_digest
                            or state.logic_digest != self._logic_fingerprint(nodes[key])):
                        return None
                    continue
                # ⚠️ lesson/figures 这两条要**自洽**：登记输入指纹必须与
                #    "按**登记的上游摘要**重算"一致（`recorded_upstream=True`）。
                #    用登记值是有意的：证书的职责之一就是允许"图产物被替换并由 audit
                #    认证"，那会改变磁盘上的图内容，所以这里**不能**看磁盘；
                #    而①正常的产物替换不会打破自洽，②有人**篡改登记身份**
                #    （`change='recorded_inputs'` 把 `inputs_digest` 改成全 0）会被抓住 ——
                #    这正是那条测试要守的"最终证据不能掩盖被改过的账"。
                if state.inputs_digest != self.inputs_digest(nodes[key],
                                                             recorded_upstream=True):
                    return None
            audit = self._states[audit_id]
            evidence = next(raw for raw in audit.outputs
                            if raw.get('content_digest') == proof['evidence_digest'])
            payload = _json.loads(Path(evidence['path']).read_text(encoding='utf-8'))['payload']
            # 证书只对**它声明的那一版图**有效：把声明的摘要与盘上今天的图对上
            figure_state = self._states[figure_id]
            if not figure_state.outputs:
                return None
            if str(payload.get('final_figures_digest') or '') != self._tree_digest(
                    Path(figure_state.outputs[0]['path'])):
                return None
            final_figure_path = Path(payload['final_figures_path']).resolve()
            replacements = 0
            for key in ids:
                state = self._states[key]
                for raw in state.outputs:
                    ref = ArtifactRef.from_dict(raw)
                    if (not ref.content_digest or ref.book_id != self.services.context.book_id or
                            ref.producer_stage != state.stage):
                        return None
                    if (key == figure_id and ref.kind == 'figures.ledger' and
                            ref.path.resolve() == final_figure_path):
                        ref.replace(content_digest=proof['final_figures_digest']).verify()
                        replacements += 1
                    else:
                        ref.verify()
            return proof if replacements == 1 else None
        except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration, ContractError):
            return None

    # ── 主循环
    def run(self) -> RunResult:
        from ..blocks.visual.worker import configure_worker_concurrency
        configure_worker_concurrency(self.services.control.policy.render['max_concurrent_workers'])
        with review_mode(self._review_mode):
            return self._run_bound()

    def _run_bound(self) -> RunResult:
        started = self.services.wall_clock()
        nodes = self.graph.by_id()
        self.services.assert_profile_allows_real_services()
        self._request_obj = self._load_request()
        self._plan_obj = self._load_plan()
        self._input_snapshot = self._snapshot_inputs()
        from ..core.jsonio import write_json_atomic
        write_json_atomic(self.store.root / "graph.json", self.graph.as_dict())
        self.store.set_status("running")
        self.store.emit(KIND_RUN_STARTED, message=f"{self.graph.goal}/{self.graph.profile}",
                        data={"nodes": len(nodes), "truncated_at": self.graph.truncated_at})

        # 已有状态（恢复时）
        self._states = self.store.node_states() if self.resume else {}
        invalid_completed = self.initial_completed - self._states.keys()
        if invalid_completed:
            raise ContractError(f"已完成前缀缺运行记录：{sorted(invalid_completed)}")

        pending = {nid: node for nid, node in nodes.items()
                   if nid not in self.initial_completed}
        if self.resume:
            self._recover_interrupted_repairs(nodes)
        running: dict = {}
        in_flight: set[str] = set()
        unit_order = {node.scope_key: index for index, node in enumerate(
            node for node in self.graph.nodes if node.stage == 'lesson')}

        def dispatch_order(item):
            _, node = item
            # Review an available draft before starting more drafts. Short
            # downstream work can make an early chapter readable while later
            # chapters are still being written; figures are not a book barrier.
            priority = (0 if node.stage == 'text_audit' else
                        1 if node.stage in {'media_plan', 'audit', 'interaction',
                                            'render', 'delivery'} else 2)
            return (priority, unit_order.get(node.scope_key, -1), node.node_id)

        def ready(node: Node) -> bool:
            return all(dep not in pending for dep in node.requires)

        def dependency_unsatisfied(node: Node) -> str | None:
            for dep in node.requires:
                if dep not in nodes:
                    continue
                state = self._states.get(dep)
                if not _satisfies(nodes[dep], state, consumer=node):
                    why = (state.error or {}).get("reason") if state and state.error else "未完成"
                    return f"上游 {dep} 不满足（{why}）"
                gate = dispatch_quality_gate(node.stage, nodes[dep].stage)
                allowed_na = bool(state and state.execution_state == ExecutionState.NOT_APPLICABLE
                                  and nodes[dep].allow_not_applicable)
                if gate and not allowed_na and (state.quality_state.value if state else "") != gate:
                    # **边级**质量门（§4.1）：只有显式声明了门的那条边才拦。
                    # 为什么不是"上游全都必须 accepted"：教案 needs_review 时**应该**
                    # 能进 text_audit（进得去才查得出问题），但图不能在未过正文门的
                    # 教案上开画。声明式的门让这两种情况互不牵连。
                    return (f"上游 {dep} 的质量态是 "
                            f"{state.quality_state.value if state else '未完成'}，"
                            f"而 {node.stage} 要求这条边达到 {gate}（边级质量门）")
            return None

        pool = ThreadPoolExecutor(max_workers=self.services.control.policy.parallelism)
        try:
            while pending or running:
                from .concept_repairs import drain_body_repairs
                drain_body_repairs(self, pending, in_flight)
                # 1) 取消 → 不再派发
                if self.services.cancel.cancelled:
                    for nid, node in list(pending.items()):
                        if nid in in_flight:
                            continue
                        self._mark(nid, node, ExecutionState.CANCELLED,
                                   error={"reason": self.services.cancel.reason})
                        self._cancelled_nodes.append(nid)
                        self.store.emit(KIND_STAGE_SKIPPED, stage=node.stage, node_id=nid,
                                        scope_key=node.scope_key, message="因取消未开始")
                        pending.pop(nid)

                # 1b) 网络（修改计划 N）：断网期间不派发；连续断网超上限 → 按取消路径安全停止
                gate = getattr(self.services.control, 'network_gate', None)
                if gate is not None and gate.paused and not self.services.cancel.cancelled:
                    self.services.cancel.cancel(gate._pause_message())
                network_down = gate is not None and gate.outage and bool(running)
                self._publish_run_status(pending, in_flight, network_down)

                # 2) 派发就绪节点
                dispatched = False
                for nid, node in ([] if network_down else sorted(list(pending.items()), key=dispatch_order)):
                    if nid in in_flight or not ready(node):
                        continue
                    why = dependency_unsatisfied(node)
                    if why:
                        # 依赖没满足 → **不拿旧输出接着跑**（§9）
                        self._mark(nid, node, ExecutionState.BLOCKED, error={"reason": why})
                        self._blocked_reason[nid] = why
                        self.store.emit(KIND_STAGE_BLOCKED, stage=node.stage, node_id=nid,
                                        scope_key=node.scope_key, message=why)
                        pending.pop(nid)
                        dispatched = True
                        continue
                    # 恢复：输入指纹一致 + 输出仍对得上 → 复用，不重跑
                    digest = self.inputs_digest(node)
                    previous = self._states.get(nid)
                    upstream_reran = any(dep in self._rerun_nodes for dep in node.requires)
                    # 声明过的输入文件被改过 → 不许复用（它在输出清单之外，别的检查看不见）
                    declared_ok = self._declared_inputs_unchanged(node)
                    # 用户 2026-10-20 定：上次审核因传输失败没做完（REVIEW_INCOMPLETE）→ 续跑时补审，不复用。
                    review_incomplete = previous is not None and any(
                        isinstance(f, dict) and f.get('code') == 'REVIEW_INCOMPLETE' for f in (previous.findings or []))
                    reusable_ctx = (not upstream_reran and declared_ok and not review_incomplete
                                    and nid not in self._repair_resume_rerun
                                    and self._reader_cache_valid(node))
                    finalized = (self._finalized_resume_evidence(node)
                                 if (previous is not None and reusable_ctx) else None)
                    from .media_review_recovery import pending_media_transport_resume
                    if (self.resume and previous is not None and reusable_ctx
                            and not pending_media_transport_resume(self, node)
                            and (finalized or self.store.reusable(previous, inputs_digest=digest))):
                        self._mark(nid, node, ExecutionState.CACHED,
                                   inputs_digest=previous.inputs_digest if finalized else digest,
                                   outputs=list(previous.outputs),
                                   quality_state=previous.quality_state,
                                   attempt=previous.attempt, findings=list(previous.findings),
                                   error=previous.error, usage_ref=previous.usage_ref,
                                   started_at=previous.started_at)
                        self.store.emit(KIND_STAGE_CACHED, stage=node.stage, node_id=nid,
                                        scope_key=node.scope_key,
                                        message="最终审核证据仍有效，复用返修结果" if finalized else "输入未变，复用",
                                        data={"finalization_evidence": finalized} if finalized else {})
                        pending.pop(nid)
                        dispatched = True
                        continue
                    if len(running) >= self.services.control.policy.parallelism:
                        break
                    policy = self.services.control.policy
                    parallelism = policy.parallelism
                    text_stages = {'lesson', 'text_audit'}
                    # Without an explicit text limit, keep the old one-worker reserve.
                    text_reserve = (min(policy.content_parallelism, parallelism - 1)
                                    if policy.content_parallelism else 1)
                    if (node.stage in text_stages and policy.content_parallelism
                            and sum(n.stage in text_stages for _, n, _ in running.values())
                                    >= policy.content_parallelism):
                        # Text depends on earlier reviewed units; keep its pace bounded.
                        continue
                    if (node.stage == 'figures' and parallelism > 1
                            and any(n.stage in text_stages for n in pending.values())
                            and sum(n.stage == 'figures' for _, n, _ in running.values())
                                    >= parallelism - text_reserve):
                        # A media stage may contain several long model/render
                        # attempts. Leave workers for pending text (including
                        # repairs), then release them when all text is finished.
                        continue
                    future = pool.submit(copy_context().run, self._execute, node, digest,
                                         self._generations.get(nid, 0))
                    running[future] = (nid, node, self._generations.get(nid, 0))
                    in_flight.add(nid)
                    dispatched = True
                if not running:
                    if not dispatched:
                        break
                    continue

                # 3) 收集结果
                done, _ = wait(list(running), timeout=0.2, return_when=FIRST_COMPLETED)
                for future in done:
                    nid, node, dispatched_generation = running.pop(future)
                    in_flight.remove(nid)
                    # ⚠️ 必须**每轮重置**：`future.result()` 抛异常时下面会走 except 分支，
                    #    但 `result` 会留着**上一轮**的值。第一版没重置，于是"上一个节点的
                    #    返修请求"会被当成"这个节点的"，而且 `for` 第一轮就会 UnboundLocalError。
                    result = None
                    try:
                        result, state = future.result()
                        if state is None or dispatched_generation != self._generations.get(nid, 0):
                            # Invalidated while in flight: retry the new generation.
                            continue
                    except Cancelled as exc:
                        self._mark(nid, node, ExecutionState.CANCELLED,
                                   error={"reason": str(exc)})
                        self._cancelled_nodes.append(nid)
                    except AifsError as exc:
                        self._mark(nid, node, ExecutionState.FAILED,
                                   error={"reason": str(exc), "type": type(exc).__name__})
                        self.store.emit(KIND_STAGE_FAILED, stage=node.stage, node_id=nid,
                                        scope_key=node.scope_key, message=str(exc))
                    except Exception as exc:                      # noqa: BLE001
                        self._mark(nid, node, ExecutionState.FAILED,
                                   error={"reason": f"{type(exc).__name__}: {exc}"})
                        self.store.emit(KIND_STAGE_FAILED, stage=node.stage, node_id=nid,
                                        scope_key=node.scope_key,
                                        message=f"{type(exc).__name__}: {exc}")
                    pending.pop(nid, None)
                    # 只有**真的执行过**的节点才进这个集合（复用来的不算）：
                    # 它的下游必须重新核验，理由见 `_rerun_nodes` 的说明。
                    self._rerun_nodes.add(nid)
                    # §4.2.2：阶段只交请求；协调器在这里决定排批次还是停下。
                    if isinstance(result, StageResult):
                        try:
                            self._handle_repair_requests(node, result, pending)
                        except Cancelled as exc:
                            self.services.cancel.cancel(str(exc))
                # A final completed review can confirm a shared correction even
                # when there were no other pending nodes before this result.
                drain_body_repairs(self, pending, in_flight)
        finally:
            pool.shutdown(wait=True)

        return self._finish(started)

    def _network_event(self, event, fields):
        """Record outage/probe/recovery notes; observers never alter the run."""
        labels = {'outage_started': '网络中断：暂停派发，开始探测', 'probe_failed': '网络探测仍失败',
                  'recovered': '网络已恢复：原请求重发，继续派发', 'paused': '连续断网超上限：整轮安全暂停'}
        self.store.emit(KIND_NOTE, message=labels.get(event, event), network_event=event, **fields)

    # ── 单节点执行
    def _execute(self, node: Node, digest: str, generation: int | None = None):
        try:
            self.services.cancel.checkpoint()
            self.services.control.check()
        except (BudgetExceeded, Cancelled):
            raise
        self.store.emit(KIND_STAGE_STARTED, stage=node.stage, node_id=node.node_id,
                        scope_key=node.scope_key)
        started = self.services.wall_clock()
        recheck_only = self._policy_recheck_allowed(node)
        ctx = StageContext(node=node, request=self._request_obj, plan=self._plan_obj,
                           workspace=self.services.workspace, context=self.services.context,
                           services=self.services,
                           repair_batch=self._pending_batch.get((node.scope_key, node.stage)),
                           recheck_only=recheck_only,
                           recheck_reason="content policy changed; generation inputs and candidate verified" if recheck_only else "")
        impl = self.services.stages.get(node.stage)
        try:
            # Concepts is a serial book-wide batch. Its actual API calls carry
            # unit scopes; the enclosing batch is bounded by the run deadline.
            # Treating 'book' as one unit would cap the entire index at 20min.
            scope = (ExecutionScope(self.store.run_id) if node.stage in ('concepts', 'source_format')
                     and node.scope_key == 'book' else
                     ExecutionScope(self.store.run_id, node.scope_key, node.stage))
            with self.services.control.work_scope(scope):
                result = impl(ctx)
        except BaseException:
            # Gateway accounts real calls itself. A local unreserved failure did not spend.
            raise
        if not isinstance(result, StageResult):
            raise ContractError(
                f"阶段 {node.stage!r} 的实现没有返回 StageResult（返回了 "
                f"{type(result).__name__}）—— 阶段结果必须结构化，不能只回一个布尔值")
        result.started_at = result.started_at or started
        result.finish()

        with self._lock:
            if generation is not None and generation != self._generations.get(node.node_id, 0):
                self.store.emit("stage.stale_discarded", stage=node.stage, node_id=node.node_id,
                                scope_key=node.scope_key, message="输入已被返修失效，拒绝提交旧在途结果")
                return None, None
            state = NodeState(node_id=node.node_id, stage=node.stage, scope_key=node.scope_key,
                              execution_state=result.execution_state,
                              quality_state=result.quality_state,
                              attempt=self._attempt_carry.pop(
                                  node.node_id,
                                  int(self._states.get(node.node_id).attempt
                                      if self._states.get(node.node_id) else 0)) + 1,
                              inputs_digest=digest,
                              outputs=[o.as_dict() for o in result.outputs],
                              error=result.error, findings=list(result.findings),
                              usage_ref=result.usage_ref or "",
                              started_at=result.started_at, finished_at=result.finished_at)
            self._mark(node.node_id, node, state.execution_state, quality_state=state.quality_state,
                       inputs_digest=digest, outputs=state.outputs, error=state.error,
                       findings=state.findings, attempt=state.attempt,
                       usage_ref=state.usage_ref, started_at=state.started_at)
            # 这一格是"一次性"的：节点跑完就消费掉，避免它影响同一阶段以后的正常运行。
            self._pending_batch.pop((node.scope_key, node.stage), None)
            # 记下阶段声明的"实际读过的文件"，resume 时靠它重新读盘校验
            self._record_declared_inputs(node, result)
        kind = {ExecutionState.SUCCEEDED: KIND_STAGE_FINISHED,
                ExecutionState.CACHED: KIND_STAGE_CACHED,
                ExecutionState.BLOCKED: KIND_STAGE_BLOCKED,
                ExecutionState.FAILED: KIND_STAGE_FAILED,
                ExecutionState.NOT_APPLICABLE: KIND_STAGE_FINISHED,
                ExecutionState.CANCELLED: KIND_STAGE_SKIPPED}[result.execution_state]
        self.store.emit(kind, stage=node.stage, node_id=node.node_id,
                        scope_key=node.scope_key,
                        message=(result.error or {}).get("reason", "")
                        or result.execution_state.value,
                        data={"quality_state": result.quality_state.value,
                              "duration": result.duration})
        return result, state

    def _load_request(self):
        from ..contracts.request import RunRequest
        return RunRequest.from_dict(_json.loads(self.store.request_path.read_text(encoding="utf-8")))


    # ── 统一返修：协调器是唯一入口（§4.2）
    def _repair_state(self, unit_id: str) -> UnitRepairState:
        """取（或建）单元的返修账。**resume 时从 state.json 恢复**，不重置计数。"""
        if unit_id not in self._repair_states:
            raw = (self.store.load_state().get("repair_states") or {}).get(unit_id)
            self._repair_states[unit_id] = (UnitRepairState.from_dict(raw) if raw
                                            else UnitRepairState(unit_id=unit_id))
        return self._repair_states[unit_id]

    def _persist_repair_states(self) -> None:
        payload = dict(self.store.load_state().get('repair_states') or {})
        payload.update({uid: st.as_dict() for uid, st in self._repair_states.items()})
        self.store.set_status(self.store.load_state().get("status") or "running",
                              repair_states=payload)

    def _recover_interrupted_repairs(self, nodes):
        """Close an interrupted reservation without refunding object attempts.

        Re-run its reviewer against the actual current artifact. Its valid cached
        verdict can be reused; the coordinator then spends only remaining rounds.
        Candidate generation is not re-run merely because its repair was interrupted.
        """
        for unit, raw in (self.store.load_state().get('repair_states') or {}).items():
            if not raw.get('active_batch_id'):
                continue
            state = self._repair_state(unit)
            deferred = bool(state.active_batch_context.get('deferred'))
            origin = state.active_batch_context.get('origin_node_id')
            if origin not in nodes:
                origin = 'text_audit:' + unit
            if origin not in nodes:
                # 修改计划 B6：本次续跑范围不含该单元的审核节点。延期标记原样保留给包含它的运行；
                # 已排的中断批次照旧结束，但写明没有重核、剩余轮次未用的原因，不归为额度耗尽。
                if deferred:
                    continue
                reason = '本次续跑范围不含该单元的审核节点；未重核，剩余返修轮次未使用（不是额度耗尽）'
                state.history.append({'batch_id': state.active_batch_id, 'state': 'interrupted_closed',
                                      'rerun': False, 'not_rerun_reason': reason})
                state.active_batch_id = None
                state.active_batch_context = {}
                self.store.emit('repair.batch.interrupted_closed', scope_key=unit,
                                message='中断批次已结束：' + reason, not_rerun_reason=reason)
                continue
            state.history.append({'batch_id': state.active_batch_id,
                                  'state': 'deferred_resumed' if deferred else 'interrupted_closed',
                                  'reason': ('取消时延期的返修：重核当前对象后按原规则排批次（未消耗轮次）' if deferred else
                                             '恢复时保留已消费次数，重核当前对象后仅安排剩余轮次')})
            state.active_batch_id = None
            state.active_batch_context = {}
            self._repair_resume_rerun |= {origin} | self._dependent_closure({origin})
            self.store.emit('repair.batch.interrupted_closed', scope_key=unit,
                            message=('取消时延期的返修：重新核实当前产物后再排' if deferred else
                                     '中断批次已结束；已消费对象次数保留，重新核实当前产物'))
        self._persist_repair_states()

    def _publish_run_status(self, pending, in_flight, network_down):
        """修改计划 E3/E5（并入 G1）：运行状态写各通道实际在途数与等待原因（约每 2 秒一次）。"""
        now = time.monotonic()
        if now - getattr(self, '_last_run_status', 0.0) < 2.0:
            return
        self._last_run_status = now
        status_fn = getattr(self.services.control, 'channel_status', None)
        if not callable(status_fn):
            return
        waiting_dependencies = sum(1 for nid in pending if nid not in in_flight)
        snapshot = {**status_fn(), 'nodes': {'running': len(in_flight),
                    'waiting_for_dependencies': waiting_dependencies},
                    'network_wait': bool(network_down), 'at': time.time()}
        try:
            self.store.set_status(self.store.load_state().get('status') or 'running', run_status=snapshot)
        except Exception:                                    # noqa: BLE001
            pass  # Status telemetry never stops a run.

    def _body_acceptance(self, nodes, failed, blocked, incomplete):
        """修改计划（用户 2026-10-20）：只看正文——lesson 全部完成、各页浏览器检查没报正文显示问题。"""
        from ..core.jsonio import read_json_soft
        lessons = [n for n in self.graph.nodes if n.stage == 'lesson']
        renders = [n for n in self.graph.nodes if n.stage == 'render']
        if not lessons or not renders:
            return False, []
        if any(not (self._states.get(n.node_id) and self._states[n.node_id].finished_ok()) for n in lessons + renders):
            return False, []
        notes = []
        for node in renders:
            receipt = read_json_soft(self.store.work / 'products' / 'render' / f'{node.scope_key}.json') or {}
            report = (receipt.get('payload') or {}).get('browser_report') or {}
            if report.get('status') == 'failed':
                return False, []                               # 页面正文显示有问题
            if report.get('status') == 'blocked':
                notes.append(f'{node.scope_key} 未能做浏览器核对')
        others = [nid for nid in (*failed, *blocked, *incomplete) if nodes[nid].stage not in ('lesson', 'render')]
        if others:
            notes.append(f'{len(others)} 个非正文阶段未完成（如 {others[:3]}）')
        needs = [s.node_id for s in self._states.values()
                 if s.finished_ok() and s.quality_state is not QualityState.ACCEPTED]
        if needs:
            notes.append(f'{len(needs)} 个阶段待复核或未核实')
        return True, notes

    def _unit_node_ids(self, unit_id: str) -> dict[str, str]:
        """该单元"阶段名 → node_id"。返修批次要按阶段找节点，不按字符串猜。

        修改计划 E2/E6：按阶段粒度精确匹配。单元级阶段只认本单元自己的节点——
        章 key 同时是章引言单元的 id，旧写法会在本单元不在范围内时退到章引言的
        节点，误用其他节的额度；只有章级阶段（如 render）才匹配章 key。
        """
        from .graph import GRAIN_CHAPTER, GRAIN_UNIT, STAGE_BY_NAME
        chapter = unit_id.split("-", 1)[0]
        out = {}
        for nid, node in self.graph.by_id().items():
            spec = STAGE_BY_NAME.get(node.stage)
            grain = spec.grain if spec is not None else None
            wanted = ((unit_id,) if grain == GRAIN_UNIT else (chapter,) if grain == GRAIN_CHAPTER
                      else ("scope", unit_id, chapter) if grain is None else ("scope",))
            if node.scope_key in wanted:
                out[node.stage] = nid
        return out

    def _owner_stage(self, owner: str, unit_id: str = "", artifact_id: str = "") -> str:
        """责任角色 → 生产阶段。§5.1 的角色集合是冻结的，映射也固定。

        ⚠️ `reviewer` 的正位是 `text_audit`（补齐依据与逐条标准）。但该阶段
        **尚未插入生产阶段表**（见 `graph.py` 的说明），所以这里在单元节点表里
        找不到它时回退到 `audit` —— 回退是**显式**的，而且只在目标阶段真的不存在时发生，
        不是"随便找个阶段顶上"。
        """
        if owner == "reviewer" and artifact_id.startswith(("visual:", "svg:")):
            return "figures"
        stage = {"lesson": "lesson", "visual_planner": "figures",
                 "visual_renderer": "figures", "interaction": "interaction",
                 "reviewer": "text_audit"}.get(owner, "")
        if stage and unit_id:
            known = self._unit_node_ids(unit_id)
            if stage not in known and stage == "text_audit" and "audit" in known:
                return "audit"
        return stage

    def _job_stage(self, job, unit_id: str = "") -> str:
        if self.graph.graph_version != GRAPH_VERSION:
            return self._owner_stage(job.owner, unit_id, job.artifact_id)
        routes = [self._repair_routes.get(ticket_id) for ticket_id in job.ticket_ids]
        if any(route is None for route in routes):
            raise ContractError("当前图返修必须为每个工单提供 RepairRouteV1")
        from .repair import RepairRouteV1
        stages = {RepairRouteV1.from_dict(route).target_stage for route in routes}
        if len(stages) != 1:
            raise ContractError("同一返修作业包含不同目标阶段，必须拆分 directive")
        return stages.pop()

    def _register_repair_routes(self, node, result):
        from .repair import RepairRouteV1
        routes = {route["ticket_id"]: RepairRouteV1.from_dict(route) for route in result.repair_routes}
        if len(routes) != len(result.repair_routes):
            raise ContractError("RepairRouteV1 不得重复声明同一工单")
        wanted = {ticket for directive in result.repair_requests for ticket in directive.ticket_ids}
        if set(routes) != wanted:
            raise ContractError("RepairRouteV1 必须与本次 repair_requests 工单集合完全一致")
        owner_targets = {"lesson": {"lesson"}, "visual_planner": {"media_plan", "figures"},
                         "visual_renderer": {"figures", "render"}, "interaction": {"interaction"},
                         "reviewer": {"text_audit", "media_plan", "figures", "audit", "interaction", "render"}}
        for directive in result.repair_requests:
            stages = set()
            for ticket in directive.ticket_ids:
                route = routes[ticket]
                if route.origin_stage != node.stage:
                    raise ContractError("RepairRouteV1 origin_stage 必须是提交工单的实际阶段")
                if route.target_artifact_id not in directive.artifact_hashes:
                    raise ContractError("RepairRouteV1 target_artifact_id 与 directive 绑定不符")
                if route.target_stage not in owner_targets.get(directive.owner, set()):
                    raise ContractError("RepairRouteV1 目标阶段与 owner 权限不符")
                if directive.owner == "reviewer" and route.target_artifact_kind not in {"evidence", "media_evidence"}:
                    raise ContractError("reviewer 只负责证据补充，不能修改业务内容")
                stages.add(route.target_stage)
                self._repair_routes[ticket] = route.as_dict()
            if len(stages) != 1:
                raise ContractError("一个 directive 不得路由到多个目标阶段")

    def _handle_repair_requests(self, node: Node, result: StageResult, pending: dict) -> None:
        """阶段交了返修请求 → 让协调器决定修不修，并**同步**执行它排出的批次。

        为什么同步、就地：批次必须串行（同单元不许并发改），而且下游在批次收尾前
        不许消费旧产物 —— 把它塞进线程池就同时破坏这两条。§4.2.4 要的正是这个。
        """
        directives = list(getattr(result, "repair_requests", ()) or ())
        if self.graph.graph_version == GRAPH_VERSION:
            self._register_repair_routes(node, result)
        if not directives:
            return
        units = {directive.unit_id for directive in directives}
        if len(units) != 1:
            raise ContractError("一次返修请求必须属于同一单元")
        unit_id = units.pop()
        state = self._repair_state(unit_id)
        # Compare the candidate that actually received this judgment. Diagnostics,
        # generation journals and host revision counters are not body progress.
        from ..core.jsonio import read_json_soft
        for directive in directives:
            for artifact, claimed_hash in directive.artifact_hashes.items():
                current_hash = claimed_hash
                if artifact == 'lesson:'+unit_id:
                    head = read_json_soft(self.store.work/'products/content'/unit_id/'current.json') or {}
                    ref = head.get('content_ref') or {}
                    candidate = read_json_soft(Path(ref['path'])) if ref.get('path') else None
                    if isinstance(candidate,dict):
                        candidate.pop('revision',None)
                        if self._review_mode == 'balanced':
                            # The same lesson repair may now add an animation
                            # without changing correct prose. Count that actual
                            # request change as progress, not a new budget.
                            media_ref = head.get('author_media_ref') or {}
                            media = read_json_soft(Path(media_ref['path'])) if media_ref.get('path') else {}
                            current_hash = digest_of({'content':candidate,
                                'author_media':(media or {}).get('requirements')}, check=False)
                        else:
                            current_hash = digest_of(candidate,check=False)
                    else:
                        # Synthetic/legacy stages have no content head. A newly
                        # rejected response still supplies its own changed hash.
                        if not any(t.startswith('ticket-content-rejected-') for t in directive.ticket_ids):
                            target_id = self._unit_node_ids(unit_id).get('lesson')
                            hashes = self._node_output_hashes(target_id) if target_id else []
                            current_hash = hashes[-1] if hashes else state.artifact_heads.get(artifact,claimed_hash)
                state.artifact_heads[artifact] = current_hash
        candidate_before = tuple(sorted((str(k),str(v)) for k,v in state.artifact_heads.items()))
        if self.services.cancel.cancelled and not state.active_batch_id:
            # 修改计划 B6：运行正在取消时不排返修批次、不消耗轮次（CH09-S02-SS06 曾在取消瞬间
            # 排出批次、扣掉一轮却什么也没修）。留一个延期标记，续跑时重核该单元再排。
            state.active_batch_id = 'deferred-on-cancel:' + node.node_id
            state.active_batch_context = {'origin_node_id': node.node_id, 'deferred': True}
            self._persist_repair_states()
            self.store.emit("repair.deferred", stage=node.stage, node_id=node.node_id, scope_key=unit_id,
                            message="运行正在取消：返修未排、未消耗轮次；续跑时重新核实后再排")
            return
        from .media_review_recovery import resume_media_review_batch
        recovered = resume_media_review_batch(self, node, directives, state)
        decision = recovered or self._repair.plan(directives, state)
        if isinstance(decision, StopDecision):
            state.stopped_reason = decision.reason
            self._persist_repair_states()
            self.store.emit("repair.stopped",
                            stage=node.stage, node_id=node.node_id, scope_key=unit_id,
                            message=f"停止自动返修：{decision.reason}",
                            **decision.as_dict())
            return

        batch = decision
        state.active_batch_id = batch.batch_id
        state.active_batch_context = {'origin_node_id': node.node_id, 'batch': batch.as_dict(),
            'directives': [d.as_dict() for d in directives], 'routes': list(result.repair_routes)}
        self.store.emit("repair.batch.planned", stage=node.stage, node_id=node.node_id,
                        scope_key=unit_id,
                        message=(f"排返修批次 {batch.batch_id[:12]}（第 {batch.round} 批"
                                 f"{'，消耗内容轮次' if batch.consumes_content_round else '，证据修复'}）"),
                        **batch.as_dict())

        stages_by_name = self._unit_node_ids(unit_id)
        invalidated: set[str] = set()
        followups = []
        # Reserve a content batch once, before its first job. Failed jobs and
        # crashes must not give back a consumed attempt on resume.
        if batch.consumes_content_round:
            state.content_rounds_used += 1
            for artifact in {job.artifact_id for job in batch.jobs if job.kind != 'evidence_format'}:
                state.content_rounds_by_artifact[artifact] = state.content_rounds_by_artifact.get(artifact, 0)+1
        for job in batch.jobs:
            if job.kind == "evidence_format" and recovered is None:
                for ticket_id in job.ticket_ids:
                    state.evidence_attempts_by_verdict[ticket_id] = state.evidence_attempts_by_verdict.get(ticket_id, 0) + 1
        self._persist_repair_states()
        stage_outcomes = {}
        dispatched_feedback = deepcopy(state.patch_feedback_by_artifact)
        for job in batch.jobs:
            stage_name = self._job_stage(job, unit_id)
            target_id = stages_by_name.get(stage_name)
            if target_id is None:
                self.store.emit("repair.job.skipped", scope_key=unit_id,
                                message=f"没有 {stage_name!r} 节点可承接 {job.owner} 的作业",
                                job_id=job.job_id, owner=job.owner,
                                artifact_id=job.artifact_id, job_kind=job.kind,
                                ticket_ids=list(job.ticket_ids))
                continue
            target = self.graph.by_id()[target_id]
            if stage_name not in stage_outcomes:
                before = self._node_output_hashes(target_id)
                stage_batch = replace(batch, jobs=tuple(j for j in batch.jobs
                    if self._job_stage(j, unit_id) == stage_name))
                repair_inputs_digest = self.inputs_digest(target)
                try:
                    outcome = self._run_repair_job(target, job, stage_batch)
                except (BudgetExceeded, Cancelled):
                    if stage_name == "figures" and all(j.kind == "evidence_format" for j in stage_batch.jobs):
                        state.history.append({"batch_id": batch.batch_id, "state": "transport_interrupted"})
                        state.active_batch_id = None
                        self._persist_repair_states()
                    raise
                stage_outcomes[stage_name] = (before, outcome)
                if outcome.get("ok"):
                    previous = self._states.get(target_id)
                    repaired_result = outcome["result"]
                    self._mark(target.node_id, target, ExecutionState.SUCCEEDED,
                               quality_state=QualityState(str(outcome.get("quality") or "not_checked")),
                               inputs_digest=repair_inputs_digest,
                               attempt=self._attempt_carry.pop(target_id, previous.attempt if previous else 0) + 1,
                               outputs=outcome.get("outputs") or [],
                               error=repaired_result.error, findings=repaired_result.findings,
                               usage_ref=repaired_result.usage_ref or "",
                               started_at=repaired_result.started_at)
                    self._record_declared_inputs(target, repaired_result)
                    self._rerun_nodes.add(target_id)
                if outcome.get("result") and outcome["result"].repair_requests:
                    followups.append((target, outcome["result"]))
            before, outcome = stage_outcomes[stage_name]
            # R2-4：正文答复被拒时，宿主把**合并反馈**（位置 + 怎么算过）挂在 finding 上，
            # 这里持久化给下一轮重写用。（旧的 CONTENT_PATCH_REJECTED 随补丁协议删除。）
            result_feedback = [row.get('feedback') for row in getattr(outcome.get('result'),'findings',())
                               if row.get('code') in ('CONTENT_REJECTED','CONTENT_PATCH_REJECTED')]
            if not outcome.get('ok') and target.stage=='lesson':
                for feedback in result_feedback:
                    if (isinstance(feedback,dict) and feedback.get('artifact_id')==job.artifact_id
                            and set(feedback.get('ticket_refs',[]))==set(job.ticket_ids)):
                        state.patch_feedback_by_artifact[job.artifact_id]=deepcopy(feedback)
                        # Persist received rejection evidence before reporting job
                        # completion; an interrupted batch keeps its charged round.
                        self._persist_repair_states()
            elif outcome.get('ok'):
                state.patch_feedback_by_artifact.pop(job.artifact_id,None)
            after = list(outcome.get("digests") or [])
            applied = bool(outcome.get("ok")) and bool(after) and before != after
            effect = self._repair.accept_result(
                job, PatchOutcome(applied=applied,
                                  reason="" if applied else str(outcome.get("reason") or "")),
                revision_hash=(sorted(after)[-1] if after else ""))
            invalidated |= set(effect["invalidated"])
            self.store.emit("repair.job.finished", stage=target.stage,
                            node_id=target.node_id, scope_key=unit_id,
                            message=(f"{job.owner} 的作业 {'已改' if applied else '未改'}"
                                     f"{'' if applied else '（' + str(outcome.get('reason') or '内容未变化') + '）'}"
                                     f"：{job.job_id}"),
                            effect=effect, content_rounds_used=state.content_rounds_used,
                            job_reason=str(outcome.get("reason") or ""),
                            job_id=job.job_id, owner=job.owner,
                            artifact_id=job.artifact_id, job_kind=job.kind,
                            ticket_ids=list(job.ticket_ids))

        state.active_batch_id = None
        state.active_batch_context = {}
        # 记下作业之后的 artifact 头用于下一批的版本绑定；
        # R3-3：早停要"正文与判词都没变" —— 正文摘要一起记下来。
        for job in batch.jobs:
            head_node = stages_by_name.get(self._job_stage(job, unit_id))
            heads = self._node_output_hashes(head_node) if head_node else []
            if heads:
                state.artifact_heads[job.artifact_id] = heads[-1]
        state.last_candidate_signature = self._repair.signature(directives,
                                                               state.artifact_heads,
                                                               dispatched_feedback)
        state.last_candidate_content = candidate_before
        self._persist_repair_states()

        # 失效的下游重新入调度：**不拿旧产物接着跑**（§9）。
        # ⚠️ 刚承接作业的那个阶段要排除掉：它这次运行本身**就是**新版本，
        #    再排一次等于同一份内容白跑两遍（还会多花一次模型调用）。
        repaired = {stages_by_name[self._job_stage(job, unit_id)] for job in batch.jobs
                    if self._job_stage(job, unit_id) in stages_by_name}
        if self.graph.graph_version == GRAPH_VERSION:
            invalidated_nodes = self._dependent_closure(repaired) - repaired
        else:
            invalidated_nodes = {stages_by_name[name] for name in invalidated
                                 if name in stages_by_name} - repaired
        for nid in sorted(invalidated_nodes):
            stage_name = self.graph.by_id()[nid].stage
            with self._lock:
                self._generations[nid] = self._generations.get(nid, 0) + 1
                # ⚠️ pop 会把"跑过几次"一起丢掉：重排后 attempt 从 1 重新数，
                #    于是"重跑过的节点 attempt 应当 >= 2"这类断言看到的是 1。
                #    次数是审计信息，不能因为重新排队就归零 —— 先把它带过去。
                _prev = self._states.get(nid)
                if _prev is not None:
                    self._attempt_carry[nid] = int(_prev.attempt or 0)
                self._states.pop(nid, None)
            # 重跑它的原因要带上：核验方（audit）靠它知道该核哪些工单
            batch_key = (self.graph.by_id()[nid].scope_key, stage_name)
            if self.graph.graph_version == GRAPH_VERSION:
                # Invalidating a consumer is not authorization to execute every
                # upstream repair there. Only the content reviewer needs content
                # jobs to verify closure; ordinary media consumers rebuild normally.
                relevant_jobs = tuple(job for job in batch.jobs
                    if self._job_stage(job, unit_id) == stage_name
                    or stage_name == "text_audit" and self._job_stage(job, unit_id) == "lesson")
                if relevant_jobs:
                    self._pending_batch[batch_key] = replace(batch, jobs=relevant_jobs)
                else:
                    self._pending_batch.pop(batch_key, None)
            else:
                self._pending_batch[batch_key] = batch
            pending[nid] = self.graph.by_id()[nid]
            self.store.emit("repair.node.invalidated", stage=stage_name, node_id=nid,
                            scope_key=unit_id,
                            message=f"{batch.batch_id[:12]} 改动后失效，重新排入调度")

        # A reviewer may turn an incomplete diagnosis into a ready content
        # ticket. Its new directive must re-enter the same bounded coordinator.
        for followup_node, followup_result in followups:
            self._handle_repair_requests(followup_node, followup_result, pending)

    def _node_output_hashes(self, node_id: str) -> list[str]:
        state = self._states.get(node_id)
        if state is None:
            return []
        return sorted(str(o.get("content_digest") or o.get("hash") or "")
                      for o in (state.outputs or []))

    def _run_repair_job(self, target: Node, job, batch: RepairBatch) -> dict:
        """执行一个具名作业：**用现有阶段执行器**再跑一次目标阶段，带上批次。

        这就是 §4.2.4 要的"由现有 executor 执行具名 RepairJob"，
        而不是在工作线程里偷偷调用整条 pipeline。
        """
        try:
            self.services.cancel.checkpoint()
            self.services.control.check()
        except (BudgetExceeded, Cancelled):
            raise
        ctx = StageContext(node=target, request=self._request_obj, plan=self._plan_obj,
                           workspace=self.services.workspace, context=self.services.context,
                           services=self.services, repair_batch=batch,
                           repair_feedback=deepcopy(self._repair_state(job.unit_id).patch_feedback_by_artifact.get(job.artifact_id)))
        impl = self.services.stages.get(target.stage)
        try:
            with self.services.control.work_scope(ExecutionScope(self.store.run_id, target.scope_key, target.stage)):
                outcome = impl(ctx)
        except (BudgetExceeded, Cancelled):
            raise
        except Exception as exc:                       # noqa: BLE001
            return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        if not isinstance(outcome, StageResult) or not outcome.feeds_downstream:
            return {"ok": False,
                    "result":outcome if isinstance(outcome,StageResult) else None,
                    "reason": (outcome.error or {}).get("reason", "返修阶段未成功返回")
                    if isinstance(outcome, StageResult) else "返修阶段返回值不是 StageResult"}
        return {"ok": True, "reason": "", "state": outcome.execution_state.value,
                "quality": outcome.quality_state.value, "job": job.job_id, "result": outcome,
                "outputs": [o.as_dict() for o in outcome.outputs],
                "digests": sorted(str(o.content_digest or "") for o in outcome.outputs)}

    def _load_plan(self):
        from ..contracts.scope import ScopePlan
        return ScopePlan.from_dict(_json.loads(self.store.plan_path.read_text(encoding="utf-8")))

    def _mark(self, node_id: str, node: Node, state: ExecutionState, *,
              quality_state: QualityState | None = None, inputs_digest: str = "",
              outputs=None, error=None, findings=None, attempt: int = 0,
              usage_ref: str = "", started_at: float | None = None) -> None:
        with self._lock:
            previous = self._states.get(node_id)
            record = NodeState(
                node_id=node_id, stage=node.stage, scope_key=node.scope_key,
                execution_state=state,
                quality_state=(quality_state if quality_state is not None
                               else (previous.quality_state if previous
                                     else QualityState.NOT_CHECKED)),
                attempt=attempt or (previous.attempt if previous else 1),
                inputs_digest=inputs_digest or (previous.inputs_digest if previous else ""),
                # 声明过的输入文件必须跟着状态走：丢了它们，"读过但不在输出清单里"
                # 的那些文件就又变回盲区（这正是本机制要治的病）。
                input_files=(previous.input_files if previous else ()),
                input_digests=(dict(previous.input_digests) if previous else {}),
                logic_digest=(previous.logic_digest if previous else ""),
                candidate_generation_digest=(previous.candidate_generation_digest if previous else ""),
                policy_identity=(previous.policy_identity if previous else ""),
                outputs=list(outputs or []),
                error=error, findings=list(findings or []),
                usage_ref=usage_ref, started_at=started_at,
                finished_at=self.services.wall_clock())
            self._states[node_id] = record
            self.store.update_node(record)

    def _final_quality_overrides(self) -> dict[str, dict]:
        """Only accepted evidence may supersede a node's original quality."""
        if reviews_frozen():
            return {}
        return self._content_receipt_overrides() if self.graph.graph_version == GRAPH_VERSION else self._final_audit_evidence()

    def _content_receipt_overrides(self) -> dict[str, dict]:
        """Only a verified content receipt can accept its current lesson candidate."""
        from .content_stages import load_content
        overrides = {}
        for nid,node in self.graph.by_id().items():
            if node.stage != "lesson":
                continue
            original=self._states.get(nid)
            review_id=f"text_audit:{node.scope_key}"
            reviewed=self._states.get(review_id)
            if (not original or not original.finished_ok() or not reviewed or not reviewed.finished_ok()
                    or reviewed.quality_state != QualityState.ACCEPTED):
                continue
            try:
                ctx=StageContext(node=node,request=self._request_obj,plan=self._plan_obj,
                    workspace=self.services.workspace,context=self.services.context,services=self.services)
                _,approval=load_content(ctx,approved=True)
                for raw in (*original.outputs,*reviewed.outputs):
                    ArtifactRef.from_dict(raw).verify()
                lesson_refs=[raw for raw in original.outputs if raw.get("kind")=="lesson.unit"]
                if len(lesson_refs)!=1:
                    continue
                payload=_json.loads(Path(lesson_refs[0]["path"]).read_text())["payload"]
                if payload.get("content_hash")!=approval["content_hash"]:
                    continue
                overrides[nid]={"quality_state":"accepted", "initial_quality_state":original.quality_state.value,
                    "evidence_node":review_id, "content_hash":approval["content_hash"],
                    "policy_digest":approval["policy_digest"]}
            except (ContractError, OSError, KeyError, ValueError, TypeError):
                continue
        return overrides

    def _final_audit_evidence(self, *, include_needs_review: bool = False,
                              trace: list | None = None) -> dict[str, dict]:
        """Verify final files independently of whether their reviewed quality passed.

        A completed negative review is reusable evidence, never an approval. The
        broader mode is used only by resume; quality promotion keeps accepted only.

        ⚠️ `trace`（可诊断性，2026-10-07 补）：这个函数原来有**六处静默 `continue`
        和一个吃掉全部异常的 `except`**，于是"证据没构造出来"这件事在外部表现为
        `{}` —— 与"条件本来就不成立"完全无法区分。实测踩过：逐项打印判据全部满足，
        函数仍返回 `{}`，只能靠猜。**静默的 continue 比错误更难查。**
        传入一个 list 就会把"每个候选节点为什么被跳过"记进去（默认 None = 零开销）。
        """
        def _why(reason: str) -> None:
            if trace is not None:
                trace.append(reason)

        overrides = {}
        allowed_quality = {QualityState.ACCEPTED}
        if include_needs_review:
            allowed_quality.add(QualityState.NEEDS_REVIEW)
        for audit_id, state in self._states.items():
            if (state.stage != "audit" or not state.finished_ok()
                    or state.quality_state not in allowed_quality):
                _why(f"{audit_id}: 不是已完成的 audit（stage={state.stage} "
                     f"state={state.execution_state.value} quality={state.quality_state.value}）")
                continue
            unit = state.scope_key
            targets = {f"lesson:{unit}", f"figures:{unit}"}
            for raw in state.outputs:
                try:
                    ref = ArtifactRef.from_dict(raw).verify()
                    if ref.producer_stage != "audit" or ref.book_id != self.services.context.book_id:
                        _why(f"{audit_id}: 产物不是本 run 的 audit 产物"
                             f"（producer={ref.producer_stage} book={ref.book_id}）")
                        continue
                    payload = _json.loads(ref.path.read_text(encoding="utf-8"))["payload"]
                    if (payload.get("unit_id") != unit
                            or payload.get("final_quality") != state.quality_state.value
                            or set(payload.get("supersedes_nodes") or ()) != targets):
                        _why(f"{audit_id}: 载荷身份不符（unit={payload.get('unit_id')!r} "
                             f"final_quality={payload.get('final_quality')!r} "
                             f"vs state={state.quality_state.value!r} "
                             f"supersedes={sorted(payload.get('supersedes_nodes') or ())} "
                             f"vs {sorted(targets)}）")
                        continue
                    paths = {}
                    for kind in ("lesson", "figures"):
                        path = Path(payload[f"final_{kind}_path"]).resolve()
                        if not path.is_relative_to(self.store.work.resolve()):
                            raise ValueError("Final audit evidence is outside the run")
                        if digest_file(path) != payload[f"final_{kind}_digest"]:
                            raise ValueError("Final audit evidence content changed")
                        paths[kind] = path
                    checks = payload["final_checks"]
                    lesson, figures, audit = checks["lesson"], checks["figures"], checks["audit"]
                    if (not isinstance(lesson.get("problems"), list)
                            or audit.get("quality_state") not in ("accepted", "needs_review")
                            or type(audit.get("blocking_open")) is not int
                            or audit["blocking_open"] < 0
                            or not isinstance(audit.get("review_errors"), list)):
                        _why(f"{audit_id}: final_checks 结构不合法（lesson.problems="
                             f"{type(lesson.get('problems')).__name__} "
                             f"audit.quality_state={audit.get('quality_state')!r} "
                             f"blocking_open={audit.get('blocking_open')!r} "
                             f"review_errors={type(audit.get('review_errors')).__name__}）")
                        continue
                    ledger = _json.loads(paths["figures"].read_text(encoding="utf-8"))["payload"]
                    if (ledger.get("source_lesson_sha256") != payload["final_lesson_digest"]
                            or not isinstance(ledger.get("requests"), list)):
                        _why(f"{audit_id}: 图账本与最终教案对不上（ledger.source="
                             f"{str(ledger.get('source_lesson_sha256'))[:20]} "
                             f"vs final_lesson_digest={str(payload['final_lesson_digest'])[:20]} "
                             f"requests={type(ledger.get('requests')).__name__}）")
                        continue
                    complete = all(request.get("state") in ("fulfilled", "not_applicable")
                                   for request in ledger["requests"])
                    if (figures.get("source_lesson_sha256") != payload["final_lesson_digest"]
                            or figures.get("requests_complete") is not complete
                            or figures.get("quality_state") != ("accepted" if complete else "needs_review")):
                        _why(f"{audit_id}: final_checks.figures 与图账本不一致"
                             f"（source_ok="
                             f"{figures.get('source_lesson_sha256') == payload['final_lesson_digest']} "
                             f"requests_complete={figures.get('requests_complete')!r} "
                             f"重算={complete} quality={figures.get('quality_state')!r} "
                             f"应为={'accepted' if complete else 'needs_review'!r}）")
                        continue
                    accepted = (not lesson["problems"] and not lesson.get("error") and complete
                                and audit["quality_state"] == "accepted"
                                and audit["blocking_open"] == 0 and not audit["review_errors"])
                    if (payload["final_quality"] == "accepted") != accepted:
                        _why(f"{audit_id}: final_quality 与重算结论不一致"
                             f"（final_quality={payload['final_quality']!r} 重算 accepted={accepted}："
                             f"lesson.problems={lesson.get('problems')!r} "
                             f"lesson.error={lesson.get('error')!r} complete={complete} "
                             f"audit.quality_state={audit.get('quality_state')!r} "
                             f"blocking_open={audit.get('blocking_open')!r} "
                             f"review_errors={audit.get('review_errors')!r}）")
                        continue
                    for request in ledger["requests"]:
                        for resource in request.get("resources") or []:
                            path = Path(resource["path"]).resolve()
                            if (not path.is_relative_to(self.store.work.resolve())
                                    or digest_file(path) != resource["sha256"]):
                                raise ValueError("Figure resource content changed")
                    for target in targets:
                        original = self._states.get(target)
                        if original is not None and original.finished_ok():
                            overrides[target] = {"quality_state": payload["final_quality"],
                                "initial_quality_state": original.quality_state.value,
                                "evidence_node": audit_id,
                                "evidence_digest": ref.content_digest,
                                "final_lesson_digest": payload["final_lesson_digest"],
                                "final_figures_digest": payload["final_figures_digest"]}
                except (OSError, ValueError, KeyError, TypeError, AttributeError, ContractError):
                    # Missing/stale evidence never promotes a node's original quality.
                    continue
        return overrides

    # ── 收尾
    def _policy_identity(self) -> dict:
        """这次运行用的策略来源与摘要（进 `RunResult` → `final.json`，供验收与复现）。"""
        policy = getattr(self.services.control, "policy", None)
        if policy is None:
            return {"policy_source": "", "policy_digest": ""}
        try:
            digest = str(policy.digest())
        except Exception:                                     # noqa: BLE001
            digest = ""
        return {"policy_source": str(getattr(policy, "policy_source", "") or ""),
                "policy_digest": digest}

    def _finish(self, started: float) -> RunResult:
        overrides = self._final_quality_overrides()
        counts: dict[str, int] = {}
        quality: dict[str, int] = {}
        failed: list[str] = []
        for node_id, state in self._states.items():
            counts[state.execution_state.value] = counts.get(state.execution_state.value, 0) + 1
            effective = overrides.get(node_id, {}).get("quality_state", state.quality_state.value)
            quality[effective] = quality.get(effective, 0) + 1
            if state.execution_state is ExecutionState.FAILED:
                failed.append(node_id)

        blocked = {nid: (state.error or {}).get("reason", "阶段阻断")
                   for nid, state in self._states.items()
                   if state.execution_state is ExecutionState.BLOCKED}
        cancelled = bool(self._cancelled_nodes) or self.services.cancel.cancelled

        # 质量门：required 节点必须 succeeded/cached 且 accepted
        nodes = self.graph.by_id()
        not_accepted = [nid for nid, node in nodes.items()
                        if node.required and nid in self._states
                        and overrides.get(nid, {}).get("quality_state",
                            self._states[nid].quality_state.value) != QualityState.ACCEPTED.value
                        and self._states[nid].execution_state
                        is not ExecutionState.NOT_APPLICABLE]
        incomplete = [nid for nid in nodes if nid not in self._states]
        balanced = substantive_reviews_enabled()
        if balanced:
            # Frozen generation/receipt checks do not decide substantive quality.
            # Missing actual text, media or page review remains unresolved.
            not_accepted = [nid for nid in not_accepted
                if nodes[nid].stage in ('text_audit', 'audit', 'render')
                or self._states[nid].quality_state in (QualityState.NEEDS_REVIEW, QualityState.REJECTED)]

        # Distinguish quality-gate propagation from an execution failure. Every
        # blocked descendant must trace to a completed but unaccepted upstream.
        quality_blocked = set()
        for node in self.graph.nodes:
            if node.node_id not in blocked:
                continue
            state = self._states[node.node_id]
            if str((state.error or {}).get("reason", "")).startswith("上游 ") and any(
                dep in quality_blocked or (
                    dep in self._states and
                    self._states[dep].execution_state in (ExecutionState.SUCCEEDED, ExecutionState.CACHED) and
                    self._states[dep].quality_state != QualityState.ACCEPTED and
                    dispatch_quality_gate(node.stage, nodes[dep].stage))
                for dep in node.requires):
                quality_blocked.add(node.node_id)

        preview_available = any(
            s.finished_ok() and any(o.get('kind') == 'render.html' and Path(o.get('path') or '').is_file()
                                    for o in s.outputs)
            for s in self._states.values() if s.stage == 'render')
        partial = preview_available and bool(failed or blocked or incomplete)
        # 用户 2026-10-20 定：balanced 下按正文判定验收——全部正文已生成、页面正文能正常显示即算通过；
        # 媒体缺失、审核未核实等技术问题只在提示与运行记录里列出，不影响退出码。
        body_ok, body_notes = (self._body_acceptance(nodes, failed, blocked, incomplete)
                               if balanced and not cancelled else (False, []))
        if cancelled:
            status, exit_code = "cancelled", EXIT_CANCELLED
        elif body_ok:
            status, exit_code = "succeeded", EXIT_OK
        elif partial:
            status, exit_code = 'partial', EXIT_QUALITY
        elif failed or (set(blocked) - quality_blocked) or incomplete:
            status, exit_code = "failed", EXIT_USAGE
        elif (not_accepted or quality_blocked) and (balanced or not reviews_frozen()):
            status, exit_code = "needs_review", EXIT_QUALITY
        else:
            status, exit_code = "succeeded", EXIT_OK

        policy = self.services.control.policy
        scope_accepted = ((not reviews_frozen() and status == "succeeded" and not failed and not blocked and not incomplete)
                          or bool(body_ok))
        release_ready = bool(scope_accepted and not body_ok and self.graph.goal == "deliver"
                             and self.graph.profile == "release"
                             and policy.release_ready_possible
                             and self.graph.truncated_at is None)

        failure_stage = None
        for nid in (failed + list(blocked)):
            failure_stage = nodes[nid].stage if nid in nodes else failure_stage
            if failure_stage:
                break

        hint = ""
        gate = getattr(self.services.control, 'network_gate', None)
        network_paused = bool(gate is not None and gate.paused)
        if network_paused:
            hint = (f"网络中断暂停：连续断网超过 {gate.pause_after / 3600:g} 小时，已安全停止；"
                    f"网络恢复后用 `aifs resume --run {self.store.run_id}` 继续，所有额度不变。")
        elif cancelled:
            hint = f"运行已取消。用 `aifs resume --run {self.store.run_id}` 从已完成处继续。"
        elif body_ok:
            hint = ('正文已全部生成，页面正文显示正常（按正文判定通过）。'
                    + ('其余记录在案的技术问题：' + '；'.join(body_notes) if body_notes else ''))
        elif partial:
            hint = '已生成可用部分的 HTML；缺失及自动处理未决对象保留在运行记录中，本次自动运行已结束。'
        elif failed:
            hint = f"有 {len(failed)} 个阶段失败：{failed[:3]}。修好输入或权限后 resume。"
        elif blocked:
            hint = f"有 {len(blocked)} 个节点因上游不满足被阻断（**没有**拿旧输出接着跑）。"
        elif incomplete:
            hint = f"有 {len(incomplete)} 个节点未开始：{incomplete[:3]}。"
        elif balanced:
            hint = ('本轮实质审核仍有未解决或未核实问题；保留已有产物和问题记录。'
                    if not_accepted else '本轮实质审核已完成；被冻结的旧检查未执行，未授予正式发布认证。')
        elif reviews_frozen():
            hint = "执行已完成；审核已冻结，仅检查 JSON 语法，未授予正式发布认证。"
        elif not_accepted or quality_blocked:
            hint = f"有 {len(not_accepted)} 个 required 阶段未 accepted，不能发布。"

        result = RunResult(
            run_id=self.store.run_id, book_id=self.services.context.book_id,
            goal=self.graph.goal, profile=self.graph.profile, status=status,
            exit_code=exit_code, scope_accepted=scope_accepted,
            release_ready=release_ready, execution_counts=counts, quality_counts=quality,
            quality_overrides=overrides,
            preview_available=preview_available, partial=partial,
            failed_nodes=failed, blocked_nodes=blocked, truncated_at=self.graph.truncated_at,
            failure_stage=failure_stage, recovery_hint=hint,
            review_mode=self._review_mode,
            review_status=(('needs_review' if not_accepted or failed or blocked or incomplete else
                            'passed' if any(n.stage in ('text_audit','audit','render')
                                and nid in self._states for nid,n in nodes.items()) else 'not_run')
                           if balanced else 'not_run' if reviews_frozen() else 'full'),
            elapsed_seconds=self.services.wall_clock() - started,
            **self._policy_identity())
        self.store.set_status(status, exit_code=exit_code, release_ready=release_ready,
                              scope_accepted=scope_accepted, failure_stage=failure_stage,
                              quality_overrides=overrides,
                              preview_available=preview_available, partial=partial,
                              stop_reason=(NETWORK_PAUSE_REASON if network_paused
                                           else self.services.control.stop_reason),
                              execution=self.services.control.snapshot())
        self.store.emit(KIND_RUN_FINISHED, message=status,
                        data={"exit_code": exit_code, "release_ready": release_ready,
                              "execution_counts": counts, "quality_counts": quality})
        return result



__all__ = ["STAGE_VERSION", "RunResult", "Runner"]
