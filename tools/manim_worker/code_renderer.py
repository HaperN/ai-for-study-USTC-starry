"""Execute API-generated Python after run_scene has applied the OS sandbox.

This module is a worker asset, never imported by the host. Entry-point checks
are usability checks; Landlock/seccomp/bwrap are the security boundary.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys


def _module(path):
    spec = importlib.util.spec_from_file_location("aifs_generated_media", path)
    if spec is None or spec.loader is None:
        raise ValueError("cannot load generated media source")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def render(job):
    from aifs_media_runtime import _configure
    scratch = Path(job["scratch"])
    for name in ("tmp", "cache", "home", "mpl", "tex", "media"):
        (scratch / name).mkdir(parents=True, exist_ok=True)
    _configure(job["context"], job["program"].get("steps") or [], narration=job.get("narration"))
    if job["program"]["backend"] == "matplotlib":
        return _static(job)
    if job["program"]["backend"] == "manim":
        return _manim(job, still=job["context"].get("request", {}).get("kind") == "drawing")
    raise ValueError("unknown generated-media backend")


def _static(job):
    import matplotlib
    matplotlib.use("Agg", force=True)
    from matplotlib import font_manager, rcParams
    from matplotlib.figure import Figure
    from aifs_media_runtime import load_context

    pinned = Path(job["font"]["file"])
    if not pinned.is_file():
        raise RuntimeError("pinned Chinese font is unavailable")
    font_manager.fontManager.addfont(str(pinned))
    family = font_manager.FontProperties(fname=str(pinned)).get_name()
    # A builder may return from a local rc_context before we save its Figure.
    # Text retaining the generic family "sans-serif" is resolved at draw time,
    # so keep the pinned CJK face in the outer family list through both saves.
    rcParams.update({"font.family": family,
                     "font.sans-serif": [family, *rcParams["font.sans-serif"]],
                     "axes.unicode_minus": False,
                     "svg.fonttype": "path", "savefig.dpi": 160, "figure.dpi": 160})
    module = _module(job["source_path"])
    build = getattr(module, "build_figure", None)
    if not callable(build):
        raise ValueError("generated Matplotlib code must define build_figure(context)")
    figure = build(load_context())
    if not isinstance(figure, Figure):
        raise TypeError("build_figure(context) must return a matplotlib.figure.Figure")
    output = Path(job["output"])
    figure.savefig(output / "figure.png", format="png", dpi=160)
    figure.savefig(output / "figure.svg", format="svg", metadata={"Date": None})
    return {"backend": "matplotlib", "steps": [], "font": {"family": family, "file": str(pinned)}}


def _manim(job, *, still=False):
    import manim
    from manim import config
    from aifs_media_runtime import _finish
    from scene_builder import _font_probe, _pinned_font_check

    scratch = Path(job["scratch"])
    quality = job["limits"]
    config.media_dir = str(scratch / "media")
    config.tex_dir = str(scratch / "tex")
    config.pixel_width = int(quality["pixel_width"])
    config.pixel_height = int(quality["pixel_height"])
    config.frame_rate = int(quality["frame_rate"])
    config.frame_size = (config.pixel_width, config.pixel_height)
    config.background_color = "#ffffff"
    config.disable_caching = True
    config.preview = False
    config.progress_bar = "none"
    config.verbosity = "ERROR"
    config.write_to_movie = not still
    config.save_last_frame = still
    config.save_pngs = False
    config.output_file = "generated"
    config.movie_file_extension = ".mp4"
    config.renderer = "cairo"
    font = _font_probe(job["font"]["family"])
    _pinned_font_check(font, job["font"]["file"])
    manim.Text.set_default(font=job["font"]["family"], color="#172033")
    import policy
    manim.MathTex.set_default(tex_template=manim.TexTemplate(preamble=policy.MATH_TEX_PREAMBLE),
                              color="#172033")

    module = _module(job["source_path"])
    scene_type = getattr(module, "GeneratedScene", None)
    if not isinstance(scene_type, type) or not issubclass(scene_type, manim.Scene):
        raise TypeError("GeneratedScene must inherit a Manim Scene subclass")
    scene = scene_type()
    scene.render()
    if still:
        # Save the rendered camera pixels, not an SVG wrapper or a screenshot
        # of a browser. Static scenes need no recorded animation timeline.
        scene.renderer.camera.get_image().save(Path(job["output"]) / "figure.png")
        return {"backend": "manim", "steps": [], "font": font}
    steps = _finish(scene)
    raw = Path(scene.renderer.file_writer.movie_file_path).resolve()
    if not raw.is_relative_to(scratch.resolve()) or not raw.is_file():
        raise ValueError("GeneratedScene did not produce a video inside its scratch directory")
    output = Path(job["output"]) / "video.mp4"
    proc = subprocess.run([job["ffmpeg"], "-nostdin", "-v", "error", "-y",
        "-threads", "1", "-filter_threads", "1", "-i", str(raw), "-map", "0:v:0",
        "-map", "0:a:0?", "-c:v", "copy", "-c:a", "aac", "-ar", "24000", "-ac", "1", "-movflags", "+faststart", str(output)],
        capture_output=True, timeout=60)
    if proc.returncode:
        raise RuntimeError("video remux failed: " + proc.stderr.decode("utf-8", "replace")[-1200:])
    return {"backend": "manim", "steps": steps, "rendered_seconds": float(scene.renderer.time),
            "font": font}
