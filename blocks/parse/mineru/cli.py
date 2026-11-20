"""MinerU 适配块命令行。

    # 适配：MinerU 产物 → document.md + figures.json + mineru.audit.json + extract.json
    python -m blocks.parse.mineru.cli adapt <MinerU 目录 或 layout.json> \
        --out data/parse/linear-algebra [--title 线性代数]

    # 只看统计、不写文件（先看数对不对）
    python -m blocks.parse.mineru.cli adapt ... --dry-run

    # 自检已有产物：三条硬规则 + 计数与 audit 是否对得上
    python -m blocks.parse.mineru.cli verify <out 目录>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "lib"))          # lib/

from adapt import MinerUFormatError, adapt, check_document       # noqa: E402
from fetch import MinerUFetchError, fetch                                     # noqa: E402
from preflight import format_report, preflight                             # noqa: E402
from files import load_json                                       # noqa: E402


def _summarize(counts: dict) -> str:
    by_type = counts.get("by_type") or {}
    kinds = "、".join(f"{k} {v}" for k, v in sorted(by_type.items(), key=lambda kv: -kv[1]))
    formulas = counts.get("formulas") or {}
    return (f"页 {counts.get('pages')}｜块 {counts.get('blocks')}（{kinds}）"
            f"｜公式 行内 {formulas.get('inline')}/独立 {formulas.get('interline')}"
            f"｜图 {counts.get('figures')}｜汉字 {counts.get('cjk_chars')}"
            f"｜正文 {counts.get('text_chars')} 字"
            f"｜丢弃 {counts.get('discarded')}（{(counts.get('discarded_by_type') or {})}）")


def cmd_adapt(args) -> int:
    try:
        result = adapt(args.mineru, args.out, book_title=args.title, note=args.note or "",
                       pdf=args.pdf,
                       copy_images=not args.no_images, dry_run=args.dry_run)
    except MinerUFormatError as exc:
        print(f"停下，什么都没写：{exc}", file=sys.stderr)
        return 2

    print(_summarize(result.counts))
    audit = result.audit
    print(f"版本：{audit['format']['version']}（{audit['format']['backend']}，OCR={audit['format']['ocr_enable']}）")
    inputs = "、".join(f"{item['path']} {item['sha256'][:8]}" for item in audit["inputs"])
    print(f"输入：{inputs}")
    problems = audit["checks"]["hard_rules"]
    if problems:
        print(f"三条硬规则没过（{len(problems)} 条）：", file=sys.stderr)
        for problem in problems[:10]:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    missing = audit["images_missing"]
    if missing:
        print(f"有 {len(missing)} 张图在 images/ 里找不到（已记进 audit，没有静默跳过）", file=sys.stderr)
    if args.dry_run:
        print("dry-run：一个文件都没写")
    else:
        print(f"已写：{args.out}/document.md、figures.json、mineru.audit.json、extract.json")
    return 0


def cmd_fetch(args) -> int:
    if args.dry_run:
        from fetch import load_token
        try:
            token = load_token(args.token)
            print(f"token：已找到（{token[:6]}…，{len(token)} 位）")
        except MinerUFetchError as exc:
            print(f"token：没找到 —— {exc}", file=sys.stderr)
            return 2
        from fetch import page_count, plan_chunks
        total = page_count(args.pdf)
        plan = "整篇" if args.page_ranges else (
            f"整篇（{total} 页）" if total and total <= 200 else
            f"{total} 页，超 200 页上限 → 自动切 " + str(len(plan_chunks(total))) + " 块"
            if total else "整篇（数不出页数）")
        print(f"会把 {args.pdf} 送到 MinerU：{plan}"
              + (f"（只解析第 {args.page_ranges} 页）" if args.page_ranges else "")
              + f"，产物落到 {args.out}/mineru/")
        return 0
    try:
        record = fetch(args.pdf, args.out, token=args.token, page_ranges=args.page_ranges,
                       model_version=args.model_version, language=args.language,
                       is_ocr=args.ocr, timeout_s=args.timeout,
                       keep_chunks=args.keep_chunks, refetch=args.refetch)
    except MinerUFetchError as exc:
        print(f"停下：{exc}", file=sys.stderr)
        return 2
    progress = record.get("extract_progress") or {}
    pages = f"｜页数 {progress.get('extracted_pages')}" if progress else ""
    print(f"批次 {record['batch_id']}｜状态流转 {' → '.join(record['states'])}"
          f"{pages}｜落盘 {len(record['files'])} 个文件")
    print("文件：" + "、".join(record["files"][:8]))
    return 0


def cmd_preflight(args) -> int:
    report = preflight(args.mineru, out=args.out)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_report(report))
        if args.out:
            print(f"\n报告已写：{args.out}/preflight.json")
    return 0 if report["passed"] else 2


def cmd_verify(args) -> int:
    out = Path(args.out)
    audit = load_json(out / "mineru.audit.json")
    if audit is None:
        print(f"找不到 {out}/mineru.audit.json，先跑 adapt", file=sys.stderr)
        return 2
    document = (out / "document.md")
    if not document.is_file():
        print(f"找不到 {document}，先跑 adapt", file=sys.stderr)
        return 2
    lines = document.read_text(encoding="utf-8").splitlines()
    problems = check_document(lines)
    counted = audit["counts"]
    real_lines = counted.get("document_lines")
    real_figures = counted.get("figures")
    mismatches = []
    if real_lines != len(lines):
        mismatches.append(f"行数：audit 说 {real_lines}，实际 {len(lines)}")
    if real_figures != len(audit.get("images") or []):
        mismatches.append(f"图片数：counts 说 {real_figures}，images 里 {len(audit.get('images') or [])}")
    if problems:
        mismatches.extend(problems)
    if mismatches:
        print(f"自检没过（{len(mismatches)} 条）：", file=sys.stderr)
        for item in mismatches[:20]:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print(f"自检通过：{len(lines)} 行、{real_figures} 张图、三条硬规则 0 违规"
          f"（版本 {audit['format']['version']}）")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="MinerU 适配块：把 MinerU 产物拼成 OCR 那一层的形状")
    sub = parser.add_subparsers(dest="command", required=True)

    p_adapt = sub.add_parser("adapt", help="MinerU 产物 → document.md / figures.json / audit / extract")
    p_adapt.add_argument("mineru", help="MinerU 产物目录（含 layout.json + images/）或直接给 layout.json")
    p_adapt.add_argument("--out", required=True, help="输出目录（通常就是 data/parse/<书>）")
    p_adapt.add_argument("--title", help="书名")
    p_adapt.add_argument("--note", help="备注（写进 audit，比如这次跑的是哪 40 页）")
    p_adapt.add_argument("--pdf", help="教材 PDF —— 有内嵌书签时，书签就是最权威的目录（**优先用它**）")
    p_adapt.add_argument("--no-images", action="store_true", help="不复制单图文件（只索引）")
    p_adapt.add_argument("--dry-run", action="store_true", help="只算不写")
    p_adapt.set_defaults(func=cmd_adapt)

    p_fetch = sub.add_parser("fetch", help="用云端 API 把 PDF 自动捞成产物（省掉网页端手工两步）")
    p_fetch.add_argument("pdf", help="教材 PDF")
    p_fetch.add_argument("--out", required=True, help="解析块目录（产物落到 <out>/mineru/）")
    p_fetch.add_argument("--pages", dest="page_ranges", help='只解析某几页，如 "1-2" 或 "2,4-6"')
    p_fetch.add_argument("--model-version", default="vlm", choices=("pipeline", "vlm"),
                         help="vlm=默认（hybrid 后端，保留书上标点，与网页端一致）；"
                              "pipeline=会把半角标点转成全角")
    p_fetch.add_argument("--language", default="ch")
    p_fetch.add_argument("--ocr", action="store_true", help="强制 OCR（扫描件用）")
    p_fetch.add_argument("--timeout", type=int, default=1800, help="等多久算超时（秒）")
    p_fetch.add_argument("--token", help="不给就读 环境变量 MINERU_API_TOKEN / .secrets/mineru.env")
    p_fetch.add_argument("--keep-chunks", action="store_true",
                         help="留着分块中间产物（默认合并后删）")
    p_fetch.add_argument("--refetch", action="store_true",
                         help="分块已下载也重新捞一遍（默认复用，用于断点续跑）")
    p_fetch.add_argument("--dry-run", action="store_true", help="只看参数，不发请求")
    p_fetch.set_defaults(func=cmd_fetch)

    p_pre = sub.add_parser("preflight", help="体检 MinerU 产物形态（不调模型、不写正文）")
    p_pre.add_argument("mineru", help="MinerU 产物目录或 layout.json")
    p_pre.add_argument("--out", help="把 preflight.json 写到这个目录（通常就是 data/parse/<书>）")
    p_pre.add_argument("--json", action="store_true", help="输出完整 JSON 报告")
    p_pre.set_defaults(func=cmd_preflight)

    p_verify = sub.add_parser("verify", help="自检已有产物：三条硬规则 + 计数对账")
    p_verify.add_argument("out", help="adapt 的输出目录")
    p_verify.set_defaults(func=cmd_verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
