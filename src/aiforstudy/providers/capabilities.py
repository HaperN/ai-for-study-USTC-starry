"""能力预检：**"这台机器上恰好装了什么"不是能力**。

方案 §8：

> 给模型的可用能力来自**预检结果**；未安装的后端不作为可选项。
> 必需图无法表达或依赖缺失时 blocked，**不能静默换成与教学含义不同的图**。

方案 §15 A26：

> 缺浏览器/Node/必需后端或缓存回放 miss → release/replay 明确 `blocked`，
> **不用 skipped 当通过**；不得自动调用真实服务补回放。

所以 `probe()` 的产物是一个 `Capabilities` 值对象：每个能力有 `available` 与
`detail`（真实探测到的版本或失败原因）。调用方按它**决定能不能跑**，
而不是"试着跑，失败了再说"。
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..core.workspace import Workspace
from ..errors import CapabilityError


@dataclass(frozen=True)
class Capability:
    name: str
    kind: str                 # python | binary
    available: bool
    detail: str = ""
    probe: tuple[str, ...] = ()
    required_for: tuple[str, ...] = ()
    # Only probes with transient diagnostics supply a stable replacement.
    # Other details (including package/tool versions) remain identity-bearing.
    identity_detail: str | None = None

    def as_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "available": self.available,
                "detail": self.detail, "probe": list(self.probe),
                "required_for": list(self.required_for)}

    def identity_dict(self) -> dict:
        result = self.as_dict()
        if self.identity_detail is not None:
            result["detail"] = self.identity_detail
        return result


@dataclass(frozen=True)
class Capabilities:
    items: tuple[Capability, ...] = ()

    def by_name(self) -> dict[str, Capability]:
        return {c.name: c for c in self.items}

    def available_names(self) -> list[str]:
        return sorted(c.name for c in self.items if c.available)

    def missing_for(self, purpose: str) -> list[Capability]:
        return [c for c in self.items if purpose in c.required_for and not c.available]

    def require(self, purpose: str) -> None:
        """某个用途需要的能力缺了就**明确报错**（不是降级、不是跳过）。"""
        missing = self.missing_for(purpose)
        if missing:
            detail = "；".join(f"{c.name}（{c.detail}）" for c in missing)
            raise CapabilityError(
                f"用途 {purpose!r} 需要的能力不齐：{detail}。"
                f"方案 §15 A26：缺必需能力属环境阻断，不能算通过，也不静默降级。")

    def figure_backends(self) -> list[str]:
        """当前真正可用的图后端 —— 这个列表才允许交给模型当选项。

        ⚠️ 第一版把"任何 required_for 含 figure 的能力"都收了进来，于是返回的是
        `bin:pdflatex`、`python:matplotlib` 这些**依赖**，而不是后端名
        （被 `tests/integration/test_m1_cli_offline.py` 当场抓住）。
        后端是 `kind == "figure_backend"` 的那一组。
        """
        return sorted(c.name.split(":", 1)[1] for c in self.items
                      if c.kind == "figure_backend" and c.available)

    def render_backends(self) -> list[str]:
        """当前真正可用的**动画**后端（T07）。缺任何一项都不出现在这个列表里。"""
        return sorted(c.name.split(":", 1)[1] for c in self.items
                      if c.kind == "render_backend" and c.available)

    def as_dict(self) -> dict:
        return {"items": [c.as_dict() for c in self.items],
                "available": self.available_names(),
                "figure_backends": self.figure_backends(),
                "render_backends": self.render_backends()}

    def identity_dict(self) -> dict:
        """Cache inputs, separate from the full per-run diagnostic report."""
        return {**self.as_dict(), "items": [c.identity_dict() for c in self.items]}


def _probe_python(module: str) -> Capability:
    spec = None
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, ValueError):
        spec = None
    if spec is None:
        return Capability(name=f"python:{module}", kind="python", available=False,
                          detail="import 不到", probe=(f"import {module}",))
    version = ""
    try:
        # ⚠️ 优先走 importlib.metadata：直接读 `mod.__version__` 会触发
        # `jsonschema` 一类的 DeprecationWarning（模块级 `__version__` 正在被弃用）。
        # 探测能力不该在别人的日志里制造噪音 —— 噪音多了就没人看真告警了。
        import importlib.metadata as _md
        version = _md.version(module)
    except Exception:                       # noqa: BLE001
        try:                                # 退路：老包只有 __version__
            import warnings
            mod = __import__(module)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                version = str(getattr(mod, "__version__", "") or "")
        except Exception as exc:            # noqa: BLE001
            version = f"（取版本失败：{type(exc).__name__}）"
    return Capability(name=f"python:{module}", kind="python", available=True,
                      detail=version or "已安装", probe=(f"import {module}",))


def env_override_name(binary: str) -> str:
    """显式指定某个外部二进制的环境变量名：`blender` → `AIFORSTUDY_BLENDER`。

    为什么要有它：兜底位置之外还得有一条"用户说在哪就在哪"的路，而且
    **预检与后端必须都认同一条**（`three.py` 的 `blender_path()` 就是查这个名字）。
    只看后端认、预检不认，就又会退回到"预检说不可用、后端却跑得起来"。
    """
    return "AIFORSTUDY_" + binary.upper().replace("-", "_").replace(".", "_")


def _probe_binary(name: str, args: tuple[str, ...], fallback_paths: tuple[str, ...] = ()) -> Capability:
    """探测一个外部二进制：**PATH 优先**，其次配置里声明的兜底位置。

    ⚠️ 2026-10-06（A01）：这里原来只看 `shutil.which`，而 `three` 后端却写死
    `~/.local/opt/blender/blender` —— 两边口径不一致，于是出现"预检说依赖齐备、
    一画就 RuntimeError"。现在兜底位置也**由配置声明**（`fallback_paths`），
    后端实现调同一个 `binary_fallback_paths()`：预检说可用就必须真能跑。
    """
    exe = os.environ.get(env_override_name(name)) or shutil.which(name)
    via_fallback = ""
    if not exe:
        for candidate in fallback_paths:
            path = Path(str(candidate)).expanduser()
            if path.is_file():
                exe, via_fallback = str(path), str(path)
                break
    if not exe:
        return Capability(name=f"bin:{name}", kind="binary", available=False,
                          detail="PATH 里没有", probe=(name, *args))
    try:
        proc = subprocess.run([exe, *args], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=20)
        first = proc.stdout.decode("utf-8", "replace").strip().splitlines()
        detail = (first[0][:120] if first else f"退出码 {proc.returncode}")
    except (OSError, subprocess.SubprocessError) as exc:
        return Capability(name=f"bin:{name}", kind="binary", available=False,
                          detail=f"执行失败：{type(exc).__name__}", probe=(name, *args))
    if via_fallback:
        detail = f"{detail}（来自配置里声明的位置 {via_fallback}）"
    return Capability(name=f"bin:{name}", kind="binary", available=proc.returncode == 0,
                      detail=detail, probe=(name, *args))


def binary_fallback_paths(workspace: Workspace, name: str) -> list[str]:
    """配置里为某个二进制声明的兜底位置（`binaries.<name>.fallback_paths`）。

    单独一个函数是为了**只有一份口径**：能力预检与后端实现（例如
    `blocks/figures/backends/three.py` 找 Blender）都调它 ——
    否则又会出现"预检说不可用、后端却跑得起来"这种两边不一致的假绿/假红。
    读不到配置（没有工作区）就返回空：只认 PATH，不猜。
    """
    cfg = workspace.config_json("capabilities.json")
    spec = (cfg.get("binaries") or {}).get(name) or {}
    return [str(p) for p in (spec.get("fallback_paths") or [])]


# ── T07：渲染环境（独立 venv／隔离／TeX／字体）───────────────────────────────
def render_environment_config(workspace: Workspace) -> dict:
    """`config/capabilities.json` 的 `render_environment` 段（读不到就返回空）。"""
    cfg = workspace.config_json("capabilities.json")
    return dict(cfg.get("render_environment") or {})


# ── T08：视觉评审的**图片输入**能力 ─────────────────────────────────────────
#: 能力名与种类。`Capabilities.require("visual_review")` 就靠它挡人。
IMAGE_INPUT_NAME = "review:image-input"
IMAGE_INPUT_KIND = "model_input"


def probe_image_input(reviewer, *, name: str = IMAGE_INPUT_NAME) -> Capability:
    """探测一个评审传输**是否真的接收图片输入**（接口能力，**不联网、不计费**）。

    为什么这是"能力"而不是"实现细节"：改造前的独立评审输入是**规格 JSON**，
    于是"审核通过"在"学生看得懂"这件事上什么也没证明（§1.3）。判据只有一条 ——
    接口是否显式接收 `images`（`frame_check.accepts_images`）。一个
    `def reviewer(*, frames, steps, spec)` 的替身，无论名字里写多少遍"视觉"，
    都拿不到图片字节，所以它**不可用**；`require()` 会抛 `CapabilityError`，
    而不是"静默退成文本评审"（§15 A26）。
    """
    # 懒导入：`providers.capabilities` 在模块顶部 import `blocks.visual` 会把
    # `providers.gateway` → `pipeline.budget` → `pipeline/__init__` 的导入顺序搅乱
    # （与 `_probe_sandbox` 里的 worker 懒导入同一条纪律）。
    from ..blocks.visual.frame_check import accepts_images

    if reviewer is None:
        return Capability(name=name, kind=IMAGE_INPUT_KIND, available=False,
                          detail="没有评审传输", probe=("images",),
                          required_for=("visual_review",))
    if accepts_images(reviewer):
        return Capability(
            name=name, kind=IMAGE_INPUT_KIND, available=True,
            detail=(f"{type(reviewer).__name__} 显式接收 images —— "
                    f"真实图片 bytes/hash 会随请求发出"),
            probe=("images",), required_for=("visual_review",))
    return Capability(
        name=name, kind=IMAGE_INPUT_KIND, available=False,
        detail=("评审接口不接收 images（只吃 JSON／消息文本）—— "
                "拿规格审核冒充看过图正是要治的病"),
        probe=("images",), required_for=("visual_review",))


def with_image_input(capabilities: Capabilities, reviewer, *,
                     name: str = IMAGE_INPUT_NAME) -> Capabilities:
    """把某个评审的**图片输入能力**并进一份预检结果。

    之后 `capabilities.require("visual_review")` 就是那句 capability 检查；
    并把结果写进能力报告（`as_dict()`），让"这次评审为什么不算数"可复核。
    """
    probe = probe_image_input(reviewer, name=name)
    items = tuple(c for c in capabilities.items if c.name != name) + (probe,)
    return Capabilities(items=items)


def require_image_input(reviewer, *, purpose: str = "visual_review") -> Capability:
    """评审前的能力检查：不接收图片输入的评审**当场失败**。"""
    capability = probe_image_input(reviewer)
    if not capability.available:
        raise CapabilityError(
            f"用途 {purpose!r} 需要图片输入能力，实际不可用：{capability.detail}。"
            f"方案 §15 A26：缺必需能力属环境阻断，**不能**静默退成文本评审、"
            f"也不能算通过。")
    return capability


def _expand_candidate(template: str, workspace: Workspace) -> str:
    """展开候选路径里的模板变量。**只有这几个变量**，不做 shell 展开、不猜。"""
    home = Path.home()
    # ⚠️ 必须 resolve：`Path(".").parent` 还是 `Path(".")`，于是
    # `{workspace_parent}/.venv-manim-linux` 会展开成 `./.venv-...` 而找不到环境（实测）。
    root = Path(workspace.root).resolve()
    values = {
        "workspace": str(root),
        "workspace_parent": str(root.parent),
        "home": str(home),
    }
    text = str(template)
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    marker = "{env:"
    while marker in text:
        start = text.index(marker)
        end = text.index("}", start)
        name = text[start + len(marker):end]
        text = text[:start] + os.environ.get(name, "") + text[end + 1:]
    return os.path.expanduser(text)


def resolve_manim_interpreter(workspace_root: str | Path | None = None) -> str:
    """解析独立渲染解释器的**真实**路径（第一个存在的候选）。

    为什么不能只写一个个人绝对路径：环境是"可重建"的（计划 §8 T07），
    位置必须由配置声明 + 环境变量可覆盖，否则换台机器就得改代码。
    """
    if workspace_root is None:
        return ""
    workspace = Workspace(root=Path(workspace_root).resolve())
    config = render_environment_config(workspace)
    candidates: list[str] = []
    env_name = str(config.get("interpreter_env") or "AIFORSTUDY_MANIM_PYTHON")
    if os.environ.get(env_name):
        candidates.append(os.environ[env_name])
    candidates += [str(item) for item in (config.get("interpreter_candidates") or [])]
    for candidate in candidates:
        path = _expand_candidate(candidate, workspace)
        if path and Path(path).is_file():
            return str(Path(path))
    return ""


def _probe_python_env(name: str, spec: dict, workspace: Workspace) -> Capability:
    """探测一个**独立解释器环境**：它自己跑 `import` 并把版本打回来。

    为什么不复用 `_probe_python`：那是**本进程**的 `find_spec`。渲染环境刻意与
    项目 venv 分开（计划 §8：不要把 Manim 装进项目环境），用本进程的导入去找
    Manim 只会得到"没装"这种**错误结论**。
    """
    namespace = f"env:{name}"
    env_name = str(spec.get("interpreter_env") or "AIFORSTUDY_MANIM_PYTHON")
    interpreter = os.environ.get(env_name) or ""
    if not interpreter:
        for candidate in (spec.get("interpreter_candidates") or []):
            path = _expand_candidate(str(candidate), workspace)
            if path and Path(path).is_file():
                interpreter = path
                break
    if not interpreter or not Path(interpreter).is_file():
        return Capability(name=namespace, kind="python_env", available=False,
                          detail="独立解释器不存在（候选都不在）",
                          probe=tuple(spec.get("interpreter_candidates") or ()))
    modules = [str(item) for item in (spec.get("modules") or [])]
    program = ("import importlib.metadata as m\n"
               + "\n".join(f"import {module}" for module in modules)
               + "\nimport json\n"
               + "print(json.dumps({name: m.version(name) for name in "
               + repr([str(item) for item in (spec.get("distributions") or modules)])
               + "}))")
    native = Path(interpreter).absolute().parent.parent / "native" / "lib"
    env = dict(os.environ)
    if __import__("sys").platform == "darwin" and native.is_dir():
        env["DYLD_LIBRARY_PATH"] = str(native)
    try:
        proc = subprocess.run([interpreter, "-c", program], env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return Capability(name=namespace, kind="python_env", available=False,
                          detail=f"探测失败：{type(exc).__name__}", probe=(interpreter,))
    text = proc.stdout.decode("utf-8", "replace").strip()
    if proc.returncode != 0:
        return Capability(name=namespace, kind="python_env", available=False,
                          detail=f"import 失败（退出码 {proc.returncode}）：{text[-160:]}",
                          probe=(interpreter,))
    return Capability(name=namespace, kind="python_env", available=True,
                      detail=f"{interpreter} → {text[-160:]}", probe=(interpreter,))


def _probe_font(name: str, spec: dict) -> Capability:
    """字体能力：文件必须在、且**家族名与文件**都记下来（不许静默回退）。"""
    namespace = f"font:{name}"
    family = str(spec.get("family") or name)
    paths = [str(item) for item in (spec.get("paths") or [])]
    for raw in paths:
        path = Path(os.path.expanduser(raw))
        if path.is_file():
            detail = f"{family} @ {path}（{path.stat().st_size} 字节）"
            return Capability(name=namespace, kind="font", available=True, detail=detail,
                              probe=(family, str(path)))
    return Capability(name=namespace, kind="font", available=False,
                      detail=f"字体文件不存在：{paths}（家族 {family}）", probe=(family,))


def _probe_sandbox(name: str, spec: dict, workspace: Workspace) -> Capability:
    """隔离能力：**真跑一次** worker 的隔离自检，而不是"看 bwrap 在不在"。

    bwrap 优先；bwrap 不可用时用 Landlock+seccomp+rlimits（同样非特权、同样默认拒绝）。
    两者都不可用 → 该能力不可用 → 渲染用途在**收费前** blocked。
    """
    namespace = f"sandbox:{name}"
    worker_dir = resolve_worker_dir(spec, workspace)
    if worker_dir is None:
        return Capability(name=namespace, kind="sandbox", available=False,
                          detail="找不到 tools/manim_worker（worker 启动资产）")
    interpreter = resolve_manim_interpreter(workspace.root)
    if not interpreter:
        return Capability(name=namespace, kind="sandbox", available=False,
                          detail="缺独立渲染解释器，无法做隔离自检")
    try:
        from ..blocks.visual.worker import (RenderLimits, WorkerServices, environment_hash, probe_isolation,
                                            select_isolation)
    except Exception as exc:                                     # noqa: BLE001
        return Capability(name=namespace, kind="sandbox", available=False,
                          detail=f"装载 worker 模块失败：{type(exc).__name__}: {exc}")
    choice = select_isolation(worker_dir)
    if not choice.available:
        return Capability(name=namespace, kind="sandbox", available=False, detail=choice.detail)
    scratch = Path(os.environ.get("AIFORSTUDY_MANIM_SCRATCH", "/tmp")) / "aiforstudy-probe"
    services = WorkerServices(worker_dir=Path(worker_dir), interpreter=interpreter,
                              scratch_root=scratch)
    limits = RenderLimits(timeout_seconds=60.0, cpu_cores=1,
                          memory_bytes=512 * 1024 ** 2, output_bytes=8 * 1024 ** 2)
    control = [str(Path.home() / ".bashrc")]
    try:
        probe = probe_isolation(services=services, limits=limits, probe_paths=control)
    except (OSError, subprocess.SubprocessError) as exc:
        return Capability(name=namespace, kind="sandbox", available=False,
                          detail=f"隔离自检无法启动：{type(exc).__name__}: {exc}")
    isolation = probe.get("isolation") or {}
    from ..core.digest import digest_file, digest_of
    # Keep actual applied policy, interpreter/control targets and worker source
    # identity. Only the job's temporary paths and run diagnostics are excluded;
    # the real isolation probe above still executes on every preflight.
    identity = digest_of({
        "backend": choice.backend, "interpreter": interpreter,
        "control_paths": control, "safe": isolation.get("safe"),
        "environment": environment_hash(probe),
        "worker_assets": {str(path.relative_to(worker_dir)): digest_file(path)
                          for path in sorted(Path(worker_dir).rglob("*.py"))},
    })
    if not isolation.get("safe"):
        return Capability(name=namespace, kind="sandbox", available=False,
                          detail=f"隔离自检未通过：{isolation.get('reason') or '未知'}",
                          identity_detail=f"{choice.backend}；隔离自检未通过：{isolation.get('reason') or '未知'}；{identity}")
    return Capability(name=namespace, kind="sandbox", available=True,
                      detail=(f"{choice.backend}；哨兵不可读、网络不可达"
                              f"（{'，'.join(str(path) for path in probe['probe_paths'])}）"),
                      identity_detail=f"{choice.backend}；哨兵不可读、网络不可达；{identity}")


def resolve_worker_dir(spec: dict, workspace: Workspace) -> Path | None:
    """`render_environment.worker_dir` 声明的 worker 资产目录（相对工作区解析）。"""
    from ..blocks.visual.worker import resolve_worker_dir as resolve_assets
    from ..errors import ConfigError
    raw = spec.get("worker_dir")
    explicit = None
    if raw:
        explicit = Path(str(raw)).expanduser()
        if not explicit.is_absolute():
            explicit = Path(workspace.root) / explicit
    try:
        return resolve_assets(explicit=explicit, workspace_root=workspace.root)
    except ConfigError:
        return None


def probe(workspace: Workspace) -> Capabilities:
    """按 `config/capabilities.json` 探测。配置驱动，代码里不写死依赖清单。"""
    cfg = workspace.config_json("capabilities.json")
    items: list[Capability] = []

    for module, spec in (cfg.get("python") or {}).items():
        cap = _probe_python(module)
        items.append(Capability(
            name=cap.name, kind=cap.kind, available=cap.available, detail=cap.detail,
            probe=cap.probe, required_for=tuple(spec.get("required_for") or ())))

    for name, spec in (cfg.get("binaries") or {}).items():
        probe_args = tuple(str(x) for x in (spec.get("probe") or [name])[1:])
        cap = _probe_binary(name, probe_args,
                            tuple(str(p) for p in (spec.get("fallback_paths") or ())))
        items.append(Capability(
            name=cap.name, kind=cap.kind, available=cap.available, detail=cap.detail,
            probe=cap.probe, required_for=tuple(spec.get("required_for") or ())))

    # 图后端是**组合**能力：它需要的每一项都在 → 该后端可用
    for backend, spec in (cfg.get("figure_backends") or {}).items():
        needs = [str(x) for x in (spec.get("requires") or [])]
        have = {c.name for c in items if c.available}
        missing = [n for n in needs if n not in have]
        items.append(Capability(
            name=f"figure:{backend}", kind="figure_backend", available=not missing,
            detail=("依赖齐备" if not missing else f"缺 {missing}"),
            probe=tuple(needs), required_for=("figure",)))

    # ── T07：独立渲染环境（解释器／隔离／TeX／字体）─────────────────────────
    render_env = dict(cfg.get("render_environment") or {})
    for name, spec in (cfg.get("python_envs") or {}).items():
        cap = _probe_python_env(name, {**render_env, **spec}, workspace)
        items.append(Capability(
            name=cap.name, kind=cap.kind, available=cap.available, detail=cap.detail,
            probe=cap.probe, required_for=tuple(spec.get("required_for") or ())))

    for name, spec in (cfg.get("fonts") or {}).items():
        cap = _probe_font(name, spec)
        items.append(Capability(
            name=cap.name, kind=cap.kind, available=cap.available, detail=cap.detail,
            probe=cap.probe, required_for=tuple(spec.get("required_for") or ())))

    for name, spec in (cfg.get("sandboxes") or {}).items():
        merged = {**render_env, **spec}
        cap = _probe_sandbox(name, merged, workspace)
        items.append(Capability(
            name=cap.name, kind=cap.kind, available=cap.available, detail=cap.detail,
            probe=cap.probe, required_for=tuple(spec.get("required_for") or ()),
            identity_detail=cap.identity_detail))

    # 动画后端同样是**组合**能力；缺任何一项 → 该后端 blocked（**收费前**）
    for backend, spec in (cfg.get("render_backends") or {}).items():
        needs = [str(x) for x in (spec.get("requires") or [])]
        have = {c.name for c in items if c.available}
        missing = [n for n in needs if n not in have]
        items.append(Capability(
            name=f"render:{backend}", kind="render_backend", available=not missing,
            detail=("依赖齐备" if not missing else f"缺 {missing}"),
            probe=tuple(needs), required_for=tuple(spec.get("required_for") or ("render",))))

    return Capabilities(items=tuple(items))


__all__ = ["Capability", "Capabilities", "probe", "binary_fallback_paths", "env_override_name",
           "IMAGE_INPUT_NAME", "IMAGE_INPUT_KIND", "probe_image_input",
           "with_image_input", "require_image_input"]
