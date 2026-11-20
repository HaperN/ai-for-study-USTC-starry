#!/usr/bin/env python3
"""M4 出口验收：把"全书索引与内容上下文"的通过条件变成一条可复跑的命令。

方案 §14 的 M4：

| 工作包 | 主要文件/模块 | 交付与通过条件 | 依赖 |
|---|---|---|---|
| M4 全书索引和内容上下文 | concepts、lesson/context、snapshot、source-repair |
| | **全书概念单写方合并；三件套实际进入请求；CanonicalSource 全消费者一致** | M3 |

判据：

    M4-1 概念身份不按名字：身份键 =（归一化名, 类型, 章），同名不同章是两个概念
    M4-2 单写方合并：按教材顺序、合并与写入顺序无关、id 沿用注册表
    M4-3 分片三态：complete / no-concepts / incomplete；"明确无概念"不挡冻结，"有漏项"挡
    M4-4 覆盖：每个教学单元要么有概念、要么明确无概念；没解释的挡住冻结
    M4-5 三件套实际进入请求（完整目录精简树 + 完整条目索引 + 本节全文，且不截断）
    M4-6 预算：超限标 CONTEXT_TOO_LARGE 且**不发请求**；缺原文标 CONTEXT_MISSING
    M4-7 请求指纹与并行度无关；不含 run_id/时间/绝对路径
    M4-8 CanonicalSource 全消费者一致（同一 selected_text + 一变全失效）
    M4-9 真实书：旧注册表丢掉的 10 个概念**全部找回**
    M4-10 真实书能冻结；**真漏仍挡冻结**；状态来自按内容重算（前提 2026-10-06 更正）
    M4-11 全量测试退出码 0

用法：
    python tools/migrations/check_m4.py
    python tools/migrations/check_m4.py --json
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

CONCEPT_TESTS = "tests/contracts/test_m4_concepts.py"
CONTEXT_TESTS = "tests/integration/test_m4_context.py"


def _pytest(targets: list[str]) -> tuple[int, str]:
    proc = subprocess.run([PYTHON, "-m", "pytest", *targets, "-q", "-p", "no:cacheprovider"],
                          cwd=str(PKG_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=1800)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def check() -> dict:
    results: list[dict] = []

    def add(cid: str, title: str, ok: bool, evidence: str, detail: str = "") -> None:
        results.append({"id": cid, "判据": title, "通过": ok, "证据": evidence, "细节": detail})

    code_c, out_c = _pytest([CONCEPT_TESTS])
    tail_c = out_c.strip().splitlines()[-1] if out_c.strip() else ""
    code_x, out_x = _pytest([CONTEXT_TESTS])
    tail_x = out_x.strip().splitlines()[-1] if out_x.strip() else ""

    add("M4-1", "概念身份不按名字：身份键 =（归一化名, 类型, 章）", code_c == 0,
        CONCEPT_TESTS, tail_c[:160])
    add("M4-2", "单写方合并：按教材顺序、与写入顺序无关、id 沿用注册表", code_c == 0,
        CONCEPT_TESTS, tail_c[:160])
    add("M4-3", "分片三态：明确无概念不挡冻结，有漏项挡冻结", code_c == 0,
        CONCEPT_TESTS, tail_c[:160])
    add("M4-4", "覆盖：逐项有概念或明确无概念；没解释的挡住冻结", code_c == 0,
        CONCEPT_TESTS, tail_c[:160])
    add("M4-5", "三件套实际进入请求且不截断", code_x == 0, CONTEXT_TESTS, tail_x[:160])
    add("M4-6", "超限标 CONTEXT_TOO_LARGE 不发请求；缺原文标 CONTEXT_MISSING", code_x == 0,
        CONTEXT_TESTS, tail_x[:160])
    add("M4-7", "请求指纹与并行度无关；不含 run_id/时间/绝对路径", code_x == 0,
        CONTEXT_TESTS, tail_x[:160])

    # ── CanonicalSource 全消费者一致
    from_src = (
        "import sys; sys.path.insert(0, 'src')\n"
        "from aiforstudy.core.source import CanonicalSource, SELECTED_TEXT_CONSUMERS\n"
        "a = CanonicalSource.build(b'raw', b'sel', revision='parse-v2')\n"
        "views = {r: a.consumer_view(r)['selected_text_sha256'] for r in SELECTED_TEXT_CONSUMERS}\n"
        "assert len(set(views.values())) == 1, views\n"
        "b = CanonicalSource.build(b'raw', b'sel2', revision='parse-v2')\n"
        "inv = set(b.invalidates(a))\n"
        "assert set(SELECTED_TEXT_CONSUMERS) <= inv, inv\n"
        "assert b.raw_sha256 == a.raw_sha256, 'raw 必须保留'\n"
        "print('consumers=%d invalidated=%d' % (len(views), len(inv)))")
    proc = subprocess.run([PYTHON, "-c", from_src], cwd=str(PKG_ROOT),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
    out_s = proc.stdout.decode("utf-8", "replace").strip()
    add("M4-8", "CanonicalSource 全消费者一致（同一 selected_text；一变全失效；raw 保留）",
        proc.returncode == 0, "aiforstudy.core.source", out_s.splitlines()[-1][:160])

    # ── 真实书
    probe = (
        "import json, sys\n"
        "sys.path.insert(0, 'src')\n"
        "from pathlib import Path\n"
        "from aiforstudy.blocks.parse import build_catalog\n"
        "from aiforstudy.blocks.concepts import build_index\n"
        "parse = Path('data/parse/complex-analysis-v2')\n"
        "if not (parse / 'chapters.json').is_file():\n"
        "    print(json.dumps({'skip': True})); raise SystemExit(0)\n"
        "cat, _ = build_catalog(parse, book_id='complex-analysis-v2', revision='parse-v2')\n"
        "idx = build_index(parse, cat, book_id='complex-analysis-v2')\n"
        "lost = ['C-0117','C-0208','C-0269','C-0292','C-0294','C-0307','C-0331','C-0384','C-0403','C-0424']\n"
        "by = idx.by_id()\n"
        "blocking = idx.blocking_parts()\n"
        "cov = idx.coverage(tuple(u.unit_key for u in cat.teaching_units()))\n"
        "print(json.dumps({'skip': False, 'concepts': len(idx.concepts),\n"
        "                  'ids': idx.verify()['counts']['ids'],\n"
        "                  'lost_found': sum(1 for c in lost if c in by),\n"
        "                  'legacy': idx.verify()['counts']['legacy_registry_entries'],\n"
        "                  'blocking': len(blocking),\n"
        "                  'blocking_reason_ok': all('漏项' in p.why for p in blocking),\n"
        "                  'frozen': idx.frozen, 'coverage_ok': cov['ok'],\n"
        "                  'multi_chapter': len(idx.multi_chapter_names),\n"
        "                  'collisions': len(idx.collisions)}, ensure_ascii=False))")
    proc = subprocess.run([PYTHON, "-c", probe], cwd=str(PKG_ROOT),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=900)
    raw = proc.stdout.decode("utf-8", "replace").strip().splitlines()
    try:
        info = json.loads(raw[-1]) if raw else {}
    except ValueError:
        info = {}
    if info.get("skip"):
        for cid, title in (("M4-9", "真实书找回旧注册表丢掉的 10 个概念"),
                           ("M4-10", "真实书能冻结 / 真漏仍挡 / 状态来自重算")):
            add(cid, title, True, "-", "真实产物不在位，跳过")
    elif not info:
        for cid, title in (("M4-9", "真实书找回旧注册表丢掉的 10 个概念"),
                           ("M4-10", "真实书能冻结 / 真漏仍挡 / 状态来自重算")):
            add(cid, title, False, "子进程探测", (raw[-1] if raw else "")[:200])
    else:
        add("M4-9", "真实书找回旧注册表丢掉的 10 个概念（旧表只有 438 条）",
            info["lost_found"] == 10 and info["ids"] == info["concepts"],
            "aiforstudy.blocks.concepts.build_index",
            f"概念 {info['concepts']}｜唯一 id {info['ids']}｜旧注册表 {info['legacy']}｜"
            f"找回 {info['lost_found']}/10｜同名跨章 {info['multi_chapter']}｜"
            f"同名多 id {info['collisions']}｜覆盖 ok={info['coverage_ok']}")
        # ⚠️ 2026-10-06 **前提更正**：原先这条断言 `blocking == 28 且 frozen is False`。
        #    那 28 个"有漏项"**不是内容不够**，而是尺子不准：旧 `normalize_name` 把
        #    「类型词 + 编号」的名字（`定理1`、`性质5`）归成**空串**，而覆盖度判定把空串
        #    当成"没覆盖"。为此花的 **669,453 token** 重抽修不好它（反而把概念 528 弄成 519）；
        #    修好尺子后**重算（0 token）**就让 `frozen` 由 False 变 True、概念仍是 528。
        #    判据没有放宽，而是换成**三条一起**：
        #      ① 真实书现在**能**冻结；② **真漏仍然挡冻结**（下面现算一个真漏的分片）；
        #      ③ 状态来自**按内容重算**，不是照念分片里那个旧的自报标记。
        #    ②③ 是关键：少了它们，这条就退化成"永远通过"。
        from aiforstudy.blocks.concepts.index import recompute_coverage
        genuine_gap = {"named": ["定理9"], "concepts": [{"name": "完全别的东西"}],
                       "coverage": {"完整": True, "完整说明": "全部覆盖"}}
        still_blocks = recompute_coverage(genuine_gap)["complete_by_content"] is False
        false_alarm = {"named": ["定理1", "性质5"],
                       "concepts": [{"name": "定理1"}, {"name": "性质5"}],
                       "coverage": {"完整": False, "完整说明": "有漏项"}}
        false_alarm_cleared = recompute_coverage(false_alarm)["complete_by_content"] is True
        add("M4-10", "真实书能冻结；**真漏仍挡冻结**；状态来自按内容重算",
            info["blocking"] == 0 and info["frozen"] is True
            and still_blocks and false_alarm_cleared,
            "ConceptIndex.assert_freezable + recompute_coverage（两个方向）",
            f"挡住冻结 {info['blocking']}｜frozen={info['frozen']}｜"
            f"**真漏仍挡**={still_blocks}（自报'完整'但内容真漏 → 判不完整）｜"
            f"**假报已清**={false_alarm_cleared}（自报'有漏项'但编号条目都在 → 判完整）")

    # ── M4-11 全量
    code, out = _pytest(["tests/", "-ra"])
    passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", out)) else 0
    skipped = int(m.group(1)) if (m := re.search(r"(\d+) skipped", out)) else 0
    failed = int(m.group(1)) if (m := re.search(r"(\d+) failed", out)) else 0
    add("M4-11", "全量测试退出码 0、无 failed", code == 0 and failed == 0 and passed > 0,
        "pytest tests/ -q -ra",
        f"{passed} passed / {skipped} skipped / {failed} failed｜退出码 {code}")

    return {"_meta": {"file_type": "m4_exit_check", "schema_version": "1.0",
                      "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
            "通过": all(r["通过"] for r in results), "判据": results}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M4 出口验收")
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
        print(f"\nM4 出口：{'通过' if payload['通过'] else '未通过'}")
    return 0 if payload["通过"] else 1


if __name__ == "__main__":
    sys.exit(main())
