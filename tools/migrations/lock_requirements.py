#!/usr/bin/env python3
"""生成 `requirements.lock`：**把"可重建"分成已证与未证两栏**。

方案 §3：

> `requirements.lock` Python 依赖及来源指纹

方案 §3 还写死了验收口径：

> 锁定清单还须收录传递依赖与前端本地资产许可证；**在干净环境验证安装，
> 不能只导出当前环境的包名就视为可重建**。

这两句话合起来决定本工具的输出形状：它**不会**把 `pip freeze` 的结果包装成"已锁定"。
`pip freeze` 只有版本号，没有来源哈希 —— 满足不了"可重建"。所以输出分三栏：

* `pinned_by_hash`：来源哈希已核对（仓库内三个 wheel + vendor 轮子）；
* `version_only`  ：只有版本号（环境里其余包），并明确标注**未证**；
* `frontend`      ：Node 侧本地资产及其许可证。

用法：
    python tools/migrations/lock_requirements.py --write
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent.parent
REPO_ROOT = PKG_ROOT.parent
PYTHON = str(REPO_ROOT / ".venv-linux" / "bin" / "python")

LOCAL_WHEELS = {
    "undergraduate-fitted-exam-system": Path("CourseForgeV2/安装包")
        / "undergraduate_fitted_exam_system-0.15.0-py3-none-any.whl",
    "courseforge": Path("CourseForgeV2/安装包") / "courseforge-2.7.0-py3-none-any.whl",
    "exercise-authoring-system": Path("CourseForgeV2/安装包")
        / "exercise_authoring_system-0.2.0-py3-none-any.whl",
    "hbep-ug-stem-controller-v2": Path("归档/快照/_已移除_审核安装包")
        / "hbep_ug_stem_controller_v2-0.4.1-py3-none-any.whl",
}


def _freeze() -> dict[str, str]:
    proc = subprocess.run([PYTHON, "-m", "pip", "freeze"], stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT)
    out: dict[str, str] = {}
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, _, version = line.partition("==")
        out[name.strip().lower()] = version.strip()
    return out


def _hash(path: Path) -> str | None:
    if not path.is_file():
        return None
    from aiforstudy.core.digest import digest_file
    return digest_file(path)


def _license_of(dist_name: str) -> str:
    try:
        import importlib.metadata as md
        meta = md.metadata(dist_name)
    except Exception:                                   # noqa: BLE001
        return "（取不到 dist-info）"
    classifiers = [c for c in meta.get_all("Classifier") or [] if "License" in c]
    field = meta.get("License")
    if field and field.strip() and field.strip().upper() != "UNKNOWN":
        return field.strip()[:200]
    return ("；".join(classifiers) if classifiers
            else "（METADATA 既无 License 字段也无 License 分类器 → **未记录**）")


def build() -> dict:
    frozen = _freeze()
    # ⚠️ "仓库里有这只 wheel"**不等于**"它能重建出现在跑的东西"。M1 实测抓到：
    #    `undergraduate-fitted-exam-system` 的 wheel 被裁剪过（少 12 个审核模块），
    #    而安装版是完整版。所以这里把 vendor.lock.json 的判定读进来，
    #    否则这份清单会自信地写"可按此重建"—— 而那正是把审核能力删掉的路径。
    drift: dict[str, dict] = {}
    lock_path = PKG_ROOT / "vendor.lock.json"
    if lock_path.is_file():
        try:
            arts = json.loads(lock_path.read_text(encoding="utf-8")).get("artifacts") or {}
            if (arts.get("normalizer") or {}).get("source_verdict"):
                drift["undergraduate-fitted-exam-system"] = arts["normalizer"]["source_verdict"]
        except ValueError:
            pass
    pinned: dict[str, dict] = {}
    for name, rel in LOCAL_WHEELS.items():
        path = REPO_ROOT / rel
        entry = {"version": frozen.get(name.lower(), ""),
                 "source": str(rel), "present": path.is_file(),
                 "sha256": _hash(path), "license": _license_of(name),
                 "rebuildable": True}
        verdict = drift.get(name)
        if verdict and verdict.get("verdict") == "rejected":
            entry["rebuildable"] = False
            entry["rebuildable_reason"] = verdict.get("note")
            entry["source_missing_modules"] = verdict.get("installed_only") or []
        pinned[name] = entry
    covered = {n.lower() for n in pinned}
    version_only = {name: version for name, version in sorted(frozen.items())
                    if name not in covered}

    # Node 依赖的声明在**工作区根**（M1 从 `.devtools/` 搬过来：那份声明与根重复，
    # 而 `.devtools/` 现在只剩无头浏览器 —— 二进制能力，不是 npm 依赖）。
    node: dict = {}
    for candidate in ("package.json", "package-lock.json"):
        path = PKG_ROOT / candidate
        node[candidate] = {"path": candidate, "present": path.is_file()}
    modules = PKG_ROOT / "node_modules"
    if modules.is_dir():
        pkgs = sorted(p.name for p in modules.iterdir()
                      if p.is_dir() and not p.name.startswith("."))
        node["installed_top_level"] = len(pkgs)
        node["packages_sample"] = pkgs[:12]
        lic = [p.name for p in modules.iterdir()
               if p.is_dir() and (p / "LICENSE").is_file()]
        node["with_license_file"] = len(lic)
        node["license_gap"] = (f"{len(pkgs) - len(lic)} 个顶层包没有 LICENSE 文件；"
                               f"方案 §3 要求前端本地资产**连同许可证**收录，属 M10 待办")
    chrome = PKG_ROOT / ".devtools" / "chrome" / "chrome-linux64" / "chrome"
    node["headless_chrome"] = {"path": ".devtools/chrome/chrome-linux64/chrome",
                               "present": chrome.is_file()}

    return {
        "_meta": {
            "file_type": "requirements_lock", "schema_version": "1.0",
            "generator": "aiforstudy.tools.migrations.lock_requirements",
            "python": sys.version.split()[0],
            "field_notes": {
                "pinned_by_hash": "来源 wheel 在仓库内且已核对 sha256 —— 这一栏才是'可按锁定来源重建'",
                "version_only": "只有版本号（来自 pip freeze），**没有来源哈希**。方案 §3 明确：只导出包名不算可重建。这一栏是 M10 的待办，不是'已锁定'",
                "frontend": "Node 侧本地资产（jsdom 等）；方案 §3 要求连同许可证一起收录",
                "为什么不能只写一行 pip freeze": "版本号相同、字节不同的包真实存在（M1 就抓到一只：同版本 wheel 被裁剪掉了 12 个审核模块）。没有来源哈希的清单无法发现这种事",
            },
        },
        "pinned_by_hash": pinned,
        "version_only": version_only,
        "version_only_note": ("这些包目前只按版本号固定。要做到方案 §3 的'可重建'，需要"
                              "逐包取得来源 wheel/sdist 的 sha256 并在干净环境验证安装 —— "
                              "属 M10 工作，未完成前不得声称整环境可重建。"),
        "frontend": node,
        "interpreter": ">=3.12,<3.13",
    }


def to_markdown(payload: dict) -> str:
    lines = ["# requirements.lock（人读版）", "",
             f"生成器：`{payload['_meta']['generator']}`｜Python {payload['_meta']['python']}", "",
             "## 一、来源哈希已核对", "",
             "| 包 | 版本 | 来源 | sha256 | 可按此重建 | 许可证 |", "|---|---|---|---|---|---|"]
    for name, item in sorted(payload["pinned_by_hash"].items()):
        sha = (item["sha256"] or "**缺**")[:22] + ("…" if item["sha256"] else "")
        ok = "✓" if item.get("rebuildable") else "**✗ 不可**"
        lines.append(f"| `{name}` | {item['version']} | `{item['source']}`"
                     f"{'' if item['present'] else '（不在位）'} | {sha} | {ok} | {item['license']} |")
        if not item.get("rebuildable"):
            lines.append(f"| | | | | | ↑ {item.get('rebuildable_reason','')} |")
    lines += ["", "## 二、只有版本号（**未证**，M10 待办）", "",
              "```text"]
    for name, version in payload["version_only"].items():
        lines.append(f"{name}=={version}")
    lines += ["```", "", payload["version_only_note"], "",
              "## 三、前端本地资产（Node）", "", "```json",
              json.dumps(payload["frontend"], ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成 requirements.lock（区分已证/未证）")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    payload = build()
    ok_n = sum(1 for i in payload["pinned_by_hash"].values() if i.get("rebuildable"))
    print(f"来源哈希已核对：{len(payload['pinned_by_hash'])} 个（其中**可按此重建** {ok_n} 个）")
    for name, item in sorted(payload["pinned_by_hash"].items()):
        mark = ("✓" if item.get("rebuildable")
                else ("!" if item["sha256"] else "×"))
        print(f"  {mark} {name} {item['version']} ← {item['source']}")
        print(f"      许可证：{item['license']}")
        if not item.get("rebuildable"):
            print(f"      ⚠️ **不可按此重建**：{item.get('rebuildable_reason','')}")
    print(f"只有版本号（未证）：{len(payload['version_only'])} 个")
    print(f"前端资产：{json.dumps(payload['frontend'], ensure_ascii=False)[:200]}")
    if args.write:
        (PKG_ROOT / "requirements.lock").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        (PKG_ROOT / "docs" / "requirements.lock.md").parent.mkdir(parents=True, exist_ok=True)
        (PKG_ROOT / "docs" / "requirements.lock.md").write_text(
            to_markdown(payload), encoding="utf-8")
        print(f"\n已写入 {PKG_ROOT / 'requirements.lock'}")
        print(f"已写入 {PKG_ROOT / 'docs' / 'requirements.lock.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
