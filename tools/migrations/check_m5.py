#!/usr/bin/env python3
"""M5 出口验收：把"图文审核和最终交互"的通过条件变成一条可复跑的命令。

方案 §14 的 M5：

| 工作包 | 主要文件/模块 | 交付与通过条件 | 依赖 |
|---|---|---|---|
| M5 图文审核和最终交互 | figures/request/spec/verify、audit、lesson/rewrite、interaction |
| | **每个图请求有终态；内容审核硬拦；交互消费最终教案版本；有界返修与严格状态** | M4 |

判据：

    M5-1 图请求终态：只有 fulfilled / 经认可的 not_applicable 算完成
    M5-2 终审核对"挂图与服务对象一致"，不只验证 SVG 存在
    M5-3 资源复用与落盘按**规格哈希**，不按文件名
    M5-4 后端只从**预检可用**的里选；表达不了就 blocked，不静默换图
    M5-5 表达式走 **AST 白名单**：14 种危险写法编译期即拒
    M5-6 图/审核/交互的生产代码里**不再有 eval/exec**
    M5-7 有界返修：轮次不可超；独立验收消耗同一轮次池；wontfix 要理由
    M5-8 改正文使下游失效（教案/图/交互/页面/包）
    M5-9 交互**消费最终教案版本**（硬断言）
    M5-10 承诺交互必须验证"参数变化导致预期状态变化"
    M5-11 全量测试退出码 0

用法：
    python tools/migrations/check_m5.py
    python tools/migrations/check_m5.py --json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent.parent
REPO_ROOT = PKG_ROOT.parent
PYTHON = str(REPO_ROOT / ".venv-linux" / "bin" / "python")
sys.path.insert(0, str(HERE))
from _scan import code_hits, iter_sources                                # noqa: E402

M5_TESTS = "tests/contracts/test_m5_figures_audit_interaction.py"
FIGURES_TESTS = "tests/test_figures.py"


def _pytest(targets: list[str]) -> tuple[int, str]:
    proc = subprocess.run([PYTHON, "-m", "pytest", *targets, "-q", "-p", "no:cacheprovider"],
                          cwd=str(PKG_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=1800)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def check() -> dict:
    results: list[dict] = []

    def add(cid: str, title: str, ok: bool, evidence: str, detail: str = "") -> None:
        results.append({"id": cid, "判据": title, "通过": ok, "证据": evidence, "细节": detail})

    code, out = _pytest([M5_TESTS])
    tail = out.strip().splitlines()[-1] if out.strip() else ""

    add("M5-1", "图请求终态：只有 fulfilled / 经认可的 not_applicable 算完成", code == 0,
        M5_TESTS, tail[:160])
    add("M5-2", "终审核对'挂图与服务对象一致'，不只验证 SVG 存在", code == 0,
        M5_TESTS + "（含 P-020/P-032 一类挂错概念的回归）", tail[:160])
    add("M5-3", "资源复用与落盘按规格哈希，不按文件名", code == 0, M5_TESTS, tail[:160])
    add("M5-4", "后端只从预检可用的里选；表达不了就 blocked", code == 0, M5_TESTS, tail[:160])
    add("M5-5", "表达式走 AST 白名单：14 种危险写法编译期即拒", code == 0, M5_TESTS,
        tail[:160])
    add("M5-7", "有界返修：轮次不可超；独立验收消耗同一池；wontfix 要理由", code == 0,
        M5_TESTS, tail[:160])
    add("M5-8", "改正文使下游失效（教案/图/交互/页面/包）", code == 0, M5_TESTS, tail[:160])
    add("M5-9", "交互消费最终教案版本（硬断言）", code == 0, M5_TESTS, tail[:160])
    add("M5-10", "承诺交互必须验证'参数变化导致预期状态变化'", code == 0, M5_TESTS,
        tail[:160])

    # ── M5-6 生产代码里不再有 eval/exec
    EVAL_OFFENCES = [r"\beval\s*\(", r"\bexec\s*\(", r"\bcompile\s*\(\s*\w+\s*,\s*[\"']exec",
                     r"__import__\s*\(", r"\bgetattr\s*\(\s*__builtins__"]
    offenders: list[str] = []
    for base in ("src/aiforstudy/blocks/figures", "src/aiforstudy/blocks/audit",
                 "src/aiforstudy/blocks/interaction", "blocks/figures/backends"):
        for path in iter_sources(PKG_ROOT / base, suffixes=(".py",)):
            rel = path.relative_to(PKG_ROOT).as_posix()
            if rel.endswith("safe_eval.py"):
                continue          # 它解释为什么不许用 eval（文档字符串里会提到）
            if code_hits(path, EVAL_OFFENCES):
                offenders.append(rel)
    add("M5-6", "图/审核/交互的生产代码里不再有 eval/exec", not offenders,
        "扫描 src/aiforstudy/blocks/{figures,audit,interaction} + blocks/figures/backends 的代码行",
        f"发现：{offenders}" if offenders else "没有发现 eval/exec")

    # ── 生产 plot 后端已切到白名单，且老图库仍然能画
    code_f, out_f = _pytest([FIGURES_TESTS])
    tail_f = out_f.strip().splitlines()[-1] if out_f.strip() else ""
    add("M5-5b", "生产 plot 后端已切到白名单且图库仍全绿", code_f == 0,
        FIGURES_TESTS, tail_f[:160])

    # ── M5-11 全量
    code, out = _pytest(["tests/", "-ra"])
    passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", out)) else 0
    skipped = int(m.group(1)) if (m := re.search(r"(\d+) skipped", out)) else 0
    failed = int(m.group(1)) if (m := re.search(r"(\d+) failed", out)) else 0
    add("M5-11", "全量测试退出码 0、无 failed", code == 0 and failed == 0 and passed > 0,
        "pytest tests/ -q -ra",
        f"{passed} passed / {skipped} skipped / {failed} failed｜退出码 {code}")

    return {"_meta": {"file_type": "m5_exit_check", "schema_version": "1.0",
                      "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
            "通过": all(r["通过"] for r in results), "判据": results}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M5 出口验收")
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
        print(f"\nM5 出口：{'通过' if payload['通过'] else '未通过'}")
    return 0 if payload["通过"] else 1


if __name__ == "__main__":
    sys.exit(main())
