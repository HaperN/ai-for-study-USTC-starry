"""Validation and portable, content-addressed video evidence bundles.

Observations are records, not assertions of semantic correctness.
"""
from __future__ import annotations
import hashlib
import json
import math
import os
import shutil
import subprocess
from fractions import Fraction
import tempfile
from pathlib import Path, PurePosixPath
import unicodedata
from .course_library import local_file, sha256


def validate_video_evidence(manifest_path: Path) -> dict:
    path = Path(manifest_path)
    errors = []
    try:
        m = json.loads(path.read_text(encoding='utf-8'))
        if m.get('schema_version') != '1.0' or not isinstance(m.get('course_id'), str) or not m['course_id']:
            errors.append('invalid schema version or course_id')
        duration, width, height = m['duration_seconds'], m['width'], m['height']
        if not (type(duration) in (int, float) and math.isfinite(duration) and duration > 0 and type(width) is int and width > 0 and type(height) is int and height > 0):
            errors.append('invalid media dimensions or duration')
        tb = m['time_base'].split('/')
        if len(tb) != 2 or any(not x.isdigit() or int(x) <= 0 for x in tb): errors.append('invalid time_base')
        ids = set()
        paths = set()
        for f in m['files']:
            if not isinstance(f.get('id'), str) or not f['id'] or f['id'] in ids: errors.append('duplicate or invalid file id')
            ids.add(f['id'])
            name = f.get('path')
            if not isinstance(name, str): raise ValueError('file path must be a string')
            normalized = PurePosixPath(name).as_posix()
            key = unicodedata.normalize('NFC', normalized).casefold()
            if normalized != name or key in paths or key.split('/')[0] in ('manifest.json', 'index.html'):
                errors.append('noncanonical, duplicate or reserved file path')
            paths.add(key)
            try:
                p = local_file(path.parent, f['path'])
                if not p.is_file() or sha256(p) != f['sha256']: errors.append('file missing or hash mismatch: ' + f['id'])
            except (OSError, ValueError): errors.append('unsafe or unreadable path: ' + f['id'])
        media = next((f for f in m['files'] if f['id'] == m.get('media_id')), None)
        if not media:
            errors.append('missing media_id')
        else:
            try:
                media_path = local_file(path.parent, media['path'])
                probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(media_path)], stderr=subprocess.PIPE, timeout=30))
                stream = next(s for s in probe['streams'] if s['codec_type'] == 'video')
                declared_streams = m.get('streams')
                if declared_streams is not None:
                    keys = ('index','codec_type','codec_name','time_base')
                    actual = [{k: item[k] for k in keys} for item in probe['streams']]
                    if declared_streams != actual: errors.append('declared streams mismatch')
                observed_duration = float(probe['format']['duration'])
                if (stream['width'] != width or stream['height'] != height or Fraction(stream['time_base']) != Fraction(m['time_base']) or not math.isclose(observed_duration, duration, abs_tol=0.05)):
                    errors.append('media stream metadata mismatch')
            except (OSError, ValueError, ZeroDivisionError, KeyError, StopIteration, subprocess.SubprocessError) as exc:
                errors.append('media probe failed: ' + str(exc))
        obs_ids = set()
        for o in m['observations']:
            if not isinstance(o.get('id'), str) or not o['id'] or o['id'] in obs_ids: errors.append('duplicate or invalid observation id')
            obs_ids.add(o['id'])
            if o.get('source_id') != m.get('media_id') or o.get('source_id') not in ids: errors.append('observation source must be the declared video')
            if not isinstance(o.get('evidence_ids', []), list) or any(ref not in ids for ref in o.get('evidence_ids', [])): errors.append('missing evidence reference')
            start, end = o['start'], o['end']
            if not ((start is None and end is None) or (type(start) in (int,float) and type(end) in (int,float) and math.isfinite(start) and math.isfinite(end) and 0 <= start <= end <= duration)): errors.append('invalid time interval')
            if 'confirmation_pts_s' in o:
                confirmation = o['confirmation_pts_s']
                if not (end is not None and type(confirmation) in (int,float) and math.isfinite(confirmation) and end <= confirmation <= duration): errors.append('invalid confirmation time')
            if 'bbox' in o:
                b = o['bbox']
                if not (isinstance(b,list) and len(b)==4 and all(type(x) in (int,float) and math.isfinite(x) for x in b) and 0 <= b[0] < b[2] <= width and 0 <= b[1] < b[3] <= height): errors.append('invalid bbox')
            if o.get('host_verified') is True: errors.append('host verification is not established by manifest')
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        errors.append('invalid manifest: ' + str(exc))
    return {'ok': not errors, 'errors': errors}


def import_video_evidence(manifest_path: Path, destination: Path) -> dict:
    path = Path(manifest_path)
    result = validate_video_evidence(path)
    if not result['ok']: raise ValueError('; '.join(result['errors']))
    raw = path.read_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    dest = Path(destination); dest.mkdir(parents=True, exist_ok=True)
    target = dest / fingerprint
    if target.is_symlink(): raise ValueError('bundle target is a symlink')
    if target.exists():
        checked = validate_video_evidence(target / 'manifest.json')
        if not checked['ok'] or (target / 'manifest.json').read_bytes() != raw: raise ValueError('existing bundle is invalid')
        return {'bundle': str(target), 'fingerprint': fingerprint}
    stage = Path(tempfile.mkdtemp(prefix='.video-', dir=dest))
    try:
        m = json.loads(raw)
        for f in m['files']:
            source = local_file(path.parent, f['path'])
            out = stage / f['path']; out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, out)
            if sha256(out) != f['sha256']: raise ValueError('file changed during import')
            out.chmod(0o444)
        (stage / 'manifest.json').write_bytes(raw)
        (stage / 'manifest.json').chmod(0o444)
        checked = validate_video_evidence(stage / 'manifest.json')
        if not checked['ok']: raise ValueError('copied bundle invalid: ' + '; '.join(checked['errors']))
        os.rename(stage, target)
        for directory in sorted((p for p in target.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True): directory.chmod(0o555)
        target.chmod(0o555)
    finally:
        if stage.exists(): shutil.rmtree(stage)
    return {'bundle': str(target), 'fingerprint': fingerprint}


def project_video_evidence(bundle: Path, output: Path, *, course_id: str | None = None) -> dict:
    """Create a standalone local evidence view; never infer course membership."""
    import html
    from urllib.parse import quote
    bundle, output = Path(bundle), Path(output)
    result = validate_video_evidence(bundle / 'manifest.json')
    if not result['ok']: raise ValueError('; '.join(result['errors']))
    m = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
    if course_id is not None and course_id != m['course_id']: raise ValueError('course mismatch')
    if output.exists() and any(output.iterdir()): raise ValueError('output must be empty')
    output.mkdir(parents=True, exist_ok=True)
    manifest_bytes = (bundle/'manifest.json').read_bytes()
    (output/'manifest.json').write_bytes(manifest_bytes)
    if hashlib.sha256(manifest_bytes).hexdigest() != bundle.name and len(bundle.name) == 64: raise ValueError('bundle fingerprint mismatch')
    for f in m['files']:
        p = output / f['path']; p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local_file(bundle, f['path']), p)
        if sha256(p) != f['sha256']: raise ValueError('file changed during projection')
    files = {f['id']: f for f in m['files']}
    video = files[m['media_id']]['path']
    groups = {}
    for o in m['observations']:
        label = html.escape(str(o.get('text', o['id'])))
        links = ''.join(f' <a href="{html.escape(quote(files[ref]["path"], safe="/"), quote=True)}">{html.escape(ref)}</a>' for ref in o.get('evidence_ids', []))
        category = str(o.get('category', '观察'))
        jump = f'<button type="button" data-time="{o["start"]}">{o["start"]} 秒</button>' if o['start'] is not None else '无时间定位'
        time_kind = html.escape(str(o.get('time_kind', 'source_seconds')))
        model_status = ' 模型自报：' + html.escape(str(o['model_status'])) if 'model_status' in o else ''
        groups.setdefault(category, []).append(f'<li>{jump} <small>{time_kind}{model_status}</small> {label}{links}</li>')
    sections = ''.join('<section><h2>' + html.escape(category) + '</h2><ul>' + ''.join(rows) + '</ul></section>' for category, rows in groups.items())
    assets = ''.join('<li><a href="' + html.escape(quote(f['path'],safe='/'),quote=True) + '">' + html.escape(f['id']) + '</a> <small>' + html.escape(f['sha256']) + '</small></li>' for f in m['files'])
    scope = html.escape(str(m.get('scope', '')))
    video_hash = html.escape(files[m['media_id']]['sha256'])
    page = '''<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>视频证据回看</title><style>body{max-width:70rem;margin:auto;padding:1rem;font:16px/1.6 system-ui}video{max-width:100%%;height:auto}li{margin:.6em 0}small{color:#555;overflow-wrap:anywhere}button{font:inherit}</style></head><body><h1>视频证据回看</h1><p>观察记录和模型候选不代表宿主语义核验或完整理解。时间区间为记录范围，并非完整覆盖。</p><p>%s</p><p>原片 SHA-256: <small>%s</small></p><video controls preload="metadata" width="800" src="%s"></video><h2>观察与候选</h2>%s<details><summary>原始文件与记录</summary><p><a href="manifest.json">证据清单</a></p><ul>%s</ul></details><script>document.querySelectorAll('[data-time]').forEach(b=>b.addEventListener('click',()=>{const v=document.querySelector('video');v.currentTime=Number(b.dataset.time);v.focus()}));</script></body></html>''' % (scope, video_hash, html.escape(quote(video, safe='/'), quote=True), sections, assets)
    (output / 'index.html').write_text(page, encoding='utf-8')
    checked = validate_video_evidence(output / 'manifest.json')
    if not checked['ok'] or (output/'manifest.json').read_bytes() != manifest_bytes:
        raise ValueError('projection verification failed: ' + '; '.join(checked['errors']))
    return {'index': str(output / 'index.html'), 'course_id': m['course_id'], 'fingerprint': hashlib.sha256((bundle / 'manifest.json').read_bytes()).hexdigest()}


def attach_video_evidence(manifest_path: Path, parse_root: Path, *, book_id: str, unit_id: str) -> dict:
    """Explicitly attach a bundle to a unit in the same course."""
    from .jsonio import write_json_atomic
    manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    if not book_id or not unit_id or manifest.get('course_id') != book_id:
        raise ValueError('course mismatch or empty unit')
    root = Path(parse_root) / 'video-evidence'
    imported = import_video_evidence(manifest_path, root / 'bundles')
    pointer = root / 'current.json'
    index = json.loads(pointer.read_text(encoding='utf-8')) if pointer.exists() else {'schema_version':'VideoEvidenceIndexV1', 'book_id':book_id, 'units':{}}
    if index.get('schema_version') != 'VideoEvidenceIndexV1' or index.get('book_id') != book_id:
        raise ValueError('course mismatch in video index')
    index['units'][unit_id] = imported['fingerprint']
    write_json_atomic(pointer, index)
    return imported


def load_attached_videos(parse_root: Path, *, book_id: str) -> list[tuple[str, Path, dict]]:
    root = Path(parse_root) / 'video-evidence'
    pointer = root / 'current.json'
    if not pointer.exists(): return []
    index = json.loads(pointer.read_text(encoding='utf-8'))
    if index.get('schema_version') != 'VideoEvidenceIndexV1' or index.get('book_id') != book_id:
        raise ValueError('course mismatch in video index')
    if not isinstance(index.get('units'), dict): raise ValueError('invalid video index')
    result = []
    for unit, fingerprint in sorted(index['units'].items()):
        if not isinstance(unit,str) or not unit or not isinstance(fingerprint,str) or len(fingerprint)!=64 or any(c not in '0123456789abcdef' for c in fingerprint): raise ValueError('invalid video index entry')
        bundle = local_file(root, 'bundles/' + fingerprint)
        manifest_path = bundle / 'manifest.json'
        if sha256(manifest_path) != fingerprint: raise ValueError('video manifest fingerprint mismatch')
        validation = validate_video_evidence(manifest_path)
        if not validation['ok']: raise ValueError('; '.join(validation['errors']))
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest['course_id'] != book_id: raise ValueError('course mismatch in video bundle')
        result.append((unit, bundle, manifest))
    return result


def frozen_video_evidence(parse_root: Path, *, book_id: str, unit_id: str) -> list[dict]:
    rows=[]
    for unit, bundle, manifest in load_attached_videos(parse_root, book_id=book_id):
        if unit != unit_id: continue
        fingerprint = sha256(bundle / 'manifest.json')
        relative = str((bundle / 'manifest.json').relative_to(Path(parse_root)))
        for i, observation in enumerate(manifest['observations']):
            if observation['start'] is None: continue
            rows.append({'fingerprint': fingerprint, 'semantic_status': 'unverified', 'observation': observation,
                'source_ref': {'kind':'video_observation', 'id':observation['id'], 'unit_id':unit,
                    'path':relative, 'json_pointer':f'/observations/{i}', 'sha256':fingerprint}})
    return rows


def video_input_files(parse_root: Path, *, book_id: str) -> list[Path]:
    pointer = Path(parse_root) / 'video-evidence/current.json'
    inputs = [pointer]
    for _, bundle, manifest in load_attached_videos(parse_root, book_id=book_id):
        inputs.append(bundle/'manifest.json')
        inputs.extend(local_file(bundle, f['path']) for f in manifest['files'])
    return inputs


def extract_video_window(source: Path, output: Path, *, start: float, end: float) -> dict:
    """Decode a bounded source window and frame, recording commands and failures.

    Requested times are source seconds; extracted files reset their own timeline.
    A frame selected by ffmpeg is not represented as an independently measured PTS.
    """
    source, output = Path(source).resolve(), Path(output)
    if not source.is_file(): raise ValueError('source missing')
    source_hash = sha256(source)
    try:
        probe = json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(source)], stderr=subprocess.PIPE, timeout=30))
        duration = float(probe['format']['duration'])
        if not any(s.get('codec_type') == 'video' for s in probe['streams']): raise ValueError('video stream missing')
    except (OSError, KeyError, ValueError, subprocess.SubprocessError) as exc:
        raise ValueError('source probe failed: ' + str(exc)) from exc
    if not (type(start) in (int,float) and type(end) in (int,float) and math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= duration):
        raise ValueError('invalid source interval')
    if output.exists() and any(output.iterdir()): raise ValueError('output must be empty')
    output.mkdir(parents=True, exist_ok=True)
    commands = [
        ('frame.png', ['ffmpeg','-v','error','-i',str(source),'-ss',str(start),'-frames:v','1','-y',str(output/'frame.png')]),
        ('window.mp4', ['ffmpeg','-v','error','-i',str(source),'-ss',str(start),'-t',str(end-start),'-map','0:v:0','-map','0:a:0?','-c:v','libx264','-c:a','aac','-y',str(output/'window.mp4')]),
    ]
    if any(s.get('codec_type') == 'audio' for s in probe['streams']):
        commands.append(('audio.wav', ['ffmpeg','-v','error','-i',str(source),'-ss',str(start),'-t',str(end-start),'-map','0:a:0','-ac','1','-ar','16000','-y',str(output/'audio.wav')]))
    results = []
    for name, cmd in commands:
        try:
            run = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            status = {'file':name, 'command':cmd, 'exit_code':run.returncode, 'stderr':run.stderr,
                      'sha256':sha256(output/name) if run.returncode == 0 and (output/name).is_file() else None}
        except (OSError, subprocess.SubprocessError) as exc:
            status = {'file':name, 'command':cmd, 'exit_code':None, 'stderr':str(exc), 'sha256':None}
        results.append(status)
    source_hash_after = sha256(source)
    result = {'ok':source_hash == source_hash_after and all(r['exit_code']==0 and r['sha256'] for r in results), 'source':str(source), 'source_sha256':source_hash,
              'source_changed':source_hash != source_hash_after, 'source_sha256_after':source_hash_after,
              'requested_source_interval_seconds':[start,end], 'timestamp_note':'Requested interval, not an exact decoded frame PTS. Output timeline starts at zero.', 'results':results}
    (output/'extraction.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return result
