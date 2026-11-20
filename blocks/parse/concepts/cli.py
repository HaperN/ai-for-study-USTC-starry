"""概念抽取命令行。

  python -m blocks.parse.concepts.cli <parse_dir> [--only CH01-S01,CH01-S02] [--force] [--effort low]
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[3] / "lib"))
from extract_concepts import extract_concepts  # noqa: E402


#: 概念抽取的**默认并行度**（2026-09-29 固化）：
#: 实测每节约 44 秒里 **43 秒是模型在思考**（推理占输出 87%），这段时间完全可以叠起来 ——
#: 6 路并行把整本 45 分钟压到 **4.5 分钟**，**花费一分不多**（实测 6.2 倍）。
#: 想退回串行用 `--parallel 1`（例如排查并发问题时）。
DEFAULT_PARALLEL = 6


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="概念抽取：逐单元抽定义+定理 → concepts.json")
    parser.add_argument("parse_dir")
    parser.add_argument("--only", help="只抽这些单元（逗号分隔），用于单节重跑")
    parser.add_argument("--force", action="store_true", help="忽略已有分片，全部重抽")
    parser.add_argument("--effort", default="high",
                        choices=("off", "low", "high", "max"),
                        help="思考档位；off = 关掉思考（抄写类任务可试）")
    parser.add_argument("--max-tokens", type=int, default=64000)
    parser.add_argument("--parallel", type=int, default=DEFAULT_PARALLEL,
                        help=f"并行几个单元（默认 {DEFAULT_PARALLEL}）。实测每节 40+ 秒里绝大部分是"
                             "模型在思考，并行只压时间、不加花费；1 = 退回串行")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    from model import DeepSeekClient
    def _new_client():
        # 每个 worker 一个自己的客户端（客户端带心跳等实例状态，不跨线程共享）
        return DeepSeekClient(effort=args.effort, max_tokens=args.max_tokens)

    client = _new_client()

    def progress(done, total, unit_id):
        if not args.quiet:
            print(f"  [{done}/{total}] {unit_id} …", file=sys.stderr, flush=True)

    only = [x.strip() for x in args.only.split(",")] if args.only else None

    def log(message):
        if not args.quiet:
            print(message, file=sys.stderr, flush=True)

    result = extract_concepts(args.parse_dir, client=client, only_units=only,
                              force=args.force, progress=progress, log=log,
                         parallel=args.parallel,
                         client_factory=_new_client)
    if not args.quiet:
        print(json.dumps({**result, "model_calls": client.calls,
                          "usage": client.session_usage.as_dict()},
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
