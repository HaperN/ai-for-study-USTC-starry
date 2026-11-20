"""Historical run fixture is read-only; never patch the production graph version."""
from pathlib import Path
import pytest
from aiforstudy.errors import ContractError
from aiforstudy.pipeline.engine import build_engine, execute
from aiforstudy.pipeline.registry import release_registry
from aiforstudy.pipeline.state import RunStore, book_runs_root, read_state_summary
from support.book_factory import _mini_book, _request


def assert_read_only_run(tmp_path,version,*,stage):
    workspace=_mini_book(tmp_path)
    engine=build_engine(_request(),workspace,until=stage)
    store=RunStore.create(book_runs_root(workspace.root,engine.request.book_id),engine.request,engine.plan, control=engine.services.control)
    state=store.load_state();state['graph_version']=version;store.save_state(state)
    evidence=store.work/'historical-evidence.json';evidence.parent.mkdir(parents=True,exist_ok=True)
    evidence.write_text('{"schema_version":"3.0","quality":"historical-only"}')
    before={str(p.relative_to(workspace.root)):p.read_bytes() for p in workspace.root.rglob('*') if p.is_file()}
    summary=read_state_summary(workspace.root,engine.request.book_id,store.run_id)
    assert summary['run_id']==store.run_id
    assert store.load_state()['graph_version']==version
    with pytest.raises(ContractError,match='只读|5.0'):
        release_registry(graph_version=version)
    with pytest.raises(ContractError):execute(engine,resume_run=store.run_id)
    assert before=={str(p.relative_to(workspace.root)):p.read_bytes() for p in workspace.root.rglob('*') if p.is_file()}
