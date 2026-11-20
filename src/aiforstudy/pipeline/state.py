"""运行状态与恢复：**原子落盘 + 按指纹续跑**。

方案 §10 的目标布局：

```text
data/books/<book_id>/runs/<run_id>/
  request.json  plan.json  state.json  events.jsonl  usage.jsonl
  work/         临时文件
  preview/      未发布预览
```

方案 §11 关于恢复的两条硬要求：

> 每阶段只有一套复用判定：stage version、schema、实际输入哈希、上下文、
> 提示词/规则/模板、模型参数与资源版本构成 digest；**文件存在不是缓存有效证据**。
> 读取前验证产物哈希和来源，缓存损坏会失效。

> 恢复保留原 request 和冻结上下文；**输入变化则先重规划并展示变化**，
> 不能把原检查结论迁到新内容。

所以 `state.json` 里每个节点记的不只是"跑完了"，而是：执行状态 + 质量状态 +
输入指纹 + 输出引用（带内容摘要）。`reusable()` 三样都要对得上才允许复用。
只比"文件在不在"的实现，正是本项目反复吃亏的那个形态。
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..contracts.artifact import ArtifactRef
from ..contracts.enums import ExecutionState, QualityState
from ..contracts.request import RunRequest
from ..contracts.scope import ScopePlan
from ..core.digest import digest_of
from ..core.jsonio import read_json, write_json_atomic
from ..errors import ContractError
from .events import EventLog
from .graph import GRAPH_VERSION

STATE_SCHEMA = "1.0"


def new_run_id(prefix: str = "run", *, at: float | None = None) -> str:
    """`run-20000101-000000-0beb84`：时间可读 + 短随机后缀防同一秒撞号。"""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(at if at is not None else time.time()))
    return f"{prefix}-{stamp}-{secrets.token_hex(3)}"


@dataclass
class NodeState:
    node_id: str
    stage: str
    scope_key: str
    execution_state: ExecutionState = ExecutionState.BLOCKED
    quality_state: QualityState = QualityState.NOT_CHECKED
    attempt: int = 0
    inputs_digest: str = ""
    outputs: list[dict] = field(default_factory=list)
    error: dict | None = None
    findings: list[dict] = field(default_factory=list)
    usage_ref: str = ""
    started_at: float | None = None
    finished_at: float | None = None
    #: 阶段声明的"实际读过的文件"（见 `StageResult.input_files` 的说明）。
    #: 必须持久化：resume 时要靠它把那些文件**重新读盘**算进输入指纹。
    input_files: tuple = ()
    #: 那些文件的摘要（登记时算好；复用判断时重新读盘核对）
    input_digests: dict = field(default_factory=dict)
    #: **证书相关**指纹：实现源码 + 运行请求 + 计划 + 原文快照（**不含上游产物**）。
    #: 它在阶段跑完时记下，复用判断时**重新算一遍**比对。为什么不直接用
    #: `inputs_digest`：那个值是"派发那一刻"连同当时的上游集合一起算的，
    #: 批次失效重排会让上游在之后变化，于是**事后没有任何一种重算能等于登记值** ——
    #: 证书就会永远失效（实测踩了整整一轮）。把"跟审核逻辑有关的输入"单独记一份，
    #: 就与调度顺序无关了。
    logic_digest: str = ""
    #: 完整来源仍由 inputs_digest 记录；此额外证明只用于政策改变后的宿主重检。
    candidate_generation_digest: str = ""
    policy_identity: str = ""

    def as_dict(self) -> dict:
        return {"node_id": self.node_id, "stage": self.stage, "scope_key": self.scope_key,
                "execution_state": self.execution_state.value,
                "quality_state": self.quality_state.value, "attempt": self.attempt,
                "inputs_digest": self.inputs_digest, "outputs": list(self.outputs),
                "error": self.error, "findings": list(self.findings),
                "usage_ref": self.usage_ref, "started_at": self.started_at,
                "finished_at": self.finished_at,
                "input_files": list(self.input_files),
                "input_digests": dict(self.input_digests),
                "logic_digest": self.logic_digest,
                "candidate_generation_digest": self.candidate_generation_digest,
                "policy_identity": self.policy_identity}

    @classmethod
    def from_dict(cls, payload: dict) -> "NodeState":
        return cls(node_id=str(payload.get("node_id") or ""),
                   stage=str(payload.get("stage") or ""),
                   scope_key=str(payload.get("scope_key") or ""),
                   execution_state=ExecutionState(
                       str(payload.get("execution_state") or ExecutionState.BLOCKED.value)),
                   quality_state=QualityState(
                       str(payload.get("quality_state") or QualityState.NOT_CHECKED.value)),
                   attempt=int(payload.get("attempt") or 0),
                   inputs_digest=str(payload.get("inputs_digest") or ""),
                   outputs=list(payload.get("outputs") or []),
                   error=payload.get("error"),
                   findings=list(payload.get("findings") or []),
                   usage_ref=str(payload.get("usage_ref") or ""),
                input_files=tuple(str(x) for x in (payload.get("input_files") or ())),
                input_digests=dict(payload.get("input_digests") or {}),
                logic_digest=str(payload.get("logic_digest") or ""),
                candidate_generation_digest=str(payload.get("candidate_generation_digest") or ""),
                policy_identity=str(payload.get("policy_identity") or ""),
                   started_at=payload.get("started_at"),
                   finished_at=payload.get("finished_at"))

    def finished_ok(self) -> bool:
        return self.execution_state in (ExecutionState.SUCCEEDED, ExecutionState.CACHED)


class RunStore:
    """一次运行的目录与状态。**所有写都是原子的**（半写的 JSON 会让下次运行读到坏缓存）。"""

    def __init__(self, root: Path, run_id: str) -> None:
        self.root = Path(root)
        self.run_id = run_id
        self._lock = threading.Lock()
        self.events = EventLog(self.root / "events.jsonl", run_id=run_id)

    # ── 路径
    @property
    def request_path(self) -> Path:
        return self.root / "request.json"

    @property
    def plan_path(self) -> Path:
        return self.root / "plan.json"

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    @property
    def usage_path(self) -> Path:
        return self.root / "usage.jsonl"

    @property
    def work(self) -> Path:
        return self.root / "work"

    @property
    def preview(self) -> Path:
        return self.root / "preview"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    # ── 创建 / 载入
    @classmethod
    def create(cls, runs_root: Path, request: RunRequest, plan: ScopePlan, *,
               control, created_at: float | None = None) -> "RunStore":
        run_id = new_run_id(at=created_at)
        store = cls(Path(runs_root) / run_id, run_id)
        store.root.mkdir(parents=True, exist_ok=True)
        write_json_atomic(store.request_path, request.as_dict())
        write_json_atomic(store.plan_path, plan.as_dict())
        # A published queued run must already be resumable. Only this creation
        # boundary may initialize a zero-attempt journal; resume stays fail-closed.
        control.bind(store.root / "execution.json", run_id=run_id)
        store.save_state({"schema_version": STATE_SCHEMA, "run_id": run_id,
                          "book_id": request.book_id, "goal": request.goal.value,
                          # §6.3：图版本在**创建期**就落盘；缺它 = legacy（旧代码建的 run）。
                          # 放在 create 而不是第一次执行，是因为"已建但还没跑"的 run
                          # （prepare 的两段执行、HTTP 排队）也必须能原地续跑。
                          "graph_version": GRAPH_VERSION,
                          "profile": request.profile.value,
                          "request_digest": request.digest(),
                          "plan_digest": plan.digest(),
                          "created_at": created_at if created_at is not None else time.time(),
                          # ⚠️ **从一开始就有 status**。第一版 create 时不写 status，
                          #    于是"刚建好、runner 还没跑到 set_status('running')"的这段窗口里，
                          #    状态文件里**没有 status 字段**。界面读到它只能显示"-"，
                          #    而按 `status != running` 判断"跑完了"的观察者会**误判为终态**。
                          #    一个状态文件在任何时刻都该有个状态。
                          "status": "queued",
                          "nodes": {}})
        if plan.resolved_scope is not None:
            from .graph import build_graph
            write_json_atomic(store.root / "graph.json", build_graph(plan).as_dict())
        return store

    @classmethod
    def open(cls, runs_root: Path, run_id: str) -> "RunStore":
        root = Path(runs_root) / run_id
        if not (root / "state.json").is_file():
            raise ContractError(f"没有这次运行的记录：{root / 'state.json'}")
        store = cls(root, run_id)
        store.events.open_for_append()
        return store

    # ── 状态
    def load_state(self) -> dict:
        return read_json(self.state_path)

    def save_state(self, payload: dict) -> dict:
        with self._lock:
            payload = {**payload, "updated_at": time.time()}
            write_json_atomic(self.state_path, payload)
            return payload

    def node_states(self) -> dict[str, NodeState]:
        raw = (self.load_state().get("nodes") or {})
        return {k: NodeState.from_dict(v) for k, v in raw.items()}

    def update_node(self, state: NodeState) -> None:
        with self._lock:
            payload = self.load_state()
            nodes = dict(payload.get("nodes") or {})
            nodes[state.node_id] = state.as_dict()
            payload["nodes"] = nodes
            payload["updated_at"] = time.time()
            write_json_atomic(self.state_path, payload)

    def set_status(self, status: str, **extra) -> None:
        with self._lock:
            payload = self.load_state()
            payload["status"] = status
            payload.update(extra)
            payload["updated_at"] = time.time()
            write_json_atomic(self.state_path, payload)

    # ── 订阅 / 事件
    def emit(self, kind: str, **kw):
        return self.events.emit(kind, **kw)

    def events_after(self, after: int = 0, limit: int | None = None):
        return self.events.read(after=after, limit=limit)

    # ── 指纹
    def node_inputs_digest(self, node_id: str, *, stage_version: str = "1",
                           inputs: dict | None = None) -> str:
        """一个节点的输入指纹。**不含时间、run_id、绝对工作目录**（§11）。"""
        return digest_of({"node_id": node_id, "stage_version": stage_version,
                          "inputs": inputs or {}})

    def reusable(self, node: NodeState, *, inputs_digest: str) -> bool:
        """能不能复用。

        三个条件**同时**成立才复用：上次跑成功（succeeded/cached）、输入指纹一致、
        且每个输出在盘上仍然对得上自己的内容摘要。方案 §11：
        "读取前验证产物哈希和来源，**缓存损坏会失效**"。
        """
        if not node.finished_ok() or not node.inputs_digest:
            return False
        if node.inputs_digest != inputs_digest:
            return False
        for raw in node.outputs:
            try:
                ref = ArtifactRef.from_dict(raw).verify(require_digest=True)
                if ref.producer_stage != node.stage:
                    return False
                if ref.book_id != self.load_state().get("book_id"):
                    return False
            except ContractError:
                return False
        return True


def book_runs_root(workspace_root: Path, book_id: str) -> Path:
    """方案 §10：`data/books/<book_id>/runs/`（不是散在 lessons 目录里）。"""
    return Path(workspace_root) / "data" / "books" / book_id / "runs"


def latest_run_id(workspace_root: Path, book_id: str) -> str | None:
    root = book_runs_root(workspace_root, book_id)
    if not root.is_dir():
        return None
    runs = [d.name for d in root.iterdir() if (d / "state.json").is_file()]
    if not runs:
        return None
    runs.sort(key=lambda name: (root / name / "state.json").stat().st_mtime)
    return runs[-1]


def read_state_summary(workspace_root: Path, book_id: str, run_id: str) -> dict:
    store = RunStore.open(book_runs_root(workspace_root, book_id), run_id)
    payload = store.load_state()
    nodes = store.node_states()
    counts: dict[str, int] = {}
    for node in nodes.values():
        counts[node.execution_state.value] = counts.get(node.execution_state.value, 0) + 1
    return {"run_id": run_id, "book_id": payload.get("book_id"),
            "goal": payload.get("goal"), "profile": payload.get("profile"),
            "status": payload.get("status"),
            "created_at": payload.get("created_at"), "updated_at": payload.get("updated_at"),
            "node_counts": counts, "request_digest": payload.get("request_digest"),
            "plan_digest": payload.get("plan_digest"),
            "last_event_seq": store.events.last_seq}


__all__ = ["STATE_SCHEMA", "new_run_id", "NodeState", "RunStore", "book_runs_root",
           "latest_run_id", "read_state_summary"]
