"""**问题的唯一汇总处**（缺口 B 的落点）：把各块的产物收成一份 `problems.json`。

为什么要有这个块（用户 2026-09-25 的原话是「一定要全局改……这个程序是要自动跑的」）：

* 交付包里给负责人看的那些**数字**（套话 0 处、例题每步都有依据、第二章烧了多少 token、
  哪几节有问题）以前是**手写的**。手写的声明会过期：内容一重跑，数字就不对了，
  而没有人会去改那句话。所以规矩是 —— **交付物里的每个数字都必须能从产物里算出来**，
  算不出来就不许写。
* 同一个毛病会在好几个块里各报一次（教案校验、建页自检、体检报告、审核工单）。
  散着看没问题，一多就没人看得住。这里按 (单元, 类别, 原句) 收口，去重、标状态、留证据。

一份 `problems.json` 里有四样东西：

    problems[]   硬问题（会挡住"通过"的）：来源块、单元、原句、状态、证据文件
    notes[]      记录但不挡的（范围外的插图没上页面、渲染层自动合并了重复概念名……）
    dropped[]    被**丢掉**的节（无进展/冻结）：原因 + 烧掉的 token（决策 4）
    metrics{}    交付声明要用的数字，每个都带 `evidence`（哪个文件、哪个字段）
    claims[]     由 metrics 生成的**声明句子**（交付包直接渲染，不许手写）

用法：

    ../.venv-linux/bin/python -m blocks.problems.cli \\
    （下面的示例写的是默认那本书；换书请用 `--book`，路径由 `lib/book.py` 解析）
        --lessons data/lesson/linear-algebra-full \\
        --delivery delivery/第二章-全章 --delivery delivery/第五章-两节 \\
        --audit data/audit/linear-algebra-full \\
        --out data/problems/linear-algebra-full.json

    # 复核：把 claims 里的每个数字重新从产物里算一遍，对不上就非 0 退出
    ../.venv-linux/bin/python -m blocks.problems.cli --check ...
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCHEMA_VERSION = "1.0"
GENERATOR = "aiforstudy.problems"

# 审核回路的结论里出现这些词 = 这一节没弄好（冻结 / 无进展），交付包里不该出现
_BAD_CONCLUSIONS = ("冻结", "无进展", "失败")

# ── 已经作废的规则 ──────────────────────────────────────────────────────────
# 规范改过之后，**旧规范判出来的工单会一直挂在产物里**（它们是真跑出来的历史记录，
# 不该手改产物去删）。但它们已经不是问题了：按新规范，那一处本来就是合格的。
# 所以这里列一条"作废规则表"，读工单时把命中的挑出来，如实标成"已失效（规范改了）"，
# 既不冒充问题，也不假装没发生过。等这一节按新规范重跑，它们自然会消失。
# ⚠️ 每条都带**作废日期**与**当时真实出现过的工单样例**（`样例` 必须逐字来自历史产物）。
# 为什么（2026-09-26 结构审计）：这张表靠**字符串前缀**匹配，而上游消息措辞一改
# （比如以后写成「超过上限 3.5 倍」），前缀就失配 → 真正作废的条目会**重新变成硬问题**，
# 凭一条早已取消的规则挡住交付。所以这里存一条样例，
# 由 `tests/test_problems.py` 断言"每个前缀仍能命中它自己的样例" —— 措辞一漂测试就红。
RETIRED_RULES = [
    {"前缀": "超过上限 3.5（注水了", "作废于": "2026-09-25",
     "为什么": "篇幅上限已于 2026-09-25 取消（用户定案：只判地板、不判上限）",
     "样例": "CH02-S01: 超过上限 3.5（注水了）：正文是原文的 3.73 倍"},
    {"前缀": "超过上限 3.5", "作废于": "2026-09-25",
     "为什么": "篇幅上限已于 2026-09-25 取消（用户定案：只判地板、不判上限）",
     "样例": "CH05-S05-SS02: 超过上限 3.5：正文是原文的 3.62 倍"},
]


def retired_reason(message: str) -> str:
    """这条问题是不是按**已经取消的规则**判出来的？是就给出原因，否则空串。"""
    text = str(message or "")
    for row in RETIRED_RULES:
        if row["前缀"] in text:
            return f"{row['为什么']}（该规则作废于 {row['作废于']}）"
    return ""
_SEVERITY_RANK = {"P0": 0, "P1": 1, "P2": 2}


def _load(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _dig(data, dotted: str):
    """按 `a.b.0.c` 取值；取不到返回 None（声明里的 evidence 就用这个语法）。"""
    node = data
    for part in str(dotted).split("."):
        if node is None:
            return None
        if isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(node, dict):
            node = node.get(part)
        else:
            return None
    return node


def _models_per_unit(lessons_dir: Path) -> dict:
    """每个单元有几个交互模型。

    ⚠️ 模型不在教案 JSON 里，而是**另外一份产物** `models/<unit>.json` —— 第一版从
    `lesson["models"]` 数，永远得到 0（交付声明里就成了"交互模型 0 个"，假的）。
    """
    counts = {}
    for path in sorted((Path(lessons_dir) / "models").glob("*.json")):
        payload = _load(path) or {}
        rows = payload.get("models") if isinstance(payload, dict) else payload
        if isinstance(rows, list):
            for row in rows:
                unit = str((row or {}).get("unit_id") or path.stem)
                counts[unit] = counts.get(unit, 0) + 1
        elif isinstance(rows, dict):
            for row in rows.values():
                unit = str((row or {}).get("unit_id") or path.stem)
                counts[unit] = counts.get(unit, 0) + 1
    return counts


def _lesson_rows(lessons_dir: Path) -> list[dict]:
    return [row for row in (_load(p) for p in sorted((lessons_dir / "lessons").glob("*.json")))
            if isinstance(row, dict)]


def _fresh_checks(lessons_dir: Path, lesson: dict, memo: dict | None = None) -> dict:
    """Inspect schema-4 structure; historical checks are explicitly unverified."""
    unit=str(lesson.get("unit_id") or "")
    if memo is not None and unit in memo:return memo[unit]
    result={"problems":[],"warnings":[],"stats":{},"quality_verified":False}
    if (lesson.get("_meta") or {}).get("schema_version")=="4.0":
        from aiforstudy.blocks.content.content_contracts import parse_content
        try:parse_content(lesson)
        except ValueError as exc:result['problems'].append(str(exc))
        result['note']='schema-only; teaching quality requires current approval receipts'
    else:
        snap=lesson.get('checks') or {}
        result.update(problems=list(snap.get('problems') or []),warnings=list(snap.get('warnings') or []),stats=dict(snap.get('stats') or {}),note='historical_snapshot_not_current_validation')
    if memo is not None and unit:memo[unit]=result
    return result


def from_lessons(lessons_dir: Path, memo: dict | None = None) -> tuple[list[dict], list[dict], dict]:
    """教案块：硬问题、措辞提醒、逐节数字（字数/倍数/套话/详解/例题依据/模型数）。"""
    problems, notes, per_unit = [], [], {}
    models = _models_per_unit(lessons_dir)
    # ⚠️ **按当前规则重算**，别读 `lesson["checks"]`（那是上一次那套规则写的快照）。
    # 实测：规则升级后汇总里还在显示旧消息、旧结论（今天第四个踩这个坑的地方）。
    for lesson in _lesson_rows(lessons_dir):
        unit = str(lesson.get("unit_id") or "?")
        checks = _fresh_checks(lessons_dir, lesson, memo)
        stats = checks.get("stats") or {}
        if checks.get("error"):
            # 重算**失败**必须说出来（不能悄悄用旧快照冒充，否则这一节看着"干净"）
            problems.append({"unit": unit, "source": "教案校验", "severity": "P1",
                             "message": f"{unit}: 校验重算失败（{checks['error']}）"
                                        f"→ 这一节的结论不可信，请重跑",
                             "evidence": f"lessons/{unit}.json"})
        elif checks.get("note") == "parse_dir_missing":
            notes.append({"unit": unit, "source": "教案校验", "severity": "P2",
                          "message": f"{unit}: 没有 parse 目录，指标与结论沿用产物里的快照"
                                     f"（不是重算结果）",
                          "evidence": f"lessons/{unit}.json"})
        for message in checks.get("problems") or []:
            item = {"unit": unit, "source": "教案校验", "severity": "P1",
                    "message": str(message),
                    "evidence": f"lessons/{unit}.json#checks.problems"}
            # 产物里存的是**当时那套规范**判出来的问题；规范改过之后要按现规范读
            reason = retired_reason(message)
            if reason:
                item["status"] = "retired"
                item["message"] = f"{item['message']}　→ 已失效：{reason}"
                notes.append(item)
            else:
                problems.append(item)
        for message in checks.get("warnings") or []:
            notes.append({"unit": unit, "source": "教案校验", "severity": "P2",
                          "message": str(message),
                          "evidence": f"lessons/{unit}.json#checks.warnings"})
        steps = [step for example in (lesson.get("examples") or [])
                 for step in (example.get("steps") or [])]
        per_unit[unit] = {
            "标题": lesson.get("title") or "",
            "字数": stats.get("正文视觉长度") or stats.get("讲解字数") or 0,
            "原文": stats.get("原文字数") or 0,
            "倍数": stats.get("正文倍数") or stats.get("倍数") or 0,
            "地板": stats.get("正文地板") or 0,
            "套话": stats.get("套话") or 0,
            "详解": len(lesson.get("explainables") or []),
            "详解引用": stats.get("详解引用") or 0,
            "例题步数": len(steps),
            "有依据步数": sum(1 for step in steps if str(step.get("basis") or "").strip()),
            "概念": len(lesson.get("concepts") or []),
            "模型": int(models.get(unit, 0)),
        }
    return problems, notes, per_unit


def from_delivery(delivery_dirs: list[Path]) -> tuple[list[dict], list[dict], dict]:
    """建页块：每个交付目录的自检问题 + 记录性说明（notes 不当硬问题）。"""
    problems, notes, per_page = [], [], {}
    for directory in delivery_dirs or []:
        report = _load(Path(directory) / "render.index.json") or {}
        name = Path(directory).name
        for message in report.get("problems") or []:
            problems.append({"unit": "", "source": f"建页自检（{name}）", "severity": "P1",
                             "message": str(message),
                             "evidence": f"{name}/render.index.json#problems"})
        for message in report.get("notes") or []:
            notes.append({"unit": "", "source": f"建页自检（{name}）", "severity": "P2",
                          "message": str(message),
                          "evidence": f"{name}/render.index.json#notes"})
        per_page[name] = {**(report.get("counts") or {}),
                          "节": report.get("scope") or []}   # 这一页里**实际有**哪几节
    return problems, notes, per_page


def from_audit(audit_dir: Path) -> tuple[list[dict], list[dict], dict]:
    """审核块：工单（按状态分级）+ 每节的回路结论。"""
    problems, notes, per_unit = [], [], {}
    for path in sorted(Path(audit_dir).glob("*.tickets.json")) if audit_dir else []:
        payload = _load(path) or {}
        unit = str(payload.get("_meta", {}).get("unit_id") or path.name.split(".")[0])
        for ticket in payload.get("tickets") or []:
            status = str(ticket.get("status") or "open")
            item = {"unit": unit, "source": "独立复核工单", "ticket": ticket.get("id"),
                    "severity": str(ticket.get("severity") or "P1"),
                    "message": f"[{ticket.get('id')}]「{ticket.get('quote')}」"
                               f"→ {ticket.get('requirement')}",
                    "status": status,
                    "evidence": f"{path.name}#tickets[{ticket.get('id')}]"}
            reason = retired_reason(item["message"])
            if reason:
                item["status"] = "retired"
                item["message"] = f"{item['message']}　→ 已失效：{reason}"
                notes.append(item)
            elif status == "closed":
                notes.append(item)
            else:
                problems.append(item)
    # ── 勘误清单：**原文疑似有错**（复核认出来的，交人核；不是成品的错）──────────
    # 为什么在这里读：`problems.json` 是"这一批产物现在什么状态"的唯一汇总出口，
    # 勘误清单如果不进这里，就没有任何人会看到它（审计目录里躺着一份没人读的文件）。
    if audit_dir:
        book = _load(Path(audit_dir) / "原文疑似有错.json") or {}
        for row in book.get("条目") or []:
            notes.append({"unit": str(row.get("unit") or "?"),
                          "source": "勘误：原文疑似有错", "severity": "P2",
                          "message": f"{row.get('unit')}: 原文疑似有错 —— "
                                     f"「{str(row.get('原文里的句子'))[:60]}」"
                                     f"｜为什么怀疑：{str(row.get('为什么怀疑原文'))[:80]}",
                          "evidence": f"audit/原文疑似有错.json#{row.get('unit')}"})

    for path in sorted(Path(audit_dir).glob("*.loop.json")) if audit_dir else []:
        payload = _load(path) or {}
        unit = str(payload.get("_meta", {}).get("unit_id") or path.name.split(".")[0])
        # ⚠️ 键名必须与**写入端**一致（`tools/audit_loop.py` 写的是中文键）。
        #    这里原来读 `conclusion` / `history` / `lessons` 三个英文键 ——
        #    而 loop.json 里根本没有它们，于是**每一节的回路结论都读成空字符串**，
        #    "回路到底通过没有"这个信息从来没有真正进过汇总（实测 14 个 loop.json 全空）。
        #    这是"同一件事在两端两个名字"的又一处。保留英文键做兼容读取，但以中文键为准。
        per_unit[unit] = {"结论": payload.get("结论") or payload.get("conclusion") or "",
                          "轮数": len(payload.get("轮次") or payload.get("history") or []),
                          "教训": payload.get("卡在哪") or payload.get("lessons") or ""}
    return problems, notes, per_unit


def from_chapter_runs(lessons_dir: Path) -> dict:
    """逐章跑批的记录：总花费、逐节每步 token、回路结论、网页自检。"""
    runs = {}
    for path in sorted((Path(lessons_dir) / "chapter-runs").glob("*.json")):
        payload = _load(path) or {}
        chapter = str(payload.get("chapter") or path.stem)
        runs[chapter] = payload
    return runs


def _tokens_for(runs: dict, unit_id: str) -> int:
    for payload in runs.values():
        for row in payload.get("节") or []:
            if str(row.get("unit_id")) == unit_id:
                return int(sum(int((step or {}).get("tokens") or 0)
                               for step in (row.get("steps") or {}).values()))
    return 0


def dropped_sections(lessons_dir: Path, runs: dict, audit_dir: Path | None = None,
                     memo: dict | None = None) -> list[dict]:
    """被丢掉的节（决策 4）：**当前**回路判无进展/冻结的那些 —— 列出原因与烧掉的 token。

    「丢掉」= **不进交付包**，只在内部包里出现。用户的原话是：让读者拿到的东西里
    没有半成品；但制作方要知道为什么、花了多少。

    ⚠️ 判据必须看**当前**状态，不能看历史快照（实测踩过）：逐章跑批记录
    (`chapter-runs/<章>.json`) 里的回路结论是**那一次**跑批的结论；一节重跑修好之后，
    那个文件不会自动更新 —— 于是"已经通过的节"仍被当成冻结、被踢出交付包。
    所以这里以**这一节自己的回路记录** (`<unit>.loop.json`) 为准，并且要求
    "教案版本 == 回路记录的那一版"（版本对不上 = 教案在回路之后又改过，结论同样不可信）。
    """
    dropped = []
    seen = set()
    # 审核目录：调用方给了就用它（`collect` 会把自己收到的那个传进来）；
    # 没给就按约定推：<项目>/data/lesson/<书名> → <项目>/data/audit/<书名>。
    # 第一版不管调用方给没给都自己推，于是测试里传进来的 audit 目录被忽略（实测踩到）。
    audit_dir = Path(audit_dir) if audit_dir else \
        (Path(lessons_dir).resolve().parents[1] / "audit" / Path(lessons_dir).name)
    work = _lesson_rows(Path(lessons_dir))
    for path in sorted(audit_dir.glob("*.loop.json")) if audit_dir.is_dir() else []:
        payload = _load(path) or {}
        unit = str((payload.get("_meta") or {}).get("unit_id") or path.name.split(".")[0])
        conclusion = str(payload.get("结论") or "")
        lesson = next((row for row in work if str(row.get("unit_id")) == unit), {})
        fresh = int(payload.get("lesson_revision") or -1) == int(lesson.get("revision") or -2)
        if any(bad in conclusion for bad in _BAD_CONCLUSIONS) and fresh:
            # ⚠️ 只看"回路说冻结"还不够：那个结论可能是**按已经作废的规则**判出来的。
            # 实测踩过：两节冻在「详解 what 太长（91 字）」上，而那条规则量的是 LaTeX 源码长度
            # （读起来只有 34 字）—— 规则改成"按读起来算"之后，这两节其实**已经合格**，
            # 却还因为旧结论被挡在交付包外。所以再加一道关卡：
            # **还有没关的工单、或按当前规则还有硬问题 → 才算真的该剔**。
            open_tickets = [x for x in (payload.get("工单") or [])
                            if str(x.get("status")) != "closed"]
            still_hard = _fresh_checks(Path(lessons_dir), lesson, memo).get("problems") or []
            if open_tickets or still_hard:
                dropped.append({"unit": unit, "chapter": str(lesson.get("chapter") or ""),
                                "原因": conclusion,
                                "未关工单": len(open_tickets), "硬问题": still_hard[:2],
                                "token": _tokens_for(runs, unit),
                                "证据": f"audit/{unit}.loop.json#结论"})
                seen.add(unit)
    for chapter, payload in (runs or {}).items():
        for row in payload.get("节") or []:
            unit = str(row.get("unit_id") or "?")
            conclusion = str(((row.get("steps") or {}).get("回路") or {}).get("结论") or "")
            if unit in seen or not any(bad in conclusion for bad in _BAD_CONCLUSIONS):
                continue
            # 逐章记录里说冻结、但这一节没有自己的回路记录（老产物）→ 沿用它的结论
            if not (audit_dir / f"{unit}.loop.json").is_file():
                dropped.append({"unit": unit, "chapter": chapter, "原因": conclusion,
                                "token": _tokens_for(runs, unit),
                                "证据": f"chapter-runs/{chapter}.json#节[{unit}].steps.回路"})
                seen.add(unit)
    return dropped


def unresolved_lessons(lessons_dir: Path, dropped: list[dict],
                       memo: dict | None = None) -> list[dict]:
    """**有硬问题、但还没到"整节不敢交"的程度**的节 —— 列出来，但**不剔除**。

    为什么和"剔除"分开（2026-09-26 想清楚的一件事）：
    决策 4 说"反复改不好（无进展/冻结）的节不进交付包" —— 那说的是**内容不过关**（会教错、
    会卡住读者）。而有些硬问题只是**约束没守**（实测：「详解 what 太长（99 字）」、
    「批注标题提到 $F$ 没讲」），把它们整节撤掉是**过度反应**：
    读者那边少一整节，而问题只是"一个字段超了 39 个字"。
    所以：无进展/冻结 → 剔除；只是有硬问题 → **如实列在包里的"还没做好的"**，节照常交付。
    """
    out = []
    for lesson in _lesson_rows(lessons_dir):
        unit = str(lesson.get("unit_id") or "?")
        if any(row["unit"] == unit for row in dropped):
            continue
        hard = _fresh_checks(lessons_dir, lesson, memo).get("problems") or []
        if hard:
            out.append({"unit": unit, "chapter": str(lesson.get("chapter") or ""),
                        "硬问题": hard[:4], "条数": len(hard),
                        "证据": f"lessons/{unit}.json#checks.problems（按当前规则重算）"})
    return out


def metrics(per_unit: dict, per_page: dict, runs: dict, dropped: list[dict] | None = None) -> dict:
    """交付声明要用的数字。**每一项都必须能从产物里再算一遍**（`verify` 会验）。

    ⚠️ 统计口径 = **交付包里真有哪几节**（不是磁盘上有几节产物）：
    决策 4 掉了的节不进包，它们要是被算进"共 N 节"，声明就又和包对不上了。
    """
    dropped_units = {str(row.get("unit")) for row in (dropped or [])}
    shipped = set()
    for info in (per_page or {}).values():
        shipped |= {str(u) for u in (info.get("节") or [])}
    lessons = [row for unit, row in per_unit.items()
               if unit not in dropped_units and (not shipped or unit in shipped)]
    tokens_by_chapter = {chapter: int(payload.get("总花费") or 0)
                         for chapter, payload in (runs or {}).items()}
    return {
        "节数": len(lessons),
        "未进包的节": len(dropped_units),
        "套话": sum(int(row.get("套话") or 0) for row in lessons),
        "篇幅": {
            "最短": min((int(row.get("字数") or 0) for row in lessons), default=0),
            "最长": max((int(row.get("字数") or 0) for row in lessons), default=0),
            "全部达地板": all(int(row.get("字数") or 0) >= int(row.get("地板") or 0)
                          for row in lessons) if lessons else False,
        },
        "详解": {
            "条数": sum(int(row.get("详解") or 0) for row in lessons),
            "被引用": sum(int(row.get("详解引用") or 0) for row in lessons),
        },
        "例题": {
            "步数": sum(int(row.get("例题步数") or 0) for row in lessons),
            "有依据步数": sum(int(row.get("有依据步数") or 0) for row in lessons),
        },
        "模型": sum(int(row.get("模型") or 0) for row in lessons),
        "token": {"总计": sum(tokens_by_chapter.values()), "逐章": tokens_by_chapter},
        "网页": per_page,
    }


def claims(payload: dict, *, include_cost: bool = True) -> list[dict]:
    """把数字变成**声明句子**（交付包直接渲染这些，不再手写）。

    每条声明都带 `evidence`：哪个文件、哪个字段。交付包自检会照着它重新算一遍 ——
    "包里出现的每个数字都能指回产物"就是这么落地的（缺口 B 的机器判据）。

    `include_cost=False`：**不把"成本"那条声明写进交付物**（2026-09-27 用户要求
    "交付物里不出现耗时/花费"，账目只留在仓库内部文档里）。
    注意：**只是不生成那条声明**，`metrics.逐章` 里的 token 数照旧保留 ——
    内部对账还要用（`--hide-cost` 不影响 `verify` 之外的东西）。
    """
    metrics_ = payload.get("metrics") or {}
    length = metrics_.get("篇幅") or {}
    explain = metrics_.get("详解") or {}
    examples = metrics_.get("例题") or {}
    tokens = metrics_.get("token") or {}
    skipped = int(metrics_.get("未进包的节") or 0)
    out = [
        {"说": f"本包共 {metrics_.get('节数', 0)} 节"
               + (f"（另有 {skipped} 节反复改不好、未进本包，见「还没做好的」）" if skipped else "")
               + f"；套话（综上所述/值得注意的是…）{metrics_.get('套话', 0)} 处",
         "evidence": ["lessons/*/lessons/*.json#checks.stats.套话"]},
        {"说": f"正文篇幅 {length.get('最短', 0)}~{length.get('最长', 0)} 字，"
               f"全部达到地板（max(250 字, 原文×0.6)）："
               f"{'是' if length.get('全部达地板') else '否'}；**没有上限**（长不算问题）",
         "evidence": ["lessons/*/lessons/*.json#checks.stats.正文地板"]},
        {"说": f"公式/定理详解 {explain.get('条数', 0)} 条，被正文引用 "
               f"{explain.get('被引用', 0)} 处",
         "evidence": ["lessons/*/lessons/*.json#explainables",
                      "lessons/*/lessons/*.json#checks.stats.详解引用"]},
        {"说": f"例题 {examples.get('步数', 0)} 步，其中 "
               f"{examples.get('有依据步数', 0)} 步带「这一步的依据」",
         "evidence": ["lessons/*/lessons/*.json#examples[].steps[].basis"]},
        {"说": f"交互模型 {metrics_.get('模型', 0)} 个（按需要出：前面没讲过的概念不配）",
         "evidence": ["lessons/*/lessons/*.json#models"]},
    ]
    if include_cost:
        out.append(
            {"说": "成本（各章**最近一次**跑批）："
                   + "；".join(f"{chapter} {value/10000:.1f} 万 token"
                              for chapter, value in (tokens.get("逐章") or {}).items()),
             "evidence": ["lessons/*/chapter-runs/*.json#总花费"]})
    return out


def collect(*, lessons_dir: Path, delivery_dirs: list[Path] | None = None,
            audit_dir: Path | None = None, include_cost: bool = True) -> dict:
    """把各块产物收成一份 problems.json（这个块的全部对外能力）。"""
    lessons_dir = Path(lessons_dir)
    runs = from_chapter_runs(lessons_dir)
    # 一次 collect 内共用一份"按当前规则重算"的结果（A10）：同一节不再算 6 遍
    memo: dict = {}
    problems, notes, per_unit = from_lessons(lessons_dir, memo)
    page_problems, page_notes, per_page = from_delivery(list(delivery_dirs or []))
    audit_problems, audit_notes, loop_rows = ([], [], {})
    if audit_dir and Path(audit_dir).is_dir():
        audit_problems, audit_notes, loop_rows = from_audit(Path(audit_dir))
    problems += page_problems + audit_problems
    notes += page_notes + audit_notes
    per_unit = {**per_unit, **{k: {**per_unit.get(k, {}), **v} for k, v in loop_rows.items()}}

    # 去重：同一个单元 + 同一句话只留一条（来源合并）
    seen, merged = {}, []
    for item in problems:
        key = (item.get("unit"), "".join(str(item.get("message")).split())[:80])
        if key in seen:
            merged[seen[key]]["来源"] = merged[seen[key]].get("来源", []) + [item["source"]]
            continue
        item = {**item, "来源": [item.pop("source")]}
        seen[key] = len(merged)
        merged.append(item)
    merged.sort(key=lambda row: (_SEVERITY_RANK.get(row.get("severity"), 9),
                                 str(row.get("unit") or ""), str(row.get("message"))))

    dropped = dropped_sections(lessons_dir, runs, audit_dir, memo)
    payload = {
        "_meta": {"file_type": "problems", "schema_version": SCHEMA_VERSION,
                  "generator": GENERATOR,
                  "field_notes": {
                      "problems[]": "硬问题：会挡住「通过」的。来源块、单元、原句、状态、证据",
                      "notes[]": "记录但不挡的（范围外的插图、自动合并的重复概念名……）",
                      "dropped[]": "被丢掉的节（决策 4：无进展/冻结）→ 不进交付包，"
                                   "只在内部包里列出原因与烧掉的 token",
                      "metrics{}": "交付声明用的数字；每一项都要能从产物再算一遍",
                      "claims[]": "由 metrics 生成的声明句子（交付包直接渲染，不许手写）",
                  }},
        "problems": merged,
        "notes": notes,
        "dropped": dropped,
    }
    payload["未解决的节"] = unresolved_lessons(lessons_dir, dropped, memo)
    payload["metrics"] = metrics(per_unit, per_page, runs, dropped)
    payload["claims"] = claims(payload, include_cost=include_cost)
    return payload


def verify(payload: dict, *, lessons_dir: Path, delivery_dirs: list[Path] | None = None,
           audit_dir: Path | None = None, fresh: dict | None = None) -> list[str]:
    """**声明复核**：把 claims 里的数字重新从产物算一遍，对不上就报出来。

    这一条就是"包里的每个数字都能指回产物"的机器判据 —— 手写数字改不动产物、
    产物变了手写数字也不会变，只有现算才可能永远一致。
    """
    # ⚠️ `fresh` 可以由调用方传进来（A16）：打包时刚刚 `collect` 过一份，
    #    再整个重算一遍纯属重复（每节 3 次 `fresh_checks`）。不传才现算。
    if fresh is None:
        fresh = collect(lessons_dir=lessons_dir, delivery_dirs=delivery_dirs, audit_dir=audit_dir)
    mismatches = []
    if fresh["metrics"] != payload.get("metrics"):
        old, new = payload.get("metrics") or {}, fresh["metrics"]
        for key in sorted(set(old) | set(new)):
            if old.get(key) != new.get(key):
                mismatches.append(f"指标「{key}」与产物对不上：声明 {old.get(key)} "
                                  f"≠ 现算 {new.get(key)}")
    if [c.get("说") for c in fresh.get("claims") or []] != \
       [c.get("说") for c in payload.get("claims") or []]:
        mismatches.append("声明句子与产物对不上（重跑 collect 就能看到新的）")
    if len(fresh.get("problems") or []) != len(payload.get("problems") or []):
        mismatches.append(f"硬问题条数对不上：声明 {len(payload.get('problems') or [])} "
                          f"≠ 现算 {len(fresh.get('problems') or [])}")
    return mismatches


__all__ = ["collect", "verify", "claims", "metrics", "dropped_sections",
           "unresolved_lessons",
           "from_lessons", "from_delivery", "from_audit", "from_chapter_runs",
           "GENERATOR", "SCHEMA_VERSION"]
