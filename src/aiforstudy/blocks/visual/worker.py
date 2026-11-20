"""宿主侧 worker：隔离执行、超时／资源上限、TERM→KILL，以及"取消不提交成功缓存"。

计划 §6.1 的每条上限都在这里落地，而且**每条都有可观察的结果字段**，不是"设置了就算"：

| 要求 | 做法 | 结果字段 |
|---|---|---|
| draft 120s／final 300s | 宿主侧 deadline（还受 `parent_deadline` 约束） | `terminated_by="timeout"`、`wall_seconds` |
| 先 TERM 再 KILL | `killpg(TERM)` → 宽限 → `killpg(KILL)` → 等进程真的消失 | `terminated_by`、`orphans_remaining` |
| 输出上限 128MiB | 轮询临时目录与输出目录总字节数，超了立刻终止 | `terminated_by="output_limit"`、`job_bytes` |
| 内存 2GiB | Linux 子进程 `RLIMIT_AS` + 两平台宿主轮询整个进程组 RSS | `peak_rss_bytes`、`terminated_by="memory_limit"` |
| worker 并发数 | 进程内共享条件闸门，执行策略在派发前设置容量 | `concurrency_observed` |
| 取消不提交成功缓存 | 缓存提交只发生在 `status=="ok"` 且未取消时 | `cache_committed`、`cache_key` |
| 缺隔离不偷偷执行 | 没有可用隔离后端 → `blocked`，绝不无隔离跑模型输入 | `isolation.backend`、`isolation.safe` |

宿主与沙箱的分工：宿主写 `job.json`（含白名单、上限、最小环境、计划），
沙箱入口 `run_scene.py` 施加 rlimits／Landlock／seccomp 后执行，
结果写到 `worker_result.json`（先 `.tmp` 再 rename）—— 半截 JSON 不会被当成成功。
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import stat
import sys
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ...core.digest import digest_bytes, digest_file, digest_of
from ...errors import Cancelled, CapabilityError, ConfigError

#: 每个 worker 的上限（与 `tools/manim_worker/policy.py` **同一份数字**的来源在那边；
#: 这里默认值只是兜底，实际由 `RenderLimits` 传入）
DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_MEMORY_BYTES = 2 * 1024 ** 3
DEFAULT_OUTPUT_BYTES = 128 * 1024 ** 2
DEFAULT_CPU_CORES = 2
MONITOR_INTERVAL_SECONDS = 0.1
TERM_GRACE_SECONDS = 5.0


@dataclass(frozen=True)
class RenderLimits:
    """§5.3 的 `RenderLimits`：超时／CPU／内存／输出／父 deadline。"""

    timeout_seconds: float
    cpu_cores: int = DEFAULT_CPU_CORES
    memory_bytes: int = DEFAULT_MEMORY_BYTES
    output_bytes: int = DEFAULT_OUTPUT_BYTES
    parent_deadline: float | None = None

    def effective_timeout(self, now: float | None = None) -> float:
        """取"本档超时"与"父级剩余"里更小的那个（§6.1：均取父级剩余更小者）。"""
        limit = float(self.timeout_seconds)
        if self.parent_deadline is not None:
            remaining = float(self.parent_deadline) - (time.monotonic() if now is None else now)
            limit = min(limit, remaining)
        return limit


@dataclass(frozen=True)
class WorkerIsolation:
    """隔离后端的选择结果（可探测、可审计）。"""

    backend: str
    detail: str
    available: bool

    def as_dict(self) -> dict:
        return {"backend": self.backend, "detail": self.detail, "available": self.available}


@dataclass
class WorkerServices:
    """宿主侧依赖：worker 资产目录、解释器、临时根、ffmpeg/ffprobe、可选替身。"""

    worker_dir: Path
    interpreter: str
    scratch_root: Path
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    isolation: str | None = None
    subprocess_runner: object = None
    host_env: dict = field(default_factory=dict)

    def entry(self) -> Path:
        return Path(self.worker_dir) / "run_scene.py"


# ── worker 资产加载（宿主侧复用它做校验／生成 bwrap 参数）────────────────────
_ASSET_CACHE: dict[str, object] = {}
_ASSET_LOCK = threading.Lock()


def load_asset(worker_dir: str | Path, name: str):
    """按**显式目录**加载 worker 资产（不做 `__file__.parents[N]` 猜路径）。

    `tools/manim_worker/` 是仓库里的目录，也随 wheel 安装到
    `aiforstudy/_legacy_tree/tools/manim_worker`（`setup.py` 与 MANIFEST 已声明）。
    """
    import importlib.util
    import sys

    path = Path(worker_dir) / f"{name}.py"
    if not path.is_file():
        raise ConfigError(f"缺 worker 资产 {path}：请传 --manim-worker 或设 "
                          f"AIFORSTUDY_MANIM_WORKER")
    key = str(path.resolve())
    with _ASSET_LOCK:
        if key in _ASSET_CACHE:
            return _ASSET_CACHE[key]
        spec = importlib.util.spec_from_file_location(f"aiforstudy_manim_worker_{name}", path)
        if spec is None or spec.loader is None:                  # pragma: no cover
            raise ConfigError(f"无法加载 worker 资产 {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _ASSET_CACHE[key] = module
        return module


def resolve_worker_dir(explicit: str | Path | None = None,
                       workspace_root: str | Path | None = None) -> Path:
    """按 **显式参数 → 环境变量 → 工作区相对路径 → 包内安装位置** 找 worker 资产。

    顺序写死在这里，只有这一处；找不到就报错，不猜。
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get("AIFORSTUDY_MANIM_WORKER", "")
    if env:
        candidates.append(Path(env))
    if workspace_root:
        candidates.append(Path(workspace_root) / "tools" / "manim_worker")
    candidates.append(Path(__file__).resolve().parents[4] / "tools" / "manim_worker")
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if (path / "run_scene.py").is_file():
            return path.resolve()
    installed = Path(__file__).resolve().parents[2] / "_legacy_tree" / "tools" / "manim_worker"
    if (installed / "run_scene.py").is_file():
        return installed
    raise ConfigError(
        "找不到 manim worker 资产（tools/manim_worker/run_scene.py）。三种给法：\n"
        "  1) 显式传 worker_dir\n"
        "  2) 环境变量 AIFORSTUDY_MANIM_WORKER=<目录>\n"
        "  3) 传入 workspace_root（默认用 <workspace>/tools/manim_worker）")


# ── 隔离后端 ───────────────────────────────────────────────────────────────
def select_isolation(worker_dir: str | Path, *, forced: str | None = None) -> WorkerIsolation:
    """macOS 使用 Seatbelt；Linux 优先 bwrap，其次 Landlock+seccomp。

    bwrap 不可用时**不是**"直接无隔离执行"：Landlock 后端会真的拒读宿主路径、拒发网络，
    并由 `probe_isolation()` 实测。两者都不可用 → `blocked`（`Capabilities.require`）。
    """
    if forced == "none":
        return WorkerIsolation(backend="none", detail="调用方显式关闭隔离（仅测试用）",
                               available=False)
    if sys.platform == "darwin" and forced in (None, "seatbelt"):
        report = load_asset(worker_dir, "seatbelt").selftest()
        return WorkerIsolation(backend="seatbelt", detail=report["detail"],
                               available=bool(report["available"]))
    if sys.platform == "darwin" and forced in ("bwrap", "landlock-seccomp"):
        return WorkerIsolation(backend=forced, detail=f"{forced} requires Linux", available=False)
    if forced == "bwrap":
        report = load_asset(worker_dir, "bwrap").selftest()
        return WorkerIsolation(backend="bwrap", detail=report["detail"],
                               available=bool(report["available"]))
    if forced == "landlock-seccomp":
        return WorkerIsolation(backend="landlock-seccomp",
                               detail="调用方指定 Landlock+seccomp", available=True)
    report = load_asset(worker_dir, "bwrap").selftest()
    if report["available"]:
        return WorkerIsolation(backend="bwrap", detail=report["detail"], available=True)
    sandbox = load_asset(worker_dir, "sandbox")
    abi = sandbox.landlock_abi()
    if abi >= 1:
        return WorkerIsolation(
            backend="landlock-seccomp",
            detail=(f"bwrap 不可用（{report['detail']}）；改用 Landlock ABI {abi}"
                    f" + seccomp + rlimits（非特权，同样默认拒绝）"),
            available=True)
    return WorkerIsolation(backend="none",
                           detail=f"bwrap 不可用（{report['detail']}），Landlock ABI {abi}",
                           available=False)


def isolation_verified(isolation: dict) -> bool:
    """Accept only a real successful probe with the expected OS policy receipt."""
    applied = isolation.get("applied") or {}
    linux = bool((applied.get("landlock") or {}).get("backend"))
    mac = applied.get("seatbelt") or {}
    seatbelt = (mac.get("backend") == "seatbelt" and mac.get("signal_scope") == "same-sandbox"
                and {"setpgid", "setsid", "posix_spawn"} <= set(mac.get("denied_syscalls") or []))
    return bool(isolation.get("safe") and (linux or seatbelt))


def _sentinel_dir(scratch_root: Path) -> Path:
    """哨兵目录：**故意**放在白名单之外（只有 job 的 scratch／output 被授权）。"""
    return Path(scratch_root) / "sentinel"


def write_sentinel(scratch_root: Path) -> Path:
    """写一个**临时假凭据**文件（不是真密钥）。`probe_isolation` 用它证明读不到。"""
    directory = _sentinel_dir(scratch_root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "fake-provider.env"
    path.write_text("DEEPSEEK_API_KEY=FAKE-SENTINEL-NOT-A-REAL-SECRET\n"
                    "note=这是隔离探针的假凭据；真实密钥从不被读取\n", encoding="utf-8")
    return path


# ── 运行一个 job ───────────────────────────────────────────────────────────
_ACTIVE_LOCK = threading.Lock()
_ACTIVE_WORKERS = 0
_MAX_OBSERVED_CONCURRENCY = 0
# All probes and renders share this gate, including calls with distinct services.
# Direct callers retain the serial default; Runner applies its frozen policy.
_WORKER_GATE = threading.Condition(_ACTIVE_LOCK)
_WORKER_CAPACITY = 1


def configure_worker_concurrency(limit: int) -> None:
    """Set process-wide capacity without replacing the gate or killing workers.

    Lowering the limit only prevents new admissions until enough jobs finish.
    """
    global _WORKER_CAPACITY
    if type(limit) is not int or limit < 1:
        raise ConfigError("render.max_concurrent_workers must be a positive integer")
    with _WORKER_GATE:
        _WORKER_CAPACITY = limit
        _WORKER_GATE.notify_all()


def max_observed_concurrency() -> int:
    """本次进程里同时活着的 worker 历史峰值。"""
    return _MAX_OBSERVED_CONCURRENCY


def monitor_decision(*, elapsed: float, rss: int, output: int, cancelled: bool,
                     limits: RenderLimits) -> str:
    """监视判据：返回终止原因，空串表示继续跑。

    抽成纯函数是为了让"到点会被终止"这件事**可单测**：真渲染只能证明其中一条路径，
    四条判据（超时／输出／内存／取消）的判断逻辑本身不该只能靠跑一遍才知道对错。
    """
    if cancelled:
        return "cancelled"
    if elapsed > limits.timeout_seconds or (
            limits.parent_deadline is not None and time.monotonic() >= limits.parent_deadline):
        return "timeout"
    if output > limits.output_bytes:
        return "output_limit"
    if rss > limits.memory_bytes:
        return "memory_limit"
    return ""


def _dir_bytes(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            info = item.lstat()
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
        except OSError:                                          # pragma: no cover
            continue
    return total


def _job_bytes(scratch: Path, output: Path) -> int:
    """Count every job-writable regular file, without following worker symlinks."""
    roots = list(dict.fromkeys((scratch.resolve(), output.resolve())))
    return sum(_dir_bytes(root) for root in roots
               if not any(root != parent and root.is_relative_to(parent) for parent in roots))


def _rss_bytes(pid: int) -> int:
    if sys.platform == "darwin":
        return next((row[2] for row in _darwin_processes() if row[0] == pid), 0)
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return 0
    return 0


def _group_members(pgid: int) -> list[int]:
    """Host-side census includes descendants after the leader has exited.

    Zombies cannot execute or retain memory and must be reaped by their parent;
    they are not live worker processes. Generated jobs cannot change groups.
    """
    if sys.platform == "darwin":
        return [pid for pid, group, rss in _darwin_processes() if group == pgid]
    members = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) == pgid and fields[0] not in {"Z", "X"}:
                members.append(int(entry.name))
        except (OSError, ValueError, IndexError):
            continue
    return members


def _darwin_processes() -> list[tuple[int, int, int]]:
    # Read-only census; ownership is exclusively the host-created process group.
    # Seatbelt forbids setsid/setpgid and the posix_spawn group/session bypass.
    proc = subprocess.run(["/bin/ps", "-axo", "pid=,pgid=,rss=,stat="],
                          capture_output=True, text=True, timeout=5, check=True)
    rows = []
    for line in proc.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 4 and not fields[3].startswith(("Z", "X")):
            rows.append((int(fields[0]), int(fields[1]), int(fields[2]) * 1024))
    return rows


def _group_rss_bytes(pgid: int) -> int:
    if sys.platform == "darwin":
        return sum(rss for _, group, rss in _darwin_processes() if group == pgid)
    return sum(_rss_bytes(pid) for pid in _group_members(pgid))


def _kill_group(process: subprocess.Popen, *, grace: float) -> tuple[str, int]:
    """先 TERM 再 KILL，并等到进程组真的消失。返回（终止方式，残留进程数）。"""
    pgid = process.pid
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:                                   # pragma: no cover
        return "already-exited", 0
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        process.poll()  # Reap the leader, but still wait for its descendants.
        if not _group_alive(pgid):
            return "term", 0
        time.sleep(0.05)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:                                   # pragma: no cover
        pass
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        process.poll()
        if not _group_alive(pgid):
            return "kill", 0
        time.sleep(0.05)
    return "kill", _group_alive(pgid)


def _group_alive(pgid: int) -> int:
    return len(_group_members(pgid))


def run_worker_job(*, services: WorkerServices, job: dict, limits: RenderLimits,
                   job_id: str, cancel=None, log_path: Path | None = None) -> dict:
    """跑一个 job，返回**结构化**结果（含终止原因、峰值资源、日志路径）。

    这里不做"临时放弃"：任何提前结束都写 `terminated_by`，并把日志留在磁盘上。
    """
    global _ACTIVE_WORKERS, _MAX_OBSERVED_CONCURRENCY
    # ⚠️ 必须 resolve：相对路径在 `cwd=scratch` 里会解析成 scratch 下的假路径
    # （实测错误：`can't open file '<scratch>/tools/manim_worker/run_scene.py'`）。
    worker_dir = Path(services.worker_dir).resolve()
    scratch = Path(job["scratch"])
    output = Path(job["output"])
    scratch.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    job_path = scratch / f"{job_id}.job.json"
    job["signal_probe_pid"] = os.getpid()
    job["result_path"] = str(scratch / f"{job_id}.result.json")
    job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    log_path = log_path or (scratch / f"{job_id}.log")

    isolation = select_isolation(worker_dir, forced=services.isolation)
    if not isolation.available:
        return {"status": "blocked", "kind": "isolation", "reason": isolation.detail,
                "isolation": isolation.as_dict(), "terminated_by": "", "cache_committed": False}
    policy = load_asset(worker_dir, "policy")
    paths = policy.sandbox_paths(interpreter=services.interpreter, worker_dir=str(worker_dir),
                                scratch=str(scratch), output=str(output))
    env = policy.worker_env(interpreter=services.interpreter, worker_dir=str(worker_dir),
                            scratch=str(scratch), host_env=services.host_env)
    job["sandbox"] = {"read_only": paths["read_only"], "read_write": paths["read_write"],
                      "memory_bytes": limits.memory_bytes, "output_bytes": limits.output_bytes,
                      "cpu_seconds": int(limits.timeout_seconds) + 30,
                      "deny_calls": job.get("deny_calls", "full"),
                      "host_netns": job.get("host_netns", "")}
    job["env"] = env

    if isolation.backend == "bwrap":
        argv = policy and load_asset(worker_dir, "bwrap").build_argv(
            read_only=paths["read_only"], read_write=paths["read_write"], scratch=str(scratch),
            output=str(output), entry=str(worker_dir / "run_scene.py"), job_path=str(job_path),
            python=services.interpreter, env=env, chdir=str(scratch))
    else:
        argv = [services.interpreter, str(worker_dir / "run_scene.py"), "--job", str(job_path)]
    job["isolation_backend"] = isolation.backend
    job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")

    # Queue time is separate from execution time. A queued job only expires
    # with its parent deadline or cancellation, not its render-duration budget.
    with _WORKER_GATE:
        while True:
            if cancel is not None and getattr(cancel, "cancelled", False):
                return {"status": "cancelled", "kind": "cancelled", "reason": "派发前已取消",
                        "isolation": isolation.as_dict(), "terminated_by": "cancelled", "cache_committed": False}
            if limits.effective_timeout() <= 0:
                return {"status": "blocked", "kind": "deadline", "reason": "父级 deadline 已用尽，不再派发",
                        "isolation": isolation.as_dict(), "terminated_by": "deadline", "cache_committed": False}
            if _ACTIVE_WORKERS < _WORKER_CAPACITY:
                break
            _WORKER_GATE.wait(timeout=.1)
        _ACTIVE_WORKERS += 1
        _MAX_OBSERVED_CONCURRENCY = max(_MAX_OBSERVED_CONCURRENCY, _ACTIVE_WORKERS)
        active_now = _ACTIVE_WORKERS
        capacity_now = _WORKER_CAPACITY
    started = time.monotonic()
    peak_rss = 0
    terminated_by = ""
    timed_out = False
    process = None
    try:
        with log_path.open("w", encoding="utf-8") as log:
            log.write(f"# argv={json.dumps(argv, ensure_ascii=False)}\n")
            log.write(f"# isolation={json.dumps(isolation.as_dict(), ensure_ascii=False)}\n")
            log.flush()
            if services.subprocess_runner is not None:
                return {**_run_with_substitute(services, argv, scratch, output, log_path,
                                              isolation, started, job_id),
                        "max_concurrent_workers": capacity_now}
            process = subprocess.Popen(argv, cwd=str(scratch), env=env,
                                       stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            while True:
                code = process.poll()
                now = time.monotonic()
                peak_rss = max(peak_rss, _group_rss_bytes(process.pid))
                decision = monitor_decision(
                    elapsed=now - started, rss=peak_rss, output=_job_bytes(scratch, output),
                    cancelled=bool(cancel is not None and getattr(cancel, "cancelled", False)),
                    limits=limits)
                if decision:
                    terminated_by = decision
                    timed_out = decision == "timeout"
                    break
                if code is not None:
                    break
                time.sleep(MONITOR_INTERVAL_SECONDS)
        if terminated_by:
            how, orphans = _kill_group(process, grace=TERM_GRACE_SECONDS)
            terminated_by = f"{terminated_by}(sig{'kill' if how == 'kill' else 'term'})"
        else:
            # A successful leader can leave subprocesses running; never let
            # their lifetimes outlast this worker's slot or output checks.
            _, orphans = _kill_group(process, grace=TERM_GRACE_SECONDS)
    except BaseException:
        if process is not None:
            _kill_group(process, grace=TERM_GRACE_SECONDS)
        raise
    finally:
        with _WORKER_GATE:
            _ACTIVE_WORKERS -= 1
            _WORKER_GATE.notify_all()

    wall = time.monotonic() - started
    result_path = Path(job["result_path"])
    payload: dict = {}
    if result_path.is_file():
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            payload = {"status": "failed", "kind": "bad_result",
                       "reason": f"结果 JSON 不可解析：{exc}"}
    status = str(payload.get("status") or "failed")
    if not payload:
        # 子进程崩在写结果之前（例如内存上限把解释器自己压死）：把日志尾部当原因，
        # 否则调用方只看到一个空理由 —— 那等于把"为什么失败"丢掉了。
        payload = {"status": "failed", "kind": "no_result_file",
                   "reason": _log_tail(log_path)}
    if terminated_by == "cancelled" or terminated_by.startswith("cancelled"):
        status = "cancelled"
    elif timed_out:
        status = "timeout"
    elif terminated_by and status == "ok":
        status = terminated_by.split("(")[0]
    elif orphans:
        status = "failed"
        payload["kind"] = "worker_cleanup"
        payload["reason"] = f"{orphans} worker processes survived termination"
    job_bytes = _job_bytes(scratch, output)
    if job_bytes > limits.output_bytes and not terminated_by:
        status, terminated_by = "output_limit", "output_limit"
    return {
        "status": status, "kind": payload.get("kind", ""),
        "reason": payload.get("reason", "") if status != "ok" else "",
        "isolation": isolation.as_dict(), "terminated_by": terminated_by,
        "orphans_remaining": orphans, "wall_seconds": round(wall, 3),
        "peak_rss_bytes": peak_rss, "output_bytes": _dir_bytes(output) if output.exists() else 0,
        "job_bytes": job_bytes,
        "concurrency_observed": active_now, "max_concurrent_workers": capacity_now,
        "log_path": str(log_path),
        "result_path": str(result_path) if result_path.is_file() else "",
        "result": payload, "cache_committed": False, "worker_scratch": str(scratch),
    }


def _log_tail(log_path: Path, limit: int = 800) -> str:
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:                                              # pragma: no cover
        return "（日志读不到）"
    if not text:
        return "子进程没有写出结果文件，日志也是空的"
    return text[-limit:]


def _run_with_substitute(services: WorkerServices, argv: list[str], scratch: Path,
                         output: Path, log_path: Path, isolation: WorkerIsolation,
                         started: float, job_id: str) -> dict:
    """受控子进程替身（测试用）：跑替身命令，然后照常读结果文件。"""
    completed = services.subprocess_runner(argv, cwd=str(scratch),
                                           env=dict(os.environ), timeout=60)
    payload = {}
    result_path = Path(scratch) / f"{job_id}.result.json"
    if result_path.is_file():
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    return {"status": str(payload.get("status") or "failed"),
            "kind": payload.get("kind", ""), "reason": payload.get("reason", ""),
            "isolation": isolation.as_dict(),
            "terminated_by": "", "orphans_remaining": 0,
            "wall_seconds": round(time.monotonic() - started, 3),
            "peak_rss_bytes": 0, "output_bytes": _dir_bytes(output),
            "concurrency_observed": _ACTIVE_WORKERS, "log_path": str(log_path),
            "result_path": str(result_path), "result": payload,
            "cache_committed": False, "worker_scratch": str(scratch),
            "substitute_exit_code": getattr(completed, "returncode", None)}


def probe_isolation(*, services: WorkerServices, limits: RenderLimits,
                    collect_environment: bool = False,
                    probe_paths: list[str] | None = None, cancel=None,
                    require_signal_scope: bool = False) -> dict:
    """隔离 + 环境探针：跑一次 `mode=probe` job（哨兵／网络实测）。

    `probe_paths` 必须包含**白名单之外**的真实文件，否则这条探针什么也没证明。
    """
    worker_dir = Path(services.worker_dir)
    scratch_root = Path(services.scratch_root)
    job_id = f"probe-{uuid.uuid4().hex[:8]}"
    scratch = scratch_root / job_id / "scratch"
    output = scratch_root / job_id / "out"
    sentinel = write_sentinel(scratch_root / job_id)
    targets = [str(sentinel), *[str(item) for item in (probe_paths or [])]]
    if not targets:                                              # pragma: no cover
        raise ConfigError("隔离探针至少要一个白名单之外的目标路径")
    host_netns = ""
    try:
        host_netns = os.readlink("/proc/self/ns/net")
    except OSError:                                              # pragma: no cover
        host_netns = ""
    job = {"schema": "manim-worker-job-1.0", "mode": "probe", "profile": "final",
           "scratch": str(scratch), "output": str(output),
           "collect_environment": collect_environment,
           "require_signal_scope": require_signal_scope,
           "probe_paths": targets, "host_netns": host_netns,
           "probe_host": "1.1.1.1", "probe_port": 443, "probe_dns": "pypi.org"}
    outcome = run_worker_job(services=services, job=job, limits=limits, job_id=job_id, cancel=cancel)
    payload = outcome.get("result") or {}
    if payload.get("environment") and "max_concurrent_workers" in outcome:
        # The sandbox can report its per-process resource limits, but only the
        # host knows the shared admission capacity used by this probe.
        payload["environment"].setdefault("limits", {})["max_concurrent_workers"] = outcome["max_concurrent_workers"]
    # 指纹落盘（证据要能被人打开看，也能被 `environment_hash` 复核）：
    reports = Path(scratch).parent
    environment_path = reports / "environment.json"
    isolation_path = reports / "isolation.json"
    environment_path.write_text(
        json.dumps(payload.get("environment", {}), ensure_ascii=False, indent=2,
                   sort_keys=True) + "\n", encoding="utf-8")
    isolation_path.write_text(
        json.dumps(payload.get("isolation", {}), ensure_ascii=False, indent=2,
                   sort_keys=True) + "\n", encoding="utf-8")
    return {"outcome": outcome, "isolation": payload.get("isolation", {}),
            "environment": payload.get("environment", {}), "sentinel": str(sentinel),
            "probe_paths": targets, "sentinel_readable_outside": sentinel.is_file(),
            "environment_path": str(environment_path),
            "environment_sha256": digest_file(environment_path),
            "isolation_path": str(isolation_path),
            "isolation_sha256": digest_file(isolation_path)}


def environment_hash(probe: dict, *, profile: str = "final") -> str:
    """环境身份哈希：**不含**每次运行都变的字段（临时目录、netns、探针结果、耗时）。

    进哈希的是"这台机器上用什么解释器／包／字体／工具／隔离机制"，
    所以同一环境两次运行得到同一个值、缓存才有意义。
    """
    environment = probe.get("environment") or {}
    isolation = probe.get("isolation") or {}
    applied = isolation.get("applied") or {}
    landlock = applied.get("landlock") or {}
    rlimits = applied.get("rlimits") or {}
    seccomp = applied.get("seccomp") or {}
    projection = {
        "schema_version": environment.get("schema_version"),
        "profile": profile,
        "python": environment.get("python"),
        "packages": environment.get("packages"),
        "native": environment.get("native"),
        "tools": environment.get("tools"),
        "font": environment.get("font"),
        "tex_packages": environment.get("tex_packages"),
        "math_tex": {key: environment.get("math_tex", {}).get(key)
                     for key in ("ok", "latex_ok", "detail", "frame_hash", "expression", "mixed_text")},
        "limits": environment.get("limits"),
        "isolation": {"backend": isolation.get("backend"),
                      "landlock_abi": landlock.get("abi"),
                      "handled_fs": landlock.get("handled_fs"),
                      "handled_net": landlock.get("handled_net"),
                      "scoped": landlock.get("scoped"),
                      "rules": len(landlock.get("rules") or []),
                      "rlimits": {"address_space_bytes": rlimits.get("address_space_bytes"),
                                  "single_file_bytes": rlimits.get("single_file_bytes"),
                                  "cpu_seconds": rlimits.get("cpu_seconds")},
                      "seccomp": seccomp.get("denied_calls"),
                      "seatbelt": {key: (applied.get("seatbelt") or {}).get(key)
                                   for key in ("backend", "network", "signal_scope", "denied_syscalls", "process_info_scope", "sysctl_policy")}},
    }
    return digest_of(projection)


# ── 成功缓存：取消/失败一律不提交 ──────────────────────────────────────────
class AnimationCache:
    """按 `spec_hash + compiler_hash + environment_hash + profile` 复用**已成功**的渲染。

    取消与失败**不写缓存**：否则下一次"命中缓存"会把一次没跑完的渲染当成产物交付。
    提交是原子的（先写临时目录再 rename），且带 `manifest.json` 记录全部资源 hash。
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)

    @staticmethod
    def key(*, spec_hash: str, compiler_hash: str, environment_hash: str,
            profile: str) -> str:
        return digest_of({"spec_hash": spec_hash, "compiler_hash": compiler_hash,
                          "environment_hash": environment_hash, "profile": profile})

    def path_for(self, key: str) -> Path:
        return self.root / key.split(":", 1)[1][:32]

    def lookup(self, key: str) -> dict | None:
        directory = self.path_for(key)
        manifest = directory / "manifest.json"
        if not manifest.is_file():
            return None
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except ValueError:                                       # pragma: no cover
            return None
        resources = data.get("resources") or []
        for entry in resources:
            candidate = directory / str(entry.get("relative_path", ""))
            if not candidate.is_file():
                return None
            if digest_file(candidate) != entry.get("sha256"):
                return None
        data["cache_hit"] = True
        data["cache_dir"] = str(directory)
        return data

    def commit(self, key: str, *, source_dir: str | Path, resources: list[dict],
               extra: dict | None = None) -> dict:
        source = Path(source_dir)
        target = self.path_for(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + f".tmp-{uuid.uuid4().hex[:6]}")
        if temp.exists():
            shutil.rmtree(temp)
        temp.mkdir(parents=True)
        for entry in resources:
            relative = str(entry["relative_path"])
            origin = source / relative
            if not origin.is_file():
                shutil.rmtree(temp, ignore_errors=True)
                raise ConfigError(f"缓存提交缺文件：{origin}")
            destination = temp / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, destination)
        manifest = {"schema_version": "animation-cache-1.0", "key": key,
                    "resources": resources, "cache_hit": False}
        if extra:
            manifest.update(extra)
        (temp / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        if target.exists():
            shutil.rmtree(target)
        temp.rename(target)
        manifest["cache_dir"] = str(target)
        return manifest


def file_resource(path: str | Path, *, kind: str, relative_path: str | None = None) -> dict:
    """§5.3 的资源三元组 + 字节数（sha256 对**内容**取，不对路径取）。"""
    from mimetypes import guess_type

    target = Path(path)
    relative = relative_path or target.name
    from .result import RESOURCE_MIMES
    if kind in RESOURCE_MIMES:
        mime=RESOURCE_MIMES[kind]
    else:
        mime = guess_type(target.name)[0] or "application/octet-stream"
    if mime == "text/vtt":
        mime = "text/vtt; charset=utf-8"
    elif mime.startswith("text/") and "charset=" not in mime:
        mime = f"{mime}; charset=utf-8"
    return {"kind": kind, "relative_path": relative, "sha256": digest_file(target),
            "bytes": target.stat().st_size, "mime": mime}


__all__ = ["RenderLimits", "WorkerIsolation", "WorkerServices", "AnimationCache",
           "monitor_decision",
           "load_asset", "resolve_worker_dir", "select_isolation", "write_sentinel",
           "run_worker_job", "isolation_verified", "configure_worker_concurrency", "probe_isolation", "environment_hash", "file_resource",
           "max_observed_concurrency", "digest_bytes", "Cancelled", "CapabilityError"]
