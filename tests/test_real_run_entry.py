"""Local real-run preparation cannot spend or mutate before explicit execution."""
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def tool():
    assert (ROOT / 'tools/real_run.py').is_file(), 'local test entry not implemented'
    return importlib.import_module('tools.real_run')


def catalog(tmp_path, rows=None):
    root = tmp_path / 'parse'
    root.mkdir()
    rows = rows or [
        {'unit_id':'CH01','chapter':'CH01','level':'chapter_intro','anchor':'1','title':'引言'},
        {'unit_id':'CH01-S02','chapter':'CH01','level':'section','anchor':'1.1.1','title':'复数的四则运算'},
        {'unit_id':'CH01-S03','chapter':'CH01','level':'section','anchor':'1.1.2','title':'共轭复数'},
    ]
    for row in rows:
        row.setdefault('original_text', row['unit_id']+'.md')
        (root / row['original_text']).write_text('可核对的教材正文。',encoding='utf-8')
    (root/'chapters.json').write_text(json.dumps({'chapters':[{'id':'CH01'}], 'lesson_units':rows}),encoding='utf-8')
    return root


def test_new_catalog_targets_resolve_without_old_ids(tmp_path):
    selected = tool().resolve_targets(catalog(tmp_path), ['1.1.2=共轭复数','1.1.1=复数的四则运算'])
    assert selected == ('CH01-S02','CH01-S03')


@pytest.mark.parametrize('targets', [['1=引言'],['1.1.1=错标题'],['1.1.1=复数的四则运算']*2,['1.1.9=不存在'],['没有等号']])
def test_target_errors_do_not_guess_other_units(tmp_path, targets):
    module=tool()
    with pytest.raises(module.InputError):
        module.resolve_targets(catalog(tmp_path),targets)


def test_ambiguous_anchor_and_empty_or_outside_source_rejected(tmp_path):
    module=tool(); root=catalog(tmp_path)
    payload=json.loads((root/'chapters.json').read_text())
    payload['lesson_units'][2].update(anchor='1.1.1',title='复数的四则运算')
    (root/'chapters.json').write_text(json.dumps(payload))
    with pytest.raises(module.InputError): module.resolve_targets(root,['1.1.1=复数的四则运算'])
    for source in ('../outside.md','CH01-S03.md'):
        payload['lesson_units']=payload['lesson_units'][:2]
        payload['lesson_units'][1]['original_text']=source
        (root/'chapters.json').write_text(json.dumps(payload))
        (root/source).write_text('' if source=='CH01-S03.md' else '外部内容')
        with pytest.raises(module.InputError): module.resolve_targets(root,['1.1.1=复数的四则运算'])


def cold_workspace(tmp_path):
    from support.preparation_book import _mini_book
    ws=_mini_book(tmp_path)
    (ws.root/'book/parse/chapters.json').unlink()
    books=json.loads((ws.root/'config/books.json').read_text())['books']
    return ws, next(iter(books))


def test_default_prepare_and_deliver_are_read_only(tmp_path,monkeypatch,capsys):
    module=tool(); ws,book=cold_workspace(tmp_path)
    def forbidden(*args,**kwargs): raise AssertionError('execution must remain disabled')
    monkeypatch.setattr(module,'configured_providers',forbidden)
    monkeypatch.setattr(module,'run_request',forbidden)
    before={str(p):p.read_bytes() for p in ws.root.rglob('*') if p.is_file()}
    for extra in (['--phase','prepare'], ['--target','1.1=甲之一节']):
        assert module.main(['--workspace',str(ws.root),'--book',book,*extra])==0
        view=json.loads(capsys.readouterr().out)
        assert view['executed'] is False
        assert view['requires_preparation'] is True
    assert before=={str(p):p.read_bytes() for p in ws.root.rglob('*') if p.is_file()}


def test_execute_cold_units_stops_before_provider_or_log_creation(tmp_path,monkeypatch,capsys):
    module=tool(); ws,book=cold_workspace(tmp_path)
    monkeypatch.setattr(module,'configured_providers',lambda *a,**k:pytest.fail('provider created before readiness'))
    out=tmp_path/'observations'
    result=module.main(['--workspace',str(ws.root),'--book',book,'--target','1.1=甲之一节',
                        '--execute','--observations',str(out)])
    assert result==2
    assert 'prepare' in capsys.readouterr().err
    assert not out.exists()
    assert not (ws.root/'data/books').exists()


def test_execute_requires_observation_path_before_execution(tmp_path,monkeypatch,capsys):
    module=tool();ws,book=cold_workspace(tmp_path)
    monkeypatch.setattr(module,'run_request',lambda *a,**k:pytest.fail('execution without observations'))
    assert module.main(['--workspace',str(ws.root),'--book',book,'--phase','prepare','--execute'])==2
    assert '--observations' in capsys.readouterr().err


def test_prepare_forwards_real_provider_and_book_scope(tmp_path,monkeypatch,capsys):
    from types import SimpleNamespace
    from aiforstudy.pipeline.services import Providers
    module=tool(); ws,book=cold_workspace(tmp_path)
    sentinel=object(); passed=[]
    monkeypatch.setattr(module,'runtime_readiness',lambda:{'ready':True,'missing':[]})
    monkeypatch.setattr(module,'configured_providers',lambda request,workspace:Providers(llm=sentinel))
    def engine(request,workspace,**kwargs):
        passed.append((request,kwargs['providers']))
        return SimpleNamespace(exit_code=0,as_dict=lambda:{'status':'succeeded','release_ready':False}),SimpleNamespace(run_id='run-offline',root=tmp_path/'run-offline'),None
    monkeypatch.setattr(module,'run_request',engine)
    out=tmp_path/'observation'
    assert module.main(['--workspace',str(ws.root),'--book',book,'--phase','prepare','--execute','--observations',str(out)])==0
    request,providers=passed[0]
    assert request.goal.value=='prepare' and request.selectors.kind.value=='all'
    assert request.profile.value=='release' and request.prepare_policy.value=='ensure'
    assert providers.llm is sentinel and callable(providers.call_observer)
    assert json.loads(capsys.readouterr().out)['executed'] is True


def test_ready_targets_plan_then_deliver_keeps_only_selected_new_scope(tmp_path,monkeypatch,capsys):
    from types import SimpleNamespace
    from support.preparation_book import _mini_book
    from aiforstudy.pipeline.services import Providers
    module=tool();ws=_mini_book(tmp_path);book='mini-book'
    path=ws.root/'book/parse/chapters.json'
    data=json.loads(path.read_text())
    row=next(u for u in data['lesson_units'] if u['unit_id']=='CH01-S01')
    row['anchor']='1.1';row['original_text']='original_text/CH01-S01.md'
    path.write_text(json.dumps(data))
    argv=['--workspace',str(ws.root),'--book',book,'--target','1.1=甲之一节']
    monkeypatch.setattr(module,'configured_providers',lambda *a:pytest.fail('read-only created provider'))
    assert module.main(argv)==0
    view=json.loads(capsys.readouterr().out)
    assert view['resolved_scope']==['CH01-S01'] and not view['requires_preparation']
    sentinel=object();requests=[]
    monkeypatch.setattr(module,'configured_providers',lambda *a:Providers(llm=sentinel))
    monkeypatch.setattr(module,'runtime_readiness',lambda:{'ready':True,'missing':[]})
    def execute(request,workspace,**kwargs):
        requests.append(request)
        assert kwargs['providers'].llm is sentinel
        return SimpleNamespace(exit_code=0,as_dict=lambda:{'status':'succeeded'}),SimpleNamespace(run_id='run-offline',root=tmp_path/'run-offline'),None
    monkeypatch.setattr(module,'run_request',execute)
    assert module.main([*argv,'--execute','--observations',str(tmp_path/'observations')])==0
    assert requests[0].selectors.values==('CH01-S01',)
    assert requests[0].goal.value=='deliver' and requests[0].prepare_policy.value=='reuse'


def test_missing_runtime_blocks_explicit_execution_before_provider(tmp_path,monkeypatch,capsys):
    module=tool();ws,book=cold_workspace(tmp_path)
    monkeypatch.setattr(module,'runtime_readiness',lambda:{'ready':False,'missing':['pymupdf']})
    monkeypatch.setattr(module,'configured_providers',lambda *a:pytest.fail('must reject incomplete environment'))
    assert module.main(['--workspace',str(ws.root),'--book',book,'--phase','prepare',
                        '--execute','--observations',str(tmp_path/'observer')])==2
    assert 'pymupdf' in capsys.readouterr().err


def test_resume_rejects_cross_book_and_nonrelease_before_execute(tmp_path,capsys):
    module=tool(); ws,book=cold_workspace(tmp_path)
    from aiforstudy.contracts.request import RunRequest,Selectors
    from aiforstudy.contracts.enums import SelectorKind,Profile
    folder=ws.root/'data/books'/book/'runs/run-case';folder.mkdir(parents=True)
    for bad in (RunRequest(book_id='other',selectors=Selectors(SelectorKind.ALL)),
                RunRequest(book_id=book,selectors=Selectors(SelectorKind.ALL),profile=Profile.SMOKE)):
        (folder/'request.json').write_text(json.dumps(bad.as_dict()))
        assert module.main(['--workspace',str(ws.root),'--book',book,'--phase','resume','--run-id','run-case'])==2
        assert capsys.readouterr().err


def test_source_launcher_selects_project_python_and_passes_arguments(tmp_path):
    source=ROOT/'aifs'
    assert source.is_file(), 'source launcher not implemented'
    repo=tmp_path/'project with spaces';repo.mkdir()
    launcher=repo/'aifs';shutil.copy(source,launcher)
    python=tmp_path/'.venv-linux/bin/python';python.parent.mkdir(parents=True);python.symlink_to(sys.executable)
    main=repo/'src/aiforstudy/__main__.py';main.parent.mkdir(parents=True)
    main.write_text('import json,sys;print(json.dumps(sys.argv[1:]))')
    env={k:v for k,v in os.environ.items() if k not in ('AIFORSTUDY_PYTHON','PYTHONPATH')}
    result=subprocess.run(['sh',str(launcher),'plan','--units','一,二'],cwd=tmp_path,env=env,text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)==['--workspace',str(repo),'plan','--units','一,二']
    env['AIFORSTUDY_PYTHON']=str(tmp_path/'missing')
    bad=subprocess.run(['sh',str(launcher),'books'],env=env,text=True,capture_output=True)
    assert bad.returncode==2 and not bad.stdout


def test_source_launcher_local_test_uses_same_environment(tmp_path):
    source=ROOT/'aifs'
    assert source.is_file()
    repo=tmp_path/'repo';repo.mkdir();shutil.copy(source,repo/'aifs')
    python=tmp_path/'.venv-linux/bin/python';python.parent.mkdir(parents=True);python.symlink_to(sys.executable)
    script=repo/'tools/real_run.py';script.parent.mkdir()
    script.write_text('import json,sys;print(json.dumps(sys.argv[1:]))')
    env={k:v for k,v in os.environ.items() if k!='AIFORSTUDY_PYTHON'}
    result=subprocess.run(['sh',str(repo/'aifs'),'local-test','--book','sample'],env=env,text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)==['--workspace',str(repo),'--book','sample']
