"""Host orchestration for generated Python; source runs only in the OS worker.

The host archives the original inputs outside the worker's writable paths,
reopens the actual media bytes, probes and decodes the video, and creates its
own captions and evidence frames. A technically valid result still requires
visual mathematical review: arbitrary Python is not a symbolic proof.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import uuid

from ...core.digest import digest_file, digest_of, digest_text
from ...errors import Cancelled, CapabilityError, ConfigError, ContractError
from ..visual.manim_adapter import default_services, limits_for_profile, faststart_ok
from ..visual.worker import environment_hash, file_resource, load_asset, probe_isolation, run_worker_job
from .presentation_policy import MAX_ANIMATION_SECONDS
from ...core.resource_files import local_resource_file
from ...core.review_mode import reviews_frozen, current_review_mode


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _validate_program(program, context):
    from .code_contracts import validate_media_code
    if not isinstance(context, dict):
        raise ContractError("generated context must be a JSON object")
    # The full approval receipt is checked by the caller. This boundary checks
    # the program against the frozen request without executing/importing source.
    program = validate_media_code(program, context.get("request"))
    if reviews_frozen():
        # Missing executable inputs are handled as ordinary worker errors.
        if isinstance(program,dict):
            program.setdefault('steps',[])
            program.setdefault('object_bindings',[])
            program.setdefault('backend','manim')
        return program
    for step in program["steps"]:
        if any(token in step["step_id"] for token in ("\n", "\r", "-->")) or step["step_id"].startswith("NOTE"):
            raise ContractError("step_id cannot contain a WebVTT control sequence")
    return program


def _safe_file(root, relative):
    # Raw worker outputs have no digest yet and must additionally be nonempty.
    try:
        return local_resource_file(root, str(relative), nonempty=True)
    except ContractError as exc:
        raise ValueError(str(exc)) from exc


def _run(argv, *, timeout=60):
    proc = subprocess.run(argv, capture_output=True, timeout=timeout)
    if proc.returncode:
        raise ValueError("media verification failed: " + proc.stderr.decode("utf-8", "replace")[-1600:])
    return proc.stdout


def _image(path, *, allow_blank=False):
    from PIL import Image, ImageStat
    with Image.open(path) as im:
        im.verify()
    with Image.open(path) as im:
        im.load()
        width, height = im.size
        if width * height > 40_000_000 or (not reviews_frozen() and min(width, height) < 16):
            raise ValueError("image dimensions are outside the display resource limits")
        variation = max(ImageStat.Stat(im.convert("RGB")).stddev)
        if variation < .2 and not allow_blank and not reviews_frozen():
            raise ValueError("rendered image is empty or uniformly blank")
        return {"width": width, "height": height, "variation": round(variation, 3)}


def _svg(path):
    raw = path.read_text(encoding="utf-8")
    # Matplotlib writes this fixed public DTD. It is unnecessary for display;
    # remove only that declaration, never accept arbitrary DTDs or entities.
    raw = raw.replace('<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN"\n'
                      '  "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">', '')
    from .svg_safety import validate_svg_source
    # Worker-facing validation retains its ValueError protocol; security rules
    # are exactly the same as the execution and acceptance consumers.
    try:
        return validate_svg_source(raw)
    except ContractError as exc:
        raise ValueError(str(exc)) from exc


def _publish(source, output, relative, kind, **metadata):
    original = _safe_file(source, relative)
    destination = output / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Refuse existing aliases rather than following them into unrelated files.
    if destination.is_symlink() or not destination.parent.resolve().is_relative_to(output.resolve()):
        raise ValueError("unsafe destination media path")
    shutil.copyfile(original, destination)
    return {**file_resource(destination, kind=kind, relative_path=relative), **metadata}


def _video(path, *, services, quality, narration=None, steps=None):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise CapabilityError("ffprobe is required for independent video verification")
    info = json.loads(_run([ffprobe, "-v", "error", "-count_frames", "-show_streams",
                           "-show_format", "-of", "json", str(path)]))
    videos = [row for row in info.get("streams", []) if row.get("codec_type") == "video"]
    if not videos:
        raise ValueError("animation has no video stream")
    audio_streams = [row for row in info.get('streams',[]) if row.get('codec_type')=='audio']
    if len(videos) != 1:
        raise ValueError('animation must contain exactly one video stream')
    stream = videos[0]
    numerator, denominator = str(stream.get("r_frame_rate", "0/1")).split("/")
    fps = float(numerator) / float(denominator)
    if not reviews_frozen() and (stream.get("codec_name") != "h264" or stream.get("pix_fmt") != "yuv420p"
            or stream.get("width") != quality["pixel_width"] or stream.get("height") != quality["pixel_height"]
            or abs(fps - quality["frame_rate"]) > .001):
        raise ValueError("encoded animation does not match the requested render profile")
    seconds = float(stream.get("duration") or info.get("format", {}).get("duration") or 0)
    count = int(stream.get("nb_read_frames") or 0)
    if not math.isfinite(seconds) or seconds <= 0 or count <= 0:
        raise ValueError("animation has no decodable frames")
    from .narration import audio_stream_problems, MAX_NARRATED_SECONDS
    audio_errors = audio_stream_problems(audio_streams,video_seconds=seconds,narration=narration,steps=steps)
    if audio_errors:
        raise ValueError('; '.join(audio_errors))
    if narration and not audio_streams:
        raise ValueError('host narration was requested but encoded video has no voice track')
    maximum = MAX_NARRATED_SECONDS if narration else MAX_ANIMATION_SECONDS
    if not reviews_frozen() and seconds > maximum:
        raise ValueError('animation exceeds its duration resource limit')
    if not reviews_frozen() and abs(count / fps - seconds) > .002:
        raise ValueError("encoded duration does not match decoded frame count")
    _run([services.ffmpeg, "-nostdin", "-v", "error", "-threads", "1", "-filter_threads", "1",
          "-i", str(path), "-map", "0:v:0", "-f", "null", "-"])
    if audio_streams:
        _run([services.ffmpeg,'-nostdin','-v','error','-i',str(path),'-map','0:a:0','-f','null','-'])
    if not reviews_frozen() and not faststart_ok(path):
        raise ValueError("animation is missing MP4 faststart metadata")
    return info, seconds, count


def _steps(program, recorded, *, seconds, fps):
    if reviews_frozen():
        declared={step.get('step_id'):step for step in (program.get('steps') or []) if isinstance(step,dict)}
        return [{**deepcopy(declared.get(step.get('step_id'),{})),**deepcopy(step),
                 'caption':declared.get(step.get('step_id'),{}).get('caption',''),
                 'evidence_moments':[],'checkpoints':[],'keyframes':[]}
                for step in (recorded or []) if isinstance(step,dict)]
    if not isinstance(recorded, list) or len(recorded) != len(program["steps"]):
        raise ValueError("media_step did not record every declared step")
    steps = []
    last = 0.0
    for declared, actual in zip(program["steps"], recorded):
        if not isinstance(actual, dict) or actual.get("step_id") != declared["step_id"]:
            raise ValueError("media_step order/identity differs from the declared program")
        start, end = actual.get("start"), actual.get("end")
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(end) or end <= start
                or abs(start - last) > .002 or end > seconds + .002):
            raise ValueError("media_step intervals must cover the decoded video in order")
        if any(abs(at * fps - round(at * fps)) > .02 for at in (start, end)):
            raise ValueError("media_step clock does not align with actual encoded frames")
        # WebVTT and the ffmpeg seek receipts use milliseconds. Keep one common
        # resolution so a frame at floor(0.733333s) is still inside its step;
        # the unquantized renderer clock remains in the worker receipt above.
        start, end = (math.floor((at + 1e-9) * 1000) / 1000 for at in (start, end))
        middle = round((start + end) / 2, 6)
        moments = [{"phase": "before", "at": start}, {"phase": "observe", "at": middle},
                   {"phase": "after", "at": end}]
        steps.append({**deepcopy(declared), "kind": "change", "start": start, "end": end,
                      "evidence_moments": moments, "checkpoints": [], "keyframes": []})
        last = end
    if not steps or abs(last - seconds) > .002:
        raise ValueError("media_step total time differs from independently decoded video")
    return steps


def _stamp(value):
    millis = round(value * 1000)
    return f"{millis // 3600000:02d}:{millis // 60000 % 60:02d}:{millis // 1000 % 60:02d}.{millis % 1000:03d}"


def _frame(video, destination, at, ffmpeg):
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run([ffmpeg, "-nostdin", "-v", "error", "-y", "-threads", "1", "-filter_threads", "1",
          "-ss", f"{at:.3f}", "-i", str(video), "-frames:v", "1", "-update", "1", str(destination)])
    return _image(destination, allow_blank=True)


def _animation_resources(program, staging, output, summary, services, quality, technical):
    video = _safe_file(staging, "video.mp4")
    info, seconds, count = _video(video, services=services, quality=quality, narration=technical.get("narration"), steps=summary.get("steps"))
    fps = float(quality["frame_rate"])
    steps = _steps(program, summary.get("steps"), seconds=seconds, fps=fps)
    resources = [_publish(staging, output, "video.mp4", "mp4")]
    video = output / "video.mp4"
    frame_reports = []
    for ordinal, step in enumerate(steps):
        for index, moment in enumerate(step["evidence_moments"]):
            offset = -1/fps if moment["phase"] == "before" else 1/fps if moment["phase"] == "after" else 0
            sample = max(step["start"], min(step["end"] - 1/fps, moment["at"] + offset))
            # A nearest-ms round can move 7.966666... to 7.967, beyond the
            # final frame PTS: ffmpeg succeeds but produces no PNG. Seek from
            # the preceding millisecond and persist that exact extraction time.
            at = math.floor((sample + 1e-9) * 1000) / 1000
            relative = f"keyframes/step-{ordinal + 1:02d}-{index}.png"
            dimensions = _frame(video, output / relative, at, services.ffmpeg)
            resource = {**file_resource(output / relative, kind="keyframe", relative_path=relative),
                        "step_id": step["step_id"], "timestamp": at, "phase": moment["phase"]}
            resources.append(resource)
            step["keyframes"].append({"relative_path": relative, "sha256": resource["sha256"]})
            frame_reports.append({"relative_path": relative, "at_seconds": at, **dimensions})
    if not reviews_frozen() and (not frame_reports or max(frame["variation"] for frame in frame_reports) < .2):
        raise ValueError("all decoded evidence frames are empty or uniformly blank")
    poster_time = max(frame_reports, key=lambda row: row["variation"])["at_seconds"] if frame_reports else 0
    _frame(video, output / "poster.png", poster_time, services.ffmpeg)
    resources.append(file_resource(output / "poster.png", kind="poster"))
    vtt = ["WEBVTT", ""]
    transcript = []
    for step in steps:
        caption = str(step.get("display_markdown") or step.get("caption",''))
        if reviews_frozen(): caption=caption.replace('-->','→').replace('\n\n','\n')
        if not reviews_frozen() and ("-->" in caption or "\n\n" in caption):
            raise ValueError("step caption cannot contain a WebVTT separator")
        from ...checks.offline import webvtt_cue_text
        vtt.extend([step["step_id"], f"{_stamp(step['start'])} --> {_stamp(step['end'])}",
                    webvtt_cue_text(caption), ""])
        transcript.append(f"{step['step_id']}  {_stamp(step['start'])} — {_stamp(step['end'])}\n{caption}\n")
    (output / "captions.vtt").write_text("\n".join(vtt), encoding="utf-8")
    (output / "transcript.txt").write_text("\n".join(transcript), encoding="utf-8")
    resources.extend([file_resource(output / "captions.vtt", kind="captions"),
                      file_resource(output / "transcript.txt", kind="transcript")])
    if technical.get('narration'):
        from .narration import bind_narration_timeline
        technical['narration'] = bind_narration_timeline(technical['narration'], steps)
        _write_json(output/'source'/'narration.json', technical['narration'])
        receipt = output/'source'/'narration.json'
        resources.append(file_resource(receipt,kind='narration',relative_path='source/narration.json'))
        for voice in sorted((output/'source'/'narration').glob('*.wav')):
            resources.append(file_resource(voice,kind='audio',relative_path='source/narration/'+voice.name))
    technical.update(ffmpeg_decode_ok=True, duration_ok=True, size_ok=True, video_info=info,
                     decoded_frames=count, frame_checks={"status": "pass", "frames": frame_reports},
                     timing_source="Manim renderer.time; host verified against decoded frame count")
    if reviews_frozen():
        technical.update(duration_ok=None,size_ok=None,frame_checks={'status':'not_run','frames':[]},
                         timing_source='Manim renderer.time; timeline review frozen',review_status='not_run')
    return resources, steps


def _verified_isolation(isolation):
    from ..visual.worker import isolation_verified
    return isolation_verified(isolation)


def render_generated_code(program, *, context, out_dir, workspace_root=None, profile="draft", cancel=None, narration=None):
    """Run one generated program without ever evaluating generated Python on host."""
    if reviews_frozen(): context={**deepcopy(context),'review_mode':current_review_mode()}
    output = Path(out_dir).absolute()
    output.mkdir(parents=True, exist_ok=True)
    if output.is_symlink():
        raise ContractError("generated media output cannot be a symlink")
    output = output.resolve()
    source_dir = output / "source"
    source_dir.mkdir(exist_ok=True)
    if source_dir.is_symlink():
        raise ContractError("generated source archive cannot be a symlink")
    source = program.get("code", "") if isinstance(program, dict) else ""
    (source_dir / "generated.py").write_text(source, encoding="utf-8")
    _write_json(source_dir / "program.json", program)
    _write_json(source_dir / "context.json", context)
    technical = {"mathematical_status": "requires_visual_review", "cache_committed": False,
                 "deferred_checks": ["visual_mathematical_correspondence", "reader_size_review"],
                 "source_hash": digest_text(source), "program_hash": digest_of(program, check=False),
                 "context_hash": digest_of(context, check=False)}
    result = {"status": "failed", "reason": "", "resources": [], "steps": [],
              "technical_report": technical, "environment_hash": "", "compiler_hash": "",
              "source_dir": str(output)}
    started = time.monotonic()
    try:
        program = _validate_program(program, context)
        if not isinstance(context, dict):
            raise ContractError("generated context must be a JSON object")
        if cancel is not None and getattr(cancel, "cancelled", False):
            result.update(status="cancelled", reason="generated media cancelled before dispatch")
            return result
        narration_config = narration
        if narration_config is None and workspace_root:
            narration_file = Path(workspace_root)/'config'/'narration.json'
            if narration_file.is_file():
                narration_config = json.loads(narration_file.read_text(encoding='utf-8'))
        services = default_services(workspace_root=workspace_root, cancel=cancel)
        limits = limits_for_profile(services.worker_dir, profile)
        policy = load_asset(services.worker_dir, "policy")
        quality = policy.PROFILES[profile]
        narration_dependency={}
        if program['backend']=='manim' and context.get('request',{}).get('kind')=='animation':
            from .local_narration import narration_dependency_identity
            from ...pipeline.runtime_dependencies import CODE_NARRATION_COMPONENTS
            narration_dependency={'effective':narration_dependency_identity(narration_config),
                'components':{name:digest_file(Path(__file__).resolve().parents[2]/name) for name in CODE_NARRATION_COMPONENTS}}
        runtime_hash = digest_of({"host":digest_file(__file__),"narration":narration_dependency,
            "assets":{path.name:digest_file(path) for path in sorted(Path(services.worker_dir).glob("*.py"))}})
        result["compiler_hash"] = digest_of({"source": technical["source_hash"],
            "program": technical["program_hash"], "context": technical["context_hash"],
            "runtime": runtime_hash, "profile": profile})
        technical["runtime_hash"] = runtime_hash
        environment = probe_isolation(services=services.worker_services(), limits=limits,
            collect_environment=True, probe_paths=[str(Path.home() / ".bashrc")], cancel=cancel,
            require_signal_scope=True)
        result["environment_hash"] = environment_hash(environment, profile=profile)
        isolation = environment.get("isolation") or {}
        technical.update(isolation=isolation, environment=environment.get("environment", {}),
                         environment_fingerprint_path=environment.get("environment_path", ""))
        probe = environment.get("outcome") or {}
        if probe.get("status") != "ok":
            technical.update(worker=probe, phase="environment_probe")
            result.update(status="cancelled" if probe.get("status") == "cancelled" else "blocked" if probe.get("status") == "blocked" else "failed",
                          reason=probe.get("reason") or "isolated environment probe did not complete")
            return result
        if not _verified_isolation(isolation):
            result.update(status="blocked", reason="OS isolation was not independently verified")
            return result
        ident = "code-" + uuid.uuid4().hex[:12]
        job_root = Path(services.scratch_root).absolute() / ident
        scratch, staging = job_root / "scratch", job_root / "out"
        scratch.mkdir(parents=True)
        # The archive in output/source is outside the worker's writable roots.
        if output.is_relative_to(scratch) or output.is_relative_to(staging):
            raise ContractError("source archive overlaps the worker writable directory")
        narration_receipt = None
        if program['backend']=='manim' and context.get('request',{}).get('kind')=='animation' and narration_config and narration_config.get('enabled',True):
            from .narration import prepare_step_audio, configured_backend
            narration_receipt = prepare_step_audio(program['steps'], source_dir/'narration',
                backend=configured_backend(narration_config),ffmpeg_path=services.ffmpeg,cancel=cancel)
            # Archive host originals outside writable scratch; worker gets fixed copies only.
            voice_root=scratch/'narration';voice_root.mkdir()
            narration_receipt=deepcopy(narration_receipt)
            for voice in narration_receipt['steps']:
                target=voice_root/Path(voice['path']).name
                shutil.copyfile(voice['path'],target);voice['path']=str(target)
            technical['narration'] = {'source':narration_receipt['source'], 'steps':[
                {k:v for k,v in voice.items() if k!='path'} for voice in narration_receipt['steps']]}
            _write_json(source_dir/'narration.json',technical['narration'])
        generated = scratch / "generated.py"
        generated.write_text(program["code"], encoding="utf-8")
        job = {"schema": "manim-worker-job-1.0", "mode": "code", "profile": profile,
               "scratch": str(scratch), "output": str(staging), "source_path": str(generated),
               "context": deepcopy(context), "program": deepcopy(program), "limits": quality,
               "font": {"family": policy.FONT_FAMILY, "file": policy.FONT_FILE}, "ffmpeg": services.ffmpeg}
        if narration_receipt:
            job["narration"] = narration_receipt
        outcome = run_worker_job(services=services.worker_services(), job=job, limits=limits,
                                 job_id=ident, cancel=cancel)
        technical.update(worker=outcome, phase="render", limits={"timeout_seconds": limits.timeout_seconds,
            "cpu_cores": limits.cpu_cores, "memory_bytes": limits.memory_bytes, "output_bytes": limits.output_bytes})
        if outcome.get("status") != "ok":
            result.update(status="cancelled" if outcome.get("status") == "cancelled" else "blocked" if outcome.get("status") == "blocked" else "failed",
                          reason=outcome.get("reason") or outcome.get("terminated_by") or "isolated rendering failed")
            return result
        if cancel is not None and getattr(cancel, "cancelled", False):
            result.update(status="cancelled", reason="generated media cancelled before verification")
            return result
        technical["phase"] = "media_verification"
        if program["backend"] == "matplotlib":
            png, svg = _safe_file(staging, "figure.png"), _safe_file(staging, "figure.svg")
            technical["image_info"] = _image(png)
            normalized_svg = _svg(svg)
            svg_resource = _publish(staging, output, "figure.svg", "svg")
            (output / "figure.svg").write_text(normalized_svg, encoding="utf-8")
            svg_resource = file_resource(output / "figure.svg", kind="svg")
            result["resources"] = [svg_resource, _publish(staging, output, "figure.png", "image")]
            technical["image_decode_ok"] = True
        elif context.get('request', {}).get('kind') == 'drawing':
            png = _safe_file(staging, "figure.png")
            technical["image_info"] = _image(png)
            result["resources"] = [_publish(staging, output, "figure.png", "image")]
            technical["image_decode_ok"] = True
        else:
            result["resources"], result["steps"] = _animation_resources(program, staging, output,
                outcome.get("result") or {}, services, quality, technical)
        if reviews_frozen():
            result.update(status='rendered',reason='旧机器审核已冻结；真实制作完成，成品审核由调用方记录。',review_mode=current_review_mode())
            technical.update(mathematical_status='not_run',review_status='not_run')
        else:
            result.update(status="needs_review", reason="technical media checks passed; actual visual and mathematical review required")
        technical["phase"] = "complete"
    except Cancelled as exc:
        result.update(status="cancelled",reason=str(exc))
    except (CapabilityError, ConfigError) as exc:
        result.update(status="blocked", reason=str(exc))
    except (ValueError, TypeError, OSError, subprocess.SubprocessError, ContractError) as exc:
        result.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
    finally:
        technical["wall_seconds_total"] = round(time.monotonic() - started, 3)
        _write_json(source_dir / "runtime.json", result)
    return result


_PREFLIGHT_CODE = {
    "matplotlib": """from matplotlib.figure import Figure
def build_figure(context):
    figure = Figure(figsize=(4, 2.5))
    axes = figure.subplots()
    axes.plot([0, 1, 2], [0, 1, 4], color='#166caa')
    axes.set_title('中文字体：平方函数')
    return figure
""",
    "manim": r"""from manim import Scene, MathTex, Text, VGroup, DOWN, RIGHT
from aifs_media_runtime import media_step
class GeneratedScene(Scene):
    def construct(self):
        group = VGroup(MathTex(r'\frac{1}{2}+x^2'), Text('中文字体')).arrange(DOWN)
        self.add(group)
        with media_step(self, 'P1'):
            self.play(group.animate.shift(.25 * RIGHT), run_time=.4)
""",
}


_PREFLIGHT_STATIC_MANIM = r'''from manim import Scene, Axes, Text, MathTex, VGroup, DOWN, BLUE
class GeneratedScene(Scene):
    def construct(self):
        axes = Axes(x_range=[-2, 2, 1], y_range=[0, 4, 1], x_length=6, y_length=3,
                    axis_config={"color": "#61738A"})
        curve = axes.plot(lambda x: x*x, x_range=[-2, 2], color=BLUE)
        labels = VGroup(Text("中文字体：平方函数", font_size=24),
                        MathTex("y=x^2", font_size=30)).arrange(DOWN)
        labels.next_to(axes, DOWN, buff=.2)
        self.add(axes, curve, labels)
'''


def preflight_generated_code(backend, *, kind=None, workspace_root=None, cancel=None):
    """Check the selected runtime once, before the caller pays for generation.

    Only these fixed host-authored probes run, through the same OS-isolated
    renderer and independent file checks used by the eventual model program.
    The caller owns the once-per-request placement; there is no global cache.
    Static Manim checks the real still-image path, including MathTex and fonts.
    The legacy Matplotlib probe stays available for replaying historical code.
    Probe artifacts remain available for diagnosis; they do not approve math.
    """
    if backend not in _PREFLIGHT_CODE:
        raise ContractError(f"unsupported generated-media backend: {backend}")
    if cancel is not None and getattr(cancel, "cancelled", False):
        raise Cancelled("generated media preflight cancelled before dispatch")
    kind = kind or ("drawing" if backend == "matplotlib" else "animation")
    request = {"request_id": "runtime-preflight", "kind": kind,
               "object_refs": [], "animation": {"steps": ["观察公式与中文字体"]}}
    steps = [] if kind == "drawing" else [{"step_id": "P1", "author_step_index": 0,
        "caption": "观察公式与中文字体", "observation": "公式和中文同时可见",
        "expected_change": "整组向右平移"}]
    program = {"schema_version": "GeneratedMediaCodeV1", "request_id": request["request_id"],
               "backend": backend,
               "code": _PREFLIGHT_STATIC_MANIM if backend == "manim" and kind == "drawing" else _PREFLIGHT_CODE[backend],
               "steps": steps, "object_bindings": []}
    result = render_generated_code(program,
        context={"request": request, "approved_content": {}, "approved_models": {}},
        out_dir=tempfile.mkdtemp(prefix="aiforstudy-code-preflight-"),
        workspace_root=workspace_root, profile="draft", cancel=cancel, narration={"enabled": False})
    report = {"status": "ok", "backend": backend, "kind": kind, "probe_kind": "fixed_host_source",
              "runtime": result}
    if result["status"] == "cancelled":
        raise Cancelled(result["reason"])
    reason = (result["reason"] or f"runtime self-test ended with {result['status']}") if result["status"] not in ("needs_review","rendered") else ""
    if not reason and not result["technical_report"].get("environment", {}).get("tools", {}).get("ffmpeg", {}).get("available"):
        reason = "ffmpeg is unavailable in the independent rendering environment"
    if reason:
        report["status"] = "blocked"
        report["reason"] = reason
        dependencies = ("Matplotlib / NumPy / Pillow / 固定中文字体" if backend == "matplotlib"
                        else "Manim / latex / dvisvgm / ffprobe / 固定中文字体")
        error = CapabilityError(f"{backend} preflight failed（独立解释器、ffmpeg、代码模式 OS 隔离、{dependencies}）: {reason}")
        error.report = report
        raise error
    from ...core.review_mode import substantive_reviews_enabled
    if substantive_reviews_enabled():
        # 修改计划 D5：成品审核要截图；买候选前在本机试拍一次，截图环境不可用就不买。
        from .validation import capture_preflight
        try:
            capture_preflight(tempfile.mkdtemp(prefix="aifs-capture-"), workspace_root=workspace_root)
            report["capture_preflight"] = "ok"
        except CapabilityError as exc:
            # 只记下来、照常制作：成品保留，截图环境好了之后恢复时只补截图与审核，不占新候选。
            report["capture_preflight"] = "failed: " + str(exc)[:500]
    return report


__all__ = ["render_generated_code", "preflight_generated_code"]
