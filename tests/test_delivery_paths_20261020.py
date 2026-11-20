"""修改计划 F1/F2（2026-10-20）：复制运行后重新挂载媒体；交付入口的播放器依赖闭包。"""
import json
import shutil

from aiforstudy.core.digest import digest_file
from aiforstudy.core.resource_files import relocated_source_dir, set_current_run_root
from aiforstudy.pipeline.delivery_stage import reader_dependency_problems


def _run_with_media(root):
    media = root / 'work' / 'media' / 'U' / 'R' / 'generation-x' / 'attempt-01' / 'final'
    media.mkdir(parents=True)
    (media / 'figure.png').write_bytes(b'png-bytes')
    resource = {'relative_path': 'figure.png', 'sha256': digest_file(media / 'figure.png')}
    return media, resource


def test_copied_run_remounts_old_absolute_source_dir_only_when_hashes_match(tmp_path):
    original = tmp_path / 'runs' / 'run-1'
    old_dir, resource = _run_with_media(original)
    copy = tmp_path / 'recovery' / 'run-1'
    shutil.copytree(original, copy)
    shutil.rmtree(original)                                  # 旧位置已不存在
    found = relocated_source_dir(str(old_dir), [resource], run_root=copy)
    assert found == copy / 'work' / 'media' / 'U' / 'R' / 'generation-x' / 'attempt-01' / 'final'
    tampered = dict(resource, sha256='sha256:' + '0' * 64)
    assert relocated_source_dir(str(old_dir), [tampered], run_root=copy) == old_dir   # 摘要不符不接受
    assert relocated_source_dir('work/media/U/R', [], run_root=copy) == copy / 'work/media/U/R'


def test_current_run_root_is_used_when_reader_has_no_explicit_run(tmp_path):
    original = tmp_path / 'a' / 'run'
    old_dir, resource = _run_with_media(original)
    copy = tmp_path / 'b' / 'run'
    shutil.copytree(original, copy)
    shutil.rmtree(original)
    set_current_run_root(copy)
    try:
        assert relocated_source_dir(str(old_dir), [resource]).is_dir()
    finally:
        set_current_run_root(None)


def _export(tmp_path, data, files=()):
    out = tmp_path / 'CH01'
    (out / 'data').mkdir(parents=True)
    (out / 'data' / 'data.js').write_text('window.__DATA__ = ' + json.dumps(data) + ';\n', encoding='utf-8')
    for name in files:
        (out / name).parent.mkdir(parents=True, exist_ok=True)
        (out / name).write_text('x', encoding='utf-8')
    return out


def test_animation_data_requires_player_script(tmp_path):
    out = _export(tmp_path, {'animations': {'U': [{'request_id': 'R'}]}, 'models': {}})
    assert any('animations.js' in p for p in reader_dependency_problems(out))
    (out / 'assets').mkdir()
    (out / 'assets' / 'animations.js').write_text('x', encoding='utf-8')
    assert reader_dependency_problems(out) == []


def test_canvas_models_require_mathbox_runtime(tmp_path):
    out = _export(tmp_path, {'animations': {}, 'models': {'M1': {'kind': 'canvas'}}},
                  files=('assets/mathbox/three.min.js',))
    assert reader_dependency_problems(out) == ['页面数据含画布模型，但缺 assets/mathbox/mathbox.min.js']
