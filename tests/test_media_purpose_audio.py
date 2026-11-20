"""Purpose narration packaging contracts; synthetic PCM is test data only."""
import hashlib
import importlib
import json
import math
from pathlib import Path
import struct
import wave

import pytest


def renderer():
    return importlib.import_module('tools.media_purpose_audio').render_purpose_audio


def manifest(tmp_path, items=None):
    path = tmp_path / 'input.json'
    path.write_text(json.dumps({'schema_version': 1, 'custom': 'preserve', 'items': items or [
        {'unit_id': 'U1', 'id': 'V1', 'kind': 'visual', 'title': '示例',
         'purpose_markdown': '观察旋转。', 'spoken_text': '观察旋转。',
         'source_basis': {'plan': 'example.json'}}]}, ensure_ascii=False))
    return path


def cache_config(tmp_path, text='观察旋转。', silent=False):
    from aiforstudy.blocks.media.local_narration import cache_key, worker_identity
    identity = worker_identity({'repo': 'test-fixture-only', 'revision': 'fixture', 'files': []})
    config = {'backend': 'local-cache', 'identity': identity, 'cache_dir': str(tmp_path / 'cache')}
    folder = Path(config['cache_dir']) / cache_key(text, identity)
    folder.mkdir(parents=True)
    with wave.open(str(folder / 'audio.wav'), 'wb') as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(24000)
        audio.writeframes(b''.join(struct.pack('<h', 0 if silent else int(9000*math.sin(i*0.1))) for i in range(24000)))
    digest = hashlib.sha256((folder / 'audio.wav').read_bytes()).hexdigest()
    receipt = {'cache_key': folder.name, 'identity': identity, 'text': text,
               'audio_sha256': digest, 'raw_sha256': 'fixture-raw', 'sample_rate': 24000,
               'sample_count': 24000, 'duration_seconds': 1, 'token_count': 12,
               'generation_seconds': 0.1, 'budget_reached': False,
               'human_listening_completed': False, 'naturalness_proven': False}
    (folder / 'result.json').write_text(json.dumps(receipt))
    return config, folder


@pytest.mark.parametrize('field', ['purpose_markdown', 'spoken_text'])
def test_empty_explanations_rejected_before_output(tmp_path, field):
    path = manifest(tmp_path); payload = json.loads(path.read_text())
    payload['items'][0][field] = '  '
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match=field):
        renderer()(path, tmp_path / 'out', {})
    assert not (tmp_path / 'out' / 'visual-purpose.json').exists()


def test_duplicate_binding_rejected(tmp_path):
    path = manifest(tmp_path); payload = json.loads(path.read_text())
    payload['items'].append(dict(payload['items'][0])); path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='Duplicate'):
        renderer()(path, tmp_path / 'out', {})


def test_real_codec_package_hash_duration_mapping_and_cache_reuse(tmp_path):
    path = manifest(tmp_path); original = path.read_bytes(); config, folder = cache_config(tmp_path)
    before = (folder / 'result.json').read_bytes()
    result = renderer()(path, tmp_path / 'out', config)
    assert path.read_bytes() == original
    assert result['custom'] == 'preserve'
    assert result['items'][0]['source_basis'] == {'plan': 'example.json'}
    audio = result['items'][0]['audio']
    assert audio['src'].startswith('assets/purpose-audio/') and audio['src'].endswith('.mp3')
    assert audio['wav_src'].endswith('.wav')
    assert audio['sha256'] == hashlib.sha256((tmp_path / 'out' / audio['src']).read_bytes()).hexdigest()
    assert audio['bytes'] == (tmp_path / 'out' / audio['src']).stat().st_size
    assert audio['duration_seconds'] == pytest.approx(1, abs=.03)
    assert audio['wav_sha256'] == hashlib.sha256((folder / 'audio.wav').read_bytes()).hexdigest()
    assert audio['source_identity'] == config['identity']
    assert audio['decoded_rms'] > .01
    assert audio['decoded_sample_rate'] == 48000
    receipt = json.loads((tmp_path / 'out' / audio['receipt_src']).read_text())
    assert receipt['text'] == '观察旋转。'
    assert receipt['raw_sha256'] == 'fixture-raw'
    second = renderer()(path, tmp_path / 'out2', config)
    assert second['items'][0]['audio']['sha256'] == audio['sha256']
    assert (folder / 'result.json').read_bytes() == before
    assert json.loads((tmp_path / 'out' / 'visual-purpose.json').read_text()) == result


@pytest.mark.parametrize('failure', ['missing', 'corrupt', 'silent', 'encoder'])
def test_failure_never_publishes_success(tmp_path, failure):
    path = manifest(tmp_path); config, folder = cache_config(tmp_path, silent=failure == 'silent')
    if failure == 'missing': (folder / 'result.json').unlink()
    if failure == 'corrupt': (folder / 'audio.wav').write_bytes(b'corrupt')
    if failure == 'encoder': config['ffmpeg'] = '/does/not/exist'
    with pytest.raises((ValueError, RuntimeError, OSError)):
        renderer()(path, tmp_path / 'out', config)
    assert not (tmp_path / 'out' / 'visual-purpose.json').exists()


def test_rejects_source_manifest_overwrite_and_unapproved_voice(tmp_path):
    path = manifest(tmp_path); path.rename(tmp_path / 'visual-purpose.json'); path = tmp_path / 'visual-purpose.json'
    with pytest.raises(ValueError, match='overwrite'):
        renderer()(path, tmp_path, {})
    path = manifest(tmp_path)
    with pytest.raises(ValueError, match='Qwen'):
        renderer()(path, tmp_path / 'out', {'backend': 'macos-say'})


def test_batch_worker_failure_releases_gpu_and_keeps_input(tmp_path, monkeypatch):
    import fcntl
    import subprocess
    module = importlib.import_module('tools.media_purpose_audio')
    path = manifest(tmp_path); original = path.read_bytes()
    model_manifest = tmp_path / 'model.json'
    model_manifest.write_text(json.dumps({'status': 'verified_complete', 'repo': 'test-fixture-only',
                                        'revision': 'fixture', 'files': []}))
    lock_path = tmp_path / 'gpu.lock'
    config = {'backend': 'local-qwen', 'model_dir': str(tmp_path),
              'model_manifest': str(model_manifest), 'python': '/not/a/python',
              'cache_dir': str(tmp_path / 'cache'), 'lock_file': str(lock_path)}
    def fail(argv, **kwargs):
        corpus = json.loads(Path(argv[argv.index('--corpus') + 1]).read_text())
        assert corpus['records'][0]['comparison_spoken_text'] == '观察旋转。'
        with lock_path.open('a') as other:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        raise subprocess.CalledProcessError(9, argv, stderr=b'fixture worker unavailable')
    monkeypatch.setattr(module, 'run_worker', fail)
    with pytest.raises(RuntimeError, match='worker unavailable'):
        renderer()(path, tmp_path / 'out', config)
    assert path.read_bytes() == original
    assert not (tmp_path / 'out' / 'visual-purpose.json').exists()
    with lock_path.open('a') as other:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_input_edited_during_generation_cannot_receive_wrong_provenance(tmp_path, monkeypatch):
    module = importlib.import_module('tools.media_purpose_audio')
    path = manifest(tmp_path); config, _ = cache_config(tmp_path)
    def editor(*args):
        changed = json.loads(path.read_text())
        changed['items'][0]['spoken_text'] = '新解释。'
        path.write_text(json.dumps(changed))
    monkeypatch.setattr(module, '_warm_cache', editor)
    with pytest.raises(ValueError, match='changed'):
        renderer()(path, tmp_path / 'out', config)
    assert not (tmp_path / 'out' / 'visual-purpose.json').exists()
