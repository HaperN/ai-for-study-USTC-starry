"""「声明输入文件」机制的聚焦测试（T04 迁移新增）。

背景（这是它要治的病）：有些阶段读的文件**不在任何节点的输出清单里**。
最典型的是 `audit`：它真正消费 `work/legacy/lesson/lessons/<单元>.json`，
而 `lesson` 阶段登记的产物是 `products/lesson/<单元>.json` 这个外层封装。
那个被下游真正读的文件于是对"缓存复用"与"上游指纹"两条路径都**不可见** ——
外部改了它，audit 照样拿旧判词。

机制：`StageResult.input_files` 声明"我实际读过这些文件"，
runner 把**路径与摘要**记进 `NodeState`，并在复用判断时**重新读盘**核对。

这一组测试分两层：
* 契约层 —— 声明要能被序列化/反序列化（否则跨 resume 就丢了）；
* 行为层 —— `Runner._declared_inputs_unchanged()` 真的会因文件变化返回 False。

**不写**"没声明就绝对不能发现变化"这类断言：那会把"声明是唯一手段"写死成契约，
而将来可能有别的机制（例如把文件并入输出清单）覆盖同一场景。
这里只钉住"声明了就一定看得见"。
"""

from __future__ import annotations

import json

import pytest

from aiforstudy.contracts.stage import StageResult
from aiforstudy.pipeline.state import NodeState


# ────────────────────────────────────────── 契约层

def test_stage_result_carries_declared_inputs():
    result = StageResult.ok("audit", input_files=("/tmp/run/work/legacy/lesson/lessons/CH01.json",))
    assert result.input_files == ("/tmp/run/work/legacy/lesson/lessons/CH01.json",)


def test_stage_result_defaults_to_declaring_nothing():
    # 默认必须是空：既有阶段没人声明过，加上默认值就等于给所有阶段改指纹。
    assert StageResult.ok("lesson").input_files == ()


def test_declared_inputs_survive_serialization():
    """序列化必须带上它 —— 它要跨 resume 存活，丢了等于机制不存在。"""
    result = StageResult.ok("audit", input_files=("a/b.json", "c/d.json"))
    assert result.as_dict()["input_files"] == ["a/b.json", "c/d.json"]


def test_node_state_round_trip_keeps_declared_inputs_and_digests():
    state = NodeState("audit:CH01", "audit", "CH01")
    state.input_files = ("a/b.json",)
    state.input_digests = {"a/b.json": "sha256:deadbeef"}

    restored = NodeState.from_dict(json.loads(json.dumps(state.as_dict())))

    assert restored.input_files == ("a/b.json",)
    assert restored.input_digests == {"a/b.json": "sha256:deadbeef"}


def test_node_state_defaults_are_empty_so_old_states_still_load():
    """旧 run 的 state.json 里没有这两个字段，必须还能读（向后兼容）。"""
    legacy = NodeState("lesson:CH01", "lesson", "CH01").as_dict()
    legacy.pop("input_files")
    legacy.pop("input_digests")

    restored = NodeState.from_dict(legacy)

    assert restored.input_files == ()
    assert restored.input_digests == {}


# ────────────────────────────────────────── 行为层

class _FakeStore:
    def __init__(self, work):
        self.work = work


class _FakeRunner:
    """只借 `Runner` 的两个纯函数行为，避免为一条判定拉起整条流水线。

    `_declared_inputs_unchanged` / `_resolve_declared` 不依赖 runner 的其他状态，
    所以这里直接用类的绑定方法 + 一个最小 `self`。这样测试跑得快，而且
    **不会**因为流水线别处的改动而变红（它测的是判定本身）。
    """

    _store_root = None
    _tree_digest = None

    def __init__(self, root):
        from aiforstudy.pipeline.runner import Runner

        self._store_root = lambda: root
        self._tree_digest = Runner._tree_digest
        self._resolve_declared = lambda rel: Runner._resolve_declared(self, rel)
        self._states = {}
        self._declared_inputs_unchanged = lambda node: Runner._declared_inputs_unchanged(self, node)


class _Node:
    def __init__(self, node_id):
        self.node_id = node_id


def test_changed_declared_file_is_detected(tmp_path):
    """核心行为：声明过的文件被改过 → 判定为"变了"（于是不允许复用）。"""
    from aiforstudy.core.digest import digest_file
    from aiforstudy.pipeline.runner import Runner

    target = tmp_path / "legacy" / "lesson.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"body_markdown": "v1"}', encoding="utf-8")

    runner = _FakeRunner(tmp_path)
    node = _Node("audit:CH01")
    state = NodeState("audit:CH01", "audit", "CH01")
    state.input_files = (str(target),)
    state.input_digests = {str(target): digest_file(target)}
    runner._states = {"audit:CH01": state}

    assert Runner._declared_inputs_unchanged(runner, node) is True, "没改就不该判变了"

    target.write_text('{"body_markdown": "v2"}', encoding="utf-8")

    assert Runner._declared_inputs_unchanged(runner, node) is False, (
        "声明过的文件被改过之后必须判为已变化 —— 否则下游会拿修复前的判词")


def test_missing_declared_file_counts_as_changed(tmp_path):
    """文件被删掉也算变了：不能因为"读不到"就当成"没变"。"""
    from aiforstudy.core.digest import digest_file
    from aiforstudy.pipeline.runner import Runner

    target = tmp_path / "gone.json"
    target.write_text("{}", encoding="utf-8")

    runner = _FakeRunner(tmp_path)
    node = _Node("audit:CH01")
    state = NodeState("audit:CH01", "audit", "CH01")
    state.input_files = (str(target),)
    state.input_digests = {str(target): digest_file(target)}
    runner._states = {"audit:CH01": state}

    target.unlink()

    assert Runner._declared_inputs_unchanged(runner, node) is False


def test_nodes_that_declared_nothing_are_unaffected(tmp_path):
    """没声明的节点一律视为"没变" —— 这条保证新机制**不误伤**既有阶段。"""
    from aiforstudy.pipeline.runner import Runner

    runner = _FakeRunner(tmp_path)
    runner._states = {"lesson:CH01": NodeState("lesson:CH01", "lesson", "CH01")}

    assert Runner._declared_inputs_unchanged(runner, _Node("lesson:CH01")) is True


def test_relative_declared_path_resolves_against_the_run_work_dir(tmp_path):
    """相对路径按 run 工作目录解析：这样 state.json 换机器/换目录也还能对上。"""
    from aiforstudy.core.digest import digest_file
    from aiforstudy.pipeline.runner import Runner

    work = tmp_path / "work"
    (work / "legacy").mkdir(parents=True)
    target = work / "legacy" / "lesson.json"
    target.write_text("{}", encoding="utf-8")

    runner = _FakeRunner(work)
    node = _Node("audit:CH01")
    state = NodeState("audit:CH01", "audit", "CH01")
    state.input_files = ("legacy/lesson.json",)
    state.input_digests = {"legacy/lesson.json": digest_file(target)}
    runner._states = {"audit:CH01": state}

    assert Runner._declared_inputs_unchanged(runner, node) is True

    target.write_text('{"changed": true}', encoding="utf-8")

    assert Runner._declared_inputs_unchanged(runner, node) is False
