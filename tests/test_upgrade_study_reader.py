from copy import deepcopy
import json
from pathlib import Path
import zipfile

import pytest

from tools.upgrade_study_reader import load_reader_data, restore_empty_units, upgrade_reader


def sample_data():
    return {'book': 'test', 'scope': ['CH01'], 'units': {'CH01': {
        'unit_id': 'CH01', 'title': '第一课', 'reader_document': {
            'schema_version': 'ReaderDocumentV1', 'unit_id': 'CH01',
            'reference_catalog': {'schema_version': 'FrozenReferenceCatalogV1', 'entries': []},
            'blocks': [{'block_id': 'title', 'group': 'title', 'markdown': '第一课'}]}}},
        'animations': {}, 'tree': [], 'figures': {}}


def source_book(tmp_path, data=None):
    source = tmp_path / 'source'
    (source / 'data').mkdir(parents=True)
    (source / 'assets').mkdir()
    (source / 'data/data.js').write_text('/* old */\nwindow.__DATA__ = ' + json.dumps(data or sample_data()) + ';\n')
    (source / 'index.html').write_text('old shell')
    return source


def test_reader_data_is_parsed_without_executing_javascript(tmp_path):
    source = source_book(tmp_path)
    assert load_reader_data(source)['scope'] == ['CH01']
    with (source / 'data/data.js').open('a') as stream:
        stream.write('alert("not data");')
    with pytest.raises(ValueError, match='data|数据|script|脚本'):
        load_reader_data(source)


def test_restore_only_empty_unit_and_ignore_correction_envelopes(tmp_path):
    data = sample_data()
    before = deepcopy(data)
    content = {'unit_id': 'CH01', 'title': '恢复课文', 'body_markdown': '这是一段曾经正确生成的正文。',
               'annotations': [], 'explainables': [], 'examples': [], 'concepts': [], 'learning_checks': []}
    archive = tmp_path / 'program.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        root = 'program/data/books/test/runs/run/work/products/content/CH01/revisions/'
        z.writestr(root + '000001.json', json.dumps(content))
        z.writestr(root + '000002.json', json.dumps({'role_task': 'local_generation_correction',
            'original_response': '{}', 'errors': ['bad JSON'], 'body_markdown': ''}))
    restored = restore_empty_units(data, archive)
    assert restored[0]['unit_id'] == 'CH01'
    assert restored[0]['revision_path'].endswith('000001.json')
    assert any('曾经正确生成' in b.get('markdown', '') for b in data['units']['CH01']['reader_document']['blocks'])
    assert data['units']['CH01']['reader_layout']['schema_version'] == 'ReaderLayoutV1'
    assert before['units']['CH01']['reader_document']['blocks'] == [{'block_id': 'title', 'group': 'title', 'markdown': '第一课'}]
    assert restore_empty_units(data, archive) == []


def test_upgrade_protects_source_and_preserves_scope(tmp_path):
    source = source_book(tmp_path)
    old_bytes = (source / 'data/data.js').read_bytes()
    output = tmp_path / 'new-reader'
    result = upgrade_reader(source, output)
    assert (source / 'data/data.js').read_bytes() == old_bytes
    assert load_reader_data(output)['scope'] == ['CH01']
    assert result['unit_count'] == 1
    assert (output / 'upgrade-report.json').is_file()
    assert 'study-visuals.js' in (output / 'index.html').read_text()
    with pytest.raises(ValueError):
        upgrade_reader(source, source)
    with pytest.raises(FileExistsError):
        upgrade_reader(source, output)


def test_static_site_declares_study_assets_and_local_three():
    from blocks.render.build import reader_site_files
    files = reader_site_files(sample_data())
    assert {'assets/study-reader.css', 'assets/study-visuals.js', 'assets/three.min.js'} <= files.keys()


def test_legacy_intro_is_preserved_without_relaxing_timing_validation():
    from tools.upgrade_study_reader import prepare_legacy_steps, retime_timestamp
    old = [{'step_id': 'S1', 'start': 1.8, 'end': 8.6},
           {'step_id': 'S2', 'start': 8.6, 'end': 21.0}]
    prepared = prepare_legacy_steps(old)
    assert prepared[0]['start'] == 0
    assert prepared[0]['source_intro_seconds'] == 1.8
    assert old[0]['start'] == 1.8
    new = [{'step_id': 'S1', 'start': 0, 'end': 12, 'source_start': 0, 'source_end': 8.6},
           {'step_id': 'S2', 'start': 12, 'end': 28, 'source_start': 8.6, 'source_end': 21}]
    assert retime_timestamp(9.6, new) == pytest.approx(13)
    with pytest.raises(ValueError):
        prepare_legacy_steps([{'start': 0, 'end': 2}, {'start': 3, 'end': 5}])


def test_resume_rebuilds_missing_audio_video_and_changed_source_media(tmp_path):
    data = sample_data()
    data['animations'] = {'CH01': [{'request_id': 'MR-one', 'base': 'assets/old/',
        'resources': {'mp4': {'relative_path': 'video.mp4'}},
        'resource_manifest': [{'kind': 'keyframe', 'relative_path': 'frame.png', 'mime': 'image/png'}],
        'steps': [{'step_id': 'S1', 'start': 0, 'end': 1, 'caption': '改变参数'}]}]}
    source = source_book(tmp_path, data)
    (source / 'assets/old').mkdir()
    original_video = source / 'assets/old/video.mp4'
    original_video.write_bytes(b'original video')
    (source / 'assets/old/frame.png').write_bytes(b'original frame')
    calls = []

    def narrator(video, steps, folder):
        folder.mkdir(parents=True, exist_ok=True)
        calls.append(video.read_bytes())
        for suffix, content in [('mp4', b'narrated video'), ('wav', b'spoken audio'),
                                ('vtt', b'WEBVTT\n'), ('txt', '中文讲解'.encode())]:
            (folder / ('narrated.' + suffix)).write_bytes(content)
        return {'video_path': folder / 'narrated.mp4', 'audio_path': folder / 'narrated.wav',
            'captions_path': folder / 'narrated.vtt', 'transcript_path': folder / 'narrated.txt',
            'steps': [{**s, 'narration_duration': .8} for s in steps],
            'source': {'backend': 'fixture'}, 'duration_seconds': 1}

    output = tmp_path / 'upgraded'
    assert upgrade_reader(source, output, narrate=True, narrator=narrator)['complete']
    assert len(calls) == 1
    row = load_reader_data(output)['animations']['CH01'][0]
    (output / row['base'] / row['resources']['mp4']['relative_path']).unlink()
    assert upgrade_reader(source, output, narrate=True, narrator=narrator, resume=True)['complete']
    assert len(calls) == 2
    row = load_reader_data(output)['animations']['CH01'][0]
    frame = next(r for r in row['resource_manifest'] if r['kind'] == 'keyframe')
    (output / row['base'] / frame['relative_path']).unlink()
    assert upgrade_reader(source, output, narrate=True, narrator=narrator, resume=True)['complete']
    assert len(calls) == 3
    original_video.write_bytes(b'changed original video')
    assert upgrade_reader(source, output, narrate=True, narrator=narrator, resume=True)['complete']
    assert calls[-1] == b'changed original video'
    assert len(calls) == 4
