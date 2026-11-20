#!/usr/bin/env python3
"""M0 测试收集记录：把"现在到底测了什么、跳了什么、为什么跳"变成可核对的清单。

M0 通过条件：**旧测试逐项映射，离线合成测试不再被整模块跳过。**

为什么不能只看"695 passed / 82 skipped"这一行：那 82 个 skip 里混着三种完全不同的东西——

* `retired`   该能力已被有意下线（例如旧 OCR 路线、旧交付样例页），跳过是**结论**；
* `no-product` 本机缺真实产物（归档挪走了），能力还在、只是没样本 → **必须补合成夹具**；
* `no-tool`   缺外部二进制/库（node、matplotlib），属环境问题。

把三种混成一个数字，就会出现"全绿但核心能力零覆盖"的假绿——正是方案 §15 里
"不能用 skipped 算正式通过"要防的事。本工具把每个 skip 归到具体测试与具体类别，
并给出每一类该不该有动作。

用法：
    python tools/migrations/test_inventory.py
    python tools/migrations/test_inventory.py --no-run   # 只收集，不实跑（更快）
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
PYTHON = str(PKG_ROOT.parent / ".venv-linux" / "bin" / "python")

# 测试文件 → 它覆盖的生产模块（目标结构里的位置）。方案 M9 要求"每项旧判据有测试对应"，
# 这张表就是那份对应的起点：改名/搬家时按这张表核对，不许有测试变成无主。
MODULE_MAP: dict[str, tuple[str, str, str]] = {
    # 文件前缀/文件名 : (目标模块, 测试层, 说明)
    "test_mineru_adapt": ("providers/mineru", "unit", "MinerU 产物适配与来源记录"),
    "test_mineru_blocktypes": ("providers/mineru", "unit", "块类型识别"),
    "test_mineru_fetch": ("providers/mineru", "unit", "下载/分块/重试"),
    "test_toc_sources": ("blocks/parse/toc", "unit", "目录三来源"),
    "test_toc_reconcile": ("blocks/parse/toc", "contracts", "目录对账与决定"),
    "test_structure": ("blocks/parse/structure", "unit", "authority 结构构建"),
    "test_review_and_concepts": ("blocks/parse/concepts", "unit", "正文归属与概念抽取"),
    "test_rebuild_concepts": ("blocks/parse/concepts", "unit", "概念重建/登记"),
    "test_export": ("blocks/parse/export", "unit", "规范化与导出"),
    "test_images": ("blocks/parse/images", "unit", "图片/表格抽取与清单"),
    "test_clean": ("blocks/clean", "unit", "原文修复（迁移为 source-repair）"),
    "test_lesson": ("blocks/lesson", "unit", "教案生成/校验/例子与详解"),
    "test_patch_coverage": ("blocks/lesson", "unit", "补丁覆盖率"),
    "test_rewrite_bookkeeping": ("blocks/lesson", "unit", "返修记账"),
    "test_style_rules": ("core/style_rules", "unit", "风格规则"),
    "test_notation": ("core/notation", "unit", "记号规范"),
    "test_feedback": ("blocks/lesson", "unit", "反馈处理"),
    "test_figures": ("blocks/figures", "unit", "图请求/规格/后端/校验"),
    "test_rubric": ("checks/quality", "contracts", "评分规则"),
    "test_quality_report": ("checks/quality", "integration", "质量报告汇总"),
    "test_problems": ("checks/quality", "contracts", "问题汇总与交付声明"),
    "test_reconcile": ("pipeline/reconcile", "integration", "整本派生视图汇总"),
    "test_doctor": ("checks", "contracts", "11 条不变量与退出码"),
    "test_book_views": ("checks", "contracts", "三条整本派生视图不变量"),
    "test_render": ("blocks/render", "unit", "载荷与页面渲染"),
    "test_render_assets": ("blocks/render", "unit", "资产清单"),
    "test_layout": ("blocks/render", "unit", "排版契约"),
    "test_delivery_scope": ("delivery", "contracts", "交付范围只增不减"),
    "test_run_book": ("pipeline/runner", "integration", "整本并行编排"),
    "test_chapter_bookkeeping": ("pipeline/state", "unit", "章记账"),
    "test_compare_runs": ("tools/verify", "integration", "两次运行比对"),
    "test_usage_accounting": ("core/usage", "unit", "用量记账"),
    "test_book_registry": ("core/book", "contracts", "书籍注册表"),
    "test_refs": ("core/refs", "unit", "引用解析"),
    "test_style_lint": ("checks/style", "unit", "风格 lint"),
    "test_baseline_acceptance": ("-", "integration", "基线验收（读真实产物）"),
    "test_wiring": ("-", "integration", "接线核对（读真实产物）"),
    # ── M1 起新增（统一契约 / 引擎 / 夹具）────────────────────────────────
    "test_m1_contracts": ("contracts", "contracts", "M1 统一契约：构造即校验、指纹口径"),
    "test_m1_registry": ("core/book", "contracts", "M1 书籍注册表：没有默认书、明确失败、同进程两本书不串状态"),
    "test_m1_cli_offline": ("cli", "integration", "M1 统一入口：仓库外 cwd 可调用、plan 零副作用、退出码不塌陷"),
    "test_synth_fixture": ("tests/fixtures", "fixtures", "离线合成教材夹具自检（15 种 broken 各被对应不变量抓住）"),
    # ── M2 起新增（范围与执行引擎）──────────────────────────────────────
    "test_m2_graph": ("pipeline/graph", "contracts", "M2 阶段图：单/多/整本同图、图闭合、粒度与档位"),
    "test_m2_engine": ("pipeline/runner", "integration", "M2 执行器：零副作用、失败传播、恢复、取消、预算、进程级整链"),
    # ── M3 起新增（解析与身份迁移）──────────────────────────────────────
    "test_m3_identity": ("core/identity", "contracts", "M3 身份与原文契约：两套身份、歧义/待裁决、CanonicalSource、复核指纹"),
    "test_m3_catalog": ("blocks/parse/catalog", "integration", "M3 统一 catalog：两种 body 形状、容器/空缺分类、逐项对账、习题绑定"),
    # ── M4 起新增（全书索引与内容上下文）────────────────────────────────
    "test_m4_concepts": ("blocks/concepts", "contracts", "M4 概念索引：身份不按名字、单写方合并、三态分片、覆盖与冻结门"),
    "test_m4_context": ("pipeline/snapshot", "integration", "M4 上下文快照：三件套进请求、预算拒发、指纹与并行度无关"),
    # ── M5 起新增（图文审核和最终交互）──────────────────────────────────
    "test_m5_figures_audit_interaction": ("blocks/{figures,audit,interaction}", "contracts", "M5 图请求终态、AST 白名单、有界返修、交互版本绑定"),
    # ── M6 起新增（可恢复运行和费用）────────────────────────────────────
    "test_m6_resume_and_cost": ("providers/gateway + core/usage + pipeline/budget", "contracts", "M6 重试上界、先记账后解析、未知费用、原子检查点、范围隔离；真实Runner失效见test_runtime_dependency_matrix"),
    # ── M7 起新增（渲染交付闭环）────────────────────────────────────────
    "test_m7_delivery": ("delivery + checks", "contracts", "M7 载荷由 manifest 装配、资源内容寻址、白名单净化、原子发布、离线验收、质量报告与交付声明"),
    # ── M8 起新增（本地界面）────────────────────────────────────────────
    # ── M10 起新增（真实验收的离线可验部分）────────────────────────────
    "test_m10_acceptance_offline": ("acceptance A02/A03/A04 + run usage ledger", "contracts", "M10 离线验收：A02 按书隔离（含账本归属）、A03 plan/check 零副作用、A04 九组合精确目标集合与无 OCR 回退、运行级 usage.jsonl 追加式落盘"),
    "test_m10_release_prefix": ("pipeline/transitional + engine.default_stage_registry", "integration", "M10 release 确定性前缀：过渡壳注册表只接六个确定性阶段、真实书复用可达、模型阶段仍 blocked、断网/禁子进程下照跑、无上传与无 OCR 回退"),
    "test_m10_lesson_audit_shells": ("pipeline/transitional + blocks/{lesson,audit}", "integration", "M10 lesson/audit 过渡壳：真跑旧实现、模型访问全走 Gateway、账本行数==假 transport 实际调用数、断网禁子进程照跑、replay 缺缓存阻断不回退、有界返修驱动 quality_state"),
    # ⚠️ 这一项不是本任务新增的测试（它由并行的 A01 验收任务建立）。这里补映射是为了
    #    让 A32 的"每个测试模块都有归属"（未映射 = 0）继续成立 —— 它按**磁盘上的文件**
    #    对账，缺一项就 FAIL，跟谁写的无关。
    "test_m10_a01_fresh_env": ("acceptance A01 + cli/sources + providers/{normalizer,auditor}", "integration", "M10 A01：全新 venv 从锁定来源安装、仓库外 cwd 跑 help/plan/sources、打乱导入顺序、不读旧 venv/CourseForgeV2、不依赖个人 Blender 路径"),
    "test_m10_render_delivery_shells": ("pipeline/transitional render+delivery", "integration", "M10 render/delivery 过渡壳：真出页面、只渲染本次产出的单元、缺教案明确 blocked、交付做离线验收、环境阻断不冒充通过、不自动切发布指针"),
    "test_m10_interaction_shell": ("pipeline/transitional interaction", "integration", "M10 interaction 过渡壳：无注入传输必须 blocked（不许偷连真服务）、缺本次教案必须 blocked、有传输时真的走到模型调用且不静默成功"),
    "test_m10_figures_shell": ("pipeline/transitional figures", "integration", "M10 figures 过渡壳：不调模型（图由代码画）、需要图时真画真校验且只登记通过的、不需要图时 not_applicable 必须带 policy+evidence、缺本次教案必须 blocked"),
    "test_m10_real_transport": ("providers/transport", "contracts", "M10 真实传输：花钱闸门（无额度拒发/无密钥报错/报告不含密钥/来源顺序与旧实现一致）、图片输入（data URL 嵌入/缺图硬报错/无图不改形状/按最后一条 user 附/单独计数）"),
    "test_m10_concept_coverage": ("blocks/parse/concepts", "contracts", "M10 覆盖度尺子：类型词+编号不许归成空串（否则 _covers 永远判没覆盖、索引永远冻不了）、归一原意不许改坏、_covers 两边都钉、端到端编号定理分片不误判"),
    "test_m10_review_bridge": ("blocks/parse/review", "contracts", "M10 建议稿→可应用复核的桥：指纹必须对得上、裁决落到正确 toc_id、对不上硬报错不静默丢、弃权变 leave-pending 不许变成裁决、过期仍拒（A08）、真实草稿端到端两条都落地"),
    "test_m8_ui": ("ui/server + ui/api", "integration", "M8 本机 HTTP 全流程 + M9 导入：CLI/UI 计划等价、幂等、刷新不取消、重启 interrupted、路径穿越、同源令牌、密钥不回传、导入只复制+记哈希"),
}

SKIP_SUMMARY_RE = re.compile(r"^SKIPPED\s+\[(?P<count>\d+)\]\s+(?P<where>[^:]+):(?P<line>\d+):\s*(?P<reason>.*)$")


def _classify(reason: str) -> tuple[str, str]:
    """(类别, 建议动作)"""
    if "归档" in reason or "没有'第六章" in reason or "不在仓库里" in reason or "还没" in reason or "没有解析产物" in reason:
        return "no-product", "补离线合成夹具，让该能力有样本可跑"
    if "换机器" in reason or "没跑过" in reason or "不在位" in reason or "本机没有" in reason:
        return "no-product", "补离线合成夹具，让该能力有样本可跑"
    if "退役" in reason or "已下线" in reason:
        return "retired", "登记退役（方案 M9 要求），不要用 skip 掩盖"
    if "没有 node" in reason or "jsdom" in reason or "matplotlib" in reason:
        return "no-tool", "环境依赖；方案 §15 要求缺必需能力算 blocked，不算通过"
    if "PDF" in reason:
        return "no-product", "补合成样本或登记为需真实材料的验收项"
    return "unknown", "人工判读：它是结论还是缺样本？"


def collect_only() -> tuple[list[str], str]:
    proc = subprocess.run(
        [PYTHON, "-m", "pytest", "tests/", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=str(PKG_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = proc.stdout.decode("utf-8", "replace")
    ids = [ln.strip() for ln in out.splitlines()
           if "::" in ln and not ln.startswith(" ")]
    return ids, out


def run_all() -> tuple[dict, str]:
    proc = subprocess.run(
        [PYTHON, "-m", "pytest", "tests/", "-q", "-ra", "-p", "no:cacheprovider"],
        cwd=str(PKG_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = proc.stdout.decode("utf-8", "replace")
    tail = "\n".join(out.strip().splitlines()[-3:])
    stats = {"passed": 0, "skipped": 0, "failed": 0, "errors": 0, "exit_code": proc.returncode,
             "tail": tail}
    m = re.search(r"(\d+) passed", out)
    if m:
        stats["passed"] = int(m.group(1))
    m = re.search(r"(\d+) skipped", out)
    if m:
        stats["skipped"] = int(m.group(1))
    m = re.search(r"(\d+) failed", out)
    if m:
        stats["failed"] = int(m.group(1))
    m = re.search(r"(\d+) error", out)
    if m:
        stats["errors"] = int(m.group(1))
    return stats, out


def parse_skips(out: str) -> list[dict]:
    skips = []
    for ln in out.splitlines():
        m = SKIP_SUMMARY_RE.match(ln.strip())
        if not m:
            continue
        where = m.group("where").strip()
        reason = m.group("reason").strip()
        kind, action = _classify(reason)
        skips.append({"file": where, "line": int(m.group("line")),
                      "count": int(m.group("count")), "reason": reason,
                      "kind": kind, "action": action})
    return skips


def module_of(path: str) -> tuple[str, str, str]:
    stem = Path(path).stem
    if stem in MODULE_MAP:
        return MODULE_MAP[stem]
    return ("未分类", "unknown", "需要人工补映射")


def build(no_run: bool) -> dict:
    ids, _collect_out = collect_only()
    stats, run_out = ({} , "") if no_run else run_all()
    skips = [] if no_run else parse_skips(run_out)

    files: dict[str, dict] = {}
    for nid in ids:
        path = nid.split("::", 1)[0]
        mod, layer, note = module_of(path)
        rec = files.setdefault(path, {"path": path, "module": mod, "layer": layer,
                                      "note": note, "tests": 0, "module_skipped": False})
        rec["tests"] += 1

    for sk in skips:
        rec = files.setdefault(sk["file"], {"path": sk["file"], "module": "未分类",
                                            "layer": "unknown", "note": "", "tests": 0,
                                            "module_skipped": False})
        rec.setdefault("skip_reasons", []).append(
            {"line": sk["line"], "count": sk["count"], "reason": sk["reason"], "kind": sk["kind"]})

    total_tests = len(ids)
    return {
        "_meta": {
            "file_type": "test_inventory",
            "schema_version": "1.0",
            "generator": "aiforstudy.tools.migrations.test_inventory",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "field_notes": {
                "kind": "retired=能力已下线（skip 是结论）；no-product=缺真实产物（必须补合成夹具）；no-tool=缺外部二进制/库；unknown=需人工判读",
                "module": "该测试文件覆盖的目标结构模块（方案 §3 的目录），用于 M9 的'每项旧判据有测试对应'核对",
                "为什么要有这份清单": "方案 §15：passed/failed/skipped 单列，缺样本明确为未验证；不能用 skipped 算通过",
            },
        },
        "stats": stats,
        "total_collected": total_tests,
        "files": [files[k] for k in sorted(files)],
        "skips": skips,
    }


def to_markdown(payload: dict) -> str:
    s = payload.get("stats") or {}
    lines = ["# M0 测试收集记录", "",
             f"生成时间：{payload['_meta']['created_at']}",
             f"收集用例数：**{payload['total_collected']}**", ""]
    if s:
        lines += [f"实跑结果：**{s.get('passed', 0)} passed / {s.get('skipped', 0)} skipped / "
                  f"{s.get('failed', 0)} failed**（退出码 {s.get('exit_code')}）", "",
                  f"```\n{s.get('tail', '')}\n```", ""]
    lines += ["## 每个测试文件覆盖的目标模块", "",
              "| 测试文件 | 用例数 | 覆盖模块 | 层 | 说明 |", "|---|---:|---|---|---|"]
    for rec in payload["files"]:
        lines.append(f"| `{rec['path']}` | {rec['tests']} | `{rec['module']}` | {rec['layer']} "
                     f"| {rec.get('note', '')} |")
    lines += ["", "## 跳过项分类与应做动作", "",
              "| 文件:行 | 条数 | 类别 | 原因 | 应做动作 |", "|---|---:|---|---|---|"]
    for sk in payload["skips"]:
        lines.append(f"| `{sk['file']}:{sk['line']}` | {sk['count']} | **{sk['kind']}** "
                     f"| {sk['reason']} | {sk['action']} |")
    lines += ["", "类别口径：`retired`=能力已下线（skip 是结论）；`no-product`=缺真实产物"
                  "（必须补离线合成夹具）；`no-tool`=缺外部二进制/库；`unknown`=需人工判读。", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M0 测试收集记录（只读）")
    ap.add_argument("--no-run", action="store_true", help="只收集不实跑")
    ap.add_argument("--json-out", default=str(PKG_ROOT / "data" / "_migration" / "test-inventory.json"))
    ap.add_argument("--md-out", default=str(PKG_ROOT / "docs" / "M0-测试收集记录.md"))
    args = ap.parse_args(argv)

    payload = build(args.no_run)
    Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json_out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(args.md_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.md_out).write_text(to_markdown(payload), encoding="utf-8")
    print(f"✓ 测试清单写入 {args.json_out}")
    print(f"✓ 人可读报告写入 {args.md_out}")
    s = payload.get("stats") or {}
    if s:
        print(f"  {s.get('passed')} passed / {s.get('skipped')} skipped / {s.get('failed')} failed"
              f"（收集 {payload['total_collected']}）")
    else:
        print(f"  收集 {payload['total_collected']} 个用例（未实跑）")
    kinds: dict[str, int] = {}
    for sk in payload["skips"]:
        kinds[sk["kind"]] = kinds.get(sk["kind"], 0) + sk["count"]
    for k, v in sorted(kinds.items()):
        print(f"  skip[{k}] = {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
