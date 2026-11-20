"""计划（planner）：**只读、零副作用**，把请求变成 `ScopePlan`。

方案 §6 的两条硬要求，直接决定了本模块的写法：

> `plan` 只返回计划，**不创建运行目录、写 pdf.path、改 scope 或修 doctor 记录**。

> 冷启动的只读计划将 `resolved_scope` 标为 `null`、`requires_preparation=true`，
> 明确展示符号化选择与准备任务；run 在 catalog 形成后先校验并固定 scope，合法才继续。
> **不能为让计划好看而虚构单元数。**

所以 `plan()` 里没有一次写操作，也没有一处"猜一个数量"。读不到 catalog 时它返回
`resolved_scope=None`（而不是空列表或凭 `--chapters CH03` 造 12 个 `CH03-Sxx`）——
因为后两者会让界面显示"0 或 12 个单元"，与实际执行范围不符。

方案 §4 的范围规则也在这里落地：

> 章输入去重后按教材顺序排序；未知编号、空集合、跨书单元、反向区间在解析范围时失败。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..contracts.enums import Goal, SelectorKind
from ..contracts.request import RunRequest, Selectors
from ..contracts.scope import PreparationTask, ScopePlan
from ..core.digest import digest_file
from ..core.registry import BookContext, BookRegistry
from ..errors import InputError
from .graph import PARSE_STAGES

CHAPTER_RE = re.compile(r"^CH(\d{1,3})$")
RANGE_RE = re.compile(r"^CH(\d{1,3})\s*-\s*CH(\d{1,3})$")

#: 解析阶段必须按顺序完成的基础步骤（方案 §6 的 mermaid 图按阶段名对齐）


@dataclass(frozen=True)
class Unit:
    """catalog 里的一个教学单元。"""
    unit_id: str
    level: str
    chapter: str
    title: str
    anchor: str = ""
    original_text: str = ""

    @property
    def key(self) -> str:
        return self.unit_id


@dataclass(frozen=True)
class Catalog:
    """`chapters.json` 的只读视图。`chapter_order` 固定为教材顺序。"""
    path: Path
    digest: str
    chapter_order: tuple[str, ...]
    chapter_titles: dict[str, str]
    units: tuple[Unit, ...]

    def by_id(self) -> dict[str, Unit]:
        return {u.unit_id: u for u in self.units}

    def units_of_chapter(self, chapter: str) -> list[Unit]:
        return [u for u in self.units if u.chapter == chapter]


def read_catalog(parse_root: Path) -> Catalog | None:
    """读 `chapters.json`。**不存在就返回 `None`**（冷启动），坏文件则报错。

    为什么坏文件要报错而不是当冷启动：那会把"解析产物损坏"变成"再跑一遍解析"，
    重复执行准备工作却掩盖了损坏原因。
    """
    path = parse_root / "chapters.json"
    if not path.is_file():
        return None
    import json
    from ..errors import ConfigError
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ConfigError(f"{path} 不是合法 JSON：{exc}") from exc
    units_raw = payload.get("lesson_units")
    if not isinstance(units_raw, list):
        raise ConfigError(f"{path} 里没有 lesson_units 数组——不是当前的 catalog 契约")
    chapters_raw = payload.get("chapters")
    if not isinstance(chapters_raw, list):
        raise ConfigError(f"{path} 里没有 chapters 数组")

    order: list[str] = []
    titles: dict[str, str] = {}
    for entry in chapters_raw:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        cid = str(entry["id"])
        order.append(cid)
        titles[cid] = str(entry.get("title") or cid)

    ordered: list[tuple[int, Unit]] = []
    for i, entry in enumerate(units_raw):
        if not isinstance(entry, dict) or not entry.get("unit_id"):
            continue
        ordered.append((i, Unit(
            unit_id=str(entry["unit_id"]),
            level=str(entry.get("level") or "unknown"),
            chapter=str(entry.get("chapter") or ""),
            title=str(entry.get("title") or ""),
            anchor=str(entry.get("anchor") or ""),
            original_text=str(entry.get("original_text") or ""),
        )))
    # 章顺序固定为教材顺序；同章内保持 catalog 原有次序（用原始下标做稳定键）
    rank = {cid: i for i, cid in enumerate(order)}
    ordered.sort(key=lambda pair: (rank.get(pair[1].chapter, len(rank)), pair[0]))
    units = [u for _i, u in ordered]
    return Catalog(path=path, digest=digest_file(path), chapter_order=tuple(order),
                   chapter_titles=titles, units=tuple(units))


def _chapter_token(raw):
    """Reject invalid syntax before any catalog exists or preparation begins."""
    token = str(raw).strip()
    if not token:
        raise InputError('章选择器里有空项')
    match = RANGE_RE.match(token)
    if match:
        if int(match.group(2)) < int(match.group(1)):
            raise InputError(f'反向区间 {token!r}：起点在终点之后')
    elif not CHAPTER_RE.match(token):
        raise InputError(f'章编号 {token!r} 格式不对（应形如 CH03 或 CH02-CH04）；不做模糊识别')
    return token, match


def expand_selector_tokens(values: tuple[str, ...], chapter_order: tuple[str, ...]) -> list[str]:
    """把 `CH03`、`CH02-CH04` 归一成章 id 列表；去重、按教材顺序、拒绝反向区间。

    方案 §4：

    > 章输入去重后按教材顺序排序；未知编号、空集合、跨书单元、反向区间在解析范围时失败。
    > CH 编号只在指定解析版本中解析，**不做字符串前缀匹配**。
    """
    known = set(chapter_order)
    index = {cid: i for i, cid in enumerate(chapter_order)}
    picked: set[str] = set()
    for raw in values:
        token, m = _chapter_token(raw)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            ids = [cid for cid in chapter_order
                   if CHAPTER_RE.match(cid) and lo <= int(CHAPTER_RE.match(cid).group(1)) <= hi]
            if not ids:
                raise InputError(f"区间 {token!r} 在本书里不覆盖任何章；本书有：{chapter_order}")
            picked.update(ids)
            continue
        if token not in known:
            raise InputError(f"本书没有 {token!r} 这一章；有的是：{chapter_order}")
        picked.add(token)
    if not picked:
        raise InputError("章选择器解析后是空集合——空范围会被误读成'跑整本'")
    return sorted(picked, key=lambda cid: index[cid])


def _resolve_units(selectors: Selectors, catalog: Catalog) -> list[Unit]:
    by_id = catalog.by_id()
    if selectors.kind is SelectorKind.ALL:
        return [by_id[u.unit_id] for u in catalog.units]
    if selectors.kind is SelectorKind.CHAPTERS:
        chapters = expand_selector_tokens(selectors.values, catalog.chapter_order)
        picked: list[Unit] = []
        for cid in chapters:                       # 教材顺序，不是用户输入顺序
            picked.extend(catalog.units_of_chapter(cid))
        return picked
    # units
    out: list[Unit] = []
    seen: set[str] = set()
    unknown: list[str] = []
    for token in selectors.values:
        if token not in by_id:
            unknown.append(token)
            continue
        if token in seen:
            continue
        seen.add(token)
        out.append(by_id[token])
    if unknown:
        raise InputError(
            f"书 {catalog.path.parent.name} 里没有这些教学单元：{unknown}。"
            f"单元键必须来自本次解析版本的 catalog（方案 §4：不做字符串前缀匹配）")
    if not out:
        raise InputError("单元选择器解析后是空集合")
    rank = {u.unit_id: i for i, u in enumerate(catalog.units)}
    return sorted(out, key=lambda u: rank[u.unit_id])   # 教材顺序


def _preparation(ctx: BookContext, catalog: Catalog | None) -> tuple[PreparationTask, ...]:
    """现在还缺什么才能开始生成。**只读检查**，不修任何东西。"""
    tasks: list[PreparationTask] = []
    if catalog is None:
        for stage in PARSE_STAGES:
            tasks.append(PreparationTask(
                stage=stage, reason=f"这本书还没有 catalog（缺 {ctx.parse / 'chapters.json'}）",
                scope="book", produces=f"parse.{stage}",
                note="全书基础准备的一部分；冷启动计划里先列出来，不猜单元数"))
        tasks.append(PreparationTask(
            stage="concepts", reason="全书概念索引要先建好，讲解阶段要按节取用",
            scope="book", produces="parse.concepts",
            note="方案 §6：全书有内容的教学单元逐项完成/明确无概念，完整索引冻结后才生成讲解"))
        return tuple(tasks)

    concepts = ctx.parse / "concepts.json"
    missing_text = [u.unit_id for u in catalog.units if u.original_text and
                    not (ctx.parse / u.original_text).is_file()]
    if missing_text:
        tasks.append(PreparationTask(stage='export', scope='book', produces='parse.catalog',
                                     reason=f'缺少单元原文：{missing_text}',
                                     note='ensure 在运行副本中重导出并重新绑定正文；不修改原始输入'))
    if not concepts.is_file():
        tasks.append(PreparationTask(
            stage="concepts", reason=f"缺 {concepts}", scope="book",
            produces="parse.concepts",
            note="只有部分分片时不算完成：失败分片不能用旧片冒充新鲜"))
    return tuple(tasks)


def plan(request: RunRequest, registry: BookRegistry, *, context: BookContext | None = None,
         preparation_completed: bool = False) -> ScopePlan:
    """**只读**计划。不建目录、不写文件、不调用模型。"""
    request.validate()
    if request.selectors.kind is SelectorKind.CHAPTERS:
        for token in request.selectors.values:
            _chapter_token(token)
    if context is None:
        from .preparation import prepared_context
        original = registry.resolve(request.book_id)
        ctx = original if request.prepare_policy.value == 'refresh' else prepared_context(original)
    else:
        ctx = context
    catalog = read_catalog(ctx.parse)
    from .preparation import preparation_freshness
    if request.profile.value == 'smoke':
        # Smoke stages never perform source adaptation or hosted preparation.
        tasks = _preparation(ctx, catalog)
        freshness = {'invalidates_catalog':catalog is None,
                     'required':{t.stage:t.reason for t in tasks}}
    else:
        freshness = preparation_freshness(ctx, refresh=(request.prepare_policy.value == 'refresh'
                                                       and not preparation_completed))
    preparation = tuple(PreparationTask(stage=stage, scope='book', produces='parse.'+stage,
        reason=reason, note='全书基础准备在本次运行副本中完成；不修改原始导入产物')
        for stage, reason in freshness['required'].items())

    units: list[Unit] = []
    resolved: tuple[str, ...] | None = None
    notes: list[str] = []

    if catalog is None or freshness['invalidates_catalog']:
        notes.append(
            f"基础目录尚未就绪或执行时将重建，当前 {ctx.parse} 不能把 "
            f"{request.selectors.as_dict()} 解析成具体单元。"
            f"计划**不虚构单元数**——先做完全书基础准备，再核实范围。")
    else:
        try:
            units = _resolve_units(request.selectors, catalog)
        except InputError:
            # 冷启动的编造不行，误报也不行：范围非法就如实抛出去（CLI 会映射成退出码 2）
            raise
        resolved = tuple(u.unit_id for u in units)

    # These remain descriptive strings in ScopePlan, not paths to create now.
    # A prepared_context may read a prior run; new outputs use the book's run root.
    from .state import book_runs_root
    runs = book_runs_root(ctx.workspace, ctx.book_id)
    run = runs / '{new_run}'
    targets = [run, run / 'work/products']
    if preparation:
        targets.extend((run / 'work/prepared-parse', runs.parent / 'prepared/current.json'))
    if request.goal is Goal.DELIVER:
        targets.extend((run / 'work/media', run / 'work/legacy/render'))
        if request.profile.release_ready_possible:
            targets.append(ctx.releases_root)
            notes.append('正式交付通过后才写入发布目录；具体章或选区由发布清单确定。')
    notes.append('写入目标中的 {new_run} 是执行时分配的运行标识模板，计划不创建目录：'
                 + '、'.join(map(str, targets)))

    return ScopePlan(
        request_digest=request.digest(),
        book_id=ctx.book_id,
        goal=request.goal,
        profile=request.profile,
        selectors=request.selectors,
        resolved_scope=resolved,
        requires_preparation=bool(preparation),
        chapter_order=(catalog.chapter_order if catalog else ()),
        preparation=preparation,
        read_dependencies=(str(ctx.parse), str(ctx.lessons)),
        write_targets=tuple(map(str, targets)),
        context_ref=(f"catalog:{catalog.digest}" if catalog and not freshness['invalidates_catalog'] else None),
        invalidations=(),
        notes=tuple(notes),
    )


__all__ = ["CHAPTER_RE", "RANGE_RE", "PARSE_STAGES", "Unit", "Catalog", "read_catalog",
           "expand_selector_tokens", "plan"]
