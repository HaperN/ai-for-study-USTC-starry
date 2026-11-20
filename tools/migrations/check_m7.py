#!/usr/bin/env python3
"""M7 出口验收：把"渲染交付闭环"的通过条件变成一条可复跑的命令。

方案 §14 的 M7：

| 工作包 | 主要文件/模块 | 交付与通过条件 | 依赖 |
|---|---|---|---|
| M7 渲染交付闭环 | render/payload/resources、delivery、checks/browser |
| | **范围页和包由 manifest 构建；最终文件空目录断网验收通过；失败不覆盖旧发布** | M5、M6 |

判据：
    M7-1 载荷**只从 manifest 装**（不扫目录拼章）；页面引用不在清单里即拒绝
    M7-2 资源按内容寻址：同名不同字节改名不覆盖；同内容合并；跨书拒绝（A22）
    M7-3 标签/属性/URL 白名单净化；危险标记被拦（§12/A23）
    M7-4 单 HTML 断网：外部资源请求 = 0（A24）
    M7-5 丢图 = 0：页面引用到的本地图必须都在（A24）
    M7-6 ZIP 成员 hash 与 manifest 100% 一致；无 members 记 blocked（A24）
    M7-7 缺必需能力 → blocked，**不用 skipped 算通过**（A26）
    M7-8 原子发布：切指针不覆盖；失败不动指针、不留半成品目录
    M7-9 单章发布只替换目标章，其余章引用不变
    M7-10 检查未通过 → 不许发布（A23）；回退是切指针
    M7-11 质量报告按当前 scope 重算，**不依赖 problems.json**；P2 列出但不挡发布
    M7-12 全量测试退出码 0
"""
from __future__ import annotations

import argparse, json, re, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent.parent
REPO_ROOT = PKG_ROOT.parent
PYTHON = str(REPO_ROOT / ".venv-linux" / "bin" / "python")
M7_TESTS = "tests/contracts/test_m7_delivery.py"


def _pytest(targets):
    proc = subprocess.run([PYTHON, "-m", "pytest", *targets, "-q", "-p", "no:cacheprovider"],
                          cwd=str(PKG_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=1800)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def check() -> dict:
    results = []

    def add(cid, title, ok, evidence, detail=""):
        results.append({"id": cid, "判据": title, "通过": ok, "证据": evidence, "细节": detail})

    code, out = _pytest([M7_TESTS])
    tail = out.strip().splitlines()[-1] if out.strip() else ""
    for cid, title in (
        ("M7-1", "载荷只从 manifest 装；引用不在清单里即拒绝"),
        ("M7-2", "资源按内容寻址：同名不同字节改名不覆盖；跨书拒绝（A22）"),
        ("M7-3", "标签/属性/URL 白名单净化（§12/A23）"),
        ("M7-4", "单 HTML 断网：外部资源请求 = 0（A24）"),
        ("M7-5", "丢图 = 0：本地图引用必须都在（A24）"),
        ("M7-6", "ZIP 成员 hash 与 manifest 100% 一致；无 members 记 blocked（A24）"),
        ("M7-7", "缺必需能力 → blocked，不用 skipped 算通过（A26）"),
        ("M7-8", "原子发布：切指针不覆盖；失败不动指针"),
        ("M7-9", "单章发布只替换目标章，其余章引用不变"),
        ("M7-10", "检查未通过不许发布（A23）；回退是切指针"),
        ("M7-11", "质量报告按当前 scope 重算，不依赖 problems.json"),
    ):
        add(cid, title, code == 0, M7_TESTS, tail[:160])

    # 结构判据：delivery/ 里不许有"扫目录拼章"
    sys.path.insert(0, str(HERE))
    from _scan import code_hits, iter_sources
    offenders = []
    for path in iter_sources(PKG_ROOT / "src/aiforstudy/delivery", suffixes=(".py",)):
        rel = path.relative_to(PKG_ROOT).as_posix()
        if code_hits(path, [r"\.glob\(", r"\.rglob\(", r"os\.listdir", r"iterdir\("]):
            offenders.append(rel)
    add("M7-12", "delivery/ 里没有'扫目录拼章'（载荷只能由 manifest 装配）", not offenders,
        "扫描 src/aiforstudy/delivery 的代码行",
        f"发现：{offenders}" if offenders else "没有目录扫描")

    code, out = _pytest(["tests/", "-ra"])
    passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", out)) else 0
    skipped = int(m.group(1)) if (m := re.search(r"(\d+) skipped", out)) else 0
    failed = int(m.group(1)) if (m := re.search(r"(\d+) failed", out)) else 0
    add("M7-13", "全量测试退出码 0、无 failed", code == 0 and failed == 0 and passed > 0,
        "pytest tests/ -q -ra",
        f"{passed} passed / {skipped} skipped / {failed} failed｜退出码 {code}")
    return {"_meta": {"file_type": "m7_exit_check", "schema_version": "1.0",
                      "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
            "通过": all(r["通过"] for r in results), "判据": results}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M7 出口验收")
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
        print(f"\nM7 出口：{'通过' if payload['通过'] else '未通过'}")
    return 0 if payload["通过"] else 1


if __name__ == "__main__":
    sys.exit(main())
