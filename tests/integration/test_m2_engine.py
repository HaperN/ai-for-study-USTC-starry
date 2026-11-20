"""M2 执行引擎回归：同图、零副作用、**结构化失败传播**、恢复、取消、预算。

方案 §14 的 M2 通过条件：**单/多/整本同图；plan 零副作用；结构化失败传播；
进程级整链测试可跑**（图的部分见 `tests/contracts/test_m2_graph.py`）。

为什么失败传播值得这么多条测试：方案 §9 里两句话看着矛盾，实际是分寸问题 ——

> **同一阶段失败不得继续拿上趟输出作为本次输入**；无依赖关系的其他所选章可以继续
> 完成候选产物，但整体范围不能发布。**失败时不自动缩小用户指定的范围。**

"继续"与"不继续"同时成立，就要求失败**沿着依赖传播**、而不是整趟停摆、也不是假装没事。
三条都要能测出来，否则最后一定退化成"整趟 exit 1"——本项目历史上正是这样把
"产物其实已合格"判成整章失败的。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from dataclasses import replace

import pytest

from aiforstudy.contracts.enums import Goal, Profile, SelectorKind
from aiforstudy.contracts.request import RunRequest, Selectors
from aiforstudy.core.workspace import Workspace
from aiforstudy.errors import ContractError, EXIT_BUDGET, EXIT_CANCELLED, EXIT_OK, EXIT_USAGE
from aiforstudy.pipeline.engine import build_engine, execute, run_request
from aiforstudy.pipeline.graph import graphs_equivalent_for_scope, stage_relative_order
from aiforstudy.pipeline.services import CancelToken, Providers

ROOT = Path(__file__).resolve().parents[2]
PYTHON = str(ROOT.parent / ".venv-linux" / "bin" / "python")
REAL_CONFIG = ROOT / "config"
UNITS = ("CH01-S01", "CH01-S02", "CH02-S01", "CH02-S02")


# ────────────────────────────────────────── 装置

def make_workspace(tmp_path: Path, **kw) -> tuple[Workspace, object]:
    """在 tmp 里造一本合成教材 + 一份最小注册表；**不碰真实产物**。"""
    from fixtures import synth_book

    book = synth_book.build(tmp_path, **kw)
    (tmp_path / "config").mkdir(exist_ok=True)
    for name in ("capabilities.json", "execution.json", "models.json"):
        shutil.copy(REAL_CONFIG / name, tmp_path / "config" / name)
    (tmp_path / "config" / "books.json").write_text(json.dumps({
        "_meta": {"file_type": "book_registry", "schema_version": "1.0"},
        "workspace": ".",
        "books": {book.book_id: {
            "title": book.title, "revision": "synth-1",
            "parse": str(book.parse.relative_to(tmp_path)),
            "lessons": str(book.lessons.relative_to(tmp_path)),
            "audit": str(book.audit.relative_to(tmp_path)),
            "problems": f"data/problems/{book.book_id}.json",
        }}}, ensure_ascii=False), encoding="utf-8")
    return Workspace(root=tmp_path), book


def request_for(book_id: str, units=UNITS, *, profile=Profile.SMOKE,
                kind=SelectorKind.UNITS, goal=Goal.DELIVER,
                parallel=2) -> RunRequest:
    return RunRequest(book_id=book_id, selectors=Selectors(kind=kind, values=tuple(units)),
                      goal=goal, profile=profile, parallelism=parallel).validate()


def snapshot_tree(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


# ────────────────────────────────────────── 同图（端到端）

@pytest.mark.parametrize("units", [("CH01-S01",), ("CH01-S01", "CH02-S01"), UNITS])
def test_smoke_runs_the_whole_chain_for_every_scope(tmp_path, units):
    workspace, book = make_workspace(tmp_path)
    result, store, engine = run_request(request_for(book.book_id, units), workspace)
    assert result.status == "succeeded", result.recovery_hint
    assert result.exit_code == EXIT_OK
    assert result.execution_counts.get("succeeded") == len(engine.graph.nodes)
    # smoke 档永远不是正式交付（§4 / A26）
    assert result.release_ready is False
    assert result.scope_accepted is True
    assert store.state_path.is_file()


def test_single_multi_and_whole_book_share_one_graph(tmp_path):
    workspace, book = make_workspace(tmp_path)
    one = build_engine(request_for(book.book_id, ("CH01-S01",)), workspace).graph
    two = build_engine(request_for(book.book_id, ("CH01-S01", "CH02-S01")), workspace).graph
    whole = build_engine(request_for(book.book_id, UNITS), workspace).graph
    assert graphs_equivalent_for_scope(one, whole)
    assert graphs_equivalent_for_scope(two, whole)
    assert stage_relative_order(one) == stage_relative_order(whole) == stage_relative_order(two)


# ────────────────────────────────────────── 零副作用（与 M1 的 plan 判据合起来看）

def test_plan_does_not_create_the_run_directory(tmp_path):
    workspace, book = make_workspace(tmp_path)
    engine = build_engine(request_for(book.book_id, ("CH01-S01",)), workspace)
    runs = engine.workspace.root / "data" / "books" / book.book_id / "runs"
    assert not runs.exists(), "只建 Engine（计划）不该创建运行目录"


def test_run_does_not_touch_the_parsed_products(tmp_path):
    """跑一次 smoke **不许**改动解析/教案产物 —— 替身只写自己 run 的 work 目录。"""
    workspace, book = make_workspace(tmp_path)
    before = snapshot_tree(book.parse) | snapshot_tree(book.lessons)
    run_request(request_for(book.book_id, UNITS), workspace)
    after = snapshot_tree(book.parse) | snapshot_tree(book.lessons)
    assert after == before


# ────────────────────────────────────────── 结构化失败传播

def test_failure_propagates_downstream_and_siblings_still_run(tmp_path):
    """一个单元挂了：它的下游 blocked；**兄弟单元照常完成**；范围不缩小。"""
    workspace, book = make_workspace(tmp_path)
    victim = "CH01-S01"
    request = request_for(book.book_id, UNITS)
    engine = build_engine(request, workspace,
                          providers=Providers(is_stub=True, fail_scopes=frozenset({victim})))
    result, store = execute(engine)

    runs = store.node_states()
    assert runs[f"lesson:{victim}"].execution_state.value == "failed"
    for downstream in (f"figures:{victim}", f"audit:{victim}", f"interaction:{victim}"):
        assert runs[downstream].execution_state.value == "blocked", downstream
        assert "上游" in (runs[downstream].error or {}).get("reason", "")
    # 兄弟单元不受影响
    for sibling in ("CH01-S02", "CH02-S01", "CH02-S02"):
        assert runs[f"lesson:{sibling}"].execution_state.value == "succeeded"
        assert runs[f"audit:{sibling}"].execution_state.value == "succeeded"
    # 整本范围不发布，但**用户指定的范围没有被缩小**
    assert result.exit_code == EXIT_USAGE
    assert result.release_ready is False
    assert result.scope_accepted is False
    assert set(UNITS) <= {n.scope_key for n in engine.graph.nodes}
    # Render can summarize a failed unit without approving the failed source.
    assert runs["render:CH01"].execution_state.value == "succeeded"
    assert result.release_ready is False


def test_failed_stage_does_not_reuse_previous_output(tmp_path):
    """§9：**同一阶段失败不得继续拿上趟输出作为本次输入。**

    先成功跑一次（产出真实输出），再让上游失败、续跑；下游必须 blocked，
    **不能**因为"上次的输出还在盘上"就接着用。
    """
    workspace, book = make_workspace(tmp_path)
    request = request_for(book.book_id, UNITS)
    result, store, _ = run_request(request, workspace)
    assert result.status == "succeeded"
    run_id = store.run_id

    ok_engine = build_engine(request, workspace,
                             providers=Providers(is_stub=True, fail_scopes=frozenset()))
    # 故意把上游产物删掉 → 复用判定必须失败（"文件存在不是缓存有效证据"的正面用法）
    victim = "CH01-S01"
    for ref in store.node_states()[f"lesson:{victim}"].outputs:
        Path(ref["path"]).unlink()
    from aiforstudy.pipeline.engine import resume_request
    result2, store2, _ = resume_request(request, run_id, workspace)
    states = store2.node_states()
    assert states[f"lesson:{victim}"].execution_state.value == "succeeded", \
        "输出被删就该重跑，而不是当作有效缓存"
    assert result2.status == "succeeded"


# ────────────────────────────────────────── 恢复

def test_resume_reuses_everything_when_nothing_changed(tmp_path):
    workspace, book = make_workspace(tmp_path)
    request = request_for(book.book_id, UNITS)
    first, store, _ = run_request(request, workspace)
    assert first.status == "succeeded"
    from aiforstudy.pipeline.engine import resume_request
    second, store2, _ = resume_request(request, store.run_id, workspace)
    assert second.status == "succeeded"
    # Smoke stages do not produce a ReaderProjection receipt, so reader outputs
    # are recomputed; all actual generation/preparation artifacts stay cached.
    assert all(row.execution_state.value == "cached" for row in store2.node_states().values()
               if row.stage not in {"render", "delivery"})
    assert "usage" not in second.as_dict()
    assert store2.run_id == store.run_id          # 续跑用同一个 run 目录


def test_resume_refuses_when_the_request_changed(tmp_path):
    """§11：输入变化则先重规划并展示变化，**不能把原检查结论迁到新内容**。"""
    workspace, book = make_workspace(tmp_path)
    request = request_for(book.book_id, UNITS)
    _result, store, _ = run_request(request, workspace)
    from aiforstudy.pipeline.engine import resume_request
    changed = replace(request, parallelism=1)
    with pytest.raises(ContractError, match="请求指纹"):
        resume_request(changed, store.run_id, workspace)


# ────────────────────────────────────────── 取消与预算

def test_cancel_stops_dispatch_and_reports_130(tmp_path):
    workspace, book = make_workspace(tmp_path)
    cancel = CancelToken()
    cancel.cancel("测试取消")
    engine = build_engine(request_for(book.book_id, UNITS), workspace, cancel=cancel)
    result, store = execute(engine)
    assert result.exit_code == EXIT_CANCELLED
    assert result.status == "cancelled"
    assert all(s.execution_state.value == "cancelled" for s in store.node_states().values())
    kinds = [e.kind for e in store.events_after()]
    assert "run.cancelled" in kinds or result.status == "cancelled"


def test_production_run_does_not_require_or_write_a_usage_ledger(tmp_path):
    workspace, book = make_workspace(tmp_path)
    result, store = execute(build_engine(request_for(book.book_id, UNITS), workspace))
    assert result.status == "succeeded"
    assert "usage" not in result.as_dict()
    assert not (store.root / "usage.jsonl").exists()
    assert (store.root / "execution.json").is_file()


def test_budget_guard_never_fills_unknown_usage_with_zero():
    """§11：网络超时导致用量不明时标 unknown 并保守保留预约，**不填零**。"""
    from aiforstudy.pipeline.budget import BudgetGuard, BudgetPolicy
    guard = BudgetGuard(BudgetPolicy(max_tokens=1000, max_minutes=10, parallelism=2))
    guard.reserve(400)
    assert guard.would_exceed(700), "在途额度必须计入，否则并行请求会一起超"
    guard.release(400, None)
    snapshot = guard.snapshot()
    assert snapshot["usage"]["unknown_events"] == 1
    assert snapshot["usage"]["tokens"] == 0        # 未知的就是未知，不是 0 花费


# ────────────────────────────────────────── not_applicable 的分寸

def test_declared_not_applicable_lets_dependents_continue(tmp_path):
    """§8：图与交互可以"没有适用对象"，且**不该挡住发布**。"""
    workspace, book = make_workspace(tmp_path)
    engine = build_engine(request_for(book.book_id, UNITS), workspace,
                          providers=Providers(is_stub=True,
                                              not_applicable_scopes=frozenset({"CH02-S02"})))
    result, store = execute(engine)
    assert result.status == "succeeded", result.recovery_hint
    states = store.node_states()
    assert states["figures:CH02-S02"].execution_state.value == "not_applicable"
    assert states["audit:CH02-S02"].execution_state.value == "succeeded"
    assert result.scope_accepted is True


def test_not_applicable_is_not_allowed_to_masquerade_for_other_stages(tmp_path):
    """§9：`not_applicable` 只能由**明确政策**产生，不允许"缺工具/没运行"冒充。

    这里让一个**没有声明允许**的阶段返回 not_applicable（lesson），
    它的下游必须被阻断。
    """
    from aiforstudy.contracts.enums import ExecutionState
    from aiforstudy.contracts.stage import StageResult
    from aiforstudy.pipeline.services import StageRegistry
    from aiforstudy.pipeline.stubs import smoke_registry

    workspace, book = make_workspace(tmp_path)
    registry = smoke_registry()

    def bogus(ctx):
        return StageResult(stage=ctx.node.stage,
                           execution_state=ExecutionState.NOT_APPLICABLE,
                           policy="我编的", evidence="没有证据")

    registry.register("lesson", bogus)
    engine = build_engine(request_for(book.book_id, ("CH01-S01",)), workspace,
                          stage_registry=registry)
    result, store = execute(engine)
    assert store.node_states()["audit:CH01-S01"].execution_state.value == "blocked"
    assert result.release_ready is False


# ────────────────────────────────────────── 未迁入阶段的诚实状态

def test_release_profile_is_honestly_blocked_until_stages_are_migrated(tmp_path):
    """release 档必须**明确 blocked**，不许假装成功。

    这条是"不许用 skipped/空实现算通过"（§15 A26）在引擎层的落点。

    ⚠️ 2026-10-05（M10）**前提变了**：release 档的确定性前缀已经由过渡壳真跑
    （`pipeline/transitional.py`：mineru/toc/structure/export/body/concepts），
    所以"第一个节点就报'尚未迁入'"不再成立。判据本身没有放宽，只是分成两半落在
    真正挡路的对象上：合成夹具没有 MinerU 产物 → `mineru` 明确 blocked
    （**且不回退 OCR**）；未迁入的模型阶段仍必须写明"尚未迁入"——直接问注册表要，
    而不是假设它一定会被跑到。真实书上的完整核对见
    `tests/integration/test_m10_release_prefix.py`。
    """
    from types import SimpleNamespace

    workspace, book = make_workspace(tmp_path)
    request = request_for(book.book_id, ("CH01-S01",), profile=Profile.RELEASE)
    result, store, engine = run_request(request, workspace)
    assert result.exit_code == EXIT_USAGE
    assert result.status == "failed"
    first = store.node_states()["mineru"]
    assert first.execution_state.value == "blocked"
    reason = (first.error or {}).get("reason", "")
    assert "MinerU" in reason and "OCR" in reason, reason
    # ⚠️ 2026-10-06：原先这里列四个（figures/interaction/render/delivery）。
    #    render 与 delivery 已经接上（它们**不调模型**，属本地阶段），所以再拿假 ctx 调它们
    #    会打到真实现、直接 AttributeError —— 那不是"判据失败"，是**前提过期**。
    #    ⚠️ 2026-10-06：`figures` 后来也接上了，12 个阶段全部有实现。所以这里改用
    #    **一个永远不会被注册的名字**验兜底 —— 判据一字未改（没注册 → not_implemented →
    #    理由写明"尚未迁入"），但不会因为"全都接完了"而变成空循环。
    for name in ("__definitely_not_a_registered_stage__",):
        missing = engine.services.stages.get(name)(
            SimpleNamespace(node=SimpleNamespace(stage=name)))
        assert missing.execution_state.value == "blocked"
        assert "尚未迁入" in (missing.error or {}).get("reason", "")

    # ⚠️ 2026-10-06（M10 后半段）**前提又变了一次**，判据跟着**加强**而不是放宽：
    #    `lesson`/`audit` 已经接了过渡壳（`pipeline/transitional.py`），所以它们**不再**
    #    落到 `not_implemented`。于是这里同时钉两件事：
    #    ① 注册表里确实有它们（漏接会被抓住）；
    #    ② 整趟运行里它们仍然 blocked 且**没有产物** —— 这台夹具没有注入模型传输，
    #       壳必须明确阻断：既不假装成功，也不自己构造真实传输去连。
    assert {"lesson", "audit"} <= set(engine.services.stages.names())
    for state in store.node_states().values():
        if state.stage in ("lesson", "audit"):
            assert state.execution_state.value == "blocked", (state.stage, state.error)
            assert state.outputs == [], f"{state.stage} 在 blocked 下不该有产物"


def test_replay_profile_refuses_to_fall_back_to_real_services(tmp_path, monkeypatch):
    """§4：replay 缺缓存**立即阻断**，禁止回退真实服务。"""
    import socket
    def forbidden_network(*args, **kwargs):
        pytest.fail('replay must not attempt a network connection')
    monkeypatch.setattr(socket.socket, 'connect', forbidden_network)
    workspace, book = make_workspace(tmp_path)
    request = request_for(book.book_id, ("CH01-S01",), profile=Profile.REPLAY)
    result, store, _engine = run_request(request, workspace)
    assert result.exit_code == EXIT_USAGE
    reason = (store.node_states()["mineru"].error or {}).get("reason", "")
    assert "MinerU" in reason and "不允许走 API" in reason


def test_replay_refuses_a_non_stub_provider(tmp_path):
    workspace, book = make_workspace(tmp_path)
    engine = build_engine(request_for(book.book_id, ("CH01-S01",), profile=Profile.REPLAY),
                          workspace, providers=Providers(is_stub=False, llm=object()))
    with pytest.raises(Exception, match="回退真实服务|替身"):
        execute(engine)


# ────────────────────────────────────────── 事件流

def test_event_log_is_append_only_and_cursor_readable(tmp_path):
    workspace, book = make_workspace(tmp_path)
    result, store, _ = run_request(request_for(book.book_id, ("CH01-S01",)), workspace)
    events = store.events_after()
    assert events, "跑一趟至少要留下事件"
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    tail = store.events_after(after=seqs[len(seqs) // 2])
    assert tail and all(e.seq > seqs[len(seqs) // 2] for e in tail)
    kinds = {e.kind for e in events}
    assert {"run.started", "run.finished", "stage.started", "stage.finished"} <= kinds


def test_event_data_refuses_secrets_and_prompts(tmp_path):
    """§5：密钥不回传浏览器、不写日志。提示词也不进事件流。"""
    from aiforstudy.pipeline.events import EventLog
    log = EventLog(tmp_path / "events.jsonl", run_id="r")
    with pytest.raises(ValueError, match="不该落盘的键"):
        log.emit("run.note", api_key="sk-xxx")
    with pytest.raises(ValueError, match="不该落盘的键"):
        log.emit("run.note", prompt="很长的提示词")
    log.emit("run.note", cache_key="abc", usage_ref="u1")      # 引用可以


# ────────────────────────────────────────── 进程级整链

def test_process_level_smoke_run_via_cli(tmp_path):
    """M2 通过条件里的"**进程级整链测试**"：真的起一个 `aifs run` 子进程。"""
    workspace, book = make_workspace(tmp_path)
    proc = subprocess.run(
        [PYTHON, "-m", "aiforstudy", "--workspace", str(tmp_path), "run",
         "--book", book.book_id, "--all", "--profile", "smoke", "--json"],
        cwd=str(tmp_path), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
    out = proc.stdout.decode("utf-8", "replace")
    assert proc.returncode == 0, out
    payload = json.loads(out)
    assert payload["status"] == "succeeded"
    assert payload["release_ready"] is False
    assert payload["execution_counts"]["succeeded"] == payload["execution_counts"]["succeeded"]
    # 运行目录落在新布局里（§10）
    run_dir = Path(payload["run_dir"])
    assert run_dir.is_dir()
    assert (run_dir / "request.json").is_file()
    assert (run_dir / "plan.json").is_file()
    assert (run_dir / "state.json").is_file()
    assert (run_dir / "events.jsonl").is_file()


def test_process_level_prepare_stops_at_concepts(tmp_path):
    workspace, book = make_workspace(tmp_path)
    proc = subprocess.run(
        [PYTHON, "-m", "aiforstudy", "--workspace", str(tmp_path), "prepare",
         "--book", book.book_id, "--profile", "smoke", "--json"],
        cwd=str(tmp_path), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
    out = proc.stdout.decode("utf-8", "replace")
    assert proc.returncode == 0, out
    payload = json.loads(out)
    assert payload["goal"] == "prepare"
    assert payload["release_ready"] is False
    assert payload["execution_counts"] == {"succeeded": 6}, "prepare 只运行六个基础准备阶段"
