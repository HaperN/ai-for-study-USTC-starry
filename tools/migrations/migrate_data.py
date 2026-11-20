#!/usr/bin/env python3
"""M9 数据迁移演练：**只读导入**存量产物到新布局，并给出逐项迁移报告。

方案 §14 的 M9：

| M9 存量迁移演练 | migration 工具、35工具去向、README/阅读顺序/契约 |
| **数据只读导入与迁移报告；完成旧入口退役候选清单，保留可回退版本** | M3、M7、M8 |

方案 §10 的目标布局：

```text
data/books/<book_id>/
  source/                         原始输入与 MinerU 来源
  parse/<parse_revision>/          不可变解析快照与 catalog
  artifacts/<stage>/<digest>/      校验后的阶段产物
  runs/<run_id>/                   request、plan、state、events、usage、reports
  releases/<release_id>/           不可变范围交付与验收清单
  current.json                    原子更新的当前版本引用
```

## 这份工具的纪律

1. **只读旧产物**。它只往 `data/books/` 下写，`data/parse|lesson|audit` 一个字节都不动；
   跑完用 `verify_products_unchanged.py` 核对（这是硬约束的判据，不是承诺）。
2. **每个条目都要有去向**。迁移报告里逐项写"迁到哪、为什么"，或者"明确排除 + 理由"。
   方案 M9 的要求是"数据只读导入与迁移报告"，而报告最容易退化成一句"迁移完成"——
   所以这里输出的是**逐项账**。
3. **不伪造旧数据里没有的 v2 字段**。缺就写缺，并在报告里列出（方案 §13 最后一行）。
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
sys.path.insert(0, str(PKG_ROOT / "src"))

from aiforstudy.blocks.concepts import build_index                     # noqa: E402
from aiforstudy.blocks.parse import build_catalog                      # noqa: E402
from aiforstudy.blocks.parse.catalog import verify_references          # noqa: E402
from aiforstudy.core.digest import digest_file, digest_of              # noqa: E402
from aiforstudy.core.jsonio import read_json_soft, write_json_atomic   # noqa: E402
from aiforstudy.core.registry import load_registry                     # noqa: E402
from aiforstudy.core.source import CanonicalSource                     # noqa: E402
from aiforstudy.core.workspace import Workspace                        # noqa: E402
from aiforstudy.delivery.resources import ResourceManifest             # noqa: E402

#: 会被搬进新布局的阶段（对应 §10 的 `artifacts/<stage>/<digest>/`）
STAGES = ("catalog", "concepts", "source", "resources")


def _write_stage(book_root: Path, stage: str, payload: dict, *,
                 key_hint: str = "") -> tuple[Path, str]:
    """写一个阶段产物，目录名 = **payload 内容**的指纹（真正的按内容寻址）。

    ⚠️ key 必须由**完整 payload** 算，不能只由"逻辑上等价的字段"算。
    第一版按 `units` 的 identity 算 key，于是"同一批单元、但 payload 里多了
    reconciliation/references"会算出**同一个 key 而内容不同** —— 那正好是
    内容寻址要防的事（同一个目录名下出现两份不同的内容）。M3 早期写下的那份
    就是按旧口径算的，本报告里标为 legacy。
    """
    # ⚠️ 先把**会变但不代表内容**的字段摘掉再算 key。
    #    第一版直接把 payload 丢进 digest，而 payload 里有 `generated_at` ——
    #    结果**每次运行都得到一个新 key**，"同一份内容"被登记成 N 份，
    #    内容寻址等于失效（而且目录会一直长）。时间戳进的是产物，不进它的身份。
    volatile = {"generated_at", "catalog_key", "index_key", "manifest_key"}
    stable = {k: v for k, v in payload.items() if k not in ("_meta",)}
    stable["_meta"] = {k: v for k, v in (payload.get("_meta") or {}).items()
                       if k not in volatile}
    key = digest_of(stable).split(":")[1][:12]
    out = book_root / "artifacts" / stage / key / f"{stage}.json"
    return write_json_atomic(out, payload), key


def migrate_book(workspace: Workspace, book_id: str, *, write: bool = True) -> dict:
    """把一本书的存量产物读成新布局的四类产物。**只读旧产物。**"""
    registry = load_registry(workspace)
    ctx = registry.resolve(book_id)
    book_root = workspace.root / "data" / "books" / book_id
    report: dict = {"book_id": book_id, "title": ctx.title, "parse": str(ctx.parse),
                    "book_root": str(book_root), "items": [], "excluded": [],
                    "missing_v2_fields": [], "stages": {}}

    def record(kind: str, source: Path, dest: Path | None, note: str) -> None:
        report["items"].append({"kind": kind, "source": str(source),
                                "dest": str(dest) if dest else None, "note": note})

    # ── 1) catalog（M3 的产物，这里**真正落盘**）
    if not (ctx.parse / "chapters.json").is_file():
        report["excluded"].append({"item": "catalog", "why": f"{ctx.parse} 下没有 chapters.json"})
    else:
        catalog, details = build_catalog(ctx.parse, book_id=book_id, revision=ctx.revision)
        refs = verify_references(ctx.parse, ctx.lessons, catalog)
        payload = catalog.as_dict()
        payload["_meta"] = {**payload["_meta"], "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                            "schema_version": "2.0", "migrated_from": str(ctx.parse)}
        payload["reconciliation"] = details["reconciliation"]
        payload["references"] = refs
        dest = key = None
        if write:
            dest, key = _write_stage(book_root, "catalog", payload)
            payload["_meta"]["catalog_key"] = key
            write_json_atomic(dest, payload)
        report["stages"]["catalog"] = {"key": key, "units": details["units"],
                                       "catalog_only": len(details["reconciliation"]["catalog_only"]),
                                       "unmatched": len(details["reconciliation"]["unmatched"]),
                                       "references_ok": refs["ok"],
                                       "ambiguities": details["verify"]["counts"]["ambiguities"],
                                       "needs_review": details["verify"]["counts"]["needs_review_pending"]}
        record("catalog", ctx.parse / "chapters.json", dest,
               f"{details['units']} 个单元；{len(details['reconciliation']['catalog_only'])} 个"
               f"catalog 独有（容器节点，已逐条写理由）")
        if not refs["ok"]:
            report["excluded"].append({"item": "references",
                                       "why": f"{len(refs['missing'])} 处引用解析不出来",
                                       "detail": refs["missing"][:5]})

    # ── 2) concepts（M4 的书级概念索引）
    if (ctx.parse / "concepts.json").is_file():
        index = build_index(ctx.parse, catalog, book_id=book_id)
        payload = index.as_dict()
        payload["_meta"] = {**payload["_meta"], "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        dest = key = None
        if write:
            dest, key = _write_stage(book_root, "concepts", payload)
            payload["_meta"]["index_key"] = key
            write_json_atomic(dest, payload)
        report["stages"]["concepts"] = index.verify()["counts"]
        record("concepts", ctx.parse / "concepts.json", dest,
               f"{len(index.concepts)} 个概念；{len(index.blocking_parts())} 个分片挡住冻结")
        if index.blocking_parts():
            report["excluded"].append({
                "item": "concepts.frozen",
                "why": f"{len(index.blocking_parts())} 个分片自报有漏项 → **索引不冻结**"
                       f"（方案 §6：失败分片不能用旧片冒充新鲜）",
                "detail": [p.unit_key for p in index.blocking_parts()][:10]})
    else:
        report["excluded"].append({"item": "concepts", "why": "没有 concepts.json"})

    # ── 3) source（CanonicalSource：raw + selected）
    document = ctx.parse / "document.md"
    if document.is_file():
        # ⚠️ 旧产物里**没有** selected_text 与修复记录 —— 不伪造。raw = selected，
        #    并在 missing_v2_fields 里明说（方案 §13：不伪造旧数据缺少的 v2 内容）。
        source = CanonicalSource.build(document.read_bytes(), revision=f"parse:{ctx.revision}",
                                      raw_path=str(document), selected_path=str(document))
        payload = source.as_dict()
        dest = key = None
        if write:
            dest, key = _write_stage(book_root, "source", payload)
        report["stages"]["source"] = {"revision": source.revision,
                                      "raw_sha256": source.raw_sha256,
                                      "is_repaired": source.is_repaired}
        record("source", document, dest,
               "旧产物没有独立 selected_text / 修复记录：本次 raw = selected，并在报告里列出缺口")
        report["missing_v2_fields"].append(
            {"field": "selected_text（独立修复版本）",
             "why": "旧产物只有 document.md，没有 source-repair 记录；"
                    "按方案 §13『不伪造旧数据缺少的 v2 内容』，这里不造一份假的修复记录"})
    else:
        report["excluded"].append({"item": "source", "why": "没有 document.md"})

    # ── 4) resources（ResourceManifest：旧产物里的图）
    library = workspace.root / "figures"
    index_json = library / "index.json"
    if index_json.is_file():
        manifest = ResourceManifest(book_id=book_id)
        payload = read_json_soft(index_json) or {}
        taken = skipped = 0
        for row in payload.get("figures") or []:
            svg = row.get("svg")
            if not svg:
                skipped += 1
                continue
            candidate = Path(svg)
            if not candidate.is_absolute():
                candidate = workspace.root / candidate
            if not candidate.is_file():
                skipped += 1
                continue
            manifest.add_file(logical_name=candidate.name, kind="svg", source=candidate,
                              book_id=book_id, scope_key=str(row.get("id") or ""))
            taken += 1
        body = manifest.as_dict()
        body["_meta"] = {**body["_meta"], "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                         "note": "从 figures/index.json 只读导入"}
        dest = key = None
        if write:
            dest, key = _write_stage(book_root, "resources", body)
            body["_meta"]["manifest_key"] = key
            write_json_atomic(dest, body)
        report["stages"]["resources"] = {"resources": taken, "skipped": skipped,
                                         "conflicts": len(manifest.conflicts)}
        record("resources", index_json, dest,
               f"{taken} 张图登记；{skipped} 条没有可用 svg 路径（已计数，未静默丢弃）")
        if skipped:
            report["excluded"].append({"item": "resources.partial",
                                       "why": f"{skipped} 条图记录没有可用 svg 路径",
                                       "detail": "已计数；需要人工确认是历史残留还是路径迁移问题"})
    else:
        report["excluded"].append({"item": "resources", "why": f"没有 {index_json}"})

    # ── 5) 可回退版本
    report["rollback"] = {
        "code": f"git HEAD 仍指向 {_git_head(workspace)}；旧 blocks/lib/tools 未删，随时可回退",
        "data": f"旧产物原位未动：{ctx.parse} / {ctx.lessons} / {ctx.audit}",
        "new_root": str(book_root),
        "note": ("回退=不读新根即可；新根只增不改。发布指针回退另见 delivery/publish.rollback "
                 "（方案 §16：三层回退）"),
    }
    return report


def _git_head(workspace: Workspace) -> str:
    import subprocess
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(workspace.root),
                              stdout=subprocess.PIPE, timeout=20).stdout.decode().strip()
    except Exception:                                          # noqa: BLE001
        return "（取不到）"


def migrate_all(workspace: Workspace, *, write: bool = True) -> dict:
    registry = load_registry(workspace)
    reports = []
    for book_id in registry.list_books():
        ctx = registry.resolve(book_id)
        if not (ctx.parse / "chapters.json").is_file():
            reports.append({"book_id": book_id, "skipped": True,
                            "why": f"{ctx.parse} 下没有 chapters.json（产物已归档或从未解析）",
                            "items": [], "excluded": [{"item": "全部",
                                                       "why": "没有可迁移的解析产物"}],
                            "missing_v2_fields": [], "stages": {}})
            continue
        reports.append(migrate_book(workspace, book_id, write=write))
    return {"_meta": {"file_type": "data_migration_report", "schema_version": "1.0",
                      "generator": "aiforstudy.tools.migrations.migrate_data",
                      "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                      "workspace": str(workspace.root),
                      "field_notes": {
                          "items": "逐项去向：旧路径 → 新路径 + 说明",
                          "excluded": "**明确排除**的条目及理由（不许凭空数组通过）",
                          "missing_v2_fields": "旧数据里缺失、且本次**不伪造**的字段",
                          "rollback": "三层回退：代码 / 数据 / 发布指针",
                      }},
            "books": reports,
            "summary": {
                "books": len(reports),
                "migrated": sum(1 for r in reports if not r.get("skipped")),
                "skipped": sum(1 for r in reports if r.get("skipped")),
                "items": sum(len(r.get("items") or []) for r in reports),
                "excluded": sum(len(r.get("excluded") or []) for r in reports),
                "missing_v2_fields": sum(len(r.get("missing_v2_fields") or []) for r in reports),
            }}


def to_markdown(payload: dict) -> str:
    lines = ["# M9 数据迁移报告（只读导入）", "",
             f"生成时间：{payload['_meta']['created_at']}",
             f"工作区：`{payload['_meta']['workspace']}`", "",
             "## 汇总", "", "| 口径 | 值 |", "|---|---:|"]
    for key, value in payload["summary"].items():
        lines.append(f"| {key} | {value} |")
    for report in payload["books"]:
        lines += ["", f"## {report['book_id']}（{report.get('title') or '—'}）", ""]
        if report.get("skipped"):
            lines += [f"**跳过**：{report['why']}", ""]
            continue
        stages = report.get("stages") or {}
        if stages:
            lines += ["| 阶段 | 结果 |", "|---|---|"]
            for stage, info in stages.items():
                lines.append(f"| `{stage}` | `{json.dumps(info, ensure_ascii=False)}` |")
            lines += [""]
        lines += ["### 逐项去向", "", "| 类别 | 旧路径 | 新路径 | 说明 |", "|---|---|---|---|"]
        for item in report.get("items") or []:
            dest = item["dest"] or "（未落盘）"
            lines.append(f"| {item['kind']} | `{item['source']}` | `{dest}` | {item['note']} |")
        if report.get("excluded"):
            lines += ["", "### 明确排除（逐条理由）", "", "| 条目 | 理由 |", "|---|---|"]
            for row in report["excluded"]:
                lines.append(f"| `{row['item']}` | {row['why']} |")
        if report.get("missing_v2_fields"):
            lines += ["", "### 旧数据缺失、本次**不伪造**的字段", "",
                      "| 字段 | 为什么不做一份假的 |", "|---|---|"]
            for row in report["missing_v2_fields"]:
                lines.append(f"| {row['field']} | {row['why']} |")
        rollback = report.get("rollback") or {}
        if rollback:
            lines += ["", "### 回退", ""]
            for key, value in rollback.items():
                lines.append(f"* **{key}**：{value}")
        lines += [""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M9 数据迁移演练（只读旧产物）")
    ap.add_argument("--workspace", default=str(PKG_ROOT))
    ap.add_argument("--book", help="只迁这一本；默认全部")
    ap.add_argument("--dry-run", action="store_true", help="只读不写")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    workspace = Workspace(root=Path(args.workspace).resolve())
    if args.book:
        payload = {"_meta": {"file_type": "data_migration_report", "schema_version": "1.0",
                             "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                             "workspace": str(workspace.root)},
                   "books": [migrate_book(workspace, args.book, write=not args.dry_run)],
                   "summary": {}}
        payload["summary"] = {"books": 1,
                              "migrated": 0 if args.dry_run else 1,
                              "items": len(payload["books"][0]["items"]),
                              "excluded": len(payload["books"][0]["excluded"]),
                              "missing_v2_fields": len(payload["books"][0]["missing_v2_fields"])}
    else:
        payload = migrate_all(workspace, write=not args.dry_run)

    if not args.dry_run:
        out = workspace.root / "data" / "_migration" / "data-migration-report.json"
        write_json_atomic(out, payload)
        doc = PKG_ROOT / "docs" / "M9-数据迁移报告.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(to_markdown(payload), encoding="utf-8")
        print(f"✓ 迁移报告写入 {out}")
        print(f"✓ 人可读报告写入 {doc}")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        summary = payload["summary"]
        print(f"书 {summary.get('books')}｜已迁 {summary.get('migrated')}｜"
              f"跳过 {summary.get('skipped', 0)}｜逐项去向 {summary.get('items')}｜"
              f"明确排除 {summary.get('excluded')}｜不伪造的缺失字段 "
              f"{summary.get('missing_v2_fields')}")
        for report in payload["books"]:
            mark = "跳过" if report.get("skipped") else "已迁"
            stages = "，".join(f"{k}={json.dumps(v, ensure_ascii=False)[:60]}"
                              for k, v in (report.get("stages") or {}).items())
            print(f"  [{mark}] {report['book_id']}：{stages or report.get('why', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
