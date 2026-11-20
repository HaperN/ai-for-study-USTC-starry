"""Production media boundaries: identity, distinct assets, and HTML consumption."""


import hashlib


import json


import sys


from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


import test_animation_pipeline as A


import test_animation_browser as B


from aiforstudy.blocks.figures.animation import attachable_animations


from aiforstudy.blocks.visual.result import parse_animation_result


def test_two_units_can_deliver_different_videos_with_standard_filenames(tmp_path):
    from test_diagnostic_animation_preview import animation_fixture
    from aiforstudy.blocks.render.diagnostics import _attach_diagnostic_animation
    payloads=[]
    for index,unit in enumerate(['CH01-S01','CH01-S02']):
        run,source,path=animation_fixture(tmp_path/str(index))
        raw=json.loads(path.read_text())['payload']['requests'][0]['animation']
        raw['request_id']=f'vis-{index}'
        sources={r['relative_path']:source/r['relative_path'] for r in raw['resources']}
        payloads.append(_attach_diagnostic_animation(tmp_path/'out',unit=unit,book_id='test',animation=raw,sources=sources,profile='final'))
    assert all(not p['missing'] for p in payloads)
    assert payloads[0]['base']!=payloads[1]['base']
    for payload in payloads:
        row=payload['resources']['mp4'];path=tmp_path/'out'/payload['base']/row['relative_path']
        assert 'sha256:'+hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256']


def test_same_unit_keeps_all_explicit_animation_requests(tmp_path):
    results = []
    for index in range(2):
        raw = A._animation_dict(tmp_path / str(index), request_id=f'vis-{index}',
             spec_hash=A.digest_of({'i': index}), profile='final', steps=1, variant=index)
        raw.pop('base_dir')
        raw['source_binding']['unit_id'] = 'U'
        results.append(parse_animation_result(raw))
    value = attachable_animations(results)['U']
    assert isinstance(value, list) and len(value) == 2


import pytest





def test_retired_media_or_writer_protocol_cannot_be_registered():
    import pytest
    from aiforstudy.errors import ContractError
    from aiforstudy.pipeline.registry import release_registry
    for version in ('3.0','4.0'):
        with pytest.raises(ContractError,match='只读'):release_registry(graph_version=version)
