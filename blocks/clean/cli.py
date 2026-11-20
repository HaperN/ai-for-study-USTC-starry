"""清洗块命令行。

    # 看会跑哪些单元（不调模型）
    ../.venv-linux/bin/python -m blocks.clean.cli run data/parse/linear-algebra --dry-run

    # 跑一节（调清洗规则后重跑也用它）
    ../.venv-linux/bin/python -m blocks.clean.cli run data/parse/linear-algebra \
        --only CH01-S01-SS01

    # 整本
    ../.venv-linux/bin/python -m blocks.clean.cli run data/parse/linear-algebra

产物（默认写到 <parse_dir>/latex/）：
    <单元>.json         契约产物（逐段 source_quote + 清洗后 text_md）
    <单元>.md           给人看的镜像：清洗后的全文
    latex.index.json    进度、覆盖率、每节请求字节数、原文未改动
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "lib"))

from clean_text import run as run_clean          # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="清洗块：把 OCR 原文的公式改成正确 LaTeX")
    sub = parser.add_subparsers(dest="command", required=True)
    # 注意：下面每个子命令都在这里注册；revalidate 见下方

    p_run = sub.add_parser("run", help="逐节清洗")
    p_run.add_argument("parse_dir", help="解析块产物目录（含 chapters.json）")
    p_run.add_argument("--out", help="输出目录（默认 <parse_dir>/latex）")
    p_run.add_argument("--only", help="只跑这些单元（逗号分隔）")
    p_run.add_argument("--force", action="store_true", help="已经有产物也重跑")
    p_run.add_argument("--retries", type=int, default=1, help="校验不过时重跑几次")
    p_run.add_argument("--effort", default="high", choices=("low", "high", "max"))
    p_run.add_argument("--max-tokens", type=int, default=128000,
                       help="上限（成本按实际输出算，抬天花板是免费保险）。实测 1.1.3 这种公式密集的长节"
                            "思考会吃掉 3 万以上，32000 会直接失败")
    p_run.add_argument("--dry-run", action="store_true", help="只算会跑哪些，不调模型")
    p_run.add_argument("--quiet", action="store_true")
    p_reval = sub.add_parser("revalidate",
                             help="复检已有产物：用**当前**校验规则重算 checks（不调模型、不花钱）")
    p_reval.add_argument("parse_dir")
    p_reval.add_argument("--out", help="输出目录（默认 <parse_dir>/latex）")

    args = parser.parse_args(argv)

    if args.command == "revalidate":
        from clean_text import revalidate as run_revalidate
        summary = run_revalidate(Path(args.parse_dir),
                                 Path(args.out) if args.out else None)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    only = [x.strip() for x in args.only.split(",")] if args.only else None
    client = None
    if not args.dry_run:
        from model import DeepSeekClient
        client = DeepSeekClient(effort=args.effort, max_tokens=args.max_tokens)

    def log(message: str) -> None:
        if not args.quiet:
            print(message, file=sys.stderr, flush=True)

    outcome = run_clean(Path(args.parse_dir), Path(args.out) if args.out else None,
                        only=only, force=args.force, client=client,
                        retries=args.retries, dry_run=args.dry_run, log=log)

    if args.dry_run:
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
        return 0
    index = outcome["index"]
    print(json.dumps({
        "输出": args.out or str(Path(args.parse_dir) / "latex"),
        "本次跑了": [row["unit_id"] for row in outcome["ran"]] or "（没有要跑的）",
        "模型调用": outcome["model_calls"],
        "总计": index["totals"],
        "最大请求字节": index["max_request_bytes"],
        "原文未改动": index["original_text_unchanged"],
        "模型用量": client.session_usage.as_dict() if client else None,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
