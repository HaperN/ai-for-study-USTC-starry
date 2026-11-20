"""T07 渲染策略：**唯一一份**上限、字体、画质与隔离口径。

为什么单独一个模块：这些数字同时被三处读到 ——

* `aiforstudy.blocks.visual.worker`（宿主侧：分配超时／内存／输出上限、选隔离后端）；
* `run_scene.py`＋`scene_builder.py`（沙箱内：真正渲染的画质与字体）；
* `probe_env.py`（环境指纹：把"实际用的字体／工具版本"记进 `AnimationResult.environment_hash`）。

三处各写一份数字，就会出现"预检说 2GiB、实际给了 4GiB"这种**没法审计**的分裂。
所以上限只写在这里，`config/capabilities.json` 只声明**位置**（解释器、worker 目录），不重复数字。

计划 §6.1 的上限（工程上限，不是速度承诺）：

| 项 | 值 |
|---|---|
| draft 超时 | 120 s |
| final 超时 | 300 s |
| 每 worker CPU | 2 |
| 每 worker 内存 | 2 GiB |
| 输出上限 | 128 MiB |
| 同一 run 并发 Manim worker | 1 |
| final | 1280×720 / 30 fps / H.264 / yuv420p / faststart / 无音轨 |
| draft | 640×360 / 15 fps |
"""
from __future__ import annotations
import sys

#: 画质档位 → 像素／帧率／超时。**只有 draft / final 两档**（§5.3）。
def _presentation_profiles():
    # Imported before sandbox activation. This is a fixed, shipped policy asset,
    # never a model-selected module or path. Both source and wheel layouts work.
    from pathlib import Path
    import importlib.util
    root=Path(__file__).resolve().parents[2]
    candidates=(root/'src/aiforstudy/blocks/media/presentation_policy.py',
                root.parent/'blocks/media/presentation_policy.py')
    path=next((p for p in candidates if p.is_file()),None)
    if path is None:
        raise RuntimeError('missing shared presentation_policy.py')
    spec=importlib.util.spec_from_file_location('aiforstudy_presentation_policy',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {key:dict(value) for key,value in module.RENDER_PROFILES.items()},path

PROFILES, PRESENTATION_POLICY_PATH = _presentation_profiles()

#: §6.1：每个 worker 2 CPU、2GiB RAM、输出 128MiB
CPU_CORES = 2
MEMORY_BYTES = 2 * 1024 ** 3
OUTPUT_BYTES = 128 * 1024 ** 2
#: 同一 run 同时最多 1 个 Manim worker（宿主侧用锁实现）
MAX_CONCURRENT_WORKERS = 1

#: 中文字体**固定一款**，名称／文件 hash 进环境指纹；不许静默回退（§5.4）
FONT_FAMILY = "Noto Sans CJK SC"
_DEFAULT_FONT_FILE = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"


def _locate_font(default=_DEFAULT_FONT_FILE, family="Noto Sans CJK SC"):
    """默认路径只适用于 Debian 系；不存在时用 fontconfig 找同一字族（用户 2026-10-20 定放宽）。
    找到的文件仍进环境指纹（hash），所以换字体文件会被如实记录，不是静默回退。"""
    import os
    import shutil
    import subprocess
    mac_font = os.path.expanduser("~/Library/Fonts/NotoSansCJKsc-Regular.otf")
    if sys.platform == "darwin" and os.path.isfile(mac_font):
        return mac_font
    if os.path.isfile(default):
        return default
    if shutil.which("fc-match"):
        try:
            found = subprocess.run(["fc-match", "-f", "%{file}", family], capture_output=True, text=True,
                                   timeout=10).stdout.strip()
            if found and os.path.isfile(found):
                return found
        except (OSError, subprocess.SubprocessError):
            pass
    return default


FONT_FILE = _locate_font()
FONT_LICENSE = "SIL Open Font License 1.1"
#: 720p 主标签目标字号下限（像素，实际画面量）
FONT_MIN_PIXELS = 24

#: Manim 自己的画质档（只影响 mobject 默认尺寸，像素由上面显式设定）
MANIM_QUALITY = {"draft": "low_quality", "final": "high_quality"}

#: 沙箱内可见的 TeX 根目录与 bin（TinyTeX 的 dvisvgm **不在 PATH**，必须显式加）。
#: 只挂 bin/ 不够：`texmf.cnf` 在 TinyTeX 根下，漏了它 latex 会
#: `Permission denied: ~/.TinyTeX/texmf.cnf`（实测），MathTex 整条链路起不来。
TEX_ROOT = "/usr/local/texlive" if sys.platform == "darwin" else "~/.TinyTeX"
TEX_BIN_DIR = "/Library/TeX/texbin" if sys.platform == "darwin" else "~/.TinyTeX/bin/x86_64-linux"
#: 本机 TinyTeX 把 `TEXMFHOME` 指到 `~/.TinyTeX/texmf-local`，而它只在 `TEXMFDBS`
#: （`!!` 前缀 = 只认 ls-R 索引）里 —— 往那儿放东西必须跑 `mktexlsr` 才找得到。
#: 所以这里**显式**把 `TEXMFHOME` 指到一个用户树，TinyTeX 发行版本体一个字节都不改。
TEXMFHOME_DIR = "~/texmf"
#: MathTex 用的**最小** TeX 模板：只依赖已确认存在的宏包 + preview（见 §环境证据）。
#: 为什么不用 Manim 默认模板：默认模板要 physics/calligra/wasysym 等 8 个本机没有的宏包，
#: 为了一屏公式把整套宏包装进用户 TeX 树是没必要的扩大。
#: ⚠️ 不要在这里写 `\documentclass`：Manim 的 `TexTemplate` 会自己加
#: `\documentclass[preview]{standalone}`，写两遍 latex 直接报
#: "Two \documentclass or \documentstyle commands"（实测踩过）。
MATH_TEX_PREAMBLE = (
    "\\usepackage{amsmath}\n"
    "\\usepackage{amssymb}\n"
    "\\usepackage{xcolor}\n")
#: MathTex 自检用的固定公式（**字面量**，不是任何模型文本）
MATH_TEX_SELFTEST = r"\frac{x_i^{2}}{2}+\sqrt{y_j}=u_{xx}"

#: worker 产物布局（相对输出目录）
VIDEO_NAME = "video.mp4"
POSTER_NAME = "poster.png"
KEYFRAME_DIR = "keyframes"
VTT_NAME = "captions.vtt"
TRANSCRIPT_NAME = "transcript.txt"
ENVIRONMENT_NAME = "environment.json"
RESULT_NAME = "worker_result.json"

#: 结果状态（与 AnimationResult 的 status 同一套词）
STATUSES = ("fulfilled", "needs_review", "blocked", "failed", "cancelled", "not_applicable")

#: 取消后的本地收尾窗口（§6.1：10s 内结束或标为远端未确认）
CANCEL_GRACE_SECONDS = 10.0
#: 超时后先 TERM、再 KILL 的宽限
TERM_GRACE_SECONDS = 5.0

#: 沙箱里被允许只读的路径（**逐条显式**，没有 `/`、没有 `$HOME`）。
#: `/proc` 只放开自省所需的那几个入口：整个 /proc 可读会顺手漏出同 UID 其他进程的
#: 环境（那里面才是真密钥），所以这里刻意不放开。
READ_ONLY_PATHS = (
    "/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32", "/etc", "/opt",
    "/usr/share/fonts",
    "/proc/self", "/proc/thread-self", "/proc/cpuinfo", "/proc/meminfo", "/proc/stat",
    "/proc/loadavg", "/proc/version", "/proc/filesystems", "/proc/mounts",
    "/sys/devices/system/cpu",
)
#: 可写路径：隔离的临时目录、输出目录、以及 /dev（`/dev/null` 等设备节点）
READ_WRITE_PATHS = ("/dev",)
if sys.platform == "darwin":
    READ_ONLY_PATHS = ("/System", "/usr", "/bin", "/sbin", "/Library/TeX", "/Library/Fonts",
                       "/opt/homebrew", "/private/etc", "/private/var/db/timezone",
                       str(__import__("pathlib").Path(FONT_FILE).parent))
    READ_WRITE_PATHS = ("/dev/null", "/dev/zero", "/dev/random", "/dev/urandom")


def sandbox_paths(*, interpreter: str, worker_dir: str, scratch: str, output: str,
                  extra_read_only: tuple[str, ...] = ()) -> dict[str, list[str]]:
    """宿主与沙箱**共用**的白名单：bwrap 的挂载参数和 Landlock 的规则都从这一份来。"""
    import os as _os
    from pathlib import Path as _Path

    # ⚠️ 这里**不能** `resolve()`：venv 的 `bin/python` 是指向 `/usr/bin/python3.12` 的
    # 符号链接，resolve 之后"venv 根"会变成 `/usr`，于是 site-packages 一个都读不到
    # （实测错误：`PermissionError: ... /site-packages/manim/__init__.py`）。
    venv = _Path(interpreter).parent.parent
    read_only = [*READ_ONLY_PATHS, str(PRESENTATION_POLICY_PATH), str(venv), str(_Path(worker_dir).resolve()),
                 str(_Path(TEX_ROOT).expanduser()), str(_Path(TEX_BIN_DIR).expanduser()),
                 str(_Path(TEXMFHOME_DIR).expanduser()),
                 *_dev_tty(extra_read_only)]
    read_write = [str(_Path(scratch).resolve()), str(_Path(output).resolve()),
                  *READ_WRITE_PATHS]
    expanded = []
    for raw in read_only:
        path = _Path(_os.path.expanduser(raw))
        if str(path) not in expanded:
            expanded.append(str(path))
    return {"read_only": expanded, "read_write": [_os.path.expanduser(path)
                                                 for path in read_write]}


def _dev_tty(extra: tuple[str, ...]) -> list[str]:
    return [str(item) for item in extra]


def worker_env(*, interpreter: str, worker_dir: str, scratch: str,
               host_env: dict[str, str] | None = None) -> dict[str, str]:
    """沙箱内**最小**环境：没有 provider 密钥、没有宿主 PATH、HOME 指向临时目录。

    `host_env` 只用于取 LANG/TERM 一类的无害项 —— 调用方必须显式给白名单，
    这里不做"原样继承宿主环境"这种等于没隔离的事。
    """
    from pathlib import Path

    venv_bin = str(Path(interpreter).absolute().parent)
    platform_bins = ":/opt/homebrew/bin:/usr/local/bin" if sys.platform == "darwin" else ""
    scratch_path = Path(scratch).resolve()
    env = {
        "HOME": str(scratch_path / "home"),
        "TMPDIR": str(scratch_path / "tmp"),
        "XDG_CACHE_HOME": str(scratch_path / "cache"),
        "XDG_DATA_HOME": str(scratch_path / "data"),
        "XDG_CONFIG_HOME": str(scratch_path / "config"),
        "TEXMFVAR": str(scratch_path / "texmf-var"),
        # TEXMFHOME 指向本机用户树（只读）：MathTex 的最小宏包（preview）在那里，
        # 发行版目录不动。TEXMFVAR/TEXMFCACHE 才需要可写。
        "TEXMFHOME": str(Path(TEXMFHOME_DIR).expanduser()),
        "TEXMFCACHE": str(scratch_path / "texmf-cache"),
        "TEXMFCONFIG": str(scratch_path / "texmf-config"),
        "MPLCONFIGDIR": str(scratch_path / "mpl"),
        "PATH": f"{venv_bin}:{Path(TEX_BIN_DIR).expanduser()}:/usr/bin:/bin{platform_bins}",
        "PYTHONPATH": str(Path(worker_dir).resolve()),
        "PYTHONDONTWRITEBYTECODE": "1",
        # 沙箱里的可写根：Manim 的默认 media_dir 是 `<cwd>/media`，而 cwd 可能是只读的
        # 可信代码目录。所有需要落盘的动作都必须走这个目录（probe_env 也读它）。
        "MANIM_WORKER_SCRATCH": str(scratch_path),
        "PYTHONHASHSEED": "0",
        "PYTHONUNBUFFERED": "1",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "OMP_NUM_THREADS": str(CPU_CORES),
        "OPENBLAS_NUM_THREADS": str(CPU_CORES),
        "MKL_NUM_THREADS": str(CPU_CORES),
        "NUMEXPR_NUM_THREADS": str(CPU_CORES),
    }
    if sys.platform == "darwin":
        env["LANG"] = env["LC_ALL"] = "en_US.UTF-8"
        native = Path(interpreter).absolute().parent.parent / "native" / "lib"
        if native.is_dir():
            env["DYLD_LIBRARY_PATH"] = str(native)
        from xml.sax.saxutils import escape
        fonts = scratch_path / "fonts.conf"
        fonts.write_text('<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd"><fontconfig><dir>' + escape(str(Path(FONT_FILE).parent)) + '</dir><dir>/System/Library/Fonts</dir><cachedir>' + escape(str(scratch_path / "font-cache")) + '</cachedir></fontconfig>', encoding="utf-8")
        env["FONTCONFIG_FILE"] = str(fonts)
    for key in ("TERM",):
        if host_env and host_env.get(key):
            env[key] = str(host_env[key])
    return env


__all__ = ["PROFILES", "CPU_CORES", "MEMORY_BYTES", "OUTPUT_BYTES", "MAX_CONCURRENT_WORKERS",
           "FONT_FAMILY", "FONT_FILE", "FONT_LICENSE", "FONT_MIN_PIXELS", "MANIM_QUALITY",
           "TEX_ROOT", "TEX_BIN_DIR", "TEXMFHOME_DIR", "MATH_TEX_PREAMBLE", "MATH_TEX_SELFTEST", "VIDEO_NAME", "POSTER_NAME", "KEYFRAME_DIR",
           "VTT_NAME", "TRANSCRIPT_NAME", "ENVIRONMENT_NAME", "RESULT_NAME", "STATUSES",
           "CANCEL_GRACE_SECONDS", "TERM_GRACE_SECONDS", "READ_ONLY_PATHS",
           "READ_WRITE_PATHS", "sandbox_paths", "worker_env"]
