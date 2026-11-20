"""Current graph contracts: retired author routes cannot resume or be registered."""
from pathlib import Path
import pytest
from aiforstudy.errors import ContractError
from aiforstudy.pipeline.graph import GRAPH_VERSION, CANONICAL_STAGES
from aiforstudy.pipeline.resource_binding import resume_guard


def test_current_graph_has_fourteen_stages_without_question_extraction():
    assert GRAPH_VERSION == '7.0'
    assert len(CANONICAL_STAGES) == 14
    from aiforstudy.pipeline.registry import release_registry
    registry = release_registry()
    assert registry.graph_version == '7.0'
    for stage in CANONICAL_STAGES:
        assert registry.get(stage.name).__module__ != 'aiforstudy.pipeline.transitional'


@pytest.mark.parametrize('version', ['3.0','4.0','5.0','6.0'])
def test_historical_graph_cannot_be_registered_or_resumed(version):
    assert not resume_guard(state={'graph_version':version}, plan={}).allowed
    from aiforstudy.pipeline.registry import release_registry
    with pytest.raises(ContractError, match='只读|read.only|7.0'):
        release_registry(graph_version=version)


def test_conflicting_graph_declarations_cannot_mask_an_old_graph():
    assert not resume_guard(state={'graph_version':'5.0'}, plan={'graph_version':'4.0'}).allowed


def test_legacy_resume_rejection_does_not_write_even_lease(tmp_path):
    from test_m2_engine import make_workspace, request_for
    from aiforstudy.pipeline.engine import build_engine, execute
    from aiforstudy.pipeline.state import RunStore, book_runs_root
    from aiforstudy.core.jsonio import write_json_atomic
    ws,book=make_workspace(tmp_path)
    engine=build_engine(request_for(book.book_id,units=('CH01-S01',)),ws)
    store=RunStore.create(book_runs_root(ws.root,book.book_id),engine.request,engine.plan, control=engine.services.control)
    state=store.load_state();state['graph_version']='4.0';store.save_state(state)
    before={str(p.relative_to(ws.root)):p.read_bytes() for p in ws.root.rglob('*') if p.is_file()}
    with pytest.raises(ContractError): execute(engine,resume_run=store.run_id)
    assert before == {str(p.relative_to(ws.root)):p.read_bytes() for p in ws.root.rglob('*') if p.is_file()}


def test_retired_author_loader_rejects_before_import():
    from aiforstudy.pipeline import stage_io
    for name in ('generate','validate','prompt','audit_adapter','model_review','blocks.lesson.generate'):
        with pytest.raises(ContractError,match='retired|退役|unsupported'):
            stage_io._legacy(name)


def test_stage_cache_tracks_math_source_profile_but_not_css(tmp_path,monkeypatch):
    from aiforstudy.pipeline.runner import Runner
    from support.runtime_tree import copy_runtime
    package=tmp_path/'package';adapters=tmp_path/'adapters'
    copy_runtime(package, adapters)
    files=['blocks/math/verify.py','blocks/content/source_inventory.py','resources/teaching_profiles.json',
           'blocks/content/prompt.py','pipeline/gateway_client.py',
           'blocks/media/author_diagnostics.py','blocks/content/reader_fields.py',
           'providers/response.py','providers/response_evidence.py','core/process_io.py']
    for name in files:
        p=package/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('first')
    css=adapters/'blocks/render/assets/style.css';css.parent.mkdir(parents=True,exist_ok=True);css.write_text('first')
    monkeypatch.setattr(Runner,'_runtime_package_root',staticmethod(lambda:package))
    monkeypatch.setattr('aiforstudy.pipeline.stage_io._repo_root',lambda:adapters)
    runner=object.__new__(Runner)
    before=runner._stage_runtime_identity('lesson')
    css.write_text('changed css')
    assert runner._stage_runtime_identity('lesson') == before
    for name in files:
        p=package/name;p.write_text('changed')
        assert runner._stage_runtime_identity('lesson') != before,name
        p.write_text('first')


def test_static_resource_generation_is_independent_of_reviewer_module(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from aiforstudy.blocks.media import dependencies
    ctx=SimpleNamespace(workspace=SimpleNamespace(root=tmp_path,config_json=lambda _:{}),services=SimpleNamespace(providers=None))
    request={'kind':'plot','acceptance_criteria':[]}
    monkeypatch.setattr(dependencies,'file_identity',lambda path:str(path))
    before=dependencies.resource_dependencies(ctx,request,{})
    original=dependencies.file_identity
    monkeypatch.setattr(dependencies,'file_identity',lambda path:'review-changed' if str(path).endswith('validation.py') else original(path))
    after=dependencies.resource_dependencies(ctx,request,{})
    assert before['generation_dependency_hash'] == after['generation_dependency_hash']
    assert before['review_dependency_hash'] != after['review_dependency_hash']


def test_notation_discovery_preserved_without_retired_writing_rules():
    from blocks.parse.concepts.extract_concepts import notation_gaps
    gaps=notation_gaps('这里使用矩阵的秩与行列式。',[])
    assert {'秩','行列式'} <= {r['key'] for r in gaps}
    assert not notation_gaps('行列式', [{'name':'行列式','formal':'定义'}])
    from blocks.parse.concepts import notation_inventory
    assert notation_inventory.notation_gaps is not None


def test_production_distribution_excludes_retired_authors():
    root=Path(__file__).resolve().parents[2]
    retired=['blocks/lesson/generate.py','blocks/lesson/validate.py','blocks/lesson/prompt.py',
             'blocks/lesson/cli.py','blocks/lesson/rubric.py','lib/style_rules.py','lib/notation.py']
    assert not [name for name in retired if (root/name).exists()]


@pytest.mark.parametrize('name',['run_book','run_chapter','audit_loop'])
def test_retired_tools_offer_no_callable_author_path(name,tmp_path):
    import importlib.util
    path=Path(__file__).resolve().parents[2]/'tools'/f'{name}.py'
    spec=importlib.util.spec_from_file_location('retired_'+name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert not hasattr(module,'run_book') and not hasattr(module,'run_chapter') and not hasattr(module,'run_unit')
    assert module.main([])==2
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('version',['3.0','4.0','5.0','6.0'])
def test_runner_rejects_retired_graph_before_binding_store(version):
    from aiforstudy.pipeline.runner import Runner
    from aiforstudy.pipeline.graph import ExecutionGraph
    with pytest.raises(ContractError,match='graph 5|只读'):
        Runner(None,ExecutionGraph((),goal='deliver',profile='release',graph_version=version),None)


def test_current_runner_cache_does_not_introspect_function_source(tmp_path,monkeypatch):
    import inspect
    from test_m2_engine import make_workspace,request_for
    from aiforstudy.pipeline.engine import run_request
    ws,book=make_workspace(tmp_path)
    def forbidden(*args,**kwargs):raise AssertionError('runtime source introspection forbidden')
    monkeypatch.setattr(inspect,'getsource',forbidden)
    monkeypatch.setattr(inspect,'getsourcefile',forbidden)
    result,_,_=run_request(request_for(book.book_id,units=('CH01-S01',)),ws)
    assert result.status=='succeeded',result.recovery_hint
