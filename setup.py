"""Ship only the current parser, renderer, interaction and controlled-library adapters."""
from pathlib import Path
import shutil
from setuptools import setup
from setuptools.command.build_py import build_py

# Explicit prefixes/files, never the complete blocks/lib trees.
ADAPTER_PATHS = (
    'blocks/__init__.py','blocks/parse',
    'blocks/render/__init__.py','blocks/render/build.py','blocks/render/assets','blocks/render/vendor','blocks/render/templates',
    'blocks/figures/__init__.py','blocks/figures/library.py','blocks/figures/spec.py','blocks/figures/spec_extra.py',
    'blocks/figures/verify_svg.py','blocks/figures/backends',
    'blocks/model/__init__.py','blocks/model/model_generate.py','blocks/model/model_prompt.py','blocks/model/model_validate.py',
    'lib/__init__.py','lib/files.py','lib/text.py','lib/refs.py','lib/book.py','lib/curriculum.py',
    'lib/non_teaching.py','lib/scopes.py','lib/parallel.py','lib/model.py','lib/usage.py',
    'tools/manim_worker','requirements-manim.lock',
)

class BuildWithAdapters(build_py):
    def run(self):
        # Never let a prior build retain a now-retired module in the wheel.
        package=Path(self.build_lib)/'aiforstudy'
        if package.exists(): shutil.rmtree(package)
        super().run()
        source=Path(__file__).parent
        destination=Path(self.build_lib)/'aiforstudy'/'_legacy_tree'
        if destination.exists(): shutil.rmtree(destination)
        for name in ADAPTER_PATHS:
            origin,target=source/name,destination/name
            target.parent.mkdir(parents=True,exist_ok=True)
            if origin.is_dir():
                shutil.copytree(origin,target,ignore=shutil.ignore_patterns('__pycache__','*.pyc','.DS_Store'))
            elif origin.is_file(): shutil.copy2(origin,target)
            else: raise RuntimeError('missing runtime adapter '+name)

setup(cmdclass={'build_py':BuildWithAdapters})
