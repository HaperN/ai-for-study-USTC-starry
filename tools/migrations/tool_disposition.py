#!/usr/bin/env python3
"""M9 工具去向：**每个脚本都要有归宿**，不能有"没人管"的文件。

方案 §13 的 25 项文件级处置我已经落成 `retire_manifest.json`；但那份表是**按处置项**
组织的（一项可能含多个文件）。M9 的出口要求是"**35 工具去向**" —— 也就是
**按文件**过一遍，确认目录里没有哪个脚本既不在处置表里、也没人知道它该去哪。

所以本工具做一件判据很硬的事：**枚举 `tools/` 下的每一个文件**，逐个查它的去向；
查不到就报出来（`unaccounted`）。这比"清单里 25 项都有状态"强得多 ——
后者永远可能漏掉清单本身没覆盖的文件。

判据（与 `retire_status.py` 同一套口径）：

* `prod` / `prod_code`：生产代码里的引用（**这一列要清零**）；
* 本工具只读：不动文件、不删东西。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent.parent
REPO_ROOT = PKG_ROOT.parent
sys.path.insert(0, str(HERE))
from _scan import code_hits, iter_sources                                # noqa: E402

#: **按文件的去向表**。key 是相对 AIforStudy 的路径。
#: 去向取值：`pipeline`/`providers`/`blocks`/`checks`/`delivery`/`ui` = 迁入新包；
#: `migrations`/`research`/`verify` = 留在 tools 的非生产子目录；
#: `experiments` = 移出生产入口；`delete` = 替代验收后删除；`keep` = 本轮保留。
DISPOSITION: dict[str, tuple[str, str]] = {
    # ── 编排（迁入 pipeline/providers/blocks）
    "tools/run_book.py": ("pipeline", "整本并行编排 → pipeline/runner + cli；过渡壳在最终切换阶段删除"),
    "tools/run_chapter.py": ("pipeline", "单章编排 → pipeline/runner（同一张图）"),
    "tools/parse_pipeline.py": ("delete", "已删除；解析编排统一走 aiforstudy prepare/run 的 graph/planner/runner"),
    "tools/ingest_book.py": ("providers", "入库 → providers/mineru（只留 MinerU 路线）"),
    "tools/audit_loop.py": ("blocks", "返修回路 → blocks/audit/loop（M5 已落地，入口待接）"),
    "tools/clean_rebuild.py": ("delete", "已删除；全书准备统一走 aiforstudy prepare，不保留旧 manifest 写驱动"),
    # ── 检查 / 派生 / 维修
    "tools/doctor.py": ("checks", "11 条不变量 → checks + cli 的 check 子命令"),
    "tools/reconcile.py": ("checks", "整本派生视图汇总 → checks（M7 的 quality 已覆盖一半）"),
    "tools/quality_report.py": ("checks", "质量报告 → checks/quality（M7 已落地）"),
    "tools/parse_report.py": ("checks", "解析对账门 → checks（M3 的 catalog.verify 已覆盖）"),
    "tools/check_lesson_readiness.py": ("checks", "教案齐备检查 → checks"),
    "tools/style_lint.py": ("checks", "风格 lint → checks/style"),
    "tools/reconcile_tickets.py": ("checks", "工单对账 → cli 的 repair tickets 子命令"),
    "tools/selfcheck.py": ("verify", "自检 → tools/verify（调公共服务，不直接读目录）"),
    "tools/render_check.py": ("verify", "渲染检查 → tools/verify"),
    "tools/shot.js": ("verify", "截图 → tools/verify（截图成功不直接代表验收通过）"),
    "tools/compare_runs.py": ("verify", "两次运行比对 → tools/verify"),
    # ── 交付
    "tools/make_book_single_file.py": ("delivery", "整本单文件 → delivery（M7 的 payload+publish 已落地）"),
    "tools/make_single_file.py": ("delivery", "范围单文件 → delivery"),
    "tools/make_delivery_package.py": ("delivery", "打包 → delivery（本轮打出过空包，是要替换而不是修补的实证）"),
    "tools/make_one_pager.py": ("delivery", "一页纸 → delivery"),
    "tools/make_preview.py": ("ui", "原文/图审阅 → ui 的 inspect；旧教案预览删除"),
    "tools/make_study_template.py": ("experiments", "学习模板 → experiments/ui"),
    # ── 展示 / 检查辅助
    "tools/show_toc.py": ("ui", "inspect 子命令"),
    "tools/show_concepts.py": ("ui", "inspect 子命令"),
    "tools/show_lesson.py": ("ui", "inspect 子命令"),
    "tools/toc_e2e.py": ("verify", "目录端到端 → 转成集成测试（不再有独立不同步的编排）"),
    # ── 一次性迁移
    "tools/authority_chain.py": ("delete", "一次性迁移已退役；历史证据保留，无现行函数消费者"),
    "tools/authority_promote.py": ("delete", "旁路产物覆盖入口已删除；历史产物与备份保留"),
    # ── 研究
    "tools/eval_external_hips.py": ("research", "外部评测 → 不进生产安装依赖"),
    "lib/ted.py": ("research", "研究用 → 不进生产安装依赖"),
    # ── UI 演示（历史方案）
    "tools/make_ui_demo.py": ("experiments", "历史 UI 方案 → experiments/ui"),
    "tools/make_ui_demo2.py": ("experiments", "历史 UI 方案 → experiments/ui"),
    "tools/make_ui_demo3.py": ("experiments", "历史 UI 方案 → experiments/ui"),
    "tools/make_style_demos.py": ("experiments", "风格样例 → experiments/ui"),
    "tools/pack_demo.py": ("experiments", "演示打包 → experiments/ui"),
    # ── 记账
    "account_run.py": ("ui", "用量统计 → inspect usage（参数化 book/run 后删除写死书籍脚本）"),
}

#: 已经**迁完**的去向（新包里已经有对应实现）——用于区分"计划"与"已落地"
LANDED = {
    "pipeline": ("src/aiforstudy/pipeline", "graph/planner/runner/state/budget/events/cache/snapshot"),
    "providers": ("src/aiforstudy/providers", "mineru/llm/gateway/artifacts/capabilities"),
    "blocks": ("src/aiforstudy/blocks", "parse/concepts/figures/audit/interaction"),
    "checks": ("src/aiforstudy/checks", "offline/quality"),
    "delivery": ("src/aiforstudy/delivery", "resources/payload/publish"),
    "ui": ("src/aiforstudy/ui", "api/server + resources/control-ui"),
}


def inventory() -> dict:
    tools = sorted(p for p in (PKG_ROOT / "tools").iterdir() if p.is_file())
    extra = [PKG_ROOT / "account_run.py", PKG_ROOT / "lib" / "ted.py"]
    rows: list[dict] = []
    unaccounted: list[str] = []
    for path in tools + [p for p in extra if p.is_file()]:
        rel = path.relative_to(PKG_ROOT).as_posix()
        if rel.startswith("tools/migrations/") or path.name == "__init__.py":
            rows.append({"tool": rel, "destination": "migrations", "why": "M0–M9 的迁移与核对工具（非生产）",
                         "landed": True, "prod_code_refs": []})
            continue
        entry = DISPOSITION.get(rel)
        if entry is None:
            unaccounted.append(rel)
            rows.append({"tool": rel, "destination": "?", "why": "**去向未登记**",
                         "landed": False, "prod_code_refs": []})
            continue
        destination, why = entry
        landed = destination in LANDED
        # 生产代码里还有谁在引用它（**只认代码行**，注释不算）
        refs: list[str] = []
        stem = path.stem
        for src in iter_sources(PKG_ROOT / "src", suffixes=(".py",)):
            if code_hits(src, [rf"\b{stem}\b"]):
                refs.append(src.relative_to(PKG_ROOT).as_posix())
        rows.append({"tool": rel, "destination": destination, "why": why,
                     "landed": landed, "prod_code_refs": refs})
    return {"_meta": {"file_type": "tool_disposition", "schema_version": "1.0",
                      "generator": "aiforstudy.tools.migrations.tool_disposition",
                      "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                      "field_notes": {
                          "tool": "**逐个文件**过一遍；不能有'没人管'的脚本",
                          "destination": "pipeline/providers/blocks/checks/delivery/ui = 迁入新包；migrations/research/verify = 留在 tools 非生产子目录；experiments = 移出生产入口",
                          "landed": "新包里是否已经有对应实现（区分'计划'与'已落地'）",
                          "prod_code_refs": "生产代码里的引用（**只认代码行**）；这一列要清零",
                      }},
            "rows": rows,
            "unaccounted": unaccounted,
            "summary": {
                "tools": len(rows),
                "unaccounted": len(unaccounted),
                "landed": sum(1 for r in rows if r["landed"]),
                "planned": sum(1 for r in rows if not r["landed"]),
                "with_prod_refs": sum(1 for r in rows if r["prod_code_refs"]),
                "by_destination": _count(rows, "destination"),
            }}


def _count(rows: list[dict], key: str) -> dict:
    out: dict[str, int] = {}
    for row in rows:
        out[str(row[key])] = out.get(str(row[key]), 0) + 1
    return dict(sorted(out.items()))


def to_markdown(payload: dict) -> str:
    summary = payload["summary"]
    lines = ["# M9 工具去向清单", "",
             f"生成时间：{payload['_meta']['created_at']}", "",
             "## 汇总", "", "| 口径 | 值 |", "|---|---:|",
             f"| 脚本数 | {summary['tools']} |",
             f"| **去向未登记** | **{summary['unaccounted']}** |",
             f"| 新包已落地 | {summary['landed']} |",
             f"| 计划中 | {summary['planned']} |",
             f"| 仍被生产代码引用 | {summary['with_prod_refs']} |", "",
             "## 按去向分组", "", "| 去向 | 数量 | 新包里的位置 |", "|---|---:|---|"]
    for dest, count in summary["by_destination"].items():
        landed = LANDED.get(dest)
        where = f"`{landed[0]}`（{landed[1]}）" if landed else "（非生产目录）"
        lines.append(f"| `{dest}` | {count} | {where} |")
    lines += ["", "## 逐个脚本", "",
              "| 脚本 | 去向 | 已落地 | 生产代码引用 | 说明 |", "|---|---|---|---|---|"]
    for row in payload["rows"]:
        refs = "、".join(f"`{r}`" for r in row["prod_code_refs"][:3]) or "—"
        lines.append(f"| `{row['tool']}` | `{row['destination']}` | "
                     f"{'✓' if row['landed'] else '计划中'} | {refs} | {row['why']} |")
    if payload["unaccounted"]:
        lines += ["", "## ⚠️ 去向未登记的脚本（必须逐条补上）", ""]
        for rel in payload["unaccounted"]:
            lines.append(f"* `{rel}`")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M9 工具去向（只读）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    payload = inventory()
    out = PKG_ROOT / "data" / "_migration" / "tool-disposition.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    doc = PKG_ROOT / "docs" / "M9-工具去向.md"
    doc.write_text(to_markdown(payload), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        summary = payload["summary"]
        print(f"✓ 工具去向写入 {out}")
        print(f"✓ 人可读报告写入 {doc}")
        print(f"  脚本 {summary['tools']}｜**去向未登记 {summary['unaccounted']}**｜"
              f"已落地 {summary['landed']}｜计划中 {summary['planned']}｜"
              f"仍被生产代码引用 {summary['with_prod_refs']}")
        for dest, count in summary["by_destination"].items():
            print(f"    {dest}: {count}")
        for rel in payload["unaccounted"]:
            print(f"  ✗ 未登记：{rel}")
    return 1 if payload["unaccounted"] else 0


if __name__ == "__main__":
    sys.exit(main())
