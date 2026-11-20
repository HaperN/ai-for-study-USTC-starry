from types import SimpleNamespace


import json


from test_media_plan_stage import ctx


from support.content_case import approved_content as fixture, teaching_case, none_decision, no_media_requirements


from aiforstudy.core.digest import digest_of


from aiforstudy.core.jsonio import write_json_atomic


def ticket_content(unit_id='U',_fields=None):
    content=teaching_case()[0];content.update(_fields or {},unit_id=unit_id)
    for index,row in enumerate(content.get('examples',[])):
        row.setdefault('id','EX-'+str(index));row.setdefault('kind','derived_example')
    return content


def test_post_media_content_errors_have_stable_distinct_identity_and_full_coverage(tmp_path):
    from aiforstudy.pipeline.media_stages import _tickets
    content=ticket_content(unit_id='U', _fields={'body_markdown':'分母条件缺失','examples':[{'statement':'平方条件缺失'}]})
    problems=[{'target_artifact_kind':'content','pointer':'/body_markdown','issue_key':'denominator-nonzero','problem':'必须约束分母','evidence':'分母条件缺失','action':'补分母非零','expected':'分母非零'}, {'target_artifact_kind':'content','pointer':'/examples/0/statement','issue_key':'square-nonnegative','problem':'必须约束平方','evidence':'平方条件缺失','action':'补平方非负','expected':'平方非负'}]
    rows=[{'request_id':'R1','kind':'geometry','state':'needs_review','review':{'problems':problems}}, {'request_id':'R2','kind':'geometry','state':'needs_review','review':{'problems':[problems[0]]}}]
    tickets,directives,routes=_tickets(ctx(tmp_path,'figures'),content,rows)
    assert len(tickets)==2 and len({t['ticket_id'] for t in tickets})==2
    assert {t['allowed_paths'][0] for t in tickets}=={'/body_markdown','/examples/0/statement'}
    assert all(r['target_artifact_kind']=='content' for r in routes)


def test_real_browser_lazy_animation_anchor_is_mounted_when_detail_opens(tmp_path):
    import subprocess
    from pathlib import Path
    from aiforstudy.checks.offline import _browser_executable
    browser=_browser_executable(Path(__file__).parents[1])
    if not browser:
        import pytest
        pytest.skip('缺Chromium')
    source=(Path(__file__).parents[1]/'blocks/render/assets/app.js').read_text()
    function=source[source.index('  function mountUnitAnimation('):source.index('\n  /** 按需把 assets/animations.js')]
    page=tmp_path/'lazy.html'
    page.write_text('<div id="main"><div class="unit"></div></div><div id="drawer-body"></div><pre id="result"></pre><script>'+"var el={main:document.getElementById('main'),drawerBody:document.getElementById('drawer-body')};el.drawerBody.dataset.unitId='U';var animationInstances=new Map();function currentUnitId(){return 'U';}function ensureAnimations(){}var mounted=0;var D={units:{U:{}},animations:{U:[{request_id:'R',placement:{status:'resolved',target:'/explainables/0/what',anchor_id:'A'}}]}};window.DSHAnimations={mountAnimations:function(target){mounted++;target.textContent='actual player mounted';return function(){};}};"+function+"mountUnitAnimation('U');var warnings=document.querySelectorAll('.missing-ref').length;document.getElementById('drawer-body').innerHTML='<div data-media-anchor=\"A\"></div>';mountUnitAnimation('U');document.getElementById('result').textContent=JSON.stringify({warnings:warnings,mounted:mounted});</script>")
    result=subprocess.run([str(browser),'--headless','--no-sandbox','--disable-gpu','--user-data-dir='+str(tmp_path/'profile'),'--dump-dom',page.as_uri()],text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    assert '<pre id="result">{"warnings":0,"mounted":1}</pre>' in result.stdout


def test_manifest_check_has_machine_issues_and_coverage():
    from aiforstudy.blocks.media.media_manifest import validate_manifest
    report=validate_manifest({'content_hash':'C','requests':[{'request_id':'R','kind':'svg'}]}, {'schema_version':'bad','requests':[]})
    assert report['schema_version']=='CheckReportV1' and report['input_hash'].startswith('sha256:')
    assert report['coverage']['missing']==['R']
    assert {i['code'] for i in report['blocking_issues']}=={'MANIFEST_SCHEMA','MANIFEST_BINDING','MANIFEST_COVERAGE'}


def test_actual_static_dependencies_include_renderer_constants_review_code_and_roles(tmp_path,monkeypatch):
    from aiforstudy.blocks.media import dependencies as D
    c=ctx(tmp_path,'figures')
    config={'roles':{'figures':{'model':'author','effort':'high'},'judge':{'model':'judge','max_tokens':100}}}
    c.workspace.config_json=lambda name:config
    request={'request_id':'R','kind':'geometry'}
    original=D.file_identity;changes={}
    monkeypatch.setattr(D,'file_identity',lambda path:changes.get(str(path),original(path)))
    def dep():return D.resource_dependencies(c,request,{})
    first=dep()
    from aiforstudy.blocks.media import geometry,validation
    changes[geometry.__file__]='sha256:new-geometry';changed=dep()
    assert changed['generation_dependency_hash']!=first['generation_dependency_hash']
    changes[validation.__file__]='sha256:new-validation';reviewed=dep()
    assert reviewed['generation_dependency_hash']==changed['generation_dependency_hash']
    assert reviewed['review_dependency_hash']!=changed['review_dependency_hash']
    config['roles']['judge']['max_tokens']=200
    judged=dep();assert judged['generation_dependency_hash']==reviewed['generation_dependency_hash']
    assert judged['review_dependency_hash']!=reviewed['review_dependency_hash']
    config['roles']['figures']['effort']='low'
    assert dep()['generation_dependency_hash']!=judged['generation_dependency_hash']


def test_post_media_repeated_issue_keeps_frozen_standard_after_reorder(tmp_path):
    from aiforstudy.pipeline.media_stages import _tickets
    content=ticket_content(unit_id='U', _fields={'body_markdown':'正文','examples':[{'id':'EX-A','statement':'负数平方小于零'},{'id':'EX-B','statement':'另一题'}]})
    issue={'target_artifact_kind':'content','pointer':'/examples/0/statement','issue_key':'square-nonnegative','problem':'平方符号错误','evidence':'负数平方小于零','action':'补实数平方非负','expected':'实数平方非负'}
    row={'request_id':'R1','kind':'geometry','state':'needs_review','review':{'problems':[issue]}}
    c=ctx(tmp_path,'figures');first,_,_=_tickets(c,content,[row]);original=first[0]
    original['history']=[{'event':'attempt','round':1}]
    original['usage_refs']=['usage-original']
    write_json_atomic(tmp_path/'products/figures/U.json',{'payload':{'tickets':[original]}})
    content['examples'].reverse()
    changed={**issue,'pointer':'/examples/1/statement','problem':'同一平方错误仍在','action':'换个修改措辞','expected':'试图替换原冻结标准'}
    repeated,_,_=_tickets(c,content,[{'request_id':'R2','kind':'geometry','state':'needs_review','review':{'problems':[changed,changed]}}])
    assert len(repeated)==1
    assert repeated[0]['ticket_id']==original['ticket_id']
    assert repeated[0]['criteria']==original['criteria']
    assert repeated[0]['history']==original['history'] and repeated[0]['usage_refs']==original['usage_refs']
    assert repeated[0]['allowed_paths']==['/examples/1/statement']


def test_same_field_two_media_content_issues_remain_distinct(tmp_path):
    from aiforstudy.pipeline.media_stages import _tickets
    content=ticket_content(unit_id='U', _fields={'body_markdown':'分母为零且平方为负'})
    base={'target_artifact_kind':'content','pointer':'/body_markdown','evidence':'分母为零且平方为负','action':'修正数学条件','expected':'条件正确'}
    issues=[{**base,'issue_key':'denominator-nonzero','problem':'分母约束错'},{**base,'issue_key':'square-nonnegative','problem':'平方约束错'}]
    tickets,_,_=_tickets(ctx(tmp_path,'figures'),content,[{'request_id':'R','kind':'geometry','state':'needs_review','review':{'problems':issues}}])
    assert len(tickets)==2 and len({t['ticket_id'] for t in tickets})==2


def test_animation_cache_checks_current_real_environment_before_outer_reuse(tmp_path,monkeypatch):
    from aiforstudy.pipeline import media as M
    from aiforstudy.blocks.visual import manim_adapter as A,worker as W
    from pathlib import Path
    c=ctx(tmp_path,'figures');c.workspace.config_json=lambda name:{'roles':{'figures':{'model':'author'}}}
    source=tmp_path/'source.txt';source.write_text('教材')
    services=SimpleNamespace(worker_dir=W.resolve_worker_dir(),interpreter=__import__('sys').executable,ffmpeg=__import__('shutil').which('ffmpeg'),worker_services=lambda:object())
    monkeypatch.setattr(A,'default_services',lambda **k:services)
    environment={'environment':{'packages':{'manim':'0.1'},'native':{'cairo':'1'},'math_tex':{'ok':True,'frame_hash':'sha256:probe'}},'isolation':{'safe':True,'applied':{'landlock':{'backend':'landlock','abi':1}}}}
    monkeypatch.setattr(W,'probe_isolation',lambda **k:environment)
    request={'request_id':'R','dependency_paths':['/body_markdown'],'source_claims':['教材']}
    first=M.cache_identity(c,request,{'body_markdown':'正文'},source)
    environment['environment']['packages']['manim']='0.2'
    assert M.cache_identity(c,request,{'body_markdown':'正文'},source)!=first


def test_interaction_repair_receives_only_frozen_selected_ticket(tmp_path,monkeypatch):
    from aiforstudy.pipeline import media_stages as M,stage_io as T
    from aiforstudy.blocks.media.media_manifest import approve_media
    content,approval=fixture();monkeypatch.setattr(M,'_load_content',lambda c:(content,approval));monkeypatch.setattr(M,'_capabilities',lambda c:{'kinds':['svg']})
    d=none_decision();d.update(requirement='required',handled_by='interaction')
    plan=no_media_requirements(content,approval,capabilities={'kinds':[], 'interaction':True},decision=d)
    monkeypatch.setattr(M,'_load_author_media',lambda *a,**k:plan['author_media'])
    manifest={'schema_version':'MediaManifestV1','content_hash':approval['content_hash'],'requirements_hash':digest_of(plan),'requests':[]}
    media,_=approve_media(plan,manifest,approval)
    for stage,payload in [('media_plan',plan),('figures',manifest),('audit',{'approval':media})]:write_json_atomic(tmp_path/'products'/stage/'U.json',{'payload':payload})
    monkeypatch.setattr(M,'validate_requirements',lambda *a,**k:{'ok':True});monkeypatch.setattr(M,'_client',lambda *a,**k:object())
    received=[]
    def generate(*a,**k):received.append(k['repair_context']);return {'models':[],'results':[]}
    monkeypatch.setattr(T,'_legacy',lambda name:SimpleNamespace(generate_assigned_models=generate))
    context=ctx(tmp_path,'interaction');context.context.parse=tmp_path
    first=M.interaction_stage(context);assert first.repair_routes[0]['target_artifact_kind']=='interaction_spec'
    path=tmp_path/'products/interaction/U.json';payload=json.loads(path.read_text())['payload'];original=payload['tickets'][0]
    from copy import deepcopy
    unrelated=deepcopy(original);unrelated['ticket_id']='unselected';payload['tickets'].append(unrelated)
    write_json_atomic(path,{'payload':payload})
    context.repair_batch=SimpleNamespace(jobs=[SimpleNamespace(ticket_ids=(original['ticket_id'],))])
    second=M.interaction_stage(context)
    assert received[-1]==[original]
    assert second.quality_state.value=='needs_review'


def test_content_discovery_without_stable_issue_key_only_requests_evidence(tmp_path):
    from aiforstudy.pipeline.media_stages import _tickets
    issue={'target_artifact_kind':'content','pointer':'/body_markdown','problem':'分母条件错','evidence':'分母为零','action':'补非零','expected':'非零'}
    tickets,_,routes=_tickets(ctx(tmp_path,'figures'),ticket_content(_fields={'body_markdown':'分母为零'}),[{'request_id':'R','kind':'geometry','state':'needs_review','review':{'problems':[issue]}}])
    assert tickets[0]['kind']=='evidence_format' and tickets[0]['owner']=='reviewer'
    assert routes[0]['target_artifact_kind']=='media_evidence'


def test_actual_gateway_client_adapter_is_a_media_dependency(tmp_path,monkeypatch):
    from aiforstudy.blocks.media import dependencies as D
    from aiforstudy.pipeline import gateway_client
    c=ctx(tmp_path,'figures');request={'request_id':'R','kind':'geometry'}
    first=D.resource_dependencies(c,request,{})
    original=D.file_identity
    monkeypatch.setattr(D,'file_identity',lambda path:'sha256:changed' if str(path)==gateway_client.__file__ else original(path))
    changed=D.resource_dependencies(c,request,{})
    assert changed['generation_dependency_hash']!=first['generation_dependency_hash']
    assert changed['review_dependency_hash']!=first['review_dependency_hash']


def test_evidence_supplement_preserves_original_ticket_and_new_content_issues(tmp_path):
    from aiforstudy.pipeline.media_stages import _tickets
    content=ticket_content(unit_id='U', _fields={'body_markdown':'分母为零且平方为负'})
    c=ctx(tmp_path,'figures')
    original=_tickets(c,content,[{'request_id':'R','kind':'geometry','state':'needs_review','evidence_only':True,'validation':{'issues':[{'code':'MEDIA_REVIEW_MISSING','message':'截图证据不足'}]}}])[0][0]
    original['history']=[{'event':'repair-attempt','round':1}]
    base={'target_artifact_kind':'content','pointer':'/body_markdown','evidence':'分母为零且平方为负'}
    issues=[{**base,'issue_key':'denominator-nonzero','problem':'分母条件错','action':'补非零','expected':'分母非零'}, {**base,'issue_key':'square-nonnegative','problem':'平方条件错','action':'补非负','expected':'平方非负'}]
    rows=[{'request_id':'R','kind':'geometry','state':'needs_review','repair_tickets':[original],'validation':{'issues':[{'code':'MEDIA_REVIEW_MISSING','message':'截图证据仍不足'}]},'review':{'problems':issues}}]
    tickets,directives,routes=_tickets(c,content,rows)
    assert len(tickets)==3
    assert tickets[0]==original
    assert [r['target_artifact_kind'] for r in routes]==['media_evidence','content','content']
    assert [d.kind for d in directives]==['evidence_format','text_content','text_content']
    assert len({t['ticket_id'] for t in tickets})==3
