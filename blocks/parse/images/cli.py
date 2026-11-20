"""图片提取命令行。

  python -m blocks.parse.images.cli <教材.pdf> --out data/parse/<名字> \
      [--ocr data/parse/<名字>/_extract_cache/<...>/ocr.json] [--model] [--model-pages 3]
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))                  # extract_images
sys.path.insert(0, str(HERE.parents[3] / "lib"))      # model client
from extract_images import extract_images  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="图片提取：整页渲染 + 插图位置框")
    parser.add_argument("pdf")
    parser.add_argument("--out", required=True)
    parser.add_argument("--scale", type=float, default=2.0)
    parser.add_argument("--page-limit", type=int)
    parser.add_argument("--ocr", help="ocr.json 路径（有它才能自动找空白带和图题）")
    parser.add_argument("--model", action="store_true", help="启用模型复核（只对候选页）")
    parser.add_argument("--model-pages", type=int, help="最多复核多少页（成本护栏）")
    parser.add_argument("--only-pages", help="只复核这些页，逗号分隔")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    client = None
    if args.model:
        from model import DeepSeekClient
        client = DeepSeekClient(effort="low")
    only = [int(x) for x in args.only_pages.split(",")] if args.only_pages else None
    result = extract_images(args.pdf, args.out, scale=args.scale,
                            page_limit=args.page_limit, ocr_json=args.ocr,
                            client=client, model_pages=args.model_pages, only_pages=only)
    if not args.quiet:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if client is not None:
            print("模型用量:", json.dumps(client.session_usage.as_dict(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
