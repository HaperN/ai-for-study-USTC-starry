#!/usr/bin/env python3
"""端到端**干跑**：一本书从预检到正文，逐层记账（实施方案 M7）。

    ../.venv-linux/bin/python tools/toc_e2e.py                       # 四本 + 新书（**不调模型**）
    ../.venv-linux/bin/python tools/toc_e2e.py --slug linear-algebra-v3
    ../.venv-linux/bin/python tools/toc_e2e.py --review              # 复核 **dry-run**（仍不调模型）
    ../.venv-linux/bin/python tools/toc_e2e.py --review --write-review   # 真调模型并应用

（**没有 `--no-review` 这个参数**：不带 `--review` 就是完全不碰模型。
 2026-09-28 核验抓出：旧 docstring 写了这个参数名，实际不存在，照抄会报
 `unrecognized arguments`。）

## 为什么要有这个工具

M7 要的是"**每本书都能给出 `confidence` 与不确定清单**"这份逐书报告。
与其每次手敲七八条命令、再从 JSON 里抄数字（抄错一次就白跑），
不如把整条链跑一遍、把账目**算出来**写成一份报告（教训 §7.1）。

## 它跑什么（**不含**教案/讲解/渲染 —— 不花钱在教案上）

```
preflight → extract → build → review(可选) → apply(可选) → preview → body
```

`--review` 默认是 **dry-run**（只把"会送什么"写进产物、不调模型）；
要真调模型得同时给 `--write-review`，这是防止误花钱的双闸。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
CLI = [PYTHON, "-m", "blocks.parse.toc.cli"]

#: 默认跑这几本（四本旧书 + 一本**没见过的**新书 —— A6 的唯一真验收）
DEFAULT_SLUGS = ["numerical-methods", "linear-algebra-v2", "complex-analysis-v2",
                 "math-analysis-2", "linear-algebra-v3"]


def _run(args: list[str], *, timeout: int = 1800) -> tuple[int, str]:
    started = time.time()
    proc = subprocess.run(args, capture_output=True, text=True, cwd=str(ROOT),
                          timeout=timeout)
    seconds = time.time() - started
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, f"[{seconds:.1f}s]\n{out}"


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _usage_delta(before: dict, after: dict) -> dict:
    return {key: after.get(key, 0) - before.get(key, 0)
            for key in ("calls", "prompt_tokens", "completion_tokens", "total_tokens")}


def _usage_now() -> dict:
    sys.path.insert(0, str(ROOT / "lib"))
    import model                                          # noqa: PLC0415
    return model.usage_summary()


def run_book(slug: str, *, review: bool, write_review: bool,
             log) -> dict:
    """跑一本书的整条链，返回它的**账目**（全部从产物里算，不抄）。"""
    parse_dir = ROOT / "data" / "parse" / slug
    row: dict = {"slug": slug, "steps": {}, "ok": True}
    if not parse_dir.is_dir():
        row["ok"] = False
        row["error"] = f"{parse_dir} 不存在"
        return row

    usage_before = _usage_now() if review else {}
    for step, extra in (("preflight", []),
                        ("extract", []),
                        ("build", []),
                        ("preview", []),
                        ("body", [])):
        args = CLI + [step, str(parse_dir)] + extra
        code, out = _run(args)
        row["steps"][step] = {"code": code, "output": out.strip().splitlines()[-3:]}
        if code != 0:
            row["ok"] = False
            row["steps"][step]["error"] = True
            log(f"  ✗ {slug} {step} 退出码 {code}")
            log("\n".join("      " + line for line in out.strip().splitlines()[-6:]))
            return row
        log(f"  ✓ {slug} {step}")

    if review:
        args = CLI + ["review", str(parse_dir)]
        if not write_review:
            args.append("--dry-run")
        code, out = _run(args)
        row["steps"]["review"] = {"code": code, "output": out.strip().splitlines()[-4:],
                                  "dry_run": not write_review}
        row["ok"] = row["ok"] and code == 0
        log(f"  {'✓' if code == 0 else '✗'} {slug} review"
            f"{'（dry-run）' if not write_review else ''}")
        if code == 0 and write_review:
            code, out = _run(CLI + ["apply", str(parse_dir), "--write"])
            row["steps"]["apply"] = {"code": code,
                                     "output": out.strip().splitlines()[-2:]}
            row["ok"] = row["ok"] and code == 0
            log(f"  {'✓' if code == 0 else '✗'} {slug} apply")

    if review:
        row["usage"] = _usage_delta(usage_before, _usage_now())

    row["report"] = _book_report(parse_dir)
    return row


def _book_report(parse_dir: Path) -> dict:
    """从产物里把"这本书怎么样"算出来（不抄任何数字）。"""
    toc = _load(parse_dir / "toc.v2.json") or {}
    body = _load(parse_dir / "body.json") or {}
    preflight = _load(parse_dir / "toc.preflight.json") or {}
    # 复核建议稿：**优先真实结论**，没有才退回 dry-run 稿（两者分文件，见 cli._pick_review）
    review = _load(parse_dir / "toc.review.json")
    review_is_dry = False
    if review is None:
        review = _load(parse_dir / "toc.review.dry.json") or {}
        review_is_dry = True
    else:
        review = review or {}
    meta = toc.get("_meta") or {}
    counts = meta.get("counts") or {}
    nodes = toc.get("nodes") or []
    body_counts = (body.get("_meta") or {}).get("counts") or {}

    with_printed = sum(1 for n in nodes if (n.get("page") or {}).get("printed"))
    with_pdf = sum(1 for n in nodes if (n.get("page") or {}).get("pdf"))
    return {
        "confidence": meta.get("confidence"),
        "sources_used": meta.get("sources_used") or [],
        "nodes": len(nodes),
        "counts": counts,
        "page_offset": meta.get("page_offset"),
        "printed_page_filled": f"{with_printed}/{len(nodes)}" if nodes else "0/0",
        "pdf_page_filled": f"{with_pdf}/{len(nodes)}" if nodes else "0/0",
        "reviewed_by": meta.get("reviewed_by"),
        "revisions": sum(len(r.get("decisions") or [])
                         for r in (toc.get("revisions") or [])),
        "unresolved": [u.get("why") for u in (toc.get("unresolved") or [])],
        "disagreements": [f"{d.get('kind')}：{d.get('title')}"
                          for d in (toc.get("disagreements") or [])],
        "suspicious": [f"{s.get('toc_id')} {s.get('title')}：{s.get('why')}"
                       for s in (toc.get("suspicious") or [])],
        "unconfirmed": [f"{n.get('number')} {n.get('title')}"
                        for n in nodes if not n.get("confirmed", True)],
        "body": body_counts,
        "body_orphans_sample": [f"{o.get('title')}（←{o.get('parent_title')}）"
                                for o in (body.get("orphans") or [])[:5]],
        "preflight": {
            "can_auto_parse": preflight.get("can_auto_parse"),
            "has_text_layer": preflight.get("has_text_layer"),
            "bookmarks": (preflight.get("bookmarks") or {}).get("count"),
            "toc_pages": (preflight.get("toc_pages") or {}).get("pdf_pages"),
            "method": (preflight.get("toc_pages") or {}).get("method"),
        },
        "review": {
            "suspects": review.get("suspects_total"),
            "images": (review.get("_meta") or {}).get("images"),
            "model_calls": (review.get("_meta") or {}).get("model_calls"),
            "is_dry": review_is_dry or not int((review.get("_meta") or {}).get("model_calls") or 0),
            "summary": review.get("summary"),
            "downgraded": (review.get("_meta") or {}).get("downgraded"),
            "decisions": [f"{d.get('action')}：{d.get('title') or d.get('reason', '')[:40]}"
                          for d in (review.get("decisions") or [])],
        } if review else None,
    }


def render(row: dict) -> str:
    """人的报告。"""
    lines = [f"== {row['slug']} =="]
    if not row.get("ok") and row.get("error"):
        return lines[0] + f"\n  ✗ {row['error']}"
    report = row.get("report") or {}
    # ⚠️ 中途失败的书**没有 report**（`run_book` 在失败步就返回了）。
    # 原来这里继续往下渲染，打出来一片 `None`，看不出"为什么失败"
    # （2026-09-28 核验抓出：那正是新书该报的错，却被渲染成"数据缺失"）。
    if not row.get("ok") and not report:
        steps = row.get("steps") or {}
        failed = [name for name, st in steps.items() if st.get("code")]
        lines.append(f"  ✗ 在 `{failed[-1] if failed else '?'}` 这一步停下（退出码非 0）"
                     f" —— 本书的账目**不可信**，先修这一步")
        for name, st in steps.items():
            for line in (st.get("output") or [])[-3:]:
                lines.append(f"      [{name}] {str(line)[:150]}")
        return "\n".join(lines)
    pre = report.get("preflight") or {}
    lines.append(f"  预检：能自动解析={pre.get('can_auto_parse')} "
                 f"文字层={pre.get('has_text_layer')} 书签={pre.get('bookmarks')} "
                 f"目录页={pre.get('toc_pages')}（{pre.get('method')}）")
    lines.append(f"  目录：confidence={report.get('confidence')} "
                 f"nodes={report.get('nodes')} 章={ (report.get('counts') or {}).get('chapters') } "
                 f"非正文={(report.get('counts') or {}).get('non_teaching')} "
                 f"来源={'+'.join(report.get('sources_used') or [])}")
    lines.append(f"  落页：印刷页 {report.get('printed_page_filled')} ｜ "
                 f"PDF页 {report.get('pdf_page_filled')} ｜ 偏移量 {report.get('page_offset')}")
    lines.append(f"  账本：unresolved={len(report.get('unresolved') or [])} "
                 f"disagreements={len(report.get('disagreements') or [])} "
                 f"suspicious={len(report.get('suspicious') or [])} "
                 f"unconfirmed={len(report.get('unconfirmed') or [])} "
                 f"revisions={report.get('revisions')}")
    for why in (report.get("unresolved") or [])[:2]:
        lines.append(f"      ○ {str(why)[:100]}")
    for why in (report.get("disagreements") or [])[:3]:
        lines.append(f"      ⚠️ {str(why)[:100]}")
    for why in (report.get("suspicious") or [])[:2]:
        lines.append(f"      ? {str(why)[:100]}")
    for why in (report.get("unconfirmed") or [])[:3]:
        lines.append(f"      ~ 未确认 {str(why)[:80]}")
    body = report.get("body") or {}
    lines.append(f"  正文：units={body.get('units')} 空节={body.get('empty_nodes')} "
                 f"贴不上={body.get('orphans')}（有父归属 {body.get('orphans_with_parent')}）"
                 f" 每个都有 toc_node={body.get('units_with_toc_node') == body.get('units')}")
    review = report.get("review")
    if review:
        tag = "**dry-run 稿**" if review.get("is_dry") else "真实结论"
        lines.append(f"  复核（{tag}）：可疑={review.get('suspects')} 图={review.get('images')} "
                     f"调用={review.get('model_calls')} 结论={review.get('summary')}")
        for row_dec in (review.get("decisions") or [])[:4]:
            lines.append(f"      → {row_dec[:100]}")
    usage = row.get("usage")
    if usage:
        lines.append(f"  花销：{usage.get('calls')} 次调用，"
                     f"{usage.get('total_tokens')} token")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="目录层端到端干跑（M7）")
    ap.add_argument("--slug", action="append", help="只跑这些书（可重复）")
    ap.add_argument("--review", action="store_true", help="跑模型视觉复核")
    ap.add_argument("--write-review", action="store_true",
                    help="真的调模型并应用（不给就只有 dry-run）")
    ap.add_argument("--json-out", help="把逐书账目写成 JSON")
    args = ap.parse_args(argv)

    slugs = args.slug or DEFAULT_SLUGS
    rows: list[dict] = []

    def log(message: str) -> None:
        print(message, flush=True)

    for slug in slugs:
        log(f"── {slug}")
        rows.append(run_book(slug, review=args.review,
                             write_review=args.write_review, log=log))

    print("\n" + "=" * 72)
    print("目录层端到端干跑报告")
    print("=" * 72)
    for row in rows:
        print(render(row))
        print()

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"✓ {args.json_out}")
    return 0 if all(row.get("ok") for row in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
