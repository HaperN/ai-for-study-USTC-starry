#!/usr/bin/env python3
"""生成 `vendor.lock.json`：把"依赖到底是哪一份"写成可核对的制品指纹。

方案 §3：

> 第三方审核器当前需要本地 vendor 与上游依赖。先把正在使用的 vendor 版本作为独立、
> 带来源和许可证信息的**依赖制品**固定，验证一致性，再移除路径注入；
> **不允许迁移时静默换成环境中另一版同名包。**

> 当前 normalizer 的可核对起点是 `undergraduate-fitted-exam-system==0.15.0`，
> 其拥有 stem_runner 并依赖 `courseforge==2.7.0`；本地 wheel 来源为 CourseForgeV2
> 安装包，SHA256 为 `ad34d4c6…`。

本工具**不会自动接受任何漂移**。它把三件事分开记：

* `payload`  —— 磁盘上实际会被 import 的那批文件的内容指纹（生产真正跑的）；
* `source_hash` —— 来源制品（wheel/目录）现在盘上的哈希；
* `recorded_source_hash` —— 安装时记下的来源哈希（`direct_url.json`）。

三者对不上就叫漂移，写进 JSON 并**打印出来**，等人工在 `accepted_drift` 里裁决。
原因：同版本号不同字节正是"静默换成另一版同名包"的形态，自动"以最新为准"会把
一次供应链替换伪装成一次正常打包。

用法：
    python tools/migrations/lock_sources.py            # 只打印，不写
    python tools/migrations/lock_sources.py --write    # 写 vendor.lock.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent.parent
sys.path.insert(0, str(PKG_ROOT / "src"))

from aiforstudy.core.workspace import Workspace                      # noqa: E402
from aiforstudy.providers import auditor, normalizer                 # noqa: E402
from aiforstudy.providers.artifacts import (                         # noqa: E402
    SourceLock, payload_digest, wheel_payload_digest)

#: 声明里写的"本地 wheel 来源"目录（相对**仓库根** = workspace.root.parent）。
#: ⚠️ 用仓库根而不是 cwd：相对 cwd 的路径会随"从哪个目录调工具"而变，
#: 于是同一台机器上两个目录跑出两个结论 —— 那正是本工具要消灭的那类不确定性。
SOURCE_WHEEL_DIR_REL = Path("CourseForgeV2") / "安装包"
NORMALIZER_WHEEL = "undergraduate_fitted_exam_system-0.15.0-py3-none-any.whl"
UPSTREAM_WHEEL = "courseforge-2.7.0-py3-none-any.whl"
#: 审核器 vendor 的**真正来源**：归档里那只 hbep 轮子（M1 核对发现它 18/18 个 .py 与
#: vendor 目录逐字节相同，只差换行符）。路径相对**仓库根**。
VENDOR_WHEEL_REL = (Path("归档") / "快照" / "_已移除_审核安装包"
                    / "hbep_ug_stem_controller_v2-0.4.1-py3-none-any.whl")
VENDOR_WHEEL_PREFIX = "hbep_controller_v2/"


def _wheel_entry(name: str, workspace: Workspace | None = None) -> dict:
    base = (workspace.root.parent if workspace else PKG_ROOT.parent) / SOURCE_WHEEL_DIR_REL
    path = base / name
    if not path.is_file():
        return {"declared": str(path), "present": False}
    from aiforstudy.core.digest import digest_file
    return {"declared": str(path), "present": True, "sha256": digest_file(path)}


def build(workspace: Workspace, accepted: dict[str, str] | None = None
          ) -> tuple[dict, list[str]]:
    """`accepted` 必须在**核对之前**传进来。

    ⚠️ 第一版把 accepted 放在核对之后读，于是 `--accept-drift` 当次不生效：
    输出还在报同一条漂移，而文件里已经写下了裁决 —— 一个"说了接受却仍报错"的自相矛盾。
    裁决是**输入**，不是输出。
    """
    lock = SourceLock.load(workspace.root / "vendor.lock.json")
    lock.accepted_drift = dict(accepted or {})
    normalizer_art, normalizer_problems = normalizer.verify(
        workspace, lock,
        source_wheel=workspace.root.parent / SOURCE_WHEEL_DIR_REL / NORMALIZER_WHEEL)
    auditor_art, auditor_problems = auditor.verify(workspace, lock)

    upstream_dir = workspace.root.parent / ".venv-linux" / "lib" / "python3.12" / "site-packages" / "courseforge"
    upstream_payload, upstream_files = ("", 0)
    if upstream_dir.is_dir():
        upstream_payload, upstream_files = payload_digest(upstream_dir)

    # ── 审核器 vendor 的来源核对：盘上的目录到底是不是那只 wheel 解出来的
    vendor_wheel = workspace.root.parent / VENDOR_WHEEL_REL
    vendor_source: dict = {"declared": str(vendor_wheel), "present": vendor_wheel.is_file()}
    if vendor_wheel.is_file():
        from aiforstudy.core.digest import digest_file
        vendor_source["sha256"] = digest_file(vendor_wheel)
        dir_norm, dir_n = payload_digest(auditor_art.path, normalize_newlines=True)
        whl_norm, whl_n = wheel_payload_digest(vendor_wheel, VENDOR_WHEEL_PREFIX)
        vendor_source["dir_payload_normalized"] = dir_norm
        vendor_source["wheel_payload_normalized"] = whl_norm
        vendor_source["files_dir"] = dir_n
        vendor_source["files_wheel"] = whl_n
        if dir_norm == whl_norm:
            vendor_source["verdict"] = "verified-identical"
            vendor_source["note"] = ("vendor 目录与这只 wheel 的对应子树**内容一致**（换行符归一后）。"
                                     "方案 §13 要求'保留来源'：这只 wheel 就是那个来源。")
        else:
            vendor_source["verdict"] = "mismatch"
            problems.append(
                f"审核器 vendor 与来源 wheel 不一致：目录 {dir_norm} ≠ wheel {whl_norm}")
    else:
        vendor_source["verdict"] = "source-missing"
        problems.append(f"审核器 vendor 的来源 wheel 不在位：{vendor_wheel}")

    # ── normalizer 的来源核对：现盘 wheel 到底是不是安装的那一份
    normalizer_source: dict = {"declared": str(
        workspace.root.parent / SOURCE_WHEEL_DIR_REL / NORMALIZER_WHEEL)}
    norm_wheel = workspace.root.parent / SOURCE_WHEEL_DIR_REL / NORMALIZER_WHEEL
    if norm_wheel.is_file():
        import zipfile
        installed_dir = Path(normalizer_art.path) if normalizer_art.path else None
        with zipfile.ZipFile(norm_wheel) as zf:
            in_wheel = {n.split("/", 1)[1] for n in zf.namelist()
                        if n.startswith(f"{normalizer.OWNER_MODULE}/") and n.endswith(".py")}
        in_installed = ({p.name for p in installed_dir.glob("*.py")}
                        if installed_dir else set())
        missing_from_wheel = sorted(in_installed - in_wheel)
        normalizer_source.update({
            "files_in_wheel": len(in_wheel), "files_installed": len(in_installed),
            "installed_only": missing_from_wheel,
        })
        if missing_from_wheel:
            normalizer_source["verdict"] = "rejected"
            normalizer_source["note"] = (
                f"⚠️ 现盘 wheel 里**少了 {len(missing_from_wheel)} 个安装版有的模块**"
                f"（{', '.join(missing_from_wheel[:6])}…）。对照源码可见 wheel 是被裁剪过的"
                f"变体（注释写着「审核已移除」），安装版才是含审核的完整版。"
                f"**不能用这只 wheel 重建**：那样会静默删掉整个审核能力。"
                f"安装时记录的那只 wheel（ad34d4c6…）已不在盘上 —— A01 的缺口记在这里。")
        else:
            normalizer_source["verdict"] = "verified-same-file-set"
            normalizer_source["note"] = "文件集合一致；逐文件内容差异见 drift 字段"
    else:
        normalizer_source["verdict"] = "source-missing"
        problems.append(f"normalizer 的声明来源 wheel 不在位：{norm_wheel}")

    artifacts = {
        "normalizer": {
            **normalizer_art.as_dict(),
            "declared_source_wheel": _wheel_entry(NORMALIZER_WHEEL, workspace),
            "source_verdict": normalizer_source,
            "owner_module": normalizer.OWNER_MODULE,
            "note": ("payload = 实际被 import 的 stem_runner/ 目录内容；"
                     "source_hash = 现盘上 wheel 的哈希；"
                     "recorded_source_hash = 安装时 direct_url.json 记下的 wheel 哈希"),
        },
        "normalizer_upstream": {
            "kind": "python-dist", "name": normalizer.UPSTREAM, "version": "2.7.0",
            "payload": upstream_payload, "payload_files": upstream_files,
            "declared_source_wheel": _wheel_entry(UPSTREAM_WHEEL, workspace),
        },
        "auditor_vendor": {**auditor_art.as_dict(), "source_wheel": vendor_source},
    }

    problems = list(normalizer_problems) + list(auditor_problems)
    if auditor_art.payload_files == 0:
        problems.append("审核器 vendor 里一个文本文件都没数到 —— 目录缺失或结构变了")
    return artifacts, problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成/核对依赖制品锁（不自动接受漂移）")
    ap.add_argument("--workspace", default=str(PKG_ROOT))
    ap.add_argument("--write", action="store_true", help="写 vendor.lock.json")
    ap.add_argument("--accept-drift", action="append", default=[],
                    metavar="KEY=理由",
                    help="显式接受某个制品的漂移（**必须写理由**）；可重复")
    args = ap.parse_args(argv)

    workspace = Workspace(root=Path(args.workspace).resolve())
    existing_path = workspace.root / "vendor.lock.json"

    # 裁决是**输入**：先把已有的与命令行给的都收齐，再去核对
    accepted: dict[str, str] = {}
    if existing_path.is_file():
        try:
            accepted = dict(json.loads(existing_path.read_text(encoding="utf-8"))
                            .get("accepted_drift") or {})
        except ValueError:
            pass
    for item in args.accept_drift:
        key, _, reason = item.partition("=")
        if not key.strip() or not reason.strip():
            print(f"✗ --accept-drift 必须是 KEY=理由，实际是 {item!r}", file=sys.stderr)
            return 2
        accepted[key.strip()] = reason.strip()

    artifacts, problems = build(workspace, accepted)

    payload = {"_meta": {"file_type": "vendor_lock", "schema_version": "1.0",
                         "generator": "aiforstudy.providers.lock",
                         "field_notes": {
                             "artifacts[].payload": "实际会被 import 的那批文件的内容指纹（生产真正跑的）",
                             "artifacts[].source_hash": "来源制品（wheel/目录）本身的哈希",
                             "artifacts[].recorded_source_hash": "安装时记下的来源哈希（direct_url.json）",
                             "artifacts[].drift": "source_hash 与 recorded_source_hash 不一致 = 同版本不同字节",
                             "accepted_drift": "键=制品 key，值=为什么接受这处漂移。**不写就等于不接受**",
                         }},
               "artifacts": artifacts, "accepted_drift": accepted}

    print(f"normalizer：payload {artifacts['normalizer']['payload']}"
          f"（{artifacts['normalizer']['payload_files']} 文件）")
    print(f"            来源 wheel 现盘哈希 {artifacts['normalizer']['source_hash'] or '（缺）'}")
    print(f"            安装时记录哈希   {artifacts['normalizer']['recorded_source_hash'] or '（缺）'}")
    if artifacts["normalizer"].get("drift"):
        print("            ⚠️ **漂移**：同一版本号、不同字节")
    print(f"审核器 vendor：payload {artifacts['auditor_vendor']['payload']}"
          f"（{artifacts['auditor_vendor']['payload_files']} 文件）"
          f"｜许可证 {artifacts['auditor_vendor']['license']}")
    print()
    if problems:
        print(f"发现 {len(problems)} 个问题（**没有被自动接受**）：")
        for problem in problems:
            print(f"  ✗ {problem}")
    else:
        print("✓ 没有发现问题")

    if args.write:
        existing_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        print(f"\n已写入 {existing_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
