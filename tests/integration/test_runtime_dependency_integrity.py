"""Runtime declarations must resolve and invalidate actual stage cache inputs."""
from pathlib import Path

import pytest

from aiforstudy.errors import ContractError
from aiforstudy.pipeline.runner import Runner
from aiforstudy.pipeline import stage_io
from support.runtime_tree import copy_runtime


def test_every_declared_runtime_component_exists():
    from aiforstudy.pipeline.runtime_dependencies import STAGE_PACKAGE_PATHS
    runner = object.__new__(Runner)
    for stage in STAGE_PACKAGE_PATHS:
        identity = runner._stage_runtime_identity(stage)
        for kind in ('package', 'adapters'):
            assert all(value != 'missing' for value in identity[kind].values()), (stage, identity[kind])


@pytest.mark.parametrize('kind,path', [('package', 'pipeline/services.py'), ('adapters', 'blocks/model/model_generate.py')])
def test_missing_declared_code_cannot_become_a_stable_cache_identity(tmp_path, monkeypatch, kind, path):
    package, adapters = tmp_path/'package', tmp_path/'adapters'
    copy_runtime(package, adapters)
    runner = object.__new__(Runner)
    monkeypatch.setattr(runner, '_runtime_package_root', lambda: package)
    monkeypatch.setattr(stage_io, '_repo_root', lambda: adapters)
    (dict(package=package, adapters=adapters)[kind]/path).unlink()
    with pytest.raises(ContractError, match='runtime.*missing|missing.*runtime'):
        runner._stage_runtime_identity('lesson')


@pytest.mark.parametrize('stage,kind,path', [
    *((stage, 'package', path) for stage in ('lesson','text_audit','media_plan','figures','audit','interaction','render')
      for path in ('blocks/audit/loop.py','blocks/audit/__init__.py','pipeline/diagnostics.py')),
    *((stage, 'adapters', path) for stage in ('lesson','media_plan','figures','audit','interaction','render')
      for path in ('blocks/model/model_prompt.py','blocks/model/model_validate.py','lib/curriculum.py','lib/files.py')),
    *((stage, 'adapters', 'lib/non_teaching.py') for stage in ('mineru','toc','structure','export','body','concepts')),
    ('export','package','blocks/parse/catalog.py'),
    ('concepts','package','blocks/concepts/index.py'),
    ('figures','adapters','blocks/figures/spec_extra.py'),
    ('figures','adapters','requirements-manim.lock'),
    ('lesson','package','pipeline/repair.py'),
    ('text_audit','package','pipeline/repair.py'),
    *((stage, 'package', path) for stage in ('delivery',) for path in (
        'blocks/media/composition.py','blocks/content/reader_fields.py','blocks/math/contracts.py',
        'blocks/render/diagnostics.py','blocks/render/readiness.py','blocks/render/source_materials.py',
        'blocks/interaction/spec.py','pipeline/interaction_runtime.py','pipeline/repair.py')),
    *((stage, 'adapters', path) for stage in ('delivery',) for path in (
        'blocks/render/build.py','blocks/model/model_generate.py','blocks/model/model_validate.py',
        'blocks/figures/spec_extra.py','requirements-manim.lock')),
])
def test_real_component_edit_changes_consuming_stage_identity(tmp_path, monkeypatch, stage, kind, path):
    package, adapters = tmp_path/'package', tmp_path/'adapters'
    copy_runtime(package, adapters)
    runner = object.__new__(Runner)
    monkeypatch.setattr(runner, '_runtime_package_root', lambda: package)
    monkeypatch.setattr(stage_io, '_repo_root', lambda: adapters)
    before = runner._stage_runtime_identity(stage)
    target = dict(package=package, adapters=adapters)[kind]/path
    target.write_bytes(target.read_bytes()+b'\n# offline implementation change\n')
    assert runner._stage_runtime_identity(stage) != before, (stage, path)


def test_runner_resume_rechecks_technical_ticket_implementation(tmp_path, monkeypatch):
    from test_m2_engine import make_workspace, request_for
    from aiforstudy.pipeline.engine import build_engine, execute, resume_request
    from aiforstudy.pipeline.stubs import smoke_registry
    package = tmp_path/'package'; copy_runtime(package)
    monkeypatch.setattr(Runner, '_runtime_package_root', staticmethod(lambda: package))
    workspace, book = make_workspace(tmp_path/'ws')
    request = request_for(book.book_id, units=('CH01-S01',))
    registry = smoke_registry()
    first, store = execute(build_engine(request, workspace, stage_registry=registry, until='interaction'))
    assert first.status == 'succeeded'
    before = store.node_states()['lesson:CH01-S01'].attempt
    target = package/'pipeline/diagnostics.py'; target.write_bytes(target.read_bytes()+b'\n# changed ticket contract\n')
    second, store, _ = resume_request(request, store.run_id, workspace, stage_registry=registry, until='interaction')
    assert second.status == 'succeeded'
    assert store.node_states()['lesson:CH01-S01'].attempt == before+1
