#!/usr/bin/env python3
"""打一个**给负责人看的交付包**（P-018）—— 一条命令，可重复、可验证。

用户 2026-09-25：「你先打包好一个可以发给负责人看的文件」「演示文件 HTML 加上显示依赖也可以打包吗，
然后发更直观」。所以这个包里放**两种形态**：

* `01`/`02`/`03`：完整的网页目录（带 `assets/`、`data/`，双击 `index.html` 即可，**需要解压后**再开）；
* `05-单文件版/`：**一个 .html 自带全部依赖**（字体 base64、脚本内联、MathBox 也内联）——
  **直接从聊天/邮件里收下来双击就能看，连解压都不用**（这一版才是"发得更直观"）。

用法：

    ../.venv-linux/bin/python tools/make_delivery_package.py \
        --out /path/to/输出目录 --zip /path/to/包.zip
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "lib"))
import scopes                                                  # noqa: E402
from files import display_path                                 # noqa: E402

PYTHON = str(ROOT.parent / ".venv-linux" / "bin" / "python")

ONE_PAGER = """# 先看这个（一页纸）

**这是什么**：把教材 PDF 变成**双击就能看**的网页教材。原文一个字不改；
AI 负责把原文省略的推理补出来、把术语讲成人话、在真正需要动手的地方配一个"能拖一下"的模型，
每一步都有机器校验 + 独立审核。

**最省事的看法**：打开 `05-单文件版/` 里那 __SINGLE_COUNT__ 个 `.html`（**一个文件自带全部依赖，双击就开，不用解压、不用联网**）。
想按章翻，就看 `__DIRS__` 里的网页（要先解压整个文件夹）。

**请你花 10 分钟看三处**：

| # | 看什么 | 怎么看 |
|---|---|---|
| 1 | `__SEC0__/index.html` | 双击打开；左边目录切节。重点看：① 正文里橙色小圆点（点一下就在那句话下面展开批注）② 右侧"详解栏"（点正文里的胶囊，它是什么 + **为什么在这里用它**）③ 例题"一步一步解"每步下的**这一步的依据** |
| 2 | `__SEC1__/index.html` | 同一套版式用在更抽象的内容上（解集的结构）：这里是**符号型**模型（消元后数主元 → `dim V = n − r`），而不是花哨的图 |
| 3 | `03-AI 自己审自己改·实录/变更前后.md` | "AI 审 AI、按意见改"的实录：工单（问题原话 + 要求）→ **改前 → 改后** → 复核判定（引用改后的句子） |

**已经做到的（数字由产物生成，见 `清单.txt` 末尾的"声明复核"）**

__CLAIMS__

**还没做好的（如实说）**

__OPEN_ITEMS__

> 说明：包内全部是本项目自产的网页与文字，**不含教材 PDF、不含扫描页图、不含任何密钥**。
"""

# 目录名里的"（N 节）"也由产物生成 —— 手写的话，一旦有节被剔除（决策 4）就对不上了
# （实测：剔掉 2 节之后目录名还写着"6 节"）。`{节数}` 就是渲染时替换的占位符。
#: 手写的那几个**样张页**（人挑出来给人看的）。它们与"整本一章一页"并存：
#: 样张页讲"这套东西长什么样"，章页是"整本书"。
#:
#: ⚠️ **每条必须声明它属于哪本书**（2026-09-27 全流程试跑实测抓出）：
#: 原来这里只有三元组、没有书，而 `all_sections()` 又**无条件**把它们并进来 ——
#: 于是给《复变函数》打包时，**线性代数的 4 个样张页会被塞进复变函数的交付包**。
#: 这正是当初引入 `config/books.json` 契约要消灭的那类硬编码
#: （`books.json` 的 `field_notes` 原文就写着「再喂一本新教材……**打包会打旧页**」）——
#: 章页那条已经改成派生了，**样张页这条一直漏着**。
SAMPLE_SECTIONS = [
    {"book": "linear-algebra-full", "dir": "delivery/第二章-全章",
     "label": "01-第二章-全章（{节数} 节）", "single": "第二章-全章（单文件）.html"},
    {"book": "linear-algebra-full", "dir": "delivery/第五章-两节",
     "label": "02-第五章-两节（更深的内容，{节数} 节）", "single": "第五章两节（单文件）.html"},
    {"book": "linear-algebra-full", "dir": "delivery/第六章-两节",
     "label": "02b-第六章-两节（特征值与特征向量，{节数} 节）",
     "single": "第六章两节（单文件）.html"},
    {"book": "linear-algebra-full", "dir": "delivery/审核回路样例-20260925",
     "label": "03-AI 自己审自己改·实录", "single": None},
]


def _book_id(book: str | None) -> str:
    """把 `--book` 解析成书目 id（解析不了就当默认那本）。"""
    try:
        sys.path.insert(0, str(ROOT / "lib"))
        import book as book_registry
        return str(getattr(book_registry.resolve(book), "book_id", "") or "")
    except Exception:                             # noqa: BLE001
        return ""


def sample_sections(book: str | None = None):
    """手写样张页清单：**只收属于当前这本书的**，且目录真的存在才收。"""
    bid = _book_id(book)
    out = []
    for row in SAMPLE_SECTIONS:
        if row["book"] != bid:
            continue                               # 别的书的样张页，绝不混进来
        if not (ROOT / row["dir"]).is_dir():
            continue                               # 目录不在就不收（同章页的道理）
        out.append((row["dir"], row["label"], row["single"]))
    return out


def chapter_sections(book: str | None = None, root: Path | None = None):
    """**按章派生**的页面清单（一章一页）：`delivery/整本/<章>`。

    为什么改成派生（2026-09-26，接手「整本并行」交接的任务 1）：
    整本跑完会产出 8 个章页（`run_book.py --out-root delivery/整本`），
    而打包清单原来是**手写的 4 行** —— 新章页不会被自动收进交付包，
    而且"加一章忘加一行"这个坑还在：历史上真漏过一页，后果是
    **出问题的节照样发给用户**（"出问题的节不发出去"这个保护只作用于清单里那几页）。

    ⚠️ 只收**已经存在**的章页（有 `render.index.json`）：
    整本还没跑的时候，目录都不存在 —— 派生出来会在打包时报"目录不存在"，
    把"还没跑"变成"打包失败"。所以这里按"存在与否"过滤，跑一章收一章。

    ⚠️ 页根**按书收窄**（2026-09-27 实测抓出）：两本书的章名会撞（都有 CH01/CH02…），
    共用 `delivery/整本/` 时，派生默认书页面会**把别的书的章页当成自己的**
    （实测：线性代数那边一口气报 60 条假违规，而且真跑起来会互相覆盖）。
    """
    base = Path(root) if root else ROOT
    try:
        sys.path.insert(0, str(ROOT / "lib"))
        import book as book_registry
        ctx = book_registry.resolve(book)
    except Exception:                             # noqa: BLE001
        return []
    chapters_path = Path(ctx.parse) / "chapters.json"
    payload = json.loads(chapters_path.read_text(encoding="utf-8")) \
        if chapters_path.is_file() else {}
    # `pages_root()` 返回的是仓库内的绝对路径；`--root` 用于测试时指向临时树，
    # 所以这里按"相对仓库的那一段"重新拼到 base 上。
    pages_rel = ctx.pages_root().relative_to(ROOT)
    out = []
    for chapter in payload.get("chapters") or []:
        cid = str(chapter.get("id") or "")
        if not cid:
            continue
        src = base / pages_rel / cid
        if not (src / "render.index.json").is_file():
            continue                              # 还没跑这一章 → 不收（跑一章收一章）
        title = str(chapter.get("title") or "")
        out.append((str(src.relative_to(base)),
                    f"10-整本-{cid}（{title}，{{节数}} 节）",
                    f"{cid}（单文件）.html"))
    return out


def all_sections(book: str | None = None):
    """打包要收的全部页面 = **按章派生的** + **属于这本书的**手写样张页。"""
    return chapter_sections(book) + sample_sections(book)


#: 保持向后兼容的名字（别处 import 的是它）。打包时用 `all_sections()` 现算，
#: 所以这里只在"没有 --book 参数"的默认情形下给一份。
SECTIONS = all_sections()

# 交付包里放了哪几个网页目录（声明复核要按**同一批输入**算 —— 少给一个目录，
# 现算出来的问题条数就和声明对不上，复核会（正确地）报"对不上"）。
#
# ⚠️ **从 SECTIONS 派生，不要再手写一份**（2026-09-26 踩过）：
#    原来这里是手写的 `[第二章, 第五章]`，后来加了「第六章-两节」到 SECTIONS，
#    却忘了加到这里 —— 后果不是"少打一个包"，而是**用户决策 4 的保护漏了一页**：
#    "无进展/冻结的节不进交付页"只作用于第二、五章，第六章出问题的节照样会发出去。
DELIVERY_DIRS = [ROOT / path for path, _title, _single in SECTIONS]
# ⚠️ 书的路径**不在这里写死**（规格 R1）：由 `lib/book.py` 从 `config/books.json` 解析。
#    `_DEFAULT_BOOK` 是"清单里的默认那本"；`main` 里用 `--book` 覆盖。
sys.path.insert(0, str(ROOT / "lib"))
import book as book_registry                                    # noqa: E402

_DEFAULT_BOOK = book_registry.resolve(None)
AUDIT_DIR = _DEFAULT_BOOK.audit
def _verify_claims(problems: dict, problems_path: Path, delivery_dirs=None, ctx=None) -> list[str]:
    """进程内做声明复核（复用 `blocks.problems.collect.verify`，并把刚算的 payload 传给它）。"""
    ctx = ctx or _DEFAULT_BOOK
    sys.path.insert(0, str(ROOT))
    try:
        from blocks.problems import collect as collector
    except Exception as exc:                      # noqa: BLE001
        return [f"复核器导入失败：{type(exc).__name__}: {exc}"]
    try:
        return collector.verify(
            problems,
            lessons_dir=ctx.lessons,
            delivery_dirs=list(delivery_dirs or DELIVERY_DIRS),
            audit_dir=AUDIT_DIR if AUDIT_DIR.is_dir() else None,
            fresh=problems)                       # ← 复用，不再重算
    except Exception as exc:                      # noqa: BLE001
        return [f"复核没跑起来：{type(exc).__name__}: {exc}"]


def _shots(sections=None) -> list[tuple[str, str]]:
    """交付包里放哪几张预览截图 —— **从 `SECTIONS` 派生**，不手写。

    为什么（2026-09-26 结构审计）：原来这里是手写的 `delivery/…/预览/xxx.png` 路径，
    而 `SECTIONS` 里的目录名带 `{节数}` 占位符、实际目录名也可能改 —— 一旦对不上，
    打包代码里那句 `if not path.is_file(): continue` 会**静默少放一张截图**，
    日志和清单都不会说什么。
    """
    shots: list[tuple[str, str]] = []
    for src, _name, _single in (sections or SECTIONS):
        base = ROOT / src
        # 两种放法都要认：「预览/xxx.png」和「预览-说明.png」（后者是早期的手工截图）。
        # ⚠️ 第一版只 glob 了前一种，结果**静默漏掉**了 `审核回路样例` 那张 ——
        #    和"手写清单漏页"是同一个坑，只是换了个方向。
        found = sorted((base / "预览").glob("*.png")) if (base / "预览").is_dir() else []
        found += sorted(base.glob("预览*.png"))
        for png in found:
            shots.append((display_path(png),
                          f"{Path(src).name}-{png.stem.replace('预览-', '')}.png"))
    return shots


SHOTS = _shots()


def render_claims(problems: dict | None) -> tuple[str, str]:
    """把 `problems.json` 里的**声明**与**未完成项**渲染成两段 markdown。

    缺口 B 的规矩：交付物里出现的每个数字都必须能从产物里算出来。所以这里**不写死任何数字** ——
    全部来自 `blocks/problems` 汇总的产物。没有产物就如实说"没跑汇总"，不许编。
    """
    if not problems:
        return ("（没有 `problems.json`：先跑 `blocks.problems.cli` 汇总，"
                "这里的数字才会由产物生成）",
                "（同上：跑一遍汇总就能看到未完成项）")
    lines = []
    for row in problems.get("claims") or []:
        lines.append("* " + str(row.get("说")))
    # 内容规范那句话由 `lib/style_rules.py` 生成（规范本身是唯一来源，不手写）
    try:
        sys.path.insert(0, str(ROOT / "lib"))
        from aiforstudy.blocks.content.content_policy import load_content_policy
        style_summary = lambda: "ContentPolicyV2 " + load_content_policy().digest
        lines.append("* " + style_summary())
    except Exception:
        pass
    open_items = problems.get("problems") or []
    dropped = problems.get("dropped") or []
    body = []
    if open_items:
        body.append(f"1. 还有 **{len(open_items)} 处硬问题**没解决（P0 会教错学生、P1 会卡住读者），"
                    f"逐条列在 `03-AI 自己审自己改·实录/` 与内部汇总里：")
        for row in open_items[:6]:
            body.append(f"   * [{row.get('severity')}] {row.get('unit') or '—'}："
                        f"{str(row.get('message'))[:100]}")
        if len(open_items) > 6:
            body.append(f"   * ……还有 {len(open_items) - 6} 处")
    if dropped:
        # ⚠️ 交付物里**只说"有几节、为什么"**，不带花费（2026-09-27 用户要求）：
        #    账目（逐节 token）仍留在 `data/problems/<书>.json` 里供内部对账，
        #    但给读者/使用者的这份说明里不出现"花了多少钱"。
        body.append(f"2. 有 **{len(dropped)} 节**反复改不好（无进展/冻结），"
                    f"**已从交付包里去掉**（不给读者半成品）；"
                    f"逐节原因与花费记在内部汇总（`data/problems/<书>.json`）：")
        for row in dropped:
            body.append(f"   * {row.get('unit')}：{str(row.get('原因'))[:70]}")
    if not body:
        body.append("1. 目前没有未解决的硬问题。")
    # 这一条不编号：它是"使用说明"，不是"没做好的事"（第一版把它算进编号里，
    # 结果列表最后一条显示成 "10." —— 序号自己都数乱了）。
    body.append("")
    body.append("另外：网页里的「可拖模型」需要浏览器支持 WebGL（普通电脑的 Chrome/Edge/Firefox "
                "都可以；不支持的会显示一句文字兜底，其余内容照常看）。")
    return "\n".join(lines), "\n".join(body)


def planned_scope(delivery_dir: Path) -> list[str]:
    """这一页**本来打算做哪几节** —— 实现收在 `lib/scopes.py`（建页那边共用同一份）。

    三个坑都踩过（2026-09-26）：① 按"页面现在的 scope − 该剔除的节"重建 → **剔除是粘的**，
    修好也回不来（实测第二章掉了 §2.3.2）；② 按章从 chapters.json 取全部单元 → 过头了
    （第五章被扩成整章 16 节、14 节是空的）；③ 只读 `范围.json` → **冻住增长**
    （新做的 §6.3.3 被打包当成"不该在页面上"又剔掉）。
    正解只有一条：**记录 ∪ 页面现状**，并把并集写回记录（只长大、不缩小）。
    """
    scope, _added = scopes.sync_plan(Path(delivery_dir))
    return scope


def reconcile_scopes(dirs) -> list[tuple[Path, list[str]]]:
    """把每个交付页的范围记录**对齐到页面现状**（0 token）。返回 (目录, 新增的节)。

    单独抽出来是为了能测：打包流程里"只在有剔除时才对齐"这个位置本身踩过坑 ——
    位置错了，`planned_scope` 写得再对也永远不被调用。
    """
    grown: list[tuple[Path, list[str]]] = []
    for directory in dirs:
        directory = Path(directory)
        plan = directory / "范围.json"
        index_path = directory / "render.index.json"
        if not (plan.is_file() or index_path.is_file()):
            continue
        before = [str(x) for x in (json.loads(plan.read_text(encoding="utf-8")).get("原本要做") or [])] \
            if plan.is_file() else []
        try:
            after = planned_scope(directory)
        except (ValueError, OSError):
            continue
        added = [unit for unit in after if unit not in before]
        if added:
            grown.append((directory, added))
    return grown


def drop_bad_sections(delivery_dir: Path, dropped: set[str], log, *,
                      parse: Path, lessons: Path) -> list[str]:
    """交付页里**只渲染"计划要做、且没被剔除"的节**（用户决策 4）。

    做法不是删产物，而是按算出来的范围**重建一次页面**（0 token，纯渲染）：
    目录、上一节/下一节、概念索引都会跟着变；而且**修好的节会自动回到页面里**。
    返回 `(这次真正被去掉的节, 重建是否成功)`。

    ⚠️ 为什么要把"成功"单独返回（2026-09-26 结构审计）：原来失败也返回 `[]`，
    和"无需剔除"**长得一模一样**，调用方 `if removed:` 什么都看不出来 ——
    于是页面可能已经被建了一半（build 是原地覆盖），打包流程照常把这一页放进包里，
    而 `清单.txt` 里一句提示都没有。现在失败必须让调用方看得见。
    """
    index_path = Path(delivery_dir) / "render.index.json"
    if not index_path.is_file():
        return [], True
    current = [str(x) for x in (json.loads(index_path.read_text(encoding="utf-8"))
                                .get("scope") or [])]
    wanted = [unit for unit in planned_scope(delivery_dir) if unit not in dropped]
    if not wanted or wanted == current:
        return [], True
    removed = [unit for unit in current if unit not in wanted]
    added = [unit for unit in wanted if unit not in current]
    log(f"  重建 {delivery_dir.name}：{len(current)} → {len(wanted)} 节"
        + (f"（去掉 {removed}）" if removed else "")
        + (f"（放回 {added}）" if added else ""))
    code = subprocess.run(
        [PYTHON, "-m", "blocks.render.cli", "build",
         "--parse", str(parse),
         "--lessons", str(lessons),
         "--out", str(delivery_dir), "--only", ",".join(wanted)],
        cwd=ROOT, capture_output=True, text=True)
    # ⚠️ **剔除一节会打断跨节引用**：实测 §2.3.1 正文里的「(2.3)」指向 §2.3.2 的原文，
    # 把 §2.3.2 剔掉之后建页自检就会报「这个编号做不成链接」（退出码 1）。
    # 这不是重建失败，是**剔除的必然结果** —— 所以这里只认「这一批问题是不是全由剔除引起」：
    # 全是引用类提醒（且页面确实是我们想要的范围）→ 当成功，并把提醒记下来；
    # 只要出现别的问题（比如某节真的建不出来）→ 仍按失败处理。
    if code.returncode != 0:
        try:
            report = json.loads(code.stdout or "{}")
        except ValueError:
            report = {}
        others = [item for item in report.get("problems") or [] if "引用完整性" not in item]
        if report.get("scope") == wanted and not others:
            for item in report.get("problems") or []:
                log(f"    （剔除的副作用，已记录）{item[:110]}")
            return removed, True
        log(f"  ✗ 重建失败（退出码 {code.returncode}）：{(code.stderr or code.stdout)[-200:]}")
        log(f"    ⚠ 这一页现在的状态**可能是半成品**（build 是原地覆盖），"
            f"不要当成「已经处理好了」")
        return removed, False
    return removed, True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="打交付包（含单文件版），并自检")
    ap.add_argument("--out", required=True, help="解压后的目录放哪")
    ap.add_argument("--zip", required=True, help="zip 路径")
    ap.add_argument("--problems", default=None,
                    help="问题汇总产物（声明与未完成项由它生成）")
    ap.add_argument("--strict-claims", action="store_true",
                    help="声明复核不过就非 0 退出（打包前把每个数字重新算一遍）")
    ap.add_argument("--no-zip", action="store_true")
    ap.add_argument("--book", help="跑哪一本书（见 config/books.json）；默认清单里那本。"
                                   "路径由 lib/book.py 解析，不在这里写死书名（规格 R1）")
    args = ap.parse_args(argv)
    # ⚠️ 先解析书契约，再定 `problems` 的**默认路径**（2026-09-27 实测抓出 B-14a）：
    #    原来 `--problems` 的默认值是**默认那本书**的问题汇总 —— 于是
    #    `--book complex-analysis` 时它会去**读并重写线性代数**的 problems.json，
    #    拿去校验的声明也是别本书的（数字当然对不上）。
    #    规格 R1 的意思就是"书的四处路径都从契约来"，problems 也不能例外。
    ctx = book_registry.resolve(args.book)      # 书的四条路径（--book 覆盖默认那本）
    problems_path = Path(args.problems) if args.problems else ctx.problems
    if not problems_path.is_absolute():
        problems_path = ROOT / problems_path
    problems = None
    if problems_path.is_file():
        problems = json.loads(problems_path.read_text(encoding="utf-8"))
    # 现算"这次要收哪几页"：**派生的章页（存在才收）+ 手写的样张页**（任务 1）。
    sections = all_sections(args.book)
    delivery_dirs = [ROOT / src for src, _t, _s in sections]
    out = Path(args.out).resolve()
    # ⚠️ 守卫（2026-09-26 结构审计）：下一行是 `rmtree(out)`，而 `--out` 是命令行参数 ——
    #    指到交付目录或项目根上就会**先把源目录删掉再拷自己**。一条命令的事，必须挡。
    forbidden = [d.resolve() for d in delivery_dirs] + [ROOT.resolve()]
    if out in forbidden:
        print(f"  ✗ --out 指向了受保护的目录（{out}）→ 它会先删掉这个目录再打包，"
              f"拒绝执行。请换一个输出目录。")
        return 1
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    # ── 决策 4：无进展/冻结的节**不进交付包** ───────────────────────────────
    # 它们的产物留在磁盘上（内部包要查原因和 token），但给读者看的页面里没有它们。
    dropped_units = {str(row.get("unit")) for row in (problems or {}).get("dropped") or []}
    removed_notes = []
    rebuild_failed = False
    # 范围记录**每次打包都对齐一次**（0 token、纯读产物）。
    # 为什么不能只在"这次有剔除"时才跑（踩过）：那样"新做的节已经建进页面、
    # 但记录里还没有"永远发现不了 —— 记录停在 4 节，打包时新节会被当成
    # "不该在页面上"又剔掉（§6.3.3 实测）。对齐之后记录只长大、不缩小。
    for directory, added in reconcile_scopes(delivery_dirs):
        print(f"  范围记录已对齐 {directory.name}：新增 {added}")
    if dropped_units:
        for directory in delivery_dirs:
            removed, ok = drop_bad_sections(directory, dropped_units, print,
                                            parse=ctx.parse, lessons=ctx.lessons)
            if removed:
                removed_notes.append(f"# 已从 {directory.name} 去掉：{removed}")
            if not ok:
                removed_notes.append(f"# ⚠ 重建失败：{directory.name}（这一页可能是半成品）")
                rebuild_failed = True

    # **每次都重新汇总** problems.json（0 token、只读产物）：
    # 声明必须反映"包里真实的样子"。第一版只在"这次掉了几节"时才刷新，
    # 结果上一轮掉的节留在旧声明里 → 复核报"网页指标对不上"（自己踩的，记在这里）。
    if problems_path.is_file():
        refresh = subprocess.run(
            [PYTHON, "-m", "blocks.problems.cli",
             "--lessons", str(ctx.lessons),
             # ⚠️ `--hide-cost`（2026-09-27 用户要求）：交付物里**不出现耗时/花费**。
             #    这条声明以前会跟着这次重汇总**又长回来**（我手跑 `--hide-cost` 被它覆盖过），
             #    所以必须在**打包侧**也钉住。逐节/逐章的 token 仍留在
             #    `problems.json#metrics` 里供内部对账。
             "--hide-cost",
             "--out", str(problems_path)]
            + sum([["--delivery", str(d)] for d in delivery_dirs], [])
            + (["--audit", str(AUDIT_DIR)] if AUDIT_DIR.is_dir() else []),
            cwd=ROOT, capture_output=True, text=True)
        print("  重新汇总声明：" + (refresh.stdout.strip() or refresh.stderr.strip())[-160:])
        # ⚠️ 汇总失败**不能继续拿旧文件**（2026-09-26 结构审计）：原来不检查返回码，
        #    于是汇总崩了会读到**上一轮的 problems.json**，一页纸上的数字静默过期，
        #    而包里看起来一切正常。宁可停下。
        if refresh.returncode != 0:
            print(f"  ✗ 重新汇总声明失败（退出码 {refresh.returncode}）→ 停止打包，"
                  f"不用旧的 problems.json 冒充")
            return 1
        problems = json.loads(problems_path.read_text(encoding="utf-8"))

    for src, name, single in sections:
        src_path = ROOT / src
        # 目录名里的节数从**产物**里读（`render.index.json` 的 scope）
        units = []
        index_path = src_path / "render.index.json"
        if index_path.is_file():
            try:
                units = json.loads(index_path.read_text(encoding="utf-8")).get("scope") or []
            except ValueError:
                units = []
        name = name.replace("{节数}", str(len(units))) if units else \
            name.replace("（{节数} 节）", "").replace("，{节数} 节", "")
        shutil.copytree(src_path, out / name)
        if single:
            target = out / "05-单文件版"
            target.mkdir(exist_ok=True)
            code = subprocess.run([PYTHON, str(HERE / "make_single_file.py"),
                                   str(src_path), str(target / single)],
                                  capture_output=True, text=True)
            print(code.stdout.strip() or code.stderr.strip())
            if code.returncode != 0:
                print(f"✗ 单文件生成失败：{src}", file=sys.stderr)
                return 1
            # ⚠️ 把**刚生成的那一份**也拷回交付页目录（2026-09-26 链路审计 A15）：
            #    交付页目录里原来躺着一份手工拷过去的单文件版，它比 `data/data.js` 旧了 3 小时 ——
            #    读者双击它看到的是**过期内容**，而清单.txt 和自检都不说（它们只查 index.html）。
            #    现在每次打包都用刚生成的那份覆盖它，"过期"在结构上不可能发生。
            page_copy = src_path / Path(single).name
            if page_copy.parent == src_path:
                shutil.copy2(target / single, page_copy)

    # ── 截图去重（P-025-6）──────────────────────────────────────────────────
    # 同一张截图常常既在各章的 `预览/` 里、又被收进 `04-截图/`，包里就是两份一样的图。
    # 这里按**内容**判重：已经在包里出现过的就不再拷一份，改为在清单里说明它在哪。
    # （`assets/` 那几份重复**不能去重**：每个网页目录都要能单独双击打开，见文件头。）
    (out / "04-截图").mkdir()
    # ⚠️ 包内每个文件的 sha256 **只算一遍**（A17）：原来 `known` 算一次、清单 `rows` 又算一次，
    #    同一批文件读了两遍。这里先建一张 `路径 → 摘要` 表，两处共用。
    digests: dict[Path, str] = {}

    def sha_of(path: Path) -> str:
        got = digests.get(path)
        if got is None:
            got = hashlib.sha256(path.read_bytes()).hexdigest()
            digests[path] = got
        return got

    known = {}
    for existing in sorted(out.rglob("*")):
        if existing.is_file() and "04-截图" not in existing.parts:
            known.setdefault(sha_of(existing), str(existing.relative_to(out)))
    shot_notes = []
    for src, name in _shots(sections):
        path = ROOT / src
        if not path.is_file():
            continue
        digest = sha_of(path)
        if digest in known:
            shot_notes.append(f"# 截图 {name} 与 {known[digest]} 内容相同 → 包内不重复放")
            continue
        target = out / "04-截图" / name
        shutil.copy2(path, target)
        known[digest] = str(target.relative_to(out))

    claims_text, open_text = render_claims(problems)
    section_dirs = [str(Path(src).name) for src, _n, _s in sections]
    single_count = sum(1 for _s, _n, single in sections if single)
    one_pager = (ONE_PAGER.replace("__CLAIMS__", claims_text)
                         .replace("__OPEN_ITEMS__", open_text)
                         .replace("__SINGLE_COUNT__", "两" if single_count == 2 else str(single_count))
                         .replace("__DIRS__", "、".join(f"`{d}`" for d in section_dirs[:2]))
                         .replace("__SEC0__", section_dirs[0] if section_dirs else "")
                         .replace("__SEC1__", section_dirs[1] if len(section_dirs) > 1 else ""))
    (out / "00-先看这个.md").write_text(one_pager, encoding="utf-8")
    code = subprocess.run([PYTHON, str(HERE / "make_one_pager.py"), str(out)], capture_output=True)
    if code.returncode != 0:
        print(f"✗ 一页纸 HTML 生成失败：{code.stderr.decode()[-300:]}", file=sys.stderr)
        return 1

    rows = [f"{sha_of(p)[:16]}  {p.stat().st_size:>9d}  "
            f"{p.relative_to(out)}" for p in sorted(out.rglob('*')) if p.is_file()]

    # ── 声明复核（缺口 B 的机器判据）────────────────────────────────────────
    # 一页纸里的每个数字都来自 `problems.json`，这里**照着产物再算一遍**：
    # 对不上就说明"包里的数字过期了"，打包的人当场知道（--strict-claims 时直接失败）。
    claims_report = ["", "# ── 声明复核（一页纸里的数字 vs 产物）",
                     f"# 汇总产物：{problems_path}"]
    claims_report += removed_notes or ["# 本次没有「无进展」的节需要剔除"]
    claims_report += shot_notes
    if problems is None:
        claims_report.append("# ⚠ 没有 problems.json：一页纸里的数字这一段是空的")
    else:
        claims_report.append("# 声明（每条都由产物生成）：")
        for row in problems.get("claims") or []:
            claims_report.append("#   * " + str(row.get("说")))
            for evidence in row.get("evidence") or []:
                claims_report.append("#       证据：" + str(evidence))
        claims_report.append(f"# 硬问题 {len(problems.get('problems') or [])} 条、"
                             f"不进交付包的节 {len(problems.get('dropped') or [])} 个")
        # ⚠️ 复核**在进程内**做，而且把刚算出来的 `problems` 直接当 `fresh` 传进去
        #    （A16）：原来是起子进程 `blocks.problems.cli --check`，它拿不到内存里那份，
        #    于是**整批重算一遍**（每节 3 次 `fresh_checks`）。现在零重复。
        mismatches = _verify_claims(problems, problems_path, delivery_dirs, ctx)
        claims_report.append("# 复核：" + ("通过（每个数字都现算过）" if not mismatches
                                          else "对不上：" + "；".join(mismatches[:6])))
        if mismatches:
            print("✗ 声明复核没过：包里的数字和产物对不上", file=sys.stderr)
            if args.strict_claims:
                return 1
    (out / "清单.txt").write_text(
        "# AIforStudy 交付包清单（给负责人看）\n"
        f"# 生成于 {datetime.datetime.now():%Y-%m-%d %H:%M}\n# 文件数 {len(rows)}\n"
        "# 列：sha256(前16位)  大小(字节)  路径\n" + "\n".join(rows) + "\n"
        + "\n".join(claims_report) + "\n", encoding="utf-8")
    print(f"目录已生成：{out}（{len(rows)} 个文件）")

    if not args.no_zip:
        zip_path = Path(args.zip).resolve()
        if zip_path.exists():
            zip_path.unlink()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for p in sorted(out.rglob("*")):
                if p.is_file():
                    z.write(p, p.relative_to(out.parent))
        print(f"包已生成：{zip_path}（{zip_path.stat().st_size/1024/1024:.1f} MB）")
    if rebuild_failed:
        # 页面重建失败 → 包可能是"半成品"，不能报成功（原来失败也返回 0）
        print("  ✗ 有交付页重建失败 → 非 0 退出（这一页可能是半成品，不要当成功）")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
