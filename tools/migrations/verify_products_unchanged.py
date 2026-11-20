#!/usr/bin/env python3
"""H 约束核对：**跑完一个里程碑之后，产物到底有没有被动过**。

方案的硬约束里有两条是"不许动坏生产产物 / 不删原始材料与付费产物"。
这类约束的特点是：**违反时不会报错**。写坏一个教案 JSON、少一个概念分片、
图被重画一次——流水线照跑，退出码照样 0，直到读者打开页面才看出来。

所以每个里程碑收口时做一次内容哈希比对：拿"开工前"的现场快照与"现在"比，
逐文件比 sha256，并**按产物范围**分类。默认只关心产物（解析/教案/审核/图/交付），
因为它们是不可重建或很贵才能重建的那一类；代码/文档的变化是本次工作本身，不算违规。

用法：
    python tools/migrations/verify_products_unchanged.py \\
        --before data/_migration/scene-snapshot-20261005-194032.json \\
        --after  data/_migration/scene-M2-出口.json

    加 --all  连代码与文档一起比（用于"这次改动到底碰了哪些文件"）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent.parent
REPO_ROOT = PKG_ROOT.parent

#: 产物范围（相对仓库根）。这些是"不许动坏"的对象。
PRODUCT_PREFIXES = (
    "AIforStudy/data/parse/",
    "AIforStudy/data/lesson/",
    "AIforStudy/data/audit/",
    "AIforStudy/data/problems/",
    "AIforStudy/data/raw/",
    "AIforStudy/data/external/",
    "AIforStudy/data/package/",
    "AIforStudy/delivery/",
    "AIforStudy/figures/",
)

#: 明确不算产物（本次工作会正常修改它们）
NON_PRODUCT_PREFIXES = (
    "AIforStudy/data/_migration/",
    "AIforStudy/data/books/",          # 运行记录（M2 起的新布局），属运行时数据
    "AIforStudy/.cache/",
)


def _is_product(rel: str) -> bool:
    if any(rel.startswith(p) for p in NON_PRODUCT_PREFIXES):
        return False
    return any(rel.startswith(p) for p in PRODUCT_PREFIXES)


def _entries(payload: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for key in ("modified_or_untracked", "ignored_but_keep", "tracked_clean"):
        for entry in payload.get(key) or []:
            if entry.get("path") and entry.get("sha256"):
                out[entry["path"]] = entry
    return out


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def compare(before_path: Path, after_path: Path, *, scope: str = "product") -> dict:
    before = json.loads(before_path.read_text(encoding="utf-8"))
    after = json.loads(after_path.read_text(encoding="utf-8"))
    b, a = _entries(before), _entries(after)

    def wanted(rel: str) -> bool:
        if scope == "all":
            return True
        if scope == "watch":
            return _is_product(rel) or rel.startswith("AIforStudy/data/")
        return _is_product(rel)

    only_before = sorted(r for r in b if r not in a and wanted(r))
    only_after = sorted(r for r in a if r not in b and wanted(r))
    changed = sorted(r for r in b if r in a and b[r]["sha256"] != a[r]["sha256"] and wanted(r))
    common = sum(1 for r in b if r in a and wanted(r))

    # 盘上再核一次"现在"的哈希（快照可能已经过时）
    on_disk_mismatch: list[str] = []
    for rel, entry in a.items():
        if not wanted(rel):
            continue
        path = REPO_ROOT / rel
        if not path.is_file():
            continue
        if _hash(path) != entry["sha256"]:
            on_disk_mismatch.append(rel)

    return {
        "before": str(before_path), "after": str(after_path), "scope": scope,
        "coverage_note": (
            "⚠️ 覆盖范围取决于快照：M0 的 `snapshot_scene.py` 只记 modified/untracked/"
            "ignored-keep 三类，**不含已跟踪且干净的文件**；该栏是后来补的（见 snapshot_scene "
            "的注释）。所以拿旧快照比对新快照时，`only_after` 里可能有大量"
            "「其实是 tracked-clean、从来没变过」的文件。"),
        "compared": common,
        "changed": changed, "deleted_from_before": only_before, "added": only_after,
        "on_disk_mismatch": on_disk_mismatch,
        "通过": not changed and not only_before and not on_disk_mismatch,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="H 约束核对：产物有没有被动过（只读）")
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--scope", choices=["product", "watch", "all"], default="product")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--allow-added", action="store_true",
                    help="不算'新增'为失败（新增通常是合法的：新书、新图）")
    args = ap.parse_args(argv)

    payload = compare(Path(args.before), Path(args.after), scope=args.scope)
    ok = payload["通过"] or (args.allow_added and not payload["changed"]
                             and not payload["deleted_from_before"]
                             and not payload["on_disk_mismatch"])
    payload["结论"] = "产物未被改动" if ok else "**发现产物被改动**"

    if args.json:
        print(json.dumps({**payload, "通过": ok}, ensure_ascii=False, indent=2))
    else:
        print(f"比对范围：{args.scope}｜共同文件 {payload['compared']}")
        print(f"内容被改：{len(payload['changed'])}")
        for rel in payload["changed"][:30]:
            print(f"   M {rel}")
        print(f"被删除：{len(payload['deleted_from_before'])}")
        for rel in payload["deleted_from_before"][:30]:
            print(f"   D {rel}")
        print(f"新增：{len(payload['added'])}")
        for rel in payload["added"][:30]:
            print(f"   + {rel}")
        print(f"盘上与快照对不上：{len(payload['on_disk_mismatch'])}")
        print(f"\n{payload['结论']}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
