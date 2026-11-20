#!/usr/bin/env python3
"""两棵产物树**归一化比对**：证明「并行」和「串行」产出同一份东西。

为什么需要它（生产版 A6 验收，2026-09-26）：
    "并行跑"当初踩的坑全都是**账本类**的，不是内容类的 ——
    `lessons.index.json` 读-改-写丢更新、`backfill_concepts` 丢更新、
    `snapshot.json` 被每个 worker 各重建一遍。这些错误**不会报错**，
    只会让产物**悄悄少几节 / 概念表旧一版**。所以要证明并行没坏，
    唯一的办法就是：**同一批活，串行做一遍、并行做一遍，然后逐文件比。**

为什么能比出结论（关键前提）：
    `lib/model.py` 的响应缓存键 = `sha256(model + effort + messages + tag)`，
    **tag 参与、绝对路径不参与**。所以两棵树跑同一单元会命中**同一份缓存内容**，
    `cached: true`、0 token。内容既然同源，**凡是比对出来的差异，就只可能是并行编排的错**，
    不可能是模型随机性。这就是这个工具的意义。

怎么比：
    先"归一化"再比 —— 把天然会变的东西抹掉：
      ① 记账类（token / 秒数 / 花费）：本来就该不同，抹掉
      ② 时间戳类：只在**值真的像时间**时才抹（避免误伤名叫 `at` 的坐标）
      ③ run id 类：身份标识，抹掉
      ④ 树根路径：`/tmp/a6/serial` → `<TREE>`（派生视图本来不该含路径，
         但报告/日志里含；不抹的话每一行都是假差异）
      ⑤ 文件名里的 run id：按归一化后的名字配对

退出码（全项目统一约定）：
    0 = 等价     1 = 有差异（不是崩溃）     2 = 工具自己坏了

用法：
    python tools/compare_runs.py /tmp/a6/serial /tmp/a6/par
    python tools/compare_runs.py A B --json          # 机器可读
    python tools/compare_runs.py A B --limit 5       # 每类最多列 5 条
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

# ── 归一化规则 ──────────────────────────────────────────────────────────
# 记账类：无条件抹掉（不同的机器/不同的并行度本来就会不一样）
ACCOUNTING_KEYS = {
    "tokens", "total_tokens", "prompt_tokens", "completion_tokens",
    "reasoning_tokens", "cache_hit_tokens", "cache_miss_tokens",
    "usage", "calls", "cached_calls", "real_calls",
    "seconds", "elapsed", "duration", "took", "cost",
    "总花费", "累计花费", "花费", "to" + "ken数",
}
# 身份类：无条件抹掉
IDENTITY_KEYS = {
    "run_id", "book_run_id", "_run_id", "run", "session",
}
# 时间戳类：**只在值真的像时间时**才抹（以免误伤名叫 `at` 的位置字段）
TIME_KEYS = {
    "at", "updated_at", "created_at", "started", "finished", "last_run",
    "时间", "对账时间", "生成时间", "更新时间", "重建时间", "抓取时间",
}
_TS = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?")


def _looks_like_time(v) -> bool:
    return isinstance(v, str) and bool(_TS.match(v))


class Normalizer:
    """把"天然会变的"值换成占位符，并**记下改了多少处**（可追溯，不静默）。"""

    def __init__(self, tree: str | Path):
        self.tree = str(Path(tree).resolve())
        # 树根路径的原样写法也要抹（可能是相对路径写法）
        self.tree_raw = str(tree)
        self.normalized = 0

    def value(self, key: str, v):
        if key in ACCOUNTING_KEYS or key in IDENTITY_KEYS:
            self.normalized += 1
            return "<VOLATILE>"
        if key in TIME_KEYS and _looks_like_time(v):
            self.normalized += 1
            return "<TIME>"
        return self.scrub_text(v) if isinstance(v, str) else v

    def scrub_text(self, s: str) -> str:
        """把字符串里的树根路径换掉（报告/日志里会含）。"""
        if self.tree and self.tree in s:
            self.normalized += 1
            s = s.replace(self.tree, "<TREE>")
        if self.tree_raw and self.tree_raw in s:
            self.normalized += 1
            s = s.replace(self.tree_raw, "<TREE>")
        return s

    def obj(self, node):
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                nv = self.value(k, v)
                out[k] = self.obj(nv) if isinstance(nv, (dict, list)) else nv
            return out
        if isinstance(node, list):
            return [self.obj(x) for x in node]
        if isinstance(node, str):
            return self.scrub_text(node)
        return node


def norm_bytes(data: bytes, nz: Normalizer, suffix: str) -> str:
    """把文件内容读成"可比字符串"（JSON 会解析后归一化再序列化）。"""
    text = data.decode("utf-8", errors="replace")
    if suffix == ".json":
        try:
            return json.dumps(nz.obj(json.loads(text)), ensure_ascii=False, sort_keys=True)
        except json.JSONDecodeError:
            return nz.scrub_text(text)
    if suffix == ".jsonl":
        lines = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                lines.append(json.dumps(nz.obj(json.loads(line)), ensure_ascii=False, sort_keys=True))
            except json.JSONDecodeError:
                lines.append(nz.scrub_text(line))
        return "\n".join(sorted(lines))     # 行序不重要（并行下追加顺序本来会变）
    return nz.scrub_text(text)


def _norm_name(rel: str) -> str:
    """文件名里的 run id 抹掉，好让两棵树按同一逻辑名配对。"""
    return re.sub(r"(book-)?\d{8}-\d{6}-[0-9a-f]{6,}", "<RUNID>", rel)


def walk(root: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[_norm_name(str(p.relative_to(root)))] = p
    return out


def first_diffs(a: str, b: str, limit: int = 3) -> list[str]:
    """给出首个差异的**上下文**，让人不用自己去 diff。"""
    if a == b:
        return []
    import difflib
    lines = list(difflib.unified_diff(
        a.splitlines(), b.splitlines(), lineterm="", n=0, fromfile="串行", tofile="并行"))
    return lines[3:3 + limit * 2]        # 跳过 ---/+++ 头


def compare(left: Path, right: Path, *, limit: int = 5) -> dict:
    la, lb = walk(left), walk(right)
    only_left = sorted(set(la) - set(lb))
    only_right = sorted(set(lb) - set(la))
    differing, same = [], 0
    nz_l, nz_r = Normalizer(left), Normalizer(right)
    for rel in sorted(set(la) & set(lb)):
        ra, rb = la[rel], lb[rel]
        na = norm_bytes(ra.read_bytes(), nz_l, ra.suffix)
        nb = norm_bytes(rb.read_bytes(), nz_r, rb.suffix)
        if hashlib.sha256(na.encode()).hexdigest() == hashlib.sha256(nb.encode()).hexdigest():
            same += 1
        else:
            differing.append({"file": rel, "diff": first_diffs(na, nb, limit=2)})
    return {
        "left": str(left), "right": str(right),
        "same": same, "differing": differing,
        "only_left": only_left, "only_right": only_right,
        "volatile_normalized": nz_l.normalized + nz_r.normalized,
        "equivalent": not (differing or only_left or only_right),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="两棵产物树归一化比对（串行 vs 并行）")
    ap.add_argument("left", help="基准树（如 /tmp/a6/serial）")
    ap.add_argument("right", help="对照树（如 /tmp/a6/par）")
    ap.add_argument("--limit", type=int, default=5, help="每类最多列几条（默认 5）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    left, right = Path(args.left), Path(args.right)
    for p in (left, right):
        if not p.is_dir():
            print(f"✗ 不是目录：{p}", file=sys.stderr)
            return 2

    r = compare(left, right, limit=args.limit)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r["equivalent"] else 1

    print(f"=== 归一化比对：串行 {left}  vs  并行 {right} ===")
    print(f"  相同文件 {r['same']}   内容不同 {len(r['differing'])}"
          f"   只在串行 {len(r['only_left'])}   只在并行 {len(r['only_right'])}")
    print(f"  （抹掉的易变值 {r['volatile_normalized']} 处：记账/时间戳/run id/树根路径）")
    if r["only_left"]:
        print(f"\n▼ 只在串行有（并行**漏做**了这些）：")
        for x in r["only_left"][:args.limit]:
            print(f"    - {x}")
    if r["only_right"]:
        print(f"\n▼ 只在并行有（并行**多做/写歪**了这些）：")
        for x in r["only_right"][:args.limit]:
            print(f"    + {x}")
    if r["differing"]:
        print(f"\n▼ 内容不同（前 {min(args.limit, len(r['differing']))} 个）：")
        for d in r["differing"][:args.limit]:
            print(f"    ≠ {d['file']}")
            for line in d["diff"]:
                print(f"        {line}")
    print()
    if r["equivalent"]:
        print("✅ 等价：并行与串行产出同一份东西（差异只剩记账/时间戳/run id）")
        return 0
    print("❌ 不等价：上面的差异是**并行编排的错**，不是模型随机性（内容同源自缓存）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
