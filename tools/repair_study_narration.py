"""Upgrade existing narrated videos to a verified frame clock, reusing voices."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

from tools.upgrade_study_reader import load_reader_data, _digest, prepare_legacy_steps
from aiforstudy.blocks.media.narration import rebuild_narrated_video, VERSION
from blocks.render.build import write_reader_site


def repair(source, output, workers=4):
    source, output = Path(source).resolve(), Path(output).resolve()
    data, original = load_reader_data(output), load_reader_data(source)
    rows = [(uid, i, row) for uid, unit_rows in data['animations'].items() for i, row in enumerate(unit_rows)]
    report_path = output / 'upgrade-report.json'
    report = json.loads(report_path.read_text())
    report.update(complete=False, video_clock_repair={'producer_version': VERSION, 'finished': 0, 'errors': []})
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    def rebuild(uid, index, row):
        row = deepcopy(row)
        old = next(a for a in original['animations'][uid] if a['request_id'] == row['request_id'])
        original_video = (source / old['base'] / old['resources']['mp4']['relative_path']).resolve()
        folder = (output / row['base']).resolve()
        if not original_video.is_relative_to(source) or not folder.is_relative_to(output):
            raise ValueError('unsafe audio repair path')
        receipt_file = folder / 'narrated.narration.json'
        receipt = json.loads(receipt_file.read_text())
        target = folder / row['resources']['mp4']['relative_path']
        result = rebuild_narrated_video(original_video, receipt['steps'], folder / 'narrated.wav', target)
        row['resources']['mp4'].update(sha256='sha256:' + result['sha256'], bytes=result['bytes'])
        for resource in row['resource_manifest']:
            if resource['kind'] == 'mp4':
                resource.update(sha256='sha256:' + result['sha256'], bytes=result['bytes'])
        row['narration'].update(producer_version=VERSION, video_timing=result['video_timing'])
        receipt.update(schema_version=VERSION, producer_version=VERSION, video_timing=result['video_timing'])
        receipt['input_hash'] = hashlib.sha256(json.dumps({'version': VERSION,
            'video': _digest(original_video).split(':', 1)[1], 'steps': prepare_legacy_steps(old['steps']),
            'source': receipt['source']}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        receipt['output_hashes'][str(target)] = result['sha256']
        receipt_file.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        return row

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(rebuild, uid, i, row): (uid, i) for uid, i, row in rows}
        for number, future in enumerate(as_completed(futures), 1):
            uid, index = futures[future]
            try:
                data['animations'][uid][index] = future.result()
            except Exception as exc:
                report['video_clock_repair']['errors'].append({'unit_id': uid, 'index': index, 'error': str(exc)})
            report['video_clock_repair']['finished'] = number
            temporary = output / 'data/data.js.tmp'
            temporary.write_text('window.__DATA__ = ' + json.dumps(data, ensure_ascii=False) + ';\n')
            temporary.replace(output / 'data/data.js')
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(f'{number}/{len(rows)} {uid}', flush=True)
    report['complete'] = not report['video_clock_repair']['errors']
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    write_reader_site(data, output, book='复变函数 · 交互课堂')
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--workers', default=4, type=int)
    args = p.parse_args()
    result = repair(args.source, args.output, args.workers)
    print(json.dumps(result['video_clock_repair'], ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['complete'] else 1)
