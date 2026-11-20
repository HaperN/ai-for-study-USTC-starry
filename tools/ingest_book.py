"""MinerU 入库兼容工具。正式全流程请使用 python -m aiforstudy run。

已有 MinerU 结果或显式 --use-api 获取；不再提供本地 OCR 兜底。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
MINERU_DIR = "mineru"
OCR_MARKERS = ("latex", "_extract_cache")          # 只在 OCR 路线上才会有的产物
MINERU_MARKERS = (MINERU_DIR,)


def _sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(module: str, args: list[str], *, dry_run: bool) -> tuple[int, str]:
    command = [PYTHON, "-m", module, *args]
    if dry_run:
        return 0, "DRY-RUN " + " ".join(command[2:])
    result = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True)
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode, output.strip()


def find_layout(parse_dir: Path) -> Path | None:
    """解析目录里有没有 MinerU 产物（认内容，不认文件名）。"""
    mineru = parse_dir / MINERU_DIR
    if not mineru.is_dir():
        return None
    direct = mineru / "layout.json"
    if direct.is_file():
        return direct
    for candidate in sorted(mineru.glob("*.json")):
        if candidate.name.endswith(".fetch.json"):
            continue
        try:
            if '"pdf_info"' in candidate.read_text(encoding="utf-8", errors="replace"):
                return candidate
        except OSError:
            continue
    return None


def decide_route(parse_dir: Path, pdf: Path | None, source: str,
                 use_api: bool = False) -> tuple[str, str]:
    """返回 (route, 理由)。route ∈ {mineru, mineru-api, ocr}。"""
    layout = find_layout(parse_dir)
    if source == "mineru":
        if layout is None:
            raise ValueError(f"--source mineru，但 {parse_dir}/{MINERU_DIR}/ 里没有带 pdf_info 的 layout.json")
        return "mineru", f"强制指定；产物 {layout.relative_to(parse_dir)}"
    if source == "ocr":
        raise ValueError("旧 OCR 入口已退役；请提供 MinerU 结果或使用 --use-api")
    if layout is not None:
        return "mineru", f"有 MinerU 产物（{layout.relative_to(parse_dir)}）→ 默认走 MinerU"
    if use_api:
        if pdf is None or not pdf.is_file():
            raise ValueError("--use-api 需要 --pdf 指向真实的 PDF")
        return "mineru-api", "没有 MinerU 产物，但给了 --use-api → 现从云端捞一份（不花我们的 token）"
    if pdf is not None and pdf.is_file():
        raise ValueError("缺少 MinerU 结果；请使用 --use-api 获取，不会退回本地 OCR")
    raise ValueError(f"{parse_dir} 里既没有 MinerU 产物，也没给 --pdf —— 没有可走的路线")


def stale_products(parse_dir: Path, route: str) -> list[str]:
    """另一条路留下的产物（跨来源污染）。"""
    stale = []
    markers = OCR_MARKERS if route == "mineru" else MINERU_MARKERS
    for name in markers:
        path = parse_dir / name
        if path.exists():
            stale.append(name)
    if route == "ocr" and (parse_dir / "preflight.json").is_file():
        stale.append("preflight.json（MinerU 路线的体检报告）")
    return stale


def ingest(parse_dir: Path, *, pdf: Path | None = None, source: str = "auto",
           title: str | None = None, dry_run: bool = False, keep_stale: bool = False,
           use_api: bool = False, page_ranges: str | None = None, token: str | None = None,
           log=print) -> dict:
    parse_dir = Path(parse_dir)
    route, reason = decide_route(parse_dir, pdf, source, use_api)
    log(f"路线：{route}（{reason}）")

    stale = stale_products(parse_dir, route)
    if stale and not keep_stale:
        raise ValueError(
            f"{parse_dir} 里有另一条路的产物：{stale}\n"
            f"  最容易出事的是 OCR 路的 `latex/`：概念层会优先读它，于是"
            f"「MinerU 的原文 + OCR 的清洗稿」混在一起，而且不报错。\n"
            "  处置：先移走/删掉这些目录（或加 --keep-stale 明确表示你知道在做什么）")
    if stale:
        log(f"⚠ 跨来源陈旧产物（已放行）：{stale}")

    produced: list[str] = []
    preflight_summary = None
    fetch_record = None
    via = "local-products" if route == "mineru" else "local-ocr"
    if route == "mineru-api":
        args = ["fetch", str(pdf), "--out", str(parse_dir)]
        if page_ranges:
            args += ["--pages", page_ranges]
        if token:
            args += ["--token", token]
        code, output = _run("blocks.parse.mineru.cli", args, dry_run=dry_run)
        if code != 0:
            raise ValueError(f"从云端捞产物失败（退出码 {code}）：\n{output}")
        produced.append("mineru/")
        fetch_record = "mineru/fetch.json"
        via = "api"
        # ⚠️ dry-run 下**不许说"已落盘"**（2026-09-27 实测）：实际一个文件都没写，
        #    却说"云端产物已落盘"，这是**假日志** —— 和 A6-3（打印假命令）同一类病，
        #    会让人误以为云端已经解析完、可以省钱跳过。
        log("（dry-run：还没真捞）接下来按 MinerU 路线走" if dry_run
            else "云端产物已落盘，接下来按 MinerU 路线走")
        route = "mineru"
    if route == "mineru":
        layout = find_layout(parse_dir)
        code, output = _run("blocks.parse.mineru.cli",
                            ["preflight", str(parse_dir / MINERU_DIR), "--out", str(parse_dir)],
                            dry_run=dry_run)
        if code != 0:
            raise ValueError(f"体检没通过（拦住不往下跑）：\n{output}")
        preflight_summary = output.splitlines()[0] if output else None
        log(f"体检：{preflight_summary}")
        args = ["adapt", str(parse_dir / MINERU_DIR), "--out", str(parse_dir),
                "--note", "由 tools/ingest_book.py 走 MinerU 默认路线"]
        if title:
            args += ["--title", title]
        # ⚠️ 把 PDF 一起传下去：有**内嵌书签**时，书签就是最权威的目录
        #    （实测《数值计算方法》109 条、《数分讲义第二册》155 条），
        #    结构层拿它对账"解析出来的章"对不对 —— 0 token。
        if pdf:
            args += ["--pdf", str(pdf)]
        code, output = _run("blocks.parse.mineru.cli", args, dry_run=dry_run)
        produced = ["document.md", "figures.json", "mineru.audit.json", "extract.json"]
    else:
        args = ["extract", str(pdf), "--out", str(parse_dir)]
        code, output = _run("blocks.parse.structure.cli", args, dry_run=dry_run)
        produced = ["document.md", "extract.json", "_extract_cache/"]
    if code != 0:
        raise ValueError(f"{route} 路线执行失败（退出码 {code}）：\n{output}")
    for name in produced:
        if not dry_run and not (parse_dir / name).exists():
            raise ValueError(f"{route} 路线说做完了，但 {name} 不在 {parse_dir} 里")
    log(output.splitlines()[-1] if output else "完成")

    record = {
        "_meta": {
            "file_type": "ingest", "schema_version": "1.0",
            "generator": "aiforstudy.tools.ingest_book",
            "field_notes": {
                "route": "这条解析产物是哪个来源做的：mineru=云端产物（默认，含刚用 API 捞的）；ocr=本地识别（兜底）",
                "via": "怎么拿到产物的：api=刚用云端 API 捞的；local-products=目录里本来就有；local-ocr=本地识别",
                "page_ranges": "用 API 捞的时候只解析了哪几页（空=全篇）",
                "fetch_record": "用 API 捞的时候的记录文件（mineru/fetch.json：批次号、状态流转、落盘文件）",
                "reason": "为什么走这条（留档：以后出问题能回答「当初为什么这么选」）",
                "preflight": "MinerU 路线的体检结论（拦级别问题会停在上一步，根本走不到这里）",
                "stale_products": "另一条路留下的产物；不为空说明这次是同目录混用，要人工确认",
            },
        },
        "route": route,
        "via": via,
        "reason": reason,
        "page_ranges": page_ranges,
        "fetch_record": fetch_record,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pdf": str(pdf) if pdf else None,
        "mineru": None,
        "preflight": preflight_summary,
        "stale_products": stale,
        "produced": produced,
        "dry_run": dry_run,
    }
    if route == "mineru":
        layout = find_layout(parse_dir)
        # ⚠️ `layout` 可能是 None（2026-09-27 实测）：`--dry-run --use-api` 只说不做，
        #    云端产物**根本没捞**，于是这里 `layout.name` 抛
        #    `AttributeError: 'NoneType' object has no attribute 'name'`。
        #    偏偏"先预览再花钱"正是**最该能用**的组合 —— 预览崩了就只能闭眼花钱。
        record["mineru"] = {"dir": MINERU_DIR,
                            "layout": layout.name if layout else None,
                            "layout_sha256": _sha256(layout) if layout else None,
                            "note": None if layout else "（dry-run：云端产物还没捞，所以没有 layout）"}
    if not dry_run:
        parse_dir.mkdir(parents=True, exist_ok=True)
        (parse_dir / "ingest.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log(f"已写 {parse_dir}/ingest.json（路线留档）")
    return record


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="MinerU 入库兼容工具（已有结果或 API）")
    parser.add_argument("parse_dir", help="解析块产物目录（如 data/parse/线性代数）")
    parser.add_argument("--pdf", help="教材 PDF（API 获取需要）")
    parser.add_argument("--source", choices=("auto", "mineru"), default="auto")
    parser.add_argument("--title", help="书名")
    parser.add_argument("--use-api", action="store_true",
                        help="没有 MinerU 产物时，用云端 API 现捞一份（需要 token）")
    parser.add_argument("--pages", dest="page_ranges",
                        help='配合 --use-api：只解析某几页，如 "1-40"')
    parser.add_argument("--token", help="MinerU token（默认读 .secrets/mineru.env）")
    parser.add_argument("--keep-stale", action="store_true",
                        help="明知目录里有另一条路的产物也放行")
    parser.add_argument("--dry-run", action="store_true", help="只说会走哪条路、不写文件")
    args = parser.parse_args(argv)
    try:
        record = ingest(Path(args.parse_dir), pdf=Path(args.pdf) if args.pdf else None,
                        source=args.source, title=args.title, dry_run=args.dry_run,
                        keep_stale=args.keep_stale, use_api=args.use_api,
                        page_ranges=args.page_ranges, token=args.token)
    except ValueError as exc:
        print(f"停下：{exc}", file=sys.stderr)
        return 2
    print(json.dumps({k: v for k, v in record.items() if k != "_meta"},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
