#!/usr/bin/env python3
"""M0/M9 退役清单状态：旧入口还在被谁引用？替代能力到位了吗？

方案 §13 要求每个退役项记录：旧路径、替代路径、原消费者、迁移结果、测试、文档更新、
是否影响产物；§15 的 A32 要求"生产旧入口引用为 0"。本工具把这两件事变成可复跑的数字。

它**只读**：扫文件、数引用，不改任何东西。输出分三列数字：

* `prod`  生产代码里的引用（`blocks/`、`lib/`、`src/`）——M9/M10 要清零的就是这一列；
* `tool`  维护/编排脚本里的引用（`tools/`，含待退役脚本互相引用）；
* `doc`   文档/测试里的引用——**不要求清零**，但改名时要一起更新。

用法：
    python tools/migrations/retire_status.py
    python tools/migrations/retire_status.py --json
    python tools/migrations/retire_status.py --check-zero-prod   # 生产引用非 0 即退出码 1
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent.parent

MANIFEST = HERE / "retire_manifest.json"

SCAN_EXTS = (".py", ".js", ".mjs", ".ts", ".json", ".sh", ".md", ".html", ".toml", ".cfg")
SKIP_DIRS = {"__pycache__", ".cache", ".pytest_cache", "data", "delivery", "node_modules",
             ".venv", ".venv-linux", "figures", "vendor"}
SKIP_SUFFIX_DIRS = ("_archive",)
# 这些文件**定义**了旧路径，不算"被引用"
SELF_DOCS = {"retire_manifest.json"}


def _is_doc(path: Path) -> bool:
    rel = path.relative_to(PKG_ROOT).as_posix()
    return rel.startswith("tests/") or rel.startswith("docs/") or rel.endswith(".md")


def _is_prod(path: Path) -> bool:
    rel = path.relative_to(PKG_ROOT).as_posix()
    return rel.startswith("blocks/") or rel.startswith("lib/") or rel.startswith("src/")


def _is_tool(path: Path) -> bool:
    rel = path.relative_to(PKG_ROOT).as_posix()
    return rel.startswith("tools/") and not rel.startswith("tools/migrations/")


def iter_scannable():
    for path in PKG_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in SCAN_EXTS:
            continue
        parts = set(path.relative_to(PKG_ROOT).parts)
        if parts & SKIP_DIRS:
            continue
        if any(s in str(path) for s in SKIP_SUFFIX_DIRS):
            continue
        if path.name in SELF_DOCS and path.parent == HERE:
            continue
        yield path


_OWN_STEMS: set[str] | None = None


def own_module_stems() -> set[str]:
    """`src/aiforstudy` 里所有 `.py` 的 stem。

    用来识别**同名不同物**：如果新包自己就有一个 `review.py`，那么
    `from .review import ...` 说的是**新包自己的** review，不是旧的
    `blocks/parse/structure/review.py`。实测抓到两处这类假阳性
    （`blocks/parse/catalog.py` 与 `pipeline/transitional.py`）。
    """
    global _OWN_STEMS
    if _OWN_STEMS is None:
        root = PKG_ROOT / "src" / "aiforstudy"
        _OWN_STEMS = {q.stem for q in root.rglob("*.py")} if root.is_dir() else set()
    return _OWN_STEMS


def _tokens(item: dict) -> tuple[list[str], list[str], list[str], list[str]]:
    """→ (路径关键词, 标识符关键词)。

    为什么要分两类：目录名当普通单词搜会满屏假阳性——`library`、`assemble`、`model`
    这些词在 minified vendor JS 和普通英文句子里到处都是。所以：

    * **路径关键词**：`blocks/clean`、`tools/doctor.py` 这种带斜杠的精确串，直接子串匹配；
    * **标识符关键词**：**只从文件**取 stem（`tools/doctor.py` → `doctor`），只认导入语句或
      `stem.py`/`stem.js` 的写法。

    ⚠️ **目录不取标识符**（2026-10-05 修正）。第一版把目录名也当标识符，于是
    `blocks/model/` 的 token 是 `model` —— 而 `lib/model.py`（模型客户端）在 15 个文件里被
    `import model` 引用，全被算成"`blocks/model` 的生产引用"。**同名不同物**，数字虚高 15。
    目录只用路径串匹配，再补一个点号形式（`blocks.model`）。
    """
    paths: list[str] = []
    idents: list[str] = []
    raw = [item["old_path"], *item.get("extra_paths", [])]
    for r in raw:
        r = r.split("（")[0].strip().rstrip("/")
        if not r or r.startswith("explanation_markdown"):
            continue
        if "/" in r:
            paths.append(r)
            if r.endswith((".py", ".js")):
                stem = Path(r).stem
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", stem):
                    idents.append(stem)
            else:
                paths.append(r.replace("/", "."))       # blocks/model → blocks.model
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", r):
            idents.append(r)
    fields: list[str] = []
    if item["id"] == "T-24":
        # ⚠️ **字段名**与模块名要分开对待：`explanation_markdown` 的引用形式**就是**
        #    字符串字面量（`lesson.get("explanation_markdown")`），所以它必须走
        #    "匹配裸字符串"的那条规则；而模块名（`review`、`quality_report`）恰恰相反。
        #    第一版把它们合成一类，于是去掉裸字符串匹配之后 T-24 的引用**全部消失** ——
        #    那不是"清零了"，是**看不见了**。
        fields.append("explanation_markdown")
    keep: list[str] = []
    shadowed: list[str] = []
    own = own_module_stems()
    # ⚠️ 例外：如果这一项**本身就住在 `src/aiforstudy` 里**（过渡壳就是这样），
    #    那么"新包引用它"是**设计如此**（T-26：切换阶段才删），必须照实计。
    #    第一版没这个例外，于是 `from .transitional import ...` 被当成"同名不同物"抹掉，
    #    T-26 从"计划内 1 处"变成"0 处" —— 那不是更干净，是**更瞎**。
    item_is_internal = str(item.get("old_path") or "").startswith("src/aiforstudy/")
    for i in set(idents):
        if len(i) < 5:
            continue                       # 太短的词做依赖信号必然误报
        if i in own and not item_is_internal:
            # ⚠️ 新包自己有同名模块 → 这个引用多半指新包自己。**不当依赖，但必须列出来**
            #    （静默过滤会让"漏掉一个真引用"永远看不见，和填零一样危险）。
            shadowed.append(i)
            continue
        keep.append(i)
    return sorted(set(paths)), sorted(keep), fields, sorted(shadowed)


def _field_re(tok: str) -> re.Pattern:
    """字段名：引用形式就是字符串字面量（也可能是 dict 键写法）。"""
    t = re.escape(tok)
    return re.compile(rf"(?:[\"']{t}[\"'])")


def _ident_re(tok: str) -> re.Pattern:
    """模块标识符的**引用写法**：导入语句 / `tok.py` / `tok.js`。"""
    t = re.escape(tok)
    return re.compile(
        rf"(?:\bfrom\s+(?:[\w.]+\.)?{t}\b)"          # from blocks.clean import / from .clean import
        # ⚠️ **包内相对导入**：`from .transitional import release_registry`。
        #    上面那条要求 `from` 后是 `名字.` 或直接 `名字`，点开头的 `.transitional`
        #    两边都不匹配 —— 于是**包内自己引用旧入口时这一列是假绿**。
        #    实测抓到：`engine.py` 的 `from .transitional import release_registry`
        #    一直被漏掉，而"生产旧入口引用为 0"这条验收正是靠这一列。
        rf"|(?:\bfrom\s+\.+{t}\b)"
        rf"|(?:\bimport\s+{t}\b)"                     # import clean
        rf"|(?:\bfrom\s+[\w.]+\s+import\s+[^#\n]*\b{t}\b)"   # from pkg import a, clean
        rf"|(?:\b{t}\.py\b)|(?:\b{t}\.js\b)"          # 文件名写法
        # ⚠️ **不**再匹配裸字符串 `"TOK"`。实测抓到两处假阳性：
        #    `checks/quality.py` 里的 `"quality_report"`（那是 file_type 的**值**）
        #    与 `blocks/parse/catalog.py` 里的 `"review": {...}`（那是一个 **dict 键**）。
        #    把"数据里出现过这个名字"算成"生产代码依赖旧入口"，会让这一列永远清不了零，
        #    也会把真正的引用淹掉。
    )


def _line_kinds(text: str) -> list[str]:
    """给每一行判 `code` / `comment` / `doc`。

    ⚠️ **必须与 `_scan.py` 共用同一份实现**。这里原先有一份**复制粘贴的副本**，
    于是我在 `_scan.py` 里修好的两个缺陷（文档字符串**起始行**被当成代码；
    把裸字符串当引用）在这一份里**照旧存在** —— 修了一处，另一处还在用旧口径。
    这就是"同一判据有两份实现"的代价：它**不会报错**，只会给出两个不同的数字，
    而"生产旧入口引用为 0"这条验收正是靠这个数字。现在只留一份。
    """
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from _scan import line_kinds
    return line_kinds(text)


def _hit_lines(text: str, paths: list[str], ident_res: list[re.Pattern],
               limit: int = 3) -> list[tuple[int, str, str]]:
    """命中处 → [(行号, 原文行, 类别)]——数量可能有假阳性，必须能一眼看回去。"""
    kinds = _line_kinds(text)
    out: list[tuple[int, str, str]] = []
    for i, line in enumerate(text.splitlines(), 1):
        hit = any(p in line for p in paths) if paths else False
        if not hit:
            hit = any(rx.search(line) for rx in ident_res)
        if hit:
            out.append((i, line.strip()[:160], kinds[i - 1] if i - 1 < len(kinds) else "code"))
            if len(out) >= limit:
                break
    return out


def scan(item: dict) -> dict:
    paths, idents, fields, shadowed = _tokens(item)
    ident_res = [_ident_re(t) for t in idents] + [_field_re(t) for t in fields]
    prod: list[str] = []
    prod_code: list[str] = []
    tool: list[str] = []
    doc: list[str] = []
    samples: dict[str, list[str]] = {}
    own = [item["old_path"], *item.get("extra_paths", [])]
    for path in iter_scannable():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        lines = _hit_lines(text, paths, ident_res)
        if not lines:
            continue
        rel = path.relative_to(PKG_ROOT).as_posix()
        # 旧路径自己的文件不算被引用
        if any(rel == o.rstrip("/") or rel.startswith(o.rstrip("/") + "/") for o in own):
            continue
        samples[rel] = [f"[{kind}] {ln}" for _no, ln, kind in lines]
        if _is_prod(path):
            prod.append(rel)
            if any(kind == "code" for _no, _ln, kind in lines):
                prod_code.append(rel)
        elif _is_tool(path):
            tool.append(rel)
        elif _is_doc(path):
            doc.append(rel)
    exists = (PKG_ROOT / item["old_path"].split("（")[0].strip().rstrip("/")).exists()
    return {"id": item["id"], "old_path": item["old_path"],
            "disposition": item["disposition"], "replacement": item["replacement"],
            "gate": item["gate"], "status": item["status"],
            "exists_now": exists,
            "tokens": {"paths": paths, "idents": idents, "fields": fields,
                       "idents_shadowed_by_new_package": shadowed},
            "refs": {"prod": sorted(prod), "prod_code": sorted(prod_code),
                     "tool": sorted(tool), "doc": sorted(doc)},
            "samples": samples,
            "counts": {"prod": len(prod), "prod_code": len(prod_code),
                       "tool": len(tool), "doc": len(doc)}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="退役清单状态（只读）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check-zero-prod", action="store_true",
                    help="生产代码里还有旧入口引用就退出码 1（M9/M10 的门）")
    ap.add_argument("--only", help="只查这几个 id（逗号分隔）")
    ap.add_argument("--show-lines", action="store_true",
                    help="把命中处的原文行打出来（数量可能有假阳性，必须能一眼看回去）")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    wanted = {x.strip() for x in (args.only or "").split(",") if x.strip()}
    rows = [scan(it) for it in manifest["items"] if not wanted or it["id"] in wanted]

    payload = {"_meta": {"file_type": "retire_status", "schema_version": "1.0",
                         "generator": "aiforstudy.tools.migrations.retire_status",
                         "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                         "source": str(MANIFEST)},
               "rows": rows,
               "totals": {
                   "items": len(rows),
                   "prod_refs": sum(r["counts"]["prod"] for r in rows),
                   "prod_code_refs": sum(r["counts"]["prod_code"] for r in rows),
                   "tool_refs": sum(r["counts"]["tool"] for r in rows),
                   "doc_refs": sum(r["counts"]["doc"] for r in rows),
                   "items_still_present": sum(1 for r in rows if r["exists_now"]),
               }}
    if args.out:
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"{'ID':<6}{'旧路径':<44}{'在':<3}{'生产':>5}{'其中代码':>8}{'工具':>5}{'文档':>5}  状态")
        for r in rows:
            c = r["counts"]
            print(f"{r['id']:<6}{r['old_path'][:42]:<44}{'✓' if r['exists_now'] else '×':<3}"
                  f"{c['prod']:>5}{c['prod_code']:>8}{c['tool']:>5}{c['doc']:>5}  {r['status']}")
        t = payload["totals"]
        print(f"\n合计：{t['items']} 项｜仍在盘上 {t['items_still_present']}｜"
              f"生产引用 {t['prod_refs']}（其中**真代码** {t['prod_code_refs']}）｜"
              f"工具引用 {t['tool_refs']}｜文档引用 {t['doc_refs']}")
        hot = [r for r in rows if r["counts"]["prod_code"]]
        print(f"\n仍有**生产代码**引用的项（M9/M10 要清零的是这一列）：共 {len(hot)} 项")
        for r in hot:
            print(f"  {r['id']} {r['old_path']} → {r['counts']['prod_code']} 个文件")
            for rel in r["refs"]["prod_code"]:
                if args.show_lines:
                    for ln in r["samples"].get(rel, []):
                        if ln.startswith("[code]"):
                            print(f"        {rel}  {ln[7:]}")
                else:
                    print(f"        {rel}")
        prose = [r for r in rows if r["counts"]["prod"] > r["counts"]["prod_code"]]
        if prose:
            print(f"\n只在注释/文档字符串里被提到的项（{len(prose)} 项）——"
                  f"这些是**解释**不是依赖，改名时一起更新即可，不阻塞退役：")
            for r in prose:
                print(f"  {r['id']} {r['old_path']}")

    if args.check_zero_prod and payload["totals"]["prod_code_refs"]:
        print(f"\n✗ 生产**代码**里还有 {payload['totals']['prod_code_refs']} 处旧入口引用")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
