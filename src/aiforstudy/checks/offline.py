"""交付前检查：**离线打开、丢图、外部引用、成员哈希**。

方案 §12 的最后一段就是本模块的规格书：

> 最终验收对象是**输出文件本身**：把单 HTML 复制到空目录，ZIP 解压到另一空目录，
> **阻断网络后打开**；验证所有图加载、公式渲染、目录/搜索/概念/详解/例题卡、
> 交互参数和重置。检查**运行时异常、外部资源请求**、页面尺寸与截图证据。
> **必需浏览器/Node/相关后端缺失属环境阻断，不能用 skipped 算正式通过。**

方案 §15 的三条验收项：

* **A23**：注入页面坏引用、缺 PNG、坏公式、动态 Three 缺失 → 检查非零，**包不发布**；
* **A24**：单 HTML 复制空目录、ZIP 另处解压、断网打开并操作 →
  **外部资源请求 = 0、丢图 = 0、未捕获异常 = 0**；ZIP 成员 hash 与 manifest 100% 一致；
* **A26**：缺浏览器/Node/必需后端或缓存回放 miss → release/replay 明确 `blocked`，
  **不用 skipped 当通过**。

## 为什么这些检查必须"看文件本身"

"页面在仓库里能打开"与"交付出去能打开"是两件不同的事：仓库里有未打包的资源、
有相对的目录结构、有网络、还有我们自己的服务器。所以这里只接受
**把产物复制/解压到空目录**之后的形态，并且**禁止网络**。

静态检查与真实 Chromium 执行都针对隔离副本；页面网络请求被阻断，
逐单元运行图、公式与交互。缺浏览器或检查未执行时明确 `blocked`。
"""
from __future__ import annotations

import re
import json
import math
import os
import shutil
import subprocess
import tempfile
import signal
from dataclasses import dataclass, field
from pathlib import Path

from ..core.digest import digest_file
from ..errors import CapabilityError
from ..blocks.visual.result import RESOURCE_KINDS, RESOURCE_MIMES as ANIMATION_MIMES

STATUS_OK = "accepted"
STATUS_BLOCKED = "blocked"


@dataclass
class OfflineCheck:
    name: str
    status: str
    detail: str = ""
    evidence: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    def as_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "detail": self.detail,
                "evidence": dict(self.evidence), "ok": self.ok}


@dataclass
class OfflineReport:
    checks: tuple[OfflineCheck, ...] = ()
    passed: bool = False
    blocked: bool = False
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"_meta": {"file_type": "offline_acceptance", "schema_version": "1.0",
                          "generator": "aiforstudy.checks.offline",
                          "field_notes": {
                              "checks": "逐项状态：accepted / failed / blocked",
                              "blocked": "缺必需能力（浏览器/Node/后端）时为真 —— **不算通过**",
                          }},
                "checks": [c.as_dict() for c in self.checks],
                "passed": self.passed, "blocked": self.blocked, "notes": list(self.notes)}


# ══════════════════════════════════════════ 动画资源检查（T10 / V03）
#
# 计划 §5.4 与 V03 要求"完整解码、0 < 时长 ≤ 90s、1280×720、30fps、H.264/yuv420p、可选一条合法 AAC 讲解音轨、
# 所有资源 hash 一致"。这些断言**不能**靠"文件存在"或"HTTP 200"来通过 ——
# 截断的视频文件同样存在、同样能 200。所以这里逐项看**文件本身**：
# 用 ffprobe 读容器与流参数、用 ffmpeg 完整解码一遍、并对每个资源复算 sha256。
#
# 另一个刻意的设计：检查结果**绑定最终 hash**（mp4 的 sha256 进 evidence），
# 这样"这份检查报告说的是哪一版视频"是可追溯的 —— 否则换个视频文件报告照样"通过"。

#: final／draft 档的参数要求（§5.4）。`duration` 是区间，其余是精确值。
from ..blocks.media.presentation_policy import MAX_ANIMATION_SECONDS, RENDER_PROFILES
ANIMATION_REQUIREMENTS = {
    name: {"codec_name":"h264", "width":row["pixel_width"], "height":row["pixel_height"],
           "fps":row["frame_rate"], "pix_fmt":"yuv420p", "duration":(0.0,MAX_ANIMATION_SECONDS)}
    for name,row in RENDER_PROFILES.items()
}

#: 资源种类 → 期望的 MIME（§5.3）。`captions` 必须带 charset，否则浏览器按 latin-1 解中文字幕。



def _ffprobe_streams(path: Path) -> tuple[dict, str]:
    """读容器与视频流参数，返回 `(info, error)`。

    ⚠️ **"工具没装"和"文件是坏的"必须分开**（这是被测出来的）：
    * `ffprobe` 进程起不来（`OSError`）→ `CapabilityError`：环境缺失，属 `blocked`；
    * `ffprobe` 起了但返回非零 → **不是能力问题，是这份产物坏了** → 返回错误文本，
      由调用方判 `failed`。

    第一版把后一种也抛成 `CapabilityError`，于是"视频文件损坏"会被报成
    "本机缺 ffprobe" —— 那会把一个内容问题伪装成环境问题，正好是计划反复禁止的
    "缺工具/没运行 冒充不适用"。而截断的视频恰恰是要挡住的头号反例。
    """
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_name,codec_type,width,height,r_frame_rate,pix_fmt,channels,sample_rate,duration,start_time",
             "-show_entries", "format=duration",
             "-of", "json", str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    except OSError as exc:
        raise CapabilityError(f"ffprobe 不可用：{exc}") from exc
    except subprocess.SubprocessError as exc:
        raise CapabilityError(f"ffprobe 执行失败：{exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        return {}, f"ffprobe 读不了 {path.name}（退出码 {proc.returncode}）：{detail[:200]}"
    return json.loads(proc.stdout.decode("utf-8") or "{}"), ""


def _parse_fps(value: str) -> float:
    if not value:
        return 0.0
    if "/" in value:
        num, _, den = value.partition("/")
        try:
            return float(num) / float(den or 1)
        except (TypeError, ValueError, ZeroDivisionError):
            return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def check_animation_video(mp4: Path, *, profile: str = "final", narrated_duration: float | None = None, narration=None, steps=None) -> OfflineCheck:
    """核对 MP4 的**容器与流参数**，并完整解码一遍。

    `profile` 只允许 final／draft；别的档位明确报错而不是"按 final 检查"——
    档位是参数来源，猜档位等于把验收标准弄丢。
    """
    if profile not in ANIMATION_REQUIREMENTS:
        raise CapabilityError(
            f"未知的渲染档位 {profile!r}；只支持 {sorted(ANIMATION_REQUIREMENTS)}")
    want = ANIMATION_REQUIREMENTS[profile]
    mp4 = Path(mp4)
    if not mp4.is_file():
        return OfflineCheck("animation.video", "failed", f"缺视频文件 {mp4}")
    evidence = {"path": str(mp4), "sha256": digest_file(mp4), "bytes": mp4.stat().st_size,
                "profile": profile}
    info, probe_error = _ffprobe_streams(mp4)
    if probe_error:
        evidence["ffprobe_error"] = probe_error
        evidence["decode_ok"] = False
        return OfflineCheck("animation.video", "failed",
                            f"视频文件读不出来（多半是截断或损坏）：{probe_error}", evidence)
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    duration = float((info.get("format") or {}).get("duration") or 0.0)
    evidence.update({"duration": duration, "audio_streams": len(audio),
                     "video": video or {}})
    problems: list[str] = []
    if video is None:
        problems.append("没有视频流")
    else:
        fps = _parse_fps(str(video.get("r_frame_rate") or ""))
        evidence["fps"] = fps
        for field, actual in (("codec_name", video.get("codec_name")),
                              ("width", video.get("width")), ("height", video.get("height")),
                              ("pix_fmt", video.get("pix_fmt"))):
            if actual != want[field]:
                problems.append(f"{field}={actual!r}，要求 {want[field]!r}")
        if abs(fps - want["fps"]) > 0.01:
            problems.append(f"fps={fps}，要求 {want['fps']}")
    from ..blocks.media.narration import audio_stream_problems
    problems.extend(audio_stream_problems(audio, video_seconds=duration,narration=narration,steps=steps))
    low, high = want["duration"]
    if narrated_duration is not None:
        from ..blocks.media.narration import MAX_NARRATED_SECONDS
        if (not math.isfinite(narrated_duration) or not 0 < narrated_duration <= MAX_NARRATED_SECONDS
                or abs(duration-narrated_duration) > .1 or len(audio)!=1):
            problems.append('配音清单时长与实际有声视频不一致')
        else:
            high = MAX_NARRATED_SECONDS
    if not (low < duration <= high):
        problems.append(f"时长 {duration:.2f}s 不在 [{low}, {high}] 秒区间内")
    evidence["profile_ok"] = not problems
    evidence["profile_problems"] = list(problems)

    # 完整解码：文件存在、ffprobe 能读，都不代表**每一帧都能解出来**
    try:
        decode = subprocess.run(["ffmpeg", "-v", "error", "-i", str(mp4), "-f", "null", "-"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CapabilityError(f"ffmpeg 不可用或执行失败：{exc}") from exc
    decode_error = decode.stderr.decode("utf-8", "replace").strip()
    evidence["decode_exit_code"] = decode.returncode
    evidence["decode_stderr"] = decode_error[:400]
    evidence["decode_ok"] = decode.returncode == 0 and not decode_error
    if decode.returncode != 0 or decode_error:
        return OfflineCheck("animation.video", "failed",
                            f"完整解码失败（退出码 {decode.returncode}）：{decode_error[:200]}",
                            evidence)
    if problems:
        return OfflineCheck("animation.video", "failed", "；".join(problems), evidence)
    return OfflineCheck("animation.video", "accepted",
                        f"{profile}：{video.get('codec_name')} "
                        f"{video.get('width')}x{video.get('height')} {evidence.get('fps')}fps "
                        f"{duration:.2f}s，{len(audio)} 条合法音轨，完整解码通过", evidence)


def check_animation_resources(result: dict, base_dir: Path, *, profile: str = "final") -> OfflineCheck:
    """逐个资源核对 **存在、字节数、sha256、MIME**，再对 MP4 做容器＋解码检查。

    判据绑定最终 hash：`evidence["binding"]` 里带 mp4 的 sha256 与
    `spec_hash`／`compiler_hash`／`environment_hash`，所以"这份报告说的是哪一版"
    是可追溯的（V03：所有资源 hash 一致）。
    """
    base_dir = Path(base_dir)
    resources = result.get("resources") or []
    if not resources:
        return OfflineCheck("animation.resources", "failed",
                            "AnimationResult.resources 为空 —— 没有资源的完成没有意义")
    problems: list[str] = []
    seen: dict[str, int] = {}
    evidence: dict = {"count": len(resources), "files": {}, "binding": {
        "spec_hash": result.get("spec_hash"), "compiler_hash": result.get("compiler_hash"),
        "environment_hash": result.get("environment_hash")}}
    mp4_path = None
    for resource in resources:
        kind = str(resource.get("kind") or "")
        if kind not in RESOURCE_KINDS:
            problems.append(f'未知动画资源种类 {kind!r}')
        rel = str(resource.get("relative_path") or "")
        if kind in seen:
            seen[kind] += 1
        else:
            seen[kind] = 1
        if not rel:
            problems.append(f"{kind} 缺 relative_path")
            continue
        if rel.startswith("/") or ".." in Path(rel).parts:
            problems.append(f"{kind} 的路径 {rel!r} 不是安全的相对路径")
            continue
        path = base_dir / rel
        if not path.is_file():
            problems.append(f"{kind} 缺文件 {rel}")
            continue
        actual_bytes = path.stat().st_size
        actual_hash = digest_file(path)
        if resource.get("bytes") not in (None, actual_bytes):
            problems.append(f"{kind} 声明 {resource.get('bytes')} 字节，实际 {actual_bytes}")
        if resource.get("sha256") not in (None, actual_hash):
            problems.append(f"{kind} 的 sha256 与文件不符（内容被篡改或过期）")
        want_mime = ANIMATION_MIMES.get(kind)
        if want_mime and resource.get("mime") != want_mime:
            problems.append(f"{kind} 的 MIME 是 {resource.get('mime')!r}，要求 {want_mime!r}")
        if kind == 'audio':
            import wave
            try:
                with wave.open(str(path),'rb') as sound:
                    audio_duration=sound.getnframes()/sound.getframerate()
                    if (sound.getnchannels() not in (1,2) or sound.getsampwidth()!=2
                            or not 16000<=sound.getframerate()<=48000 or not 0<audio_duration<=1800):
                        raise ValueError('unsupported WAV PCM profile or duration')
                    # Header alone does not prove all declared samples exist.
                    remaining=sound.getnframes()*sound.getnchannels()*sound.getsampwidth()
                    while remaining>0:
                        chunk=sound.readframes(min(sound.getnframes(),65536))
                        if not chunk: raise ValueError('truncated WAV samples')
                        remaining-=len(chunk)
            except (OSError,EOFError,ValueError,wave.Error) as exc:
                problems.append(f'音频 WAV 无效 {rel}: {exc}')
        if kind == 'narration':
            try:
                receipt=json.loads(path.read_text(encoding='utf-8'))
                if (not isinstance(receipt,dict) or not isinstance(receipt.get('source'),dict)
                        or not receipt['source'].get('backend') or not isinstance(receipt.get('steps'),list)
                        or not receipt['steps']):
                    raise ValueError('missing narration source or steps')
            except (OSError,UnicodeError,ValueError) as exc:
                problems.append(f'配音来源清单无效 {rel}: {exc}')
        evidence["files"][f"{kind}:{rel}"] = {"sha256": actual_hash, "bytes": actual_bytes}
        if kind == "mp4":
            mp4_path = path
    kinds = set(seen)
    for required in ("mp4", "poster", "keyframe", "captions", "transcript"):
        if required not in kinds:
            problems.append(f"缺 {required} 资源（§5.4 要求四类齐全 + 每步检查点）")
    if problems:
        return OfflineCheck("animation.resources", "failed", "；".join(problems), evidence)

    narration = (result.get('technical_report') or {}).get('narration')
    narrated_duration = None
    if narration:
        steps = result.get('steps') or []
        if not steps or not narration.get('source') or not narration.get('steps'):
            return OfflineCheck('animation.resources','failed','配音缺少来源或分步时间清单',evidence)
        narrated_duration = float(steps[-1].get('end',0))
    video = check_animation_video(mp4_path, profile=profile, narrated_duration=narrated_duration,
                                  narration=narration,steps=result.get("steps"))
    evidence["video"] = video.as_dict()
    evidence["binding"]["mp4_sha256"] = video.evidence.get("sha256")
    if not video.ok:
        return OfflineCheck("animation.resources", "failed",
                            f"视频检查未过：{video.detail}", evidence)
    return OfflineCheck("animation.resources", "accepted",
                        f"{len(resources)} 个资源 hash 一致；视频参数与完整解码通过",
                        evidence)


def webvtt_cue_text(caption: str) -> str:
    """Escape caption text for a WebVTT cue: a bare `<` starts a tag and drops the rest."""
    return str(caption).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _unescape_cue(text: str) -> str:
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def parse_webvtt_cues(text: str) -> list[dict]:
    """Parse the bounded cue format emitted by our host caption writer.

    Cue text is returned unescaped, so it compares equal to the step caption.
    """
    text=text.lstrip("\ufeff").replace("\r\n","\n")
    blocks=re.split(r"\n\s*\n",text.strip())
    if not blocks or blocks[0].strip()!="WEBVTT": raise ValueError("缺合法 WEBVTT 头")
    def seconds(value):
        h,m,s=value.split(":");m=int(m);s=float(s)
        if m>=60 or s>=60: raise ValueError("字幕时间字段越界")
        return int(h)*3600+m*60+s
    cues=[];previous=0.0;ids=set()
    for block in blocks[1:]:
        lines=block.splitlines()
        if lines[0].startswith("NOTE"): continue
        ident=""
        if "-->" not in lines[0]: ident=lines.pop(0)
        match=re.fullmatch(r"(\d{2,}:\d{2}:\d{2}\.\d{3})\s+-->\s+(\d{2,}:\d{2}:\d{2}\.\d{3})",lines[0] if lines else "")
        if not match or len(lines)<2 or not "\n".join(lines[1:]).strip(): raise ValueError("无效或空字幕 cue")
        start,end=map(seconds,match.groups())
        if start<previous or end<=start: raise ValueError("字幕重叠、倒序或空时段")
        if ident and ident in ids: raise ValueError("重复字幕 cue ID")
        ids.add(ident);previous=end
        cues.append({"id":ident,"start":start,"end":end,"text":_unescape_cue("\n".join(lines[1:]))})
    if not cues: raise ValueError("没有任何带时段的 cue")
    return cues


def check_webvtt(captions: Path) -> OfflineCheck:
    captions=Path(captions)
    if not captions.is_file(): return OfflineCheck("animation.captions","failed",f"缺字幕文件 {captions}")
    raw=captions.read_bytes();evidence={"sha256":digest_file(captions),"bytes":len(raw)}
    try: cues=parse_webvtt_cues(raw.decode("utf-8"))
    except (UnicodeDecodeError,ValueError) as exc:
        return OfflineCheck("animation.captions","failed",str(exc),evidence)
    evidence.update(cues=len(cues),cue_records=cues)
    return OfflineCheck("animation.captions","accepted",f"{len(cues)} 段合法 UTF-8 WebVTT 字幕",evidence)


def check_no_missing_images(root: Path) -> OfflineCheck:
    """**丢图 = 0**：页面引用到的本地资源必须真的在解压/复制后的目录里。"""
    root = Path(root)
    if not root.is_dir():
        return OfflineCheck("no-missing-images", "failed", f"目录不在位：{root}")
    missing: list[str] = []
    checked = 0
    for path in root.rglob("*"):
        if path.suffix not in (".html", ".js", ".css"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in re.finditer(r"""(?:src|href)\s*=\s*["']([^"']+\.(?:png|jpg|jpeg|gif|webp|svg))["']""",
                                 text, re.I):
            ref = match.group(1)
            if ref.startswith(("http:", "https:", "//", "data:")):
                continue
            checked += 1
            candidate = (path.parent / ref.lstrip("/")).resolve()
            if not candidate.is_file():
                missing.append(f"{path.name}: {ref}")
    if missing:
        return OfflineCheck("no-missing-images", "failed",
                            f"有 {len(missing)} 处引用指向不存在的图：{missing[:5]}", 
                            {"missing": len(missing), "checked": checked})
    return OfflineCheck("no-missing-images", "accepted",
                        f"检查了 {checked} 处本地图引用，全部存在", {"checked": checked})


def check_browser_available(*, node: str | None, jsdom: Path | None) -> OfflineCheck:
    """§15 A26：缺必需能力 → `blocked`，**不用 skipped 算通过**。"""
    if node and jsdom and Path(jsdom).is_dir():
        return OfflineCheck("browser-runtime", "accepted", f"node={node}｜jsdom={jsdom}")
    return OfflineCheck(
        "browser-runtime", "blocked",
        f"缺必需能力（node={node}｜jsdom={jsdom}）。方案 §15 A26：属环境阻断，"
        f"**不能用 skipped 算正式通过**；交互与运行时异常那一层本轮未验证。")


def _browser_executable(workspace_root: Path | None = None) -> Path | None:
    configured = os.environ.get("AIFS_BROWSER_EXECUTABLE")
    candidates = ([Path(configured)] if configured else [])
    if workspace_root is not None:
        candidates.append(Path(workspace_root) / ".devtools/chrome/chrome-linux64/chrome")
    candidates += [Path(p) for name in ("chromium", "chromium-browser", "google-chrome")
                   if (p := shutil.which(name))]
    candidates += [parent / ".devtools/chrome/chrome-linux64/chrome"
                   for parent in Path(__file__).resolve().parents]
    return next((p for p in candidates if p.is_file() and os.access(p, os.X_OK)), None)


def _browser_check_timeout(root: Path) -> float:
    """整个浏览器检查的总时限按页面与单元数放大（全书入口单元多、数据大），可用环境变量覆盖。

    原来固定 90 秒，全书入口经常超时（用户 2026-10-20 定：技术检查放宽）。
    """
    override = os.environ.get("AIFS_BROWSER_CHECK_TIMEOUT")
    if override:
        try:
            return max(30.0, float(override))
        except ValueError:
            pass
    units = 0
    for data in Path(root).rglob("data/data.js"):
        try:
            text = data.read_text(encoding="utf-8")
            units += len(json.loads(text[text.index("=") + 1:].strip().rstrip(";")).get("units") or {})
        except (OSError, ValueError):
            units += 20
    return max(90.0, 60.0 + 8.0 * units)


def check_browser_execution(root: Path, *, node: str | None,
                            workspace_root: Path | None = None) -> OfflineCheck:
    browser = _browser_executable(workspace_root)
    if not node or browser is None:
        return OfflineCheck("browser-execution", "blocked", "缺真实 Chromium 或 Node；未执行浏览器验收")
    try:
        with tempfile.TemporaryDirectory(prefix="aifs-offline-") as directory:
            isolated = Path(directory) / "candidate"
            # Reject symlinks before copying; otherwise a local secret can be pulled into the candidate.
            if any(p.is_symlink() for p in Path(root).rglob("*")):
                return OfflineCheck("browser-execution", "failed", "交付目录含符号链接，无法限定只读资源范围")
            shutil.copytree(root, isolated)
            from ..blocks.render.display_policy import reader_display_policy
            from ..core.review_mode import substantive_reviews_enabled
            display_policy=reader_display_policy()
            if substantive_reviews_enabled():
                display_policy={**display_policy,'review_mode':'balanced'}
            process = subprocess.Popen([node, str(Path(__file__).with_name("browser_acceptance.cjs")),
                str(browser), str(isolated), json.dumps(display_policy)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, start_new_session=True)
            try:
                stdout, stderr = process.communicate(timeout=_browser_check_timeout(isolated))
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                raise
            payload = json.loads(stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return OfflineCheck("browser-execution", "blocked", f"浏览器检查未完成：{exc}")
    status = "blocked" if payload.get("blocked") else "accepted" if payload.get("ok") else "failed"
    return OfflineCheck("browser-execution", status, payload.get("error") or
                        "隔离目录打开页面，阻断网络，执行逐单元图/公式/交互检查", payload.get("evidence") or {})


def run_offline_acceptance(*, tree_root: Path | None = None,
                           node: str | None = None,
                           jsdom: Path | None = None,
                           workspace_root: Path | None = None) -> OfflineReport:
    """把上面几项跑一遍，给出**可判定的**结论。"""
    checks: list[OfflineCheck] = []
    if tree_root is not None:
        checks.append(check_no_missing_images(tree_root))
    if tree_root is not None:
        checks.append(check_browser_execution(Path(tree_root), node=node,workspace_root=workspace_root))
    else:
        checks.append(OfflineCheck("browser-execution", "blocked", "缺可执行的 HTML 候选目录，不能只凭工具存在通过"))
    failed = [c for c in checks if c.status == "failed"]
    blocked = [c for c in checks if c.status == STATUS_BLOCKED]
    notes = []
    if blocked:
        notes.append("有环境阻断项：" + "，".join(c.name for c in blocked)
                     + "。**它不算通过**（方案 §15 A26：不能用 skipped 算正式通过），"
                       "要装齐再复跑：" + "；".join(c.detail for c in blocked))
    if failed:
        notes.append("有失败项：" + "，".join(c.name for c in failed)
                     + "。方案 §15 A23：检查非零则**包不发布**。")
    return OfflineReport(checks=tuple(checks),
                         passed=not failed and not blocked,
                         blocked=bool(blocked), notes=tuple(notes))


def require_capabilities_for_release(*, node: str | None, jsdom: Path | None) -> None:
    """release 档要真发布就必须有必需能力；缺了**明确阻断**。"""
    check = check_browser_available(node=node, jsdom=jsdom)
    if not check.ok:
        raise CapabilityError(check.detail)


# ────────────────────────────────────────── T10：浏览器**实际播放**检查

#: 在隔离副本里额外放一份 harness 页：用**与生产一致**的
#: `<iframe sandbox="allow-scripts">` 打开章节页，这样沙箱子 frame 在 Chrome 里
#: 是一个可 attach 的 OOPIF target，`browser_acceptance.cjs` 能进到 frame 自己的
#: 文档里量"到底播没播、字幕读不读得到、换节停不停"。
#:
#: 注意：harness 本身**不带**产物 CSP（`ARTIFACT_CSP` 是给教材页响应头的）；
#: 这里要验的是"沙箱里能不能真的播"，产物响应头那一层由 T09 的测试与 T10 的
#: `tests/browser/animation_player.browser.cjs`（打真实 `ControlServer`）覆盖。
_T10_HARNESS = ("<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
                "<title>T10 播放验收 harness</title></head><body>"
                "<iframe id=\"f\" sandbox=\"allow-scripts\" src=\"index.html\" "
                "width=\"1000\" height=\"620\"></iframe></body></html>")


def _range_handler(directory: str):
    """只读地服务导出目录：分块 + 单 Range（视频 seek 需要 206）。"""
    import functools
    import http.server
    import mimetypes
    from ..delivery.resources import iter_chunks, mime_for, parse_range_header, range_for_response

    for suffix, kind in ((".vtt", "text/vtt"), (".mp4", "video/mp4"),
                         (".m4v", "video/mp4"), (".webm", "video/webm")):
        mimetypes.add_type(kind, suffix)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):        # 检查不要往 stdout 喷访问日志
            return

        def do_HEAD(self):                   # noqa: N802
            self._serve(head=True)

        def do_GET(self):                    # noqa: N802
            self._serve(head=False)

        def _serve(self, *, head: bool) -> None:
            import urllib.parse
            relative = urllib.parse.unquote(self.path.split("?", 1)[0]).lstrip("/")
            target = (Path(directory) / relative).resolve()
            if not target.is_relative_to(Path(directory).resolve()) or not target.is_file():
                self.send_error(404)
                return
            total = target.stat().st_size
            start, length, content_range, status = 0, total, None, 200
            header = self.headers.get("Range") if self.headers else None
            if header and str(header).strip():
                span = parse_range_header(str(header))
                if span is not None:
                    resolved = range_for_response(span, total)
                    if resolved is None:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{total}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    start, last = resolved
                    length, status = last - start + 1, 206
                    content_range = f"bytes {start}-{last}/{total}"
            self.send_response(status)
            self.send_header("Content-Type", mime_for(target.name))
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            if content_range:
                self.send_header("Content-Range", content_range)
            self.send_header("Cache-Control", "no-store")
            # Match the production read-only artifact server for opaque-origin
            # sandbox fonts/VTT. This grants no management API capability.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if head or length == 0:
                return
            try:
                for block in iter_chunks(target, start=start, length=length):
                    self.wfile.write(block)
            except (BrokenPipeError, ConnectionResetError):
                return

    return functools.partial(Handler, directory=directory)


def check_animation_playback(root: str | Path, *, node: str | None,
                             workspace_root: Path | None = None,
                             timeout: float = 180.0) -> OfflineCheck:
    """在**独立导出目录**上起本机服务，用真实 Chromium 播一遍（V04 的浏览器侧）。

    与 `check_animation_video`（ffmpeg 完整解码）是两件事，缺一不可：

    * ffmpeg 解码只说明"文件本身是完整的"；
    * 这里说明"浏览器真的解出了元数据、真的在放、字幕真的读得到 cue、换节真的停"。

    `poster` 可见、HTTP 200、文件非空都**不算**通过 —— 那些在坏文件上照样成立。
    缺 Chromium 或 Node 时返回 `blocked`（不是 skip、不是"用静态 DOM 检查代替"）。
    """
    import http.server
    import threading

    browser = _browser_executable(workspace_root)
    if not node or browser is None:
        return OfflineCheck(
            "animation-playback", "blocked",
            f"缺真实 Chromium 或 Node（node={node}｜browser={browser}）："
            f"浏览器实际播放检查未执行，**不用静态 DOM 检查冒充通过**")
    root = Path(root)
    if not (root / "index.html").is_file():
        return OfflineCheck("animation-playback", "failed",
                            f"导出目录里没有 index.html：{root}")
    try:
        with tempfile.TemporaryDirectory(prefix="aifs-playback-") as directory:
            isolated = Path(directory) / "export"
            if any(path.is_symlink() for path in root.rglob("*")):
                return OfflineCheck("animation-playback", "failed",
                                    "导出目录含符号链接，无法限定只读资源范围")
            shutil.copytree(root, isolated)
            (isolated / "_t10-harness.html").write_text(_T10_HARNESS, encoding="utf-8")
            httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _range_handler(str(isolated)))
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{httpd.server_address[1]}"
            try:
                process = subprocess.Popen(
                    [node, str(Path(__file__).with_name("browser_acceptance.cjs")),
                     str(browser), base + "/_t10-harness.html"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    start_new_session=True)
                try:
                    stdout, stderr = process.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()
                    return OfflineCheck("animation-playback", "blocked",
                                        f"浏览器播放检查超时（{timeout:.0f}s），未完成")
            finally:
                httpd.shutdown()
                httpd.server_close()
            payload = json.loads(stdout)
    except (OSError, ValueError) as exc:
        return OfflineCheck("animation-playback", "blocked", f"浏览器播放检查未完成：{exc}")
    evidence = dict(payload.get("evidence") or {})
    animation = evidence.get("animation") or {}
    errors = list(evidence.get("animation_errors") or [])
    if payload.get("blocked"):
        return OfflineCheck("animation-playback", "blocked",
                            payload.get("error") or "浏览器播放检查被阻断", evidence)
    if not animation.get("present"):
        # 没有动画载荷的普通单元可以合法地没有播放器 —— 但**必须说清楚**这是"没有可播的东西"，
        # 而不是"播过了"。
        return OfflineCheck("animation-playback", "failed",
                            "导出页里没有挂上动画播放器（没有可播的东西，不能算通过）",
                            evidence)
    if errors or not payload.get("ok"):
        return OfflineCheck("animation-playback", "failed",
                            "浏览器实际播放检查未通过：" + "；".join(errors[:4] or ["见证据"]),
                            evidence)
    return OfflineCheck(
        "animation-playback", "accepted",
        f"真实 Chromium：单元 {animation.get('unit')} 解出元数据并播放"
        f"（{animation.get('play', {}).get('width')}x{animation.get('play', {}).get('height')}，"
        f"duration={animation.get('play', {}).get('duration')}），"
        f"字幕 {animation.get('captions', {}).get('cues')} 条；"
        + ("换节停止已验" if animation.get("switchAway") else "单单元产物，无换节目标"),
        evidence)


__all__ = ["STATUS_OK", "STATUS_BLOCKED", "OfflineCheck", "OfflineReport",
           "check_no_missing_images", "check_browser_available",
           "check_animation_playback",
           "run_offline_acceptance", "require_capabilities_for_release"]
