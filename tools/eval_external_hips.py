#!/usr/bin/env python3
"""**外部基准跑分**：拿 HiPS 的金标目录（arXiv 2509.00909）评我们的解析产物。

## 为什么用它（Q3，2026-09-28 定案）

我们一直只有自测（6 本中文理科教材）。HiPS 放出了 **49 本开放获取书 / 9,812 条人工标题**
的金标，而且**跟我们的域不同**（英文法律书、层级最深 9 层）——正好检验"泛用性"。

## 用的就是他们的口径（不是自己另造一套）

| 他们 notebook 里的东西 | 我们这里怎么用 |
|---|---|
| `title_cleaner` | 原样复制（折空白 + 去标点）——见 `data/external/hips/protocol_extract.py` |
| `compute_metrics`：精确 `prec/rec` + 容差 `tolprec/tolrec`（Levenshtein 距离 < `tol`） | 同一判据（`tol=2`），自己实现 Levenshtein 以免依赖 `thefuzz` |
| `build_hierarchy_from_rows` + `zss.simple_dist` 的树编辑距离 | **直接用官方 `zss` 库**（已 `pip install zss`）算，再按 `(n1+n2)` 归一 → nTED |

**金标格式**：`层级,标题,页码`（`1,Introduction to Swiss Law,2`）。

## 我们这边的输入

* `--gold`：金标 `.toc`
* `--parse-dir`：我们的解析产物目录（读 `structure.json` 的 `headings[]`：
  `canonical_level` 当层级、`title` 当标题）

## 诚实说明

* 只比**标题与层级**（他们另一本 `evaluate_segments.ipynb` 才比"边界落点"），
  因为我们这边"每节在正文里的行号区间"还没出成统一产物；
* 页码字段我们**不参与评分**（两边的"第几页"口径可能是印刷页/PDF 页，容易假错）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


# ── 他们的口径（复制自 evaluate_toc.ipynb，见 protocol_extract.py）──────────
def title_cleaner(text: str) -> str:
    """折空白 + 去标点（与官方一致）。"""
    if not text:
        return ""
    s = text.replace("\r", " ").replace("\n", " ").strip()
    s = re.sub(r"\s+", " ", s)
    s = s.translate({ord(c): None for c in "“”‘’«»„‚()/'\""})
    return s.replace("\\", "\\\\")


def lev_distance(a: str, b: str) -> int:
    """Levenshtein 距离（官方用 `thefuzz`/`rapidfuzz` 的同一量；这里自带实现免依赖）。"""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def safe_div(a: float, b: float) -> float:
    return round(a / b, 4) if b else 0.0


def compute_metrics(preds: list[str], gts: list[str], *, tol: int = 2) -> dict:
    """精确 + 容差的 precision/recall（与官方 `compute_metrics` 同一判据）。"""
    pred_set, gt_set = set(preds), set(gts)
    tp = sum(1 for v in preds if v in gt_set)
    toltp = sum(1 for v in preds if min((lev_distance(v, g) for g in gts), default=99) < tol)
    rec_tp = sum(1 for g in gts if g in pred_set)
    tolrec_tp = sum(1 for g in gts if min((lev_distance(g, p) for p in preds), default=99) < tol)
    return {
        "prec": safe_div(tp, len(preds)), "rec": safe_div(rec_tp, len(gts)),
        "tolprec": safe_div(toltp, len(preds)), "tolrec": safe_div(tolrec_tp, len(gts)),
        "pred": len(preds), "gt": len(gts), "exact_hit": tp, "tol_hit": toltp,
    }


# ── 数据读写 ────────────────────────────────────────────────────────────
#: 前置/后置材料（金标把它们当 1 级标题；我们的解析层**故意**判成"非正文"）。
#: 实测《boats to burn》金标 23 个 lv1 里有 12 个是这类 —— 不分开的话，
#: "lv1 召回只有 0.435"这种读数会被误读成"我们漏了半本书"。
_NON_TEACHING = re.compile(
    r"^\s*(table of contents|contents|list of (tables|maps|figures|plates|illustrations|"
    r"abbreviations|contributors)|foreword|preface|acknowledg|abbreviations|"
    r"appendix|annex|conventions|index|bibliography|references|about the|"
    r"copyright|title page|dedication|glossary|prologue|epilogue)\b", re.I)


def is_non_teaching(title: str) -> bool:
    return bool(_NON_TEACHING.match(title or ""))


def load_gold(path: Path) -> list[tuple[int, str]]:
    """金标 `.toc` → `[(level, title)]`（跳过读不动的行，不猜）。"""
    rows: list[tuple[int, str]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        parts = line.split(",", 2)
        if len(parts) < 2:
            continue
        try:
            level = int(parts[0].strip())
        except ValueError:
            continue
        title = title_cleaner(parts[1])
        if title:
            rows.append((level, title))
    return rows


def load_ours(parse_dir: Path) -> list[tuple[int, str]]:
    """我们的 `structure.json` → `[(level, title)]`。"""
    payload = json.loads((parse_dir / "structure.json").read_text(encoding="utf-8"))
    rows: list[tuple[int, str]] = []
    for head in payload.get("headings") or []:
        title = title_cleaner(str(head.get("title") or ""))
        level = int(head.get("canonical_level") or 0)
        if title and level > 0:
            rows.append((level, title))
    return rows


def hierarchy_and_nted(gold: list[tuple[int, str]], ours: list[tuple[int, str]]) -> dict:
    """官方口径的树编辑距离 → nTED（`zss` 就是他们用的库）。

    `clamp_upward_jumps`（层级跳变夹紧）与官方 `build_hierarchy_from_rows` 默认一致：
    层级一次最多比上一条深 1 层 —— 不夹紧的话，脏层级会造出官方口径之外的树。
    """
    try:
        # 官方 notebook 写的是 `from zss import simple_dist`，但装的 zss 1.2 里叫
        # `simple_distance`（同库不同版本的命名差异）—— 两个都试。
        from zss import Node
        try:
            from zss import simple_distance as _dist
        except ImportError:                       # pragma: no cover
            from zss import simple_dist as _dist
    except ImportError:
        return {"nted": None, "why": "没装 zss（pip install zss）"}

    def build(rows: list[tuple[int, str]]):
        root = Node("root")
        stack: list[tuple[Node, int]] = [(root, 0)]
        for level, title in rows:
            if level > stack[-1][1] + 1:
                level = stack[-1][1] + 1                 # 与官方 clamp 一致
            while stack and stack[-1][1] >= level:
                stack.pop()
            node = Node(title)
            (stack[-1][0] if stack else root).addkid(node)
            stack.append((node, level))
        return root

    def size(node) -> int:
        return 1 + sum(size(k) for k in getattr(node, "children", []) or [])

    t_gold, t_ours = build(gold), build(ours)
    dist = _dist(t_gold, t_ours)
    total = size(t_gold) + size(t_ours)
    # ⚠️ `zss` 返回 numpy 标量 —— 不转成 float 的话 `json.dumps` 会炸（实测踩过）
    return {"nted": round(float(dist) / total, 4) if total else None,
            "zss_distance": float(dist), "gold_nodes": size(t_gold),
            "our_nodes": size(t_ours)}


def eval_book(gold_path: Path, parse_dir: Path, *, tol: int = 2,
              content_only: bool = False) -> dict:
    gold = load_gold(gold_path)
    ours = load_ours(parse_dir)
    if content_only:
        # 两边都过滤：只比"正文标题"。金标把前置/后置材料当标题，我们故意不当 ——
        # 不过滤的话分数会混进"口径差异"，不是"解析能力差异"。
        gold = [r for r in gold if not is_non_teaching(r[1])]
        ours = [r for r in ours if not is_non_teaching(r[1])]
    out: dict = {
        "gold": str(gold_path), "parse_dir": str(parse_dir),
        "overall": compute_metrics([t for _, t in ours], [t for _, t in gold], tol=tol),
        "by_level": {},
    }
    for level in sorted({lv for lv, _ in gold} | {lv for lv, _ in ours}):
        out["by_level"][str(level)] = compute_metrics(
            [t for lv, t in ours if lv == level], [t for lv, t in gold if lv == level], tol=tol)
    out.update(hierarchy_and_nted(gold, ours))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="用 HiPS 的协议给我们的解析产物打分")
    ap.add_argument("--gold", required=True, help="HiPS 金标 .toc")
    ap.add_argument("--parse-dir", required=True)
    ap.add_argument("--json-out")
    ap.add_argument("--tol", type=int, default=2)
    ap.add_argument("--content-only", action="store_true",
                    help="两边都剔除前置/后置材料，只比正文标题（第二个口径）")
    args = ap.parse_args(argv)

    result = eval_book(Path(args.gold), Path(args.parse_dir), tol=args.tol,
                       content_only=args.content_only)
    result["content_only"] = bool(args.content_only)
    o = result["overall"]
    print("=" * 78)
    print("HiPS 外部基准：%s%s" % (Path(args.gold).stem,
                                  "（只比正文）" if args.content_only else ""))
    print("=" * 78)
    print("  我们的标题 %d 条 ｜ 金标 %d 条" % (o["pred"], o["gt"]))
    print("  精确   ：prec %.4f  rec %.4f（命中 %d）" % (o["prec"], o["rec"], o["exact_hit"]))
    print("  容差(%d)：tolprec %.4f  tolrec %.4f（命中 %d）"
          % (args.tol, o["tolprec"], o["tolrec"], o["tol_hit"]))
    print("  nTED   ：%s（zss 距离 %s ｜ 节点 %s + %s）"
          % (result.get("nted"), result.get("zss_distance"),
             result.get("gold_nodes"), result.get("our_nodes")))
    print("  分层：")
    for level, m in result["by_level"].items():
        if not m["gt"] and not m["pred"]:
            continue
        print("    lv%-2s 我们 %-4d 金标 %-4d ｜ prec %.3f rec %.3f ｜ tolprec %.3f tolrec %.3f"
              % (level, m["pred"], m["gt"], m["prec"], m["rec"], m["tolprec"], m["tolrec"]))
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("✓ %s" % args.json_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
