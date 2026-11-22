"""Prepare Laurent assets and technical evidence for independent media review.

This writes no acceptance verdict. The ordinary media packager remains gated
on an independently authored final-media-review.json.
"""
from pathlib import Path
from array import array
import argparse
import hashlib
import json
import math
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def prepare(work, render, motion_report):
    work, render = Path(work), Path(render)
    result = read(render/'result.json')
    motion = read(motion_report)
    if result['status'] != 'needs_review' or motion['status'] != 'motion_observed':
        raise ValueError('completed technical rendering and spatial motion evidence are required')
    if (motion['width'], motion['height'], motion['fps'], motion['audio_present']) != (1280, 720, '30/1', True):
        raise ValueError('expected a narrated 720p/30fps final render')
    if motion['video_sha256'] != sha(render/'video.mp4'):
        raise ValueError('motion report does not bind current video')
    specs = read(work/'scene-specs-final.json')
    source = ROOT/'examples/practice_media/laurent_scene.py'
    if source.read_text(encoding='utf-8') != specs[0]['program']['code']:
        raise ValueError('source differs from final program')
    if source.read_text(encoding='utf-8') != (render/'source/generated.py').read_text(encoding='utf-8'):
        raise ValueError('source differs from rendered program')
    destination = work/'renders/final/laurent'
    if destination.exists():
        raise ValueError('canonical final folder already exists')
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(render, destination)
    shutil.copy2(work/'scene-specs-final.json', work/'scene-specs.json')
    (work/'statics').mkdir(exist_ok=True)
    shutil.copy2(work/'laurent.svg', work/'statics/laurent.svg')
    video, picture, audio = (destination/name for name in ('video.mp4', 'picture.mp4', 'voice.mp3'))
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(video), '-map', '0:v:0',
                    '-an', '-c:v', 'copy', '-movflags', '+faststart', str(picture)], check=True)
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(video), '-vn',
                    '-c:a', 'libmp3lame', '-q:a', '3', str(audio)], check=True)

    def stream_hash(path):
        return subprocess.check_output(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path),
                                        '-map', '0:v:0', '-c:v', 'copy', '-f', 'hash', '-hash', 'sha256', '-']).decode().strip()

    pcm = array('f')
    pcm.frombytes(subprocess.check_output(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(audio),
                                          '-f', 'f32le', '-ac', '1', '-ar', '24000', 'pipe:1']))
    rms = math.sqrt(sum(value*value for value in pcm)/len(pcm))
    frames = []
    for step in result['steps']:
        for moment, keyframe in zip(step['evidence_moments'], step['keyframes']):
            path = destination/keyframe['relative_path']
            frames.append({'step': step['step_id'], 'phase': moment['phase'], 'time': moment['at'],
                           'path': str(path), 'sha256': sha(path)})
    technical = {'id': 'laurent', 'duration': motion['seconds'], 'frames': frames,
                 'motion_frames_unique': len({row['sha256'] for row in frames}),
                 'continuous_spatial_motion': motion['motion'],
                 'audio_rms': rms, 'audio_peak': max(abs(value) for value in pcm),
                 'audio_samples': len(pcm), 'audio_rate': 24000,
                 'video_stream_copy_identical': stream_hash(video) == stream_hash(picture),
                 'video_sha256': sha(video)}
    if not technical['video_stream_copy_identical'] or not rms > 0:
        raise ValueError('picture stream or decoded audio verification failed')
    write(work/'media-technical-verification.json', [technical])
    assets = {'mp4': video, 'picture': picture, 'audio': audio,
              'svg': work/'statics/laurent.svg', 'source': source}
    packet = {'id': 'laurent', 'status': 'ready_for_independent_media_review',
              'question_id': specs[0]['question_id'],
              'question_content_sha256': specs[0]['question_content_sha256'],
              'assets': {key: str(value) for key, value in assets.items()},
              'asset_hashes': {key: sha(value) for key, value in assets.items()},
              'steps': [{'step_id': s['step_id'], 'start': s['start'], 'end': s['end']} for s in result['steps']],
              'frames': frames, 'extra_evidence_frames': motion['extra_evidence_frames'],
              'speech_review': str(work/'speech-review.json'),
              'technical_review': str(work/'media-technical-verification.json'),
              'actual_audio_listened': False}
    write(work/'independent-media-review-packet.json', packet)
    return packet


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--render', type=Path, required=True)
    parser.add_argument('--motion-report', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.work, args.render, args.motion_report), ensure_ascii=False, indent=2))
