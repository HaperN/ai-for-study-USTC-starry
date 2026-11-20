"""图形块命令行。

    # 画一张图并当场校验（不调模型）
    ../.venv-linux/bin/python -m blocks.figures.cli draw --out figures

    # 只校验已有产物（改完图或改完校验规则后重跑这条）
    ../.venv-linux/bin/python -m blocks.figures.cli verify --out figures

    # 概念表重抽之后**必须**跑这条：按概念名把"图 → 概念"重挂到当前概念表上，并打印 diff
    # （不跑的话，登记表里留的是上一版的 id —— 号一旦被重用，图就配错了概念，P-020）

校验不过以非 0 退出，方便接流水线。产物：
    <out>/vector-parallelogram.svg            矢量图（交付用，颜色跟主题走）
    <out>/vector-parallelogram.png            位图（只为人工目视一次）
    <out>/vector-parallelogram.geometry.json  坐标与标注外框（校验时读它）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "lib"))

import library                                        # noqa: E402
import registry                                      # noqa: E402
import verify_svg                                    # noqa: E402
import vector_parallelogram as parallelogram         # noqa: E402

# 图库里的图（声明式）+ 老模块那张，统一成一个"能画的清单"
FIGURES = {"vector-parallelogram": parallelogram}
# 图清单 = 向量图 + 新图种（电路/曲线/3D）；`--name` 与四个动词都不变
FIGURES.update({spec["name"]: spec for spec in library.ALL_FIGURES})


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="图形块：用代码画图 + 断言校验")
    sub = parser.add_subparsers(dest="command", required=True)

    p_draw = sub.add_parser("draw", help="画图并当场校验")
    p_draw.add_argument("--out", default="figures")
    p_draw.add_argument("--name", default="all", choices=["all"] + sorted(FIGURES))

    p_verify = sub.add_parser("verify", help="只校验已有产物")
    p_verify.add_argument("--out", default="figures")
    p_verify.add_argument("--name", default="all", choices=["all"] + sorted(FIGURES))

    p_rehang = sub.add_parser("rehang", help="按概念名把图重挂到当前概念表（概念重抽后必跑）")
    p_rehang.add_argument("--out", default="figures")
    p_rehang.add_argument("--concepts", required=True,
                          help="当前概念表（concepts.json）")
    p_rehang.add_argument("--dry-run", action="store_true", help="只看 diff，不写回")

    sub.add_parser("list", help="看有哪些图可以画")
    args = parser.parse_args(argv)

    if args.command == "list":
        print(json.dumps({"可画": sorted(FIGURES)}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "rehang":
        out = Path(args.out)
        index_path = out / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.is_file() \
            else {"figures": []}
        table = json.loads(Path(args.concepts).read_text(encoding="utf-8"))
        result = registry.rehang(index.get("figures", []), table)
        if not args.dry_run:
            index["figures"] = result["figures"]
            index.setdefault("_meta", {})["rehung_against"] = str(args.concepts)
            registry.write_index(out, index["figures"])
        print(json.dumps({
            "重挂了": len(result["figures"]),
            "对不上当前概念表的": result["unresolved"],
            "只登记过 id 的老图（没有名字，无法重挂）": result.get("legacy") or [],
            "diff": result["diff"],
            "写回了": (not args.dry_run),
        }, ensure_ascii=False, indent=2))
        return 0

    out = Path(args.out)
    names = sorted(FIGURES) if args.name == "all" else [args.name]
    index_path = out / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) \
        if index_path.is_file() else {"figures": []}
    kept = [row for row in index.get("figures", []) if row.get("name") not in set(names)]

    summary, failed = [], 0
    for name in names:
        module = FIGURES[name]
        if args.command == "draw":
            if isinstance(module, dict):          # 图库里的声明式图
                library.draw(module, out)
            else:                                  # 老模块
                module.draw(out)
        svg = out / f"{name}.svg"
        geometry = out / f"{name}.geometry.json"
        result = verify_svg.verify(svg, geometry)
        # 登记表：**只登记校验通过的图**（登记了就得保证它是对的）
        if result["ok"]:
            kept.append(registry.entry(name, module, {}, result))
        else:
            failed += 1
            print(f"⚠ {name} 校验没过，**不登记**（不给渲染层用）", file=sys.stderr)
        summary.append({"图": name, "通过": result["ok"], "问题": result["problems"]})

    registry.write_index(out, kept)
    print(json.dumps({"画了": len(names), "通过": len(names) - failed, "未通过": failed,
                      "逐张": summary, "登记表": str(index_path)},
                     ensure_ascii=False, indent=2))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
