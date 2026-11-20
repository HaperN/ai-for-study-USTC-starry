"""Current author envelope → real new SVG/Manim → approved reader artifact.

Only model responses are controlled; no renderer, approval, or stage is mocked.
"""
import importlib.util
import json
from pathlib import Path
import pytest


def _verification_module():
    path = Path(__file__).resolve().parents[2] / 'tools/verify_code_media_e2e.py'
    spec = importlib.util.spec_from_file_location('author_media_fresh_e2e', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_current_author_one_image_one_fresh_animation_reaches_reader(tmp_path):
    module = _verification_module()
    report = module.run_acceptance(tmp_path / 'evidence')
    assert report['ok'], report
    assert report['external_api_calls'] == 0
    assert {row['kind'] for row in report['media']} == {'animation','drawing'}
    assert all(row['state']=='fulfilled' for row in report['media'])
    assert report['html'].endswith('index.html')
    text=(Path(report['html']).parent/'data/data.js').read_text()
    data=json.loads(text.split('window.__DATA__ = ',1)[1].rsplit(';',1)[0])
    assert data['preview_only'] is False and data['production_accepted'] is True
    animations=[animation for values in data['animations'].values() for animation in values]
    assert animations and all(animation['schema_version']=='ReaderAnimationV1'
        and animation['preview_only'] is False and animation['production_accepted'] is True
        for animation in animations)
    for row in report['media']:
        assert (Path(row['source_dir'])/'source/generated.py').is_file()
        assert (Path(row['source_dir'])/'source/context.json').is_file()
    assert all(row['advanced'] and row['no_external_requests'] for row in report['browser'].values())


def test_populated_output_cannot_relabel_an_old_artifact_as_fresh(tmp_path):
    prior = tmp_path / 'old-video.mp4'
    prior.write_bytes(b'previous run evidence')
    with pytest.raises(ValueError, match='fresh media requires'):
        _verification_module().run_acceptance(tmp_path)
    assert prior.read_bytes() == b'previous run evidence'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['old-video.mp4']
