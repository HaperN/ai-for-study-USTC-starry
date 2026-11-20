"""模型块命令行。

    # 看会给哪些节生成、要几次调用（不调模型）
    ../.venv-linux/bin/python -m blocks.model.cli run \
        --lessons data/lesson/linear-algebra-full --out data/lesson/linear-algebra-full/models --dry-run

    # 只跑一节（试点用）
    ../.venv-linux/bin/python -m blocks.model.cli run \
        --lessons data/lesson/linear-algebra-full --out data/lesson/linear-algebra-full/models \
        --only CH02-S01 --force
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "lib"))

from model_generate import run as run_models      # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="模型块：给每一节生成交互模型规格（只填数据）")
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run", help="逐节生成模型规格")
    p_run.add_argument("--lessons", required=True, help="教案目录（里面要有 lessons/）")
    p_run.add_argument("--parse", help="解析目录（P-016 的「前置知识」判定要用它的 chapters.json"
                                       "＋concepts.json；不填按 data/parse/<书> 猜）")
    p_run.add_argument("--out", required=True, help="输出目录（渲染层按 models/<unit>.json 读）")
    p_run.add_argument("--only", help="只跑这些单元（逗号分隔）")
    p_run.add_argument("--force", action="store_true", help="已经有产物也重跑")
    p_run.add_argument("--retries", type=int, default=1, help="有模型被丢弃时重跑几次")
    p_run.add_argument("--dry-run", action="store_true", help="只说要跑哪些节、不调模型")
    p_run.add_argument("--effort", default="high")
    p_run.add_argument("--max-tokens", type=int, default=16000)
    p_run.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    def log(message: str) -> None:
        if not args.quiet:
            print(message, file=sys.stderr, flush=True)

    client = None
    if not args.dry_run:
        from model import DeepSeekClient
        client = DeepSeekClient(effort=args.effort, max_tokens=args.max_tokens)

    outcome = run_models(Path(args.lessons), Path(args.out),
                         only=[x.strip() for x in args.only.split(",")] if args.only else None,
                         force=args.force, client=client, retries=args.retries,
                         dry_run=args.dry_run,
                         parse_dir=Path(args.parse) if args.parse else None, log=log)
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    return 0 if not outcome["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
