"""MinerU 输入形态体检：**在读我们的 JSON 之前**，先看这份产物"长得对不对"。

## 它解决的是"发现点太晚"的问题

管道里最贵的动作是抽概念和写教案（实测：29 节 ≈ 47 万 token）。而这两个动作
**按"单元结构"干活** —— 哪些节、哪些小节。单元结构一旦错，下游全部按错的单元跑，
钱花完才在网页上看见问题。

这次（2026-09-24）实测撞到过：MinerU 的目录条目是"标题 + 空格 + 页码"（没有引导点），
结构层原有的目录规则认不出来 → 第一次跑认出 **71 个标题**（真值 29，章从 1 个变 9 个）。
如果没当场发现，就是"29 节的教案按 71 个单元跑完" —— 白花约 28 万 token。

所以体检放在**适配块之前**：不调模型、不联网、几秒钟，结论是
**"可以往下跑"** 或 **"别跑，先修"**。

## 报警分三级（这是"报警有啥用"的答案）

| 级别 | 含义 | 处置 |
|---|---|---|
| **拦** | 往下跑**一定**产错东西 | 停下，先修适配块或换输入；脚本非 0 退出 |
| **提醒** | 形态和以前不一样了，但**不一定**错 | 记下来、继续；异常数字会自己跳出来 |
| **信息** | 这本书长什么样的基线 | 跨书对比用；下一本书一眼能看出"哪里变了" |

每条检查都写清楚三件事：**判据**、**不成立时会发生什么**（引用真实事故）、**建议动作**。
写不出"会发生什么"的检查不该存在 —— 那种报警只会造成报警疲劳。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "lib"))          # lib/

from adapt import (BODY_TYPES, FIGURE_BLOCK_TYPES, MinerUFormatError, adapt,   # noqa: E402
                   block_text, caption_texts, detect_format, find_layout,
                   image_body_path, render_span, title_key, toc_blocks)
from files import sha256_file, write_json                                  # noqa: E402

SCHEMA_VERSION = "1.0"
GENERATOR = "aiforstudy.parse.mineru.preflight"
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffd]")
KNOWN_DISCARDED = {"page_number", "header", "footer"}
# 适配块**明确处理过**的块类型；不在名单里的会在 F12 报警
HANDLED_BLOCK_TYPES = {"text", "title", "interline_equation", "index", "list", "ref_text",
                       *FIGURE_BLOCK_TYPES}
HEADING_SHAPE = re.compile(r"^\s*(?:第\s*[一二三四五六七八九十百零〇\d]+\s*[章篇部分]"
                           r"|[§$S]?\s*\d+(?:[.．]\d+)*\s*\S)")
LEVELS = ("拦", "提醒", "信息")


def _check(cid: str, name: str, level: str, verdict: str, judge: str,
           observed: str, consequence: str, action: str) -> dict:
    return {"id": cid, "name": name, "级别": level, "状态": verdict, "判据": judge,
            "观察": observed, "不成立时会发生什么": consequence, "建议": action}


def preflight(mineru_dir: str | Path, *, out: str | Path | None = None,
              dry_run: bool = False) -> dict:
    """体检。返回报告 dict（含 `passed`：有没有"拦"级别的问题）。"""
    layout = find_layout(mineru_dir)
    base = layout.parent
    payload = json.loads(layout.read_text(encoding="utf-8"))
    checks: list[dict] = []

    # ── F1 版本与格式（拦）──────────────────────────────────────────────
    try:
        fmt = detect_format(payload)
        checks.append(_check(
            "F1", "版本与格式", "拦", "通过",
            "顶层有 pdf_info（3.x legacy），且 _version_name 在适配块的白名单里",
            f"{fmt['family']} / {fmt['version']}（backend={fmt['backend']}, ocr={fmt['ocr_enable']}）",
            "认不出格式还硬跑 → 字段对不上，适配块会产出**看似正常但内容错位**的 document.md",
            "不用动"))
    except MinerUFormatError as exc:
        checks.append(_check("F1", "版本与格式", "拦", "拦",
                            "顶层有 pdf_info（3.x legacy），且版本在白名单里",
                            f"认不出：{exc}", "适配块会立刻报错并停止（它是硬门），但那样就白跑一趟",
                            "确认 MinerU 版本，或按设计稿第四节补字段映射"))
        return _report(layout, base, checks, None, out=out, dry_run=dry_run)

    pages = payload.get("pdf_info") or []

    # ── F2 控制字符 / 替换字符（拦）─────────────────────────────────────
    dirty: list[tuple[int, str]] = []
    for page in pages:
        number = int(page.get("page_idx") or 0) + 1
        for block in page.get("para_blocks") or []:
            for line in block.get("lines") or []:
                for span in line.get("spans") or []:
                    hit = CONTROL.search(span.get("content") or "")
                    if hit:
                        dirty.append((number, hex(ord(hit.group()))))
    # 适配层若已声明"我来剥并记账"（`adapt.STRIPS_CONTROL`），这一条就**从"拦"降为"提醒"**：
    # 体检的职责是"别让脏数据静默流过"，而剥掉+记账恰恰满足了这一点（H7/R-b）。
    # ⚠️ **三种导入方式都要试**（2026-09-29 实测踩坑）：`cli.py` 里是
    #    `from preflight import ...`（**顶层模块**方式），那种上下文下相对导入
    #    `from . import adapt` 会抛 ImportError —— 于是"适配层已声明会剥"这个事实
    #    读不到，闸门照旧拦（明明改对了却还是过不去）。
    # ⚠️ **优先读 `sys.modules` 里已经导入的那个模块对象**，不要用 `__import__("adapt")`
    #    去"再导一次"：那会生成**另一份模块副本**（2026-09-29 实测：测试里 monkeypatch 掉
    #    `STRIPS_CONTROL` 之后仍然读到旧值 True），生产里同样是隐患（两处状态不同步）。
    handles = False
    for name in ("blocks.parse.mineru.adapt", "adapt"):
        module = sys.modules.get(name)
        if module is not None and getattr(module, "STRIPS_CONTROL", False):
            handles = True
            break
    else:
        try:
            from . import adapt as _adapt               # 包内首次导入
            handles = bool(getattr(_adapt, "STRIPS_CONTROL", False))
        except Exception:                               # noqa: BLE001
            handles = False
    checks.append(_check(
        "F2", "控制字符 / 替换字符", _f2_verdict(handles, len(dirty))[0],
        # ⚠️ 状态**只能是枚举值**（通过/提醒/拦）——`format_report` 用固定表映射 emoji，
        #    写成"通过（…）"会 KeyError 崩掉（2026-09-29 实测）。说明放"建议"字段。
        _f2_verdict(handles, len(dirty))[1],
        "所有 span 的文本里不该有 \\x00-\\x1f 这类控制字符和 U+FFFD（无法识别字符）",
        "干净" if not dirty else f"{len(dirty)} 处：{dirty[:6]}",
        "控制字符会原样进原文 → 渲染时和内部占位符撞车，页面上出现看不见的垃圾",
        "不用动（适配层已声明会剥掉并记进 `mineru.audit.json`）" if (handles and dirty)
        else ("不用动" if not dirty else "在适配块里过滤掉，并把它记进 audit")))

    # ── F3 页尺寸（拦）──────────────────────────────────────────────────
    missing = [int(p.get("page_idx") or 0) + 1 for p in pages if len(p.get("page_size") or []) != 2]
    checks.append(_check(
        "F3", "页尺寸齐全", "拦", "通过" if not missing else "拦",
        "每页都要有 page_size（bbox 是 pt 坐标，换算成像素要用它核对）",
        f"{len(pages)} 页全有" if not missing else f"缺 {len(missing)} 页：{missing[:6]}",
        "缺页尺寸时无法判断 bbox 是否越界 → 插图可能裁成空白",
        "不用动" if not missing else "换一次解析，或手工补页尺寸"))

    # ── F4 三条硬规则 + 规模（复用适配块的自检，dry_run 不写文件）────────
    result = adapt(base, base / "_preflight_scratch", dry_run=True)
    hard = result.audit["checks"]["hard_rules"]
    counts = result.counts
    checks.append(_check(
        "F4", "适配块硬规则（4 条）", "拦", "通过" if not hard else "拦",
        "独立公式单行 / 标题裸行 / 相邻公式之间有空格 / 行号与 audit 对得上",
        "4 条全过" if not hard else f"{len(hard)} 条不过：{hard[:3]}",
        "拆行的 `$$` 会被结构层当页眉删掉（公式消失）；相邻公式拼出 `$A$$B$` 会让渲染器"
        "吞掉中间整段（实测：一处公式渲染失败 + 13 个内部槽位泄漏到页面上）",
        "不用动" if not hard else "这是适配块的 bug，先修适配块再往下跑"))

    # ── F5 目录页（提醒）────────────────────────────────────────────────
    toc_pages = [int(p.get("page_idx") or 0) + 1 for p in pages if toc_blocks(p)]
    checks.append(_check(
        "F5", "目录页", "提醒", "通过" if len(toc_pages) <= 8 else "提醒",
        "有 `目录` 标记的页会被整页丢掉（正文不动）；页数太多说明可能误判",
        f"{len(toc_pages)} 页：{toc_pages[:10]}",
        "目录条目和正文标题长得一模一样 —— 不丢就会把整份目录当成章节结构。"
        "实测：结构层认出 **71 个标题**（真值 29），下游全按错的单元跑",
        "不用动" if len(toc_pages) <= 8 else "抽查这几页，确认是目录不是正文"))

    # ── F6 重复标题（提醒）──────────────────────────────────────────────
    titles: dict[str, list[int]] = {}
    for page in pages:
        number = int(page.get("page_idx") or 0) + 1
        for block in page.get("para_blocks") or []:
            if str(block.get("type")) != "title":
                continue
            key = title_key(block)
            if key:
                titles.setdefault(key, []).append(number)
    repeated = {t: ps for t, ps in titles.items() if len(ps) >= 2}
    checks.append(_check(
        "F6", "重复标题（页眉/页脚被认成标题）", "提醒", "通过" if not repeated else "提醒",
        "同一个标题文本出现在 2 页以上 → 很可能页眉页脚；适配块只留第一次",
        f"{len(titles)} 个标题，其中重复的 {len(repeated)} 个"
        + (f"：{list(repeated.items())[:3]}" if repeated else ""),
        "重复行会让结构层的「出现在 ≥3 页 = 跨页页眉，整行删掉」规则**把真标题一起删掉**。"
        "实测：`§1.7 集合与映射*` 就是这么消失的，它下面 5 个小节被提升成节",
        "不用动" if not repeated else "看一眼这些重复标题的首页是不是真标题"))

    # ── F7 图块与图题数量（提醒）───────────────────────────────────────
    mismatched: list[str] = []
    for page in pages:
        number = int(page.get("page_idx") or 0) + 1
        blocks = [b for b in page.get("para_blocks") or [] if str(b.get("type")) == "image"]
        bodies = [b for b in blocks if image_body_path(b)]
        captions = [c for b in blocks for c in caption_texts(b) if re.search(r"图\s*\d+", c)]
        if len(bodies) != len(captions):
            mismatched.append(f"第{number}页 {len(bodies)}图/{len(captions)}题")
    checks.append(_check(
        "F7", "图块与图题数量匹配", "提醒", "通过" if not mismatched else "提醒",
        "同一页里「有图的块数」应等于「图 x.y 形状的图题数」；不等就说明 MinerU 挂错了",
        "全部匹配" if not mismatched else f"{len(mismatched)} 页不等：{mismatched[:5]}",
        "实测第 29 页：三张并排小图的**三条图题全挂在最右边那块**上，左边两块一条都没有；"
        "第 15 页图 1.5 被拆成两块。适配块按「整页图题池重新配对 + 并排面板并框」处理，"
        "但不匹配的页要人工看一眼才放心",
        "不用动" if not mismatched else "按页码抽查这几页的图题配得对不对"))

    # ── F8 丢弃块类型（提醒）───────────────────────────────────────────
    kinds: dict[str, int] = {}
    for page in pages:
        for block in page.get("discarded_blocks") or []:
            kind = str(block.get("type") or "unknown")
            kinds[kind] = kinds.get(kind, 0) + 1
    unknown = {k: v for k, v in kinds.items() if k not in KNOWN_DISCARDED}
    checks.append(_check(
        "F8", "丢弃块类型", "提醒", "通过" if not unknown else "提醒",
        "丢弃块应该是页码 / 页眉 / 页脚这几类；出现别的类型要看看是不是被误丢的正文",
        f"{kinds}" if kinds else "没有丢弃块",
        "MinerU 会把丢掉的东西明确列出来（这是我们相对自己猜着丢的优势）。"
        "但出现新类型时，可能是**正文被当成页码丢了**",
        "不用动" if not unknown else f"抽查 {list(unknown)} 这几类的原文本"))

    # ── F9 标题块里认不出编号的（提醒）─────────────────────────────────
    odd_titles = []
    for page in pages:
        number = int(page.get("page_idx") or 0) + 1
        for block in page.get("para_blocks") or []:
            if str(block.get("type")) != "title":
                continue
            text = block_text(block).strip()
            if text and not HEADING_SHAPE.match(text):
                odd_titles.append((number, text[:24]))
    checks.append(_check(
        "F9", "认不出编号的标题块", "提醒", "通过" if len(odd_titles) <= 12 else "提醒",
        "标题块里应该有章 / 节 / 小节的编号形状；认不出的会被结构层当假标题或丢掉",
        f"{len(odd_titles)} 个" + (f"：{odd_titles[:4]}" if odd_titles else ""),
        "实测这一类里混着「目录」「页脚」和**正文里的定理**（`定理1.7.5`）——"
        "它们会变成层级里的假标题，把真标题的编号顶歪",
        "不用动" if len(odd_titles) <= 12 else "抽查这些标题，必要时用结构复核（T1.6）兜底"))

    # ── F11 输入里的公式形态（提醒）───────────────────────────────────
    # 适配块会"悄悄"改两个字节：给相邻公式之间插空格。这种改动必须被看见 ——
    # 不报的话，"公式被吞掉"那次事故就只能等渲染完才发现。
    glued = 0
    self_delimited = 0
    for page in pages:
        for block in page.get("para_blocks") or []:
            for line in block.get("lines") or []:
                pieces = [render_span(s) for s in (line.get("spans") or [])]
                pieces = [p for p in pieces if p]
                # 只数"前一个以 $ 收尾、后一个以 $ 开头"的**相邻**边界 ——
                # 直接把整行拼起来数 `$$` 会把显示公式的定界符也算进去（实测 333 vs 真值 1）
                for left, right in zip(pieces, pieces[1:]):
                    if left.endswith("$") and right.startswith("$"):
                        glued += 1
                self_delimited += sum(1 for s in (line.get("spans") or [])
                                      if str(s.get("type")) == "inline_equation"
                                      and (s.get("content") or "").strip().startswith("$$"))
    checks.append(_check(
        "F11", "输入里的公式形态", "提醒", "通过" if not (glued or self_delimited) else "提醒",
        "数一数输入里有多少「相邻公式会拼出 $$」和「span 自带 $$」的地方",
        f"相邻公式 {glued} 处｜span 自带 $$ {self_delimited} 处",
        "实测有 1 处相邻公式（第 1.7.4 节的 σ 式子）拼出了 `$A$$B$`，渲染器把中间整段吞掉："
        "一处公式渲染失败 + 13 个内部槽位泄漏到页面上。适配块现在会插一个空格处理掉，"
        "**但它改了一个字节，所以必须报出来**",
        "不用动（适配块已处理）" if (glued or self_delimited) else "不用动"))

    # ── F12 没见过的块类型（提醒）─────────────────────────────────────
    # 这条是**全本那次事故**换来的：前 40 页从没见过 `index`/`table`/`chart`，
    # 全本一跑全冒出来了 —— `index` 是目录清单（漏进正文 → 整份目录会变成章节结构），
    # `table`/`chart` 的内容是**图片**（按文本处理 → 渲染成空行、静默丢内容）。
    seen_types: dict[str, int] = {}
    for page in pages:
        for block in page.get("para_blocks") or page.get("preproc_blocks") or []:
            kind = str(block.get("type") or "unknown")
            seen_types[kind] = seen_types.get(kind, 0) + 1
    unknown_types = {k: v for k, v in seen_types.items() if k not in HANDLED_BLOCK_TYPES}
    checks.append(_check(
        "F12", "没见过的块类型", "提醒", "通过" if not unknown_types else "提醒",
        "块类型应该在适配块明确处理过的名单里（文字/标题/公式/图/表/图/目录/列表/参考文献）",
        f"共 {len(seen_types)} 种：{seen_types}"
        + (f"；**没见过的** {unknown_types}" if unknown_types else ""),
        "没见过的类型会走通用分支：**有文字的还看得见，内容是图片的会渲染成空行、静默丢内容**。"
        "实测：`table`/`chart` 就是这样丢的（修好后它们按图处理），`index` 漏进正文后"
        "会让整份目录被当成章节结构",
        "不用动" if not unknown_types else
        "看一眼这些块的原文，决定是「当图」「当文本」还是「整块丢」，然后在 adapt.py 里明确处理"))

    # ── F13 块类型普查（信息）─────────────────────────────────────────
    by_kind = []
    for kind, count in sorted(seen_types.items(), key=lambda kv: -kv[1]):
        if kind in FIGURE_BLOCK_TYPES:
            how = "当图处理"
        elif kind == "index":
            how = "整块丢（目录）"
        elif kind in ("text", "list", "ref_text"):
            how = "当文本"
        elif kind == "title":
            how = "当标题"
        elif kind == "interline_equation":
            how = "当独立公式"
        else:
            how = "通用分支"
        by_kind.append(f"{kind}×{count}（{how}）")
    checks.append(_check(
        "F13", "块类型普查", "信息", "通过",
        "把每种块类型有多少、适配块怎么处理它列出来（换本书一眼能看出多了什么）",
        "；".join(by_kind) or "没有块",
        "无（这是基线，不是问题）", "跨书对比时看这一行"))

    # ── F10 规模基线（信息）───────────────────────────────────────────
    checks.append(_check(
        "F10", "规模基线", "信息", "通过",
        "把这本书的规模记下来，下一本书一眼能看出哪里变了",
        f"页 {counts['pages']}｜块 {counts['blocks']}｜汉字 {counts['cjk_chars']}"
        f"｜公式 行内 {counts['formulas']['inline']}/独立 {counts['formulas']['interline']}"
        f"｜图 {counts['figures']}｜丢弃 {counts['discarded']}",
        "无（这是基线，不是问题）", "跨书对比时看这一行"))

    return _report(layout, base, checks, result, out=out, dry_run=dry_run)


def _f2_verdict(handles: bool, dirty_count: int) -> "tuple[str, str]":
    """F2（控制字符/替换字符）的判据：**适配层声明会剥并记账 → 只提醒；否则拦**。

    抽成纯函数是为了能直接测（2026-09-29 实测教训：`adapt` 在本仓会被以
    `adapt` 与 `blocks.parse.mineru.adapt` **两种模块名各导入一份**，
    于是"patch 一个全局量来测闸门"必然打偏 —— 别再依赖模块状态测判据）。
    """
    if not dirty_count:
        return "拦", "通过"
    return ("提醒", "通过") if handles else ("拦", "拦")


def _report(layout: Path, base: Path, checks: list[dict], result, *,
            out: str | Path | None, dry_run: bool) -> dict:
    blocked = [c for c in checks if c["状态"] == "拦"]
    warned = [c for c in checks if c["状态"] == "提醒"]
    report = {
        "_meta": {
            "file_type": "mineru-preflight", "schema_version": SCHEMA_VERSION,
            "generator": GENERATOR, "source_sha256": sha256_file(layout),
            "field_notes": {
                "checks[].级别": "拦=往下跑一定产错；提醒=形态变了但未必错；信息=基线",
                "checks[].不成立时会发生什么": "引用真实事故 —— 写不出后果的检查不该存在",
                "passed": "没有『拦』级别的问题就可以往下跑",
            },
        },
        "input": {"layout": layout.name, "dir": str(base)},
        "passed": not blocked,
        "counts": result.counts if result else None,
        "checks": checks,
        "blocked": [c["id"] for c in blocked],
        "warnings": [c["id"] for c in warned],
    }
    if out is not None and not dry_run:
        write_json(Path(out) / "preflight.json", report)
    return report


def format_report(report: dict) -> str:
    lines = []
    blocked = report["blocked"]
    head = "✅ 可以往下跑" if report["passed"] else f"⛔ 别往下跑（{len(blocked)} 条『拦』）"
    if report["warnings"]:
        head += f"（另有 {len(report['warnings'])} 条提醒）"
    lines.append(head)
    for check in report["checks"]:
        mark = {"通过": "✅", "提醒": "⚠️ ", "拦": "⛔"}[check["状态"]]
        lines.append(f"{mark} [{check['id']}] {check['name']}（{check['级别']}）：{check['观察']}")
        if check["状态"] != "通过":
            lines.append(f"     会发生什么：{check['不成立时会发生什么']}")
            lines.append(f"     建议：{check['建议']}")
    return "\n".join(lines)


__all__ = ["preflight", "format_report"]
