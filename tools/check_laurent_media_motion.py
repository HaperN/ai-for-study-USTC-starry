"""Inspect encoded Laurent video frames without treating motion as math approval."""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
from PIL import Image, ImageChops, ImageStat


def check(render, output):
    render, output = Path(render), Path(output)
    result = json.loads((render/'result.json').read_text(encoding='utf-8'))
    if result['status'] not in ('needs_review', 'rendered'):
        raise ValueError('render has not passed technical checks')
    video = render/'video.mp4'
    probe = json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(video)]))
    stream = next(row for row in probe['streams'] if row['codec_type'] == 'video')
    # This crop contains only the complex plane, its blue radius, and the point.
    # Formula cards and changing numerical counters cannot make this check pass.
    fps, width, height = 4, 270, 235
    raw = subprocess.check_output([
        'ffmpeg', '-nostdin', '-v', 'error', '-i', str(video), '-vf',
        f'fps={fps},scale=640:360,crop={width}:{height}:12:64,format=gray',
        '-f', 'rawvideo', '-pix_fmt', 'gray', 'pipe:1'])
    size = width*height
    frames = [Image.frombytes('L', (width, height), raw[start:start+size])
              for start in range(0, len(raw), size)]
    moving_seconds = {'S1': 12, 'S2': 7, 'S3': 13, 'S4': 7, 'S5': 8}
    motion = []
    for step in result['steps']:
        start = float(step['start'])
        end = start+moving_seconds[step['step_id']]
        begin, finish = int(start*fps)+1, min(int(end*fps), len(frames)-1)
        selected = frames[begin:finish+1]
        differences = [ImageStat.Stat(ImageChops.difference(a, b)).mean[0]
                       for a, b in zip(selected, selected[1:])]
        unique = len({hashlib.sha256(frame.tobytes()).digest() for frame in selected})
        record = {'step_id': step['step_id'], 'sampled_start': begin/fps,
                  'sampled_end': finish/fps, 'frame_count': len(selected),
                  'unique_frame_count': unique,
                  'changing_pair_fraction': sum(value > .002 for value in differences)/len(differences),
                  'mean_absolute_pixel_change': sum(differences)/len(differences)}
        record['continuous_motion_observed'] = record['changing_pair_fraction'] >= .95
        motion.append(record)
    output.mkdir(parents=True, exist_ok=True)
    frame_dir = output/'frames'
    frame_dir.mkdir(exist_ok=True)
    starts = {row['step_id']: float(row['start']) for row in result['steps']}
    moments = [('S2-boundary', starts['S2']+10), ('S4-boundary', starts['S4']+10),
               ('S5-cancellation', starts['S5']+11.8), ('S5-asymptotic', starts['S5']+15.5)]
    evidence = []
    for name, second in moments:
        image = frame_dir/(name+'.png')
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', str(second),
                        '-i', str(video), '-frames:v', '1', str(image)], check=True)
        evidence.append({'name': name, 'at_seconds': second, 'path': str(image),
                         'sha256': hashlib.sha256(image.read_bytes()).hexdigest()})
    report = {'status': 'motion_observed' if all(row['continuous_motion_observed'] for row in motion) else 'needs_inspection',
              'scope': 'Encoded frames and continuous spatial motion only; not independent mathematical or listening approval.',
              'video_sha256': hashlib.sha256(video.read_bytes()).hexdigest(),
              'width': stream['width'], 'height': stream['height'],
              'fps': stream['avg_frame_rate'], 'seconds': float(probe['format']['duration']),
              'audio_present': any(row['codec_type'] == 'audio' for row in probe['streams']),
              'spatial_crop_at_640_by_360': [12, 64, width, height],
              'motion': motion, 'extra_evidence_frames': evidence}
    (output/'motion-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--render', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.render, args.out), ensure_ascii=False, indent=2))
