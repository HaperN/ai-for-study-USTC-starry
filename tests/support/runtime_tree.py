"""Complete disposable runtime trees for dependency/cache tests."""
from pathlib import Path
import shutil


def copy_runtime(package, adapters=None):
    from aiforstudy.pipeline.runner import Runner
    from aiforstudy.pipeline.stage_io import _repo_root
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo', '_legacy_tree')
    shutil.copytree(Runner._runtime_package_root(), package, dirs_exist_ok=True, ignore=ignore)
    if adapters is not None:
        origin = _repo_root()
        for name in ('blocks', 'lib', 'tools/manim_worker'):
            shutil.copytree(origin / name, Path(adapters) / name, dirs_exist_ok=True, ignore=ignore)
        shutil.copy2(origin / 'requirements-manim.lock', Path(adapters) / 'requirements-manim.lock')
