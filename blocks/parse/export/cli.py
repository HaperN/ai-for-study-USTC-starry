"""导出层命令行。

用法：
  python -m blocks.parse.export.cli <normalized-source.jsonl> --out data/parse/<名字> [--title 书名]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_parse import export_parse  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="导出层：normalized-source.jsonl → data/parse/")
    parser.add_argument("source", help="normalized-source.jsonl 路径")
    parser.add_argument("--out", required=True, help="输出目录（data/parse/<名字>）")
    parser.add_argument("--title", help="书名，写进 chapters.json 的 book.title")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    result = export_parse(args.source, args.out, book_title=args.title)
    if not args.quiet:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
