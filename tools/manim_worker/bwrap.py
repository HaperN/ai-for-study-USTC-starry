"""bubblewrap 参数生成与可用性自检（计划 §8 T07 的**首选**隔离后端）。

本模块是纯函数 + 一次真实自检，不 import Manim，也不假设 bwrap 一定可用：

* `build_argv(...)` 只生成参数（可单测：白名单挂载、无网络命名空间、输入只读、输出可写）；
* `selftest()` 真跑一次最小 bwrap，把**原始 stderr** 留下来 —— 本机 Ubuntu 24.04
  的 `kernel.apparmor_restrict_unprivileged_userns=1` 会让它失败在
  `setting up uid map`，那条错误必须能被审计，而不是被一句"不可用"盖掉。

⚠️ 计划明确：**不把整个 HOME 或根文件系统原样只读绑定当作"无密钥"证明。**
所以 `build_argv` 的 ro 绑定是**逐条显式**的（解释器／库／字体／TeX／可信代码），
没有 `/`、没有 `$HOME`、没有 `--ro-bind /home`。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

BWRAP = "bwrap"


def build_argv(*, read_only: list[str], read_write: list[str], scratch: str, output: str,
               entry: str, job_path: str, python: str, env: dict[str, str],
               chdir: str | None = None) -> list[str]:
    """生成一条 bwrap 命令行：无网络命名空间、白名单只读挂载、输出与临时独立可写。"""
    # The host already starts a new session. Keep every sandbox process in
    # that process group so host resource accounting and killpg cover it.
    argv = [BWRAP, "--die-with-parent", "--unshare-all",
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    for raw in read_only:
        path = Path(raw)
        # /proc is a new PID namespace filesystem. Host /proc/self and
        # thread-self are dynamic symlinks and cannot be rebound into it.
        if path == Path('/proc') or Path('/proc') in path.parents:
            continue
        if not path.exists():
            continue
        argv += ["--ro-bind-try", str(path), str(path)]
    for raw in read_write + [scratch, output]:
        path = Path(raw)
        if path == Path('/dev'):
            continue  # Keep the private device tree created by --dev.

        path.mkdir(parents=True, exist_ok=True)
        argv += ["--bind-try", str(path), str(path)]
    argv.append("--clearenv")
    for key, value in sorted(env.items()):
        argv += ["--setenv", str(key), str(value)]
    argv += ["--chdir", str(chdir or scratch), "--", python, entry, "--job", job_path]
    return argv


def selftest(timeout: float = 20.0) -> dict:
    """真跑一次最小 bwrap；返回 `{"available": bool, "detail": str, "argv": [...]}`。"""
    argv = [BWRAP, "--unshare-all", "--die-with-parent", "--ro-bind", "/usr", "/usr",
            "--ro-bind-try", "/lib", "/lib", "--ro-bind-try", "/lib64", "/lib64",
            "--ro-bind-try", "/bin", "/bin", "--proc", "/proc", "--dev", "/dev",
            "--tmpfs", "/tmp", "--", "/bin/sh", "-c", "echo bwrap-ok"]
    try:
        proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": False, "argv": argv,
                "detail": f"{type(exc).__name__}: {exc}"}
    if proc.returncode == 0 and b"bwrap-ok" in proc.stdout:
        return {"available": True, "argv": argv, "detail": "bwrap 自检通过（无网络命名空间）"}
    stderr = proc.stderr.decode("utf-8", "replace").strip().splitlines()
    return {"available": False, "argv": argv, "exit_code": proc.returncode,
            "stderr": stderr[:4],
            "detail": (stderr[0] if stderr else f"退出码 {proc.returncode}")}


__all__ = ["BWRAP", "build_argv", "selftest"]
