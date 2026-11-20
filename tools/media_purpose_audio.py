"""Build independently playable purpose narration from an authored manifest.

Only the pinned Qwen Serena worker/cache is supported. The source manifest and
spoken text are never rewritten. Publication happens after every MP3 decodes to
non-silent PCM; this signal check does not establish pronunciation or naturalness.
"""
from __future__ import annotations

import argparse
from array import array
from copy import deepcopy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import wave

from aiforstudy.blocks.media.local_narration import (
    AutoLocalQwenBackend, LocalCacheBackend, cache_key, digest, run_worker,
)
from aiforstudy.blocks.media.narration import resolve_ffmpeg


def _validate(manifest):
    if manifest.get('schema_version') != 1 or not isinstance(manifest.get('items'), list) or not manifest['items']:
        raise ValueError('Expected schema_version 1 and nonempty items')
    seen = set()
    for item in manifest['items']:
        for field in ('unit_id', 'id', 'title', 'purpose_markdown', 'spoken_text'):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise ValueError('Nonempty ' + field + ' required')
        if item.get('kind') not in ('visual', 'animation', 'figure'):
            raise ValueError('Unknown purpose kind')
        binding = (item['unit_id'], item['kind'], item['id'])
        if binding in seen:
            raise ValueError('Duplicate purpose binding: ' + repr(binding))
        seen.add(binding)


def _warm_cache(backend, items):
    """Load one native model for all misses while retaining the shared GPU lock."""
    if not isinstance(backend, AutoLocalQwenBackend):
        return
    missing = {cache_key(i['spoken_text'], backend.source): i['spoken_text'] for i in items
               if not (backend.cache / cache_key(i['spoken_text'], backend.source) / 'result.json').exists()}
    if not missing:
        return
    backend.lock.parent.mkdir(parents=True, exist_ok=True)
    with backend.lock.open('a') as lock:
        deadline = time.monotonic() + backend.timeout
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Purpose narration GPU lock timeout; no fallback')
                time.sleep(.05)
        try:
            missing = {key: text for key, text in missing.items()
                       if not (backend.cache / key / 'result.json').exists()}
            if not missing:
                return
            backend.cache.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='.purpose-worker-', dir=backend.cache) as temporary:
                temporary = Path(temporary)
                corpus = temporary / 'corpus.json'
                corpus.write_text(json.dumps({'records': [
                    {'id': key, 'comparison_spoken_text': text} for key, text in missing.items()
                ]}, ensure_ascii=False), encoding='utf-8')
                argv = [backend.paths['python'], '-m', 'aiforstudy.blocks.media.local_speech_worker',
                        '--model-dir', backend.paths['model_dir'],
                        '--model-manifest', backend.paths['model_manifest'],
                        '--corpus', str(corpus), '--cache-dir', str(backend.cache),
                        '--output-config', str(temporary / 'config.json')]
                env = dict(os.environ)
                env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1] / 'src')
                print(json.dumps({'event': 'qwen_batch_start', 'missing': len(missing)}), flush=True)
                try:
                    result = run_worker(argv, timeout=backend.timeout, env=env)
                except subprocess.CalledProcessError as exc:
                    raise RuntimeError('Purpose Qwen worker failed; no fallback: ' +
                                       exc.stderr.decode('utf-8', 'replace')[-2000:]) from exc
                print(result.stdout.decode('utf-8', 'replace'), end='', flush=True)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _decode_mp3(ffmpeg, path):
    decoded = subprocess.run([ffmpeg, '-v', 'error', '-i', str(path), '-f', 'f32le',
                              '-ac', '1', '-ar', '48000', 'pipe:1'],
                             check=True, capture_output=True, timeout=120)
    if decoded.stderr:
        raise ValueError('MP3 decode reported an error: ' + decoded.stderr.decode('utf-8', 'replace'))
    samples = array('f')
    samples.frombytes(decoded.stdout)
    if sys.byteorder != 'little':
        samples.byteswap()
    if not samples or not all(math.isfinite(value) for value in samples):
        raise ValueError('MP3 decoded to empty or nonfinite PCM')
    rms = math.sqrt(sum(value * value for value in samples) / len(samples))
    if rms <= 1e-5:
        raise ValueError('MP3 is silent or below signal floor')
    return {'duration_seconds': len(samples) / 48000, 'decoded_sample_rate': 48000,
            'decoded_sample_count': len(samples), 'decoded_rms': rms,
            'decoded_peak': max(abs(value) for value in samples)}


def render_purpose_audio(manifest_path, output_root, config):
    """Return/write visual-purpose.json plus MP3, original PCM WAV, and receipts."""
    manifest_path = Path(manifest_path).resolve()
    output_root = Path(output_root).resolve()
    destination = output_root / 'visual-purpose.json'
    if destination == manifest_path:
        raise ValueError('Refusing to overwrite source manifest')
    original_bytes = manifest_path.read_bytes()
    input_sha256 = hashlib.sha256(original_bytes).hexdigest()
    manifest = json.loads(original_bytes)
    _validate(manifest)
    kind = config.get('backend', 'local-qwen')
    if kind == 'local-qwen':
        backend = AutoLocalQwenBackend(config)
    elif kind == 'local-cache':
        backend = LocalCacheBackend(config)
    else:
        raise ValueError('Purpose narration requires pinned Qwen Serena; no fallback')
    if backend.source.get('backend') != 'local-qwen3-tts' or backend.source.get('voice') != 'serena':
        raise ValueError('Purpose narration requires pinned Qwen Serena identity')
    _warm_cache(backend, manifest['items'])
    ffmpeg = resolve_ffmpeg(config.get('ffmpeg'))
    output_root.mkdir(parents=True, exist_ok=True)
    result = deepcopy(manifest)
    # All audio is staged: a worker/encoding failure publishes no new manifest.
    with tempfile.TemporaryDirectory(prefix='.purpose-audio-', dir=output_root) as temporary:
        temporary = Path(temporary)
        for item in result['items']:
            text = item['spoken_text']
            key = cache_key(text, backend.source)
            wav_path, mp3_path = temporary / (key + '.wav'), temporary / (key + '.mp3')
            backend.synthesize(text, wav_path)
            receipt_path = backend.cache / key / 'result.json'
            receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
            with wave.open(str(wav_path), 'rb') as wav:
                wav_rate, wav_count = wav.getframerate(), wav.getnframes()
                if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
                    raise ValueError('Qwen cache must contain original PCM16 mono WAV')
            subprocess.run([ffmpeg, '-v', 'error', '-y', '-i', str(wav_path), '-map_metadata', '-1',
                            '-ac', '1', '-ar', '48000', '-c:a', 'libmp3lame', '-b:a', '128k', str(mp3_path)],
                           check=True, capture_output=True, timeout=120)
            signal = _decode_mp3(ffmpeg, mp3_path)
            if abs(signal['duration_seconds'] - wav_count / wav_rate) > .05:
                raise ValueError('Encoded MP3 duration differs from original PCM')
            prefix = 'assets/purpose-audio/' + key
            item['audio'] = {'src': prefix + '.mp3', 'wav_src': prefix + '.wav',
                             'receipt_src': prefix + '.json', 'sha256': digest(mp3_path),
                             'bytes': mp3_path.stat().st_size, 'wav_sha256': digest(wav_path),
                             'wav_bytes': wav_path.stat().st_size, 'wav_sample_rate': wav_rate,
                             'wav_sample_count': wav_count, 'cache_key': key,
                             'source_identity': deepcopy(backend.source), 'codec': 'mp3',
                             'encoding': {'sample_rate': 48000, 'channels': 1, 'bitrate': 128000},
                             'spoken_text_sha256': hashlib.sha256(text.encode()).hexdigest(),
                             'content_review': 'not_established_by_signal_check', **signal}
            (temporary / (key + '.json')).write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'event': 'purpose_audio_verified', 'unit_id': item['unit_id'],
                              'id': item['id'], 'seconds': signal['duration_seconds']}, ensure_ascii=False), flush=True)
        if digest(manifest_path) != input_sha256:
            raise ValueError('Source manifest changed during narration; rerun with the updated input')
        result['audio_build'] = {'version': 1, 'input_sha256': input_sha256,
                                 'item_count': len(result['items']),
                                 'source_identity': deepcopy(backend.source),
                                 'total_duration_seconds': sum(i['audio']['duration_seconds'] for i in result['items']),
                                 'signal_check': 'every_mp3_decoded_non_silent',
                                 'human_listening_completed': False, 'naturalness_proven': False}
        assets = output_root / 'assets' / 'purpose-audio'
        assets.mkdir(parents=True, exist_ok=True)
        for source in temporary.iterdir():
            source.replace(assets / source.name)
        pending = temporary / 'visual-purpose.json'
        pending.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        pending.replace(destination)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'output', 'config'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    result = render_purpose_audio(args.manifest, args.output, json.loads(args.config.read_text(encoding='utf-8')))
    print(json.dumps({'manifest': str(args.output / 'visual-purpose.json'),
                      'count': len(result['items']), 'seconds': result['audio_build']['total_duration_seconds']}))


if __name__ == '__main__':
    main()
