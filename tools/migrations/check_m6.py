#!/usr/bin/env python3
"""M6 出口验收：把"可恢复运行和费用"的通过条件变成一条可复跑的命令。

方案 §14 的 M6：

| 工作包 | 主要文件/模块 | 交付与通过条件 | 依赖 |
|---|---|---|---|
| M6 可恢复运行和费用 | pipeline/budget/state、providers/llm、core/usage/store |
| | **缓存、重试、最后一批预算、取消、未知费用、原子检查点、范围隔离测试通过** | M2；联测依赖 M5 |

判据：

    M6-1 缓存：七项输入构成指纹；文件存在不算命中；输出被改/被删即失效；坏缓存当没有
    M6-2 指纹不含 run_id/时间/绝对路径；**含书籍/范围/单元归属**（跨书不串号）
    M6-3 重试：一个逻辑请求的尝试总数受一个上界约束（网络与格式错误共用）
    M6-4 顺序：响应到达**先记账再解析**；坏 JSON 的用量也记上
    M6-5 未知费用：超时记 unknown 且**不填零**；预约保守保留
    M6-6 缓存命中有独立账且 0 新增费用，且不触碰 transport
    M6-7 预算：每次尝试前都核对；超限即停
    M6-8 取消：请求前检查取消信号
    M6-9 replay 缺缓存**立即阻断**（不许回退真实服务）
    M6-10 原子检查点：半行/半写 JSON 不损坏账本与状态
    M6-11 范围隔离：基础准备与章生成分别汇总；按 run 分组
    M6-12 A16：只改装饰 CSS → 新增模型请求必须为 0；画面几何只重新审核
    M6-13 MinerU 用独立的服务操作/页数账，不混作聊天 token
    M6-14 全量测试退出码 0

用法：
    python tools/migrations/check_m6.py          # 定向机制检查
    python tools/migrations/check_m6.py --full   # 另行执行全套
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent.parent
REPO_ROOT = PKG_ROOT.parent
PYTHON = str(REPO_ROOT / ".venv-linux" / "bin" / "python")

M6_TESTS = "tests/contracts/test_m6_resume_and_cost.py"
RUNTIME_TESTS = "tests/integration/test_runtime_dependency_matrix.py"
FOCUSED_TESTS = [M6_TESTS, RUNTIME_TESTS, "tests/test_media_code_dependencies.py",
                 "tests/integration/test_v4_invalidation.py",
                 "tests/test_content_generation_v3.py::test_host_policy_recheck_reuses_candidate_with_zero_author_calls"]


def _pytest(targets: list[str]) -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(str(PKG_ROOT/p) for p in ("src", "tests", "tests/support"))
    proc = subprocess.run([PYTHON, "-m", "pytest", *targets, "-q", "-s", "-p", "offline_guard", "-p", "no:cacheprovider"],
                          cwd=str(PKG_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=1800, env=env)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def check(*, full: bool = False) -> dict:
    results: list[dict] = []

    def add(cid: str, title: str, ok: bool, evidence: str, detail: str = "") -> None:
        results.append({"id": cid, "判据": title, "通过": ok, "证据": evidence, "细节": detail})

    code, out = _pytest(FOCUSED_TESTS)
    runtime_evidence = []
    for line in out.splitlines():
        if "R6_RUNNER_EVIDENCE=" in line:
            try:
                runtime_evidence = json.loads(line.split("R6_RUNNER_EVIDENCE=", 1)[1])
            except ValueError:
                pass
    tail = out.strip().splitlines()[-1] if out.strip() else ""

    add("M6-1", "缓存：七项输入构成指纹；输出被改/被删即失效；坏缓存当没有", code == 0,
        M6_TESTS, tail[:160])
    add("M6-2", "指纹不含 run_id/时间/绝对路径，且含书籍/范围/单元归属（跨书不串号）",
        code == 0, M6_TESTS, tail[:160])
    add("M6-3", "重试：一个逻辑请求的尝试总数受一个上界约束", code == 0, M6_TESTS, tail[:160])
    add("M6-4", "响应到达先记账再解析；坏 JSON 的用量也记上", code == 0, M6_TESTS, tail[:160])
    add("M6-5", "未知费用记 unknown 且不填零；预约保守保留", code == 0, M6_TESTS, tail[:160])
    add("M6-6", "缓存命中独立记账、0 新增费用、不触碰 transport", code == 0, M6_TESTS,
        tail[:160])
    add("M6-7", "预算：每次尝试前都核对；超限即停", code == 0, M6_TESTS, tail[:160])
    add("M6-8", "取消：请求前检查取消信号", code == 0, M6_TESTS, tail[:160])
    add("M6-9", "replay 缺缓存立即阻断（不回退真实服务）", code == 0, M6_TESTS, tail[:160])
    add("M6-10", "原子检查点：半行/半写 JSON 不损坏账本与状态", code == 0, M6_TESTS, tail[:160])
    add("M6-11", "范围隔离：基础准备与章生成分别汇总；按 run 分组", code == 0, M6_TESTS,
        tail[:160])
    add("M6-12", "A16：装饰变化0新增调用；几何/审核只重审；生成提示词才重生成",
        code == 0 and len(runtime_evidence) == 5, RUNTIME_TESTS,
        json.dumps(runtime_evidence, ensure_ascii=False))
    add("M6-13", "MinerU 独立服务/页数账，不混作聊天 token", code == 0, M6_TESTS, tail[:160])

    # ── 结构判据：生产代码里只有一处模型出口
    from_scan = None
    try:
        sys.path.insert(0, str(HERE))
        from _scan import code_hits, iter_sources
        offenders = []
        for path in iter_sources(PKG_ROOT / "src/aiforstudy", suffixes=(".py",)):
            rel = path.relative_to(PKG_ROOT).as_posix()
            if rel.endswith(("providers/gateway.py", "providers/llm.py")):
                continue
            if code_hits(path, [r"urllib\.request", r"requests\.", r"httpx\.", r"http\.client",
                                r"socket\."]):
                offenders.append(rel)
        from_scan = f"发现：{offenders}" if offenders else "包内没有旁路的 HTTP 调用"
    except Exception as exc:                              # noqa: BLE001
        from_scan = f"扫描失败：{type(exc).__name__}: {exc}"
    add("M6-14", "唯一 gateway：包内除 providers/ 外没有旁路 HTTP 调用",
        from_scan == "包内没有旁路的 HTTP 调用", "扫描 src/aiforstudy 的代码行", from_scan)

    if full:
        code, out = _pytest(["tests/", "-ra"])
        passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", out)) else 0
        skipped = int(m.group(1)) if (m := re.search(r"(\d+) skipped", out)) else 0
        failed = int(m.group(1)) if (m := re.search(r"(\d+) failed", out)) else 0
        add("M6-15", "全量测试退出码 0、无 failed", code == 0 and failed == 0 and passed > 0,
            "pytest tests/ -q -ra",
            f"{passed} passed / {skipped} skipped / {failed} failed｜退出码 {code}")

    return {"_meta": {"file_type": "m6_exit_check", "schema_version": "1.0",
                      "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
            "通过": all(r["通过"] for r in results), "scope": "full" if full else "focused",
            "full_suite_executed": full, "runtime_evidence": runtime_evidence, "判据": results}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M6 出口验收")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--full", action="store_true", help="另行运行全套；默认仅定向机制检查")
    args = ap.parse_args(argv)
    payload = check(full=args.full)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for r in payload["判据"]:
            print(f"{'✓' if r['通过'] else '✗'} {r['id']} {r['判据']}")
            if r["细节"]:
                print(f"      {r['细节']}")
            print(f"      证据：{r['证据']}")
        print(f"\nM6 出口：{'通过' if payload['通过'] else '未通过'}")
    return 0 if payload["通过"] else 1


if __name__ == "__main__":
    sys.exit(main())
