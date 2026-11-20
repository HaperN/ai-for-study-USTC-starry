"""Generate real, offline Chinese listening guides for mathematical scenes."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import wave


def generate_guides(catalog, output, *, backend=None, workers=4):
    from aiforstudy.blocks.media.narration import configured_backend, spoken_text
    backend = backend or configured_backend()
    output = Path(output)
    folder = output / 'assets/study-guides'
    folder.mkdir(parents=True, exist_ok=True)

    def generate(kind, scene):
        # Formula-specific prose is generated from the same scene declaration
        # the graph uses, not from an unrelated chapter or mathematical claim.
        script = spoken_text({'caption': scene['formula'], 'observation': scene['explanation']})
        script += '你可以修改图像下方的参数，观察图形与数值的变化。'
        identity = hashlib.sha256(json.dumps({'script': script, 'source': backend.source},
            ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        name = hashlib.sha256(kind.encode()).hexdigest()[:20] + '.wav'
        audio, receipt = folder / name, folder / (name + '.json')
        saved = json.loads(receipt.read_text()) if receipt.is_file() else {}
        def digest():
            return hashlib.sha256(audio.read_bytes()).hexdigest()
        if not (audio.is_file() and saved.get('identity') == identity and saved.get('sha256') == digest()):
            temporary = audio.with_suffix('.tmp.wav')
            backend.synthesize(script, temporary)
            with wave.open(str(temporary), 'rb') as wav:
                if wav.getnframes() <= 0 or wav.getnchannels() not in (1, 2):
                    raise ValueError('场景讲解未产生有效音频')
                seconds = wav.getnframes() / wav.getframerate()
            temporary.replace(audio)
            saved = {'identity': identity, 'sha256': digest(), 'duration_seconds': seconds}
            receipt.write_text(json.dumps(saved, ensure_ascii=False), encoding='utf-8')
        return kind, {'audio': audio.relative_to(output).as_posix(), 'script': script,
            'source': backend.source, 'sha256': saved['sha256'], 'duration_seconds': saved['duration_seconds']}

    guides, errors = {}, []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(generate, kind, scene): kind for kind, scene in catalog.items()}
        for future in as_completed(futures):
            try:
                kind, row = future.result()
                guides[kind] = row
            except Exception as exc:
                errors.append({'kind': futures[future], 'error': type(exc).__name__ + ': ' + str(exc)})
    return {'guides': guides, 'errors': errors, 'count': len(guides)}
