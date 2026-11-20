"""Extract planned teaching frames without rebuilding videos, voices, or the book.

Example: python tools/curate_textbook_posters.py --output-dir ../delivery/BOOK
Run after narration: times are derived from the current neural-media.json steps.
Only listed poster PNGs, their manifest bytes/hashes, and the report are written.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory

import imageio_ffmpeg
from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
IMMUTABLE_SUFFIXES = {'.mp4', '.wav', '.vtt', '.txt'}


def metadata(path):
    content = path.read_bytes()
    return {'bytes': len(content), 'sha256': 'sha256:' + hashlib.sha256(content).hexdigest()}


def immutable_snapshot(output):
    return {str(p.relative_to(output)): metadata(p)
            for p in sorted((output / 'book').rglob('*'))
            if p.is_file() and p.suffix.lower() in IMMUTABLE_SUFFIXES}


def inside(root, path):
    path = path.resolve()
    if not path.is_relative_to(root):
        raise ValueError(f'Resource outside output: {path}')
    return path


def frame_stats(path):
    with Image.open(path) as image:
        image = image.convert('RGB')
        channels = image.split()
        minimum = ImageChops.darker(ImageChops.darker(channels[0], channels[1]), channels[2])
        mask = minimum.point(lambda value: 255 if value < 250 else 0)
        nonwhite = mask.histogram()[255]
        return {'size': list(image.size), 'nonwhite_pixels_below_250': nonwhite,
                'nonwhite_fraction': nonwhite / (image.width * image.height),
                'nonwhite_bbox': list(mask.getbbox()) if mask.getbbox() else None}


def verify_manifest(output, media):
    count = 0
    for row in media['animations']:
        animation = row['animation']
        base = inside(output, output / 'book' / animation['base'])
        for resource in animation['resources'].values():
            matches = [r for r in animation['resource_manifest'] if r['kind'] == resource['kind']]
            if len(matches) != 1 or matches[0] != resource:
                raise ValueError(f"Manifest differs from resources: {row['unit_id']} {resource['kind']}")
            actual = metadata(inside(output, base / resource['relative_path']))
            if actual != {k: resource[k] for k in ('bytes', 'sha256')}:
                raise ValueError(f"File differs from manifest: {row['unit_id']} {resource['kind']}")
            count += 1
    return {'checked_resource_count': count, 'all_files_match_bytes_and_sha256': True,
            'all_resource_manifest_entries_match_resources': True}


def curate(output, plans_path, dry_run=False):
    output = output.resolve()
    media_path = output / 'neural-media.json'
    original_json = media_path.read_bytes()
    media = json.loads(original_json)
    plans = json.loads(plans_path.read_text(encoding='utf-8'))
    if plans['schema_version'] != 'TextbookPosterPlanV1':
        raise ValueError('Unsupported poster plan schema')
    manifest_before = verify_manifest(output, media)
    before = immutable_snapshot(output)
    rows, pending, seen = [], [], set()
    with TemporaryDirectory(prefix='textbook-posters-') as folder:
        for index, plan in enumerate(plans['posters']):
            key = (plan['unit_id'], plan['request_id'])
            if key in seen:
                raise ValueError(f'Duplicate poster plan: {key}')
            seen.add(key)
            matches = [row['animation'] for row in media['animations']
                       if (row['unit_id'], row['request_id']) == key]
            if len(matches) != 1:
                raise ValueError(f'Expected exactly one animation: {key}')
            animation = matches[0]
            steps = [s for s in animation['steps'] if s['step_id'] == plan['step_id']]
            fraction = float(plan['fraction'])
            if len(steps) != 1 or not 0 < fraction < 1:
                raise ValueError(f'Invalid step or fraction: {key}')
            step = steps[0]
            if float(step['end']) <= float(step['start']):
                raise ValueError(f'Invalid step interval: {key}')
            seek = float(step['start']) + (float(step['end']) - float(step['start'])) * fraction
            base = inside(output, output / 'book' / animation['base'])
            video = inside(output, base / animation['resources']['mp4']['relative_path'])
            poster = inside(output, base / animation['resources']['poster']['relative_path'])
            video_before = metadata(video)
            if video_before != {k: animation['resources']['mp4'][k] for k in ('bytes', 'sha256')}:
                raise ValueError(f'Video does not match manifest: {key}')
            manifests = [r for r in animation['resource_manifest'] if r['kind'] == 'poster']
            if len(manifests) != 1:
                raise ValueError(f'Expected one poster manifest entry: {key}')
            candidate = Path(folder) / f'{index}.png'
            # Select by original decoded PTS, without resizing, overlays, or redraws.
            result = subprocess.run([
                imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'info',
                '-i', str(video), '-vf', f'select=gte(t\\,{seek:.9f}),showinfo',
                '-frames:v', '1', '-fps_mode', 'vfr', '-y', str(candidate)
            ], check=True, capture_output=True, text=True)
            pts = re.search(r'\bn:\s*0\s+pts:.*?pts_time:([\d.]+)', result.stderr)
            if not pts:
                raise ValueError(f'No decoded frame timestamp: {key}')
            stats = frame_stats(candidate)
            if stats['nonwhite_fraction'] < 0.002:
                raise ValueError(f'Blank or near-blank planned poster: {key}')
            updated = metadata(candidate)
            rows.append({**plan, 'step_start_seconds': step['start'], 'step_end_seconds': step['end'],
                         'requested_seek_seconds': round(seek, 9),
                         'selected_frame_pts_seconds': float(pts.group(1)),
                         'poster': str(poster.relative_to(output)),
                         'before': metadata(poster), 'after': updated, 'image_checks': stats,
                         'source_video': str(video.relative_to(output)),
                         'source_video_sha256': video_before['sha256']})
            animation['resources']['poster'].update(updated)
            manifests[0].update(updated)
            pending.append((candidate, poster))
        after = immutable_snapshot(output)
        changed = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
        if changed:
            raise ValueError(f'Immutable media changed: {changed}')
        if media_path.read_bytes() != original_json:
            raise ValueError('neural-media.json changed during extraction; retry with current data')
        report = {
            'schema_version': 'TextbookPosterCurationReportV1',
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'method': 'Original narrated.mp4 frame selected by step_id + fraction; FFmpeg decode/select by PTS; no media rebuild',
            'plan': str(plans_path.resolve()), 'plan_sha256': metadata(plans_path)['sha256'],
            'visual_review': plans.get('visual_review', ''),
            'poster_count': len(rows), 'posters': rows,
            'immutable_media': {'checked_file_count': len(before), 'all_unchanged': True,
                                'changed_files': changed, 'sha256_before_and_after': before},
            'resource_consistency': {'before': manifest_before, 'after': None},
            'book_rebuilt': False, 'dry_run': dry_run,
        }
        if not dry_run:
            for candidate, poster in pending:
                poster.write_bytes(candidate.read_bytes())
            media_path.write_text(json.dumps(media, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            # A final independent snapshot covers the entire operation, including writes.
            if immutable_snapshot(output) != before:
                raise RuntimeError('Immutable media changed after poster writes')
            report['resource_consistency']['after'] = verify_manifest(output, media)
            (output / 'poster-curation-report.json').write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--plans', type=Path, default=ROOT / 'data/study-revision-20261029/poster-plans.json')
    parser.add_argument('--dry-run', action='store_true', help='Decode and validate without writing output files')
    args = parser.parse_args()
    result = curate(args.output_dir, args.plans, args.dry_run)
    print(json.dumps({'poster_count': result['poster_count'],
                      'immutable_media_files': result['immutable_media']['checked_file_count'],
                      'all_immutable_media_unchanged': result['immutable_media']['all_unchanged'],
                      'dry_run': result['dry_run'],
                      'frames': [{k: p[k] for k in ('unit_id', 'step_id', 'fraction', 'selected_frame_pts_seconds', 'image_checks')}
                                 for p in result['posters']]}, ensure_ascii=False, indent=2))
