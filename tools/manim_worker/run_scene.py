"""沙箱内入口：**唯一**被 worker 启动的脚本（计划 §7 `tools/manim_worker/`）。

它按顺序做四件事，任何一步失败都写结构化结果并给非零退出码 —— 没有"悄悄降级"这一档：

1. **清空环境**：只保留宿主显式给的最小环境（没有 provider 密钥、没有宿主 PATH）。
   注意：bwrap 用 `--clearenv`，但 Landlock 后端的子进程会**继承**宿主环境，
   所以清空必须在进程内做，而且要在 import manim／起子进程之前。
2. **施加沙箱**：Linux 为 rlimits → Landlock → seccomp；macOS 为 Seatbelt 默认拒绝白名单与系统调用限制。
   三者都被子进程继承，所以 Manim 起的 ffmpeg／latex 一起受约束。
3. **跑模式**：`probe`（隔离探针 + 环境指纹）或 `render`（真渲染 + 出片）。
4. **原子写结果**：先写 `.tmp` 再 rename，宿主不会把半截 JSON 当成功。

`--host-netns` 由宿主传入，用来证明"网络命名空间确实不在一个里面"（本机 `/proc/1/ns/net`
不可读，所以只能跟宿主自报的值比）。
"""
from __future__ import annotations

import json
import os
import errno
import signal
import socket
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import policy                                                        # noqa: E402
import sandbox                                                       # noqa: E402

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_BLOCKED = 3
EXIT_UNSAFE = 4


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    os.replace(temp, path)


def _scrub_environment(env: dict[str, str]) -> None:
    os.environ.clear()
    for key, value in env.items():
        os.environ[str(key)] = str(value)


def _apply(job: dict) -> dict:
    spec = job["sandbox"]
    if job.get("isolation_backend") == "seatbelt":
        import seatbelt
        # macOS RLIMIT_AS constrains reserved VM, including the system shared
        # cache, and cannot represent a useful resident-memory budget. The host
        # enforces the aggregate RSS of the non-escapable worker process group.
        import resource
        resource.setrlimit(resource.RLIMIT_FSIZE, (int(spec["output_bytes"]),) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (int(spec["cpu_seconds"]), int(spec["cpu_seconds"]) + 10))
        try:
            applied = seatbelt.apply(read_only=list(spec["read_only"]), read_write=list(spec["read_write"]))
        except (OSError, RuntimeError, ValueError) as exc:
            raise sandbox.SandboxError(str(exc)) from exc
        return {"seatbelt": applied, "rlimits": {"backend": "rlimits",
                "address_space_bytes": None, "memory_enforcement": "host-process-group-rss",
                "single_file_bytes": int(spec["output_bytes"]), "cpu_seconds": int(spec["cpu_seconds"])}}
    deny = (sandbox.DENY_NETWORK_CALLS if spec.get("deny_calls", "full") == "full"
            else sandbox.DENY_NETWORK_CALLS_MINIMAL)
    generated = job.get("mode") == "code"
    read_write = list(spec["read_write"])
    if generated:
        deny = {**deny, **sandbox.DENY_PROCESS_ESCAPE_CALLS}
        # Do not grant /dev/shm (outside the monitored scratch/output budget).
        read_write = [path for path in read_write if path != "/dev"]
        read_write.extend(["/dev/null", "/dev/zero", "/dev/random", "/dev/urandom"])
    return sandbox.apply_sandbox(
        read_only=list(spec["read_only"]), read_write=read_write,
        memory_bytes=int(spec["memory_bytes"]), output_bytes=int(spec["output_bytes"]),
        cpu_seconds=int(spec["cpu_seconds"]), deny_calls=deny,
        require_signal_scope=generated or bool(job.get("require_signal_scope")))


def _read_probe(path: str) -> dict:
    target = Path(path)
    try:
        data = target.read_bytes()
    except OSError as exc:
        return {"path": path, "readable": False, "errno": exc.errno,
                "error": f"{type(exc).__name__}: {exc.strerror or exc}"}
    return {"path": path, "readable": True, "bytes": len(data)}


def _network_probe(host: str, port: int, url: str) -> dict:
    report: dict = {"host": host, "port": port, "url": url}
    try:
        maybe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        maybe.close()
        report["socket_created"] = True
    except OSError as exc:
        report["socket_created"] = False
        report["socket_error"] = f"{type(exc).__name__}: {exc}"
    try:
        connection = socket.create_connection((host, port), timeout=3)
        connection.close()
        report["tcp_connected"] = True
    except OSError as exc:
        report["tcp_connected"] = False
        report["tcp_error"] = f"{type(exc).__name__}: {exc}"
    try:
        report["dns_result"] = socket.getaddrinfo(url, 443)[0][4][0]
    except OSError as exc:
        report["dns_result"] = ""
        report["dns_error"] = f"{type(exc).__name__}: {exc}"
    report["network_reachable"] = bool(report.get("tcp_connected"))
    return report


def _isolation_report(job: dict, applied: dict) -> dict:
    paths = [_read_probe(path) for path in job.get("probe_paths", [])]
    network = _network_probe(job.get("probe_host", "1.1.1.1"), int(job.get("probe_port", 443)),
                             job.get("probe_dns", "pypi.org"))
    signals = _signal_probe(job.get("signal_probe_pid"))
    process_environment = _process_environment_probe(job.get("signal_probe_pid"))
    netns = ""
    try:
        netns = os.readlink("/proc/self/ns/net")
    except OSError:                                                  # pragma: no cover
        netns = "（读不到 /proc/self/ns/net）"
    host_netns = str(job.get("host_netns") or "")
    # 最小环境审计：沙箱里到底有哪些环境变量（provider 密钥一个都不许有）。
    keys = sorted(os.environ)
    suspicious = [key for key in keys
                  if any(token in key.upper() for token in ("SECRET", "TOKEN", "API_KEY",
                                                            "PASSWORD", "CREDENTIAL"))]
    report = {
        "backend": job.get("isolation_backend", "landlock-seccomp"),
        "env_keys": keys,
        "provider_secret_present": bool(suspicious),
        "provider_secret_keys": suspicious,
        "applied": applied,
        "probed_paths": paths,
        "network": network,
        "signals": signals,
        "process_environment": process_environment,
        "netns": netns,
        "host_netns": host_netns,
        "netns_isolated": bool(host_netns) and netns != host_netns,
    }
    unreadable = all(not item["readable"] for item in paths) and bool(paths)
    report["paths_blocked"] = unreadable
    report["safe"] = bool(unreadable and not network["network_reachable"] and signals["blocked"]
                          and process_environment["blocked"])
    report["reason"] = ("哨兵路径不可读、网络不可达、外部进程信号与环境读取受限" if report["safe"]
                        else "隔离未生效：哨兵、网络、外部进程信号或环境读取未受限")
    return report


def _process_environment_probe(target_pid) -> dict:
    if sys.platform != "darwin":
        return {"blocked": True, "mechanism": "proc filesystem whitelist"}
    # Query only the size; never copy the host's real environment into worker
    # memory. Seatbelt must reject even this operation across the new session.
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    mib = (ctypes.c_int * 3)(1, 49, int(target_pid or 0))  # KERN_PROCARGS2
    size = ctypes.c_size_t()
    code = libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0)
    error = ctypes.get_errno() if code else 0
    return {"blocked": code != 0 and error in (errno.EPERM, errno.EACCES),
            "errno": error, "mechanism": "Seatbelt process-info + sysctl + separate session"}


def _signal_probe(target_pid) -> dict:
    """SIGCONT is harmless to our running host, unlike a termination signal."""
    if type(target_pid) is not int or target_pid <= 0:
        return {"blocked": False, "reason": "missing real outside signal target"}
    try:
        os.kill(target_pid, signal.SIGCONT)
    except OSError as exc:
        # Under bwrap the host PID is normally invisible (ESRCH); Landlock
        # denies it with EPERM. Either prevents reaching the outside process.
        return {"blocked": exc.errno in (errno.EPERM, errno.EACCES, errno.ESRCH),
                "errno": exc.errno, "signal": "SIGCONT", "target_pid": target_pid}
    return {"blocked": False, "signal": "SIGCONT", "target_pid": target_pid}


def _run_probe(job: dict) -> int:
    applied = _apply(job)
    isolation = _isolation_report(job, applied)
    payload = {"schema": "manim-worker-result-1.0", "mode": "probe",
               "status": "ok" if isolation["safe"] else "unsafe",
               "isolation": isolation}
    result_path = Path(job["result_path"])
    if job.get("collect_environment"):
        import probe_env
        payload["environment"] = probe_env.collect(sandbox=isolation, profile=job["profile"])
    _write_atomic(result_path, payload)
    return EXIT_OK if isolation["safe"] else EXIT_UNSAFE


def _run_render(job: dict) -> int:
    applied = _apply(job)
    result_path = Path(job["result_path"])
    try:
        import scene_builder
        started = time.monotonic()
        summary = scene_builder.render(
            job["plan"], scratch=job["scratch"], output=job["output"],
            font_family=job["font"]["family"], pinned_font=job["font"]["file"],
            pixel_width=int(job["limits"]["pixel_width"]),
            pixel_height=int(job["limits"]["pixel_height"]),
            frame_rate=int(job["limits"]["frame_rate"]),
            poster_seconds=float(job.get("poster_seconds", 1.0)),
            ffmpeg=job["ffmpeg"])
        wall = time.monotonic() - started
        payload = {"schema": "manim-worker-result-1.0", "mode": "render", "status": "ok",
                   "sandbox": applied, "wall_seconds": round(wall, 3), **summary}
    except MemoryError as exc:
        _write_atomic(result_path, {"schema": "manim-worker-result-1.0", "mode": "render",
                                    "status": "failed", "kind": "memory_limit",
                                    "reason": f"{type(exc).__name__}: {exc}",
                                    "sandbox": applied})
        return EXIT_FAILED
    except Exception as exc:                                         # noqa: BLE001
        _write_atomic(result_path, {"schema": "manim-worker-result-1.0", "mode": "render",
                                    "status": "failed", "kind": type(exc).__name__,
                                    "reason": f"{type(exc).__name__}: {exc}",
                                    "traceback": traceback.format_exc()[-4000:],
                                    "sandbox": applied})
        return EXIT_FAILED
    _write_atomic(result_path, payload)
    return EXIT_OK


def _run_code(job: dict) -> int:
    # Generated source must never be imported until the real OS policy is active.
    applied = _apply(job)
    import code_renderer
    started = time.monotonic()
    summary = code_renderer.render(job)
    _write_atomic(Path(job["result_path"]), {
        "schema": "manim-worker-result-1.0", "mode": "code", "status": "ok",
        "sandbox": applied, "wall_seconds": round(time.monotonic() - started, 3), **summary})
    return EXIT_OK


def main(argv: list[str]) -> int:
    job_path = ""
    rest = list(argv)
    while rest:
        item = rest.pop(0)
        if item == "--job":
            job_path = rest.pop(0)
        else:
            print(f"未知参数 {item!r}", file=sys.stderr)
            return 2
    if not job_path or not Path(job_path).is_file():
        print("用法：run_scene.py --job <job.json>", file=sys.stderr)
        return 2
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    _scrub_environment(job.get("env") or {})
    mode = job.get("mode", "render")
    try:
        if mode == "probe":
            return _run_probe(job)
        if mode == "render":
            return _run_render(job)
        if mode == "code":
            return _run_code(job)
    except sandbox.SandboxError as exc:
        _write_atomic(Path(job["result_path"]),
                      {"schema": "manim-worker-result-1.0", "mode": mode,
                       "status": "blocked", "kind": "sandbox", "reason": str(exc)})
        return EXIT_BLOCKED
    except Exception as exc:                                         # noqa: BLE001
        # 结果文件必须总是可读：否则宿主只看到一个退出码，"为什么失败"就丢了。
        _write_atomic(Path(job["result_path"]),
                      {"schema": "manim-worker-result-1.0", "mode": mode,
                       "status": "failed", "kind": type(exc).__name__,
                       "reason": f"{type(exc).__name__}: {exc}",
                       "traceback": traceback.format_exc()[-4000:]})
        return EXIT_FAILED
    print(f"未知模式 {mode!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":                                           # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
