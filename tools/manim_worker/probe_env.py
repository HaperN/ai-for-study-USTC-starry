"""环境探测：把"这台机器上真正用的东西"记成一份可哈希的指纹。

计划 §8 T07 明确：**不能只证明 `import manim` 成功**。所以这里逐项记录真实版本与路径，
并且真的做两件事：

1. **MathTex 自检**：构造一次 `MathTex`，逼出 latex → dvi → dvisvgm → svg 全链路。
   TinyTeX 的 `dvisvgm` 不在 PATH（`~/.TinyTeX/bin/x86_64-linux`），所以这一项同时
   证明了"worker 的 PATH 补对了"；失败就是 blocked，不是"以后再说"。
2. **字体指纹**：固定一款中文字体（`Noto Sans CJK SC`），记录**文件 sha256**、是否在
   manimpango 的家族列表里、以及 fontconfig 实际解析到的文件。三者不一致就是静默回退，
   直接失败（§5.4）。

宿主把这些字段（外加沙箱自检结果与上限）合成 `environment_hash`，写进
`AnimationResult.environment_hash`；指纹里**不放**时间／耗时／run_id 一类每次运行都变的字段
（`core.digest.FORBIDDEN_KEYS` 会在哈希时直接报错，这是故意的）。
"""
from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import policy

#: 进指纹的包（顺序固定，缺失记 None —— 不写"已安装"这种模糊话）
PACKAGES = ("manim", "pycairo", "ManimPango", "numpy", "pillow", "scipy", "networkx",
            "svgelements", "av", "matplotlib", "sympy")
#: 进指纹的外部工具
TOOLS = ("ffmpeg", "ffprobe", "latex", "dvisvgm")
TOOL_ARGS = {"ffmpeg": ["-version"], "ffprobe": ["-version"],
             "latex": ["--version"], "dvisvgm": ["--version"]}


def _run(argv: list[str], timeout: float = 30.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, f"{type(exc).__name__}: {exc}"
    return proc.returncode, proc.stdout.decode("utf-8", "replace").strip()


def _tool_report(name: str) -> dict:
    path = shutil.which(name)
    if not path:
        return {"name": name, "path": "", "available": False, "version": "PATH 里没有"}
    code, output = _run([path, *TOOL_ARGS.get(name, ["--version"])])
    first = output.splitlines()[0] if output else ""
    return {"name": name, "path": path, "available": code == 0, "version": first[:160]}


def _pkg_config(module: str) -> str:
    pkg_config = shutil.which("pkg-config")
    if not pkg_config:
        return ""
    code, output = _run([pkg_config, "--modversion", module])
    return output if code == 0 else ""


def _font_report(font_family: str, font_file: str) -> dict:
    report = {"family": font_family, "file": font_file, "license": policy.FONT_LICENSE,
              "sha256": "", "in_manimpango": False, "fc_match": ""}
    path = Path(font_file)
    if path.is_file():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        report["sha256"] = "sha256:" + digest
        report["bytes"] = path.stat().st_size
    try:
        import manimpango
        report["in_manimpango"] = font_family in set(manimpango.list_fonts())
    except Exception as exc:                                    # noqa: BLE001
        report["manimpango_error"] = f"{type(exc).__name__}: {exc}"
    fc_match = shutil.which("fc-match")
    if fc_match:
        code, output = _run([fc_match, "--format", "%{file}", font_family])
        report["fc_match"] = output if code == 0 else f"fc-match 失败：{output[:80]}"
    return report


def _math_tex_selftest() -> dict:
    """真的编译一次公式：latex → dvi → dvisvgm → svg。失败必须看得见。"""
    import os as _os
    from manim import MathTex, TexTemplate, Text, VGroup, Scene, DOWN, config
    scratch = Path(_os.environ.get("MANIM_WORKER_SCRATCH") or config.media_dir).resolve()
    # Manim 默认会把 INFO 日志打到 stdout —— 探测输出必须是**纯 JSON**，否则解析不了。
    config.verbosity = "ERROR"
    config.progress_bar = "none"
    config.disable_caching = True
    config.media_dir = str(scratch / "media")
    (scratch / "tex-selftest").mkdir(parents=True, exist_ok=True)
    config.tex_dir = str(scratch / "tex-selftest")
    try:
        template = TexTemplate(preamble=policy.MATH_TEX_PREAMBLE)
        mob = MathTex(policy.MATH_TEX_SELFTEST, tex_template=template, color="#172033")
        chinese = Text("观察分子、分母与上下标",font=policy.FONT_FAMILY,font_size=28,color="#172033")
        group=VGroup(mob,chinese).arrange(DOWN,buff=0.4)
        config.pixel_width=640;config.pixel_height=360;config.frame_rate=15
        config.background_color="#ffffff";config.write_to_movie=False
        config.save_last_frame=True;config.output_file="math-chinese-selftest"
        class TypographyProbe(Scene):
            def construct(self):
                self.add(group)
        scene=TypographyProbe();scene.render()
        frame_path=Path(scene.renderer.file_writer.image_file_path)
        frame_bytes=frame_path.read_bytes()
        import hashlib
        frame_hash='sha256:'+hashlib.sha256(frame_bytes).hexdigest()
        pixel_array=scene.renderer.get_frame()
        nonblank=bool((pixel_array[:,:,:3] < 220).any())
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}",
                "latex_ok": False, "svg_bytes": 0}
    svgs = sorted((scratch / "tex-selftest").rglob("*.svg"))
    return {"ok": len(mob.submobjects) > 0 and nonblank, "detail": "latex+dvisvgm+分数/上下标/中文实帧可用",
            "frame_path":str(frame_path),"frame_hash":frame_hash,"frame_bytes":len(frame_bytes),
            "mixed_text":"观察分子、分母与上下标","expression":policy.MATH_TEX_SELFTEST,
            "latex_ok": True, "svg_count": len(svgs),
            "svg_bytes": svgs[-1].stat().st_size if svgs else 0}


#: MathTex 需要的宏包文件：逐个 `kpsewhich` 出真实路径与 sha256（"声明并检测"，不是"应该有"）
TEX_PACKAGES = ("standalone.cls", "preview.sty", "amsmath.sty", "amssymb.sty", "xcolor.sty")


def _tex_package_report() -> dict:
    kpsewhich = shutil.which("kpsewhich")
    report: dict = {}
    if not kpsewhich:
        return {"kpsewhich": "", "note": "PATH 里没有 kpsewhich，无法核对宏包"}
    for name in TEX_PACKAGES:
        code, output = _run([kpsewhich, name])
        path = output if code == 0 and output else ""
        entry = {"path": path, "found": bool(path)}
        if path and Path(path).is_file():
            entry["sha256"] = "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()
        report[name] = entry
    return report


def collect(*, sandbox: dict | None = None, profile: str = "final",
            extra: dict | None = None) -> dict:
    """采集环境指纹。`sandbox` 由 `run_scene.py --selftest` 提供（同一次运行内）。"""
    quality = policy.PROFILES[profile]
    fingerprint = {
        "schema_version": "manim-env-1.0",
        "python": {"implementation": platform.python_implementation(),
                   "version": platform.python_version(),
                   "executable": sys.executable,
                   "byteorder": sys.byteorder},
        "packages": {},
        "native": {"cairo": _pkg_config("cairo"), "pango": _pkg_config("pango"),
                   "pixman": _pkg_config("pixman-1")},
        "tools": {name: _tool_report(name) for name in TOOLS},
        "font": _font_report(policy.FONT_FAMILY, policy.FONT_FILE),
        "tex_packages": _tex_package_report(),
        "math_tex": _math_tex_selftest(),
        "limits": {"profile": profile, "timeout_seconds": quality["timeout_seconds"],
                   "pixel_width": quality["pixel_width"],
                   "pixel_height": quality["pixel_height"],
                   "frame_rate": quality["frame_rate"],
                   "cpu_cores": policy.CPU_CORES, "memory_bytes": policy.MEMORY_BYTES,
                   "output_bytes": policy.OUTPUT_BYTES,
                   "max_concurrent_workers": policy.MAX_CONCURRENT_WORKERS},
        "sandbox": sandbox or {"state": "未探测"},
    }
    native = Path(sys.executable).absolute().parent.parent / "native" / "lib"
    fingerprint["native"]["private_libraries"] = {
        item.name: "sha256:" + hashlib.sha256(item.read_bytes()).hexdigest()
        for item in sorted(native.glob("*.dylib")) if item.is_file()
    } if sys.platform == "darwin" else {}
    for name in PACKAGES:
        try:
            fingerprint["packages"][name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            fingerprint["packages"][name] = None
    if extra:
        fingerprint["extra"] = extra
    return fingerprint


def main(argv: list[str]) -> int:
    profile = "final"
    output = ""
    sandbox_path = ""
    rest = list(argv)
    while rest:
        item = rest.pop(0)
        if item == "--profile":
            profile = rest.pop(0)
        elif item == "--out":
            output = rest.pop(0)
        elif item == "--sandbox":
            sandbox_path = rest.pop(0)
        else:
            print(f"未知参数 {item!r}", file=sys.stderr)
            return 2
    sandbox = None
    if sandbox_path and Path(sandbox_path).is_file():
        sandbox = json.loads(Path(sandbox_path).read_text(encoding="utf-8"))
    payload = collect(sandbox=sandbox, profile=profile)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if output:
        Path(output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":                                      # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
