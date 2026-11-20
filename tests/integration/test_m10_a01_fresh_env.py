"""M10 **A01** 验收：全新虚拟环境从锁定来源安装 —— 在**仓库外 cwd** 跑通，且
不读旧虚拟环境、不读 CourseForgeV2 源码、不依赖个人 Blender 路径。

方案 §15 A01（逐字）：

> 全新虚拟环境从锁定来源安装，在仓库外 cwd 调用 help/plan/check 并跑 normalizer、
> 审核器、启用后端夹具；打乱模块导入顺序 → 不读取旧虚拟环境或 CourseForgeV2 源码，
> 不依赖个人 Blender 路径。

## 这个文件怎么验（以及为什么这么验）

1. **真的建一个全新 venv**（默认 `/tmp/aiforstudy-a01-venv`，可用
   `AIFORSTUDY_A01_VENV` 覆盖）—— 不是打个记号。装法见 `_provision()`；
   锁定来源 = `requirements.lock` 的 `version_only`（49 个 pin）+ 本仓库本身。
2. **CLI 没有 `check` 子命令**。这是事实，不是漏做：`aifs --help` 里有
   `books` / `plan` / `capabilities` / `sources` / `run` / `prepare` / `resume` /
   `runs` / `events` / `ui`。所以 A01 里那句 `check` 用**最接近的只读三件套**代替：
   `books`（工作区与产物状态）、`capabilities`（能力预检）、`sources`（依赖来源核对，
   它内部正是 normalizer + 审核器两条核对）。本文件显式断言"没有 check"，
   免得下一个人以为漏测。
3. **驱动器**（`_DRIVER_SOURCE`）在**新 venv**里执行，cwd 在仓库外（`tmp_path`），
   并给每个 CLI 子进程挂一个 `sitecustomize` 守卫：把 `stem_runner` / `courseforge` /
   `courseforge_content` / `exercise_authoring_system` / `undergraduate_fitted_exam_system`
   从 `sys.meta_path` 上封掉。**真偷偷 import 了就会当场 ImportError**，
   而不是"碰巧从别处找到"。
4. **打乱导入顺序**：同一驱动器用不同 seed 与不同 `PYTHONHASHSEED` 跑多次，
   比对内容摘要（工作区解析、plan 指纹、能力的可用后端、normalizer 现场、
   审核器哈希、后端夹具结果）必须逐字节一致。
5. **不用个人路径**：另跑一次，把 `HOME` 换到空目录、把 PATH 里 `$HOME` 下的条目
   全部摘掉 → plan 指纹与审核器结论不变，启用后端集合按能力预检**如实缩小**
   （`circuitikz` 要 `pdflatex`、`three` 要 `blender`：两者都先认 PATH、
   再认 `config/capabilities.json` 里**声明**的位置；声明的位置随 HOME 一起消失），
   缩小的每一个仍能画出来并校验通过。

## 排除清单（A01 要求"记录排除 + 理由"）

`requirements.lock` 的 `pinned_by_hash` 里 4 个 CourseForgeV2 系包**不装**：

| 包 | 为什么可以不装 |
|---|---|
| `undergraduate-fitted-exam-system`（import 名 `stem_runner`） | `rebuildable: false`（现盘 wheel 被裁剪掉 12 个审核模块）。它只被**待退役 tools** 使用；在 `src/aiforstudy` 里仅作为 normalizer 的**比对对象名字**出现（`normalizer.py:25-26` 两个字符串常量），不被 import |
| `courseforge` | 同上，是 `stem_runner` 的**上游名字**；新生产路径不 import |
| `exercise-authoring-system` | 全仓库 0 处 import |
| `hbep-ug-stem-controller-v2` | 审核能力已在树内 vendored（`blocks/audit/vendor/hbep_controller_v2`，与归档 wheel **逐字节一致**），不需要装 |

`version_only` 的 49 个 pin 里**没有**这 4 个名字（`pinned_by_hash` 与 `version_only`
不相交，本文件有断言），所以"按锁安装"与"排除这 4 个"是同一份清单的两半。

对应文档：`docs/M10-A01-验收记录.md`。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]              # AIforStudy/
LOCK = ROOT / "requirements.lock"

#: 全新 venv 放**仓库外**（A01）。可用环境变量换位置（例如 CI 的临时盘）。
DEFAULT_VENV = Path(os.environ.get("AIFORSTUDY_A01_VENV", "/tmp/aiforstudy-a01-venv"))
MARKER_NAME = ".a01-provisioned.json"

#: 建 venv 用的解释器（A01 要求 3.12；`requirements.lock` 的 interpreter 是 >=3.12,<3.13）。
BASE_PYTHON = (os.environ.get("AIFORSTUDY_A01_BASE_PYTHON")
               or shutil.which("python3.12") or "/usr/bin/python3.12")

#: 安装/建环境可以很慢（实测一次全量下装 250MB+，约 4–6 分钟），给足超时。
PROVISION_TIMEOUT = 3600
LOCK_WAIT_SECONDS = 3600

EXCLUDED_PACKAGES: dict[str, str] = {
    "undergraduate-fitted-exam-system":
        "rebuildable:false（盘上 wheel 被裁剪掉 12 个审核模块）；仅供待退役 tools 使用；"
        "新生产路径只在 normalizer 里当**比对对象**出现（字符串常量），不被 import",
    "courseforge":
        "是 stem_runner 的上游名字；新生产路径不 import，只在 normalizer 里当比对对象",
    "exercise-authoring-system": "全仓库 0 处 import",
    "hbep-ug-stem-controller-v2":
        "审核能力已树内 vendored（blocks/audit/vendor/hbep_controller_v2，与归档 wheel 逐字节一致）",
}

WORKSPACE_BOOK = "complex-analysis-v2"


# ────────────────────────────────────────────────────────── 驱动器（在新 venv 里跑）

#: ⚠️ 用 `r'''...'''` 原样嵌：这份脚本文本会被写到 tmp 再用**新 venv 的解释器**执行。
_DRIVER_SOURCE = r'''
"""A01 验收驱动器：**在全新 venv 里、仓库外 cwd** 跑一遍。

它自己不是测试；测试把它写到 tmp 再用新 venv 的解释器执行。
所有结论都写成 JSON，便于跨"打乱导入顺序"的多次运行比对。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

#: CourseForgeV2 系：**故意不装、也不许被 import**（A01）。用 meta_path 挡掉。
BLOCKED_ROOTS = (
    "stem_runner",
    "undergraduate_fitted_exam_system",
    "courseforge",
    "courseforge_content",
    "exercise_authoring_system",
)
BLOCKED_DISTS = (
    "undergraduate-fitted-exam-system",
    "courseforge",
    "exercise-authoring-system",
    "hbep-ug-stem-controller-v2",
)
#: 不许出现在 sys.path 里的路径特征（旧 venv / CourseForgeV2 源码 / 旧 lib 垫片）。
FORBIDDEN_PATH_PARTS = ("CourseForgeV2", ".venv-linux", "AIforStudy/lib")

GUARD = """\
# A01 守卫：CLI 子进程启动时自动装载（放在 PYTHONPATH 里的 sitecustomize）。
import importlib.util
import json
import os
import sys

BLOCKED = ("stem_runner", "undergraduate_fitted_exam_system", "courseforge",
           "courseforge_content", "exercise_authoring_system")


class _Blocker:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ModuleNotFoundError(
                "A01 guard: %s 被封锁（CourseForgeV2 系不在生产路径上）" % name, name=name)
        return None


sys.meta_path.insert(0, _Blocker())

blocked_importable = []
for name in BLOCKED:
    try:
        if importlib.util.find_spec(name):
            blocked_importable.append(name)
    except Exception:
        pass

report = {
    "argv": sys.argv[1:],
    "prefix": sys.prefix,
    "executable": sys.executable,
    "sys_path": [p for p in sys.path if p],
    "blocked_importable": blocked_importable,
    "forbidden_in_sys_path": sorted(
        {p for p in sys.path for part in ("CourseForgeV2", ".venv-linux", "AIForStudy/lib")
         if part in p}),
}
target = os.environ.get("A01_GUARD_REPORT")
if target:
    with open(target, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(report, ensure_ascii=False) + "\\n")
"""


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--lock", required=True, type=Path)
    ap.add_argument("--seed", type=int, default=0)
    return ap.parse_args()


def install_blocker() -> None:
    class Blocker:
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in BLOCKED_ROOTS:
                raise ModuleNotFoundError(
                    f"A01 guard: {name} 被封锁（CourseForgeV2 系不在生产路径上）", name=name)
            return None

    sys.meta_path.insert(0, Blocker())


def forbidden_in_sys_path() -> list:
    return sorted({p for p in sys.path for part in FORBIDDEN_PATH_PARTS if part in p})


def shuffle_imports(seed: int) -> list:
    """按 seed 打乱导入顺序，再导入生产模块。顺序必须真的变、且可复现。"""
    stdlib_first = ["json", "hashlib", "pathlib", "dataclasses", "importlib", "tempfile",
                    "subprocess", "shutil", "typing", "re"]
    random.Random(seed).shuffle(stdlib_first)
    for name in stdlib_first:
        importlib.import_module(name)

    names = [
        "aiforstudy", "aiforstudy.cli", "aiforstudy.errors", "aiforstudy.contracts.request",
        "aiforstudy.core.workspace", "aiforstudy.core.registry", "aiforstudy.core.digest",
        "aiforstudy.pipeline.planner", "aiforstudy.providers.capabilities",
        "aiforstudy.providers.normalizer", "aiforstudy.providers.artifacts",
    ]
    order = list(names)
    random.Random(seed + 1).shuffle(order)
    for name in order:
        importlib.import_module(name)
    return order


def run_cli(argv, *, repo: Path, cwd: Path, guard_dir: Path, guard_report: Path,
            env_extra=None) -> dict:
    """跑一次 CLI：cwd **在仓库外**，工作区只从环境变量来。"""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONPATH"] = str(guard_dir)
    env["AIFORSTUDY_WORKSPACE"] = str(repo)
    env["A01_GUARD_REPORT"] = str(guard_report)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(argv, cwd=str(cwd), env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=900)
    out = proc.stdout.decode("utf-8", "replace")
    payload = None
    if "--json" in argv:
        try:
            payload = json.loads(out[out.index("{"):])
        except (ValueError, json.JSONDecodeError):
            payload = None
    return {"argv": argv, "returncode": proc.returncode, "stdout": out[:8000],
            "payload": payload}


def pins_report(lock_path: Path) -> dict:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    md = importlib.metadata
    installed, mismatch = {}, []
    for name, want in sorted((lock.get("version_only") or {}).items()):
        try:
            got = md.version(name)
        except md.PackageNotFoundError:
            got = None
        installed[name] = got
        if got != want:
            mismatch.append({"name": name, "want": want, "got": got})
    excluded = {}
    for name in BLOCKED_DISTS:
        try:
            excluded[name] = md.version(name)
        except md.PackageNotFoundError:
            excluded[name] = None
    return {"installed": installed, "mismatch": mismatch, "excluded_installed": excluded,
            "lock_pinned_by_hash": sorted(lock.get("pinned_by_hash") or {})}


def workspace_checks(repo: Path) -> dict:
    """用**装好的**包（不碰 repo/src/）解析工作区。"""
    import aiforstudy
    from aiforstudy.core.workspace import find_workspace, open_workspace

    ws = open_workspace(repo)
    return {"workspace_root": str(ws.root),
            "via_env": str(find_workspace(None)),
            "installed_at": str(Path(aiforstudy.__file__).resolve()),
            "sys_prefix": sys.prefix}


def normalizer_checks(repo: Path) -> dict:
    from aiforstudy.core.workspace import open_workspace
    from aiforstudy.errors import CapabilityError
    from aiforstudy.providers import normalizer
    from aiforstudy.providers.artifacts import SourceLock

    ws = open_workspace(repo)
    lock = SourceLock.load(repo / "vendor.lock.json")
    art, problems = normalizer.verify(ws, lock)
    require_error = ""
    try:
        normalizer.require_available(ws)
    except CapabilityError as exc:
        require_error = f"{type(exc).__name__}: {exc}"
    blocked = {}
    for name in BLOCKED_ROOTS:
        try:
            blocked[name] = bool(importlib.util.find_spec(name))
        except Exception:
            blocked[name] = False
    return {"dist_name": normalizer.DIST_NAME, "owner_module": normalizer.OWNER_MODULE,
            "upstream": normalizer.UPSTREAM,
            "artifact": {k: (str(v) if isinstance(v, Path) else v)
                         for k, v in art.as_dict().items()
                         if k in ("name", "version", "payload", "payload_files")},
            "problems": sorted(problems),
            "require_available_error": require_error,
            "blocked_importable": [n for n, ok in blocked.items() if ok],
            "courseforgev2_modules_loaded": sorted(
                {n.split(".")[0] for n in list(sys.modules)
                 if n.split(".")[0] in BLOCKED_ROOTS})}


AUDIT_SOURCE = """# 第一节 导数

定义：函数 f 在 x0 处的导数定义为极限 f'(x0) = lim_{h->0} (f(x0+h)-f(x0))/h。

定理：可导必连续。
"""

AUDIT_GOOD = AUDIT_SOURCE

AUDIT_BAD = """# 第一节 导数

定义：函数 f 在 x0 处的导数定义为 the slope of the tangent line。

定理：可导必连续。
"""


def auditor_checks(repo: Path, scratch: Path) -> dict:
    vendor = repo / "blocks" / "audit" / "vendor"
    if str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))
    from hbep_controller_v2.direct_document_audit import audit_document_files

    work = scratch / "audit"
    work.mkdir(parents=True, exist_ok=True)
    (work / "source.md").write_text(AUDIT_SOURCE, encoding="utf-8")
    (work / "analysis.md").write_text(AUDIT_GOOD, encoding="utf-8")
    (work / "analysis-bad.md").write_text(AUDIT_BAD, encoding="utf-8")

    def once(source: str, analysis: str, out: str) -> dict:
        result = audit_document_files(work / source, work / analysis, work / out)
        payload = result.as_dict()
        return {"schema": payload["schema"], "complete": payload["complete"],
                "source_sha256": payload["source_sha256"],
                "analysis_sha256": payload["analysis_sha256"],
                "program_sha256": payload["program_sha256"],
                "issue_codes": sorted({str(i.get("code")) for i in payload["issues"]}),
                "issues": len(payload["issues"])}

    clean = once("source.md", "analysis.md", "report-clean.json")
    planted = once("source.md", "analysis-bad.md", "report-bad.json")
    return {"import_path": str(Path(sys.modules["hbep_controller_v2"].__file__).resolve()),
            "clean": clean, "planted_defect": planted}


def backend_checks(repo: Path, scratch: Path) -> dict:
    from aiforstudy.core.workspace import open_workspace
    from aiforstudy.providers.capabilities import probe

    fig = repo / "blocks" / "figures"
    if str(fig) not in sys.path:
        sys.path.insert(0, str(fig))
    import library
    import spec as spec_mod
    import verify_svg
    from backends import render

    caps = probe(open_workspace(repo))
    enabled = caps.figure_backends()
    out = scratch / "figs"
    out.mkdir(parents=True, exist_ok=True)

    def verified(produced):
        verdict = verify_svg.verify(Path(produced["svg"]), Path(produced["geometry"]))
        return bool(verdict.get("ok")), sorted(verdict.get("problems") or [])

    results = {}
    for backend in enabled:
        candidates = [s for s in (spec_mod.LIBRARY + library.ALL_FIGURES)
                      if str(s.get("backend") or "vector") == backend]
        if backend == "vector":
            import vector_parallelogram as vp
            try:
                ok, problems = verified(vp.draw(out))
                results[backend] = {"ok": ok, "spec": "vector-parallelogram",
                                    "problems": problems}
            except Exception as exc:
                results[backend] = {"ok": False, "spec": "vector-parallelogram",
                                    "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
            continue
        if not candidates:
            results[backend] = {"ok": False, "spec": None, "error": "图库里没有该后端的规格"}
            continue
        chosen = candidates[0]
        try:
            ok, problems = verified(render(chosen, out))
            results[backend] = {"ok": ok, "spec": chosen["name"], "problems": problems}
        except Exception as exc:
            results[backend] = {"ok": False, "spec": chosen["name"],
                                "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    items = {c.name: {"available": c.available, "detail": c.detail} for c in caps.items
             if c.name.startswith("figure:")}
    return {"enabled": enabled, "results": results, "figure_items": items}


def main() -> int:
    args = parse_args()
    repo = args.repo.resolve()
    scratch = Path(tempfile.mkdtemp(prefix="a01-driver-"))
    guard_dir = scratch / "guard"
    guard_dir.mkdir(parents=True, exist_ok=True)
    (guard_dir / "sitecustomize.py").write_text(GUARD, encoding="utf-8")
    guard_report = scratch / "guard.jsonl"
    outer_cwd = Path.cwd()

    install_blocker()
    import_order = shuffle_imports(args.seed)

    report = {
        "seed": args.seed,
        "driver_cwd": str(outer_cwd),
        "driver_sys_prefix": sys.prefix,
        "driver_executable": sys.executable,
        "import_order": import_order,
        "forbidden_in_sys_path": forbidden_in_sys_path(),
        "python_version": sys.version.split()[0],
        "pins": pins_report(args.lock),
        "workspace": workspace_checks(repo),
    }

    venv_bin = Path(sys.executable).parent
    report["cli"] = {
        "aifs_help_console_script": run_cli([str(venv_bin / "aifs"), "--help"], repo=repo,
                                            cwd=outer_cwd, guard_dir=guard_dir,
                                            guard_report=guard_report),
        "module_help": run_cli([sys.executable, "-m", "aiforstudy", "--help"], repo=repo,
                               cwd=outer_cwd, guard_dir=guard_dir, guard_report=guard_report),
        "books": run_cli([sys.executable, "-m", "aiforstudy", "books", "--json"], repo=repo,
                         cwd=outer_cwd, guard_dir=guard_dir, guard_report=guard_report),
        "plan": run_cli([sys.executable, "-m", "aiforstudy", "plan",
                         "--book", "complex-analysis-v2", "--chapters", "CH02", "--json"],
                        repo=repo, cwd=outer_cwd, guard_dir=guard_dir,
                        guard_report=guard_report),
        "capabilities": run_cli([sys.executable, "-m", "aiforstudy", "capabilities", "--json"],
                                repo=repo, cwd=outer_cwd, guard_dir=guard_dir,
                                guard_report=guard_report),
        "sources": run_cli([sys.executable, "-m", "aiforstudy", "sources", "--json"],
                           repo=repo, cwd=outer_cwd, guard_dir=guard_dir,
                           guard_report=guard_report),
    }
    # 没有工作区时必须**明确失败**（退出码 2），不许从包位置猜（方案 §3 / A01）
    report["cli"]["no_workspace"] = run_cli(
        [sys.executable, "-m", "aiforstudy", "books"], repo=repo, cwd=outer_cwd,
        guard_dir=guard_dir, guard_report=guard_report,
        env_extra={"AIFORSTUDY_WORKSPACE": ""})
    # 隔离模式（`-I`：忽略 PYTHON* 环境变量与 user site）——证明包确实装在这个 venv 里。
    report["cli"]["isolated_books"] = run_cli(
        [sys.executable, "-I", "-m", "aiforstudy", "--workspace", str(repo), "books", "--json"],
        repo=repo, cwd=outer_cwd, guard_dir=guard_dir, guard_report=guard_report)
    probe = subprocess.run([sys.executable, "-I", "-c",
                            "import json, sys; print(json.dumps(sys.path))"],
                           cwd=str(outer_cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=120)
    try:
        report["isolated_sys_path"] = json.loads(probe.stdout.decode())
    except (ValueError, json.JSONDecodeError):
        report["isolated_sys_path"] = probe.stdout.decode()[:400]

    report["normalizer"] = normalizer_checks(repo)
    report["auditor"] = auditor_checks(repo, scratch)
    report["backends"] = backend_checks(repo, scratch)

    guard_lines = []
    if guard_report.is_file():
        guard_lines = [json.loads(line) for line in
                       guard_report.read_text(encoding="utf-8").splitlines() if line.strip()]
    report["guard"] = {
        "processes": len(guard_lines),
        "prefixes": sorted({g["prefix"] for g in guard_lines}),
        "forbidden_in_sys_path": sorted({p for g in guard_lines
                                         for p in g["forbidden_in_sys_path"]}),
        "blocked_importable": sorted({n for g in guard_lines for n in g["blocked_importable"]}),
    }

    caps_payload = report["cli"]["capabilities"]["payload"] or {}
    digest_source = {
        "workspace_root": report["workspace"]["workspace_root"],
        "via_env": report["workspace"]["via_env"],
        "plan_digest": (report["cli"]["plan"]["payload"] or {}).get("request_digest"),
        "plan_scope": (report["cli"]["plan"]["payload"] or {}).get("resolved_scope"),
        "plan_rc": report["cli"]["plan"]["returncode"],
        "books_rc": report["cli"]["books"]["returncode"],
        "isolated_books_rc": report["cli"]["isolated_books"]["returncode"],
        "isolated_forbidden": sorted(p for p in report["isolated_sys_path"]
                                     for part in FORBIDDEN_PATH_PARTS if part in p),
        "capabilities_figure_backends": caps_payload.get("figure_backends"),
        "normalizer": {"artifact": report["normalizer"]["artifact"],
                       "require_error": bool(report["normalizer"]["require_available_error"]),
                       "problems": report["normalizer"]["problems"]},
        "auditor": report["auditor"],
        "backends": report["backends"]["results"],
    }
    blob = json.dumps(digest_source, ensure_ascii=False, sort_keys=True)
    report["digest_source"] = digest_source
    report["digest"] = "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


# ────────────────────────────────────────────────────────── 搭环境（真建 venv，不 mock）

class ProvisionError(RuntimeError):
    """建环境失败 —— 明确失败，不许跳过当通过（方案 §15 A26）。"""


def _tail(text: str, lines: int = 25) -> str:
    return "\n".join(text.splitlines()[-lines:])


def _lock_digest(path: Path = LOCK) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _run_logged(cmd: list[str], log: list[str], *, env: dict | None = None,
                timeout: int = PROVISION_TIMEOUT,
                cwd: Path | None = None) -> subprocess.CompletedProcess:
    log.append("$ " + " ".join(str(c) for c in cmd))
    proc = subprocess.run([str(c) for c in cmd], stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, env=env, timeout=timeout,
                          cwd=str(cwd) if cwd else None)
    text = proc.stdout.decode("utf-8", "replace")
    log.append(text[-4000:])
    log.append(f"[退出码 {proc.returncode}]")
    return proc


def _pins_from_lock() -> tuple[list[str], list[str]]:
    """从锁里生成安装清单：`version_only` 的 49 个 pin，**不**含 `pinned_by_hash` 的 4 个。"""
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    version_only = lock.get("version_only") or {}
    excluded = sorted(lock.get("pinned_by_hash") or {})
    assert set(excluded) == set(EXCLUDED_PACKAGES), (
        "requirements.lock 的 pinned_by_hash 与测试里记的排除清单不一致："
        f"{excluded} vs {sorted(EXCLUDED_PACKAGES)}")
    assert not (set(version_only) & set(excluded)), "排除清单与按版本固定的清单不该相交"
    return [f"{name}=={ver}" for name, ver in sorted(version_only.items())], excluded


def _requirements_file(work: Path) -> Path:
    pins, excluded = _pins_from_lock()
    header = [
        "# A01：从 requirements.lock 的 version_only 生成的安装清单。",
        "# 明确排除（pinned_by_hash，不在新生产路径上）：" + "、".join(excluded),
        "# 理由见 tests/integration/test_m10_a01_fresh_env.py 的 EXCLUDED_PACKAGES 与",
        "# docs/M10-A01-验收记录.md。",
    ]
    path = work / "a01-install-reqs.txt"
    path.write_text("\n".join(header + pins) + "\n", encoding="utf-8")
    return path


def _bootstrap_pip(venv_python: Path, work: Path, log: list[str]) -> str:
    """给 `--without-pip` 建出来的 venv 装 pip。

    为什么 `--without-pip`：本机 `python3.12` 没有 `ensurepip`（Debian 的 python3-venv
    没装），现有的 `.venv-linux` 也是这么建的（见它的 `pyvenv.cfg`）。
    pip 本身**不在** `requirements.lock` 里 —— 它是引导工具，不是项目依赖；
    这里现下现装，并把版本与 sha256 记进 log。
    """
    wheels = work / "bootstrap"
    wheels.mkdir(parents=True, exist_ok=True)
    if not list(wheels.glob("pip-*.whl")):
        downloaders = [[sys.executable, "-m", "pip"]]
        if shutil.which("pip"):
            downloaders.append([shutil.which("pip"), ])
        ok = False
        for base in downloaders:
            proc = _run_logged([*base, "download", "pip", "--no-deps", "-d", str(wheels)], log)
            if proc.returncode == 0 and list(wheels.glob("pip-*.whl")):
                ok = True
                break
        if not ok:
            raise ProvisionError(
                "下不到 pip wheel（引导用）——需要的不是项目依赖，而是能联网的 pip。"
                "最近一次输出：\n" + _tail("\n".join(log)))
    wheel = sorted(wheels.glob("pip-*.whl"))[-1]
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    log.append(f"引导 pip wheel：{wheel.name} sha256:{digest}")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(wheel)
    proc = _run_logged([venv_python, "-m", "pip", "install", "--no-index", "--no-deps",
                        "--no-cache-dir", str(wheel)], log, env=env)
    if proc.returncode != 0:
        raise ProvisionError("把 pip 装进新 venv 失败：\n" + _tail("\n".join(log)))
    return wheel.name


def _installed_version(venv_python: Path, dist: str) -> str:
    proc = subprocess.run(
        [str(venv_python), "-c",
         "import importlib.metadata as m, sys\n"
         "try:\n    print(m.version(sys.argv[1]))\nexcept Exception:\n    print('')",
         dist],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    return proc.stdout.decode("utf-8", "replace").strip()


def _source_digest() -> str:
    """本仓库**被装进去的那部分**源码的摘要（pyproject + src/**/*.py）。

    为什么要它：venv 是**非 editable** 安装，源码一改，venv 里那份就旧了 ——
    旧的那份会让 A01 验的是"上一次的代码"。摘要不一致就只重装本仓库（几秒），
    不必重建整个 venv。
    """
    digest = hashlib.sha256()
    files = [ROOT / "pyproject.toml"] + sorted((ROOT / "src").rglob("*.py"))
    for path in files:
        if "__pycache__" in path.parts:
            continue
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()


def _install_project(venv_python: Path, work: Path) -> dict:
    """**先构建 wheel、再把 wheel 装进 venv**；返回 wheel 身份（名 + sha256）。

    ⚠️ T11 第 4 条要求"fresh_env 以**本地已构建 wheel**／依赖缓存执行"。
    原来是 `pip install --no-deps --no-build-isolation <源码树>` —— 那证明的是
    "**源码**能装"，而冻结与交付拿出去的是 **wheel**：构建期会拷贝资产、
    打包清单会决定哪些文件进包，**两者是不同的东西**（台账 §9.44 记为"已定位的差距"）。

    上一轮我第一次试做失败并回退（§9.45），原因是改动落在了**死代码**里
    （当时这个文件里 `_provision` 被定义了两次）。重复定义已清理，这次落在活路径上。

    两条刻意的加固，让"到底装进去的是什么"不再靠 pip 的一句话来判断：

    * 装 wheel 时带 `--force-reinstall --no-deps --no-index`：
      不加 `--force-reinstall` 时 pip 可能因为"同名同版本已存在"而**直接跳过安装**
      （上一轮就撞到这个提示），于是"我装了 wheel"这句话**不成立**；
      `--no-index` 保证依赖已按锁装好、这里不许上网。
    * 装完**立刻断言** `aiforstudy.__file__` 落在**这个 venv 里**：
      否则"以 wheel 执行"就只是文件系统上躺着的一个 whl 而已。
    """
    log: list[str] = []
    # ⚠️ **必须用干净环境跑构建/安装/自检**（2026-10-07 实测，这是上一轮"pip 说已装、
    #    site-packages 里却没有"的真正原因）：
    #    测试进程自己带着 `PYTHONPATH=src:tests/support`（那是跑 pytest 的基座），
    #    子进程默认继承它 —— 于是 `/tmp/aiforstudy-a01-venv/bin/python -m pip install …`
    #    **把源码树当成了已安装的 aiforstudy**：
    #        Found existing installation: aiforstudy 0.1.0
    #        Not uninstalling aiforstudy at …/AIforStudy/src, outside environment /tmp/…
    #    结果 wheel "装成功"了却根本没生效，`import aiforstudy` 仍然解析到源码树。
    #    把 `PYTHONPATH` 摘掉，这一步才真的在往那个 venv 里装东西。
    clean_env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    wheelhouse = work / "wheelhouse"
    wheelhouse.mkdir(parents=True, exist_ok=True)
    proc = _run_logged([venv_python, "setup.py",
                        "build", "--build-base", str(work / "wheel-build"),
                        "bdist_wheel", "-d", str(wheelhouse)], log, cwd=ROOT, env=clean_env)
    if proc.returncode != 0:
        raise ProvisionError("构建 wheel 失败（**不静默跳过**）：\n" + _tail("\n".join(log), 60))
    wheels = sorted(wheelhouse.glob("aiforstudy-*.whl"))
    if not wheels:
        raise ProvisionError(f"构建完却没有 wheel：{sorted(p.name for p in wheelhouse.iterdir())}")
    wheel = wheels[-1]
    identity = {"wheel_name": wheel.name,
                "wheel_sha256": "sha256:" + hashlib.sha256(wheel.read_bytes()).hexdigest()}
    log.append(f"[本项目 wheel] {identity['wheel_name']} {identity['wheel_sha256']}")

    proc = _run_logged([venv_python, "-m", "pip", "install",
                        "--force-reinstall", "--no-deps", "--no-index", str(wheel)],
                       log, env=clean_env)
    if proc.returncode != 0:
        raise ProvisionError("安装本项目的 wheel 失败：\n" + _tail("\n".join(log), 60))

    # 断言"装进去的确实是这个 venv 里的一份"，而不是源码树或系统环境
    probe = subprocess.run(
        [str(venv_python), "-c", "import aiforstudy, sys; print(aiforstudy.__file__)"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120, env=clean_env)
    installed_at = probe.stdout.decode("utf-8", "replace").strip().splitlines()[-1]
    log.append(f"[aiforstudy.__file__] {installed_at}")
    (work / "a01-install-project.log").write_text("\n".join(log), encoding="utf-8")
    if probe.returncode != 0 or str(venv_python.parent.parent) not in installed_at:
        raise ProvisionError(
            "装完 wheel 之后 `import aiforstudy` 没有解析到本 venv 里 —— "
            "那说明这份 venv 用的不是 wheel 里的那份（T11 第 4 条要的正是它）：\n"
            + installed_at)
    return identity


def _write_marker(venv: Path, lock_digest: str, source_digest: str, extra: dict) -> dict:
    _pins, excluded = _pins_from_lock()
    marker = {
        "lock_digest": lock_digest,
        "source_digest": source_digest,
        "lock_pins": len(_pins),
        "excluded": excluded,
        "base_python": BASE_PYTHON,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        **extra,
    }
    venv.mkdir(parents=True, exist_ok=True)
    (venv / MARKER_NAME).write_text(json.dumps(marker, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    return marker


def _read_marker(venv: Path) -> dict:
    path = venv / MARKER_NAME
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def _venv_usable(venv: Path) -> bool:
    return ((venv / "bin" / "aifs").is_file() and (venv / "bin" / "python").is_file()
            and (venv / "lib").is_dir())


def _provision(venv: Path, work: Path) -> None:
    """真建一个全新 venv 并按锁安装。"""
    work.mkdir(parents=True, exist_ok=True)
    log: list[str] = [f"base python: {BASE_PYTHON}",
                      f"venv: {venv}",
                      f"lock: {LOCK} {_lock_digest()}"]
    if venv.exists():
        shutil.rmtree(venv)
    proc = _run_logged([BASE_PYTHON, "-m", "venv", "--without-pip", str(venv)], log)
    if proc.returncode != 0:
        raise ProvisionError(
            f"建 venv 失败（{BASE_PYTHON}）：\n" + _tail("\n".join(log)) +
            "\n提示：本机 python3.12 的 ensurepip 缺失时，venv 只能用 --without-pip，"
            "pip 由 _bootstrap_pip() 现装 —— 这一步靠网络。")
    venv_python = venv / "bin" / "python"
    if not venv_python.is_file():
        raise ProvisionError("venv 里没有 bin/python：\n" + _tail("\n".join(log)))

    pip_wheel = _bootstrap_pip(venv_python, work, log)
    reqs = _requirements_file(work)
    proc = _run_logged([venv_python, "-m", "pip", "install", "-r", str(reqs)], log)
    if proc.returncode != 0:
        raise ProvisionError("按锁安装依赖失败（**不静默跳过**，逐条看下面的输出）：\n"
                             + _tail("\n".join(log), 60))
    project_identity = _install_project(venv_python, work)

    pins, _excluded = _pins_from_lock()
    bad = [line for line in pins
           if _installed_version(venv_python, line.split("==")[0]) != line.split("==")[1]]
    if bad:
        raise ProvisionError("装完对不上锁的 pin：" + json.dumps(bad, ensure_ascii=False))

    (work / "a01-provision.log").write_text("\n".join(log), encoding="utf-8")
    _write_marker(venv, _lock_digest(), _source_digest(),
                  {"pip_wheel": pip_wheel,
                   # 装进去的是**哪一份 wheel** —— T11 第 4 条要的证据
                   **project_identity,
                   "python_version": subprocess.run(
                       [venv_python, "-c", "import sys; print(sys.version.split()[0])"],
                       stdout=subprocess.PIPE).stdout.decode().strip()})


@pytest.fixture(scope="session")
def fresh_env(tmp_path_factory) -> dict:
    """全新 venv（仓库外、独立解释器、独立 site-packages）。

    复用规则写死在这里：**锁摘要 + 源码摘要都对得上才复用**。
    * 锁变了 → 整个 venv 重建（pin 集可能变了）；
    * 只有源码变了 → 只重装本仓库（几秒），因为 venv 是非 editable 安装；
    * 两个都没变 → 后续 pytest 直接复用（省几分钟）。
    """
    venv = DEFAULT_VENV
    work = tmp_path_factory.mktemp("a01-provision")
    lock_digest = _lock_digest()
    source_digest = _source_digest()

    def ready() -> bool:
        marker = _read_marker(venv)
        return (_venv_usable(venv) and marker.get("lock_digest") == lock_digest
                and marker.get("source_digest") == source_digest)

    lockfile = venv.parent / (venv.name + ".provision.lock")
    if not ready():
        acquired = False
        deadline = time.time() + LOCK_WAIT_SECONDS
        while time.time() < deadline:
            try:
                fd = os.open(lockfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()}".encode())
                os.close(fd)
                acquired = True
                break
            except FileExistsError:
                try:
                    stale = time.time() - lockfile.stat().st_mtime > LOCK_WAIT_SECONDS
                except OSError:
                    stale = False
                if stale:
                    lockfile.unlink(missing_ok=True)
                    continue
                if ready():                              # 别的进程装好了
                    break
                time.sleep(5)
        if not acquired and not ready():
            raise ProvisionError(f"等 {LOCK_WAIT_SECONDS}s 也没等到 {venv} 建好"
                                 f"（锁文件 {lockfile}）")
        if acquired:
            try:
                marker = _read_marker(venv)
                if (_venv_usable(venv) and marker.get("lock_digest") == lock_digest):
                    _install_project(venv / "bin" / "python", work)   # 只重装源码
                    _write_marker(venv, lock_digest, source_digest,
                                  {k: v for k, v in marker.items()
                                   if k in ("pip_wheel", "python_version", "base_python")})
                else:
                    _provision(venv, work)
            finally:
                lockfile.unlink(missing_ok=True)
    marker = _read_marker(venv)
    assert marker.get("lock_digest") == lock_digest
    assert marker.get("source_digest") == source_digest
    return {"venv": venv, "python": venv / "bin" / "python", "aifs": venv / "bin" / "aifs",
            "work": work, "marker": marker, "lock_digest": lock_digest,
            "source_digest": source_digest}


def _run_driver(fresh_env: dict, out: Path, *, seed: int, cwd: Path,
                env_extra: dict | None = None, clean_personal: bool = False) -> dict:
    """在新 venv 里、**仓库外 cwd** 跑一次驱动器。"""
    driver = fresh_env["work"] / "a01_driver.py"
    driver.write_text(_DRIVER_SOURCE, encoding="utf-8")
    env = dict(os.environ)
    env["AIFORSTUDY_WORKSPACE"] = str(ROOT)
    env["PYTHONHASHSEED"] = str(seed)
    env.pop("PYTHONPATH", None)
    if clean_personal:
        home = fresh_env["work"] / "empty-home"
        home.mkdir(parents=True, exist_ok=True)
        env["HOME"] = str(home)
        home_prefix = str(Path.home())
        env["PATH"] = os.pathsep.join(
            p for p in env.get("PATH", "").split(os.pathsep)
            if p and not p.startswith(home_prefix))
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run([str(fresh_env["python"]), str(driver), "--repo", str(ROOT),
                           "--out", str(out), "--lock", str(LOCK), "--seed", str(seed)],
                          cwd=str(cwd), env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=PROVISION_TIMEOUT)
    text = proc.stdout.decode("utf-8", "replace")
    if proc.returncode != 0 or not out.is_file():
        raise AssertionError(f"驱动器退出码 {proc.returncode}：\n{_tail(text, 60)}")
    return json.loads(out.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def a01_run(fresh_env, tmp_path_factory) -> dict:
    """基准运行（seed 0）：后续断言都读它，避免重复跑。"""
    out = tmp_path_factory.mktemp("a01-report") / "seed0.json"
    return _run_driver(fresh_env, out, seed=0, cwd=Path("/tmp"))


@pytest.fixture(scope="session")
def driver_runner(fresh_env, tmp_path_factory):
    reports = tmp_path_factory.mktemp("a01-reports")

    def run(name: str, *, seed: int = 0, clean_personal: bool = False,
            env_extra: dict | None = None) -> dict:
        return _run_driver(fresh_env, reports / f"{name}.json", seed=seed,
                           cwd=Path("/tmp"), clean_personal=clean_personal,
                           env_extra=env_extra)

    return run


# ────────────────────────────────────────────────────────── A01：安装集与排除集

def test_fresh_venv_installs_every_locked_pin(fresh_env, a01_run):
    """按锁装：`version_only` 的每一个 pin 都装着，且版本与锁一致。"""
    pins = a01_run["pins"]
    assert pins["mismatch"] == [], f"这些 pin 与锁不一致：{pins['mismatch']}"
    assert len(pins["installed"]) == len(json.loads(LOCK.read_text(encoding="utf-8"))["version_only"])
    assert all(v for v in pins["installed"].values())
    # 装的是**新 venv**里的包，不是旧 venv
    assert a01_run["driver_sys_prefix"] == str(fresh_env["venv"])
    assert str(fresh_env["venv"]) in a01_run["workspace"]["installed_at"]
    assert a01_run["python_version"].startswith("3.12"), a01_run["python_version"]


def test_courseforgev2_packages_are_excluded_with_a_recorded_reason(fresh_env, a01_run):
    """4 个 CourseForgeV2 系包**明确排除**，并逐个写了理由（A01 要求记录排除 + 理由）。"""
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    pinned = lock["pinned_by_hash"]
    assert set(pinned) == set(EXCLUDED_PACKAGES), "排除清单必须与锁里的 pinned_by_hash 一一对应"
    assert all(reason.strip() for reason in EXCLUDED_PACKAGES.values())
    # 一个都没装进新 venv
    assert a01_run["pins"]["excluded_installed"] == {name: None for name in EXCLUDED_PACKAGES}
    # 与"按版本号固定"的那 49 个不相交 —— 排除掉的本来就不在安装集里
    assert not (set(lock["version_only"]) & set(EXCLUDED_PACKAGES))
    # 其中被判为不可重建的那一个，理由要写明是 rebuildable:false
    assert pinned["undergraduate-fitted-exam-system"]["rebuildable"] is False
    assert "12" in pinned["undergraduate-fitted-exam-system"]["rebuildable_reason"]


# ────────────────────────────────────────────────────────── A01：仓库外 cwd 跑命令

def test_help_works_from_a_foreign_cwd_with_console_script_and_module_entry(a01_run):
    """A01：`aifs --help` 与 `python -m aiforstudy --help` 在仓库外 cwd 都能跑。"""
    for key in ("aifs_help_console_script", "module_help"):
        run = a01_run["cli"][key]
        assert run["returncode"] == 0, run["stdout"]
        assert "plan" in run["stdout"] and "books" in run["stdout"]
    assert Path(a01_run["driver_cwd"]) == Path("/tmp"), "驱动器必须在仓库外 cwd 跑"


def test_the_cli_has_no_check_subcommand_so_books_capabilities_sources_stand_in(a01_run):
    """A01 的原话里有 `check`；**当前 CLI 没有这个子命令**，用最接近的只读件代替。

    这条断言本身是证据：把"没有 check"写死，免得它被当成漏测。
    """
    help_text = a01_run["cli"]["module_help"]["stdout"]
    commands = set(re.findall(r"^\s{2,}([a-z][a-z0-9-]*)\s{2,}", help_text, re.M))
    assert {"books", "plan", "capabilities", "sources", "run"} <= commands, commands
    assert "check" not in commands, "将来真加了 check，这条要改成跑 check"
    for key in ("books", "plan", "capabilities"):
        run = a01_run["cli"][key]
        assert run["returncode"] == 0, f"{key}: {run['stdout']}"
    books = a01_run["cli"]["books"]["payload"]
    assert books["_meta"]["file_type"] == "books_view"
    assert WORKSPACE_BOOK in {b["book_id"] for b in books["books"]}
    assert books["workspace"] == str(ROOT)
    assert a01_run["cli"]["isolated_books"]["returncode"] == 0


def test_plan_has_a_stable_digest_and_scope_from_a_foreign_cwd(a01_run, tmp_path):
    """plan 零副作用、且从仓库外 cwd 给出一致的范围与请求指纹。"""
    from support.preparation_book import _mini_book

    # 真实书可能需要重新准备；范围解析用本次创建的有效目录验证，不能借旧缓存。
    plan = a01_run["cli"]["plan"]["payload"]
    assert plan["book_id"] == WORKSPACE_BOOK
    assert plan["request_digest"].startswith("sha256:")
    assert a01_run["workspace"]["workspace_root"] == str(ROOT)
    assert a01_run["workspace"]["via_env"] == str(ROOT), "工作区应从环境变量解析"

    workspace = _mini_book(tmp_path / "fixture")
    foreign_cwd = tmp_path / "outside"
    foreign_cwd.mkdir()

    def snapshot():
        return {str(p.relative_to(workspace.root)):
                (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                for p in workspace.root.rglob("*") if p.is_file()}

    before = snapshot()
    plans = []
    for seed in (0, 17):
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
        env.update(AIFORSTUDY_WORKSPACE=str(workspace.root), PYTHONHASHSEED=str(seed))
        proc = subprocess.run(
            [a01_run["driver_executable"], "-m", "aiforstudy", "plan",
             "--book", "mini-book", "--chapters", "CH01", "--json"],
            cwd=foreign_cwd, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=120)
        assert proc.returncode == 0, proc.stdout.decode("utf-8", "replace")
        plans.append(json.loads(proc.stdout))
    assert snapshot() == before, "plan 不得写入或触碰测试书文件"
    assert all(p["resolved_scope"] == ["CH01", "CH01-S01"] for p in plans)
    assert all(p["requires_preparation"] is False for p in plans)
    assert plans[0]["request_digest"].startswith("sha256:")
    assert plans[0]["request_digest"] == plans[1]["request_digest"]


def test_sources_verifies_the_vendored_auditor_against_the_lock(a01_run):
    """`aifs sources`（= normalizer + 审核器两条核对）：审核器 vendor 与归档 wheel 逐字节一致。

    退出码是 2 —— **不是这次安装坏了**，是因为我们**有意**不装 normalizer 那个 pin，
    它如实报"现场没有"（下一节的第 3 条测试专门盯这件事）。
    """
    run = a01_run["cli"]["sources"]
    assert run["returncode"] in (0, 2), run["stdout"]
    payload = run["payload"]
    vendor = next(a for a in payload["artifacts"] if a["name"] == "hbep_controller_v2")
    assert vendor["source_wheel"]["verdict"] == "verified-identical"
    assert vendor["source_wheel"]["sha256"].startswith("sha256:")
    assert any("undergraduate-fitted-exam-system" in p or "normalizer" in p
               for p in payload["problems"]), payload["problems"]


def test_no_workspace_and_no_env_var_fails_loudly_with_exit_2(a01_run):
    """没有工作区就**明确失败**（退出码 2），不许从包所在位置猜（方案 §3）。"""
    run = a01_run["cli"]["no_workspace"]
    assert run["returncode"] == 2, run["stdout"]
    assert "找不到工作区" in run["stdout"]


# ────────────────────────────────────────────────────────── A01：normalizer

def test_normalizer_runs_with_courseforgev2_packages_blocked(a01_run):
    """normalizer 在 4 个包被 `meta_path` 封死时照常工作，并**如实报告**现场没有那一份。

    它不静默退回别的实现：`require_available()` 明确抛 `CapabilityError`。
    """
    normalizer = a01_run["normalizer"]
    assert normalizer["dist_name"] == "undergraduate-fitted-exam-system"
    assert normalizer["owner_module"] == "stem_runner" and normalizer["upstream"] == "courseforge"
    assert normalizer["blocked_importable"] == [], "封锁没生效，测的就不是这条"
    assert normalizer["courseforgev2_modules_loaded"] == []
    assert normalizer["artifact"]["payload"] == ""      # 现场确实没有
    assert normalizer["require_available_error"].startswith("CapabilityError"), \
        normalizer["require_available_error"]
    assert normalizer["problems"], "现场与锁不一致时必须报问题，不许静默"
    assert a01_run["forbidden_in_sys_path"] == []


# ────────────────────────────────────────────────────────── A01：审核器

def test_vendored_auditor_runs_and_catches_a_planted_defect(a01_run):
    """树内 vendored 审核器：能跑、给出版本化报告、且**真的抓得住**人为埋的缺陷。"""
    auditor = a01_run["auditor"]
    assert auditor["import_path"].startswith(str(ROOT / "blocks" / "audit" / "vendor"))
    clean, planted = auditor["clean"], auditor["planted_defect"]
    assert clean["schema"] == "HBEP-DIRECT-DOCUMENT-AUDIT/2"
    assert clean["issues"] == 0 and clean["issue_codes"] == []
    assert planted["issues"] >= 1
    assert "STUDENT_VISIBLE_LANGUAGE_NOT_CHINESE" in planted["issue_codes"]
    # 同一份源文 → 同一份 program 哈希；两份产物内容不同 → 分析哈希必须不同
    assert clean["program_sha256"] == planted["program_sha256"]
    assert clean["source_sha256"] == planted["source_sha256"]
    assert clean["analysis_sha256"] != planted["analysis_sha256"]


# ────────────────────────────────────────────────────────── A01：启用后端夹具

def test_enabled_figure_backends_all_render_and_verify(a01_run):
    """启用的图后端逐个画一张图并当场校验 —— 用能力预检的结果当"启用"的定义。"""
    backends = a01_run["backends"]
    advertised = sorted(name.split(":", 1)[1] for name, item in backends["figure_items"].items()
                        if item["available"])
    assert backends["enabled"] == advertised
    assert {"vector", "plot", "schemdraw"} <= set(backends["enabled"])
    for backend, result in backends["results"].items():
        assert result["ok"], f"{backend}: {result}"
        assert result.get("problems") == []


def test_three_backend_capability_follows_the_declared_rule(a01_run):
    """A01：**不依赖个人 Blender 路径**。

    修之前：`capabilities.json` 只声明 `bin:node`，实现却写死
    `~/.local/opt/blender/blender` → `three` 被报成"依赖齐备"，一画就 `RuntimeError`。

    修之后两边**同一条规则**（`AIFORSTUDY_BLENDER` → PATH → 配置里声明的位置）：
    预检说可用就必须真能跑（本机：配置里那一行指向真实存在的 Blender，所以三条都过），
    预检说不可用就必须明确报错。**不依赖**这一条由"摘掉个人 HOME/PATH 后照样全绿"证明
    （见 `test_everything_still_works_with_no_personal_home_or_path`）。
    """
    items = a01_run["backends"]["figure_items"]
    if items["figure:three"]["available"]:
        assert "three" in a01_run["backends"]["enabled"]
        assert a01_run["backends"]["results"]["three"]["ok"], \
            a01_run["backends"]["results"]["three"]
    else:
        assert "three" not in a01_run["backends"]["enabled"]
        assert "blender" in items["figure:three"]["detail"]
    # 预检与后端**同一条规则**这件事在离线那条（不需要新 venv）里逐条比；
    # "找不到时必须两边一致地说不可用"由 no-personal 那条测试盯。


# ────────────────────────────────────────────────────────── A01：打乱导入顺序

def test_shuffled_module_import_order_changes_nothing(fresh_env, a01_run, driver_runner):
    """打乱导入顺序（多个 seed × 多个 PYTHONHASHSEED）→ 结论逐字节一致。"""
    digests = {"seed0": a01_run["digest"]}
    orders = {tuple(a01_run["import_order"])}
    for seed in (1, 2, 3, 7):
        report = driver_runner(f"seed{seed}", seed=seed)
        digests[f"seed{seed}"] = report["digest"]
        orders.add(tuple(report["import_order"]))
        assert report["cli"]["plan"]["payload"]["request_digest"] == \
            a01_run["cli"]["plan"]["payload"]["request_digest"]
        assert report["cli"]["plan"]["payload"]["resolved_scope"] == \
            a01_run["cli"]["plan"]["payload"]["resolved_scope"]
        assert report["workspace"]["workspace_root"] == a01_run["workspace"]["workspace_root"]
    assert len(orders) == 5, f"导入顺序没真的被打乱：{orders}"
    assert len(set(digests.values())) == 1, digests


# ────────────────────────────────────────────────────────── A01：不依赖个人路径

def test_everything_still_works_with_no_personal_home_or_path(fresh_env, a01_run, driver_runner):
    """HOME 换成空目录、PATH 摘掉 `$HOME` 下所有条目：核心结论不变，后端集合如实缩小。

    这一条就是 A01"**不依赖**个人 Blender 路径"的判据：那个路径没了，
    help/plan/normalizer/审核器/其余后端**一个都不少**，只有依赖它的那条后端
    被能力预检如实关掉。
    """
    report = driver_runner("no-personal-paths", seed=0, clean_personal=True)
    assert report["cli"]["plan"]["payload"]["request_digest"] == \
        a01_run["cli"]["plan"]["payload"]["request_digest"]
    assert report["workspace"]["workspace_root"] == a01_run["workspace"]["workspace_root"]
    assert report["normalizer"]["problems"] == a01_run["normalizer"]["problems"]
    assert report["auditor"] == a01_run["auditor"]
    assert report["cli"]["books"]["returncode"] == 0
    assert report["cli"]["isolated_books"]["returncode"] == 0
    # 干净的 PATH（无 $HOME 条目）里没有 blender/pdflatex → 那两条后端必须如实不可用
    clean_path = [p for p in (os.environ.get("PATH") or "").split(os.pathsep)
                  if p and not p.startswith(str(Path.home()))]
    clean_tools = {name for name in ("blender", "pdflatex")
                   if any((Path(p) / name).is_file() for p in clean_path)}
    for tool, backend in (("blender", "three"), ("pdflatex", "circuitikz")):
        if tool not in clean_tools:
            assert report["backends"]["figure_items"][f"figure:{backend}"]["available"] is False
            assert backend not in report["backends"]["enabled"]
    assert {"vector", "plot", "schemdraw"} <= set(report["backends"]["enabled"])
    for backend, result in report["backends"]["results"].items():
        assert result["ok"], f"{backend}: {result}"
    # 差异只允许出现在"能力驱动的后端集合"上
    changed = {k for k in a01_run["digest_source"]
               if a01_run["digest_source"][k] != report["digest_source"][k]}
    assert changed <= {"capabilities_figure_backends", "backends"}, changed
    # 而且这一跑确实比基准少了一条依赖个人路径的后端（否则这条测试什么也没证明）
    assert set(report["backends"]["enabled"]) <= set(a01_run["backends"]["enabled"])
    assert "three" not in report["backends"]["enabled"] or "blender" in clean_tools


# ────────────────────────────────────────────────────────── A01：不读旧 venv / CourseForgeV2

def _declared_render_env_roots() -> list[Path]:
    """允许名单**从产品自己的解析路径读**，绝不写死本机目录。

    用 `resolve_manim_interpreter()` —— 它就是生产代码用来定位渲染环境的那条路径
    （读 workspace 配置的 `render_environment.interpreter_candidates`，
    支持 `{env:...}`／`{workspace_parent}`／`{home}` 模板）。所以：
    * 配置怎么声明，这里就允许什么 —— 不把 `.venv-manim-linux` 烘进验收；
    * 本机没装渲染环境时它返回空串，于是这里也**不额外放行**任何东西。
    """
    from aiforstudy.providers.capabilities import resolve_manim_interpreter

    interpreter = resolve_manim_interpreter(ROOT)
    if not interpreter:
        return []
    # ⚠️ **不要 `.resolve()`**（这一行我踩过，见台账 §9.31）：
    #    venv 里的 `bin/python3` 是指向 `/usr/bin/python3` 的**绝对符号链接**，
    #    一旦 resolve，`parent.parent` 就从 venv 根变成了 `/usr` ——
    #    于是"配置声明的渲染环境"被算成 `/usr`，真正该放行的 venv 反被判成"未声明"。
    #    生产代码 `tools/manim_worker/policy.py:114` 用的正是**不带 resolve** 的写法，
    #    所以生产是对的，是我的辅助函数引入了这个 bug。
    return [Path(interpreter).parent.parent]


def test_no_old_venv_or_courseforgev2_path_is_ever_on_sys_path(fresh_env, a01_run):
    """旧 venv、CourseForgeV2 源码、旧 `lib/` 垫片：**任何进程的 sys.path 上都没有**。

    ⚠️ T11 迁移（2026-10-07）：原来断言"sys.path 上只允许基准 venv 与 `/usr/lib`"，
    并断言 `guard["prefixes"] == [基准 venv]`。T07 引入了**第二个、由配置声明**的环境
    （独立 Manim 渲染环境）之后，这个断言把**合法的**第二环境判成了"旧 venv 混进来"：
    能力预检会**故意**在那个环境里起子进程探测能力，守卫如实记下了它的前缀。

    迁移原则（**放行范围由配置决定，禁令一条不少**）：
    * 允许 = 基准 venv ＋ 由 `render_environment` 候选路径解析出来的渲染环境 ＋ 系统库；
    * **不许**把本机路径（如 `.venv-manim-linux`）写进本文件 —— 换台机器必红；
    * 旧 venv／CourseForgeV2／`lib/` 垫片的禁令、`blocked_importable` 的断言**原样保留**。
    """
    assert a01_run["forbidden_in_sys_path"] == []
    assert a01_run["isolated_sys_path"], "隔离模式的 sys.path 没取到"
    forbidden = [p for p in a01_run["isolated_sys_path"]
                 for part in ("CourseForgeV2", ".venv-linux", "AIforStudy/lib") if part in p]
    assert forbidden == []

    declared_roots = _declared_render_env_roots()

    def _allowed(entry: str) -> bool:
        if not entry:
            return True
        if str(fresh_env["venv"]) in entry or entry.startswith(("/usr/lib", "/usr/local/lib")):
            return True
        resolved = Path(entry).resolve()
        return any(resolved.is_relative_to(root) for root in declared_roots)

    assert all(_allowed(entry) for entry in a01_run["isolated_sys_path"]), (
        f"sys.path 上出现了既不是基准 venv、也不是配置声明的渲染环境、也不是系统库的条目："
        f"{[e for e in a01_run['isolated_sys_path'] if not _allowed(e)]}"
        f"（配置声明的渲染环境根：{[str(r) for r in declared_roots]}）")

    guard = a01_run["guard"]
    assert guard["processes"] >= 6, "CLI 子进程没挂上守卫，等于没验"
    observed = set(guard["prefixes"])
    assert str(fresh_env["venv"]) in observed, (
        f"基准 venv 没出现在被守卫进程的前缀里：{sorted(observed)}")
    allowed_prefixes = {str(fresh_env["venv"])} | {str(root) for root in declared_roots}
    assert observed <= allowed_prefixes, (
        f"被守卫进程里出现了**未声明**的环境：{sorted(observed - allowed_prefixes)}；"
        f"允许的只有基准 venv 与配置声明的渲染环境 {sorted(allowed_prefixes)}")
    assert guard["forbidden_in_sys_path"] == [] and guard["blocked_importable"] == []


# ────────────────────────────────────────────────────────── 离线可跑的静态判据（不需要 venv）

def _scan_module():
    """复用仓库自己的"只认代码行"扫描器（`tools/migrations/_scan.py`）。"""
    path = ROOT / "tools" / "migrations" / "_scan.py"
    spec = importlib.util.spec_from_file_location("_a01_scan", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: `Path.home()`／`expanduser("~")` **只允许**出现在这两类位置，每条都要写清理由。
#: 为什么不能一刀禁掉：`~/.bashrc` 是**隔离探针的对照目标**（"白名单之外的真实文件"，
#: 用来证明沙箱读不到它），而 `{home}` 是 workspace 配置里的模板变量。
#: 真正要禁的是**用 home 去定位依赖**（见
#: `test_three_backend_declares_blender_as_a_capability_and_hardcodes_no_home_path`
#: 记录的历史 bug）以及**写死的绝对个人路径**（下面单独判）。
_ALLOWED_HOME_USE = {
    ("providers/capabilities.py", "_expand_candidate"):
        "展开 workspace 配置里的 {home} 模板变量（配置驱动，不是代码写死）",
    ("providers/capabilities.py", "_probe_sandbox"):
        "隔离探针的对照目标：证明沙箱读不到白名单外的真实文件（只记可读性，见下一条测试）",
    ("blocks/visual/manim_adapter.py", "render_scene"):
        "环境指纹里的隔离探针目标（同上，只记可读性）",
    ("blocks/media/code_runtime.py", "render_generated_code"):
        "开放代码 worker 的隔离探针对照目标；只传给 probe_isolation(probe_paths=...)，不用于定位依赖或读取内容",
}


def test_production_path_never_imports_courseforgev2_and_has_no_personal_paths():
    """静态判据：新生产代码里没有 CourseForgeV2 的**真实导入**，也没有个人路径。

    与 A01 那份"封锁 + 跑通"的实测互为补充：实测证明不依赖，静态扫描证明不会偷偷依赖。
    """
    scan = _scan_module()
    src = ROOT / "src" / "aiforstudy"
    import_patterns = [re.compile(r"^\s*(?:import|from)\s+stem_runner\b"),
                       re.compile(r"^\s*(?:import|from)\s+courseforge"),
                       re.compile(r"^\s*(?:import|from)\s+exercise_authoring_system\b"),
                       re.compile(r"^\s*(?:import|from)\s+undergraduate_fitted_exam_system\b")]
    hits = []
    personal = []
    hardcoded = []
    # 写死的**绝对个人路径**：这才是真正要禁的东西（把某个人的家目录烘进产品）。
    absolute_home = re.compile(r"(?:/home/|/Users/)[A-Za-z0-9._-]+/|[A-Za-z]:\\\\Users\\\\")
    for path in sorted(src.rglob("*.py")):
        rel = str(path.relative_to(ROOT))
        enclosing = _enclosing_functions(path)
        for line_no, line in scan.code_lines(path):
            for pattern in import_patterns:
                if pattern.search(line):
                    hits.append(f"{rel}:{line_no}:{line.strip()}")
            if absolute_home.search(line):
                hardcoded.append(f"{rel}:{line_no}:{line.strip()}")
            if "Path.home()" in line or "expanduser(\"~\")" in line:
                func = enclosing.get(line_no, "")
                key = (rel.split("aiforstudy/", 1)[-1], func)
                if key in _ALLOWED_HOME_USE:
                    continue          # 已登记的两类正当用法，理由见常量说明
                personal.append(f"{rel}:{line_no}（在 {func}() 里）:{line.strip()}")
    assert hits == [], f"新生产路径里出现了 CourseForgeV2 的真实导入：{hits}"
    assert hardcoded == [], (
        f"新生产路径里出现了**写死的绝对个人路径**（换台机器就失效、还泄露开发者身份）：{hardcoded}")
    assert personal == [], (
        f"新生产路径里出现了未登记的个人路径用法：{personal}\n"
        f"`Path.home()`/`expanduser(\"~\")` 只允许两类用法（见 `_ALLOWED_HOME_USE`）："
        f"①展开 workspace 配置里的 `{{home}}` 变量；②隔离探针的对照目标。"
        f"如果你想用它**定位依赖**（下一条测试记录的那个历史 bug：把 "
        f"`~/.local/opt/blender/blender` 写死成兜底路径），那就应当走配置声明。")


def test_isolation_probe_records_no_content_and_no_content_hash():
    """隔离探针只许记「能不能读」，**不许记内容或内容哈希**。

    这是把 T07 的约定（"用临时假凭据哨兵证明隔离，**不读取真实秘密**"）变成可测判据。
    探针会拿 `~/.bashrc` 这类**真实文件**当对照目标来证明沙箱读不到它 —— 那本身是正当的；
    但如果它把读到的字节或内容哈希记进报告，那就等于**把用户的家目录内容带进了产物**。
    实测字段（`tools/manim_worker/run_scene.py::_read_probe`）只允许这几种：
    路径、可读性、errno、错误文本、**字节数**。
    """
    import ast as _ast

    probe = ROOT / "tools" / "manim_worker" / "run_scene.py"
    tree = _ast.parse(probe.read_text(encoding="utf-8"))
    fn = next((n for n in _ast.walk(tree)
               if isinstance(n, _ast.FunctionDef) and n.name == "_read_probe"), None)
    assert fn is not None, "找不到 _read_probe()：本判据要有对象，否则是空转"
    allowed = {"path", "readable", "errno", "error", "bytes"}
    recorded: set[str] = set()
    hashed = False
    for node in _ast.walk(fn):
        if isinstance(node, _ast.Dict):
            for key in node.keys:
                if isinstance(key, _ast.Constant) and isinstance(key.value, str):
                    recorded.add(key.value)
        if isinstance(node, _ast.Call):
            name = getattr(node.func, "attr", "") or getattr(node.func, "id", "")
            if name in ("sha256", "md5", "hexdigest", "digest"):
                hashed = True
            if name == "read_text":
                raise AssertionError(
                    "_read_probe 用了 read_text()：探针只该按字节判断可读性，不该读文本内容")
    assert not hashed, "隔离探针里出现了内容哈希：那会把用户文件的内容指纹写进产物"
    extra = recorded - allowed
    assert not extra, f"隔离探针记录了额外字段 {sorted(extra)}，只允许 {sorted(allowed)}"


def test_generated_media_home_lookup_is_only_an_isolation_probe_target():
    """新代码入口的登记例外只允许作为沙箱探针输入，不能扩散为依赖路径。"""
    import ast as _ast

    path = ROOT / "src" / "aiforstudy" / "blocks" / "media" / "code_runtime.py"
    tree = _ast.parse(path.read_text(encoding="utf-8"))
    fn = next(node for node in tree.body
              if isinstance(node, _ast.FunctionDef) and node.name == "render_generated_code")
    home_calls = [node for node in _ast.walk(fn)
                  if isinstance(node, _ast.Call) and isinstance(node.func, _ast.Attribute)
                  and isinstance(node.func.value, _ast.Name)
                  and node.func.value.id == "Path" and node.func.attr == "home"]
    assert len(home_calls) == 1
    probe_inputs = [keyword.value for node in _ast.walk(fn)
                    if isinstance(node, _ast.Call) and isinstance(node.func, _ast.Name)
                    and node.func.id == "probe_isolation"
                    for keyword in node.keywords if keyword.arg == "probe_paths"]
    assert len(probe_inputs) == 1
    assert home_calls[0] in set(_ast.walk(probe_inputs[0]))


def _enclosing_functions(path) -> dict:
    """行号 → 所在函数名。用于判定 `Path.home()` 出现在哪个函数里。"""
    import ast as _ast

    mapping: dict[int, str] = {}
    try:
        tree = _ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return mapping
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            for line in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                mapping.setdefault(line, node.name)
    return mapping


def test_normalizer_mentions_courseforgev2_only_as_comparison_strings():
    """normalizer 里那两行是**文字**（用来定位并和锁比对），不是 import。"""
    normalizer = ROOT / "src" / "aiforstudy" / "providers" / "normalizer.py"
    text = normalizer.read_text(encoding="utf-8")
    assert 'OWNER_MODULE = "stem_runner"' in text
    assert 'UPSTREAM = "courseforge"' in text
    scan = _scan_module()
    code = [line for _, line in scan.code_lines(normalizer)]
    assert not [line for line in code if re.search(r"\bimport\b.*\b(stem_runner|courseforge)\b", line)]


def test_three_backend_declares_blender_as_a_capability_and_hardcodes_no_home_path():
    """这个 bug 的回归锁：`three` 必须**声明**它真正要的东西，且代码里不许写死个人路径。

    2026-10-06 实测的形态是"能力被**高报**"：配置只写 `bin:node`，实现却写死
    `~/.local/opt/blender/blender` → 后端被报成可用、一画就炸。现在：
    Blender 是**声明式能力**（PATH + `binaries.blender.fallback_paths`），
    后端与预检调同一个 `binary_fallback_paths()`，代码里一个个人路径都没有。
    """
    config = json.loads((ROOT / "config" / "capabilities.json").read_text(encoding="utf-8"))
    binary = config["binaries"]["blender"]
    assert binary["probe"][0] == "blender"
    assert "bin:blender" in config["figure_backends"]["three"]["requires"]
    assert "bin:node" in config["figure_backends"]["three"]["requires"]
    # 如果声明了兜底位置，那它必须是配置里的**数据**（可删/可覆盖），不是代码里的常量
    for candidate in binary.get("fallback_paths") or []:
        assert candidate.startswith("~/"), candidate

    three = ROOT / "blocks" / "figures" / "backends" / "three.py"
    scan = _scan_module()
    code_lines = [line for _, line in scan.code_lines(three)]
    assert not [line for line in code_lines if "Path.home()" in line], \
        "3D 后端又不许在代码里写死个人 Blender 路径了"
    assert not [line for line in code_lines if ".local/opt/blender" in line], \
        "兜底位置只能来自配置，不能是代码里的常量"
    assert [line for line in code_lines if "binary_fallback_paths(" in line], \
        "兜底位置必须走能力预检那份口径（只有一份）"
    assert [line for line in code_lines if "AIFORSTUDY_BLENDER" in line], \
        "要能用环境变量显式指定"
    assert [line for line in code_lines if "blender_path()" in line], \
        "draw() 必须走 blender_path()（与能力预检同一条规则）"


def test_capability_probe_and_three_backend_agree_on_blender(monkeypatch, tmp_path):
    """能力预检与后端实现**同一条规则**：预检说可用 ⇔ `blender_path()` 找得到。

    三次比对：现状 → 显式环境变量指定 → PATH 摘空 + HOME 换空。
    """
    from aiforstudy.core.workspace import open_workspace
    from aiforstudy.providers.capabilities import env_override_name, probe

    monkeypatch.setenv("AIFORSTUDY_WORKSPACE", str(ROOT))
    assert env_override_name("blender") == "AIFORSTUDY_BLENDER", \
        "环境变量名的口径变了，three.py 里那个常量要跟着改"
    sys.path.insert(0, str(ROOT / "blocks" / "figures"))
    try:
        from backends.three import blender_path
        caps = probe(open_workspace(ROOT))
        assert caps.by_name()["figure:three"].available == (blender_path() is not None)

        # ① 显式指定：两边都必须认
        stub = tmp_path / "blender"
        stub.write_text("#!/bin/sh\necho 'Blender 5.2.2 (stub)'\n", encoding="utf-8")
        stub.chmod(0o755)
        monkeypatch.setenv("AIFORSTUDY_BLENDER", str(stub))
        assert blender_path() == stub
        assert probe(open_workspace(ROOT)).by_name()["bin:blender"].available is True

        # ② PATH 摘空、HOME 换空、显式变量也去掉：两边都说不可以
        monkeypatch.delenv("AIFORSTUDY_BLENDER", raising=False)
        monkeypatch.setenv("HOME", str(tmp_empty_home()))
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        assert blender_path() is None
        caps_empty = probe(open_workspace(ROOT))
        assert caps_empty.by_name()["figure:three"].available is False
        assert caps_empty.by_name()["bin:blender"].available is False
    finally:
        sys.path.remove(str(ROOT / "blocks" / "figures"))


def tmp_empty_home() -> Path:
    """一个空的"家目录"（给上面那条测试用；不写进仓库）。"""
    import tempfile
    return Path(tempfile.mkdtemp(prefix="a01-empty-home-"))
