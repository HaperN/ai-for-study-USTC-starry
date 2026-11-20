import json
from pathlib import Path
import wave

from tools.study_guides import generate_guides


def test_scene_guide_cache_checks_actual_audio_bytes(tmp_path):
    class Backend:
        source = {'backend': 'fixture'}
        calls = 0
        def synthesize(self, text, destination):
            self.calls += 1
            assert '代表函数' in text
            with wave.open(str(destination), 'wb') as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
                wav.writeframes(b'\x01\x00' * 24000)
    backend = Backend()
    catalog = {'square': {'formula': 'w=z²', 'explanation': '代表函数的网格。'}}
    result = generate_guides(catalog, tmp_path, backend=backend)
    assert result['count'] == 1 and not result['errors']
    assert backend.calls == 1
    assert generate_guides(catalog, tmp_path, backend=backend)['count'] == 1
    assert backend.calls == 1
    audio = tmp_path / result['guides']['square']['audio']
    audio.write_bytes(b'damaged')
    assert generate_guides(catalog, tmp_path, backend=backend)['count'] == 1
    assert backend.calls == 2
