#!/usr/bin/env python3
"""M1 出口验收：把"包化和上下文隔离"的通过条件变成一条可复跑的命令。

方案 §14 的 M1：

| 工作包 | 主要文件/模块 | 交付与通过条件 | 依赖 |
|---|---|---|---|
| M1 包化和上下文隔离 | pyproject、core/book、contracts、providers、现有模块 imports |
| | **config/books 唯一权威；仓库外工作目录能调用统一包；同进程两本书不串状态；<br>normalizer/vendor 可按锁定来源重建** | M0 |

判据：

    M1-1 pyproject 声明了包与 `aifs` 入口，且源码在 src/ 布局下
    M1-2 `aifs` 从**仓库外**目录可调用（--workspace / 环境变量两种给法）
    M1-3 config/books.json 是唯一权威：没有「默认」项；data/books.json 不被任何生产代码读取
    M1-4 统一契约可导入且构造即校验（§6 的六个契约都在）
    M1-5 没有默认书：省略 --book 明确失败
    M1-6 同进程两本书不串状态（专用测试）
    M1-7 依赖来源已固定：vendor.lock.json 存在，vendor 来源经核对；不可重建的项被**显式标注**
    M1-8 仓库外调用不依赖 `sys.path.insert` 猜路径（生产源里按路径加载本项目模块的写法只剩过渡适配器）
    M1-9 全量测试退出码 0

用法：
    python tools/migrations/check_m1.py
    python tools/migrations/check_m1.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _scan import code_hits, iter_sources                            # noqa: E402

PKG_ROOT = HERE.parent.parent
REPO_ROOT = PKG_ROOT.parent
PYTHON = str(REPO_ROOT / ".venv-linux" / "bin" / "python")

#: 迁移期内**允许**保留路径注入的地方（每处都要能说出为什么）
ALLOWED_PATH_INJECTION = {
    "src/aiforstudy/providers/llm.py": "过渡适配器：按角色参数构造现有 lib/model.py 的客户端（M6 换成真 gateway）",
    "src/aiforstudy/providers/auditor.py": "vendor 的路径注入收在这一处（方案 §3 要求先固定来源再移除）",
    "lib/book.py": "旧入口的只读适配器（M9/M10 随旧入口一起退役）",
}


def _run(cmd: list[str], cwd: Path, env: dict | None = None) -> tuple[int, str]:
    merged = dict(os.environ)
    merged.pop("AIFORSTUDY_WORKSPACE", None)
    if env:
        merged.update(env)
    proc = subprocess.run(cmd, cwd=str(cwd), env=merged, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=900)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def check() -> dict:
    results: list[dict] = []

    def add(cid: str, title: str, ok: bool, evidence: str, detail: str = "") -> None:
        results.append({"id": cid, "判据": title, "通过": ok, "证据": evidence, "细节": detail})

    # ── M1-1 pyproject
    pyproject = PKG_ROOT / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8") if pyproject.is_file() else ""
    has_entry = 'aifs = "aiforstudy.cli:main"' in text
    has_src = 'package-dir = { "" = "src" }' in text
    add("M1-1", "pyproject 声明包与 aifs 入口，源码在 src/ 布局", has_entry and has_src,
        str(pyproject), f"入口={has_entry}｜src 布局={has_src}")

    # ── M1-2 仓库外调用
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        code1, out1 = _run([PYTHON, "-m", "aiforstudy", "--workspace", str(PKG_ROOT),
                            "books", "--json"], Path(tmp))
        code2, out2 = _run([PYTHON, "-m", "aiforstudy", "books", "--json"], Path(tmp),
                           env={"AIFORSTUDY_WORKSPACE": str(PKG_ROOT)})
        code3, out3 = _run([PYTHON, "-m", "aiforstudy", "books"], Path(tmp))
    ok = code1 == 0 and code2 == 0 and code3 == 2
    add("M1-2", "仓库外目录可调用统一包；无工作区提示时明确失败", ok,
        "aifs books（cwd=/tmp 临时目录）",
        f"--workspace→{code1}｜环境变量→{code2}｜无提示→{code3}（应为 2）")

    # ── M1-3 config 唯一权威
    registry = PKG_ROOT / "config" / "books.json"
    detail_parts: list[str] = []
    ok = registry.is_file()
    if ok:
        payload = json.loads(registry.read_text(encoding="utf-8"))
        no_default = "默认" not in payload and "default" not in payload
        ok = ok and no_default
        detail_parts.append(f"无「默认」项={no_default}")
    # ⚠️ 只认**代码行**：`data/books.json` 会出现在解释它的文档字符串里
    # （例如 lib/book.py 开头就写着"不再读 data/books.json"）。把注释算成依赖，
    # 门就永远达不到，或者逼着人删掉有价值的说明。
    legacy_readers: list[str] = []
    for base in ("lib", "blocks", "src", "tools"):
        for path in iter_sources(PKG_ROOT / base, suffixes=(".py",)):
            rel = path.relative_to(PKG_ROOT).as_posix()
            # `tools/migrations/` **故意豁免**：迁移/核对工具必须能提到旧表
            # （它要证明"旧表在哪、内容是什么"）。生产路径不该读旧表，那才是判据。
            if rel.startswith("tools/migrations/"):
                continue
            if code_hits(path, ["data/books.json", '"data" / "books.json"']):
                legacy_readers.append(rel)
    ok = ok and not legacy_readers
    detail_parts.append(f"仍读旧表的文件={legacy_readers or '无'}")
    legacy = PKG_ROOT / "data" / "_migration" / "legacy-books.json"
    detail_parts.append(f"旧表已归档为证据={legacy.is_file()}")
    add("M1-3", "config/books.json 唯一权威（旧表只作迁移证据，生产不双读）", ok,
        str(registry), "｜".join(detail_parts))

    # ── M1-4 契约齐全
    probe = (
        "from aiforstudy.contracts import (ArtifactRef, ExecutionState, Goal, "
        "PreparePolicy, Profile, QualityState, ReleaseManifest, RunRequest, ScopePlan, "
        "Selectors, StageResult)\n"
        "from aiforstudy.core.registry import BookContext, load_registry\n"
        "print('contracts ok')")
    code, out = _run([PYTHON, "-c", probe], Path("/tmp"))
    add("M1-4", "方案 §6 的六个契约与两轴状态都可导入", code == 0 and "contracts ok" in out,
        "aiforstudy.contracts（cwd=/tmp）", out.strip().splitlines()[-1][:120] if out else "")

    # ── M1-5 没有默认书
    code, out = _run([PYTHON, "-m", "aiforstudy", "--workspace", str(PKG_ROOT),
                      "plan", "--all"], Path("/tmp"))
    add("M1-5", "省略 --book 明确失败（不退回历史默认书）", code == 2,
        "aifs plan --all", f"退出码 {code}")

    # ── M1-6 同进程两本书不串状态
    code, out = _run([PYTHON, "-m", "pytest", "tests/contracts/test_m1_registry.py",
                      "-q", "-p", "no:cacheprovider"], PKG_ROOT)
    add("M1-6", "同进程两本书不串状态（专用测试通过）", code == 0,
        "tests/contracts/test_m1_registry.py", out.strip().splitlines()[-1][:120])

    # ── M1-7 依赖来源已固定
    lock = PKG_ROOT / "vendor.lock.json"
    detail = ""
    ok = False
    if lock.is_file():
        payload = json.loads(lock.read_text(encoding="utf-8"))
        arts = payload.get("artifacts") or {}
        vendor = (arts.get("auditor_vendor") or {}).get("source_wheel") or {}
        norm = (arts.get("normalizer") or {}).get("source_verdict") or {}
        ok = (vendor.get("verdict") == "verified-identical"
              and norm.get("verdict") in ("rejected", "verified-same-file-set"))
        detail = (f"vendor 来源={vendor.get('verdict')}｜normalizer 来源={norm.get('verdict')}"
                  f"｜已裁决漂移：{list((payload.get('accepted_drift') or {}))}")
    add("M1-7", "依赖来源已固定（vendor 经核对；不可重建项被显式标注）", ok, str(lock), detail)

    # ── M1-8 路径注入已收敛
    found: list[str] = []
    for path in iter_sources(PKG_ROOT / "src", suffixes=(".py",)):
        rel = path.relative_to(PKG_ROOT).as_posix()
        if rel in ALLOWED_PATH_INJECTION:
            continue
        # 同样只认代码行：包头的文档字符串里写着"移除散落的 sys.path.insert"，
        # 那是**目标**不是**用法**（第一版检查就在这里误报了两处）。
        if code_hits(path, [r"sys\.path\.insert", r"spec_from_file_location"]):
            found.append(rel)
    add("M1-8", "包内不再按路径猜位置（允许清单之外没有 sys.path.insert）", not found,
        f"允许清单 {len(ALLOWED_PATH_INJECTION)} 处（过渡适配器）",
        f"允许清单外发现：{found}" if found else "包内干净")

    # ── M1-9 全量测试
    code, out = _run([PYTHON, "-m", "pytest", "tests/", "-q", "-ra",
                      "-p", "no:cacheprovider"], PKG_ROOT)
    m_p = re.search(r"(\d+) passed", out)
    m_s = re.search(r"(\d+) skipped", out)
    m_f = re.search(r"(\d+) failed", out)
    passed = int(m_p.group(1)) if m_p else 0
    skipped = int(m_s.group(1)) if m_s else 0
    failed = int(m_f.group(1)) if m_f else 0
    add("M1-9", "全量测试退出码 0、无 failed", code == 0 and failed == 0 and passed > 0,
        "pytest tests/ -q -ra",
        f"{passed} passed / {skipped} skipped / {failed} failed｜退出码 {code}")

    return {"_meta": {"file_type": "m1_exit_check", "schema_version": "1.0",
                      "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
            "通过": all(r["通过"] for r in results), "判据": results}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M1 出口验收")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    payload = check()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for r in payload["判据"]:
            print(f"{'✓' if r['通过'] else '✗'} {r['id']} {r['判据']}")
            if r["细节"]:
                print(f"      {r['细节']}")
            print(f"      证据：{r['证据']}")
        print(f"\nM1 出口：{'通过' if payload['通过'] else '未通过'}")
    return 0 if payload["通过"] else 1


if __name__ == "__main__":
    sys.exit(main())
