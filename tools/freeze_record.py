#!/usr/bin/env python3
"""冻结记录：把"这次交付到底是哪一版代码 + 哪一套环境 + 哪一份产物 + 跑出来什么"落成一份可复核的文件。

计划 §T11 最后一条要求"冻结代码与环境指纹，记录 PASS／SKIP 及理由和新 wheel SHA"。
这个脚本就是那条要求的执行者 —— 它只**测量并记录**，不做任何判断、不改任何东西。

为什么需要它：交付时最容易被含糊过去的三件事恰好是复核时最需要的：
1. **代码是哪一版** —— "我本地是好的"没有意义，要能说出这一版的内容摘要；
2. **环境是什么** —— 同一份代码在不同依赖/字体/隔离配置下行为不同；
3. **哪些项是跳过的、为什么** —— "全绿"与"关键项跳过"必须能分开看。

用法：

    python tools/freeze_record.py --out <记录.json> \
        [--wheel <whl 路径> ...] [--pytest-log <pytest -ra 输出>] \
        [--environment <T07 environment.json>] [--label <标签>]

它会打印一份人类可读摘要，并把完整 JSON 写到 `--out`。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

#: 算进代码指纹的根（相对仓库根）。**只含交付需要的东西**：
#: 源码、随包旧树、配置、测试。刻意**不含** `data/`（运行产物）、
#: `build/`、`dist/`、缓存与 node_modules —— 那些是产物或本机残留，不是"代码"。
CODE_ROOTS = ("src", "blocks", "lib", "tools", "config", "tests")
SKIP_DIR_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                  "node_modules", "build", "dist", ".cache", ".git"}
#: 打包/安装产生的元数据目录（`src/aiforstudy.egg-info/` 这类）。
#: ⚠️ 必须排除：**构建一次 wheel 就会重新生成它们**，算进指纹的话
#: "同一版代码"会因为你构建过而得到**不同的指纹** —— 冻结记录立刻失去意义。
#: （这是我自己在核对"wheel 与代码是否一致"时发现的，见台账 §9.47。）
SKIP_DIR_SUFFIXES = (".egg-info", ".dist-info", ".egg-link")
SKIP_SUFFIXES = {".pyc", ".pyo", ".so", ".o", ".a"}

SKIP_LINE = re.compile(r"^SKIPPED\s+\[(\d+)\]\s+(.+?):(\d+):\s*(.*)$")
SUMMARY_LINE = re.compile(
    r"^(?:=+\s*)?(?P<body>(?:\d+\s+\w+(?:,\s*)?)+)(?:\s*in\s*[\d.]+s)?", re.M)
COUNT = re.compile(r"(\d+)\s+(passed|failed|skipped|xfailed|xpassed|error|errors|deselected|warnings)")


def _digest_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_code_files(root: Path, roots: tuple[str, ...]):
    for name in roots:
        base = root / name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if any(part in SKIP_DIR_NAMES or part.endswith(SKIP_DIR_SUFFIXES)
                   for part in path.parts):
                continue
            if path.suffix in SKIP_SUFFIXES:
                continue
            yield path


def code_fingerprint(root: Path, roots: tuple[str, ...] = CODE_ROOTS) -> dict:
    """代码内容指纹：`相对路径 → sha256`，再整体取一次摘要。

    只用**相对路径 + 内容**，所以与工作目录、mtime、机器都无关。
    """
    rows: dict[str, str] = {}
    for path in _iter_code_files(root, roots):
        rows[str(path.relative_to(root))] = _digest_file(path)
    overall = hashlib.sha256()
    for rel in sorted(rows):
        overall.update(rel.encode("utf-8"))
        overall.update(b"\0")
        overall.update(rows[rel].encode("ascii"))
        overall.update(b"\n")
    return {"roots": list(roots), "files": len(rows), "digest": "sha256:" + overall.hexdigest(),
            "detail": rows}


def environment_fingerprint(environment_json: Path | None) -> dict:
    """环境指纹：Python 与关键依赖版本，外加（可选）T07 那份渲染环境指纹。"""
    out: dict = {"python": platform.python_version(), "python_impl": platform.python_implementation(),
                 "platform": platform.platform(), "machine": platform.machine(),
                 "packages": {}, "render_environment": None}
    for name in ("manim", "numpy", "scipy", "pillow", "pydantic", "pytest"):
        try:
            module = __import__(name)
            out["packages"][name] = str(getattr(module, "__version__", "unknown"))
        except Exception:                                     # noqa: BLE001
            out["packages"][name] = None
    if environment_json and Path(environment_json).is_file():
        raw = json.loads(Path(environment_json).read_text(encoding="utf-8"))
        # 只把**指纹与关键事实**带进来，避免把一大坨探针输出塞进冻结记录
        out["render_environment"] = {
            "path": str(environment_json),
            "sha256": "sha256:" + _digest_file(Path(environment_json)),
            "environment_hash": raw.get("environment_hash") or raw.get("hash"),
            "keys": sorted(raw)[:20],
        }
    return out


def wheel_fingerprints(paths: list[str]) -> list[dict]:
    out = []
    for item in paths or []:
        path = Path(item)
        if path.is_file():
            out.append({"path": str(path), "bytes": path.stat().st_size,
                        "sha256": "sha256:" + _digest_file(path)})
    return out


def parse_pytest_log(path: Path | None) -> dict:
    """从 `pytest -q -ra` 的输出里读出计数与**每一条跳过及其理由**。

    为什么必须解析理由：计划明确要求"记录 PASS／SKIP 及理由"，
    而"关键新项跳过数必须 0"这句话只有把跳过逐条列出来才**可复核**。
    """
    if not path or not Path(path).is_file():
        return {"log": None}
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    counts: dict[str, int] = {}
    for line in text.splitlines():
        if COUNT.search(line) and ("passed" in line or "failed" in line or "error" in line):
            found = dict((m.group(2), int(m.group(1))) for m in COUNT.finditer(line))
            if found and (not counts or sum(found.values()) >= sum(counts.values())):
                counts = found
    skips = [{"count": int(m.group(1)), "where": f"{m.group(2)}:{m.group(3)}",
              "reason": m.group(4).strip()}
             for m in (SKIP_LINE.match(line) for line in text.splitlines()) if m]
    failures = [line.strip() for line in text.splitlines() if line.startswith("FAILED")]
    errors = [line.strip() for line in text.splitlines() if line.startswith("ERROR")]
    return {"log": str(path), "counts": counts, "skips": skips,
            "skip_total": sum(item["count"] for item in skips),
            "failures": failures, "errors": errors}


def git_revision(root: Path) -> dict:
    """尽力取一次 git 版本信息（取不到不算失败：交付物可能不带 .git）。"""
    info: dict = {"head": None, "dirty": None}
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=20)
        if head.returncode == 0:
            info["head"] = head.stdout.strip()
        status = subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                                capture_output=True, text=True, timeout=30)
        if status.returncode == 0:
            lines = [line for line in status.stdout.splitlines() if line.strip()]
            info["dirty"] = len(lines)
    except (OSError, subprocess.SubprocessError):
        pass
    return info


def build_record(*, root: Path, label: str, wheel_paths: list[str],
                 pytest_log: Path | None, environment: Path | None) -> dict:
    return {
        "record_version": "1.0",
        "label": label,
        "frozen_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "code": code_fingerprint(root),
        "git": git_revision(root),
        "environment": environment_fingerprint(environment),
        "wheels": wheel_fingerprints(wheel_paths),
        # 这条必须写在记录里，否则复核的人会把 wheel 的 sha256 当成代码身份：
        # 实测（2026-10-07）：源码一字未改，连续两次 bdist_wheel 得到的 whl
        # sha256 **不同**（zip 内嵌时间戳，构建不可按位复现）。
        # 所以：wheel 的 sha256 标识"这一份构建产物"（用来核对"我装的是不是这一份"），
        # 代码身份看 code.digest（它已被验证：构建前后一致）。
        "wheel_reproducibility": {
            "bit_reproducible": False,
            "why": "bdist_wheel 把文件时间戳写进 zip，同一份源码两次构建 SHA 不同",
            "use_for_code_identity": "code.digest",
            "use_for_artifact_identity": "wheels[].sha256",
        },
        "suite": parse_pytest_log(pytest_log),
    }


def summarise(record: dict) -> str:
    code = record["code"]
    suite = record["suite"]
    lines = [
        f"标签            : {record['label']}",
        f"冻结时间        : {record['frozen_at']}",
        f"代码指纹        : {code['digest']}（{code['files']} 个文件，根：{', '.join(code['roots'])}）",
        f"git             : head={record['git']['head'] or '(不可用)'} 未提交条目={record['git']['dirty']}",
        f"Python          : {record['environment']['python']}",
    ]
    for wheel in record["wheels"]:
        lines.append(f"wheel           : {wheel['sha256']}  {wheel['bytes']}B  {wheel['path']}")
    if suite.get("log"):
        counts = " ".join(f"{v} {k}" for k, v in sorted(suite["counts"].items()))
        lines.append(f"suite           : {counts or '(未解析到计数)'}")
        lines.append(f"跳过            : 共 {suite['skip_total']} 条，逐条理由见 JSON")
        lines.append(f"失败/错误       : {len(suite['failures'])} / {len(suite['errors'])}")
    else:
        lines.append("suite           : (未提供 pytest 日志)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成冻结记录（代码+环境+产物+suite 结果）")
    parser.add_argument("--out", required=True, help="记录写到哪个 JSON")
    parser.add_argument("--root", default=None, help="仓库根（默认取本脚本的上一级）")
    parser.add_argument("--label", default="t11-freeze")
    parser.add_argument("--wheel", action="append", default=[], help="wheel 路径（可多次）")
    parser.add_argument("--pytest-log", default=None, help="`pytest -q -ra` 的输出文件")
    parser.add_argument("--environment", default=None, help="T07 的 environment.json")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    record = build_record(root=root, label=args.label, wheel_paths=args.wheel,
                          pytest_log=Path(args.pytest_log) if args.pytest_log else None,
                          environment=Path(args.environment) if args.environment else None)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(summarise(record))
    print(f"\n完整记录：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
