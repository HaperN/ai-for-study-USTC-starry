"""Capability checks and rendering must find identical wheel-packaged assets."""
from pathlib import Path
from types import SimpleNamespace
from aiforstudy.providers.capabilities import resolve_worker_dir
from aiforstudy.blocks.visual import worker


def test_installed_assets_available_from_empty_workspace(tmp_path, monkeypatch):
    package = tmp_path / 'installed/aiforstudy'
    module = package / 'blocks/visual/worker.py'
    module.parent.mkdir(parents=True)
    module.write_text('')
    assets = package / '_legacy_tree/tools/manim_worker'
    assets.mkdir(parents=True)
    (assets / 'run_scene.py').write_text('')
    monkeypatch.setattr(worker, '__file__', str(module))
    workspace = SimpleNamespace(root=tmp_path / 'user-workspace')
    assert resolve_worker_dir({}, workspace) == assets
