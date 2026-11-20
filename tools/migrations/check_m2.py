#!/usr/bin/env python3
"""M2 出口验收：把"范围与执行引擎"的通过条件变成一条可复跑的命令。

方案 §14 的 M2：

| 工作包 | 主要文件/模块 | 交付与通过条件 | 依赖 |
|---|---|---|---|
| M2 范围与执行引擎 | pipeline/planner、graph、runner、state、events；cli |
| | **单/多/整本同图；plan 零副作用；结构化失败传播；进程级整链测试可跑** | M1 |

判据：

    M2-1 阶段表只有一份（`CANONICAL_STAGES`），粒度覆盖 书/章/单元/范围
    M2-2 单 / 多 / 整本同图：阶段顺序与两两先后关系一致
    M2-3 图闭合：依赖不指向不存在的节点；无不可达节点
    M2-4 plan 零副作用：只建 Engine 不创建运行目录
    M2-5 结构化失败传播：失败沿依赖传播、兄弟单元继续、范围不缩小
    M2-6 恢复：输入未变则全复用（0 新调用）；请求指纹变了则拒绝
    M2-7 取消 → 130；预算 → 3
    M2-8 进程级整链：子进程跑 `aifs run --profile smoke` 成功并落盘 §10 布局
    M2-9 未迁入阶段诚实 blocked（不用空实现冒充通过）
    M2-10 全量测试退出码 0

用法：
    python tools/migrations/check_m2.py
    python tools/migrations/check_m2.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent.parent
REPO_ROOT = PKG_ROOT.parent
PYTHON = str(REPO_ROOT / ".venv-linux" / "bin" / "python")

GRAPH_TESTS = "tests/contracts/test_m2_graph.py"
ENGINE_TESTS = "tests/integration/test_m2_engine.py"


def _pytest(targets: list[str], cwd: Path = PKG_ROOT) -> tuple[int, str]:
    proc = subprocess.run([PYTHON, "-m", "pytest", *targets, "-q", "-p", "no:cacheprovider"],
                          cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=1800)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def _py(code: str, cwd: Path | None = None) -> tuple[int, str]:
    proc = subprocess.run([PYTHON, "-c", code], cwd=str(cwd or PKG_ROOT),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def check() -> dict:
    results: list[dict] = []

    def add(cid: str, title: str, ok: bool, evidence: str, detail: str = "") -> None:
        results.append({"id": cid, "判据": title, "通过": ok, "证据": evidence, "细节": detail})

    # ── M2-1 阶段表
    code, out = _py(
        "from aiforstudy.pipeline.graph import CANONICAL_STAGES, GRAINS\n"
        "grains={s.grain for s in CANONICAL_STAGES}\n"
        "print(len(CANONICAL_STAGES), sorted(grains))")
    ok = code == 0 and "'book'" in out and "'chapter'" in out and "'unit'" in out \
        and "'scope'" in out
    add("M2-1", "阶段表只有一份，粒度覆盖 书/章/单元/范围", ok,
        "aiforstudy.pipeline.graph.CANONICAL_STAGES", out.strip()[:160])

    # ── M2-2 同图 + M2-3 图闭合
    code, out = _pytest([GRAPH_TESTS])
    tail = out.strip().splitlines()[-1] if out.strip() else ""
    add("M2-2", "单 / 多 / 整本同图（阶段顺序与两两先后一致）", code == 0,
        GRAPH_TESTS, tail[:160])
    add("M2-3", "图闭合：依赖指向真实节点；无不可达节点；无环", code == 0,
        GRAPH_TESTS + "（含专用回归）", tail[:160])

    # ── M2-4 plan 零副作用
    code, out = _py(
        "import tempfile, json, pathlib, shutil\n"
        "tmp = pathlib.Path(tempfile.mkdtemp())\n"
        "sys_path = None\n"
        "import sys; sys.path.insert(0, 'tests')\n"
        "from fixtures import synth_book\n"
        "book = synth_book.build(tmp)\n"
        "(tmp/'config').mkdir(exist_ok=True)\n"
        "for name in ('capabilities.json','models.json'):\n"
        "    shutil.copy(pathlib.Path('config')/name, tmp/'config'/name)\n"
        "shutil.copy(pathlib.Path('tests/fixtures/legacy_budget/budget.json'), tmp/'config'/'budget.json')\n"
        "(tmp/'config'/'books.json').write_text(json.dumps({'_meta':{}, 'workspace':'.',"
        " 'books': {book.book_id: {'title': book.title, 'parse': str(book.parse.relative_to(tmp)),"
        " 'lessons': str(book.lessons.relative_to(tmp)), 'audit': str(book.audit.relative_to(tmp))}}}),"
        " encoding='utf-8')\n"
        "from aiforstudy.core.workspace import Workspace\n"
        "from aiforstudy.contracts.request import RunRequest, Selectors, Budget\n"
        "from aiforstudy.contracts.enums import SelectorKind, Profile\n"
        "from aiforstudy.pipeline.engine import build_engine\n"
        "ws = Workspace(root=tmp)\n"
        "req = RunRequest(book_id=book.book_id, selectors=Selectors(kind=SelectorKind.ALL),"
        " profile=Profile.SMOKE, budget=Budget(1000, 10)).validate()\n"
        "before = sorted(str(p.relative_to(tmp)) for p in tmp.rglob('*'))\n"
        "build_engine(req, ws)\n"
        "after = sorted(str(p.relative_to(tmp)) for p in tmp.rglob('*'))\n"
        "print('SAME' if before == after else 'CHANGED:' + str(set(after)-set(before)))",
        cwd=PKG_ROOT)
    add("M2-4", "plan 零副作用：只建 Engine 不创建运行目录、不写任何文件",
        code == 0 and "SAME" in out, "合成工作区快照比对", out.strip().splitlines()[-1][:160])

    # ── M2-5..M2-9 引擎行为
    code, out = _pytest([ENGINE_TESTS])
    tail = out.strip().splitlines()[-1] if out.strip() else ""
    add("M2-5", "结构化失败传播（下游 blocked、兄弟继续、范围不缩小）", code == 0,
        ENGINE_TESTS, tail[:160])
    add("M2-6", "恢复：未变则全复用（0 新调用）；请求指纹变化则拒绝", code == 0,
        ENGINE_TESTS, tail[:160])
    add("M2-7", "取消 → 130；预算停止 → 3；未知用量不填零", code == 0,
        ENGINE_TESTS + " + budget 单测", tail[:160])
    add("M2-8", "进程级整链：子进程 `aifs run --profile smoke` 成功并按 §10 落盘", code == 0,
        ENGINE_TESTS + "::test_process_level_smoke_run_via_cli", tail[:160])
    add("M2-9", "未迁入阶段诚实 blocked（release 档不假装成功；replay 不回退真实服务）",
        code == 0, ENGINE_TESTS, tail[:160])

    # ── M2-10 全量
    code, out = _pytest(["tests/", "-ra"])
    passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", out)) else 0
    skipped = int(m.group(1)) if (m := re.search(r"(\d+) skipped", out)) else 0
    failed = int(m.group(1)) if (m := re.search(r"(\d+) failed", out)) else 0
    add("M2-10", "全量测试退出码 0、无 failed", code == 0 and failed == 0 and passed > 0,
        "pytest tests/ -q -ra",
        f"{passed} passed / {skipped} skipped / {failed} failed｜退出码 {code}")

    return {"_meta": {"file_type": "m2_exit_check", "schema_version": "1.0",
                      "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
            "通过": all(r["通过"] for r in results), "判据": results}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M2 出口验收")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    payload = check()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for r in payload["判据"]:
            print(f"{'✓' if r['通过'] else '✗'} {r['id']} {r['判据']}")
            if r["细节"]:
                print(f"      {r['细节']}")
            print(f"      证据：{r['证据']}")
        print(f"\nM2 出口：{'通过' if payload['通过'] else '未通过'}")
    return 0 if payload["通过"] else 1


if __name__ == "__main__":
    sys.exit(main())
