"""Two independent TOCs must really be consumed, reconciled and traceable."""
import json
from pathlib import Path

import pytest


def entry(title='向量', number='第一章', level=1, page=3, category='teaching', **extra):
    return dict(title=title, number=number, level=level, printed_page='7',
                toc_page=page, category=category, reason='目录图该行清晰可见', **extra)


def fixture(tmp_path, monkeypatch, rows=None, pages=(3,)):
    from blocks.parse.toc import dual_source as D
    (tmp_path/'document.md').write_text('第一章 向量\n正文。')
    (tmp_path/'toc.json').write_text(json.dumps({'book_title':'测试书', 'printed_entries': rows or [
        {'text':'第一章 向量','toc_page':7,'level':1,'from_page':3}]}))
    pdf=tmp_path/'book.pdf';pdf.write_bytes(b'controlled-pdf')
    def render(pdf, requested, out_dir, **kw):
        shots=[]
        for n in requested:
            path=tmp_path/f'page-{n}.png';path.write_bytes(b'controlled-image-'+str(n).encode())
            shots.append({'path':str(path),'pdf_page':n})
        return shots
    monkeypatch.setattr(D,'render_toc_pages',render)
    return D,pdf


class Client:
    def __init__(self, change=False, unsure=False, omit=False):
        self.calls=[];self.change=change;self.unsure=unsure;self.omit=omit
    def chat_json(self,messages,*,tag,images=None,**kwargs):
        self.calls.append({'messages':messages,'tag':tag,'images':images})
        data=json.loads(messages[-1]['content'])
        if tag.startswith('toc-visual:'):
            assert images
            assert 'mineru' not in data
            result={'schema_version':'1.0','complete':True,
                    'pages':[{'pdf_page':p,'status':'toc'} for p in data['pdf_pages']],
                    'entries':[entry('空间' if self.change else '向量',page=p) for p in data['pdf_pages']]}
        elif tag.startswith('toc-compare:'):
            assert not images
            result={'schema_version':'1.0','matches':[] if self.omit else [
                {'mineru_id':m['entry_id'],'visual_id':v['entry_id'],
                 'status':'different' if self.change else 'same','reason':'逐项比较'}
                for m,v in zip(data['mineru'],data['visual'])]}
        else:
            assert tag.startswith('toc-arbitrate:') and images
            rows=[{**v,'source_ids':[m['entry_id'],v['entry_id']],'basis':'visual'}
                  for m,v in zip(data['mineru'],data['visual'])]
            result={'schema_version':'1.0','entries':rows,'discarded':[],
                    'unresolved':['图像看不清'] if self.unsure else []}
        return {'data':result,'usage':{'total_tokens':11},'cached':False}


def test_even_identical_tocs_read_images_and_compare_then_reuse(tmp_path,monkeypatch):
    D,pdf=fixture(tmp_path,monkeypatch);client=Client()
    result=D.generate_directory(tmp_path,pdf,client)
    assert result['status']=='accepted',result
    assert len(client.calls)==2
    assert client.calls[0]['images']
    toc=json.loads((tmp_path/'toc.v2.json').read_text())
    assert toc['nodes'][0]['title']=='向量'
    assert D.is_current(tmp_path,pdf)
    D.generate_directory(tmp_path,pdf,client)
    assert len(client.calls)==2
    (tmp_path/'document.md').write_text('changed source')
    assert not D.is_current(tmp_path,pdf)


def test_disagreement_rechecks_actual_image_and_uses_visual(tmp_path,monkeypatch):
    D,pdf=fixture(tmp_path,monkeypatch);client=Client(change=True)
    result=D.generate_directory(tmp_path,pdf,client)
    assert result['status']=='accepted',result
    assert len(client.calls)==3
    assert client.calls[-1]['images']==client.calls[0]['images']
    assert json.loads((tmp_path/'toc.v2.json').read_text())['nodes'][0]['title']=='空间'
    assert (tmp_path/'toc.arbitration.json').is_file()


@pytest.mark.parametrize('options',[{'omit':True},{'change':True,'unsure':True}])
def test_missing_compare_coverage_or_uncertain_review_never_accepts(tmp_path,monkeypatch,options):
    D,pdf=fixture(tmp_path,monkeypatch)
    result=D.generate_directory(tmp_path,pdf,Client(**options))
    assert result['status']=='needs_review'
    assert result['problems']
    assert not D.is_current(tmp_path,pdf)
    assert not (tmp_path/'toc.v2.json').exists()


def test_missing_images_do_not_fall_back_to_text(tmp_path,monkeypatch):
    D,pdf=fixture(tmp_path,monkeypatch);client=Client()
    monkeypatch.setattr(D,'render_toc_pages',lambda *a,**k:[])
    result=D.generate_directory(tmp_path,pdf,client)
    assert result['status']=='needs_review'
    assert client.calls==[]


def test_more_than_eight_toc_pages_are_all_sent(tmp_path,monkeypatch):
    pages=list(range(3,13))
    rows=[{'text':'第一章 向量','toc_page':7,'from_page':p,'level':1} for p in pages]
    D,pdf=fixture(tmp_path,monkeypatch,rows=rows);client=Client()
    result=D.generate_directory(tmp_path,pdf,client)
    # Duplicate synthetic chapters may block final identity, but no directory page is skipped.
    sent=[p for c in client.calls if c['tag'].startswith('toc-visual:')
          for p in json.loads(c['messages'][-1]['content'])['pdf_pages']]
    assert sent==pages


def test_format_unification_does_not_hide_math_or_title_differences():
    from blocks.parse.toc.dual_source import normalize_entry, comparison_key
    a=normalize_entry(entry('向量','第一章'),source='M',index=1)
    b=normalize_entry(entry('向量','１'),source='V',index=1)
    assert comparison_key(a)==comparison_key(b)
    a=normalize_entry(entry('x+y=1'),source='M',index=1)
    b=normalize_entry(entry('x-y=1'),source='V',index=1)
    assert comparison_key(a)!=comparison_key(b)


def test_pure_exercise_chapter_excluded_with_children_but_teaching_kept():
    from blocks.parse.toc.dual_source import normalize_entry, make_authority
    rows=[entry('典型例题与方法'),entry('综合习题','第二章',category='exercise'),
          entry('第一组','2.1',2),entry('线性空间','第三章')]
    norm=[normalize_entry(r,source='V',index=i) for i,r in enumerate(rows,1)]
    toc,excluded=make_authority(norm,book_title='测试',slug='book',pdf_hash='p',document_hash='d')
    assert [n['title'] for n in toc['nodes']]==['典型例题与方法','线性空间']
    assert [r['title'] for r in excluded['entries']]==['综合习题','第一组']
    assert len(excluded['all_entries'])==4
    assert excluded['all_entries'][3]['order']==4


def test_visual_prompt_excludes_exercise_chapters_without_discarding_source():
    from blocks.parse.toc.dual_source import PROMPTS
    text=(PROMPTS/'directory_visual.md').read_text()
    assert '纯习题' in text and '例题' in text and 'schema_version' in text


def test_compare_cannot_hide_order_change_by_reordering_matches():
    from blocks.parse.toc import dual_source as D
    m=[D.normalize_entry(entry(title=t,number=str(i)),source='M',index=i) for i,t in enumerate(['A','B'],1)]
    v=[D.normalize_entry(entry(title=t,number=str(3-i)),source='V',index=i) for i,t in enumerate(['B','A'],1)]
    data={'schema_version':'1.0','matches':[
        {'mineru_id':'M0002','visual_id':'V0001','status':'same'},
        {'mineru_id':'M0001','visual_id':'V0002','status':'same'}]}
    assert D._compare(data,m,v)


def test_exercise_parent_does_not_reclassify_its_answers():
    from blocks.parse.toc import dual_source as D
    rows=[entry('向量'),entry('综合习题','第二章',category='exercise'),
          entry('参考答案','2.1',2,category='answers')]
    rows=[D.normalize_entry(r,source='V',index=i) for i,r in enumerate(rows,1)]
    _,sidecar=D.make_authority(rows,book_title='test',slug='b',pdf_hash='p',document_hash='d')
    assert sidecar['entries'][-1]['category']=='answers'


def test_output_tampering_and_image_deletion_invalidate_cache(tmp_path,monkeypatch):
    D,pdf=fixture(tmp_path,monkeypatch)
    assert D.generate_directory(tmp_path,pdf,Client())['status']=='accepted'
    (tmp_path/'page-3.png').unlink()
    assert not D.is_current(tmp_path,pdf)


def test_selection_marker_is_not_normalized_away():
    from blocks.parse.toc import dual_source as D
    a=D.normalize_entry(entry(number='*1.2'),source='V',index=1)
    b=D.normalize_entry(entry(number='1.2'),source='M',index=1)
    assert D.comparison_key(a)!=D.comparison_key(b)


def test_pdf_discovery_adds_page_missing_from_mineru(tmp_path,monkeypatch):
    from blocks.parse.toc import cli
    D,pdf=fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(cli,'_find_toc_pages',lambda pdf:[3,4])
    assert D._pages(tmp_path,json.loads((tmp_path/'toc.json').read_text()),pdf)==[3,4]


@pytest.mark.parametrize('error_name',['Cancelled','BudgetExceeded'])
def test_actual_model_control_error_propagates_and_is_recorded(tmp_path,monkeypatch,error_name):
    from aiforstudy import errors
    D,pdf=fixture(tmp_path,monkeypatch)
    class Stop:
        def chat_json(self,*a,**kw):raise getattr(errors,error_name)('stop at boundary')
    with pytest.raises(getattr(errors,error_name)):
        D.generate_directory(tmp_path,pdf,Stop())
    report=json.loads((tmp_path/'toc.dual-source.json').read_text())
    assert error_name in str(report['problems'])


def test_model_same_cannot_hide_math_change(tmp_path,monkeypatch):
    D,pdf=fixture(tmp_path,monkeypatch)
    m=[D.normalize_entry(entry(title='x+y'),source='M',index=1)]
    v=[D.normalize_entry(entry(title='x-y'),source='V',index=1)]
    raw={'schema_version':'1.0','matches':[{'mineru_id':'M0001','visual_id':'V0001','status':'same'}]}
    assert D._compare(raw,m,v)


def test_title_normalization_preserves_math_superscripts():
    from blocks.parse.toc import dual_source as D
    a=D.normalize_entry(entry(title='x²+y²=1'),source='M',index=1)
    b=D.normalize_entry(entry(title='x2+y2=1'),source='V',index=1)
    assert D.comparison_key(a)!=D.comparison_key(b)


def test_model_complete_cannot_override_reported_unreadable_problems():
    from blocks.parse.toc import dual_source as D
    with pytest.raises(D.ContractError):
        D._visual({'schema_version':'1.0','complete':True,'pages':[{'pdf_page':3,'status':'toc'}],
                  'entries':[entry()], 'problems':['第3页第2行看不清']},[3],0)
