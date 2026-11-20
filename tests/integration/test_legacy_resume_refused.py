"""T05／E01：**旧 run 可读、不可原地 resume**，且拒绝时旧目录零改动（§6.3）。

方案 §6.3 的原话是「旧 run／HTML 保持只读可浏览；缺 graph_version 视作 legacy。
**禁止把旧 audit 改名 text_audit，禁止在同一个旧 run 混新节点**」。本文件把这句话
变成两条可执行判据：

1. 缺 `graph_version` 的 run → resume **明确拒绝**，给出错误码与**新建命令建议**；
2. 拒绝时旧目录**一个字节都没变**。

第 2 条比第 1 条更容易被忽略：如果判定写在 `RunStore.open()` **之后**，`open` 会追加
事件、落盘状态，于是"我们拒绝了升级"这句话在数据上其实不成立 —— 它已经动过旧 run 了。
所以这里用整目录哈希来核对，而不是只看有没有抛异常。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from aiforstudy.contracts.enums import Goal  # noqa: E402
from aiforstudy.pipeline.engine import build_engine, execute, resume_request  # noqa: E402
from aiforstudy.pipeline.graph import GRAPH_VERSION  # noqa: E402
from aiforstudy.pipeline.resource_binding import LegacyRunError  # noqa: E402
from aiforstudy.pipeline.stubs import smoke_registry  # noqa: E402

from test_m2_engine import make_workspace, request_for  # noqa: E402


def _dir_fingerprint(root: Path) -> str:
    """整个 run 目录的内容指纹（路径 + 内容），用来证明"零改动"。"""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        if path.is_file():
            digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()


def test_new_run_records_graph_version(tmp_path):
    """创建期就写 graph_version —— "已建但还没跑"的 run 也必须能原地续跑。"""
    workspace, book = make_workspace(tmp_path)
    registry = smoke_registry()
    request = request_for(book.book_id, goal=Goal.PREPARE)
    _result, store = execute(build_engine(request, workspace, stage_registry=registry))
    state = store.load_state()
    assert state.get("graph_version") == GRAPH_VERSION == "7.0"


def test_run_without_graph_version_is_refused_and_left_untouched(tmp_path):
    workspace, book = make_workspace(tmp_path)
    registry = smoke_registry()
    request = request_for(book.book_id, goal=Goal.PREPARE)
    _result, store = execute(build_engine(request, workspace, stage_registry=registry))

    # 模拟"旧代码建的 run"：把 graph_version 抹掉（**只改这一个字段**，其余保持真实）
    state = store.load_state()
    state.pop("graph_version", None)
    store.save_state(state)

    before = _dir_fingerprint(store.root)
    with pytest.raises(LegacyRunError) as excinfo:
        resume_request(request, store.run_id, workspace, stage_registry=registry)
    decision = excinfo.value.decision
    assert decision.code == "legacy_graph_requires_new_run"
    assert decision.command, "拒绝时必须给出新建命令建议，否则用户无路可走"
    assert "aifs run" in decision.command and "--book" in decision.command
    assert store.run_id not in decision.command, (
        "建议的新命令**不许复用旧 run_id** —— 那会把新图混进旧目录")

    after = _dir_fingerprint(store.root)
    assert before == after, (
        "拒绝原地 resume 时旧 run 目录被改动了：判定必须做在 RunStore.open 之前，"
        "且只用只读读取（旧 run 是只读留档，不是可以顺手写一笔的工作目录）")


def test_legacy_run_still_readable(tmp_path):
    """"不可原地 resume" **不等于**"不可读"：旧产物要继续能浏览。"""
    workspace, book = make_workspace(tmp_path)
    registry = smoke_registry()
    request = request_for(book.book_id, goal=Goal.PREPARE)
    _result, store = execute(build_engine(request, workspace, stage_registry=registry))
    state = store.load_state()
    state.pop("graph_version", None)
    store.save_state(state)

    # 只读读取不抛异常
    payload = json.loads(store.state_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == store.run_id
    assert store.node_states(), "旧 run 的节点状态必须仍然读得出来"


def test_missing_state_json_is_not_treated_as_legacy(tmp_path):
    """断言建议里**不含**新建命令：没有这个 run 时不该让人去新建一个。

    （这个缺陷是集成测试抓出来的：T05 第一版把"什么都没有"也判成 legacy 并建议新建 run。
    修好后 `xfail(strict=True)` 报 XPASS，标记已按规矩摘掉 —— 缺口不会被忘掉。）
    """
    """目录不存在／state 缺失不是「legacy」：那是「没有这个 run」，不该给新建建议。

    用户敲错 run_id 时若被告知"你的图是旧的，去新建一个 run"，他会照做 ——
    既没找到原来的 run，又白建一个。计划 §6.3 管的是"**存在但老**的 run"。
    """
    workspace, book = make_workspace(tmp_path)
    registry = smoke_registry()
    request = request_for(book.book_id, goal=Goal.PREPARE)
    with pytest.raises(Exception) as excinfo:
        resume_request(request, "run-不存在-000000-000000", workspace,
                       stage_registry=registry)
    assert not isinstance(excinfo.value, LegacyRunError), (
        "把「没有这个 run」误报成「旧图需要新建」会让人按错误的建议去新建一个 run")
    assert not hasattr(excinfo.value, "decision") or not getattr(
        excinfo.value, "command", ""), "没有这个 run 时不该给出新建 run 的建议"
