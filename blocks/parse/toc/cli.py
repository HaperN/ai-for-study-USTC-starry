"""目录层命令行：`python -m blocks.parse.toc.cli <子命令>`。

| 子命令 | 里程碑 | 产出 | 花钱 |
|---|---|---|---|
| `extract` | M1 | `toc.draft.json`（三源抽取的原始结果，**人可读的对账单**） | 0 |
| `build` | M1 | `toc.json`（v2 终稿，带 confidence/账目） | 0 |
| `preflight` | M2 | `toc.preflight.json` | 0 |
| `review` | M3 | `toc.review.json`（建议稿） | 有（每片一次模型调用） |
| `apply` | M3 | 写回 `toc.json`，逐条记 `revisions` | 0 |
| `preview` | M5 | `toc.preview.json`（一屏给人看） | 0 |
| `body` | M4 | `body.json`（正文绑定到 toc_id） | 0 |

## 两条纪律

1. **绝不覆盖既有产物**：`toc.json` 这个文件名被 `write_artifact()` 挡着（H2/H9）。
   新块写的是 `toc.draft.json` / `toc.review.json` / `toc.preview.json` / `body.json`；
   v2 的目录终稿写 **`toc.v2.json`**，跟老 `toc.json` 并存对比。
2. **`--dry-run` 一定可用**：`apply` 不带 `--write` 就只打印会改成什么样。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import body as body_mod
from . import toc_preflight as preflight_mod
from . import preview as preview_mod
from . import reconcile, sources, toc_review as review_mod
from .contract import (GENERATOR, ROOT, SCHEMA_VERSION, read_json, sha256_file,
                       validate_toc, write_artifact, write_json)

TOC_V2_NAME = "toc.v2.json"
#: 复核建议稿：**真实结论**与**dry-run 稿**分两个文件名（2026-09-28 核验抓出）。
#: 合用一个名字时，一次 dry-run 就会把"真花过钱"的结论覆盖成 `model_calls: 0`，
#: 审计轨迹随之消失 —— 所以两者必须分开，且真实结论永不被 dry-run 覆盖。
REVIEW_NAME = "toc.review.json"
REVIEW_DRY_NAME = "toc.review.dry.json"


def _book_dir(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


#: 六本书的名字对不上文件名时的映射（**只在这一个地方**）。
#: 为什么需要它：`linear-algebra-v2` / `complex-analysis-v2` 是"重跑产物"的 slug，
#: 它们入库时是拿 `--pdf` 显式指定的，`ingest.json` 里没留 PDF 路径；
#: 靠文件名 glob 也猜不出来。所以这里把"产物目录 → 教材文件"写死一份可版本控制的对照。
SLUG_PDF = {
    "linear-algebra-v2": "../教材/线性代数教材.pdf",
    "complex-analysis-v2": "data/raw/复变函数.pdf",
    "numerical-methods": "data/raw/数值计算方法与算法.pdf",
    "math-analysis-2": "data/raw/数分讲义第二册.pdf",
}


def _pdf_for(parse_dir: Path) -> Path | None:
    """这次要读哪份 PDF。**四个来源按优先级**，都在这里，别处不许再写一份：

    0. **耐久指针 `pdf.path`**（2026-09-29 加）：`ingest.json` 是**产物**、清空重建时会被删，
       而 PDF 是书签（目录权威）与目录页截图的唯一来源 —— 清空重建后若不认这个指针，
       目录会**悄悄退化**成"只有印刷目录+正文形状"、复核送图 0 张（实测 D-31）。
    1. 本块自己记的 `toc.pdf.json`（上次用哪份，下次还用哪份 —— 可核对、可重跑）；
    2. `ingest.json` 里记的（MinerU 路线入库时会写）；
    3. `SLUG_PDF` 对照表（v2 那两本没写 ingest）；
    4. `data/raw/<slug>*.pdf` 文件名前缀兜底。
    """
    pointer = parse_dir / "pdf.path"
    if pointer.is_file():
        try:
            raw = pointer.read_text(encoding="utf-8").strip()
        except OSError:
            raw = ""
        if raw:
            path = Path(raw)
            if not path.is_absolute():
                path = ROOT / path
            if path.is_file():
                return path
    marker = parse_dir / "toc.pdf.json"
    if marker.is_file():
        try:
            raw = str(read_json(marker).get("pdf") or "")
        except (OSError, ValueError):
            raw = ""
        if raw:
            path = Path(raw)
            if not path.is_absolute():
                path = ROOT / path
            if path.is_file():
                return path
    ingest = parse_dir / "ingest.json"
    if ingest.is_file():
        try:
            raw = str(read_json(ingest).get("pdf") or "")
        except (OSError, ValueError):
            raw = ""
        if raw:
            path = Path(raw)
            if not path.is_absolute():
                path = ROOT / path
            if path.is_file():
                return path
    mapped = SLUG_PDF.get(parse_dir.name)
    if mapped and (ROOT / mapped).is_file():
        return ROOT / mapped
    for candidate in sorted((ROOT / "data" / "raw").glob(f"{parse_dir.name}*.pdf")):
        return candidate
    return None


def _remember_pdf(parse_dir: Path, pdf: Path | None) -> None:
    """把"这次用的是哪份 PDF"记进 `toc.pdf.json`（相对路径，可版本控制）。"""
    if pdf is None:
        return
    try:
        shown = str(Path(pdf).resolve().relative_to(ROOT))
    except ValueError:
        shown = str(pdf)
    write_json(parse_dir / "toc.pdf.json", {
        "_meta": {"file_type": "toc_pdf", "schema_version": "1.0",
                  "generator": GENERATOR + ".cli",
                  "field_notes": {
                      "pdf": "这个解析产物目录对应哪份 PDF（相对仓库根）；"
                             "预检/复核/default 都读它，避免每次重猜"}},
        "pdf": shown})


def _sources_for(parse_dir: Path, pdf: Path | None):
    """跑三源抽取，返回 `(bookmarks, printed, body, page_map, old_toc)`。"""
    old_toc = {}
    toc_path = parse_dir / "toc.json"
    if toc_path.is_file():
        try:
            old_toc = read_json(toc_path)
        except (OSError, ValueError):
            old_toc = {}

    printed_rows = old_toc.get("printed_entries") or []
    printed = sources.extract_printed(printed_rows)
    if not printed.candidates:
        # 老产物没有 `printed_entries`（或为空）时，退回"从目录页文字里现抽"
        printed = sources.extract_printed(sources.printed_candidates_from_toc(old_toc))
    if not printed.candidates and pdf is not None:
        # **新书**（还没跑 MinerU）走到这里：直接从 PDF 的目录页读文字。
        # 目录页从预检结果拿（有就拿来，没有再按密度现找），不另建一套判据。
        pages = _preflight_toc_pages(parse_dir) or _find_toc_pages(pdf)
        rows = sources.printed_rows_from_pdf(pdf, pages)
        printed = sources.extract_printed(rows)
        if printed.candidates:
            printed.detail["pages"] = pages
            printed.detail["from"] = "pdf-text-layer"

    raw_bookmarks = sources.read_raw_bookmarks(pdf)
    bookmarks = sources.extract_bookmarks(pdf, printed=printed.candidates)
    page_map = sources.PageMap()
    page_map.learn(raw_bookmarks, printed.candidates,
                   shift=bookmarks.detail.get("shift") if bookmarks.available else None)
    # 方案 A：body 源优先吃 MinerU 的 title 块（带行号/页码/区域）；
    # 没有 headings.json 的老产物才退回"从 document.md 文字形状猜"（D-7）。
    body = sources.extract_body_titles(parse_dir)
    if not body.candidates:
        body = sources.extract_body_shapes(parse_dir / "document.md")
    return bookmarks, printed, body, page_map, old_toc


def _preflight_toc_pages(parse_dir: Path) -> list[int]:
    """预检结果里的目录页（有就用，省得再扫一遍）。"""
    path = parse_dir / "toc.preflight.json"
    if not path.is_file():
        return []
    try:
        found = read_json(path)
    except (OSError, ValueError):
        return []
    try:
        return [int(p) for p in (found.get("toc_pages") or {}).get("pdf_pages") or []]
    except (TypeError, ValueError):
        return []


def _find_toc_pages(pdf: Path) -> list[int]:
    """现找目录页（预检还没跑过时用）。找不到就返回空 —— 不猜页。"""
    try:
        import pymupdf
    except ImportError:
        return []
    from .toc_preflight import find_toc_pages
    try:
        doc = pymupdf.open(str(pdf))
    except Exception:                                     # noqa: BLE001
        return []
    try:
        return list(find_toc_pages(doc)["pdf_pages"])
    finally:
        doc.close()


def _load_toc_v2(parse_dir: Path) -> dict:
    path = parse_dir / TOC_V2_NAME
    if not path.is_file():
        raise SystemExit(f"✗ {path} 不存在：先跑 `cli.py build {parse_dir}`")
    return read_json(path)


def _slug_title(parse_dir: Path, old_toc: dict) -> tuple[str, str]:
    title = str(old_toc.get("book_title") or "")
    if not title:
        try:
            title = str(read_json(parse_dir / "chapters.json").get("book", {}).get("title") or "")
        except (OSError, ValueError, AttributeError):
            title = ""
    return parse_dir.name, title or parse_dir.name


def cmd_extract(args) -> int:
    parse_dir = _book_dir(args.parse_dir)
    pdf = Path(args.pdf) if args.pdf else _pdf_for(parse_dir)
    _remember_pdf(parse_dir, pdf)
    bookmarks, printed, body, page_map, old_toc = _sources_for(parse_dir, pdf)
    slug, title = _slug_title(parse_dir, old_toc)
    payload = {
        "_meta": {"file_type": "toc_draft", "schema_version": SCHEMA_VERSION,
                  "generator": GENERATOR + ".extract", "slug": slug,
                  "pdf": str(pdf) if pdf else None,
                  "pdf_sha256": sha256_file(pdf) if pdf else "",
                  "book_title": title},
        "sources": {
            "pdf-bookmarks": {**bookmarks.as_dict(),
                              "candidates": [c.as_dict() for c in bookmarks.candidates]},
            "printed-toc": {**printed.as_dict(),
                            "candidates": [c.as_dict() for c in printed.candidates]},
            "body-heading-shapes": {**body.as_dict(),
                                    "candidates": [c.as_dict() for c in body.candidates]},
        },
        "page_map": page_map.report(),
    }
    out = parse_dir / "toc.draft.json"
    write_json(out, payload)
    print(f"✓ {out}")
    for name, result in (("书签", bookmarks), ("印刷目录", printed), ("正文形状", body)):
        print(f"  {name}: {len(result.candidates)} 条候选，"
              f"{len(result.unresolved)} 条记不下来"
              + (f"（{result.reason}）" if result.reason else ""))
    print(f"  页码偏移：{page_map.offset}（{page_map.method}，证据 {page_map.clean_votes} 条）")
    return 0


def _write_alignment(parse_dir: Path, toc: dict) -> bool:
    """**方案 A 的落点**：把"目录（权威）× MinerU 标题候选（正文）"的对齐算出来落盘。

    为什么放在 `build` 里：它只依赖 `toc.v2.json` + `headings.json`（两份都在），
    **0 token**，而且跟着 build 走就**永不过期**（目录变了它必然重算）。

    产出两份：

    * `toc.alignment.json` —— 对齐结果 + 账本（配上/目录未配/正文多出/怎么配的）；
    * `toc.decisions.json` —— **给结构层的标题判定**（`line/level/number/title/toc_id`）。
      结构层拿到它就不再"用编号正则推层级"，层级直接来自书的目录。
    """
    if not (parse_dir / "headings.json").is_file():
        print("  （没有 headings.json：跳过方案 A 对齐，结构层只能走老的编号规则）")
        return True
    from . import align as align_mod
    try:
        alignment = align_mod.align(toc, parse_dir)
        write_artifact(parse_dir / "toc.alignment.json", alignment)
        dropped: list = []
        write_artifact(parse_dir / "toc.decisions.json", {
            "_meta": {"file_type": "toc_headings", "schema_version": "1.0",
                      "generator": "aiforstudy.parse.toc.align",
                      "field_notes": {
                          "decisions[]": "给结构层的标题判定：line/level/number/title/toc_id",
                          "怎么用": "blocks.parse.structure 的 authority 参数"
                                    "（CLI：--authority toc.decisions.json）"}},
            "decisions": align_mod.heading_decisions(alignment, dropped=dropped),
            # 丢了什么要说得出来（R-b）：目前只有 D-22 那类"编号与已声明节相同的书眉/重复条"
            "dropped": dropped})
        c = alignment["_meta"]["counts"]
        print(f"  方案 A 对齐：目录 {c['authority']} × 候选 {c['candidates']} → 配上 "
              f"{c['matched']}（exact {c['by_how']['exact']} / substring "
              f"{c['by_how']['substring']} / fuzzy {c['by_how']['fuzzy']}）"
              f" ｜ 目录未配 {c['unmatched_authority']} ｜ 正文多出 {c['extra_candidates']}")
        return True
    except Exception as exc:                       # noqa: BLE001
        # ⚠️ **失败时必须把旧产物清掉**（D-21，2026-09-29 抓到）：
        # 原来是"打印一句警告就当没事" —— 于是**上一次的 `toc.decisions.json`
        # 继续留在盘上冒充新鲜产物**，下游拿着旧标题一路跑到底（实测：
        # 复核把章 4 改成「双口网络」、结构层却还用着旧的「双口电路」）。
        # 现在：删掉旧文件 + 明确报错，宁可不产出，也不产出"看着新鲜其实是旧的"。
        for name in ("toc.alignment.json", "toc.decisions.json"):
            try:
                (parse_dir / name).unlink(missing_ok=True)
            except OSError:
                pass
        print(f"  ⛔ 方案 A 对齐没跑成（已删掉旧的决策文件，避免下游用到过期数据）："
              f"{type(exc).__name__}: {exc}")
        return False


AUTHORITY_INPUTS = ('toc.v2.json', 'toc.excluded.json', 'toc.pdf.json',
                    'document.md', 'headings.json')
AUTHORITY_OUTPUTS = ('toc.alignment.json', 'toc.decisions.json', 'toc.regions.json')
DERIVED_PRODUCTS = (
    'toc.authority.json', 'toc.alignment.json', 'toc.decisions.json', 'toc.regions.json',
    'structure.json', 'structured.md', 'structured.anchor3.md', 'body.json',
    'chapters.json', 'concepts.json', 'concepts.parts', 'original_text', '_norm',
    'exercises.json',
)


def invalidate_derived(parse_dir: Path) -> list[str]:
    """Invalidate only derived files in the explicitly supplied mutable parse copy."""
    removed = []
    for name in DERIVED_PRODUCTS:
        path = Path(parse_dir) / name
        if path.is_symlink() or path.is_file():
            path.unlink()
            removed.append(name)
        elif path.is_dir():
            shutil.rmtree(path)
            removed.append(name)
    return removed


def _authority_hashes(parse_dir: Path, names) -> dict:
    return {name: sha256_file(Path(parse_dir) / name)
            if (Path(parse_dir) / name).is_file() else None for name in names}


def _authority_protocol() -> dict:
    return {name: sha256_file(Path(__file__).parent / name)
            for name in ('cli.py', 'align.py', 'regions.py')}


def authority_is_current(parse_dir: Path) -> bool:
    path = Path(parse_dir) / 'toc.authority.json'
    if not path.is_file():
        return False
    try:
        record = read_json(path)
        inputs = _authority_hashes(parse_dir, AUTHORITY_INPUTS)
        outputs = _authority_hashes(parse_dir, AUTHORITY_OUTPUTS)
        return (record.get('status') == 'accepted' and all(inputs.values()) and
                all(outputs.values()) and record.get('inputs') == inputs and
                record.get('outputs') == outputs and
                record.get('protocol') == _authority_protocol())
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def write_authority(parse_dir: Path, toc: dict) -> dict:
    """Consume the agreed directory in the same pass as body/exercise boundaries."""
    from .regions import write_regions

    parse_dir = Path(parse_dir)
    problems = []
    local_exclusions = []
    if not (parse_dir / 'headings.json').is_file():
        problems.append('缺 MinerU headings.json，无法形成正文目录落点')
    if not (parse_dir / 'toc.excluded.json').is_file():
        problems.append('缺 toc.excluded.json，不能确认非教学正文边界')
    if not problems:
        regions = write_regions(parse_dir, toc)
        local_exclusions = list(regions.get('unresolved') or [])
    if not problems and not _write_alignment(parse_dir, toc):
        problems.append('目录与正文对齐失败')
    if not problems:
        alignment = read_json(parse_dir / 'toc.alignment.json')
        if ((alignment.get('_meta') or {}).get('counts') or {}).get('unmatched_authority'):
            problems.append('教学目录仍有未找到正文落点的条目，请核对 toc.alignment.json')
    if problems:
        for name in ('toc.authority.json', 'toc.decisions.json'):
            (parse_dir / name).unlink(missing_ok=True)
        return {'status': 'needs_review', 'problems': problems}
    record = {'status': 'accepted', 'problems': [],
              'unresolved_exclusions': local_exclusions,
              'exclusion_state': 'partial' if local_exclusions else 'complete',
              'protocol': _authority_protocol(),
              'inputs': _authority_hashes(parse_dir, AUTHORITY_INPUTS),
              'outputs': _authority_hashes(parse_dir, AUTHORITY_OUTPUTS)}
    if not all(record['inputs'].values()) or not all(record['outputs'].values()):
        (parse_dir / 'toc.decisions.json').unlink(missing_ok=True)
        return {'status': 'needs_review', 'problems': ['目录权威所需产物缺失']}
    write_json(parse_dir / 'toc.authority.json', record)
    return record


def cmd_authority(args) -> int:
    """Local-only consumption; paid generation belongs to the shared budgeted engine."""
    from .dual_source import is_current
    parse_dir = _book_dir(args.parse_dir)
    pdf = Path(args.pdf) if args.pdf else _pdf_for(parse_dir)
    if pdf is None or not pdf.is_file() or not is_current(parse_dir, pdf):
        print('⛔ 缺有效双源目录。请从 aifs prepare --until toc 生成；'
              '旧复核建议稿不能替代完整视觉目录。', file=sys.stderr)
        return 2
    if authority_is_current(parse_dir):
        print('✓ 双源目录及正文权威指纹未变，复用现有产物')
        return 0
    _remember_pdf(parse_dir, pdf)
    invalidate_derived(parse_dir)
    result = write_authority(parse_dir, _load_toc_v2(parse_dir))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] == 'accepted' else 2


def cmd_build(args) -> int:
    parse_dir = _book_dir(args.parse_dir)
    pdf = Path(args.pdf) if args.pdf else _pdf_for(parse_dir)
    _remember_pdf(parse_dir, pdf)
    bookmarks, printed, body, page_map, old_toc = _sources_for(parse_dir, pdf)
    slug, title = _slug_title(parse_dir, old_toc)
    payload = reconcile.reconcile(
        slug=slug, book_title=title, bookmarks=bookmarks, printed=printed, body=body,
        page_map=page_map,
        pdf_sha256=sha256_file(pdf) if pdf else "",
        document_sha256=(sha256_file(parse_dir / "document.md")
                         if (parse_dir / "document.md").is_file() else ""),
    )
    payload = _reapply_previous_review(parse_dir, payload)
    problems = validate_toc(payload, strict=False)
    out = parse_dir / TOC_V2_NAME
    write_json(out, payload)
    meta = payload["_meta"]
    print(f"✓ {out}")
    print(f"  confidence={meta['confidence']}  counts={json.dumps(meta['counts'], ensure_ascii=False)}")
    print(f"  nodes={len(payload['nodes'])}  disagreements={len(payload['disagreements'])}  "
          f"unresolved={len(payload['unresolved'])}  suspicious={len(payload['suspicious'])}")
    if not _write_alignment(parse_dir, payload):
        return 2                      # 对齐失败：build 失败（宁可停，不产"过期决策"）
    if problems:
        print(f"  ⚠️ 契约校验有 {len(problems)} 条问题：")
        for line in problems[:10]:
            print(f"     - {line}")
        return 1
    return 0


def _reapply_previous_review(parse_dir: Path, payload: dict) -> dict:
    """重跑 `build` 时，把**上一轮已经应用的复核结论**重新应用一遍。

    ## 为什么必须这么做（实测，2026-09-28）

    `build` 是"从源重建"，不读旧产物。于是端到端重跑一次就会把模型已经裁决、
    已经写进 `revisions` 的修改**悄悄抹掉** —— 实测《复变函数》的 `5.2 幂级数`
    就是这样被打回 `基级数` 的，而且一句提示都没有。

    ## 对位规则（**只认源身份，且不许撞车**）

    一条 revision 记着 `before`（当初从**源**里抽出来的那一条：编号 + 原标题）
    与 `after`（模型给的结论）。源没变，身份就不变，所以：

    1. **几号条目**：`before.number` 在节点里能对上就用它（唯一性最强）；
    2. 否则拿 `before.title` 折平后找。找到**多条同名**就**跳过这一条并在产物里留痕**
       —— 撞车时硬改会把决定用在错的那条上，而且两次 `build` 会挑中不同的那条，
       产物在两次运行之间来回震荡（实测 hash 173/174 反复横跳，查了很久）。

    对不上的旧结论**原样留档**（`revisions` 不删，R-a：只增不丢），
    只是不再声称它生效着 —— 产物里 `applied: false` 会写清楚。
    """
    path = parse_dir / TOC_V2_NAME
    if not path.is_file():
        return payload
    try:
        previous = read_json(path)
    except (OSError, ValueError):
        return payload
    return _reapply_from(previous, payload)


def _reapply_previous_review_from(previous: dict, payload: dict) -> dict:
    """给测试/调用方用的纯函数入口（不读盘）。"""
    return _reapply_from(previous, payload)


def _reapply_from(previous: dict, payload: dict) -> dict:
    """重放的核心（纯函数：`previous` 进、新产物出）。"""
    revisions = previous.get("revisions") or []
    if not revisions:
        return payload

    # ⚠️ **不动点护栏**（实测，2026-09-28）：如果这次的输入（源指纹）与上一轮**完全一样**，
    # 而上一轮的产物**已经带着复核结论**，那就说明"现在磁盘上这份就是最终态" ——
    # 此时**不要再重放**。
    #
    # 为什么需要这道护栏：重放要先把决定"对回节点"，而 `build` 每次都从源重建、
    # 节点的编号/顺序会变（例如目录页切条留下 `线性方程组的解空间 48` 这种残片时），
    # 对位就会在两条候选之间摇摆 → 产物在两个状态之间**无限震荡**（实测 hash A→B→A）。
    # 复核结论已经写在产物里了，重放一次就够；再放一次只会把它推回去。
    previous_meta = previous.get("_meta") or {}
    if (previous_meta.get("review_state")
            and previous_meta.get("pdf_sha256") == payload["_meta"].get("pdf_sha256")
            and previous_meta.get("document_sha256")
            == payload["_meta"].get("document_sha256")
            and previous.get("nodes")):
        # 上一轮**已经落过**复核结论（`review_state` 是 `apply` 留下的标记），
        # 而且这次吃的是同一份输入 → 磁盘上这份就是最终态，直接沿用。
        return {
            **payload,
            "nodes": previous.get("nodes"),
            "disagreements": previous.get("disagreements") or payload["disagreements"],
            "unresolved": previous.get("unresolved") or payload["unresolved"],
            "suspicious": previous.get("suspicious") or payload["suspicious"],
            "revisions": revisions,
            "_meta": {**payload["_meta"],
                      **_carried_meta(previous_meta),
                      "counts": _recount(previous)},
        }

    from ..structure.structure import title_key            # noqa: PLC0415
    by_id = {n.get("toc_id"): n for n in previous.get("nodes") or []}
    decisions: list[dict] = []
    skipped: list[dict] = []
    # 同一个条目可能被好几批复核都记过：只留**最后一条**（更早的已被覆盖）
    latest: dict[str, dict] = {}
    for batch in revisions:
        if not str(batch.get("source") or "").startswith("model_toc"):
            continue
        for decision in batch.get("decisions") or []:
            old = by_id.get(decision.get("toc_id")) or {}
            before = decision.get("before") or {}
            after = decision.get("after") or {}
            key = str(decision.get("toc_id") or "") or \
                f"{before.get('number')}|{before.get('title')}"
            latest[key] = {
                "action": decision.get("action"),
                "title": after.get("title"), "level": after.get("level"),
                "number": after.get("number"),
                "source_number": str(old.get("number") or before.get("number") or ""),
                "source_title": str(before.get("title") or old.get("title") or ""),
                "confidence": decision.get("confidence"),
                "reason": decision.get("reason"),
                "resolution": f"沿用上一轮复核结论（{decision.get('reason') or ''}）"[:400],
                "model_said": decision.get("model_said"),
                "applied": True,
            }

    nodes = payload.get("nodes") or []
    for row in latest.values():
        hit = _locate(nodes, row, title_key)
        if hit is None:
            row["applied"] = False
            row["line"] = None
            skipped.append(row)
            continue
        row["line"] = int(hit.get("order") or 0)
        decisions.append(row)
    if not decisions and not skipped:
        return payload

    merged = review_mod.apply_review(payload, {"decisions": decisions})
    merged["revisions"] = list(revisions)
    if skipped:
        # 留痕：哪几条旧结论这次没能重新对上（人要知道"它现在没生效"）
        merged.setdefault("unresolved", []).append({
            "raw": "（重放复核结论）", "source": "pipeline", "page": None,
            "why": f"{len(skipped)} 条上一轮的复核结论这次没能对回节点"
                   f"（源变了或同名撞车），**未生效**："
                   + "、".join(str(r.get("source_title") or r.get("title"))[:24]
                               for r in skipped[:6]),
        })
    return merged


def _carried_meta(previous_meta: dict) -> dict:
    """沿用的元信息：复核标记、reviewed_by、confidence（都是上一轮算好的）。"""
    keys = ("review_state", "reviewed_by", "confidence", "page_offset",
            "sources_used", "pdf_sha256", "document_sha256")
    return {key: previous_meta[key] for key in keys if key in previous_meta}


def _recount(previous: dict) -> dict:
    """账目**重新算**（教训 §7.1：派生数据不许复制旧值）。"""
    from .contract import counts_of                        # noqa: PLC0415
    return counts_of(previous.get("nodes") or [],
                     disagreements=previous.get("disagreements") or [],
                     unresolved=previous.get("unresolved") or [],
                     suspicious=previous.get("suspicious") or [])


def _locate(nodes: list[dict], decision: dict, title_key) -> dict | None:
    """按"源的稳定身份"找节点。**三条候选表，逐个试；撞车就跳过（不猜）**。

    ## 候选表的顺序（实测定下来的）

    1. **源编号**：`before.number`/旧节点编号，最强；
    2. **源原名的精确折平**：`title_key(源标题) == title_key(节点标题)`；
    3. **"节点标题 == 源标题 + 残片"**：切条没切干净时，源里那条会带着下一条的
       编号/页码（`线性方程组的解空间 48`），而 revision 记的是干净的原名。

    ⚠️ 第 2、3 步都**必须精确**，不能做"结尾包含"这种模糊匹配：实测它会跨条目撞车
    （源里 `线性方程组的解空间 48` 这条把决定吸走），产物在两次 `build` 之间来回震荡。
    撞车（多条命中）时**不取任何一条**，返回 `None` —— 由调用方留痕（不猜，R-c）。
    """
    number = str(decision.get("source_number") or "").strip()
    if number:
        hits = [n for n in nodes if str(n.get("number") or "").strip() == number]
        if hits:
            hits.sort(key=lambda n: str(n.get("toc_id") or ""))
            return hits[0]
    source = title_key(str(decision.get("source_title") or ""))
    if not source:
        return None
    exact = [n for n in nodes if title_key(str(n.get("title") or "")) == source]
    if len(exact) == 1:
        return exact[0]
    if exact:
        return None
    residue = [n for n in nodes
               if title_key(str(n.get("title") or "")).startswith(source)
               and len(title_key(str(n.get("title") or ""))) > len(source)]
    if len(residue) == 1:
        return residue[0]
    return None


def cmd_preflight(args) -> int:
    parse_dir = _book_dir(args.parse_dir)
    pdf = Path(args.pdf) if args.pdf else _pdf_for(parse_dir)
    _remember_pdf(parse_dir, pdf)
    old_toc = {}
    if (parse_dir / "toc.json").is_file():
        old_toc = read_json(parse_dir / "toc.json")
    slug, title = _slug_title(parse_dir, old_toc)
    payload = preflight_mod.preflight(parse_dir=parse_dir, pdf=pdf, slug=slug,
                                      book_title=title)
    out = parse_dir / "toc.preflight.json"
    write_artifact(out, payload)
    print(preflight_mod.render_text(payload))
    print(f"✓ {out}")
    return 0


def cmd_review(args) -> int:
    parse_dir = _book_dir(args.parse_dir)
    payload = _load_toc_v2(parse_dir)
    if args.only is not None:
        payload = dict(payload)
        payload["nodes"] = [n for n in payload["nodes"] if n["toc_id"] in set(args.only)]
    review = review_mod.review_toc(
        payload, parse_dir=parse_dir,
        max_images=args.max_images, dry_run=args.dry_run,
        client=None if args.no_model else review_mod.make_client())
    # ── dry-run 与真实复核**分文件**（2026-09-28 核验抓出）────────────────────
    # 原来两者都写 `toc.review.json`，于是"跑一次不带 --write-review 的 e2e"
    # 就把上一次**真花过钱**的复核结论（model_calls / images / summary）覆盖成
    # `model_calls: 0`、`note: "dry-run：没有调模型"` —— 审计轨迹没了（违反 D8）。
    # 现在：dry-run 写 `toc.review.dry.json`，真实结论永远留在 `toc.review.json`。
    out = parse_dir / (REVIEW_DRY_NAME if args.dry_run else REVIEW_NAME)
    write_artifact(out, review)
    meta = review["_meta"]
    print(f"✓ {out}")
    print(f"  可疑项 {review['suspects_total']} 条 → 送模型 {review.get('chunks', 0)} 片 / "
          f"{meta.get('images', 0)} 张图（降级：{meta.get('downgraded', False)}）")
    print(f"  结论 {len(review['decisions'])} 条："
          + json.dumps({k: v for k, v in (review.get("summary") or {}).items()},
                       ensure_ascii=False))
    if args.dry_run:
        real = parse_dir / REVIEW_NAME
        print(f"  （dry-run：没有落盘到 toc.v2.json；真实复核结论"
              + ("仍在 " + REVIEW_NAME if real.is_file() else "还没有（要真跑加 --write-review）")
              + "）")
    return 0


def _pick_review(parse_dir: Path) -> tuple[Path | None, str]:
    """挑一份复核建议稿：**优先真实结论**，只有没有时才退回 dry-run 的。

    返回值 `(路径 | None, 说明)`；说明会打印出来，避免"我以为是真实结论、其实是 dry-run"。
    """
    real = parse_dir / REVIEW_NAME
    if real.is_file():
        note = ""
        try:
            if int(((read_json(real) or {}).get("_meta") or {}).get("model_calls") or 0) == 0:
                note = "（注意：这份是 dry-run 留下的，没有模型结论）"
        except (TypeError, ValueError):
            note = ""
        return real, note
    dry = parse_dir / REVIEW_DRY_NAME
    if dry.is_file():
        return dry, "（注意：**这是 dry-run 稿**，不是模型真实结论）"
    return None, ""


def cmd_apply(args) -> int:
    parse_dir = _book_dir(args.parse_dir)
    payload = _load_toc_v2(parse_dir)
    review_path, note = _pick_review(parse_dir)
    if review_path is None:
        print(f"✗ 没有复核建议稿（既没有 {REVIEW_NAME} 也没有 {REVIEW_DRY_NAME}）："
              f"先跑 `cli review`", file=sys.stderr)
        return 2
    review = read_json(review_path)
    print(f"  用建议稿：{review_path.name}{note}")
    updated = review_mod.apply_review(payload, review)
    # 打标记：这份产物**已经落过**复核结论。`build` 再跑时靠它判断"要不要重放"
    # （没有这个标记，重放会无限震荡：见 `_reapply_previous_review` 的说明）。
    updated["_meta"]["review_state"] = {
        "reviewed": len(review.get("decisions") or []),
        "batches": len([b for b in (updated.get("revisions") or [])
                        if str(b.get("source") or "").startswith("model_toc")]),
    }
    validate_toc(updated)
    if args.write:
        write_json(parse_dir / TOC_V2_NAME, updated)
        print(f"✓ 写回 {parse_dir / TOC_V2_NAME}（{len(updated['revisions'])} 批改动）")
    else:
        print("（dry-run：加 --write 才落盘）会改成：")
    for row in (updated.get("revisions") or [])[-1:]:
        for change in row.get("decisions", [])[:20]:
            print("   ", json.dumps(change, ensure_ascii=False))
    return 0


def cmd_preview(args) -> int:
    parse_dir = _book_dir(args.parse_dir)
    payload = _load_toc_v2(parse_dir)
    review_path, note = _pick_review(parse_dir)
    review = read_json(review_path) if review_path is not None else None
    if note:
        print(f"  预览用的建议稿：{review_path.name}{note}")
    payload_out = preview_mod.preview(payload, review=review)
    out = parse_dir / "toc.preview.json"
    write_artifact(out, payload_out)
    print(preview_mod.render_text(payload_out))
    print(f"✓ {out}")
    return 0


def cmd_body(args) -> int:
    parse_dir = _book_dir(args.parse_dir)
    toc = _load_toc_v2(parse_dir)
    before = sha256_file(parse_dir / TOC_V2_NAME)
    payload = body_mod.build_body(toc=toc, parse_dir=parse_dir,
                                  toc_sha256=before)
    out = parse_dir / "body.json"
    # ── 前置校验：上游正文产物不在，就**不许**写一份"全空"的 body ──────────────
    # 为什么必须 fail-closed（2026-09-28 核验抓出）：A6 那本新书从没跑过
    # MinerU 适配 + 导出，没有 document.md / original_text/，于是这里安安静静
    # 产出 units=0 / empty_nodes=171 的 body.json，体检表还因为空真判 ✅，
    # 报告把它写成"目录层全部跑通"。**空产物比报错更坏**，所以在这里挡住。
    units_total = int(((payload.get("_meta") or {}).get("counts") or {}).get("units") or 0)
    if units_total == 0:
        print(f"✗ {parse_dir.name}：目录有 "
              f"{len(toc.get('nodes') or [])} 条，但正文层**一个单元都没绑上**"
              f"（document.md={'有' if (parse_dir / 'document.md').is_file() else '没有'} ／ "
              f"original_text/={'有' if (parse_dir / 'original_text').is_dir() else '没有'}）\n"
              f"  这本书还没走 ① 取书 + ② 正文解析：先跑\n"
              f"    python tools/ingest_book.py {parse_dir} --pdf <教材.pdf> --title <书名>\n"
              f"  再跑本命令。**不写出 body.json**（空产物会被下游当成'解析成功'）。",
              file=sys.stderr)
        return 2
    write_artifact(out, payload)
    after = sha256_file(parse_dir / TOC_V2_NAME)
    counts = payload["_meta"]["counts"]
    print(f"✓ {out}")
    print(f"  units={counts['units']}"
          f"（声明 {counts.get('declared_units', 0)} + 承载 {counts.get('undeclared_units', 0)}）"
          f"  empty_nodes={counts['empty_nodes']}  orphans={counts['orphans']}"
          f"  unconfirmed={counts.get('unconfirmed', 0)}")
    if counts.get("undeclared_units"):
        print(f"  内容承载节点：{counts['undeclared_units']} 个（目录没声明、但承载正文，"
              f"已贴到最近的已声明祖先）｜ 壳节 {counts.get('sections_carrying_in_children', 0)} 个")
    overlaps = counts.get("content_overlaps", 0)
    print("  防重复断言：" + ("✅ 0 处重叠（同一段正文没被算两遍）" if not overlaps
                             else f"❌ {overlaps} 处重叠 —— 父子里有整段重复"))
    print(f"  toc.v2.json 哈希 {before[:12]} → {after[:12]}"
          f"（{'未变 ✅ 两步独立' if before == after else '变了 ❌ 违反 A5'}）")
    return 0 if before == after else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m blocks.parse.toc.cli",
                                 description="目录层：三源抽取 → 对账 → 复核 → 预览 → 正文绑定")
    sub = ap.add_subparsers(dest="command", required=True)
    for name, handler, help_text in (
            ("extract", cmd_extract, "三源抽取（0 token）→ toc.draft.json"),
            ("build", cmd_build, "对账 → toc.v2.json（契约 v2，带账目）"),
            ("preflight", cmd_preflight, "预检（0 token）→ toc.preflight.json"),
            ("review", cmd_review, "模型视觉复核 → toc.review.json（建议稿）"),
            ("apply", cmd_apply, "把建议稿应用到 toc.v2.json（dry-run 默认）"),
            ("authority", cmd_authority, "消费已确认双源目录 → 正文及习题边界（0 token）"),
            ("preview", cmd_preview, "给人看的预览 → toc.preview.json"),
            ("body", cmd_body, "正文绑定 → body.json")):
        sub_ap = sub.add_parser(name, help=help_text)
        sub_ap.add_argument("parse_dir", help="data/parse/<slug>")
        sub_ap.add_argument("--pdf", help="显式指定 PDF（默认从 ingest.json 找）")
        sub_ap.set_defaults(func=handler)
        if name == "review":
            sub_ap.add_argument("--max-images", type=int, default=8,
                                help="最多送几张目录页截图（超了降级，见 Q-b 定案）")
            sub_ap.add_argument("--dry-run", action="store_true",
                                help="不调模型，只把会送什么打出来")
            sub_ap.add_argument("--no-model", action="store_true",
                                help="连客户端都不建（排查用）")
            sub_ap.add_argument("--only", nargs="*", help="只复核这些 toc_id")
        if name == "apply":
            sub_ap.add_argument("--write", action="store_true", help="真的写回")
            sub_ap.add_argument("--dry-run", action="store_true", help="只看会改成什么样")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
