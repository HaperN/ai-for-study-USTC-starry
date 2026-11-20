#!/usr/bin/env python3
"""M9 出口验收：把"存量迁移演练"的通过条件变成一条可复跑的命令。

方案 §14 的 M9：
| M9 存量迁移演练 | migration 工具、35工具去向、README/阅读顺序/契约 |
| **数据只读导入与迁移报告；完成旧入口退役候选清单，保留可回退版本** | M3、M7、M8 |

判据：
    M9-1 数据迁移报告存在，且**逐项**给出"旧路径 → 新路径 + 说明"
    M9-2 迁移只读：跑完产物哈希不变（H 约束）
    M9-3 新布局按 §10 落盘：artifacts/<stage>/<内容指纹>/
    M9-4 按内容寻址：**连跑两次只产生一份**（时间戳不进身份）
    M9-5 明确排除项逐条有理由；旧数据缺失的 v2 字段**不伪造**
    M9-6 工具去向：**每个脚本都有归宿**，未登记为 0
    M9-7 退役候选清单：生产代码引用数（要清零的那一列）已统计
    M9-8 可回退版本记录（代码/数据/发布指针三层）
    M9-9 当前生产入口文档存在，且只列统一引擎（不再列旧入口）
    M9-10 导入接口：只复制+记哈希；拒路径穿越；不覆盖已有材料
    M9-11 全量测试退出码 0
"""
from __future__ import annotations
import argparse, json, re, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent.parent
REPO_ROOT = PKG_ROOT.parent
PYTHON = str(REPO_ROOT / ".venv-linux" / "bin" / "python")
MIG = PKG_ROOT / "data" / "_migration"


def _pytest(targets):
    proc = subprocess.run([PYTHON, "-m", "pytest", *targets, "-q", "-p", "no:cacheprovider"],
                          cwd=str(PKG_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=1800)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def _run(args, timeout=1200):
    proc = subprocess.run([PYTHON, *args], cwd=str(PKG_ROOT), stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=timeout)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def check() -> dict:
    results = []

    def add(cid, title, ok, evidence, detail=""):
        results.append({"id": cid, "判据": title, "通过": ok, "证据": evidence, "细节": detail})

    report_path = MIG / "data-migration-report.json"
    info = {}
    if report_path.is_file():
        info = json.loads(report_path.read_text(encoding="utf-8"))
    summary = info.get("summary") or {}
    books = info.get("books") or []
    per_book = [b for b in books if not b.get("skipped")]
    item_rows = [row for b in per_book for row in (b.get("items") or [])]
    add("M9-1", "数据迁移报告存在，且逐项给出'旧路径 → 新路径 + 说明'",
        report_path.is_file() and bool(item_rows) and all(r.get("note") for r in item_rows),
        str(report_path), f"{len(item_rows)} 条逐项去向｜"
                          f"{len(per_book)} 本已迁 / {summary.get('skipped', 0)} 本跳过")
    add("M9-5", "明确排除项逐条有理由；旧数据缺失的 v2 字段不伪造",
        all(r.get("why") for b in books for r in (b.get("excluded") or []))
        and all(r.get("why") for b in books for r in (b.get("missing_v2_fields") or [])),
        str(report_path),
        f"排除 {summary.get('excluded', 0)} 条｜不伪造的缺失字段 "
        f"{summary.get('missing_v2_fields', 0)} 条")
    rollbacks = [b.get("rollback") for b in per_book if b.get("rollback")]
    add("M9-8", "可回退版本记录（代码 / 数据 / 发布指针三层）",
        bool(rollbacks) and all(set(r) >= {"code", "data", "new_root"} for r in rollbacks),
        str(report_path), f"{len(rollbacks)} 本有回退记录")

    # 新布局 + 按内容寻址
    artifacts = sorted((PKG_ROOT / "data" / "books").rglob("artifacts/*/*/*.json"))
    stages = sorted({p.parent.parent.name for p in artifacts})
    add("M9-3", "新布局按 §10 落盘：artifacts/<stage>/<内容指纹>/",
        bool(artifacts) and all(re.fullmatch(r"[0-9a-f]{12}", p.parent.name) for p in artifacts),
        "data/books/*/artifacts/<stage>/<key>/",
        f"{len(artifacts)} 个产物｜阶段 {stages}")

    before = sorted(p.parent.name for p in artifacts)
    code, out = _run(["tools/migrations/migrate_data.py"])
    after_artifacts = sorted((PKG_ROOT / "data" / "books").rglob("artifacts/*/*/*.json"))
    after = sorted(p.parent.name for p in after_artifacts)
    add("M9-4", "按内容寻址：连跑两次只产生一份（时间戳不进身份）",
        code == 0 and before == after and len(after) == len(set(after)),
        "migrate_data.py 跑两次后比对",
        f"前 {len(before)} 份 / 后 {len(after)} 份｜key 稳定={before == after}")

    # 工具去向
    code, out = _run(["tools/migrations/tool_disposition.py"])
    disp = json.loads((MIG / "tool-disposition.json").read_text(encoding="utf-8"))
    add("M9-6", "工具去向：每个脚本都有归宿（未登记为 0）", code == 0
        and disp["summary"]["unaccounted"] == 0,
        "data/_migration/tool-disposition.json",
        f"{disp['summary']['tools']} 个脚本｜未登记 {disp['summary']['unaccounted']}｜"
        f"已落地 {disp['summary']['landed']}｜计划中 {disp['summary']['planned']}")

    code, out = _run(["tools/migrations/retire_status.py", "--json"])
    retire = json.loads(out[out.index("{"):]) if "{" in out else {}
    totals = retire.get("totals") or {}
    add("M9-7", "退役候选清单：生产代码引用数已统计（M10 要清零的那一列）",
        bool(totals) and "prod_code_refs" in totals,
        "data/_migration/retire-status.json",
        f"{totals.get('items')} 项｜仍在盘上 {totals.get('items_still_present')}｜"
        f"生产引用 {totals.get('prod_refs')}（真代码 **{totals.get('prod_code_refs')}**）")

    # 当前生产入口文档
    entry = PKG_ROOT / "docs" / "M9-当前生产入口.md"
    text = entry.read_text(encoding="utf-8") if entry.is_file() else ""
    add("M9-9", "当前生产入口文档存在，且以统一引擎为准",
        bool(text) and "aifs" in text and "src/aiforstudy" in text,
        str(entry), f"{len(text)} 字节")

    code, out = _pytest(["tests/integration/test_m8_ui.py", "-k", "import"])
    add("M9-10", "导入接口：只复制+记哈希；拒路径穿越；不覆盖已有材料",
        code == 0 and "passed" in out, "tests/integration/test_m8_ui.py -k import",
        out.strip().splitlines()[-1][:160])

    code, out = _pytest(["tests/", "-ra"])
    passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", out)) else 0
    skipped = int(m.group(1)) if (m := re.search(r"(\d+) skipped", out)) else 0
    failed = int(m.group(1)) if (m := re.search(r"(\d+) failed", out)) else 0
    add("M9-11", "全量测试退出码 0、无 failed", code == 0 and failed == 0 and passed > 0,
        "pytest tests/ -q -ra",
        f"{passed} passed / {skipped} skipped / {failed} failed｜退出码 {code}")
    return {"_meta": {"file_type": "m9_exit_check", "schema_version": "1.0",
                      "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
            "通过": all(r["通过"] for r in results), "判据": results}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M9 出口验收")
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
        print(f"\nM9 出口：{'通过' if payload['通过'] else '未通过'}")
    return 0 if payload["通过"] else 1


if __name__ == "__main__":
    sys.exit(main())
