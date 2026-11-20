"""结构层命令行。

用法：
  # 1) 抽取（PDF → document.md；文字层坏了会自动走 OCR）
  python -m blocks.parse.structure.cli extract <教材.pdf> --out data/parse

  # 2) 结构化（document.md → structured.md + structure.json）
  python -m blocks.parse.structure.cli structure <document.md> --out data/parse

  # 3) 一步到位
  python -m blocks.parse.structure.cli run <教材.pdf> --out data/parse

产物：
  <out>/document.md            抽取出来的纯文本（每页 `[第 N 页]`）
  <out>/structured.md          规范标题 markdown（忠实版，含全部层级）
  <out>/structured.anchor3.md  只保留三层锚点的版本，可直接喂下游 normalize-source
  <out>/structure.json         结构清单（含 warnings / stats / 每个标题的源行号）
  <out>/extract.json           OCR 抽取元信息（引擎、页数、置信度、耗时）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))                            # structure.py / review.py
sys.path.insert(0, str(HERE.parent / "extract"))         # ocr_pdf.py
sys.path.insert(0, str(HERE.parents[2] / "lib"))         # lib/model.py（跨块基础设施）

from structure import (  # noqa: E402
    ANCHOR_MAX_LEVEL, DEFAULT_MAX_LEVEL,
    build_from_file, build_structure, render, split_pages, denoise, _drop_toc_region,
    join_split_headings, detect_headings, write_outputs,
)


def _prepare_lines(text: str):
    """复原 build_structure 用的同一套行序列（给二次渲染用）。"""
    lines, _pages = split_pages(text)
    lines, _ = _drop_toc_region(lines)
    lines, _warn, _stats = denoise(lines)
    lines, _ = join_split_headings(lines)
    detect_headings(lines)
    return lines


# ── 视觉恢复的标题行：把"目录有、正文缺"的那一行**插回去**（2026-09-30 用户定案）──
def _norm_anchor(text: str) -> str:
    """归一化：只留字母数字和汉字（OCR 的空格/标点差异不该影响定位）。"""
    return "".join(ch for ch in str(text or "") if ch.isalnum())


def _locate_anchor(raw_lines: list[str], anchor: str) -> int | None:
    """在**原始行**里找锚（`first_line_after`），返回 1-based 行号。

    不用页码定位：标题常印在页面下部的标题栏里，按页首插会把上一节的正文拦腰切断。
    用"标题之后的第一行正文"逐字定位，插到它**前面**。
    """
    key = _norm_anchor(anchor)[:12]
    if len(key) < 6:
        return None
    for index, line in enumerate(raw_lines, 1):
        if key and key in _norm_anchor(line):
            return index
    return None


def _insert_one_heading(text: str, decision: dict, anchor: str, toc_line: int,
                        recovery: dict) -> str:
    """在锚行**前面**插一行标题；插完把 text 返回（行号平移由调用方处理）。"""
    raw_lines = text.splitlines(keepends=True)
    number = str(recovery.get("number") or "").strip()
    dashed = number.replace(".", "-") if re.fullmatch(r"\d+(\.\d+)+", number) else number
    title = str(recovery.get("declared_title") or decision.get("title") or "").strip()
    newline = f"§ {dashed} {title}" if dashed else title
    raw_lines.insert(toc_line - 1, newline + "\n")
    return "".join(raw_lines)


def apply_recovery(text: str, authority: list[dict], rows: list[dict],
                   warnings: list[str]) -> tuple[str, list[dict]]:
    """把视觉恢复的标题行插进正文，并**平移其后所有 authority 行号**。

    为什么必须插行 + 平移（而不是把锚那一行当标题）：锚那一行是**正文首句**
    （例："半导体三极管又称双极型晶体管…"），直接标成标题会把这句话从正文里吃掉。
    """
    if not rows:
        return text, authority
    raw_lines = text.splitlines()
    todo = []
    for row in rows:
        line_no = _locate_anchor(raw_lines, row.get("first_line_after"))
        if line_no is None:
            warnings.append(f"视觉恢复：{row.get('toc_id')} 的锚在正文里找不到"
                            f"（{str(row.get('first_line_after'))[:20]}…）→ **不插**，保持缺失")
            continue
        todo.append((line_no, row))
    todo.sort(key=lambda item: item[0])
    shifted = [dict(item) for item in authority]
    for line_no, row in todo:
        decision = {"line": line_no, "level": int(row.get("level") or 2),
                    "number": row.get("number"),
                    # 标题用**目录写法**（`晶体管的大信号模型`）；截图里那行原文
                    # （`＊§ 2-5 晶体管的大信号模型`）只作证据留在 applied_recovery 里
                    "title": row.get("declared_title") or row.get("title"),
                    "toc_id": row.get("toc_id"), "recovered_by": "vision",
                    "printed_title": row.get("title")}
        text = _insert_one_heading(text, decision, row.get("first_line_after"), line_no, row)
        for item in shifted:
            try:
                if int(item.get("line") or 0) >= line_no:
                    item["line"] = int(item["line"]) + 1
            except (TypeError, ValueError):
                continue
        shifted.append(decision)
        warnings.append(f"视觉恢复：第 {line_no} 行插入标题「{row.get('number')} "
                        f"{row.get('declared_title')}」（recovered_by=vision）")
    # 按行号排好再交出去（结构层自己也会排，但这里排序让产物可读、可比对）
    shifted.sort(key=lambda item: int(item.get("line") or 0))
    return text, shifted


def cmd_structure(args) -> int:
    authority = None
    if getattr(args, "authority", None):
        payload = json.loads(Path(args.authority).read_text(encoding="utf-8"))
        authority = payload.get("decisions") if isinstance(payload, dict) else payload
    warnings: list[str] = []
    # 视觉恢复：把"目录有、正文缺"的标题行**插回正文**（改动后的文本要一路用到底 ——
    # 包括下面渲染 `structured.anchor3.md` 时的行号，否则锚点会整体错位）。
    source_text: str | None = None
    if getattr(args, "recover", None) and Path(args.recover).is_file():
        rows = (json.loads(Path(args.recover).read_text(encoding="utf-8"))
                .get("recovered") or [])
        if rows:
            source_text = Path(args.source).read_text(encoding="utf-8")
            source_text, authority = apply_recovery(source_text, authority or [], rows, warnings)
            for line in warnings:
                print(f"  · {line}")

    if source_text is not None:
        result = build_structure(source_text, max_level=args.max_level,
                                 source_path=Path(args.source), authority=authority)
        result.warnings.extend(warnings)
    else:
        result = build_from_file(args.source, max_level=args.max_level, authority=authority)
    if args.out is None:
        print(result.structured_markdown)
        if args.json:
            print(json.dumps(result.to_json(), ensure_ascii=False, indent=2))
        return 0
    written = write_outputs(result, args.out)
    # 三层锚点版：给下游 normalize-source 用（它只认 章/节/小节）
    # ⚠️ 必须用**插行后**的文本：行号要与 `result.headings` 一致（视觉恢复那批尤其如此）
    text = source_text if source_text is not None else Path(args.source).read_text(
        encoding="utf-8", errors="replace")
    lines = result.render_lines
    anchor_path = Path(args.out) / f"structured.anchor{ANCHOR_MAX_LEVEL}.md"
    anchor_path.write_text(render(result.headings, lines, anchor_level=ANCHOR_MAX_LEVEL),
                           encoding="utf-8")
    written[f"structured_anchor{ANCHOR_MAX_LEVEL}"] = str(anchor_path)
    if not args.quiet:
        _report(result, written)
    return 0


def cmd_extract(args) -> int:
    from ocr_pdf import extract_pdf

    def progress(done: int, total: int) -> None:
        if not args.quiet and (done % 10 == 0 or done == total):
            print(f"  OCR {done}/{total} 页…", file=sys.stderr)

    meta = extract_pdf(args.source, cache_root=args.cache or (Path(args.out or ".") / "_extract_cache"),
                       scale=args.scale, page_limit=args.page_limit, force=args.force,
                       progress=progress)
    if args.out:
        Path(args.out).mkdir(parents=True, exist_ok=True)
        (Path(args.out) / "extract.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not args.quiet:
        print(json.dumps({k: v for k, v in meta.items() if k != "_meta"},
                         ensure_ascii=False, indent=2))
    return 0


def cmd_run(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    extract_args = argparse.Namespace(source=args.source, out=str(out),
                                      cache=str(out / "_extract_cache"),
                                      scale=args.scale, page_limit=args.page_limit,
                                      force=args.force, quiet=True)
    meta = None
    from ocr_pdf import extract_pdf
    meta = extract_pdf(args.source, cache_root=out / "_extract_cache",
                       scale=args.scale, page_limit=args.page_limit, force=args.force)
    (out / "extract.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8")
    document = Path(meta["document"])
    result = build_from_file(document, max_level=args.max_level)
    written = write_outputs(result, out)
    # 把 document.md 也复制到 out 下，方便一起看
    import shutil
    target = out / "document.md"
    if document.resolve() != target.resolve():
        shutil.copy2(document, target)
    written["document"] = str(target)
    lines = result.render_lines
    anchor_path = out / f"structured.anchor{ANCHOR_MAX_LEVEL}.md"
    anchor_path.write_text(render(result.headings, lines, anchor_level=ANCHOR_MAX_LEVEL),
                           encoding="utf-8")
    written[f"structured_anchor{ANCHOR_MAX_LEVEL}"] = str(anchor_path)
    if not args.quiet:
        print(json.dumps({"extract": {k: v for k, v in meta.items() if k != "_meta"},
                          "files": written}, ensure_ascii=False, indent=2))
        _report(result, written)
    return 0


def cmd_review(args) -> int:
    import review as R
    payload = R.read_json(args.structure_json)
    from model import DeepSeekClient
    client = DeepSeekClient(effort="low")
    review = R.review_structure(payload, client, chunk_size=args.chunk,
                                only_suspects=not args.all)
    review["_meta"]["model_calls"] = client.calls
    review["_meta"]["usage"] = client.session_usage.as_dict()
    R.write_json(args.out, review)
    print(json.dumps({
        "review": args.out,
        "headings_total": review["headings_total"],
        "suspects": review["suspects_total"],
        "decisions": len(review["decisions"]),
        "model_calls": client.calls,
        "usage": client.session_usage.as_dict(),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_apply_review(args) -> int:
    import review as R
    payload = R.read_json(args.structure_json)
    review = R.read_json(args.review)
    updated = R.apply_review(payload, review)
    last = (updated.get("revisions") or [{}])[-1]
    summary = {
        "reviewed": last.get("reviewed", 0),
        "changed": last.get("changed", 0),
        "headings_before": payload.get("stats", {}).get("headings_total"),
        "headings_after": updated.get("stats", {}).get("headings_total"),
        "changes": [
            {"line": r["source_line"], "action": r["action"],
             "before": r["before"], "after": r["after"], "reason": r["reason"][:60]}
            for r in last.get("decisions", [])],
    }
    if args.dry_run:
        print(json.dumps({"dry_run": True, **summary}, ensure_ascii=False, indent=2))
        return 0
    R.write_json(args.structure_json, updated)
    print(json.dumps({"applied": args.structure_json, **summary},
                     ensure_ascii=False, indent=2))
    return 0


def _report(result, written) -> None:
    print("\n=== 结构统计 ===")
    for key, value in result.stats.items():
        if key != "source_sha256":
            print(f"  {key}: {value}")
    print("\n=== 警告 ===")
    for warning in result.warnings[:20]:
        print(f"  • {warning}")
    if len(result.warnings) > 20:
        print(f"  … 还有 {len(result.warnings) - 20} 条")
    print("\n=== 产物 ===")
    for key, path in written.items():
        print(f"  {key}: {path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="结构层：PDF → 规范标题 markdown + 结构清单")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("source", help="PDF 或 document.md")
        p.add_argument("--out", help="输出目录")
        p.add_argument("--max-level", type=int, default=DEFAULT_MAX_LEVEL,
                       help=f"最大层级（默认 {DEFAULT_MAX_LEVEL}）")
        p.add_argument("--quiet", action="store_true")

    p_struct = sub.add_parser("structure", help="document.md → structured.md + structure.json")
    common(p_struct)
    p_struct.add_argument("--json", action="store_true", help="stdout 时同时打印 structure.json")
    p_struct.add_argument("--recover",
                          help="toc.recover.json：视觉恢复的标题行（插回正文，标 recovered_by=vision）")
    p_struct.add_argument("--authority",
                          help="方案 A：toc.decisions.json（目录权威×MinerU 候选的标题判定）。"
                               "给了它，标题与层级全部取自目录，不再用编号正则推")
    p_struct.set_defaults(func=cmd_structure)

    p_extract = sub.add_parser("extract", help="PDF → document.md（文字层坏则走 OCR）")
    p_extract.add_argument("source")
    p_extract.add_argument("--out")
    p_extract.add_argument("--cache")
    p_extract.add_argument("--scale", type=float, default=2.0)
    p_extract.add_argument("--page-limit", type=int)
    p_extract.add_argument("--force", action="store_true")
    p_extract.add_argument("--quiet", action="store_true")
    p_extract.set_defaults(func=cmd_extract)

    p_review = sub.add_parser("review", help="结构复核：产出 review.json（只给建议，不改文件）")
    p_review.add_argument("structure_json", help="structure.json 路径")
    p_review.add_argument("--out", required=True, help="review.json 输出路径")
    p_review.add_argument("--chunk", type=int, default=50, help="每片送模型多少个标题")
    p_review.add_argument("--all", action="store_true", help="不筛可疑项，全部标题都送模型")
    p_review.set_defaults(func=cmd_review)

    p_apply = sub.add_parser("apply-review", help="应用 review.json（可 --dry-run 先看）")
    p_apply.add_argument("structure_json")
    p_apply.add_argument("--review", required=True)
    p_apply.add_argument("--dry-run", action="store_true", help="只打印会改成什么样，不落盘")
    p_apply.set_defaults(func=cmd_apply_review)

    p_run = sub.add_parser("run", help="PDF → document.md → structured.md（一步到位）")
    p_run.add_argument("source")
    p_run.add_argument("--out", required=True)
    p_run.add_argument("--scale", type=float, default=2.0)
    p_run.add_argument("--page-limit", type=int)
    p_run.add_argument("--force", action="store_true")
    p_run.add_argument("--max-level", type=int, default=DEFAULT_MAX_LEVEL)
    p_run.add_argument("--quiet", action="store_true")
    p_run.set_defaults(func=cmd_run)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in {"extract", "run"}:
        parser.error("旧 PDF/OCR 入口已退役；请使用 python -m aiforstudy prepare --book <书籍ID>（MinerU）")
    if args.command == "structure" and not args.authority:
        parser.error("生产结构必须提供 --authority toc.decisions.json；不再回退编号正则推层级")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
