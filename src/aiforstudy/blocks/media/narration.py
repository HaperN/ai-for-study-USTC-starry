"""Host-side, replaceable Chinese narration and offline video packaging.

No generated Python runs here. Adapters receive plain text and write audio;
all timing comes from decoded PCM. The video and narration share one MP4 clock.
"""
from __future__ import annotations

from copy import deepcopy
import asyncio
from array import array
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from urllib.request import Request, urlopen
import wave

VERSION = 'host-narration-1.4-proven-hold'
SAMPLE_RATE = 24000
MAX_NARRATED_SECONDS = 1800


def _run(args, *, timeout=600):
    process = subprocess.run([str(arg) for arg in args], capture_output=True, timeout=timeout)
    if process.returncode:
        raise RuntimeError('Narration media command failed: ' + process.stderr.decode('utf-8', 'replace')[-1800:])
    return process


def resolve_ffmpeg(path=None):
    if path:
        return str(path)
    found = shutil.which('ffmpeg')
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as exc:
        raise RuntimeError('Narration requires ffmpeg or imageio-ffmpeg') from exc


@dataclass
class MacOSSayBackend:
    voice: str = 'Tingting'
    rate: int = 175

    @property
    def source(self):
        return {'backend':'macos-say', 'voice':self.voice, 'rate':self.rate, 'language':'zh-CN'}

    def synthesize(self, text, destination):
        if not Path('/usr/bin/say').is_file():
            raise RuntimeError('macOS say is unavailable; configure command or HTTP narration backend')
        destination = Path(destination)
        script = destination.with_suffix('.speech.txt')
        script.write_text(text, encoding='utf-8')
        try:
            _run(['/usr/bin/say', '-v', self.voice, '-r', self.rate, '-f', script,
                  '-o', destination, '--file-format=WAVE', '--data-format=LEI16@24000'], timeout=180)
        finally:
            script.unlink(missing_ok=True)


@dataclass
class EdgeNeuralBackend:
    """Microsoft Edge online Neural synthesis; never falls back to system TTS.

    edge-tts is an independent client of Microsoft's online read-aloud service,
    not an Azure subscription. Network failures are reported to the caller.
    """
    voice: str = 'zh-CN-XiaoxiaoNeural'
    rate: str = '-5%'
    pitch: str = '+0Hz'
    retries: int = 3

    @property
    def source(self):
        from importlib.metadata import version
        return {'backend':'edge-tts', 'voice':self.voice, 'rate':self.rate,
                'pitch':self.pitch, 'language':'zh-CN', 'neural':True,
                'client_version':version('edge-tts'),
                'service':'Microsoft Edge online read-aloud',
                'reference':'https://github.com/rany2/edge-tts'}

    def synthesize(self, text, destination):
        import edge_tts
        destination = Path(destination)
        async def generate():
            communicate=edge_tts.Communicate(str(text), self.voice, rate=self.rate,
                                             pitch=self.pitch, receive_timeout=120)
            await communicate.save(str(destination))
        for attempt in range(self.retries):
            try:
                asyncio.run(generate())
                if destination.stat().st_size < 128:
                    raise ValueError('Neural synthesis returned empty audio')
                return
            except Exception:
                destination.unlink(missing_ok=True)
                if attempt+1 == self.retries:
                    raise
                time.sleep(min(2**attempt, 4))


@dataclass
class CommandTTSBackend:
    """Explicit argv template, without a shell: {text_file}, {output}, {voice}."""
    command: list[str]
    voice: str = 'default'
    name: str = 'external-command'

    @property
    def source(self):
        return {'backend':self.name, 'voice':self.voice, 'adapter':'command', 'command_hash':
                hashlib.sha256(json.dumps(self.command).encode()).hexdigest()}

    def synthesize(self, text, destination):
        script = Path(destination).with_suffix('.speech.txt')
        script.write_text(text, encoding='utf-8')
        try:
            _run([part.format(text_file=str(script), output=str(destination), voice=self.voice)
                  for part in self.command], timeout=180)
        finally:
            script.unlink(missing_ok=True)


@dataclass
class HTTPTTSBackend:
    """POST JSON {input,model,voice,response_format}; endpoint returns audio bytes.

    A Qwen compatible gateway can be configured explicitly. Credentials stay in
    the host environment, never enter a worker context or the provenance file.
    """
    endpoint: str
    model: str
    voice: str
    token_env: str = 'AIFORSTUDY_TTS_TOKEN'
    name: str = 'external-http'

    @property
    def source(self):
        return {'backend':self.name, 'model':self.model, 'voice':self.voice, 'adapter':'http',
                'endpoint_hash':hashlib.sha256(self.endpoint.encode()).hexdigest()}

    def synthesize(self, text, destination):
        headers = {'Content-Type':'application/json'}
        token = os.environ.get(self.token_env)
        if token:
            headers['Authorization'] = 'Bearer ' + token
        request = Request(self.endpoint, data=json.dumps({'input':text, 'model':self.model,
                          'voice':self.voice, 'response_format':'wav'}).encode(), headers=headers, method='POST')
        with urlopen(request, timeout=180) as response:
            data = response.read(32 * 1024 * 1024 + 1)
        if not data or len(data) > 32 * 1024 * 1024:
            raise ValueError('TTS response is empty or exceeds 32 MiB')
        Path(destination).write_bytes(data)


def configured_backend(config=None):
    config = config or {}
    kind = config.get('backend', 'local-qwen')
    if kind == 'local-qwen':
        from .local_narration import AutoLocalQwenBackend
        return AutoLocalQwenBackend(config)
    if kind == 'local-cache':
        from .local_narration import LocalCacheBackend
        return LocalCacheBackend(config)
    if kind in ('minimax', 'elevenlabs'):
        from .narration_providers import PremiumTTSBackend
        return PremiumTTSBackend(config)
    if kind == 'edge-tts':
        return EdgeNeuralBackend(config.get('voice','zh-CN-XiaoxiaoNeural'),
                                 str(config.get('rate','-5%')), str(config.get('pitch','+0Hz')))
    if kind == 'macos-say':
        return MacOSSayBackend(voice=config.get('voice', 'Tingting'), rate=int(config.get('rate', 175)))
    if kind == 'command':
        command = config.get('command')
        if not isinstance(command, list) or not command or not all(isinstance(arg, str) for arg in command):
            raise ValueError('narration command must be an explicit argv list')
        return CommandTTSBackend(command, config.get('voice','default'), config.get('name','external-command'))
    if kind == 'http':
        return HTTPTTSBackend(config['endpoint'], config['model'], config['voice'],
                              config.get('token_env','AIFORSTUDY_TTS_TOKEN'), config.get('name','external-http'))
    raise ValueError('unknown narration backend: ' + str(kind))


def spoken_text(step):
    """Speak authored explanation, else observations; never read drawing orders."""
    if step.get('canonical_spoken_text') is not None:
        text = step['canonical_spoken_text']
        review = step.get('spoken_review') or {}
        if not isinstance(text, str) or not text.strip() or review.get('status') != 'approved':
            raise ValueError('canonical_spoken_text requires an approved spoken_review')
        # An editorially approved script must not be reinterpreted by the legacy
        # LaTeX/Unicode heuristic (which cannot prove mathematical meaning).
        return text
    if step.get('narration_text'):
        text = str(step['narration_text']).strip()
    else:
        caption = str(step.get('caption') or step.get('text') or '')
        observation = str(step.get('observation') or '').strip()
        directive = bool(re.search(r'对\s*\S+\s*画|画出|停在|淡出|退场|移到画面|标绿框|缩小后停在', caption))
        text = observation or caption
        # A mathematical statement may clarify a purely visual observation.
        # Keep explanatory order while excluding production instructions.
        if observation and caption and not directive and re.search(r'[$=等于]|所以|因此|意味着',caption):
            text += '。' + caption
        text = re.sub(r'^看(?=[蓝绿橙紫红灰色圆向量图])', '请留意', text)
        text = re.sub(r'^看\s*', '请留意，', text)
        text = re.sub(r'被(?:绿色|蓝色|橙色|红色)?(?:矩形)?框出并停留|并保持该等式静止|停在画面中央|停留在画面上', '', text)
    subs = dict(zip('₀₁₂₃₄₅₆₇₈₉', '0123456789'))
    text = re.sub(r'[₀₁₂₃₄₅₆₇₈₉]+', lambda m:'下标'+''.join(subs[c] for c in m[0])+'，', text)
    supers = dict(zip('⁰¹²³⁴⁵⁶⁷⁸⁹ⁿ⁺⁻', '0123456789n+-'))
    text = re.sub(r'[⁰¹²³⁴⁵⁶⁷⁸⁹ⁿ⁺⁻]+', lambda m:'的'+''.join(supers[c] for c in m[0])+'次方，', text)
    text = re.sub(r'\\(?:d?frac|tfrac)\s*\{([^{}]+)\}\s*\{([^{}]+)\}', r'\2分之\1', text)
    text = re.sub(r'\\sqrt\s*\{([^{}]+)\}', r'根号\1', text)
    commands = {'alpha':'阿尔法','beta':'贝塔','gamma':'伽马','delta':'德尔塔','epsilon':'艾普西隆',
        'theta':'西塔','lambda':'拉姆达','mu':'缪','pi':'派','rho':'柔','sigma':'西格玛','phi':'斐','varphi':'斐','zeta':'泽塔',
        'omega':'欧米伽','tau':'陶','eta':'伊塔','xi':'克西','upsilon':'宇普西隆','chi':'凯','psi':'普西',
        'infty':'无穷大','int':'积分','oint':'闭路积分','sum':'求和','lim':'极限',
        'bar':'共轭','overline':'共轭','partial':'偏导','nabla':'梯度','cdot':'乘','times':'乘',
        'leq':'小于等于','geq':'大于等于','neq':'不等于','to':'趋于','rightarrow':'趋于',
        'in':'属于','sin':'正弦','cos':'余弦','tan':'正切','log':'对数','ln':'自然对数',
        'sinh':'双曲正弦','cosh':'双曲余弦','Re':'实部','Im':'虚部','arg':'辐角',
        'left':'','right':'','mathrm':'','mathbf':'','mathbb':'','mathcal':'','quad':' '}
    text = re.sub(r'\\([A-Za-z]+)', lambda m: commands.get(m[1], m[1]), text)
    greek = {'α':'阿尔法','β':'贝塔','γ':'伽马','δ':'德尔塔','ε':'艾普西隆','θ':'西塔',
             'λ':'拉姆达','μ':'缪','π':'派','ρ':'柔','σ':'西格玛','φ':'斐','ϕ':'斐','ω':'欧米伽','ζ':'泽塔',
             'τ':'陶','η':'伊塔','ξ':'克西','υ':'宇普西隆','χ':'凯','ψ':'普西'}
    for before, after in greek.items():
        text = text.replace(before, after)
    text = re.sub(r'\b(alpha|beta|gamma|delta|epsilon|theta|lambda|mu|pi|rho|sigma|phi|omega|zeta|tau|eta|xi|upsilon|chi|psi)\b',
                  lambda m:commands[m[0]],text)
    text = re.sub(r'_\{([^{}]+)\}|_([A-Za-z0-9]+)', lambda m:'下标'+(m[1] or m[2])+'，', text)
    text = re.sub(r'\^\{([^{}]+)\}|\^([A-Za-z0-9]+)', lambda m:'的'+(m[1] or m[2])+'次方，', text)
    for before, after in [('≤','小于等于'),('≥','大于等于'),('≠','不等于'),('→','趋于'),
                          ('∞','无穷大'),('∫','积分'),('=','等于'),('+','加'),('−','减'),('<','小于'),('>','大于')]:
        text = text.replace(before, after)
    text = re.sub(r'[$\\{}`]', '', text)
    text = re.sub(r'[。]{2,}', '。', text)
    return re.sub(r'\s+', ' ', text).strip(' 。') + '。'


def visual_latex(text):
    """Normalize legacy formula glyphs into TeX, preserving authored $...$."""
    text=str(text).strip()
    if '$' not in text and re.search(r'\\[A-Za-z]+',text) and not re.search(r'[\u4e00-\u9fff]',text):
        return '$'+text+'$'
    def formula(value):
        subs=dict(zip('₀₁₂₃₄₅₆₇₈₉ₙ', '0123456789n'))
        supers=dict(zip('⁰¹²³⁴⁵⁶⁷⁸⁹ⁿ⁺⁻', '0123456789n+-'))
        value=re.sub(r'[₀₁₂₃₄₅₆₇₈₉ₙ]+',lambda m:'_{'+''.join(subs[c] for c in m[0])+'}',value)
        value=re.sub(r'[⁰¹²³⁴⁵⁶⁷⁸⁹ⁿ⁺⁻]+',lambda m:'^{'+''.join(supers[c] for c in m[0])+'}',value)
        for a,b in {'α':'alpha','β':'beta','γ':'gamma','δ':'delta','ε':'varepsilon','θ':'theta',
                    'λ':'lambda','μ':'mu','π':'pi','ρ':'rho','σ':'sigma','φ':'varphi','ϕ':'varphi',
                    'ω':'omega','ζ':'zeta','∞':'infty','Σ':'sum','∑':'sum','∫':'int','∮':'oint',
                    '≤':'leq','≥':'geq','≠':'ne','∈':'in','∉':'notin','→':'to','⇒':'Rightarrow',
                    '×':'times','·':'cdot','∂':'partial','∇':'nabla'}.items():
            value=value.replace(a, '\\'+b+' ')
        value=value.replace('−','-').replace('…',r'\ldots ').replace('°',r'^{\circ}')
        value=re.sub(r'(?<![A-Za-z\\])(rho|theta|phi|zeta|pi|delta|epsilon)\b',r'\\\1 ',value)
        value=re.sub(r'([A-Za-z])\u0304',r'\\overline{\1}',value)
        return value.strip()
    parts=re.split(r'(\$\$[\s\S]*?\$\$|(?<!\\)\$[^$\n]+(?<!\\)\$)',str(text))
    pattern=r"[|(\[]*[A-Za-z0-9α-ωϕΣ∞∑∫∮≤≥≠∈∉→⇒×·∂∇₀-₉ₙ⁰¹²³⁴⁵⁶⁷⁸⁹ⁿ][A-Za-z0-9α-ωϕΣ∞∑∫∮≤≥≠∈∉→⇒×·∂∇₀-₉ₙ⁰¹²³⁴⁵⁶⁷⁸⁹ⁿ⁺⁻\u0304\s_{}^=+*/|()\[\]<>.,'−-]*"
    for index,part in enumerate(parts):
        if not part or part.startswith('$'):
            continue
        def wrap(match):
            raw=match[0];content=raw.rstrip(' .,')
            return '$'+formula(content)+'$'+raw[len(content):]
        parts[index]=re.sub(pattern,wrap,part)
    return ''.join(parts)


def _pcm_quality(data):
    samples=array('h');samples.frombytes(data)
    if not samples:
        raise ValueError('TTS returned empty PCM audio')
    rms=math.sqrt(sum(value*value for value in samples)/len(samples))/32768
    peak=max(abs(value) for value in samples)/32768
    if rms<.0001 or peak<.001:
        raise ValueError('TTS returned silent audio (静音)')
    return {'rms':rms,'peak':peak,'non_silent':True}


def _hash(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def _video_metadata(video, ffmpeg):
    process = subprocess.run([ffmpeg, '-hide_banner', '-i', str(video)], capture_output=True, timeout=60)
    info = process.stderr.decode('utf-8','replace')
    duration = re.search(r'Duration: (\d+):(\d+):(\d+(?:\.\d+)?)', info)
    fps = re.search(r'(\d+(?:\.\d+)?) fps', info)
    if not duration or not fps or 'Video:' not in info:
        raise ValueError('Narration input must be a decodable video with known duration and fps')
    return sum(float(value) * scale for value, scale in zip(duration.groups(), (3600,60,1))), float(fps[1])


def _stamp(seconds):
    value = round(seconds * 1000)
    return f'{value//3600000:02d}:{value//60000%60:02d}:{value//1000%60:02d}.{value%1000:03d}'


def _verify_video_timeline(path, *, expected, fps, ffmpeg):
    """Check the video stream itself, never the audio/container duration."""
    try:
        import av
    except ImportError:
        result = _run([ffmpeg,'-nostdin','-v','error','-progress','pipe:1','-nostats',
                       '-i',path,'-map','0:v:0','-f','null','-'])
        values = re.findall(r'^out_time_us=(\d+)$', result.stdout.decode(), re.MULTILINE)
        frames = re.findall(r'^frame=(\d+)$', result.stdout.decode(), re.MULTILINE)
        if not values or not frames:
            raise ValueError('cannot independently verify the extended video clock')
        seconds = int(values[-1])/1000000
        count = int(frames[-1])
        method = 'FFmpeg independently decoded video-only progress clock'
    else:
        with av.open(str(path)) as container:
            streams = container.streams.video
            if len(streams)!=1:
                raise ValueError('narration output must have one video stream')
            end, count = 0.0, 0
            for packet in container.demux(streams[0]):
                if packet.pts is None:
                    continue
                count += 1
                end=max(end,float((packet.pts+(packet.duration or 0))*packet.time_base))
            seconds = end
        method = 'PyAV all video packet PTS plus packet duration'
    tolerance=max(1/fps+.002,.025)
    if count<=0 or not math.isfinite(seconds) or abs(seconds-expected)>tolerance:
        raise ValueError(f'extended video stream ends at {seconds:.6f}s; steps require {expected:.6f}s')
    return {'video_seconds':seconds,'video_packets_or_frames':count,'expected_seconds':expected,
            'fps':fps,'method':method,'producer_version':VERSION}


def _compose_narrated_video(video_path, steps, audio_path, output_path, *, ffmpeg, fps):
    filters=[]
    clock=0.0
    for index,step in enumerate(steps):
        source_start=float(step['source_start']);source_end=float(step['source_end'])
        start=float(step['start']);end=float(step['end']);target=end-start;original=float(step.get('input_motion_duration',source_end-source_start))
        if not 0<original<=source_end-source_start+.04:
            raise ValueError('input motion duration lies outside current source window')
        source_end=source_start+original
        if (not all(math.isfinite(v) for v in (source_start,source_end,start,end))
                or abs(start-clock)>.002 or target<original-1/fps or original<=0):
            raise ValueError('retimed narration steps or original source intervals are invalid')
        frames=round(target*fps)
        if frames<=0:
            raise ValueError('retimed narration step has no video frames')
        # setpts clears the link frame rate in supported FFmpeg builds. tpad
        # needs an explicit fps filter here or silently emits NO cloned frames.
        filters.append(f'[0:v]trim=start={source_start}:end={source_end},setpts=PTS-STARTPTS,'
                       f'fps={fps:g},tpad=stop_mode=clone:stop_duration={max(0,target-original)+2/fps:.8f},'
                       f'trim=end_frame={frames},setpts=N/({fps:g}*TB)[v{index}]')
        clock=end
    if not steps or not 0<clock<=MAX_NARRATED_SECONDS:
        raise ValueError('retimed narration duration is invalid')
    with wave.open(str(audio_path),'rb') as audio:
        audio_seconds=audio.getnframes()/audio.getframerate()
    if abs(audio_seconds-clock)>max(1/fps,.01):
        raise ValueError('existing narration WAV does not match the retimed steps')
    filters.append(''.join(f'[v{i}]' for i in range(len(steps)))+f'concat=n={len(steps)}:v=1:a=0[outv]')
    _run([ffmpeg,'-nostdin','-v','error','-y','-threads','1','-filter_complex_threads','1',
          '-i',video_path,'-i',audio_path,'-filter_complex',';'.join(filters),
          '-map','[outv]','-map','1:a:0','-r',f'{fps:g}','-c:v','libx264','-preset','ultrafast',
          '-crf','24','-pix_fmt','yuv420p','-c:a','aac','-b:a','96k','-movflags','+faststart',
          '-t',f'{clock:.6f}',output_path])
    # Decode every output track, then separately verify the real video clock.
    _run([ffmpeg,'-nostdin','-v','error','-i',output_path,'-map','0:v:0','-map','0:a:0','-f','null','-'])
    return _verify_video_timeline(output_path,expected=clock,fps=fps,ffmpeg=ffmpeg)


def rebuild_narrated_video(video_path, steps, audio_path, output_video, *, ffmpeg_path=None):
    """Rebuild only the MP4 using an ORIGINAL video, retimed steps and saved WAV.

    Each step requires source_start/source_end plus the desired start/end.
    TTS, captions, transcripts and WAV bytes are untouched. Atomically replaces
    output_video only after full decode and actual video-stream PTS verification.
    """
    original=Path(video_path).resolve();audio=Path(audio_path).resolve();output=Path(output_video).absolute()
    if output.is_symlink() or output.resolve()==original:
        raise ValueError('video repair must not overwrite the original source video')
    ffmpeg=resolve_ffmpeg(ffmpeg_path)
    source_seconds,fps=_video_metadata(original,ffmpeg)
    if not steps or abs(float(steps[0]['source_start']))>.06 or abs(float(steps[-1]['source_end'])-source_seconds)>max(.06,1/fps):
        raise ValueError('repair steps must cover the complete original source video')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.video-rebuild-',dir=output.parent) as folder:
        staged=Path(folder)/'video.mp4'
        timing=_compose_narrated_video(original,steps,audio,staged,ffmpeg=ffmpeg,fps=fps)
        staged.replace(output)
    return {'video_path':str(output.resolve()),'duration_seconds':float(steps[-1]['end']),
            'video_timing':timing,'producer_version':VERSION,'sha256':_hash(output),
            'bytes':output.stat().st_size}


def narrate_animation(video_path, steps, output_dir, *, backend=None, ffmpeg_path=None, stem=None):
    """Return paths, retimed steps and source metadata for an offline spoken MP4.

    Steps must cover the original video contiguously. A short original step
    plays at normal speed then holds its final frame until the complete voice
    finishes. Existing output is reused only if all input and output hashes match.
    """
    video_path = Path(video_path).resolve()
    output = Path(output_dir).resolve()
    if output == video_path.parent:
        raise ValueError('Narration output must be separate from original media')
    backend = backend or configured_backend()
    ffmpeg = resolve_ffmpeg(ffmpeg_path)
    stem = stem or 'narrated'
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', stem):
        raise ValueError('unsafe narration stem')
    identity = hashlib.sha256(json.dumps({'version':VERSION,'video':_hash(video_path),
                   'steps':steps,'source':backend.source}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    receipt = output / (stem + '.narration.json')
    expected_paths={str(output/(stem+suffix)) for suffix in ('.mp4','.wav','.vtt','.txt')}
    if receipt.is_file():
        try:
            saved = json.loads(receipt.read_text())
        except (OSError,ValueError):
            saved = {}
        cached_steps=saved.get('steps') or []
        scripts_current=(len(cached_steps)==len(steps) and all(
            cached.get('narration_text')==spoken_text(original)
            and cached.get('display_markdown')==visual_latex(original.get('display_markdown') or original.get('caption_tex')
                or original.get('observation') or original.get('caption') or spoken_text(original))
            for cached,original in zip(cached_steps,steps)))
        if (saved.get('input_hash') == identity and scripts_current and set(saved.get('output_hashes',{}))==expected_paths
                and all(Path(path).is_file() and not Path(path).is_symlink() and _hash(path)==digest
                        for path,digest in saved.get('output_hashes',{}).items())):
            return {**saved,'cache_hit':True}
    seconds, fps = _video_metadata(video_path, ffmpeg)
    if not steps or not math.isfinite(fps) or not 1 <= fps <= 120:
        raise ValueError('Narration requires steps and a bounded video frame rate')
    last = 0.0
    for step in steps:
        start, end = float(step.get('start',0)), float(step.get('end',0))
        if not all(math.isfinite(at) for at in (start,end)) or abs(start-last)>.06 or end<=start:
            raise ValueError('Narration steps must cover the video contiguously in order')
        if not spoken_text(step).strip('。 '):
            raise ValueError('Narration step has no spoken text')
        last = end
    if abs(last-seconds) > max(.06, 1/fps):
        raise ValueError('Narration steps must cover the entire original video')
    output.mkdir(parents=True, exist_ok=True)
    final_video, final_audio = output/(stem+'.mp4'), output/(stem+'.wav')
    captions, transcript = output/(stem+'.vtt'), output/(stem+'.txt')
    updated, clock, audio_quality = [], 0.0, []
    with tempfile.TemporaryDirectory(prefix='.narration-', dir=output) as folder:
        temporary = Path(folder)
        with wave.open(str(temporary/'voice.wav'),'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(SAMPLE_RATE)
            for index, step in enumerate(steps):
                raw, normalized = temporary/f'{index}.raw.wav', temporary/f'{index}.wav'
                text = spoken_text(step)
                synthesis = backend.synthesize(text, raw)
                _run([ffmpeg,'-nostdin','-v','error','-y','-i',raw,'-ac','1','-ar',SAMPLE_RATE,
                      '-c:a','pcm_s16le', normalized], timeout=180)
                with wave.open(str(normalized),'rb') as voice:
                    frames = voice.getnframes()
                    duration = frames/SAMPLE_RATE
                    if not 0 < duration <= MAX_NARRATED_SECONDS:
                        raise ValueError('TTS audio duration is invalid or exceeds limit')
                    pcm=voice.readframes(frames)
                    quality=_pcm_quality(pcm)
                    audio_quality.append({'step_id':step.get('step_id',f'step-{index+1}'),**quality})
                    original = float(step.get('input_motion_duration',float(step['end']) - float(step['start'])))
                    if not 0<original<=float(step['end'])-float(step['start'])+.04:
                        raise ValueError('input motion duration lies outside current source window')
                    target = math.ceil(max(original, duration+.25)*fps-1e-7)/fps
                    target_frames = round(target*SAMPLE_RATE)
                    audio.writeframesraw(pcm)
                    audio.writeframesraw(b'\0' * ((target_frames-frames)*2))
                start, end = round(clock,6), round(clock+target,6)
                row = {**deepcopy(step),'start':start,'end':end, 'narration_text':text,
                       'narration_duration':round(duration,6), 'source_start':step['start'],'source_end':step['end']}
                row.update(_speech_metadata(step, synthesis, frames, SAMPLE_RATE))
                row['caption_tex']=visual_latex(step.get('caption_tex') or step.get('display_markdown')
                                                or step.get('observation') or step.get('caption') or text)
                row['display_markdown']=visual_latex(step.get('display_markdown') or row['caption_tex'])
                # Remap recorded evidence moments to the same motion followed by hold.
                for key in ('evidence_moments','checkpoints'):
                    row[key] = [{**item,'at':round(start+min(float(item.get('at',step['start']))-float(step['start']),original),6)}
                                for item in row.get(key,[])]
                updated.append(row)
                clock = end
                if clock > MAX_NARRATED_SECONDS:
                    raise ValueError('Narrated video exceeds 30 minute resource limit')
        video_timing = _compose_narrated_video(video_path, updated, temporary/'voice.wav',
                                                temporary/'video.mp4', ffmpeg=ffmpeg, fps=fps)
        shutil.move(str(temporary/'video.mp4'), final_video)
        shutil.move(str(temporary/'voice.wav'), final_audio)
    from ...checks.offline import webvtt_cue_text
    vtt, words = ['WEBVTT',''], []
    for index, row in enumerate(updated):
        label = re.sub(r'[\r\n]', '', str(row.get('step_id') or f'step-{index+1}')).replace('-->','→')
        caption = row['display_markdown'].replace('-->','→')
        vtt.extend([label, f'{_stamp(row["start"])} --> {_stamp(row["end"])}',webvtt_cue_text(caption),''])
        words.append(f'{label}  {_stamp(row["start"])} — {_stamp(row["end"])}\n{caption}\n')
    captions.write_text('\n'.join(vtt)+'\n',encoding='utf-8')
    transcript.write_text('\n'.join(words),encoding='utf-8')
    result = {'schema_version':VERSION,'input_hash':identity,'video_path':str(final_video),'audio_path':str(final_audio),
              'captions_path':str(captions),'transcript_path':str(transcript),'steps':updated,'source':backend.source,
              'duration_seconds':clock,'video_timing':video_timing,'producer_version':VERSION,
              'audio_quality':{'non_silent':True,'steps':audio_quality},
              'cache_hit':False,'timing':'original motion followed by final-frame hold; complete decoded voice'}
    result = bind_narration_timeline(result, updated)
    result['output_hashes'] = {str(path):_hash(path) for path in (final_video,final_audio,captions,transcript)}
    pending = receipt.with_suffix('.tmp')
    pending.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    pending.replace(receipt)
    return result


def narration_speech_end(narration, steps=None):
    """Last spoken sample on the actual media clock, excluding silent final hold."""
    voices = narration.get('steps') or []
    timeline = steps if steps is not None else voices
    if not isinstance(voices,list) or not voices or not isinstance(timeline,list) or len(timeline)!=len(voices):
        raise ValueError('配音清单必须覆盖全部实际步骤')
    by_id={row.get('step_id'):row for row in timeline if row.get('step_id')}
    if len(by_id)!=len([row for row in timeline if row.get('step_id')]):
        raise ValueError('配音步骤身份重复')
    endpoint=0.0
    for index,voice in enumerate(voices):
        identity=voice.get('step_id')
        actual=by_id.get(identity) if identity else timeline[index]
        if actual is None:
            raise ValueError('配音步骤缺少实际媒体时钟')
        try:
            start=float(actual.get('start',voice.get('start')))
            duration=float(voice.get('duration',voice.get('narration_duration')))
            end=start+duration
            boundary=float(actual.get('end',end))
        except (TypeError,ValueError) as exc:
            raise ValueError('配音开始时间或实际语音时长缺失') from exc
        if (not all(math.isfinite(value) for value in (start,duration,boundary))
                or start<0 or duration<=0 or end>boundary+.1):
            raise ValueError('配音时长越过所属步骤或时钟无效')
        endpoint=max(endpoint,end)
    return endpoint


def audio_stream_problems(streams, *, video_seconds, narration=None, steps=None):
    """Bounded optional voice track policy shared by host/offline acceptance."""
    if not streams:
        return ['配音清单存在但视频没有旁白音轨'] if narration else []
    if len(streams) != 1:
        return ['音轨必须为零条或一条中文讲解 AAC']
    stream = streams[0]
    problems = []
    if stream.get('codec_name') != 'aac':
        problems.append('音轨编码必须为 AAC')
    if stream.get('channels') not in (1,2):
        problems.append('音轨必须为单声道或双声道')
    try:
        rate = int(stream.get('sample_rate',0))
        duration = float(stream.get('duration') or video_seconds)
        if not 16000 <= rate <= 48000:
            problems.append('音轨采样率必须在 16000..48000 Hz')
        if not math.isfinite(duration) or duration <= 0 or duration > video_seconds+.25:
            problems.append('音轨时长不得越过视频末尾')
        if narration:
            if not stream.get('duration'):
                problems.append('配音音轨缺少可核验的实际时长')
            else:
                required_end=narration_speech_end(narration,steps)
                audio_start=float(stream.get('start_time') or 0)
                if not math.isfinite(audio_start) or audio_start<-.1 or audio_start+duration+.1<required_end:
                    problems.append('旁白音轨截断：未覆盖最后实际配音终点')
    except (TypeError,ValueError):
        problems.append('音轨时长或采样率无效')
    return problems


def prepare_step_audio(steps, output_dir, *, backend=None, ffmpeg_path=None, cancel=None):
    """Synthesize plain-text voice on host before dispatching any generated code.

    Returned records contain only fixed file paths, hashes and decoded durations.
    Callers copy these files into the worker's explicitly bounded scratch tree.
    """
    from .local_narration import check_cancel
    check_cancel(cancel)
    backend = backend or configured_backend()
    ffmpeg = resolve_ffmpeg(ffmpeg_path)
    output = Path(output_dir).resolve(); output.mkdir(parents=True,exist_ok=True)
    records=[]
    for index, step in enumerate(steps):
        check_cancel(cancel)
        text = spoken_text(step)
        if not text.strip('。 '):
            raise ValueError('narration step has no spoken text')
        path = output / f'step-{index+1:03d}.wav'
        raw = output / f'.step-{index+1:03d}.raw.wav'
        try:
            synthesis = (backend.synthesize(text,raw,cancel=cancel) if getattr(backend,"supports_cancel",False) else backend.synthesize(text,raw))
            check_cancel(cancel)
            _run([ffmpeg,'-nostdin','-v','error','-y','-i',raw,'-ac','1','-ar',SAMPLE_RATE,
                  '-c:a','pcm_s16le',path],timeout=180)
        finally:
            raw.unlink(missing_ok=True)
        with wave.open(str(path),'rb') as voice:
            frames, sample_rate = voice.getnframes(), voice.getframerate()
            duration=frames/sample_rate
            _pcm_quality(voice.readframes(frames))
        if not 0 < duration <= MAX_NARRATED_SECONDS:
            raise ValueError('invalid narration duration')
        records.append({'step_id':step['step_id'],'path':str(path),'duration':duration,
                        'sha256':_hash(path),'text':text,
                        **_speech_metadata(step, synthesis, frames, sample_rate)})
    return {'source':backend.source,'steps':records}


def script_identity(step):
    """Identity includes review, displayed formula and unapplied directions."""
    from .narration_providers import identity
    return identity({'spoken_text': spoken_text(step), 'spoken_review': step.get('spoken_review'),
        'display_markdown': step.get('display_markdown') or step.get('caption_tex') or step.get('caption'),
        'director_notes': step.get('director_notes')})


def _speech_metadata(step, synthesis, frames, rate):
    alignment = (synthesis or {}).get('alignment') or {
        'status': 'unavailable', 'time_unit': 'seconds', 'clock': 'segment_audio', 'tracks': [],
        'content_review': 'not_verified_by_alignment'}
    # The final decoded PCM is authoritative after host normalization. No trim
    # or time stretch is performed; do not invent precision from missing words.
    for track in alignment['tracks']:
        for token in track['tokens']:
            if not 0 <= token['start_seconds'] <= token['end_seconds'] <= frames/rate:
                raise ValueError('provider alignment exceeds normalized PCM duration')
    return {'script_sha256': script_identity(step), 'sample_rate': rate, 'sample_count': frames,
        'display_markdown': visual_latex(step.get('display_markdown') or step.get('caption_tex')
            or step.get('observation') or step.get('caption') or spoken_text(step)),
        'spoken_review': deepcopy(step.get('spoken_review') or {'status': 'legacy_unreviewed'}),
        'director_notes': deepcopy(step.get('director_notes')), 'direction_status': 'not_applied',
        'alignment': deepcopy(alignment)}


def bind_narration_timeline(receipt, steps):
    """Attach actual MP4 step origins; keep provider tokens on segment PCM clock.

    Called by both production media packaging and legacy re-narration. It does
    not infer timing from caption lengths or place animation events.
    """
    narration_speech_end(receipt, steps)  # Validate coverage and real voice ends.
    result = deepcopy(receipt)
    by_id = {row['step_id']: row for row in steps if row.get('step_id')}
    for index, row in enumerate(result['steps']):
        actual = by_id[row['step_id']] if row.get('step_id') else steps[index]
        row['timeline'] = {'start_seconds': float(actual['start']), 'end_seconds': float(actual['end']),
                           'time_unit': 'seconds', 'clock': 'media'}
    return result


def narration_anchor(receipt, *, step_id, text_start, text_end, expected_text):
    """Query exact half-open Unicode script offsets against observed timing.

    Returns media-clock seconds only if the script and token boundaries match.
    Repeated phrases are disambiguated by explicit offsets; there is no search
    for a first occurrence and no interpolation inside sentence-only records.
    This API does not associate a mathematical object or execute a visual event.
    """
    def unresolved(reason):
        return {'status': 'unresolved', 'reason': reason, 'step_id': step_id}
    rows = [row for row in receipt.get('steps', []) if row.get('step_id') == step_id]
    if len(rows) != 1:
        return unresolved('missing or ambiguous step')
    row = rows[0]
    text = row.get('text', row.get('narration_text', ''))
    if (type(text_start) is not int or type(text_end) is not int or not 0 <= text_start < text_end <= len(text)
            or text[text_start:text_end] != expected_text):
        return unresolved('canonical text or offset mismatch')
    if not row.get('timeline'):
        return unresolved('actual media step clock is not available')
    for track in (row.get('alignment') or {}).get('tracks', []):
        if not track.get('canonical_match'):
            continue
        tokens = [t for t in track['tokens'] if text_start <= t['text_start'] and t['text_end'] <= text_end]
        if not tokens or tokens[0]['text_start'] != text_start or tokens[-1]['text_end'] != text_end:
            continue
        if any(a['text_end'] != b['text_start'] for a, b in zip(tokens, tokens[1:])):
            continue
        if ''.join(t['text'] for t in tokens) != expected_text:
            continue
        origin = row['timeline']['start_seconds']
        return {'status': 'aligned', 'step_id': step_id, 'text': expected_text,
            'text_start': text_start, 'text_end': text_end, 'offset_unit': 'unicode_codepoints_half_open',
            'start_seconds': origin + tokens[0]['start_seconds'], 'end_seconds': origin + tokens[-1]['end_seconds'],
            'time_unit': 'seconds', 'clock': 'media', 'timing_source': 'provider_response',
            'script_sha256': row['script_sha256'], 'audio_sha256': row.get('sha256'),
            'content_review': 'not_verified_by_alignment'}
    return unresolved('no exact provider timing for this canonical span')
