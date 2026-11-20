"""M1 整链回归：**仓库外工作目录调用统一包**、**plan 零副作用**、**退出码不塌陷**。

M1 通过条件里那两条与"能不能真的用"直接相关：

> config/books.json 唯一权威；**仓库外工作目录能调用统一包**；同进程两本书不串状态；
> normalizer/vendor 可按锁定来源重建

为什么"仓库外"要单独测：本项目此前的入口全部靠 `__file__.parents[N]` 与
`sys.path.insert` 猜位置，于是"从哪个目录跑"会静默影响"处理哪本书"。M1 把它去掉之后，
必须有一台**站在仓库外**的测试来证明它真的还能用、且失败时是明确失败。

对应验收项：A01（仓库外 cwd 调用 help/plan/check）、A03（plan/check 保护目录内容与 mtime
不变、无 HTTP/模型/子进程/usage 追加）、A05（非法范围明确失败）。
"""
from __future__ import annotations

import json
from copy import deepcopy
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PYTHON = str(ROOT.parent / ".venv-linux" / "bin" / "python")
REAL_BOOK = "complex-analysis-v2"


@pytest.fixture
def fresh_book(tmp_path):
    """Portable, freshly indexed two-chapter catalog; never rely on live book age."""
    from support.preparation_book import _mini_book, _write_json
    ws = _mini_book(tmp_path / "fixture")
    parse = ws.root / "book/parse"
    catalog = json.loads((parse / "chapters.json").read_text())
    catalog['chapters'].append({'id':'CH02','title':'第二章 乙'})
    row = deepcopy(catalog['lesson_units'][1])
    row.update(unit_id='CH02-S01', chapter='CH02', title='乙之一节', original_text='original_text/CH02-S01.md')
    catalog['lesson_units'].append(row)
    _write_json(parse / 'chapters.json', catalog)
    (parse / row['original_text']).write_bytes((parse / 'original_text/CH01-S01.md').read_bytes())
    part = json.loads((parse / 'concepts.parts/CH01-S01.json').read_text())
    part['unit_id'] = row['unit_id']
    _write_json(parse / 'concepts.parts/CH02-S01.json', part)
    return ws



def run(args: list[str], cwd: Path, *, env: dict | None = None) -> subprocess.CompletedProcess:
    merged = dict(os.environ)
    merged.pop("AIFORSTUDY_WORKSPACE", None)
    if env:
        merged.update(env)
    return subprocess.run([PYTHON, "-m", "aiforstudy", *args],
                          cwd=str(cwd), env=merged,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)


def test_help_works_outside_the_repo(tmp_path):
    """A01：仓库外 cwd 调 help 必须能用（那是安装后的第一件事）。"""
    proc = run(["--help"], tmp_path)
    assert proc.returncode == 0, proc.stdout.decode()
    assert b"plan" in proc.stdout and b"books" in proc.stdout


def test_books_works_from_a_foreign_cwd_with_explicit_workspace(tmp_path):
    proc = run(["--workspace", str(ROOT), "books", "--json"], tmp_path)
    assert proc.returncode == 0, proc.stdout.decode()
    payload = json.loads(proc.stdout.decode())
    assert payload["_meta"]["file_type"] == "books_view"
    ids = {b["book_id"] for b in payload["books"]}
    assert REAL_BOOK in ids
    book = next(b for b in payload["books"] if b["book_id"] == REAL_BOOK)
    assert book["catalog_present"] is True and book["concepts_present"] is True


def test_env_variable_also_works_from_a_foreign_cwd(tmp_path):
    proc = run(["books", "--json"], tmp_path, env={"AIFORSTUDY_WORKSPACE": str(ROOT)})
    assert proc.returncode == 0, proc.stdout.decode()


def test_no_workspace_anywhere_is_a_loud_exit_2(tmp_path):
    """**退出码 2**（输入/配置错误）而不是 1：它没跑成，不是"跑了但不够好"。"""
    proc = run(["books"], tmp_path)
    assert proc.returncode == 2
    assert "找不到工作区" in proc.stdout.decode()


@pytest.mark.parametrize("selectors,expect", [
    (["--chapters", "CH99"], "没有"),
    (["--chapters", "CH04-CH02"], "反向区间"),
    (["--units", "CH03-S01-NOPE"], "没有这些教学单元"),
])
def test_illegal_scopes_fail_loudly(tmp_path, fresh_book, selectors, expect):
    """A05：未知编号、反向区间、跨书单元 → 明确失败，**不误跑整本**。"""
    proc = run(["--workspace", str(fresh_book.root), "plan", "--book", "mini-book", *selectors], tmp_path)
    assert proc.returncode == 2
    assert expect in proc.stdout.decode()


def test_missing_book_flag_fails(tmp_path):
    proc = run(["--workspace", str(ROOT), "plan", "--all"], tmp_path)
    assert proc.returncode == 2
    assert "required: --book" in proc.stdout.decode() or "--book" in proc.stdout.decode()


def test_plan_resolves_a_chapter_from_a_fresh_catalog(tmp_path, fresh_book):
    proc = run(["--workspace", str(fresh_book.root), "plan", "--book", "mini-book",
                "--chapters", "CH01", "--json"], tmp_path)
    assert proc.returncode == 0, proc.stdout.decode()
    payload = json.loads(proc.stdout.decode())
    scope = payload["resolved_scope"]
    assert scope == ['CH01', 'CH01-S01']
    assert payload["requires_preparation"] is False
    assert 'estimates' not in payload
    assert payload['target_count'] == 2
    assert payload['context_ref'].startswith('catalog:')


def test_plan_range_expands_in_textbook_order(tmp_path, fresh_book):
    proc = run(["--workspace", str(fresh_book.root), "plan", "--book", "mini-book",
                "--chapters", "CH02,CH01,CH02", "--json"], tmp_path)
    assert proc.returncode == 0, proc.stdout.decode()
    scope = json.loads(proc.stdout.decode())["resolved_scope"]
    chapters = []
    for unit in scope:
        chapter = unit.split("-", 1)[0]
        if chapter not in chapters:
            chapters.append(chapter)
    assert chapters == ["CH01", "CH02"], "章输入去重后必须按**教材顺序**排序（方案 §4）"


def _tree_state(root: Path) -> dict:
    """目录内容的"动过没有"凭据：路径 → (大小, mtime_ns)。"""
    state = {}
    for path in root.rglob("*"):
        if path.is_file():
            st = path.stat()
            state[str(path.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return state


@pytest.mark.parametrize("target", ["data/parse/" + REAL_BOOK, "data/lesson/" + REAL_BOOK,
                                    "data/audit/" + REAL_BOOK, "delivery"])
def test_plan_has_zero_side_effects(tmp_path, target):
    """A03：plan **不创建运行目录、不写产物、不改 mtime、不追加 usage**。

    这条比"返回值对不对"重要：一个会顺手建目录/补记录的 plan，会把"只想看看"
    变成一次真实的写操作 —— 而它在并行或只读场景下就是数据损坏的来源。
    """
    watched = ROOT / target
    if not watched.exists():
        pytest.skip(f"{target} 不在位（该产物已归档）")
    usage_log = ROOT / ".cache" / "model" / "usage.jsonl"
    before = _tree_state(watched)
    usage_before = usage_log.stat().st_size if usage_log.is_file() else None

    proc = run(["--workspace", str(ROOT), "plan", "--book", REAL_BOOK,
                "--chapters", "CH03", "--json"], tmp_path)
    assert proc.returncode == 0, proc.stdout.decode()

    after = _tree_state(watched)
    assert after == before, f"plan 动了 {target} 下的文件"
    usage_after = usage_log.stat().st_size if usage_log.is_file() else None
    assert usage_after == usage_before, "plan 不该追加 usage"


def test_plan_does_not_create_a_run_directory(tmp_path):
    runs = ROOT / "data" / "books" / REAL_BOOK / "runs"
    existed = runs.exists()
    proc = run(["--workspace", str(ROOT), "plan", "--book", REAL_BOOK, "--all", "--json"],
               tmp_path)
    assert proc.returncode == 0, proc.stdout.decode()
    if not existed:
        assert not runs.exists(), "plan 不该创建运行目录（方案 §11）"


def test_capabilities_probe_is_config_driven(tmp_path):
    proc = run(["--workspace", str(ROOT), "capabilities", "--json"], tmp_path)
    assert proc.returncode == 0, proc.stdout.decode()
    payload = json.loads(proc.stdout.decode())
    names = {item["name"] for item in payload["items"]}
    assert {"python:matplotlib", "bin:node"} <= names
    # 给模型的图后端选项必须来自探测结果，不是写死的五个名字
    assert "vector" in payload["figure_backends"], payload["figure_backends"]
    assert all(":" not in name for name in payload["figure_backends"]), \
        "给模型的必须是**后端名**，不是依赖名"


def test_sources_reports_the_vendor_source_verdict(tmp_path):
    """M1 核对的实际产出：vendor 的来源 wheel 被证明**内容一致**。

    这条测试守的是"来源被固定下来"这件事本身 —— 只记录版本号不算固定。
    """
    proc = run(["--workspace", str(ROOT), "sources", "--json"], tmp_path)
    payload = json.loads(proc.stdout.decode())
    assert proc.returncode in (0, 2), proc.stdout.decode()
    vendor = next(a for a in payload["artifacts"] if a["name"] == "hbep_controller_v2")
    assert vendor["source_wheel"]["verdict"] == "verified-identical"
    assert vendor["source_wheel"]["sha256"].startswith("sha256:")
    # 归一化后的指纹必须与锁里记的一致（否则就是"来源变了"）
    lock = json.loads((ROOT / "vendor.lock.json").read_text(encoding="utf-8"))
    assert (lock["artifacts"]["auditor_vendor"]["source_wheel"]["wheel_payload_normalized"]
            == vendor["source_wheel"]["wheel_payload_normalized"])


def test_sources_records_the_normalizer_source_rejection(tmp_path):
    """normalizer 的来源 wheel 是**被裁剪过的变体**，必须被显式拒绝而不是静默采用。"""
    proc = run(["--workspace", str(ROOT), "sources", "--json"], tmp_path)
    payload = json.loads(proc.stdout.decode())
    norm = next(a for a in payload["artifacts"]
                if a["name"] == "undergraduate-fitted-exam-system")
    assert norm["source_verdict"]["verdict"] == "rejected"
    assert norm["source_verdict"]["installed_only"], "必须列出 wheel 里缺了哪些模块"
    assert "normalizer" in payload["accepted_drift"], "漂移裁决必须写在锁里"


def test_every_declared_requirement_names_a_real_capability(tmp_path):
    """配置里写的 `requires` 名字必须**等于**探测产物的 name。

    为什么值得一条测试（2026-10-05 实测）：配置写的是 `binaries:pdflatex`，而探测产出的
    name 是 `bin:pdflatex` —— 名字对不上不会报错，只会让 `circuitikz` / `three` 两个后端
    被判成"缺依赖"。也就是说**能力还在，却被静默关掉了**；方案 §8 明确"未安装的后端
    不作为可选项"，反过来说已安装的后端也不该被误关。

    这条是通用的：任何将来新增的 requires 条目都会被它核对。
    """
    proc = run(["--workspace", str(ROOT), "capabilities", "--json"], tmp_path)
    assert proc.returncode == 0, proc.stdout.decode()
    payload = json.loads(proc.stdout.decode())
    names = {item["name"] for item in payload["items"]}
    declared = json.loads((ROOT / "config" / "capabilities.json").read_text(encoding="utf-8"))
    requires = set()
    for spec in (declared.get("figure_backends") or {}).values():
        requires.update(spec.get("requires") or [])
    requires.update(declared["browser_acceptance"]["requires"])
    unknown = sorted(requires - names)
    assert not unknown, f"这些 requires 指向了不存在的探测名：{unknown}；实际有：{sorted(names)}"


def test_figure_backends_come_from_the_config_not_from_hardcoded_names(tmp_path):
    """五个后端都在配置里；探测结果只反映"这台机器缺什么"。"""
    declared = json.loads((ROOT / "config" / "capabilities.json").read_text(encoding="utf-8"))
    assert set(declared["figure_backends"]) == {"vector", "plot", "schemdraw",
                                                "circuitikz", "three"}
    proc = run(["--workspace", str(ROOT), "capabilities", "--json"], tmp_path)
    payload = json.loads(proc.stdout.decode())
    probed = {item["name"].split(":", 1)[1] for item in payload["items"]
              if item["name"].startswith("figure:")}
    assert probed == set(declared["figure_backends"])


@pytest.mark.parametrize('selectors', [['--chapters','CH99'], ['--units','CH03-S01-NOPE']])
def test_stale_catalog_defers_membership_without_side_effects(tmp_path, selectors):
    from test_preparation_scope_v5 import stale_pdf_book
    ws, _, _ = stale_pdf_book(tmp_path/'stale')
    before = _tree_state(ws.root)
    proc = run(['--workspace',str(ws.root),'plan','--book','mini-book',*selectors,'--json'],tmp_path)
    assert proc.returncode == 0, proc.stdout.decode()
    plan = json.loads(proc.stdout)
    assert plan['requires_preparation'] is True and plan['resolved_scope'] is None
    assert _tree_state(ws.root) == before
