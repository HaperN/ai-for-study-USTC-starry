"""Directory warnings never weaken image coverage or source-accounting gates."""
import copy
import json

import pytest

from blocks.parse.toc import dual_source as D


def entry(page=3, number='第8章', title='空间解析几何', **changes):
    return dict(number=number, title=title, level=1, printed_page='1',
                pdf_page=None, toc_page=page, category='teaching',
                reason='图中章号和标题清晰', **changes)


def issue(*, severity='warning', code='source_anomaly', scope='entry', pages=(3,)):
    return dict(severity=severity, code=code, scope=scope, pdf_pages=list(pages),
                entry='习题9.6', reason='原书在11.6后清楚印作习题9.6，按原样保留')


def visual(**changes):
    data={'schema_version':'1.0', 'complete':True,
          'pages':[{'pdf_page':3,'status':'toc'}], 'entries':[entry()], 'issues':[]}
    data.update(changes)
    return data


def test_source_warning_keeps_original_transcription_and_response():
    raw=visual(issues=[issue()], entries=[entry(number='习题9.6', title='习题9.6')])
    before=copy.deepcopy(raw)
    rows=D._visual(raw,[3],0)
    assert len(rows)==1 and rows[0]['title']=='习题9.6'
    assert raw==before


@pytest.mark.parametrize(('patch','pages','code'), [
    ({},[3,4],'visual_page_coverage'),
    ({'pages':[{'pdf_page':3,'status':'toc'}]*2},[3],'visual_page_coverage'),
    ({'pages':[{'pdf_page':3,'status':'unreadable'}]},[3],'visual_unreadable'),
    ({'complete':False},[3],'visual_incomplete'),
    ({'pages':['not a page object']},[3],'visual_page_contract'),
    ({'pages':[{'pdf_page':True,'status':'toc'}]},[1],'visual_page_contract'),
])
def test_visual_rejection_identifies_the_actual_gate(patch,pages,code):
    raw=visual(**patch)
    with pytest.raises(D.ContractError,match=code):
        D._visual(raw,pages,0)


def test_explicit_blocker_reports_specific_reason_and_next_step():
    blocker=issue(severity='blocking',code='uncertain_classification')
    blocker['reason']='第3页末行无法区分讲解和纯习题'
    with pytest.raises(D.ContractError,match='uncertain_classification') as caught:
        D._visual(visual(issues=[blocker]),[3],0)
    message=str(caught.value)
    assert '第3页末行无法区分讲解和纯习题' in message
    assert '下一步' in message and '通过标准' in message


@pytest.mark.parametrize(('changed','code'), [
    ({'code':'unreadable'},'issue_severity'),
    ({'severity':'info'},'issue_severity'),
    ({'code':'arbitrary_free_text'},'issue_code'),
    ({'pdf_pages':[99]},'issue_scope'),
    ({'pdf_pages':[]},'issue_scope'),
    ({'scope':'entry','entry':''},'issue_scope'),
    ({'reason':''},'issue_reason'),
])
def test_ambiguous_or_mislabelled_warning_cannot_pass(changed,code):
    warning=issue();warning.update(changed)
    with pytest.raises(D.ContractError,match=code):
        D._visual(visual(issues=[warning]),[3],0)


@pytest.mark.parametrize('legacy',[
    ['本册从第8章开始，图像清晰'],
    [{'page':3,'reason':'原书错号但图像清晰'}],
])
def test_legacy_problems_are_not_guessed_to_be_harmless(legacy):
    with pytest.raises(D.ContractError,match='legacy_problems') as caught:
        D._visual(visual(problems=legacy),[3],0)
    assert '无法区分' in str(caught.value)
    assert '图像不可读' not in str(caught.value)


def test_legacy_empty_problem_response_remains_compatible():
    raw=visual();raw.pop('issues');raw['problems']=[]
    assert len(D._visual(raw,[3],0))==1


@pytest.fixture
def directory(tmp_path,monkeypatch):
    (tmp_path/'document.md').write_text('第8章 空间解析几何\n正文')
    (tmp_path/'toc.json').write_text(json.dumps({'book_title':'分册测试','printed_entries':[
        {'text':f'第{n+6}章 空间{n}','toc_page':n,'level':1,'from_page':n}
        for n in range(2,7)]},ensure_ascii=False))
    pdf=tmp_path/'book.pdf';pdf.write_bytes(b'controlled-pdf')
    def render(_pdf,pages,out_dir):
        shots=[]
        for n in pages:
            path=tmp_path/f'page-{n}.png';path.write_bytes(f'image{n}'.encode())
            shots.append({'pdf_page':n,'path':str(path)})
        return shots
    from blocks.parse.toc import cli
    monkeypatch.setattr(cli,'_find_toc_pages',lambda _pdf:[])
    monkeypatch.setattr(D,'render_toc_pages',render)
    return tmp_path,pdf


class DirectoryClient:
    def __init__(self,arbitrate=False,fail_page=None):
        self.calls=[];self.arbitrate=arbitrate;self.fail_page=fail_page

    def chat_json(self,messages,*,tag,images=None):
        data=json.loads(messages[-1]['content'])
        self.calls.append((tag,data,images))
        if tag.startswith('toc-visual:'):
            pages=data['pdf_pages']
            rows=[entry(page=n,number=f'第{n+6}章',title=f'空间{n}') for n in pages]
            for row in rows:row['printed_page']=str(row['toc_page'])
            if self.arbitrate:rows[0]['title']='视觉差异'+rows[0]['title']
            warnings=[issue(pages=(pages[0],))]
            response=visual(entries=rows,issues=warnings,
                pages=[{'pdf_page':n,'status':'unreadable' if n==self.fail_page else 'toc'} for n in pages])
        elif tag.startswith('toc-compare:'):
            response={'schema_version':'1.0','matches':[
                {'mineru_id':m['entry_id'],'visual_id':v['entry_id'],
                 'status':'different' if self.arbitrate else 'same','reason':'按图逐项比对'}
                for m,v in zip(data['mineru'],data['visual'])]}
        else:
            response={'schema_version':'1.0','entries':[
                {**m,'source_ids':[m['entry_id'],v['entry_id']],'basis':'visual','reason':'原图此页章标题清晰'}
                for m,v in zip(data['mineru'],data['visual'])],
                'discarded':[],'issues':[issue(pages=tuple(data['pdf_pages']))]}
        return {'data':response,'usage':{'total_tokens':11},'cached':False}


@pytest.mark.parametrize('arbitrate',[False,True])
def test_all_batches_and_source_accounting_complete_with_auditable_warnings(directory,arbitrate):
    path,pdf=directory;client=DirectoryClient(arbitrate=arbitrate)
    report=D.generate_directory(path,pdf,client)
    assert report['status']=='accepted',report
    assert [call[1]['pdf_pages'] for call in client.calls if call[0].startswith('toc-visual:')]==[[2,3,4,5],[6]]
    assert len(report['warnings'])==(7 if arbitrate else 2)
    assert all(w['issue']['severity']=='warning' and w['tag'].startswith('toc-') for w in report['warnings'])
    assert [n['title'] for n in json.loads((path/'toc.v2.json').read_text())['nodes']]==['空间2','空间3','空间4','空间5','空间6']
    assert D.is_current(path,pdf)
    # Warning audit is covered by the already fingerprinted raw response artifact.
    response=json.loads((path/'toc.visual.json').read_text());response['batches'][0]['response']['issues']=[]
    (path/'toc.visual.json').write_text(json.dumps(response))
    assert not D.is_current(path,pdf)


def test_later_unreadable_batch_keeps_earlier_warning_but_never_publishes(directory):
    path,pdf=directory;client=DirectoryClient(fail_page=6)
    report=D.generate_directory(path,pdf,client)
    assert report['status']=='needs_review'
    assert len(report['warnings'])==1
    assert 'visual_unreadable' in report['problems'][0]
    assert len(client.calls)==2 and not (path/'toc.v2.json').exists()


@pytest.mark.parametrize('source_ids', [['M0001','V9999'],['M0001'],['M0001','V0001','V0001']])
def test_arbitration_warnings_do_not_relax_source_reference_coverage(source_ids):
    m=[D.normalize_entry(entry(),source='M',index=1)]
    v=[D.normalize_entry(entry(),source='V',index=1)]
    raw={'schema_version':'1.0','issues':[issue()], 'discarded':[],
         'entries':[{**entry(),'source_ids':source_ids,'basis':'visual'}]}
    with pytest.raises(D.ContractError):D._arbitrate(raw,m,v,[3])


def test_arbitration_explicit_blocker_is_not_lost_when_unresolved_is_absent():
    m=[D.normalize_entry(entry(),source='M',index=1)]
    v=[D.normalize_entry(entry(),source='V',index=1)]
    raw={'schema_version':'1.0','issues':[issue(severity='blocking',code='unresolved_difference')],
         'entries':[{**entry(),'source_ids':['M0001','V0001'],'basis':'visual'}],'discarded':[]}
    with pytest.raises(D.ContractError,match='unresolved_difference'):D._arbitrate(raw,m,v,[3])


def test_comparison_explicit_blocker_cannot_be_masked_by_same():
    m=[D.normalize_entry(entry(),source='M',index=1)]
    v=[D.normalize_entry(entry(),source='V',index=1)]
    raw={'schema_version':'1.0','issues':[issue(severity='blocking',code='unresolved_difference')],
         'matches':[{'mineru_id':'M0001','visual_id':'V0001','status':'same'}]}
    with pytest.raises(D.ContractError,match='unresolved_difference'):D._compare(raw,m,v)


def test_comparison_warning_is_audited_without_skipping_matching(directory):
    class WithComparisonWarning(DirectoryClient):
        def chat_json(self,*args,**kwargs):
            result=super().chat_json(*args,**kwargs)
            if kwargs['tag'].startswith('toc-compare:'):
                data=json.loads(args[0][-1]['content'])
                result['data']['issues']=[issue(pages=tuple(data['pdf_pages']))]
            return result
    path,pdf=directory
    result=D.generate_directory(path,pdf,WithComparisonWarning())
    assert result['status']=='accepted'
    assert [w['tag'] for w in result['warnings']]==[
        'toc-visual:1','toc-visual:2', 'toc-compare:3', 'toc-compare:4',
        'toc-compare:5', 'toc-compare:6', 'toc-compare:7']


@pytest.mark.parametrize('missing',['reason','entries'])
def test_warning_does_not_override_missing_entry_evidence_or_empty_toc_page(missing):
    raw=visual(issues=[issue()])
    if missing=='reason':raw['entries'][0]['reason']=''
    else:raw['entries']=[]
    with pytest.raises(D.ContractError):D._visual(raw,[3],0)
