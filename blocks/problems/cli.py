"""问题汇总块：命令行（跑批与交付包都用它，不再各写一套）。

    # 汇总一份 problems.json（0 token，只读产物）
    ../.venv-linux/bin/python -m blocks.problems.cli \
        --lessons data/lesson/linear-algebra-full \
        --delivery delivery/第二章-全章 --delivery delivery/第五章-两节 \
        --audit data/audit/linear-algebra-full \
        --out data/problems/linear-algebra-full.json

    # 声明复核：把交付包的每个数字重新从产物算一遍，对不上非 0 退出
    ../.venv-linux/bin/python -m blocks.problems.cli --check \
        --problems data/problems/linear-algebra-full.json ...

    # 只看一眼（人读的摘要）
    ../.venv-linux/bin/python -m blocks.problems.cli --show \
        --problems data/problems/linear-algebra-full.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "lib"))

import collect as collector                                    # noqa: E402
from files import write_json                                   # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="问题汇总（缺口 B）：各块产物 → 一份 problems.json")
    ap.add_argument("--lessons", help="教案目录（data/lesson/<书名>）")
    ap.add_argument("--delivery", action="append", default=[],
                    help="交付网页目录（可给多次，读它的 render.index.json）")
    ap.add_argument("--audit", help="审核目录（data/audit/<书名>）")
    ap.add_argument("--out", help="problems.json 写到哪")
    ap.add_argument("--problems", help="已存在的 problems.json（--check / --show 用）")
    ap.add_argument("--check", action="store_true", help="把声明里的数字重新算一遍，对不上非 0 退出")
    ap.add_argument("--hide-cost", action="store_true",
                    help="不把「成本」写进声明（2026-09-27 用户要求：交付物里不出现耗时/花费；"
                         "账目本身仍留在 metrics 里供内部对账）")
    ap.add_argument("--show", action="store_true", help="打印人读摘要")
    args = ap.parse_args(argv)

    if args.show:
        payload = json.loads(Path(args.problems).read_text(encoding="utf-8"))
        print("── 声明（每个数字都能指回产物）")
        for row in payload.get("claims") or []:
            print("  *", row["说"])
        print(f"\n── 硬问题 {len(payload.get('problems') or [])} 条")
        for row in (payload.get("problems") or [])[:20]:
            print(f"  [{row.get('severity')}] {row.get('unit') or '-'} "
                  f"{str(row.get('message'))[:90]}")
        dropped = payload.get("dropped") or []
        print(f"\n── 不进交付包的节 {len(dropped)} 个（原因 + 烧掉的 token）")
        for row in dropped:
            print(f"  {row['unit']}：{str(row.get('原因'))[:70]}"
                  f"｜{row.get('token', 0)/10000:.1f} 万 token")
        print(f"\n── 记录性说明 {len(payload.get('notes') or [])} 条（不算问题）")
        return 0

    if not args.lessons:
        ap.error("至少要给 --lessons")

    if args.check:
        if not args.problems:
            ap.error("--check 要配合 --problems")
        payload = json.loads(Path(args.problems).read_text(encoding="utf-8"))
        mismatches = collector.verify(payload, lessons_dir=Path(args.lessons),
                                      delivery_dirs=[Path(p) for p in args.delivery],
                                      audit_dir=Path(args.audit) if args.audit else None)
        print(json.dumps({"声明复核": "通过" if not mismatches else "对不上",
                          "对不上的": mismatches}, ensure_ascii=False, indent=2))
        return 0 if not mismatches else 1

    payload = collector.collect(include_cost=not args.hide_cost,
                                lessons_dir=Path(args.lessons),
                                delivery_dirs=[Path(p) for p in args.delivery],
                                audit_dir=Path(args.audit) if args.audit else None)
    if args.out:
        write_json(Path(args.out), payload)
        print(f"写到 {args.out}：硬问题 {len(payload['problems'])} 条、"
              f"说明 {len(payload['notes'])} 条、不进交付包的节 {len(payload['dropped'])} 个、"
              f"声明 {len(payload['claims'])} 条")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
