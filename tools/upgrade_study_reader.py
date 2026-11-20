"""Upgrade an existing offline reader into a separate, inspectable study copy.

No model calls, source edits, or historical approval promotion. Existing reader
data is parsed as JSON, never evaluated as JavaScript. Narration is resumable.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))


def _digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return 'sha256:' + h.hexdigest()


def load_reader_data(source):
    source = Path(source)
    path = source / 'data/data.js' if source.is_dir() else source
    text = path.read_text(encoding='utf-8-sig')
    match = re.search(r'\bwindow\.__DATA__\s*=\s*', text)
    if not match:
        raise ValueError('缺少 reader JSON 数据赋值')
    data, end = json.JSONDecoder().raw_decode(text[match.end():])
    tail = text[match.end() + end:].strip()
    if tail not in ('', ';'):
        raise ValueError('reader data 包含额外脚本，不执行')
    if not isinstance(data, dict) or not isinstance(data.get('units'), dict):
        raise ValueError('reader data 缺少 units')
    return data


def restore_empty_units(data, archive):
    """Recover only empty lessons from actual nonempty historical revisions."""
    if not archive:
        return []
    from aiforstudy.blocks.content.reader_document import project_reader
    from aiforstudy.core.review_mode import review_mode
    restored = []
    with zipfile.ZipFile(archive) as z:
        entries = z.namelist()
        book_prefix = '/books/' + str(data.get('book', '')) + '/'
        for uid, unit in data['units'].items():
            reader = unit.get('reader_document') or {}
            if any(b.get('group') == 'body_markdown' and b.get('markdown', '').strip()
                   for b in reader.get('blocks', [])):
                if unit.get('study_recovery'):
                    restore_reader_layout(unit)
                continue
            suffix = '/products/content/' + uid + '/revisions/'
            candidates = sorted((name for name in entries if suffix in name
                and name.endswith('.json') and book_prefix in name), reverse=True)
            for name in candidates:
                content = json.loads(z.read(name))
                if (not isinstance(content, dict) or content.get('unit_id') != uid
                    or not isinstance(content.get('body_markdown'), str)
                    or not content['body_markdown'].strip()
                    or any(key in content for key in ('role_task', 'original_response', 'errors'))):
                    continue
                catalog = reader.get('reference_catalog') or {'entries': []}
                with review_mode('balanced'):
                    projected = project_reader(content, catalog).as_dict()
                unit['reader_document'] = projected
                unit['title'] = content.get('title') or unit.get('title', uid)
                # Old slots refer to an empty document. A recovered lesson is
                # deliberately reflowed, without changing historical media.
                restore_reader_layout(unit)
                unit['review_outcome'] = {'status': 'unverified',
                    'reasons': ['从非空历史修订恢复，未重新进行模型审核']}
                record = {'unit_id': uid, 'revision_path': name,
                    'source_sha256': 'sha256:' + hashlib.sha256(z.read(name)).hexdigest(),
                    'body_characters': len(content['body_markdown'])}
                unit['study_recovery'] = record
                restored.append(record)
                break
    return restored


def restore_reader_layout(unit):
    """Rebind recovered media to the actual recovered text, retaining provenance."""
    from aiforstudy.blocks.render.layout import layout_from_reader_media
    from aiforstudy.core.digest import digest_of
    document = unit['reader_document']
    blocks = {b['json_pointer']: b for b in document['blocks']}
    body = blocks.get('/body_markdown')
    if body is None:
        raise ValueError('恢复单元缺正文块')
    for row in unit.get('reader_media', []):
        old = deepcopy(row['placement'])
        block = blocks.get(old.get('target_pointer')) or body
        raw = block.get('raw_text', block.get('markdown', ''))
        anchor = old.get('anchor_text', '')
        if not anchor or anchor not in raw:
            block = body
            raw = block.get('raw_text', block.get('markdown', ''))
            anchor = raw
        start = raw.find(anchor)
        row.setdefault('study_original_placement', old)
        row['placement'] = {**old, 'target_pointer': block['json_pointer'],
            'owner_id': block['owner_id'], 'anchor_text': anchor,
            'span': [start, start + len(anchor)], 'field_hash': digest_of(raw)}
        row['review_outcome'] = {'status': 'unverified',
            'reasons': ['随恢复正文重新绑定，原媒体画面未改，未重新模型审核']}
    unit['reader_layout'] = layout_from_reader_media(document, unit.get('reader_media', []))


def prepare_legacy_steps(steps):
    """Account for a real unlabelled opening, without accepting internal gaps."""
    result = deepcopy(steps)
    if not result:
        raise ValueError('动画没有步骤')
    start = float(result[0]['start'])
    if not math.isfinite(start) or start < 0:
        raise ValueError('动画起点非法')
    if start > 0:
        result[0].update(start=0, source_intro_seconds=start)
    last = 0.0
    for row in result:
        if abs(float(row['start']) - last) > .06 or float(row['end']) <= float(row['start']):
            raise ValueError('原动画时间轴存在内部空缺或重叠')
        last = float(row['end'])
    return result


def retime_timestamp(timestamp, steps):
    at = float(timestamp)
    for step in steps:
        first, last = float(step['source_start']), float(step['source_end'])
        if first - .06 <= at <= last + .06:
            return round(float(step['start']) + max(0, min(at - first, last - first)), 6)
    raise ValueError('关键帧时间不在原视频步骤范围内')


def _resource(kind, path, mime):
    path = Path(path)
    return {'kind': kind, 'relative_path': path.name, 'sha256': _digest(path),
            'bytes': path.stat().st_size, 'mime': mime}


def _safe_media(root, base, relative):
    path = (root / str(base) / str(relative)).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('媒体路径越出阅读包')
    return path


def narration_is_current(row, original, source, output):
    """A ready label is not evidence: recheck source and every shipped byte."""
    try:
        from aiforstudy.blocks.media.narration import VERSION
        narration = row.get('narration') or {}
        if narration.get('status') != 'ready' or narration.get('producer_version') != VERSION:
            return False
        original_video = _safe_media(source, original['base'], original['resources']['mp4']['relative_path'])
        if _digest(original_video) != narration.get('source_video_sha256'):
            return False
        resources = row['resources']
        if not {'mp4', 'audio', 'captions', 'transcript'} <= resources.keys():
            return False
        manifest = row.get('resource_manifest')
        if not isinstance(manifest, list):
            return False
        for resource in [*resources.values(), *manifest]:
            if not isinstance(resource, dict):
                return False
            file = _safe_media(output, row['base'], resource['relative_path'])
            if (not file.is_file() or file.stat().st_size != resource.get('bytes')
                    or _digest(file) != resource.get('sha256')):
                return False
        return True
    except (KeyError, OSError, TypeError, ValueError):
        return False


def _narrate_row(uid, index, animation, source, output, narrator):
    from aiforstudy.blocks.media.narration import VERSION
    row = deepcopy(animation)
    old_base = Path(row['base'])
    if old_base.is_absolute() or '..' in old_base.parts:
        raise ValueError('动画 base 必须是安全相对路径')
    resources = row.get('resources') or {}
    mp4 = resources.get('mp4') or {}
    relative = mp4.get('relative_path', 'video.mp4')
    video = (source / old_base / relative).resolve()
    if not video.is_relative_to(source.resolve()) or not video.is_file():
        raise ValueError('动画原视频缺失或越出阅读包')
    ident = re.sub(r'[^A-Za-z0-9_.-]', '-', row.get('request_id') or str(index))
    folder = output / 'assets/narrated' / uid / ident
    result = narrator(video, prepare_legacy_steps(row['steps']), folder)
    updated_resources = {}
    for kind, key, mime in [('mp4', 'video_path', 'video/mp4'),
                            ('captions', 'captions_path', 'text/vtt; charset=utf-8'),
                            ('transcript', 'transcript_path', 'text/plain; charset=utf-8'),
                            ('audio', 'audio_path', 'audio/wav')]:
        updated_resources[kind] = _resource(kind, result[key], mime)
    poster = resources.get('poster')
    if poster:
        src = source / old_base / poster['relative_path']
        if src.is_file():
            dest = folder / 'poster.png'
            shutil.copy2(src, dest)
            updated_resources['poster'] = _resource('poster', dest, 'image/png')
    extra_resources = []
    for resource in animation.get('resource_manifest', []):
        if resource.get('kind') != 'keyframe':
            continue
        relative = Path(resource['relative_path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('关键帧路径非法')
        src = source / old_base / relative
        if not src.is_file():
            raise ValueError('原动画关键帧缺失')
        dest = folder / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        entry = {**resource, **_resource('keyframe', dest, resource.get('mime', 'image/png')),
                 'relative_path': relative.as_posix()}
        if 'timestamp' in resource:
            entry['source_timestamp'] = resource['timestamp']
            entry['timestamp'] = retime_timestamp(resource['timestamp'], result['steps'])
        extra_resources.append(entry)
    row.update(base=folder.relative_to(output).as_posix() + '/', resources=updated_resources,
        resource_manifest=[*updated_resources.values(), *extra_resources], steps=result['steps'],
        captions_text=Path(result['captions_path']).read_text(),
        transcript_text=Path(result['transcript_path']).read_text())
    row['narration'] = {'status': 'ready', 'source': result['source'],
        'producer_version': result.get('producer_version', result.get('schema_version', VERSION)),
        'duration_seconds': result['duration_seconds'], 'source_video_sha256': _digest(video),
        'original_base': animation['base'], 'source_request_id': animation.get('request_id')}
    row.setdefault('technical_report', {})['narration'] = {'source': result['source'],
        'steps': [{'step_id': step.get('step_id', ''), 'duration': step['narration_duration']}
                  for step in result['steps']]}
    return row


def upgrade_reader(source, output, *, archive=None, narrate=False, narrator=None,
                   workers=4, resume=False, progress=None):
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output or source.is_relative_to(output) or output.is_relative_to(source):
        raise ValueError('升级输出必须与原阅读包独立')
    original_data = source / 'data/data.js'
    source_hash = _digest(original_data)
    report_path = output / 'upgrade-report.json'
    if output.exists() and any(output.iterdir()):
        if not resume:
            raise FileExistsError('输出目录非空；明确使用 --resume 才能继续本次升级')
        saved = json.loads(report_path.read_text())
        if saved.get('source_data_sha256') != source_hash:
            raise ValueError('续跑来源已改变')
        data = load_reader_data(output)
        restored = saved.get('restored_units', [])
        for unit in data['units'].values():
            if unit.get('study_recovery'):
                restore_reader_layout(unit)
    else:
        shutil.copytree(source, output, dirs_exist_ok=True)
        data = load_reader_data(source)
        restored = restore_empty_units(data, archive)
    report = {'schema_version': 'StudyReaderUpgradeV1', 'source': str(source),
        'source_data_sha256': source_hash, 'unit_count': len(data['units']),
        'restored_units': restored, 'narration_requested': bool(narrate),
        'narrated_animations': 0, 'animation_count': sum(len(a) for a in data.get('animations', {}).values()),
        'narration_errors': [], 'original_source_preserved': True}

    def checkpoint(final=False):
        report['narrated_animations'] = sum(1 for rows in data.get('animations', {}).values()
            for animation in rows if (animation.get('narration') or {}).get('status') == 'ready')
        report['complete'] = bool(final and not report['narration_errors'] and
            (not narrate or report['narrated_animations'] == report['animation_count']))
        data['study_reader'] = {**data.get('study_reader', {}), 'version': '1.0', 'theme': 'light-text-dark-visuals',
            'proofs_collapsed': True, 'examples_collapsed': True,
            'narrated_animations': report['narrated_animations']}
        text = 'window.__DATA__ = ' + json.dumps(data, ensure_ascii=False) + ';\n'
        temporary = output / 'data/data.js.tmp'
        temporary.write_text(text, encoding='utf-8')
        temporary.replace(output / 'data/data.js')
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    checkpoint()
    if narrate:
        if narrator is None:
            from aiforstudy.blocks.media.narration import narrate_animation
            narrator = narrate_animation
        originals = load_reader_data(source).get('animations', {})
        pending = []
        for uid, rows in data.get('animations', {}).items():
            for i, row in enumerate(rows):
                matches = [a for a in originals.get(uid, []) if a.get('request_id') == row.get('request_id')]
                if len(matches) != 1:
                    report['narration_errors'].append({'unit_id': uid, 'index': i,
                        'error': '原动画身份不唯一或已缺失'})
                    continue
                original = matches[0]
                if narration_is_current(row, original, source, output):
                    continue
                data['animations'][uid][i] = deepcopy(original)
                pending.append((uid, i, original))
        checkpoint()
        with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
            futures = {pool.submit(_narrate_row, uid, i, row, source, output, narrator): (uid, i)
                       for uid, i, row in pending}
            for number, future in enumerate(as_completed(futures), 1):
                uid, i = futures[future]
                try:
                    data['animations'][uid][i] = future.result()
                except Exception as exc:
                    report['narration_errors'].append({'unit_id': uid, 'index': i,
                        'error': type(exc).__name__ + ': ' + str(exc)})
                checkpoint()
                if progress:
                    progress(number, len(pending), uid, bool(report['narration_errors']))
    from blocks.render.build import write_reader_site
    write_reader_site(data, output, book='复变函数 · 交互课堂')
    checkpoint(final=True)
    if _digest(original_data) != source_hash:
        raise RuntimeError('原阅读数据在升级期间发生变化')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--narrate', action='store_true')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args(argv)
    report = upgrade_reader(args.source, args.output, archive=args.archive,
        narrate=args.narrate, resume=args.resume, workers=args.workers,
        progress=lambda n, total, uid, errors: print(f'{n}/{total} {uid}', flush=True))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
