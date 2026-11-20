import json
from types import SimpleNamespace
import pytest
from aiforstudy.ui.server import ControlServer
from aiforstudy.errors import ContractError
from aiforstudy.core.workspace import Workspace
from aiforstudy.contracts.request import RunRequest, Selectors


def test_old_resume_rejected_before_background_write(tmp_path):
    root = tmp_path/'data/books/b/runs/old'
    root.mkdir(parents=True)
    request = RunRequest(book_id='b', selectors=Selectors(kind='all'))
    (root/'request.json').write_text(json.dumps(request.as_dict()))
    (root/'state.json').write_text(json.dumps({'run_id':'old','graph_version':'3.0','status':'failed'}))
    before = {str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    app = object.__new__(ControlServer)
    app.workspace=Workspace(tmp_path)
    app.state=SimpleNamespace()  # Must never reach live-state mutation for old run.
    with pytest.raises(ContractError, match='3.0'):
        app.api_resume('old', {})
    assert {str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()} == before


def test_http_legacy_resume_is_rejected_with_entire_directory_unchanged(tmp_path):
    import threading
    import urllib.request, urllib.error
    root=tmp_path/'data/books/b/runs/old';root.mkdir(parents=True)
    request=RunRequest(book_id='b',selectors=Selectors('all'))
    (root/'request.json').write_text(json.dumps(request.as_dict()))
    (root/'state.json').write_text(json.dumps({'run_id':'old','graph_version':'3.0','status':'failed'}))
    before={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    app=ControlServer(Workspace(tmp_path),port=0)
    httpd=app.make_server();thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
    try:
        request=urllib.request.Request(app.origin+'/api/runs/old/resume',data=b'{}',method='POST',headers={'Content-Type':'application/json','Origin':app.origin,'X-Aifs-Token':app.token})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request,timeout=5)
        assert error.value.code==400
        assert '3.0' in error.value.read().decode()
        assert {str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}==before
    finally:
        httpd.shutdown();httpd.server_close();thread.join(timeout=2)


def test_workbench_reads_registered_current_requirements_without_old_fallback(tmp_path):
    from aiforstudy.contracts.artifact import ArtifactRef
    work=tmp_path/'work';path=work/'products/media_plan/CH01-S01.json';path.parent.mkdir(parents=True)
    payload={'unit_id':'CH01-S01','revision':2,'content_hash':'current','decisions':[{'target_id':'s1','reason':'解释关系','request_ids':['R1']}],'requests':[{'request_id':'R1','learning_goal':'看懂关系','placement':{'target_pointer':'/body_markdown'}}]}
    path.write_text(json.dumps({'payload':payload}))
    ref=ArtifactRef(book_id='b',kind='media.requirements',logical_key='U',path=path,producer_stage='media_plan').with_content().as_dict()
    state={'nodes':{'media_plan:CH01-S01':{'stage':'media_plan','scope_key':'CH01-S01','quality_state':'accepted','outputs':[ref]}}}
    rows=ControlServer._v4_products(SimpleNamespace(work=work),state)
    assert rows['media_requirements'][0]['revision']==2
    assert rows['media_requirements'][0]['requests'][0]['learning_goal']=='看懂关系'
    path.write_text('{}')
    stale=ControlServer._v4_products(SimpleNamespace(work=work),state)
    assert stale['media_requirements'][0]['valid'] is False
