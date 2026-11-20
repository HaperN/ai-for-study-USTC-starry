#!/usr/bin/env python3
"""`doctor`：把**不变量**变成可执行的检查，一条命令问"现在这套产物自洽吗"。

## 为什么需要它（三份调研共同的结论）

用户 2026-09-26 问：「为什么总出错？这样的程序如何之后跑自动化呢？」
把这个问题当调研做了一遍，证据（`问题清单.md` 全量清点 + 代码结构审计 + 业界做法）指向同一个结论：

> 出错最多的**不是模型算错数学**，而是「同一份信息写了两处」和「存了一份会过期的副本」。
> 而这两件事**没有任何一行代码会报错** —— 不变量没写下来，就等于不存在。

所以这里的每一条检查，都对应一个**真实发生过**的事故（括号里是编号，见问题清单）：
  * 清单写两份：`DELIVERY_DIRS` 漏了「第六章-两节」→ 决策 4 的保护漏了一页（§17.5④）
  * 范围只防缩水：新做的 §6.3.3 被打包剔掉（§17.2③）
  * 章记录陈旧：§6.3.2 补跑通过了，记录里还写"未通过"（§17.3）
  * 教案快照过期：规则改了，`lesson["checks"]` 还是旧结论（§11.4，一天四踩）
  * 「卡在哪」记录与回路结论不一致（§15）
  * 交付页产物不全 / counts 对不上 scope（§10.5）

## 用法与退出码约定（调研建议的"退出码不许塌陷"）

    ../.venv-linux/bin/python tools/doctor.py            # 人看的摘要
    ../.venv-linux/bin/python tools/doctor.py --json     # 机器读

退出码：**0 = 全部通过；1 = 有违规（不是崩溃，是被发现的漂移）；2 = doctor 自己坏了**。
「有违规」和「崩了」必须分开 —— 否则真失败会藏在假失败里（实测：体检"发现问题返回 1"
被打成「✗ 失败」，于是真崩溃和正常告警长得一模一样）。
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

DELIVERY_ROOT = ROOT / "delivery"
INDEX_NAME = "lessons.index.json"      # historical read-only index


def _resolve_book(book: str | None = None):
    """默认那本书（或 `--book` 指的那本）的四个位置 —— 走 `lib/book.py` 这一份解析（R1）。"""
    import book as _book
    return _book.resolve(book)


_DEFAULT_BOOK = _resolve_book()
LESSONS = _DEFAULT_BOOK.lessons
AUDIT = _DEFAULT_BOOK.audit
PARSE = _DEFAULT_BOOK.parse
BOOK = _DEFAULT_BOOK.book_id


def use_book(book: str | None) -> None:
    """把 doctor 的检查范围切到另一本书（`--book`）。

    为什么是"重新绑定模块级常量"而不是把 context 传遍每个检查函数：
    现有 10 个检查函数与测试都直接用 `PARSE` / `LESSONS` / `AUDIT`（测试还 monkeypatch 它们），
    改成传参要动 10 个函数与全部测试，收益只是"看上去更函数式"。
    重绑一处、语义足够清楚：**doctor 一次只查一本书**。
    """
    global LESSONS, AUDIT, PARSE, BOOK
    ctx = _resolve_book(book)
    LESSONS, AUDIT, PARSE, BOOK = ctx.lessons, ctx.audit, ctx.parse, ctx.book_id


_PKG = None


def _pkg():
    """加载打包工具（拿它的 SECTIONS/DELIVERY_DIRS）—— 清单只有那一份。"""
    global _PKG
    if _PKG is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "pkg_for_doctor", ROOT / "tools" / "make_delivery_package.py")
        _PKG = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_PKG)
    return _PKG


def delivery_pages(include_strays: bool = False):
    """要检查的交付页。

    ⚠️ 默认**只查当前交付页**（`SECTIONS` 里那几个）：`delivery/` 下面还躺着若干
    历史的试点目录（`第二章试点-v3`、`线性代数-样张`…），它们不是交付物。
    第一版把整个目录扫进来，一下报了 4 条"没有 范围.json"—— **假警报会把真失败淹掉**，
    这正是"体检退出码"那个教训（见 tools/doctor.py 模块头）。要看历史的用 --all。
    """
    if include_strays:
        return sorted(d for d in DELIVERY_ROOT.glob("*") if (d / "render.index.json").is_file())
    return [Path(p) for p in _pkg().DELIVERY_DIRS if (Path(p) / "render.index.json").is_file()]


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ────────────────────────────────────────── 每条不变量：返回违规列表（空 = 通过）

def inv_delivery_lists_agree():
    """交付目录清单只能有一份。"""
    pkg = _pkg()
    names = sorted(p.name for p in pkg.DELIVERY_DIRS)
    want = sorted(Path(p).name for p, _t, _s in pkg.SECTIONS)
    if names != want:
        return [{"不变量": "交付目录清单只有一份",
                 "说明": f"DELIVERY_DIRS 与 SECTIONS 对不上：{names} != {want}",
                 "证据": "tools/make_delivery_package.py",
                 "后果": "清单漏掉的那一页不受「出问题的节不发出去」的保护"}]
    return []


def inv_scope_records_never_shrink(include_strays: bool = False):
    """交付页的范围记录不许缩水，页面也不许越界，记录里的节必须有产物。"""
    out = []
    for d in delivery_pages(include_strays):
        idx = _read_json(d / "render.index.json")
        plan = _read_json(d / "范围.json")
        if idx is None:
            continue                      # 不是交付页（比如纯素材目录）
        scope = [str(x) for x in (idx.get("scope") or [])]
        if plan is None:
            out.append({"不变量": "范围记录不缩水", "说明": f"{d.name} 没有 范围.json",
                        "证据": str(d / "范围.json"), "后果": "没有记录，剔掉的节永远回不来"})
            continue
        planned = [str(x) for x in (plan.get("原本要做") or [])]
        for unit in scope:
            if unit not in planned:
                out.append({"不变量": "范围记录不缩水",
                            "说明": f"{d.name}：页面里有 {unit}，但范围记录里没有",
                            "证据": str(d / "render.index.json"),
                            "后果": "打包时会把这节当成「不该在页面上」剔掉"})
        for unit in planned:
            if not (LESSONS / "lessons" / f"{unit}.json").is_file() and \
               not (PARSE / "concepts.parts" / f"{unit}.json").is_file():
                out.append({"不变量": "范围记录不缩水",
                            "说明": f"{d.name}：范围记录里的 {unit} 既没有教案也没有概念产物",
                            "证据": str(d / "范围.json"),
                            "后果": "页面上会出现一节空内容"})
    return out


def inv_chapter_records_are_current():
    """章记录必须是**产物的视图**：节不能缺，回路结论不能陈旧。"""
    out = []
    for path in sorted((LESSONS / "chapter-runs").glob("*.json")):
        rec = _read_json(path) or {}
        chapter = str(rec.get("chapter") or path.stem)
        have = {str(r.get("unit_id")) for r in rec.get("节") or []}
        for f in sorted((LESSONS / "lessons").glob("*.json")):
            lesson = _read_json(f) or {}
            uid = str(lesson.get("unit_id") or "")
            if not uid.startswith(chapter + "-"):
                continue
            if uid not in have:
                out.append({"不变量": "章记录不陈旧",
                            "说明": f"{chapter} 记录里少了 {uid}（磁盘上有教案产物）",
                            "证据": str(path), "后果": "整个章的进度看着像丢了"})
                continue
            loop = _read_json(AUDIT / f"{uid}.loop.json") or {}
            if not loop:
                continue
            row = next((r for r in rec["节"] if str(r.get("unit_id")) == uid), {})
            recorded = str(((row.get("steps") or {}).get("回路") or {}).get("结论") or "")
            if recorded != str(loop.get("结论") or ""):
                out.append({"不变量": "章记录不陈旧",
                            "说明": f"{uid}：章记录写「{recorded[:20]}」，"
                                    f"回路产物写「{str(loop.get('结论'))[:20]}」",
                            "证据": str(path), "后果": "看到的是旧结论（补跑过也不会更新）"})
    return out


def inv_lesson_snapshots_are_fresh():
    """Historical checks remain historical; never rerun retired validators."""
    return [{"不变量":"历史质量不可继承","说明":"旧快照仅供读取；当前质量由graph 5收据证明",
              "证据":str(LESSONS),"后果":"如需当前验收，请显式导入候选到新运行"}] if (LESSONS/"lessons").is_dir() else []


def inv_stuck_record_matches_loop():
    """「卡在哪」记录的最后一条，必须与回路结论一致（记录不能停在旧状态）。"""
    out = []
    for f in sorted(AUDIT.glob("*.stuck.jsonl")):
        unit = f.name[:-len(".stuck.jsonl")]
        lines = [ln for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if not lines:
            continue
        last = json.loads(lines[-1])
        loop = _read_json(AUDIT / f"{unit}.loop.json") or {}
        if str(last.get("结论")) != str(loop.get("结论")):
            out.append({"不变量": "卡在哪记录不过期",
                        "说明": f"{unit}：stuck 记录写「{str(last.get('结论'))[:20]}」，"
                                f"回路写「{str(loop.get('结论'))[:20]}」",
                        "证据": str(f), "后果": "「卡在哪」会指向已经解决的老问题"})
    return out


def inv_delivery_pages_are_complete(include_strays: bool = False):
    """交付页产物要齐，且 render.index 里的 counts 与 scope 对得上。"""
    out = []
    for d in delivery_pages(include_strays):
        idx = _read_json(d / "render.index.json")
        if idx is None:
            continue
        for rel in ("index.html", "data/data.js", "assets/app.js"):
            if not (d / rel).is_file():
                out.append({"不变量": "交付页产物齐全", "说明": f"{d.name} 缺 {rel}",
                            "证据": str(d / rel), "后果": "双击打不开"})
        counts = idx.get("counts") or {}
        scope = idx.get("scope") or []
        if counts.get("units") not in (None, len(scope)):
            out.append({"不变量": "交付页产物齐全",
                        "说明": f"{d.name}：counts.units={counts.get('units')}，"
                                f"但 scope 有 {len(scope)} 节",
                        "证据": str(d / "render.index.json"), "后果": "页面指标对不上内容"})
    return out



# ────────────────────────────────────────── 对账（--fix）：只修"可从产物派生"的那两类
#
# 为什么只能修这两类（而不是"看到违规就自动改"）：
# 章记录的**指标与结论**、范围记录的**计划范围**都是产物的视图，重算是确定性的、0 token，
# 改错了也不会丢信息；而"教案快照过期""清单写两份"这类要**重新跑流水线/改代码**，
# 自动"修"只会把问题掩盖掉。所以 --fix 的边界写死在这里。
def fix_chapter_records(verbose=True,log=print):
    raise RuntimeError("历史目录只读；修复请使用 aifs run / resume 创建当前契约产物")


def fix_scope_records(verbose=True,log=print):
    raise RuntimeError("历史目录只读；修复请使用 aifs run / resume 创建当前契约产物")


def inv_delivery_pages_are_not_stale():
    """交付页必须是用**当前这一版教案**建的（`built_from` 对得上 `revision`）。

    为什么（2026-09-26）：`render.index.json` 原来没有"输入指纹"，于是"页面是不是过期产物"
    没人能判 —— 教案被补跑改过、页面还是旧的，而 `counts`/`scope` 看起来都正常。
    这条不变量就是调研报告里说的"给物化产物加指纹"的最小版本。
    """
    out = []
    for d in delivery_pages():
        idx = _read_json(d / "render.index.json") or {}
        built = idx.get("built_from") or {}
        if not built:
            out.append({"不变量": "交付页不过期",
                        "说明": f"{d.name} 没有 built_from（无法判断这一页是不是过期产物）",
                        "证据": str(d / "render.index.json"),
                        "后果": "教案改了页面没重建，也没人看得出来"})
            continue
        for unit, revision in sorted(built.items()):
            lesson = _read_json(LESSONS / "lessons" / f"{unit}.json")
            if lesson is None:
                out.append({"不变量": "交付页不过期",
                            "说明": f"{d.name} 里的 {unit} 已经找不到教案产物了",
                            "证据": str(d / "render.index.json"),
                            "后果": "页面上那一节是幽灵内容"})
                continue
            now = int(lesson.get("revision") or 0)
            if int(revision or 0) != now:
                out.append({"不变量": "交付页不过期",
                            "说明": f"{d.name}：{unit} 是用 revision {revision} 建的，"
                                    f"教案现在是 revision {now}",
                            "证据": str(d / "render.index.json"),
                            "后果": "页面是过期产物（重跑一次建页即可，0 token）"})
    return out




def inv_lesson_index_is_complete():
    """**A1 索引不丢行**：`lessons.index.json` 的行集合 == 磁盘 `lessons/*.json` 的集合。

    为什么必须有这条（规格 §3.1 **S5**）：索引原来是"整本一份、读-改-写"。
    单章串行时没问题；按章并行时两个 worker 各读同一份旧文件、各自写回，
    **后写的把先写的盖掉** —— 另一整章的行就消失了，而且**没有任何一行代码会报错**。
    现象是"这一章明明跑完了，进度表里却没有它"。

    一行都不许差，两个方向都查：
      * 磁盘上有教案、索引里没有 → 行被盖掉了（这就是并行丢更新）；
      * 索引里有、磁盘上没有 → 幽灵行（教案被删了，列表还在说它做过）。
    """
    index = _read_json(LESSONS / INDEX_NAME)
    disk = {p.stem for p in (LESSONS / "lessons").glob("*.json")}
    if index is None:
        if not disk:
            return []                     # 还没跑过任何一节：没有索引是正常的
        return [{"不变量": "教案索引完整", "说明": f"磁盘上有 {len(disk)} 节教案，但没有索引",
                 "证据": str(LESSONS / INDEX_NAME),
                 "后果": "进度表是空的；并行丢行时看不出少了谁"}]
    rows = {str(r.get("unit_id")) for r in (index.get("units") or []) if r.get("unit_id")}
    out = []
    lost = sorted(disk - rows)
    ghost = sorted(rows - disk)
    if lost:
        out.append({"不变量": "教案索引完整",
                    "说明": f"磁盘上有 {len(lost)} 节教案，索引里没有：{lost[:5]}",
                    "证据": str(LESSONS / INDEX_NAME),
                    "后果": "并行时被后写的 worker 盖掉了（S5 丢更新）—— 重跑 reconcile 即可"})
    if ghost:
        out.append({"不变量": "教案索引完整",
                    "说明": f"索引里有 {len(ghost)} 节教案，磁盘上没有：{ghost[:5]}",
                    "证据": str(LESSONS / INDEX_NAME),
                    "后果": "索引在说「这节做过了」，其实是幽灵行"})
    return out


def inv_concept_registration_is_complete():
    """**A2 概念登记不丢**：每节引用到的概念 id，必须**登记在这一节名下**（或进 gaps）。

    为什么必须有这条（规格 §3.1 **S6**）：occurrence 原来也是"整本一份、读-改-写"，
    并行时同样会丢；而且它还有一个更隐蔽的丢失途径 —— 概念**重抽**时
    `merge_parts` 会按"分片里出现的单元"重建 occurrences，**把教案引用产生的行全部抹掉**，
    要等那些节重新跑一遍才补回来。实测在本仓库上一次性报出 **35 条**这种丢失
    （比如 `C-0004 矩阵` 明明被 §2.1 引用了，表里却没记 §2.1）。

    判"登记在这一节名下"而不是"登记在任意一节名下"：后者查不出上面这种丢失。
    """
    out = []
    payload = _read_json(PARSE / "concepts.json") or {}
    concepts = payload.get("concepts") or []
    if not concepts:
        return []
    units_of = {str(c.get("id")): {str(o.get("unit_id")) for o in (c.get("occurrences") or [])}
                for c in concepts}
    known = set(units_of)
    gaps_payload = _read_json(PARSE / "concepts.gaps.json") or {}
    gaps = {(str(g.get("unit_id")), str(g.get("id"))) for g in (gaps_payload.get("gaps") or [])}
    # gaps 也可能是分片的（worker 只写自己那一节）
    for path in sorted((PARSE / "concepts.gaps.parts").glob("*.json")):
        for g in (_read_json(path) or {}).get("gaps") or []:
            gaps.add((str(g.get("unit_id")), str(g.get("id"))))
    missing, unknown = [], []
    for path in sorted((LESSONS / "lessons").glob("*.json")):
        lesson = _read_json(path) or {}
        uid = str(lesson.get("unit_id") or path.stem)
        for item in lesson.get("concepts") or []:
            cid = str(item.get("id") or "")
            if not cid:
                continue
            if cid not in known:
                if (uid, cid) not in gaps:
                    unknown.append(f"{uid}→{cid}")
            elif uid not in units_of.get(cid, set()):
                missing.append(f"{uid}→{cid}")
    if missing:
        out.append({"不变量": "概念登记完整",
                    "说明": f"{len(missing)} 处引用没登记在本节名下：{missing[:5]}",
                    "证据": str(PARSE / "concepts.json"),
                    "后果": "概念抽屉里少列引用它的节（读者点不到）；重跑 reconcile 即可"})
    if unknown:
        out.append({"不变量": "概念登记完整",
                    "说明": f"{len(unknown)} 处引用的概念既不在概念表、也不在 gaps：{unknown[:5]}",
                    "证据": str(PARSE / "concepts.gaps.json"),
                    "后果": "页面上的概念链接指不到东西"})
    return out


def inv_book_snapshot_is_fresh():
    """Historical checks remain historical; never rerun retired validators."""
    return [{"不变量":"历史质量不可继承","说明":"旧快照仅供读取；当前质量由graph 5收据证明",
              "证据":str(LESSONS),"后果":"如需当前验收，请显式导入候选到新运行"}] if (LESSONS/"lessons").is_dir() else []


def inv_delivery_pages_concepts_are_fresh():
    """交付页里嵌的那份**概念表副本**必须与当前概念表一致。

    为什么单列一条（2026-09-26，接手「整本并行」交接的任务 0）：
    `built_from` 只钉了每节教案的 `revision` —— **页面同样依赖概念表**
    （抽屉里的名字/解释/"出现在哪些节"都嵌在 `data.js` 里），而概念表变了
    （比如补回丢失的 occurrence）时，`built_from` 判据**天然失明**。
    实测：3 个交付页分别有 16/14/11 处概念副本与概念表不一致，没有任何东西会报错。

    判据：`render.index.json#built_from_concepts.fingerprint` == 按当前 `concepts.json`
    重算的指纹（算法与建页侧**同一个函数** `build.concept_copy_fingerprint`）。
    """
    out = []
    concepts_path = Path(PARSE) / "concepts.json"
    try:
        concepts = {c["id"]: c for c in (_read_json(concepts_path) or {}).get("concepts", [])}
    except (KeyError, TypeError):
        return [{"不变量": "交付页的概念表不过期",
                 "说明": f"{concepts_path} 读不出来或结构不对",
                 "证据": str(concepts_path), "后果": "这一条判据失效"}]
    sys.path.insert(0, str(ROOT))
    try:
        from blocks.render.build import CONCEPT_COPY_FIELDS, concept_copy_fingerprint
    except Exception as exc:                      # noqa: BLE001
        return [{"不变量": "交付页的概念表不过期",
                 "说明": f"拿不到指纹算法（{type(exc).__name__}: {exc}）",
                 "证据": "blocks/render/build.py", "后果": "这一条判据失效"}]
    for d in delivery_pages():
        idx = _read_json(d / "render.index.json") or {}
        recorded = idx.get("built_from_concepts") or {}
        if not recorded:
            out.append({"不变量": "交付页的概念表不过期",
                        "说明": f"{d.name} 没有 built_from_concepts（早于该字段）→ 判为过期",
                        "证据": str(d / "render.index.json"),
                        "后果": "概念表变了也看不出来（重建一次即可，0 token）"})
            continue
        ids = [str(x) for x in recorded.get("ids") or []]
        page_copy = {cid: {f: (concepts.get(cid) or {}).get(f) for f in CONCEPT_COPY_FIELDS}
                     for cid in ids}
        now = concept_copy_fingerprint(page_copy)["fingerprint"]
        if now != str(recorded.get("fingerprint")):
            out.append({"不变量": "交付页的概念表不过期",
                        "说明": f"{d.name} 里那份概念表副本已过期"
                                f"（存盘指纹 {recorded.get('fingerprint')} ≠ 现算 {now}）",
                        "证据": str(d / "render.index.json"),
                        "后果": "读者点概念抽屉看到的是旧的引用列表（重建一次即可，0 token）"})
    return out


CHECKS = (
    ("交付目录清单只有一份", inv_delivery_lists_agree),
    ("范围记录不缩水", inv_scope_records_never_shrink),
    ("章记录不陈旧", inv_chapter_records_are_current),
    ("教案快照不过期", inv_lesson_snapshots_are_fresh),
    ("卡在哪记录不过期", inv_stuck_record_matches_loop),
    ("交付页产物齐全", inv_delivery_pages_are_complete),
    ("交付页不过期", inv_delivery_pages_are_not_stale),
    # ── 下面三条是"整本并行"规格（§5 A1/A2/A3）新增的 ──────────────────────
    # 它们守的都是**整本级的派生视图**：并行会让这些视图"少东西而不报错"，
    # 所以必须有机器判据（调研结论：不变量没写下来就等于不存在）。
    ("教案索引完整", inv_lesson_index_is_complete),
    ("概念登记完整", inv_concept_registration_is_complete),
    ("整本快照不过期", inv_book_snapshot_is_fresh),
    # 第 11 条（接手「整本并行」交接的任务 0）：页面也依赖概念表，
    # 而 `built_from` 只钉教案 revision —— 概念表变了它看不见。
    ("交付页的概念表不过期", inv_delivery_pages_concepts_are_fresh),
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="不变量自检（0 token）：现在这套产物自洽吗")
    ap.add_argument("--json", action="store_true", help="只输出 JSON（给机器/agent 读）")
    ap.add_argument("--only", help="只跑这几条（逗号分隔，按名字匹配）")
    ap.add_argument("--all", action="store_true",
                    help="连 historical 的试点目录一起查（默认只查当前交付页）")
    ap.add_argument("--book", help="查哪一本书（见 config/books.json）；默认清单里的「默认」那本")
    ap.add_argument("--fix", action="store_true",
                    help="对账：把**可从产物派生**的记录改回来（章记录、范围记录），"
                         "然后重新检查。只修这两类 —— 别的违规必须重新跑流水线或改代码")
    args = ap.parse_args(argv)
    if args.book:
        try:
            use_book(args.book)
        except Exception as exc:              # noqa: BLE001 — 书清单的问题要**说清楚**
            print(f"✗✗ 拿不到这本书的产物位置：{exc}")
            return 2

    if args.fix:
        # 先对账，再检查（K8s 控制器那套：声明期望状态，循环把实际状态拉回来）
        n = fix_chapter_records(not args.json) + fix_scope_records(not args.json)
        if not args.json and n:
            print(f"  （共对账 {n} 处；对账只动「产物的视图」，不动教案/工单）")

    wanted = {x.strip() for x in (args.only or "").split(",") if x.strip()}
    violations: list[dict] = []
    crashed: list[str] = []
    for name, fn in CHECKS:
        if wanted and name not in wanted:
            continue
        try:
            violations += fn(include_strays=args.all) if fn.__code__.co_argcount else fn()
        except Exception as exc:          # noqa: BLE001
            # doctor 自己坏了要**单独**报，不能混进"发现违规"里（退出码 2 vs 1）
            crashed.append(f"{name}: {type(exc).__name__}: {exc}")

    payload = {"通过": not violations and not crashed,
               "检查项": [n for n, _ in CHECKS],
               "违规": violations,
               "doctor自己坏了": crashed}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for v in violations:
            print(f"✗ [{v['不变量']}] {v['说明']}")
            print(f"    后果：{v.get('后果', '')}｜证据：{v.get('证据', '')}")
        for c in crashed:
            print(f"✗✗ doctor 自己坏了：{c}")
        if not violations and not crashed:
            print(f"✓ {len(CHECKS)} 条不变量全部通过")
        else:
            print(f"\n共 {len(violations)} 条违规、{len(crashed)} 处 doctor 故障")
    if crashed:
        return 2
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
