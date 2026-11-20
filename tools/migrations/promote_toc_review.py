#!/usr/bin/env python3
"""把目录复核的**建议稿**提升为**可应用的复核文件** `toc.review.json`。

## 为什么需要这个命令

`review_toc(...)` 产出的是建议稿（`inputs_digest = None`），而 `apply_review` 要求
输入指纹一致，否则一律按"不可消费旧决策"（§15 A08）拒绝。中间没有桥，
于是真实书上那两条**已经被视觉复核裁决过**的标题分歧，每次运行都重报一遍"未裁决"
—— CH04 那一章 32 条 `needs_review` 里，**22 条**就是这么来的。

桥本身（`review_from_draft`）已经在包里；这个命令是它的**显式使用点**。

## 为什么默认 dry-run

写 `toc.review.json` 是**让一次判断长期生效**的动作：落盘之后，后续每一次运行都会
把它当成权威复核去应用。所以：

* **默认只打印**将要写入什么（含裁决条数、指纹）；
* 只有显式 `--apply` 才真写；
* 写之前**先跑一遍 `apply_review`**，应用不了就不写 —— 免得落一个必然被拒的文件。

用法：
    python tools/migrations/promote_toc_review.py --run <run_id>            # 只看
    python tools/migrations/promote_toc_review.py --run <run_id> --apply     # 真写
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent.parent
sys.path.insert(0, str(PKG_ROOT / "src"))

from aiforstudy.blocks.parse.review import (apply_review, expected_inputs_digest,  # noqa: E402
                                            load_review, review_from_draft)
from aiforstudy.core.jsonio import read_json, write_json_atomic                    # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="把复核建议稿提升为可应用的 toc.review.json")
    ap.add_argument("--book", default="complex-analysis-v2")
    ap.add_argument("--run", required=True, help="含 toc.review.suggested.json 的 run 目录名")
    ap.add_argument("--apply", action="store_true", help="真写（默认只打印）")
    ap.add_argument("--arbiter", default="model", choices=["model", "human"])
    args = ap.parse_args(argv)

    parse_root = PKG_ROOT / "data" / "parse" / args.book
    runs_root = PKG_ROOT / "data" / "books" / args.book / "runs"
    draft_path = runs_root / args.run / "toc.review.suggested.json"
    if not draft_path.is_file():
        raise SystemExit(f"找不到建议稿：{draft_path}")
    toc = read_json(parse_root / "toc.v2.json")
    draft = read_json(draft_path)
    disagreements = list(toc.get("disagreements") or [])

    review = review_from_draft(draft, parse_root, disagreements=disagreements,
                               arbiter=args.arbiter,
                               produced_by=f"{args.run}#{Path(draft_path).name}")
    expected = expected_inputs_digest(parse_root)

    # 落盘之前先确认它**应用得了** —— 否则写下一个必然被拒的文件，只会让人以为"复核有了"
    application = apply_review(review, expected_inputs_digest=expected,
                               disagreements=disagreements)
    target = parse_root / "toc.review.json"
    summary = {
        "建议稿": str(draft_path.relative_to(PKG_ROOT)),
        "目标": str(target.relative_to(PKG_ROOT)),
        "裁决条数": len(review.decisions),
        "逐条": [{"verdict": d.verdict, "subject": d.subject, "title": d.title,
                  "reason": d.reason[:80]} for d in review.decisions],
        "输入指纹": review.inputs_digest,
        "与当前输入一致": review.inputs_digest == expected,
        "预演应用结果": {"applied": len(application.applied),
                         "rejected": len(application.rejected),
                         "pending": len(application.pending),
                         "stale": application.stale},
        "已存在的复核": str(target) if target.is_file() else None,
        "动作": "写入" if args.apply else "**未写入**（dry-run：加 --apply 才真写）",
    }
    if application.stale or application.rejected:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        raise SystemExit("预演就没通过（stale 或有 rejected）—— 不写。先修好再提升。")

    if args.apply:
        if target.is_file():
            backup = target.with_suffix(f".json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
            shutil.copy2(target, backup)
            summary["原文件已备份为"] = str(backup.relative_to(PKG_ROOT))
        write_json_atomic(target, review.as_dict())
        # 立刻用 `load_review` 读回来核一遍（写进去 ≠ 读得出来）
        reread = load_review(parse_root)
        summary["写回后读得出来"] = bool(reread and len(reread.decisions) == len(review.decisions))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
