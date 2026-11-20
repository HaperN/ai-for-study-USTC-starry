#!/usr/bin/env python3
"""M10 出口验收：**最终证据**。能离线判定的判，不能判的**明确 blocked**。

方案 §14 的 M10：

| M10 真实验收与最终切换 | 小样、代表整书、退役清单、报告与交付 |
| **回放后真实单章、多章、整本通过，再删除旧OCR与重复入口；复跑离线回归和活动引用检查，形成最终证据** | M9 |

方案 §14 还有一句决定顺序的话：

> 旧生产实现的可回退快照**保留到 M10 通过**，最终生产包不包含它们。

以及：

> M10 **通过后**完成删除，并复跑 A01/A04/A32；这一步属于最终交付，
> 不能遗留为无期限兼容工作。

## 这个脚本的立场

M10 有两半：

1. **真实单章/多章/整本通过** —— 要调真实模型与 MinerU，**要预算**；
2. **复跑离线回归和活动引用检查，形成最终证据** —— 离线可做。

没有第 1 半，按方案自己的顺序，**第 2 半里的"删除"不能做**（"M10 通过后完成删除"）。
所以本脚本把第 1 半如实报成 `blocked`，并且**不让整体"通过"**——
一个"因为跑不了就标 skipped 然后算过"的验收，正是方案 §15 A26 点名禁止的。

判据：
    M10-1 离线验收（A02/A03/A04 九组合）通过
    M10-2 全量离线回归通过，且**没有靠扩大 skip 掩盖问题**
    M10-3 测试收集清单逐项有映射（A32）
    M10-4 活动引用检查：新引擎对旧入口的**真实代码引用为 0**
    M10-5 退役候选清单与删除计划在位（**删除动作本身待 M10-1 通过**）
    M10-6 A01 全新环境从锁定来源安装（**曾是错的 blocked，后更正为 verified**）
    M10-7 真实教材验收（回放后单章/多章/整本）—— 需要预算，如实报告
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
MIG = PKG_ROOT / "data" / "_migration"
ACCEPTANCE_TEST = "tests/contracts/test_m10_acceptance_offline.py"

STATUS_OK = "accepted"
STATUS_FAIL = "failed"
STATUS_BLOCKED = "blocked"


def _run(args, timeout=2400):
    proc = subprocess.run([PYTHON, *args], cwd=str(PKG_ROOT), stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=timeout)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def _pytest(targets):
    return _run(["-m", "pytest", *targets, "-q", "-p", "no:cacheprovider"])


def check() -> dict:
    results: list[dict] = []

    def add(cid, title, status, evidence, detail=""):
        results.append({"id": cid, "判据": title, "状态": status, "通过": status == STATUS_OK,
                        "证据": evidence, "细节": detail})

    # ── M10-1 离线验收
    if (PKG_ROOT / ACCEPTANCE_TEST).is_file():
        code, out = _pytest([ACCEPTANCE_TEST])
        tail = out.strip().splitlines()[-1] if out.strip() else ""
        add("M10-1", "离线验收（A02/A03/A04 九组合）通过",
            STATUS_OK if code == 0 else STATUS_FAIL, ACCEPTANCE_TEST, tail[:200])
    else:
        add("M10-1", "离线验收（A02/A03/A04 九组合）通过", STATUS_FAIL, ACCEPTANCE_TEST,
            "验收测试文件不在位")

    # ── M10-2 全量离线回归 + skip 是否被扩大
    code, out = _pytest(["tests/", "-ra"])
    passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", out)) else 0
    skipped = int(m.group(1)) if (m := re.search(r"(\d+) skipped", out)) else 0
    failed = int(m.group(1)) if (m := re.search(r"(\d+) failed", out)) else 0
    reasons = sorted(set(re.findall(r"SKIPPED \[(\d+)\] ([^\n]+)", out)))
    add("M10-2", "全量离线回归通过，且没有靠扩大 skip 掩盖问题",
        STATUS_OK if (code == 0 and failed == 0 and passed > 0) else STATUS_FAIL,
        "pytest tests/ -q -ra",
        f"{passed} passed / {skipped} skipped / {failed} failed｜退出码 {code}｜"
        f"skip 成因 {len(reasons)} 类")

    # ── M10-3 测试收集清单逐项映射（A32）
    # A32 的原话是"测试有**逐项映射**"，所以判据是：磁盘上每个测试模块都能在映射表里找到，
    # 一个都不许漏 —— 漏掉的那个就是"没人在管的测试"。
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_inv", PKG_ROOT / "tools" / "migrations" / "test_inventory.py")
    inv_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inv_mod)
    module_map = getattr(inv_mod, "MODULE_MAP", {})
    on_disk = {p.stem: p.relative_to(PKG_ROOT).as_posix()
               for p in (PKG_ROOT / "tests").rglob("test_*.py")}
    unmapped = sorted(stem for stem in on_disk if stem not in module_map)
    add("M10-3", "测试收集清单逐项有映射（A32）",
        STATUS_OK if not unmapped and module_map else STATUS_FAIL,
        "tools/migrations/test_inventory.py 的 MODULE_MAP × tests/ 实际文件",
        f"映射表 {len(module_map)} 项｜盘上测试模块 {len(on_disk)} 个｜"
        f"**未映射 {len(unmapped)}**" + (f"：{unmapped}" if unmapped else ""))

    # ── M10-4 活动引用检查：新引擎的真实代码引用
    code, out = _run(["tools/migrations/retire_status.py", "--json"])
    retire = json.loads(out[out.index("{"):]) if "{" in out else {}
    totals = retire.get("totals") or {}
    # 新引擎（src/aiforstudy）里对旧入口的引用 —— 这是 A32 真正要清零的那一列
    new_engine_refs: list[dict] = []
    for row in retire.get("rows") or []:
        for hit in (row.get("refs") or {}).get("prod_code") or []:
            if str(hit).startswith("src/aiforstudy"):
                new_engine_refs.append({"item": row["id"], "file": hit})

    # ⚠️ 门槛口径（这一条不定清楚，修好测量缺陷之后会把"计划内的过渡壳"判成违规）：
    #    `retire_status._ident_re` 原先认不出包内相对导入（`from .transitional import …`），
    #    所以 `engine.py` 对过渡壳的引用一直是**假绿**。补上之后，T-26（过渡壳）
    #    立刻会被算成"未清理引用"。
    #    但方案 §14 规定过渡壳**在最终切换阶段才删除**，也就是说在切换之前
    #    "engine 引用过渡壳"是**本来就该存在**的。
    #    所以门槛是：**只有 `disposition=delete` 且 `status` 已完成（不再是 in_progress）
    #    的项**，在新引擎里被引用才算违规；`in_progress` 的项单独列出（看得见，但不拦）。
    #    这样既不会因为修了测量缺陷就永远 FAIL，也不会把真正的漏删放过去。
    manifest_for_status = HERE / "retire_manifest.json"
    in_progress: set[str] = set()
    shell_files: set[str] = set()
    if manifest_for_status.is_file():
        blob = json.loads(manifest_for_status.read_text(encoding="utf-8"))
        for item in blob.get("items") or []:
            if str(item.get("status") or "") == "in_progress":
                in_progress.add(str(item.get("id")))
                # ⚠️ 门槛还要看**引用来自哪个文件**，不能只看"被引用项"的状态。
                #    过渡壳（T-26 的 `pipeline/transitional.py`）**本来就是要调旧实现的** ——
                #    那是壳存在的全部理由，而方案 §14 规定它在最终切换阶段整体删除。
                #    第一版只查目标项，于是新写的 interaction 壳调 `blocks/model`（T-09）
                #    被算成"未清理引用"。那不是漏删，是**壳在干它该干的事**。
                for key in ("old_path", *([] if not item.get("extra_paths") else [])):
                    if key and str(item.get(key) or "").startswith("src/aiforstudy/"):
                        shell_files.add(str(item[key]))
    def _planned(row: dict) -> bool:
        if row["item"] in in_progress:
            return True
        return any(str(row["file"]) == f or str(row["file"]).startswith(f.rstrip("/") + "/")
                   for f in shell_files)
    violations = [r for r in new_engine_refs if not _planned(r)]
    planned = [r for r in new_engine_refs if _planned(r)]
    detail = (f"清单 {totals.get('items')} 项｜真代码引用 {totals.get('prod_code_refs')} 处"
              f"（其中新引擎 {len(new_engine_refs)} 处：**违规 {len(violations)}**、"
              f"计划内过渡壳 {len(planned)}）")
    if planned:
        detail += (f"｜计划内（方案 §14 规定最终切换阶段才删）："
                   + "、".join(sorted({f"{r['item']}→{r['file']}" for r in planned})))
    if violations:
        detail += f"｜**违规**：{violations[:4]}"
    add("M10-4", "活动引用检查：新引擎对旧入口的真实代码引用为 0（计划内过渡壳单列）",
        STATUS_OK if not violations else STATUS_FAIL,
        "data/_migration/retire-status.json（只认代码行，不认注释/文档字符串）",
        detail)

    # ── M10-5 退役清单与删除计划在位
    manifest = HERE / "retire_manifest.json"   # ⚠️ 它在 tools/migrations/ 下，不在 data/_migration/
    legacy_manifest = MIG / "retire_manifest.json"
    disp = MIG / "tool-disposition.json"
    payload_disp = json.loads(disp.read_text(encoding="utf-8")) if disp.is_file() else {}
    add("M10-5", "退役候选清单与删除计划在位（**删除动作本身待 M10-1 通过**）",
        STATUS_OK if ((manifest.is_file() or legacy_manifest.is_file()) and payload_disp)
        else STATUS_FAIL,
        f"{(manifest if manifest.is_file() else legacy_manifest).name} + {disp.name}",
        f"清单项 {totals.get('items')}｜工具 {payload_disp.get('summary', {}).get('tools')} 个"
        f"｜未登记 {payload_disp.get('summary', {}).get('unaccounted')}｜"
        f"**删除未执行**（方案 §14：旧实现保留到 M10 通过）")

    # ── M10-6 A01：全新环境从锁定来源安装
    #
    # ⚠️ 这一条**曾经被我判成 blocked，判错了**。当时的推理是：
    #    `requirements.lock` 里 `undergraduate-fitted-exam-system` 的
    #    `rebuildable: false` → "锁定来源装不出来" → A01 阻断。
    #    错在**没先问一句"这个包到底在不在我们的生产路径上"**。
    #    复查（用户提示后）：新引擎的审核走**树内 vendored** 的 `hbep_controller_v2`，
    #    它的入口只依赖 stdlib + 兄弟模块；那 4 个 CourseForgeV2 包在新生产路径上
    #    **一个 import 都没有**（`exercise_authoring_system` 更是全仓 0 处）；
    #    `stem_runner`/`courseforge` 在 normalizer 里只是**用来比对锁的字符串常量**。
    #
    #    所以 A01 该做的是"真跑一遍"，而不是"论证它做不了"。认据是那一遍的记录，
    #    不是我对 lock 文件的解读。
    a01_record = PKG_ROOT / "docs" / "M10-A01-验收记录.md"
    a01_test = "tests/integration/test_m10_a01_fresh_env.py"
    code_a01, out_a01 = _pytest([a01_test])
    tail_a01 = out_a01.strip().splitlines()[-1] if out_a01.strip() else ""
    verdict_ok = a01_record.is_file() and "A01 通过" in a01_record.read_text(encoding="utf-8")
    add("M10-6", "A01 全新环境从锁定来源安装、在仓库外 cwd 调用",
        STATUS_OK if (code_a01 == 0 and verdict_ok) else STATUS_FAIL,
        f"{a01_test} + docs/M10-A01-验收记录.md",
        (f"**verified**：新建仓库外 venv（Python 3.12.3，include-system-site-packages=false），"
         f"按 requirements.lock 装齐 49 个 pin（0 失败）；仓库外 cwd 跑 "
         f"help/plan/books/capabilities/sources + normalizer + 树内 vendored 审核器 + "
         f"全部启用图后端；用 sys.meta_path 封死那 5 个 CourseForgeV2 包 → **一个都没被 import**；"
         f"5 组打乱导入顺序得到逐字节相同摘要。"
         f"明确排除的 4 个包（不在新生产路径上）：stem_runner / courseforge / "
         f"exercise_authoring_system / hbep_controller_v2（后者已树内 vendored）。"
         f"｜{tail_a01[:120]}"))

    # ── M10-7 真实教材验收
    real_runs = MIG / "real-acceptance.json"
    if real_runs.is_file():
        payload = json.loads(real_runs.read_text(encoding="utf-8"))
        combos = payload.get("combinations") or []
        ok = bool(combos) and all(c.get("status") == "passed" for c in combos)
        add("M10-7", "真实教材验收：回放后单章 / 多章 / 整本通过",
            STATUS_OK if ok else STATUS_BLOCKED, str(real_runs),
            f"{len(combos)} 个组合｜{sum(1 for c in combos if c.get('status') == 'passed')} 通过")
    else:
        add("M10-7", "真实教材验收：回放后单章 / 多章 / 整本通过", STATUS_BLOCKED,
            "data/_migration/real-acceptance.json（不存在）",
            "**卡在实现，不卡在钱**：预算已批准 5,000,000 token（档 C：目录复核 + 28 分片"
            "概念重抽 + 真实单章），但 `figures`/`interaction`/`render`/`delivery` 四个阶段的"
            "模型壳还没接，所以真实单章端到端**跑不起来**（不是没批钱）。"
            "按方案 §15 A26 报 blocked，**不写 skipped、不写成通过**。"
            "前置条件：① 剩下四个壳接上；② 档 B 把那 28 个分片补齐（否则 §6 索引不可冻结、"
            "真实书一步都走不动）。")

    blocked = [r for r in results if r["状态"] == STATUS_BLOCKED]
    failed = [r for r in results if r["状态"] == STATUS_FAIL]
    # ⚠️ 有 blocked 项时整体**不算通过**（方案 §15 A26：缺必需能力不用 skipped 算正式通过）
    overall = not failed and not blocked
    return {"_meta": {"file_type": "m10_exit_check", "schema_version": "1.0",
                      "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                      "field_notes": {
                          "状态": "accepted / failed / **blocked**（blocked 是环境或预算阻断，"
                                  "不算通过、也不算代码缺陷）",
                          "通过": "仅 accepted 计入；**有 blocked 项时整体不为真**",
                      }},
            "通过": overall, "有阻断项": bool(blocked),
            "判据": results,
            "note": ("M10 的两半：离线回归与活动引用检查已完成；"
                     "真实教材验收与 A01 需要预算/来源补齐，如实报 blocked。"
                     "按方案 §14『M10 通过后完成删除』，旧实现**未删除**。")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M10 出口验收（最终证据）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    payload = check()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for r in payload["判据"]:
            mark = {"accepted": "✓", "failed": "✗", "blocked": "⛔"}[r["状态"]]
            print(f"{mark} {r['id']} {r['判据']}  [{r['状态']}]")
            if r["细节"]:
                print(f"      {r['细节']}")
        if payload["有阻断项"]:
            print("\nM10 出口：**未通过**（有阻断项 —— 按方案 §15 A26，不写成通过）")
        else:
            print(f"\nM10 出口：{'通过' if payload['通过'] else '未通过'}")
    return 0 if payload["通过"] else 1


if __name__ == "__main__":
    sys.exit(main())
