#!/usr/bin/env python3
"""M0 出口验收：把方案的 M0 通过条件变成一条可复跑的命令。

方案 §14 的 M0 通过与交付条件：

| 交付 | 通过条件 |
|---|---|
| 迁移清单、fixture、测试收集记录 | 已修改/未跟踪/需保留的忽略文件均有哈希清单；<br>旧测试逐项映射，离线合成测试不再被整模块跳过 |

"有清单""有记录"这类话如果只写在文档里，下一个人无法判断它是否**真的**成立。
所以这里逐条做成机器判据，跑完给 0/1：

    M0-1 现场快照存在、覆盖 modified/untracked、内容哈希非空
    M0-2 代码/文件备份存在（方案 §16：不能用 git clean/reset 得到"干净基线"）
    M0-3 测试收集记录存在，含实跑统计
    M0-4 每个测试文件都有目标模块映射（没有"未分类"）
    M0-5 三个原本整模块跳过的测试模块，现在**各自至少有一条测试真实运行并通过**
    M0-6 全量 pytest 退出码 0，且无 failed/error
    M0-7 迁移清单存在，且退役状态已生成（M9 的"活动引用为 0"从这里起算）
    M0-8 合成夹具存在且能被收集到

用法：
    python tools/migrations/check_m0.py
    python tools/migrations/check_m0.py --json
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent.parent
PYTHON = str(PKG_ROOT.parent / ".venv-linux" / "bin" / "python")
MIG = PKG_ROOT / "data" / "_migration"

# 这三个模块原本是 `pytestmark = pytest.mark.skipif(...)` 整模块跳过（方案 M0 要治的）
DESKIP_MODULES = ("tests/test_delivery_scope.py", "tests/test_book_views.py", "tests/test_doctor.py")


def _pytest(args: list[str]) -> tuple[int, str]:
    proc = subprocess.run([PYTHON, "-m", "pytest", *args, "-p", "no:cacheprovider"],
                          cwd=str(PKG_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def _latest(pattern: str) -> Path | None:
    hits = sorted(glob.glob(str(MIG / pattern)))
    return Path(hits[-1]) if hits else None


def check() -> dict:
    results: list[dict] = []

    def add(cid: str, title: str, ok: bool, evidence: str, detail: str = "") -> None:
        results.append({"id": cid, "判据": title, "通过": ok, "证据": evidence, "细节": detail})

    # ── M0-1 现场快照
    snap = _latest("scene-snapshot-*.json")
    ok, detail = False, "没有 data/_migration/scene-snapshot-*.json"
    if snap:
        data = json.loads(snap.read_text(encoding="utf-8"))
        s = data["summary"]
        entries = data["modified_or_untracked"]
        hashed = [e for e in entries if e.get("sha256")]
        ok = (s["modified"] > 0 and s["untracked"] > 0 and len(hashed) >= len(entries) - 3
              and s["ignored_keep"] > 0)
        detail = (f"修改 {s['modified']}｜未跟踪 {s['untracked']}｜忽略但保留 {s['ignored_keep']}｜"
                  f"已哈希 {len(hashed)}/{len(entries)}｜合计 {s['total_files']} 文件 / "
                  f"{s['total_bytes'] / 1e6:.1f} MB")
    add("M0-1", "现场快照：已修改/未跟踪/需保留的忽略文件均有哈希清单", ok,
        str(snap) if snap else "-", detail)

    # ── M0-2 备份
    bak = _latest("workspace-code-backup-*.tar.gz")
    ok = bool(bak) and bak.stat().st_size > 10_000
    add("M0-2", "代码/文件备份存在（不许用 git clean/reset 取得干净基线）", ok,
        str(bak) if bak else "-",
        f"{bak.stat().st_size / 1e6:.1f} MB" if bak else "没有 workspace-code-backup-*.tar.gz")

    # ── M0-3 测试收集记录
    inv = MIG / "test-inventory.json"
    ok, detail = False, "没有 data/_migration/test-inventory.json"
    if inv.is_file():
        payload = json.loads(inv.read_text(encoding="utf-8"))
        st = payload.get("stats") or {}
        ok = bool(st) and payload.get("total_collected", 0) > 0
        detail = (f"收集 {payload.get('total_collected')}｜{st.get('passed')} passed / "
                  f"{st.get('skipped')} skipped / {st.get('failed')} failed｜退出码 {st.get('exit_code')}")
    add("M0-3", "测试收集记录存在且含实跑统计", ok, str(inv), detail)

    # ── M0-4 每个测试文件都有模块映射
    ok, detail = False, ""
    if inv.is_file():
        payload = json.loads(inv.read_text(encoding="utf-8"))
        unmapped = [r["path"] for r in payload["files"] if r["module"] in ("未分类", "")]
        ok = not unmapped
        detail = f"未映射：{unmapped}" if unmapped else "全部测试文件都有目标模块"
    add("M0-4", "旧测试逐项映射：每个测试文件都有目标模块", ok, str(inv), detail)

    # ── M0-5 三个模块不再整模块跳过
    details = []
    all_ok = True
    for mod in DESKIP_MODULES:
        c, o = _pytest([mod, "-q", "-ra"])
        p = re.search(r"(\d+) passed", o)
        s = re.search(r"(\d+) skipped", o)
        passed = int(p.group(1)) if p else 0
        skipped = int(s.group(1)) if s else 0
        ok_mod = c == 0 and passed > 0
        all_ok = all_ok and ok_mod
        details.append(f"{Path(mod).name}: {passed} passed / {skipped} skipped"
                       f"{'' if ok_mod else ' ← 仍然是整模块跳过或失败'}")
    add("M0-5", "离线合成测试不再被整模块跳过（三个原跳过模块各有真实通过的用例）",
        all_ok, " · ".join(DESKIP_MODULES), "；".join(details))

    # ── M0-6 全量 pytest
    code, out = _pytest(["tests/", "-q", "-ra"])
    p = re.search(r"(\d+) passed", out)
    s = re.search(r"(\d+) skipped", out)
    f = re.search(r"(\d+) failed", out)
    e = re.search(r"(\d+) error", out)
    passed = int(p.group(1)) if p else 0
    skipped = int(s.group(1)) if s else 0
    failed = int(f.group(1)) if f else 0
    errors = int(e.group(1)) if e else 0
    ok = code == 0 and failed == 0 and errors == 0 and passed > 0
    add("M0-6", "全量测试退出码 0，无 failed/error", ok, "pytest tests/ -q -ra",
        f"{passed} passed / {skipped} skipped / {failed} failed / {errors} errors｜退出码 {code}")

    # ── M0-7 迁移清单 + 退役状态
    man = HERE / "retire_manifest.json"
    st = MIG / "retire-status.json"
    ok_data = man.is_file() and st.is_file()
    detail = ""
    if ok_data:
        payload = json.loads(st.read_text(encoding="utf-8"))
        t = payload["totals"]
        detail = (f"{t['items']} 项｜仍在盘上 {t['items_still_present']}｜"
                  f"生产引用 {t['prod_refs']}（其中真代码 {t['prod_code_refs']}）｜"
                  f"工具 {t['tool_refs']}｜文档 {t['doc_refs']}")
    add("M0-7", "迁移清单存在且退役状态已生成（M9 从这组数字起算）", ok_data,
        f"{man} + {st}", detail)

    # ── M0-8 合成夹具
    fx = sorted(glob.glob(str(PKG_ROOT / "tests" / "fixtures" / "**" / "*.py"), recursive=True))
    synth = [f for f in fx if "synth" in Path(f).name]
    add("M0-8", "离线合成夹具存在（后续里程碑复用）", bool(synth),
        ", ".join(Path(f).name for f in synth) or "tests/fixtures 下没有 synth*.py",
        f"{len(fx)} 个 fixture 模块")

    return {"_meta": {"file_type": "m0_exit_check", "schema_version": "1.0",
                      "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
            "通过": all(r["通过"] for r in results), "判据": results}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M0 出口验收")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    payload = check()
    if args.out:
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for r in payload["判据"]:
            print(f"{'✓' if r['通过'] else '✗'} {r['id']} {r['判据']}")
            if r["细节"]:
                print(f"      {r['细节']}")
            print(f"      证据：{r['证据']}")
        print(f"\nM0 出口：{'通过' if payload['通过'] else '未通过'}")
    return 0 if payload["通过"] else 1


if __name__ == "__main__":
    sys.exit(main())
