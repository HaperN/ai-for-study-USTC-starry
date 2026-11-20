"""JSON-only runs produce local teaching output without claiming approval."""
from pathlib import Path
from types import SimpleNamespace

from aiforstudy.contracts.enums import QualityState
from aiforstudy.contracts.stage import StageResult
from aiforstudy.core.jsonio import read_json, write_json_atomic
from aiforstudy.core.review_mode import review_mode, reviews_frozen
from aiforstudy.pipeline.engine import build_engine
from aiforstudy.pipeline.graph import ExecutionGraph, Node
from aiforstudy.pipeline.runner import Runner
from aiforstudy.pipeline.services import Providers, StageRegistry
from aiforstudy.pipeline.state import RunStore
from support.runner_v5 import setup_book, _request, UNIT


def runner_case(tmp_path, mode):
    workspace=setup_book(tmp_path)
    write_json_atomic(workspace.root/'config/review_mode.json', {'mode':mode})
    seen=[]
    def stage(ctx):
        seen.append(reviews_frozen())
        # A mid-run configuration edit cannot change a dispatched run's mode.
        write_json_atomic(workspace.root/'config/review_mode.json', {'mode':'full'})
        return StageResult.ok('text_audit', quality_state=QualityState.NOT_CHECKED)
    engine=build_engine(_request(),workspace,providers=Providers(),
        stage_registry=StageRegistry({'text_audit':stage}),until='text_audit')
    graph=ExecutionGraph((Node('text_audit:'+UNIT,'text_audit',UNIT),),
        goal='deliver',profile='release')
    store=RunStore.create(tmp_path/'runs',engine.request,engine.plan,control=engine.services.control)
    return Runner(engine.services,graph,store),seen


def test_json_only_runner_freezes_mode_in_worker_and_finishes_without_fake_approval(tmp_path):
    runner,seen=runner_case(tmp_path,'json_only')
    result=runner.run()
    assert seen==[True]
    assert result.status=='succeeded' and result.exit_code==0
    assert not result.scope_accepted and not result.release_ready
    assert result.quality_counts=={'not_checked':1}
    assert result.as_dict()['review_status']=='not_run'
    assert runner.store.load_state()['review_mode']=='json_only'


def test_full_runner_keeps_quality_gate(tmp_path):
    runner,seen=runner_case(tmp_path,'full')
    result=runner.run()
    assert seen==[False] and result.status=='needs_review'
    assert not result.scope_accepted and not result.release_ready


def test_json_only_resume_keeps_recorded_mode_after_config_changes(tmp_path):
    runner,seen=runner_case(tmp_path,'json_only')
    runner.run()
    resumed=Runner(runner.services,runner.graph,runner.store,resume=True)
    result=resumed.run()
    assert result.as_dict()['review_mode']=='json_only'
    assert all(seen) and result.status=='succeeded'


def test_unmarked_old_run_resume_does_not_adopt_new_json_only_config(tmp_path):
    runner,seen=runner_case(tmp_path,'full')
    runner.run()
    state=runner.store.load_state();state.pop('review_mode',None);runner.store.save_state(state)
    write_json_atomic(runner.services.workspace.root/'config/review_mode.json',{'mode':'json_only'})
    resumed=Runner(runner.services,runner.graph,runner.store,resume=True)
    assert resumed.run().status=='needs_review'
    assert 'review_mode' not in runner.store.load_state()


def test_json_only_delivery_packages_real_html_without_receipt_or_browser_gate(tmp_path,monkeypatch):
    from aiforstudy.pipeline.delivery_stage import delivery_stage
    from aiforstudy.checks import offline
    work=tmp_path/'run/work'; chapter=work/'legacy/render/C'
    chapter.mkdir(parents=True);(chapter/'index.html').write_text('<html><body>实际教材正文</body></html>')
    parse=tmp_path/'parse'
    write_json_atomic(parse/'chapters.json',{'lesson_units':[{'unit_id':'U','chapter':'C'}]})
    ctx=SimpleNamespace(node=Node('delivery','delivery','scope'),
        context=SimpleNamespace(book_id='book',revision='r',parse=parse),
        workspace=SimpleNamespace(root=tmp_path),plan=SimpleNamespace(resolved_scope=('U',)),
        services=SimpleNamespace(store=SimpleNamespace(work=work,run_id='run'),
            cancel=SimpleNamespace(checkpoint=lambda:None)))
    def forbidden(*args,**kwargs): raise AssertionError('non-JSON review must remain frozen')
    monkeypatch.setattr(offline,'run_offline_acceptance',forbidden)
    with review_mode('json_only'):
        result=delivery_stage(ctx)
    assert result.execution_state.value=='succeeded',result.error
    assert result.quality_state is QualityState.NOT_CHECKED
    payload=read_json(result.outputs[0].path)['payload']
    assert payload['review_outcome']['status']=='unverified'
    assert payload['offline_acceptance']['status']=='not_run'
    assert payload['members']['C/index.html']
    assert '实际教材正文' in (chapter/'index.html').read_text()
