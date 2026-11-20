"""Build an sdist and install its wheel; compare actual offline runtime inputs."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[2]


def run(args, cwd, *, timeout=120):
    env = {k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME','AIFORSTUDY_WORKSPACE')}
    result = subprocess.run([sys.executable, *args], cwd=cwd, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    assert result.returncode == 0, result.stdout[-6000:]+'\n'+result.stderr[-6000:]
    return result.stdout


@pytest.fixture(scope='module')
def distributions(tmp_path_factory):
    base = tmp_path_factory.mktemp('runtime-wheel'); source = base/'source'; source.mkdir()
    ignore = shutil.ignore_patterns('__pycache__','*.pyc','*.egg-info','_legacy_tree')
    for name in ('src','blocks','lib','tools/manim_worker'):
        shutil.copytree(ROOT/name, source/name, ignore=ignore)
    for path in ROOT.iterdir():
        if path.is_file() and path.suffix in ('.py','.toml','.in','.md','.lock'):
            shutil.copy2(path, source/path.name)
    run(['setup.py','sdist','--dist-dir',str(base/'sdist')], source)
    archive = next((base/'sdist').glob('*.tar.gz'))
    with tarfile.open(archive) as stream:
        stream.extractall(base/'unpacked', filter='data')
    unpacked = next((base/'unpacked').iterdir())
    run(['setup.py','bdist_wheel','--dist-dir',str(base/'wheels')], unpacked)
    wheel = next((base/'wheels').glob('*.whl'))
    target = base/'installed'
    run(['-m','pip','install','--no-index','--no-deps','--target',str(target),str(wheel)], base)
    foreign = base/'foreign'; foreign.mkdir()
    return source, target, foreign


def observe(root, foreign, mode):
    script = '''
import sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import aiforstudy
assert Path(aiforstudy.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
mode=sys.argv[2]
if mode=='catalog':
    from aiforstudy.blocks.parse.catalog import _directory_review
    root=Path(sys.argv[3]); root.mkdir(exist_ok=True)
    (root/'toc.dual-source.json').write_text('{}')
    _,result=_directory_review(root,{})
    assert result['workflow']=='dual-source' and result['stale']
    print(json.dumps(result))
elif mode=='imports':
    from aiforstudy.pipeline.stage_io import _legacy,_repo_root
    runtime_root=_repo_root()
    sys.path.insert(0,str(runtime_root))
    from blocks.figures import library
    assert Path(library.__file__).resolve().is_relative_to(runtime_root.resolve())
    generator=_legacy('model_generate')
    assert library.ALL_FIGURES
    lock=_repo_root()/'requirements-manim.lock'
    assert lock.is_file(),'missing packaged render lock'
    print(json.dumps({'figure_names':[r['name'] for r in library.ALL_FIGURES],
        'lock':lock.read_text(),'has_selector':callable(generator.plan_models)}))
elif mode=='identities':
    from aiforstudy.pipeline.runner import Runner
    from aiforstudy.pipeline.runtime_dependencies import STAGE_PACKAGE_PATHS
    runner=object.__new__(Runner)
    print(json.dumps({stage:runner._stage_runtime_identity(stage) for stage in STAGE_PACKAGE_PATHS}))
elif mode=='resources':
    from types import SimpleNamespace
    from aiforstudy.blocks.media import dependencies as D
    from aiforstudy.checks import offline
    D.shutil.which=lambda _:None
    offline._browser_executable=lambda _:None
    ctx=SimpleNamespace(workspace=SimpleNamespace(root=Path(sys.argv[3]),config_json=lambda _:{}),
        services=SimpleNamespace(providers=None))
    request={'kind':'plot','library_id':'test','acceptance_criteria':[]}
    print(json.dumps(D.resource_dependencies(ctx,request,{})))
'''
    return json.loads(run(['-I','-c',script,str(root),mode,str(foreign/'data')],foreign))


def test_catalog_adapter_loads_without_an_earlier_stage_or_cwd(distributions):
    source, installed, foreign = distributions
    assert observe(source/'src',foreign,'catalog') == observe(installed,foreign,'catalog')


def test_wheel_contains_the_actual_figure_library_and_render_lock(distributions):
    source, installed, foreign = distributions
    assert observe(source/'src',foreign,'imports') == observe(installed,foreign,'imports')


def test_source_and_installed_wheel_have_identical_stage_identities(distributions):
    source, installed, foreign = distributions
    assert observe(source/'src',foreign,'identities') == observe(installed,foreign,'identities')


def test_library_resource_dependencies_ignore_retired_unshipped_tools(distributions):
    source, installed, foreign = distributions
    assert observe(source/'src',foreign,'resources') == observe(installed,foreign,'resources')
