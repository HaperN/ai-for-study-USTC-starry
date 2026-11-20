#!/usr/bin/env python3
"""解析层**体检表**：一本书解析得对不对，一张表看完（**0 token，只读产物**）。

用户 2026-09-27 的测试流程：每给一本新书，先只跑解析、不跑整本，然后看这张表。

    python tools/parse_report.py data/parse/<书名>            # 人读
    python tools/parse_report.py data/parse/<书名> --json      # 机器可读

查什么（每条都能当场判、不需要人翻书）：

| 检查 | 判据 | 不对的话意味着什么 |
|---|---|---|
| **章数对账** | 解析出的 1 级标题 vs **PDF 内嵌书签**（没有书签就用印刷目录页） | 结构层把目录/答案/习题当成章了 |
| **教案单元里的垃圾** | 单元标题里含 `……(页码)` / 习题语气 / 前言类 | 区域排除没生效 |
| **习题语气残留** | 以 求/证明/设/… 开头的单元占比 | 内联题干判据没覆盖到 |
| **原书目录资料** | `source_materials.json` 的资料区、定位状态与来源有效性 | 目录资料未定位或来源已变化 |
| **原文完整性** | `original_text/` 字数 vs `document.md` 字数 | 区域排除误删了正文 |
| **书签乱码** | 书签里还有非中文乱码就是没修 | `repair_pdf_text` 没接上 |
| **目录↔正文绑定**（新） | `body.json` 的每个 unit 有没有 `toc_node`；`chapters.json` 的节是不是都绑上了 | 目录层与正文层脱钩（重排一次就挂错节） |
| **账本**（新） | `toc.v2.json` 的 `unresolved`/`disagreements`/`suspicious`/`unconfirmed` 逐条能不能解释 | 有东西被静默丢掉（这次重构要治的病，见 H6） |

⚠️ **新检查只在有 `toc.v2.json` / `body.json` 时才算**（这两个是重构新产物）。
老产物目录没有它们时，这两档检查跳过。原书资料未准备会单独列明；不再读取旧题目或答案配对索引。

退出码：`0` 全部通过 / `1` 有可疑项（不是崩溃） / `2` 工具自己坏了。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# 章名对账要用**同一个标题比较口径**（全程序唯一一份，见 structure.norm_title/title_similarity）
from blocks.parse.structure.structure import title_similarity  # noqa: E402
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ⚠️ **标题口径只有一处**（结构层）：体检表和目录对账必须用同一个归一函数，
#    否则会出现"体检表说对不上、对账说对上了"这种自相矛盾（实测踩过：
#    书签写 `Gauss－Seidel`、目录页写 `Gauss-Seidel`）。
from blocks.parse.structure.structure import (          # noqa: E402
    norm_title as _norm, looks_like_exercise, EXERCISE_MARK)
#: 目录条目尾巴（`…… (107)` / `…… (Ⅰ)`）——出现就说明目录串进正文了
TOC_TAIL = re.compile(r"[…⋯]\s*[（(]\s*(?:\d{1,4}|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+)\s*[）)]\s*$")
FRONT_WORDS = ("内容简介", "图书在版编目", "总序", "前言", "目 录", "目次")


def _load(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _source_material_report(parse_dir: Path) -> dict:
    path = parse_dir / 'source_materials.json'
    payload = _load(path)
    rows = payload.get('materials') if isinstance(payload, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return {'状态': 'invalid' if path.exists() else 'missing', '资料区': None,
                '习题区': None, '答案区': None, '已记录': None, '未定位': []}
    from aiforstudy.core.source_materials import is_current
    return {'状态': 'current' if is_current(parse_dir) else 'stale', '资料区': len(rows),
            '习题区': sum(row.get('kind') == 'exercise' for row in rows),
            '答案区': sum(row.get('kind') == 'answers' for row in rows),
            '已记录': sum(row.get('state') == 'recorded' for row in rows),
            '未定位': [{key: row.get(key) for key in ('id', 'title', 'reason')}
                       for row in rows if row.get('state') == 'unresolved']}


def report(parse_dir: Path) -> dict:
    parse_dir = Path(parse_dir)
    chapters = (_load(parse_dir / "chapters.json") or {})
    units = chapters.get("lesson_units") or []
    structure = (_load(parse_dir / "structure.json") or {})
    stats = structure.get("stats") or {}
    toc = (_load(parse_dir / "toc.json") or {})

    # ── 章数对账 ─────────────────────────────────────────────────────────
    claimed = [_norm(t) for t in (toc.get("chapters") or [])]
    got_level1 = [str(h.get("title") or "") for h in (structure.get("headings") or [])
                  if h.get("canonical_level") == 1]
    detected = [_norm(t) for t in got_level1]
    used = set()
    missing = []
    for want in claimed:
        hit = next((i for i, got in enumerate(detected)
                    if i not in used and (want in got or got in want)), None)
        if hit is None:
            # ⚠️ 退一步用**标题相似度**匹配（2026-09-29 实测抓出）：
            # 模型视觉复核会把形近字/OCR 错字改对（《电路分析基础》章 4：电路 → 网络），
            # 而基线（adapt 的 chapters）还是改前的写法 —— 逐字/包含匹配会把它算成
            # "目录里有、没解析出来" + "解析出来、目录里没有"两条**假警报**。
            # 阈值 0.7 与目录层"还算同一节"的口径同源（改一个字的相似度通常 ≥0.85）。
            hit = next((i for i, got in enumerate(detected)
                        if i not in used and title_similarity(want, got) >= 0.7), None)
        if hit is None:
            missing.append(want)
        else:
            used.add(hit)
    extra = [got for i, got in enumerate(detected) if i not in used]

    # ── 单元里的垃圾 ─────────────────────────────────────────────────────
    def unit_flags(row: dict) -> list[str]:
        title = str(row.get("title") or "")
        flags = []
        if TOC_TAIL.search(title):
            flags.append("目录条目")
        if looks_like_exercise(title.strip()):
            flags.append("习题语气")
        if EXERCISE_MARK.match(title.strip()):
            flags.append("习题区标记")
        if any(w in title for w in FRONT_WORDS):
            flags.append("章前材料")
        return flags
    junk = {row["unit_id"]: unit_flags(row) for row in units if unit_flags(row)}

    # ── 原文完整性 ───────────────────────────────────────────────────────
    doc = parse_dir / "document.md"
    doc_chars = len(doc.read_text(encoding="utf-8")) if doc.is_file() else 0
    text_dir = parse_dir / "original_text"
    files = sorted(text_dir.glob("*.md")) if text_dir.is_dir() else []
    unit_chars = sum(len(f.read_text(encoding="utf-8")) for f in files)

    skeleton = stats.get("skeleton") or {}
    ledger = _ledger_checks(parse_dir)
    toc_source = str(toc.get("source") or ("printed-toc" if toc else "（没有 toc.json）"))
    outline = toc.get("entries") or []
    garbled = [str(e.get("text")) for e in outline
               if re.search(r"[¿ÕÏÁÐÎµÄ¸öÊý]", str(e.get("text") or ""))][:3]

    return {
        "书": parse_dir.name,
        "目录来源": toc_source,
        "目录条目数": len(outline),
        "对账": {"目录声明章数": len(claimed), "解析出章数": len(detected),
               "对上的": len(used), "目录有但没解析出来": missing, "解析出但目录没有": extra},
        "教案单元": len(units),
        "单元里的垃圾": junk,
        "内联题干被剔": stats.get("exercise_items_marked"),
        "骨架": {"目录条目": skeleton.get("目录条目"),
               "骨架层级": skeleton.get("骨架层级"),
               "剔除": [x.get("title") for x in (skeleton.get("剔除") or [])],
               "救回": skeleton.get("救回") or [],
               "目录未配": [x.get("text") for x in (skeleton.get("目录未配") or [])],
               "目录未配-非正文": [x.get("text")
                                   for x in (skeleton.get("目录未配-非正文") or [])]},
        "区域排除": {"行": stats.get("excluded_region_lines"),
                   "页数": len(stats.get("excluded_region_pages") or [])},
        "原书资料": _source_material_report(parse_dir),
        "原文": {"document.md 字数": doc_chars, "original_text 字数": unit_chars,
               "文件数": len(files)},
        "书签乱码残留": garbled,
        "新产物": ledger,
    }


def _ledger_checks(parse_dir: Path) -> dict:
    """重构新产物的两档检查：**绑定完整性**与**账本**（只读、缺产物就跳过）。

    为什么单独一档而不是并进老检查：老检查读的是 `chapters.json`/`toc.json`（schema 1.0），
    新的读 `body.json`/`toc.v2.json`（schema 2.0）。**两份产物并存**是为了对比
    "新的比旧的好在哪"，所以报告也要能同时显示两边，而不是谁覆盖谁。
    """
    out: dict = {"有 toc.v2": False, "有 body": False}
    toc_path = parse_dir / "toc.v2.json"
    body_path = parse_dir / "body.json"
    if toc_path.is_file():
        toc = _load(toc_path) or {}
        meta = toc.get("_meta") or {}
        counts = meta.get("counts") or {}
        out.update({
            "有 toc.v2": True,
            "confidence": meta.get("confidence"),
            "nodes": len(toc.get("nodes") or []),
            "counts": counts,
            "unresolved": [u.get("why") for u in (toc.get("unresolved") or [])],
            "disagreements": [f"{d.get('kind')}: {d.get('title')} —— {d.get('why')}"
                              for d in (toc.get("disagreements") or [])],
            "suspicious": [f"{s.get('toc_id')} {s.get('title')}：{s.get('why')}"
                           for s in (toc.get("suspicious") or [])],
            "契约问题": _contract_problems(toc),
        })
    if body_path.is_file():
        body = _load(body_path) or {}
        counts = (body.get("_meta") or {}).get("counts") or {}
        units = body.get("units") or []
        out.update({
            "有 body": True,
            "units": len(units),
            # ── 空真守卫（2026-09-28 核验抓出）──────────────────────────────
            # `units=0` 时"每个 unit 都有 toc_node"恒真 —— 实测一本**从没跑过
            # 正文解析**的新书（units=0 / empty_nodes=171）因此被判 ✅，
            # 报告把它写成"目录层全部跑通"。这是 F 类错误（判据太松 → 假过），
            # 所以把"空 body"单独记出来，并让它算可疑（退出码 1）。
            "body_is_empty": not units,
            "units_without_toc_node": [u.get("unit_id") for u in units
                                       if not u.get("toc_node")],
            "empty_nodes": [f"{e.get('toc_node')} {e.get('title')}"
                            for e in (body.get("empty_nodes") or [])],
            "orphans": [f"{o.get('title')}（{o.get('why')}）"
                        for o in (body.get("orphans") or [])],
            "unconfirmed": [f"{u.get('toc_node')} {u.get('title')}"
                            for u in (body.get("unconfirmed") or [])],
            "anchor_conflicts": [f"{c.get('toc_id')} 目录说 {c.get('toc_number')} / "
                                 f"正文锚点 {c.get('body_anchor')}"
                                 for c in (body.get("anchor_conflicts") or [])],
            "counts_body": counts,
        })
    return out


def _contract_problems(toc: dict) -> list[str]:
    """`toc.v2.json` 过不过契约（字段白名单/账目算得上/父子不断链）。"""
    try:
        import sys
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from blocks.parse.toc.contract import validate_toc
        return validate_toc(toc, strict=False)
    except Exception as exc:                              # noqa: BLE001
        return [f"契约校验跑不起来：{type(exc).__name__}: {exc}"]


def _ok(cond: bool) -> str:
    return "✅" if cond else "⚠️"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="解析层体检表（0 token，只读产物）")
    ap.add_argument("parse_dir")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    parse_dir = Path(args.parse_dir)
    if not (parse_dir / "chapters.json").is_file():
        # 老检查要 `chapters.json`；但**新产物**（toc.v2 / body）不需要它 ——
        # 实测新书还没跑 MinerU/导出，只有目录层产物，这时不该整个报"✗"：
        # 那会让人以为"目录层白跑了"。有目录层产物就把新检查报出来。
        ledger = _ledger_checks(parse_dir)
        if ledger.get("有 toc.v2") or ledger.get("有 body"):
            print(f"=== 解析层体检：{parse_dir.name}（只有目录层产物）===")
            print("  ⚠️ 没有 chapters.json —— 老检查（章数对账/教案单元/原文）跑不了，"
                  "先跑 MinerU 适配 + 导出")
            # ⚠️ 新检查的问题**必须影响退出码**（2026-09-28 核验抓出）：
            # 原来这里写死 `return 0`，于是"units=0（一本书一个单元都没绑上）"
            # 也能拿到退出码 0，CI 会全绿放行。这就是 F 类"假过"。
            _, new_bad = _print_new_checks(ledger)
            return 1 if new_bad else 0
        print(f"✗ {parse_dir} 里没有 chapters.json（先跑解析链）", file=sys.stderr)
        return 2
    r = report(parse_dir)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0

    c = r["对账"]
    print(f"=== 解析层体检：{r['书']} ===")
    print(f"  目录来源：{r['目录来源']}（{r['目录条目数']} 条）")
    print(f"  {_ok(not c['目录有但没解析出来'] and not c['解析出但目录没有'])} "
          f"章数对账：目录说 {c['目录声明章数']} 章 / 解析出 {c['解析出章数']} 章，"
          f"对上 {c['对上的']}")
    if c["目录有但没解析出来"]:
        print(f"      ⚠️ 目录里有、没解析出来：{c['目录有但没解析出来']}")
    if c["解析出但目录没有"]:
        print(f"      ⚠️ 解析出来、目录里没有：{c['解析出但目录没有']}")
    print(f"  {_ok(not r['单元里的垃圾'])} 教案单元 {r['教案单元']} 个"
          f"，其中可疑 {len(r['单元里的垃圾'])} 个")
    for unit, flags in list(r["单元里的垃圾"].items())[:8]:
        print(f"      ⚠️ {unit}：{'/'.join(flags)}")
    print(f"  内联题干被剔 {r['内联题干被剔']} 个 ｜ "
          f"区域排除 {r['区域排除']['行']} 行 / {r['区域排除']['页数']} 页")
    sk = r["骨架"]
    if sk.get("目录条目"):
        print(f"  🗂 目录当骨架（{sk['目录条目']} 条目录，管到层级 {sk.get('骨架层级')}）：剔掉目录里没有的 "
              f"{len(sk['剔除'])} 个 ｜ 字面不符但救回 {len(sk['救回'])} 个 ｜ "
              f"目录里有、正文没配上 {len(sk['目录未配'])} 条"
              f"（另有非正文 {len(sk.get('目录未配-非正文') or [])} 条：前言/附录/习题集）")
        for t in sk["剔除"][:6]:
            print(f"      ✂ {t[:66]}")
        for x in sk["救回"][:4]:
            print(f"      ~ 救回 {x['title'][:30]} ← 目录「{x['toc'][:24]}」（相似度 {x['similarity']}）")
        for t in sk["目录未配"][:6]:
            print(f"      ? 目录未配 {t[:56]}")
    materials = r['原书资料']
    state = {'current': '来源有效', 'stale': '来源过期或产物被改动',
             'missing': '未准备 source_materials.json', 'invalid': 'source_materials.json 无法读取'}[materials['状态']]
    if materials['资料区'] is None:
        print(f"  原书目录资料：{state}")
    else:
        print(f"  原书目录资料 {materials['资料区']} 区 ｜ 习题区 {materials['习题区']} ｜ "
              f"答案区 {materials['答案区']} ｜ 已记录 {materials['已记录']} 区 ｜ "
              f"未定位 {len(materials['未定位'])} 区 ｜ {state}")
        for row in materials['未定位']:
            print(f"      ? {row['id']} {row['title']}：{row['reason']}")
    o = r["原文"]
    print(f"  {_ok(bool(o['original_text 字数']))} 原文：document.md {o['document.md 字数']:,} 字 "
          f"→ original_text {o['original_text 字数']:,} 字（{o['文件数']} 个文件）")
    if r["书签乱码残留"]:
        print(f"      ⚠️ 书签还有乱码：{r['书签乱码残留']}")

    new_ok, new_bad = _print_new_checks(r["新产物"])
    bad = (bool(r["单元里的垃圾"]) or bool(c["目录有但没解析出来"] and c["目录声明章数"])
           or materials['状态'] in {'invalid', 'stale'})
    return 1 if (bad or new_bad) else 0


def _print_new_checks(new: dict) -> tuple[bool, bool]:
    """打印"绑定完整性"与"账本"两档新检查；返回 `(有没有新产物, 有没有问题)`。"""
    if not new.get("有 toc.v2") and not new.get("有 body"):
        return False, False
    print("  ── 重构新产物（toc.v2 / body）──")
    bad = False
    if new.get("有 toc.v2"):
        counts = new.get("counts") or {}
        problems = new.get("契约问题") or []
        print(f"  {'✅' if not problems else '⚠️'} 目录契约 v2：confidence={new.get('confidence')}"
              f"  nodes={new.get('nodes')}  非正文={counts.get('non_teaching')}"
              f"  未确认={counts.get('unconfirmed')}")
        if problems:
            bad = True
            for line in problems[:6]:
                print(f"      ⚠️ 契约：{line}")
        # **账本**：每一项都要有名字、说得清
        for label, rows in (("抽不出来/落不了页", new.get("unresolved") or []),
                            ("源间冲突", new.get("disagreements") or []),
                            ("可疑", new.get("suspicious") or [])):
            mark = "✅" if not rows else "ℹ️"
            print(f"  {mark} 账本·{label}：{len(rows)} 条")
            for row in rows[:6]:
                print(f"      · {str(row)[:110]}")
    if new.get("有 body"):
        counts = new.get("counts_body") or {}
        missing = new.get("units_without_toc_node") or []
        anchor = new.get("anchor_conflicts") or []
        empty_body = bool(new.get("body_is_empty"))
        # 防重复断言（方案「乙」）：父节点的正文里不许含子节点的正文。
        # **必须为 0** —— 不是 0 就说明同一段正文被父、子各算一遍（会重复烧 token）。
        overlaps = counts.get("content_overlaps", 0)
        undeclared = counts.get("undeclared_units", 0)
        shells = counts.get("sections_carrying_in_children", 0)
        units_n = counts.get("units", new.get("units") or 0)
        # 判据分三档（2026-09-28 核验后重定）：
        #   * **一个都没绑上** → ⚠️ 且算可疑（`units=0` 不许空真通过）
        #   * **锚点错位** → ℹ️ 只报账：目录层已经以书的目录为准给出了正确编号，
        #     错位来自**下游旧产物**（`chapters.json` 的 anchor 是老结构层推的），
        #     属于"记了账、还没结账"，交给下游用 `toc.v2` 的编号覆盖即可 —— 不算目录层失败
        #   * 其余 → ✅
        if empty_body:
            mark, head = "⚠️", f"**一个单元都没绑上**（units=0，空节={counts.get('empty_nodes')}）"
        elif missing:
            mark, head = "⚠️", f"units={units_n}，其中 {len(missing)} 个没有 toc_node"
        else:
            mark, head = "✅", (f"units={units_n} 每个都有 toc_node=True "
                                f"｜ 空节={counts.get('empty_nodes')} "
                                f"｜ 贴不上={counts.get('orphans')}")
        print(f"  {mark} 目录↔正文绑定：{head}")
        if undeclared:
            print(f"      ℹ️ 内容承载节点 {undeclared} 个（目录没声明、但承载正文 → "
                  f"已贴到最近的已声明祖先）｜ 其中壳节 {shells} 个")
        # 防重复断言：**不是 0 就是真问题**（同一段正文被算两遍），所以算可疑
        if overlaps:
            bad = True
            print(f"      ⚠️ **防重复断言命中 {overlaps} 处**：父节点的正文里出现了子节点的正文"
                  f" —— 同一段内容会被父、子各讲一遍（要修绑定，不许静默放过）")
        else:
            print("      ✅ 防重复断言：0 处重叠（父子里没有整段重复）")
        if empty_body:
            bad = True
            print("      ⚠️ 这本书还没跑 ① 取书 + ② 正文解析（没有 document.md/original_text），"
                  "先跑 ingest_book.py 再跑 `cli body`")
        elif anchor:
            print(f"      ℹ️ 锚点错位={len(anchor)}（目录层的编号已按书自己的目录给出；"
                  f"错位来自下游旧产物的 anchor，需下游覆盖）")
        else:
            print("      ℹ️ 锚点错位=0")
        if missing:
            bad = True
            print(f"      ⚠️ 这些 unit 没绑上 toc_node：{missing[:6]}")
        for row in (new.get("empty_nodes") or [])[:6]:
            print(f"      ○ 目录有正文没有：{row}")
        for row in (new.get("orphans") or [])[:6]:
            print(f"      ○ 正文有目录没有（一个字没删）：{row}")
        for row in (new.get("unconfirmed") or [])[:6]:
            print(f"      ~ 未确认（原样带下去）：{row}")
    return True, bad


if __name__ == "__main__":
    sys.exit(main())
