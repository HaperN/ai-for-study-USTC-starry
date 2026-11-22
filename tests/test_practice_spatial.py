"""Portable spatial pages retain real local runtimes and safe question links."""
from pathlib import Path
import importlib.util
import json
import re
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def builder():
    file = ROOT / 'tools/build_practice_spatial.py'
    assert file.is_file(), 'the standalone spatial builder must be implemented'
    spec = importlib.util.spec_from_file_location('spatial_builder', file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_spatial_build_is_self_contained_and_bindings_do_not_inject_html(tmp_path):
    result = builder().build_spatial(tmp_path, {'sphere': ['Q-1', '\"><script>bad()</script>'], 'log': ['Q-2']})
    page = (tmp_path / '三维图解.html').read_text()
    assert result['scenes'] == 2
    assert '<script>bad()</script>' not in page
    assert 'Q-1' in page and 'Q-2' in page
    assert not re.search(r'(?:src|href)=[\"\']https?://', page)
    for relative in re.findall(r'(?:src|href)=[\"\'](assets/[^\"\']+)', page):
        assert (tmp_path / relative).is_file(), relative
    assert list((tmp_path / 'assets/fonts').glob('*.woff2'))
    for kind in ('sphere', 'log'):
        svg = ET.parse(tmp_path / f'assets/practice-spatial-{kind}.svg')
        assert len(svg.findall('.//{http://www.w3.org/2000/svg}path')) > 5
        assert not svg.findall('.//{http://www.w3.org/2000/svg}image')
    manifest = json.loads((tmp_path / 'assets/practice-spatial-manifest.json').read_text())
    assert manifest['scenes']['sphere']['question_ids'][0] == 'Q-1'
    assert manifest['scenes']['log']['question_ids'] == ['Q-2']
