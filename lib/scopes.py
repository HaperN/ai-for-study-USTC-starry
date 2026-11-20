"""交付页的**范围**：一个页面包含哪几节 —— 唯一实现。

为什么单独抽出来（2026-09-26 结构审计）：这段逻辑原来在两个文件里各写了一遍 ——
`tools/run_chapter.py` 的 `page_scope()`（建页用）和
`tools/make_delivery_package.py` 的 `planned_scope()`（打包用）。
两份只差"没有记录时回退到什么"，改一处不会带动另一处，
于是"打包剔掉的节"和"建页算的范围"会打架。

规则（只许长大、不许缩小）：
    范围 = `范围.json` 的「原本要做」 ∪ 页面现有 scope ∪ 这一趟跑的节
* `范围.json` 是**剔除操作不动**的记录 → 被剔掉的节修好后能自动回来；
* 页面现有 scope 是事实 → 后来新增的节会被承认（否则新节会被打包当成"不该在页面上"剔掉）；
* 这一趟跑的节 → 第一次建页时范围就是它。
"""
from __future__ import annotations

import json
from pathlib import Path

PLAN_NAME = "范围.json"
INDEX_NAME = "render.index.json"
PLAN_KEY = "原本要做"
PLAN_NOTES = ("这一份交付页计划包含哪些节；出问题的节被临时剔除时，这个记录不变 —— "
              "修好之后能自动放回来；后来新增的节会并进这里（记录只长大、不缩小）")


def _read_plan(delivery_dir: Path) -> list[str]:
    plan = Path(delivery_dir) / PLAN_NAME
    if not plan.is_file():
        return []
    doc = _load(plan)
    if not isinstance(doc, dict):
        return []
    return [str(x) for x in (doc.get(PLAN_KEY) or doc.get("scope") or [])]


def _read_page(delivery_dir: Path) -> list[str]:
    index_path = Path(delivery_dir) / INDEX_NAME
    if not index_path.is_file():
        return []
    doc = _load(index_path)
    if not isinstance(doc, dict):
        return []
    return [str(x) for x in (doc.get("scope") or [])]


def _load(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def scope_of(delivery_dir: Path, ran: list[str] | None = None) -> list[str]:
    """范围 = 记录 ∪ 页面现有 ∪ 这一趟跑的（顺序：记录 → 页面 → 跑的，去重）。"""
    keep: list[str] = []
    for source in (_read_plan(delivery_dir), _read_page(delivery_dir), list(ran or [])):
        for unit in source:
            if unit not in keep:
                keep.append(unit)
    return keep


def write_plan(delivery_dir: Path, scope: list[str]) -> Path:
    """把范围写回 `范围.json`（只长大：调用方传的是 `scope_of` 的结果）。

    ⚠️ **必须自己建目录**（2026-09-26 生产版 A6 实测抓出，第二个 0 秒级真 bug）：
    原来这里直接 `write_text`，于是交付目录**还不存在**时抛
    `FileNotFoundError: /tmp/a6/serial/delivery/CH03/范围.json`。

    仓库内为什么一直没暴露：`delivery/<章>/` 早被历次跑建好了 —— 也就是说
    **从零起步（新书 / 新机器 / 空交付目录）必然崩**，而"整本并行"恰恰是从空目录开跑。
    后果还会**一路冒泡成 worker 整体崩溃**：`run_chapter` 在"每个单元跑完就重建这一页"
    那一步死掉，界面上看起来像"整章跑不动"，真实原因被埋在最底下。

    教训（和 `display_path` 是同一类）：**写文件的人负责保证目录在**，
    不能指望"上一次跑已经把它建好了"。
    """
    plan = Path(delivery_dir) / PLAN_NAME
    payload = json.dumps({"_meta": {"file_type": "delivery_scope",
                                    "field_notes": {PLAN_KEY: PLAN_NOTES}},
                          PLAN_KEY: list(scope)}, ensure_ascii=False, indent=2)
    if not plan.is_file() or plan.read_text(encoding="utf-8") != payload:
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(payload, encoding="utf-8")
    return plan


def sync_plan(delivery_dir: Path, ran: list[str] | None = None) -> tuple[list[str], list[str]]:
    """算范围 + 写回记录。返回 `(范围, 这次新增的节)`。"""
    before = _read_plan(delivery_dir)
    scope = scope_of(delivery_dir, ran)
    if scope:
        write_plan(delivery_dir, scope)
    return scope, [u for u in scope if u not in before]


# ── "这一页 / 这个模型是不是过期产物"：两处判断，**给编排器用**（2026-09-26）──────
# 为什么需要（链路审计 A14/A18/A21）：`run_chapter` 只知道"文件在不在"，
# 不知道"是不是过期" —— 于是补跑一节会把整页重建一遍（0 token 但要时间），
# 教案重写后交互模型**不会**重跑（页面上的模型落点可能已经错了）。
# 判断依据是**版本号**，不是 mtime（mtime 在复制/检出之后就不可信 —— 项目在别处吃过这个亏）。



def models_are_fresh(models_dir: Path, lessons_dir: Path,
                     units: list[str] | None = None) -> tuple[bool, list[str]]:
    """交互模型是不是跟**当前这一版教案**对得上。返回 `(是否新鲜, 不新鲜的原因)`。

    `models/<unit>.json#_meta.source_lesson_revision` 由模型块写入（`write_models`）。
    没记这个字段的旧产物 → 判**不新鲜**（保守：重跑一次只花 ~9k token，
    而"模型落在错位置"要靠人看才发现）。
    """
    stale: list[str] = []
    for unit in (units or sorted(p.stem for p in Path(models_dir).glob("*.json")
                                 if not p.stem.endswith(".handwritten"))):
        lesson = Path(lessons_dir) / "lessons" / f"{unit}.json"
        if not lesson.is_file():
            continue
        try:
            now = int((_load(lesson) or {}).get("revision") or 0)
        except (ValueError, OSError):
            continue
        model_path = Path(models_dir) / f"{unit}.json"
        if not model_path.is_file():
            stale.append(f"{unit}: 还没有模型")
            continue
        try:
            meta = (_load(model_path) or {}).get("_meta") or {}
        except (ValueError, OSError):
            stale.append(f"{unit}: 模型产物读不出来")
            continue
        if "source_lesson_revision" not in meta:
            stale.append(f"{unit}: 模型没记教案版本（旧产物）→ 判为过期")
            continue
        if int(meta.get("source_lesson_revision") or 0) != now:
            stale.append(f"{unit}: 模型用 revision {meta['source_lesson_revision']} 生成，"
                         f"教案现在是 {now}")
    return (not stale), stale
