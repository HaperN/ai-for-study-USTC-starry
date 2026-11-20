"""沙箱原语：rlimits + Landlock + seccomp（**全部非特权**，不需要 root／userns）。

## 为什么不是只有 bubblewrap

计划 §8 T07 要求"可探测的 bubblewrap 隔离＋无网络命名空间"。本机实测 bwrap
**用不了**：Ubuntu 24.04 的 `kernel.apparmor_restrict_unprivileged_userns=1`
让非特权用户命名空间创建被 AppArmor 拒绝，bwrap 在 `setting up uid map` 就失败
（证据见 `文档/02-记录与报告/Manim与审核闭环改造证据-20261007-0035/T07/`）。
`bwrap.py` 仍然实现并**可探测**，只是在这台机器上如实判为不可用。

那"隔离设施不可用就 blocked、不许偷偷无隔离执行"怎么办？答案是**换一个真的能生效的机制**，
而不是放弃隔离：Linux 5.13+ 的 Landlock（本机 ABI 7）＋ seccomp 过滤器＋ rlimits，
三条都**非特权**、都被子进程继承，所以 Manim 与它启动的 ffmpeg／latex 一起受约束：

| 机制 | 挡住什么 | 与 bwrap 的对应 |
|---|---|---|
| Landlock 文件规则 | 除白名单路径外的**读写全拒**（宿主密钥文件根本读不到） | `--ro-bind` 白名单的反面（默认拒绝） |
| Landlock 网络规则（ABI≥4） | TCP bind / connect | `--unshare-net` |
| seccomp 过滤器 | `socket`/`socketpair`/`connect`/`bind`…（TCP 之外还挡 UDP DNS） | 同上，更严 |
| rlimits | 地址空间、单文件字节、CPU 秒 | `--rlimit-*` |

**它们不是"隔离的替代品"，是同一件事的另一套实现**：探针（`--selftest`）会真的去读一个
哨兵凭据文件、真的去连外网，并把结果写进环境指纹；读得到或连得通就判 blocked，
绝不放行。`probe_env.py` 把实际用的后端名字记进 `environment_hash`。
"""
from __future__ import annotations

import ctypes
import errno
import os
import resource
from pathlib import Path

# ── Landlock ───────────────────────────────────────────────────────────────
_SYS_CREATE_RULESET = 444
_SYS_ADD_RULE = 445
_SYS_RESTRICT_SELF = 446
_RULESET_VERSION = 1
_RULE_PATH_BENEATH = 1

#: 文件系统访问位（内核 `include/uapi/linux/landlock.h`）
FS_EXECUTE = 1 << 0
FS_WRITE_FILE = 1 << 1
FS_READ_FILE = 1 << 2
FS_READ_DIR = 1 << 3
FS_REMOVE_DIR = 1 << 4
FS_REMOVE_FILE = 1 << 5
FS_MAKE_CHAR = 1 << 6
FS_MAKE_DIR = 1 << 7
FS_MAKE_REG = 1 << 8
FS_MAKE_SOCK = 1 << 9
FS_MAKE_FIFO = 1 << 10
FS_MAKE_BLOCK = 1 << 11
FS_MAKE_SYM = 1 << 12
FS_REFER = 1 << 13        # ABI ≥ 2
FS_TRUNCATE = 1 << 14     # ABI ≥ 3
FS_IOCTL_DEV = 1 << 15    # ABI ≥ 5
#: 网络访问位（ABI ≥ 4）
NET_BIND_TCP = 1 << 0
NET_CONNECT_TCP = 1 << 1
#: ABI >= 6: signals may reach only this Landlock domain and its descendants.
#: https://docs.kernel.org/userspace-api/landlock.html#ipc-scoping
SCOPE_SIGNAL = 1 << 1

#: 我们**处理**（= 默认拒绝）的文件权限。故意不含 MAKE_CHAR/MAKE_BLOCK/IOCTL_DEV：
#: 沙箱不需要创建设备节点，也不需要 ioctl 设备；不处理 = 不限制，少一类误伤。
_HANDLED_FS_BASE = (FS_EXECUTE | FS_WRITE_FILE | FS_READ_FILE | FS_READ_DIR | FS_REMOVE_DIR
                    | FS_REMOVE_FILE | FS_MAKE_DIR | FS_MAKE_REG | FS_MAKE_SOCK | FS_MAKE_FIFO
                    | FS_MAKE_SYM)
#: 只读 + 可执行（解释器、库、字体、TeX、可信代码、配置）
RO = FS_EXECUTE | FS_READ_FILE | FS_READ_DIR
#: 读写 + 可创建（输出目录、临时目录）。
#: ⚠️ `TRUNCATE`／`REFER` 一定要给：只给 WRITE_FILE 的话，"重新打开已有文件并写入"
#: （`open(path,"w")` 会截断）和跨目录 rename 都被拒 —— 实测症状是 Manim 写文本 SVG 时
#: `PermissionError: .../media/texts/xxx.svg`。少一个权限位，表现是"看起来像路径白名单错了"。
RW = (RO | FS_WRITE_FILE | FS_MAKE_DIR | FS_MAKE_REG | FS_MAKE_SYM | FS_REMOVE_FILE
      | FS_REMOVE_DIR | FS_REFER | FS_TRUNCATE)

#: 被 seccomp 直接拒绝（EPERM）的调用：**不允许任何网络 socket**
DENY_NETWORK_CALLS = {
    "socket": 41, "connect": 42, "accept": 43, "sendto": 44, "recvfrom": 45,
    "sendmsg": 46, "recvmsg": 47, "bind": 49, "listen": 50, "socketpair": 53,
    "accept4": 288,
}
#: 只挡"连出去/接进来"的退路：真需要 AF_UNIX（例如 multiprocessing）时用它
DENY_NETWORK_CALLS_MINIMAL = {
    "connect": 42, "accept": 43, "bind": 49, "listen": 50, "accept4": 288,
}
# Generated Python must remain in the process group monitored by the host.
# Regular fork/exec and signalling its own descendants remain available.
DENY_PROCESS_ESCAPE_CALLS = {"setpgid": 109, "setsid": 112}


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64),
                ("handled_access_net", ctypes.c_uint64),
                ("scoped", ctypes.c_uint64)]


class _PathBeneathAttr(ctypes.Structure):
    _fields_ = [("allowed_access", ctypes.c_uint64),
                ("parent_fd", ctypes.c_int32),
                ("flags", ctypes.c_int32)]


class SandboxError(RuntimeError):
    """隔离设施没有真正生效 —— 调用方必须 blocked，不能继续跑模型输入。"""


def _libc() -> ctypes.CDLL:
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return libc


def landlock_abi() -> int:
    """内核支持的 Landlock ABI 版本；0 表示不支持（老内核或未编译）。"""
    libc = _libc()
    rc = libc.syscall(_SYS_CREATE_RULESET, ctypes.c_void_p(None), ctypes.c_size_t(0),
                      ctypes.c_uint32(_RULESET_VERSION))
    if rc < 0:
        return 0
    return int(rc)


def _handled_fs(abi: int) -> int:
    handled = _HANDLED_FS_BASE
    if abi >= 2:
        handled |= FS_REFER
    if abi >= 3:
        handled |= FS_TRUNCATE
    return handled


def _ancestors(raw: str) -> list[str]:
    """一个路径的所有祖先目录（不含自身，含 `/`）。"""
    path = Path(raw)
    out: list[str] = []
    for parent in path.parents:
        out.append(str(parent))
    return out


def _mask_for(info: os.stat_result, right: int, *, abi: int) -> int:
    """按**文件类型**裁剪访问位。

    内核会对不合理组合直接返回 EINVAL：`READ_DIR` 只能给目录，`MAKE_*`／`REMOVE_*`
    也只能给目录，`WRITE_FILE`／`TRUNCATE` 只能给普通文件。第一版把一份目录用的掩码
    套到 `/proc/cpuinfo` 上，结果整条沙箱在 add_rule 就 EINVAL —— 这类错误必须当场报，
    不能"跳过这一条继续跑"。
    """
    import stat as stat_module

    is_dir = stat_module.S_ISDIR(info.st_mode)
    if is_dir:
        mask = right
    else:
        mask = right & ~(FS_READ_DIR | FS_MAKE_CHAR | FS_MAKE_DIR | FS_MAKE_REG | FS_MAKE_SOCK
                         | FS_MAKE_FIFO | FS_MAKE_BLOCK | FS_MAKE_SYM | FS_REMOVE_DIR
                         | FS_REMOVE_FILE | FS_REFER)
    if abi < 3:
        mask &= ~FS_TRUNCATE
    if abi < 2:
        mask &= ~FS_REFER
    return mask


def apply_landlock(*, read_only: list[str], read_write: list[str],
                   deny_network: bool = True, require_signal_scope: bool = False) -> dict:
    """对**当前进程**施加 Landlock（不可逆，子进程继承）。

    默认拒绝：只有明确列出的路径拿到权限，其余一律拒绝。返回实际生效的规则摘要。
    """
    abi = landlock_abi()
    if abi < 1:
        raise SandboxError("内核没有 Landlock（ABI 0）：非特权文件隔离不可用")
    if require_signal_scope and abi < 6:
        raise SandboxError("generated Python requires Landlock ABI >= 6 signal scoping")
    handled_fs = _handled_fs(abi)
    handled_net = (NET_BIND_TCP | NET_CONNECT_TCP) if (deny_network and abi >= 4) else 0
    scoped = SCOPE_SIGNAL if abi >= 6 else 0

    libc = _libc()
    ruleset_fd = -1
    last = 0
    # Never truncate the structure to silently lose a requested signal scope.
    sizes = (24,) if scoped else (24, 16, 8)
    for size in sizes:
        attr = _RulesetAttr(handled_access_fs=handled_fs, handled_access_net=handled_net,
                            scoped=scoped)
        ruleset_fd = libc.syscall(_SYS_CREATE_RULESET, ctypes.byref(attr),
                                  ctypes.c_size_t(size), ctypes.c_uint32(0))
        if ruleset_fd >= 0:
            break
        last = ctypes.get_errno()
        if last not in (errno.E2BIG, errno.EINVAL):
            break
    if ruleset_fd < 0:
        raise SandboxError(f"landlock_create_ruleset 失败：errno={last}")

    granted: list[dict] = []
    try:
        # Landlock 的 path-beneath 规则只覆盖"该目录之下的层级"；要**走到**那个目录，
        # 路径上每一级目录还得有 search（EXECUTE）权限。第一版漏了这一步，症状是
        # `PermissionError: .../site-packages/manim/__init__.py` —— 规则明明加了，
        # 却因为 环境所在的几级上层目录不可搜索而读不到。
        # 这里只给祖先目录 EXECUTE（可进入、不可列举、不可读文件），不给更多。
        search_only = sorted({str(parent) for raw in [*read_only, *read_write]
                              for parent in _ancestors(raw)})
        for raw in search_only:
            path = Path(raw)
            try:
                info = path.stat()
            except OSError:
                continue
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                attr = _PathBeneathAttr(allowed_access=FS_EXECUTE, parent_fd=fd, flags=0)
                if libc.syscall(_SYS_ADD_RULE, ctypes.c_int(ruleset_fd),
                                ctypes.c_uint32(_RULE_PATH_BENEATH),
                                ctypes.byref(attr), ctypes.c_uint32(0)) < 0:
                    raise SandboxError(
                        f"landlock_add_rule(search {path}) 失败：errno={ctypes.get_errno()}")
            finally:
                os.close(fd)
            granted.append({"path": str(path), "access": "search"})
        for right, paths in ((RO, read_only), (RW, read_write)):
            for raw in paths:
                path = Path(raw)
                try:
                    info = path.stat()
                except OSError:
                    continue
                access = _mask_for(info, right, abi=abi)
                if not access:
                    continue
                fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
                try:
                    attr = _PathBeneathAttr(allowed_access=access, parent_fd=fd, flags=0)
                    if libc.syscall(_SYS_ADD_RULE, ctypes.c_int(ruleset_fd),
                                    ctypes.c_uint32(_RULE_PATH_BENEATH),
                                    ctypes.byref(attr), ctypes.c_uint32(0)) < 0:
                        raise SandboxError(
                            f"landlock_add_rule({path}) 失败：errno={ctypes.get_errno()}")
                finally:
                    os.close(fd)
                granted.append({"path": str(path), "access": "rw" if right == RW else "ro"})
        if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
            raise SandboxError(f"prctl(PR_SET_NO_NEW_PRIVS) 失败：errno={ctypes.get_errno()}")
        if libc.syscall(_SYS_RESTRICT_SELF, ctypes.c_int(ruleset_fd),
                        ctypes.c_uint32(0)) < 0:
            raise SandboxError(f"landlock_restrict_self 失败：errno={ctypes.get_errno()}")
    finally:
        os.close(ruleset_fd)
    return {"backend": "landlock", "abi": abi, "handled_fs": handled_fs,
            "handled_net": handled_net, "scoped": scoped, "rules": granted}


# ── seccomp ────────────────────────────────────────────────────────────────
class _SockFilter(ctypes.Structure):
    _fields_ = [("code", ctypes.c_uint16), ("jt", ctypes.c_uint8),
                ("jf", ctypes.c_uint8), ("k", ctypes.c_uint32)]


class _SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_uint16), ("filter", ctypes.POINTER(_SockFilter))]


_BPF_LD_W_ABS = 0x20
_BPF_JMP_JEQ_K = 0x15
_BPF_RET_K = 0x06
_SECCOMP_RET_ALLOW = 0x7FFF0000
_SECCOMP_RET_ERRNO = 0x00050000
_SECCOMP_RET_KILL_PROCESS = 0x80000000
_AUDIT_ARCH_X86_64 = 0xC000003E
#: seccomp_data 偏移：nr=0, arch=4
_PR_SET_NO_NEW_PRIVS = 38
_PR_SET_SECCOMP = 22
_SECCOMP_MODE_FILTER = 2


def apply_seccomp(*, deny_calls: dict[str, int] | None = None) -> dict:
    """装一个 seccomp-BPF 过滤器：列出的系统调用一律返回 EPERM。

    为什么用 EPERM 而不是 KILL_PROCESS：隔离探针要能**说清**是哪一步被挡住；
    被内核静默杀掉只会留下一个"退出码 -9"，那对定位没有帮助。
    架构不匹配时才 KILL（防止 x86_64 之外的 ABI 绕过检查）。
    """
    calls = dict(DENY_NETWORK_CALLS if deny_calls is None else deny_calls)
    filters: list[_SockFilter] = [
        _SockFilter(_BPF_LD_W_ABS, 0, 0, 4),                       # arch
        _SockFilter(_BPF_JMP_JEQ_K, 1, 0, _AUDIT_ARCH_X86_64),     # == x86_64 ? next : KILL
        _SockFilter(_BPF_RET_K, 0, 0, _SECCOMP_RET_KILL_PROCESS),
        _SockFilter(_BPF_LD_W_ABS, 0, 0, 0),                       # nr
    ]
    for number in sorted(calls.values()):
        filters.append(_SockFilter(_BPF_JMP_JEQ_K, 0, 1, number))   # equal → next, else skip 1
        filters.append(_SockFilter(_BPF_RET_K, 0, 0, _SECCOMP_RET_ERRNO | errno.EPERM))
    filters.append(_SockFilter(_BPF_RET_K, 0, 0, _SECCOMP_RET_ALLOW))

    array = (_SockFilter * len(filters))(*filters)
    program = _SockFprog(len=len(filters), filter=array)
    libc = _libc()
    if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise SandboxError(f"prctl(NO_NEW_PRIVS) 失败：errno={ctypes.get_errno()}")
    if libc.prctl(_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER, ctypes.byref(program), 0, 0) != 0:
        raise SandboxError(f"prctl(SET_SECCOMP) 失败：errno={ctypes.get_errno()}")
    return {"backend": "seccomp", "denied_calls": sorted(calls)}


def apply_rlimits(*, memory_bytes: int, output_bytes: int, cpu_seconds: int) -> dict:
    """地址空间／单文件字节／CPU 秒的硬上限（软硬都设，子进程无法自己抬）。"""
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    resource.setrlimit(resource.RLIMIT_FSIZE, (output_bytes, output_bytes))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 10))
    soft_as = resource.getrlimit(resource.RLIMIT_AS)[0]
    return {"backend": "rlimits", "address_space_bytes": soft_as,
            "single_file_bytes": output_bytes, "cpu_seconds": cpu_seconds}


def apply_sandbox(*, read_only: list[str], read_write: list[str], memory_bytes: int,
                  output_bytes: int, cpu_seconds: int,
                  deny_calls: dict[str, int] | None = None,
                  require_signal_scope: bool = False) -> dict:
    """按顺序施加三层限制，返回实际生效的配置（进环境指纹）。

    顺序要紧：先 rlimits（还需读写文件），再 Landlock（此后只剩白名单），
    最后 seccomp（最严，装在最后以免影响前面的侦错信息）。
    """
    report = {"rlimits": apply_rlimits(memory_bytes=memory_bytes, output_bytes=output_bytes,
                                       cpu_seconds=cpu_seconds),
              "landlock": apply_landlock(read_only=read_only, read_write=read_write,
                                          require_signal_scope=require_signal_scope),
              "seccomp": apply_seccomp(deny_calls=deny_calls)}
    if report["landlock"]["backend"] != "landlock":
        raise SandboxError("Landlock 未生效")
    return report


__all__ = ["SandboxError", "landlock_abi", "apply_landlock", "apply_seccomp", "apply_rlimits",
           "apply_sandbox", "DENY_NETWORK_CALLS", "DENY_NETWORK_CALLS_MINIMAL",
           "DENY_PROCESS_ESCAPE_CALLS", "SCOPE_SIGNAL",
           "RO", "RW", "FS_EXECUTE", "FS_READ_FILE", "FS_READ_DIR", "FS_WRITE_FILE"]
