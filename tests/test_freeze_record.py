"""`tools/freeze_record.py` 的测试。

为什么一个"只是记录"的工具也需要测试：**它一旦悄悄算错，冻结记录就是废纸，
而且不会有任何症状** —— 交付时人人看着一个 sha256 就放心了。
所以这里专门钉住三件最要命的事：

1. **确定性**：同一棵树两次算出同一个摘要（不然"冻结"无从谈起）；
2. **与 cwd 无关**：键是相对路径、内容是字节，换目录不许变；
3. **对内容敏感**：改一个字节就必须变 —— 否则它根本无法证明"这是哪一版代码"。

外加跳过解析：计划要求"记录 PASS／SKIP 及理由"，而"关键新项跳过数必须 0"
这句话只有把跳过**逐条**读出来才可复核。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _load_tool():
    """按路径加载工具模块（`tools/` 不是包，测试环境里也不该靠 sys.path 凑）。"""
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("_freeze_record", root / "tools" / "freeze_record.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FR = _load_tool()


def _tree(root: Path) -> Path:
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "a.py").write_text("A = 1\n", encoding="utf-8")
    (root / "config").mkdir(parents=True)
    (root / "config" / "x.json").write_text('{"k": 1}\n', encoding="utf-8")
    return root


# ────────────────────────────────────────── 确定性

def test_same_tree_yields_the_same_digest(tmp_path):
    root = _tree(tmp_path / "repo")
    first = FR.code_fingerprint(root)
    second = FR.code_fingerprint(root)
    assert first["digest"] == second["digest"]
    assert first["files"] == second["files"] == 2


def test_digest_does_not_depend_on_the_working_directory(tmp_path, monkeypatch):
    root = _tree(tmp_path / "repo")
    before = FR.code_fingerprint(root)["digest"]
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert FR.code_fingerprint(root)["digest"] == before, (
        "换了 cwd 摘要就变，说明键里混进了绝对路径 —— 那这份指纹换台机器就对不上了")


# ────────────────────────────────────────── 对内容敏感

def test_one_byte_change_changes_the_digest(tmp_path):
    root = _tree(tmp_path / "repo")
    before = FR.code_fingerprint(root)["digest"]
    (root / "src" / "pkg" / "a.py").write_text("A = 2\n", encoding="utf-8")
    assert FR.code_fingerprint(root)["digest"] != before, (
        "改了源码摘要却没变 —— 这个指纹证明不了任何事")


def test_a_new_file_changes_the_digest(tmp_path):
    root = _tree(tmp_path / "repo")
    before = FR.code_fingerprint(root)["digest"]
    (root / "src" / "pkg" / "b.py").write_text("B = 1\n", encoding="utf-8")
    after = FR.code_fingerprint(root)
    assert after["digest"] != before and after["files"] == 3


def test_rename_changes_the_digest_even_with_identical_content(tmp_path):
    """**同样的内容换个文件名也要变** —— 否则"删了 A 加了 B"探测不到。"""
    root = _tree(tmp_path / "repo")
    before = FR.code_fingerprint(root)["digest"]
    (root / "src" / "pkg" / "a.py").rename(root / "src" / "pkg" / "renamed.py")
    assert FR.code_fingerprint(root)["digest"] != before


# ────────────────────────────────────────── 排除项（产物不是代码）

@pytest.mark.parametrize("noise", [
    "src/__pycache__/a.cpython-312.pyc",
    "build/lib/aiforstudy/whatever.py",
    "dist/aiforstudy-0.1.0-py3-none-any.whl",
    "src/.pytest_cache/v/cache/lastfailed",
    ".cache/replay/abc.json",
    # 打包元数据：**构建一次 wheel 就会重新生成**，算进指纹就会让"同一版代码"
    # 因为"你构建过"而得到不同指纹（我在核对 wheel 与代码一致性时踩到）
    "src/aiforstudy.egg-info/SOURCES.txt",
    "src/aiforstudy.egg-info/PKG-INFO",
    "lib/x.dist-info/METADATA",
])
def test_generated_and_cache_paths_do_not_enter_the_fingerprint(tmp_path, noise):
    """`data/`（运行产物）、`build/`、缓存都不能进"代码指纹"，否则同一版代码会有多个指纹。"""
    root = _tree(tmp_path / "repo")
    before = FR.code_fingerprint(root)
    path = root / noise
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("noise\n", encoding="utf-8")
    after = FR.code_fingerprint(root)
    assert after["digest"] == before["digest"], f"{noise} 不该进指纹"
    assert after["files"] == before["files"]


def test_missing_roots_are_skipped_not_an_error(tmp_path):
    """只交付一部分目录时不该崩：没有的根直接跳过。"""
    root = _tree(tmp_path / "repo")
    result = FR.code_fingerprint(root, roots=("src", "definitely-missing-dir"))
    assert result["files"] == 1


# ────────────────────────────────────────── 跳过理由解析

def test_pytest_log_skips_are_parsed_with_locations_and_reasons(tmp_path):
    log = tmp_path / "suite.log"
    log.write_text(
        "................................\n"
        "SKIPPED [1] tests/test_baseline_acceptance.py:79: 没有 /x/data/parse\n"
        "SKIPPED [2] tests/integration/test_x.py:12: 本机缺对应教材产物/已归档\n"
        "7 failed, 1839 passed, 41 skipped in 451.34s\n"
        "FAILED tests/a.py::test_one - AssertionError: boom\n",
        encoding="utf-8")
    parsed = FR.parse_pytest_log(log)
    assert parsed["counts"]["passed"] == 1839 and parsed["counts"]["failed"] == 7
    assert parsed["skip_total"] == 3, "两条跳过分别是 1 个和 2 个用例，合计必须按个数算"
    reasons = {row["reason"] for row in parsed["skips"]}
    assert "没有 /x/data/parse" in reasons
    assert all(row["where"].count(":") == 1 for row in parsed["skips"]), parsed["skips"]
    assert parsed["failures"] == ["FAILED tests/a.py::test_one - AssertionError: boom"]


def test_missing_log_is_reported_as_absent_not_as_zero(tmp_path):
    """"没给日志"与"日志里有 0 条"必须是两件可区分的事。"""
    parsed = FR.parse_pytest_log(tmp_path / "nope.log")
    assert parsed["log"] is None
    assert "counts" not in parsed


# ────────────────────────────────────────── 端到端

def test_record_round_trips_through_json_with_all_four_sections(tmp_path):
    root = _tree(tmp_path / "repo")
    log = tmp_path / "suite.log"
    log.write_text("1 passed in 0.1s\n", encoding="utf-8")
    record = FR.build_record(root=root, label="unit-test", wheel_paths=[],
                             pytest_log=log, environment=None)
    payload = json.loads(json.dumps(record))
    for key in ("code", "git", "environment", "wheels", "suite"):
        assert key in payload, f"冻结记录缺 {key} 段"
    assert payload["suite"]["counts"]["passed"] == 1
    assert payload["environment"]["python"], "环境段至少要记下 Python 版本"
    # 摘要文本要能一眼看到指纹与计数，而不是让人去翻 JSON
    text = FR.summarise(payload)
    assert payload["code"]["digest"] in text and "passed" in text
