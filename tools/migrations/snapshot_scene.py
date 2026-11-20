#!/usr/bin/env python3
"""M0 现场快照：把"当前工作区到底有什么"变成一份**可核对的内容哈希清单**。

为什么需要它（方案 §16 / M0 通过条件）：

    M0 保存当前工作区所有修改、未跟踪文件及需保留的忽略数据清单和内容哈希；
    不能用 git clean/reset 获得"干净基线"。

现实是：这个工作区有几十个未提交修改和几十个未跟踪文件，`git HEAD` **不能**代表
当前完整实现；还有 `data/`（1.2G）这类被 `.gitignore` 排除、但**必须保留**的产物
（原文、MinerU 结果、概念/教案/审核、图、用量、人工记录）。任何重构动到它们之前，
先得有一份"动之前长什么样"的凭据，否则"是否丢东西"根本无法判定。

本工具**只读**：不改文件、不调用模型、不写产物（只写自己那份清单）。
它做三件事：

1. 走 `git status --porcelain -z --untracked-files=all` 拿到**修改 / 未跟踪**的逐文件清单；
2. 走 `git status --ignored` 拿到**被忽略但需保留**的文件（按下面的 KEEP 策略筛，缓存类不计）；
3. 对每个文件记 `size / mtime_ns / sha256`，写成 JSON；可选再打一份 tar.gz 备份。

用法：
    python tools/migrations/snapshot_scene.py                      # 写清单
    python tools/migrations/snapshot_scene.py --backup             # 顺带打包备份
    python tools/migrations/snapshot_scene.py --no-hash            # 只记路径与大小（快）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent.parent                     # AIforStudy/
DEFAULT_WORKSPACE = PKG_ROOT.parent               # 仓库根（AIforStudy 的上一级）

# 被忽略、但**属于要保留的证据/材料**的前缀（相对仓库根）。
# 依据：方案 §16「备份既包括代码差异，也包括原文、MinerU、概念/教案/审核、图、用量和人工记录」。
KEEP_IGNORED_PREFIXES = (
    "AIforStudy/data/",
    "AIforStudy/delivery/",
    "AIforStudy/figures/",
    "AIforStudy/.secrets/",
    "教材/",
    "归档/",
    "AIforStudy/.devtools/",
)

# 纯缓存/可重建物：记路径但不哈希内容，也不算"必须保留"。
CACHE_MARKERS = (
    "__pycache__/", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/",
    ".venv", "node_modules/", ".cache/", ".DS_Store",
)

SECRET_PREFIXES = ("AIforStudy/.secrets/", ".env")


def _is_cache(path: str) -> bool:
    return any(m in path for m in CACHE_MARKERS)


def _is_secret(path: str) -> bool:
    return path.startswith(SECRET_PREFIXES) or path.endswith(".env")


def _is_keep_ignored(path: str) -> bool:
    if _is_cache(path):
        return False
    return any(path.startswith(p) for p in KEEP_IGNORED_PREFIXES)


def _run_git(workspace: Path, *args: str) -> bytes:
    proc = subprocess.run(["git", *args], cwd=str(workspace),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{proc.stderr.decode('utf-8', 'replace')[:400]}")
    return proc.stdout


def _parse_porcelain_z(raw: bytes) -> list[tuple[str, str]]:
    """`git status --porcelain -z` → [(状态, 路径)]；重命名取新路径。"""
    out: list[tuple[str, str]] = []
    items = raw.split(b"\0")
    i = 0
    while i < len(items):
        item = items[i]
        i += 1
        if not item:
            continue
        text = item.decode("utf-8", "surrogateescape")
        status, path = text[:2], text[3:]
        if status[0] in ("R", "C"):          # 重命名/复制：下一条是原路径，丢弃
            i += 1
        out.append((status, path))
    return out


def _git_head(workspace: Path) -> dict:
    try:
        head = _run_git(workspace, "rev-parse", "HEAD").decode().strip()
        branch = _run_git(workspace, "rev-parse", "--abbrev-ref", "HEAD").decode().strip()
    except RuntimeError:
        head, branch = "", ""
    return {"head": head, "branch": branch}


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _stat_entry(abs_path: Path, rel: str, category: str, do_hash: bool,
                workspace: Path) -> dict:
    entry: dict = {"path": rel, "category": category}
    try:
        st = abs_path.stat()
    except OSError as exc:
        entry["error"] = f"{type(exc).__name__}: {exc}"
        return entry
    entry["size"] = st.st_size
    entry["mtime_ns"] = st.st_mtime_ns
    if abs_path.is_symlink():
        entry["symlink_to"] = os.readlink(abs_path)
    if _is_secret(rel):
        entry["secret"] = True                 # 密钥只记存在与大小，不哈希内容
        entry["sha256"] = None
        return entry
    if do_hash and abs_path.is_file():
        try:
            entry["sha256"] = _sha256(abs_path)
        except OSError as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
            entry["sha256"] = None
    return entry


def collect(workspace: Path, do_hash: bool = True,
            include_ignored: bool = True, include_tracked: bool = True) -> dict:
    workspace = workspace.resolve()
    head = _git_head(workspace)

    status = _parse_porcelain_z(
        _run_git(workspace, "status", "--porcelain", "-z", "--untracked-files=all"))
    modified: list[str] = []
    untracked: list[str] = []
    deleted: list[str] = []
    for st, path in status:
        if _is_cache(path):
            continue
        if "?" in st:
            untracked.append(path)
        elif "D" in st:
            deleted.append(path)
        else:
            modified.append(path)

    entries: list[dict] = []
    for rel in sorted(set(modified)):
        entries.append(_stat_entry(workspace / rel, rel, "modified", do_hash, workspace))
    for rel in sorted(set(untracked)):
        abs_path = workspace / rel
        if abs_path.is_dir():                 # --untracked-files=all 一般给文件；防御一下
            for sub in sorted(p for p in abs_path.rglob("*") if p.is_file()):
                if _is_cache(str(sub.relative_to(workspace))):
                    continue
                srel = str(sub.relative_to(workspace))
                entries.append(_stat_entry(sub, srel, "untracked", do_hash, workspace))
        else:
            entries.append(_stat_entry(abs_path, rel, "untracked", do_hash, workspace))
    for rel in sorted(set(deleted)):
        entries.append({"path": rel, "category": "deleted", "sha256": None})

    # ── 已跟踪且未修改的文件
    #    ⚠️ 为什么必须收这一栏（2026-10-05 实测抓到）：只记"修改/未跟踪/被忽略"三类时，
    #    **已跟踪且干净**的文件根本不在清单里。于是事后做"产物有没有被动过"的比对时，
    #    它们会全部显示成"新增"——本次一次比对就冒出 24 个假新增（其实 mtime 还在 9 月 25 日）。
    #    假新增多了，真新增就没人信了；所以清单要覆盖**整棵树**，不只覆盖"有变化的那些"。
    tracked_clean: list[dict] = []
    if include_tracked:
        try:
            tracked_raw = _run_git(workspace, "ls-files", "-z")
            dirty = set(modified) | set(untracked)
            for rel in sorted(x.decode("utf-8", "surrogateescape")
                              for x in tracked_raw.split(b"\0") if x):
                if rel in dirty or _is_cache(rel):
                    continue
                abs_path = workspace / rel
                if not abs_path.is_file():
                    continue
                tracked_clean.append(_stat_entry(abs_path, rel, "tracked-clean",
                                                 do_hash, workspace))
        except RuntimeError as exc:
            tracked_clean.append({"path": "<git ls-files 失败>", "category": "error",
                                  "error": str(exc)})

    kept_ignored: list[dict] = []
    if include_ignored:
        try:
            ignored_raw = _run_git(workspace, "status", "--porcelain", "-z",
                                   "--ignored", "--untracked-files=all")
            seen = set()
            for st, path in _parse_porcelain_z(ignored_raw):
                if "!" not in st:      # 只会拿到 "!!"（被忽略）；其余交给上面的分支
                    continue
                if not _is_keep_ignored(path) or path in seen:
                    continue
                seen.add(path)
                abs_path = workspace / path
                if abs_path.is_dir():
                    for sub in sorted(p for p in abs_path.rglob("*") if p.is_file()):
                        srel = str(sub.relative_to(workspace))
                        if not _is_keep_ignored(srel) or srel in seen:
                            continue
                        seen.add(srel)
                        kept_ignored.append(_stat_entry(sub, srel, "ignored-keep", do_hash, workspace))
                else:
                    kept_ignored.append(_stat_entry(abs_path, path, "ignored-keep", do_hash, workspace))
        except RuntimeError as exc:
            kept_ignored.append({"path": "<git status --ignored 失败>", "category": "error",
                                 "error": str(exc)})

    payload = {
        "_meta": {
            "file_type": "scene_snapshot",
            "schema_version": "1.0",
            "generator": "aiforstudy.tools.migrations.snapshot_scene",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "workspace": str(workspace),
            "hashed": do_hash,
            "field_notes": {
                "category": "modified=已跟踪但被改；untracked=没进版本库；deleted=被删的已跟踪文件；ignored-keep=被 .gitignore 排除但属于必须保留的材料；tracked-clean=已跟踪且未修改（**必须收**，否则事后比对会把它们全算成'新增'）",
                "sha256": "内容指纹；secret=true 的条目不哈希内容（只记存在与大小）",
                "为什么要有这份清单": "M0 要求：改动前后能证明'没丢东西'。git HEAD 不代表当前实现（有几十处未提交修改），所以必须有一份按内容哈希的现场凭据",
            },
        },
        "git": head,
        "summary": {
            "modified": len(modified),
            "untracked": len([e for e in entries if e["category"] == "untracked"]),
            "deleted": len(deleted),
            "ignored_keep": len(kept_ignored),
            "tracked_clean": len(tracked_clean),
            "total_files": len(entries) + len(kept_ignored) + len(tracked_clean),
            "total_bytes": sum(e.get("size") or 0
                               for e in entries + kept_ignored + tracked_clean),
        },
        "modified_or_untracked": entries,
        "ignored_but_keep": kept_ignored,
        "tracked_clean": tracked_clean,
    }
    return payload


def write_backup(workspace: Path, payload: dict, out_dir: Path,
                 manifest_path: Path) -> Path:
    """把"修改 + 未跟踪"的文件打包（不含 1.2G 的 data/，那些由 ignored 清单+归档覆盖）。

    ⚠️ 清单路径必须复用同一份（不要把同一个 stamp 生成两遍 → 会多出一份重复清单）。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = manifest_path.stem.replace("scene-snapshot-", "")
    tar_path = out_dir / f"workspace-code-backup-{stamp}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        for entry in payload["modified_or_untracked"]:
            rel = entry["path"]
            abs_path = workspace / rel
            if entry["category"] == "deleted" or not abs_path.exists() or not abs_path.is_file():
                continue
            tar.add(abs_path, arcname=rel)
        tar.add(manifest_path, arcname=f"_migration/{manifest_path.name}")
    return tar_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M0 现场快照（只读）")
    ap.add_argument("--workspace", default=str(DEFAULT_WORKSPACE))
    ap.add_argument("--out", default=None, help="清单输出路径（默认 data/_migration/scene-snapshot-<时间>.json）")
    ap.add_argument("--backup", action="store_true", help="同时打一份 tar.gz 代码/文件备份")
    ap.add_argument("--backup-dir", default=None)
    ap.add_argument("--no-hash", action="store_true", help="只记路径/大小/mtime（快，但不能证明内容一致）")
    ap.add_argument("--no-ignored", action="store_true", help="不统计被忽略但需保留的文件")
    ap.add_argument("--no-tracked", action="store_true",
                    help="不统计已跟踪且干净的文件（清单会变小，但事后比对会出现假新增）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    workspace = Path(args.workspace)
    payload = collect(workspace, do_hash=not args.no_hash,
                      include_ignored=not args.no_ignored,
                      include_tracked=not args.no_tracked)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out) if args.out else (PKG_ROOT / "data" / "_migration" / f"scene-snapshot-{stamp}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.backup:
        bdir = Path(args.backup_dir) if args.backup_dir else out.parent
        tar_path = write_backup(workspace, payload, bdir, out)
        if not args.quiet:
            print(f"备份：{tar_path}")

    if not args.quiet:
        s = payload["summary"]
        print(f"✓ 现场快照写入 {out}")
        print(f"  修改 {s['modified']}｜未跟踪 {s['untracked']}｜删除 {s['deleted']}｜"
              f"忽略但保留 {s['ignored_keep']}｜已跟踪且干净 {s.get('tracked_clean', 0)}｜"
              f"合计 {s['total_files']} 个文件 / {s['total_bytes'] / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
