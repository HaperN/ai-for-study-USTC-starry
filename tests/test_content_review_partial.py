"""A protocol error cannot erase another current, independently valid failure."""
from copy import deepcopy
import importlib
import json

import pytest

from aiforstudy.blocks.content.content_contracts import content_digest
from aiforstudy.errors import ContractError
from support.content_case import approved_inputs


def partial_module():
    assert importlib.util.find_spec('aiforstudy.blocks.content.review_partial'), 'missing bounded partial review retention'
    return importlib.import_module('aiforstudy.blocks.content.review_partial')


def case(count=2):
    c,source,policy,document,reports,math=approved_inputs()
    original=reports[1]
    first=original['review_items'][0]
    items=[{**deepcopy(first),'criterion_id':'test:'+str(i)} for i in range(count)]
    catalog=original['evidence_catalog']
    row=deepcopy(original['verdicts'][0])
    rows=[{**deepcopy(row),'criterion_id':item['criterion_id']} for item in items]
    return c,source,policy,document,reports,math,items,catalog,rows


def exchange(c,source,items,catalog,rows,offset=0):
    batch=items[offset:offset+12]
    relevant=catalog
    aliases={f'E{index:04d}':eid for index,eid in enumerate(sorted(catalog),1) if eid in relevant}
    reverse={v:k for k,v in aliases.items()}
    raw={'verdicts':deepcopy(rows)}
    for row in raw['verdicts']:
        for ref in row.get('evidence_refs',[]):ref['evidence_id']=reverse.get(ref['evidence_id'],ref['evidence_id'])
    return {'schema_version':'ContentReviewExchangeV1',
        'binding':{'encoding':'EvidenceAliasesV1','content_hash':content_digest(c),
            'source_context_hash':source.get('source_context_hash') or content_digest(source),
            'policy_digest':items[0]['policy_digest'],'items_digest':content_digest({'items':batch}),
            'catalog_digest':content_digest(relevant),'batch_index':offset//12},
        'criteria':deepcopy(batch),'evidence_catalog':deepcopy(relevant),
        'evidence_aliases':aliases,'response':raw}


def decode(response,aliases):
    result=deepcopy(response)
    for row in result['verdicts']:
        for ref in row.get('evidence_refs',[]):
            if ref['evidence_id'] not in aliases:raise ContractError('unknown evidence alias')
            ref['evidence_id']=aliases[ref['evidence_id']]
    return result


def build(data,exchanges,error=None):
    c,source,_,_,_,_,items,catalog,_=data
    return partial_module().build_partial_review_report(c,source,items,catalog,exchanges,
        error or ContractError('another verdict protocol failed'),decode_response=decode)


def test_valid_failure_survives_another_rows_bad_alias_and_retains_unknown():
    data=case(3);c,source,*_,items,catalog,rows=data
    rows[0]['status']='fail';rows[0]['reason']='原条件被遗漏。'
    rows[1]['evidence_refs'][0]['evidence_id']='bad:reference'
    rows[2]['status']='unknown';rows[2]['reason']='证据不足。'
    before=deepcopy(rows);raw=exchange(c,source,items,catalog,rows)
    report=build(data,[raw])
    assert [r['status'] for r in report['verdicts']]==['fail','unknown','unknown']
    assert report['verdicts'][0]==before[0] and report['verdicts'][2]==before[2]
    assert report['complete'] is False and report['decision']=='needs_review'
    assert report['schema_version']=='PartialReviewReportV1'
    assert report['exchanges']==[raw] and raw['response']['verdicts'][1]['evidence_refs'][0]['evidence_id']=='bad:reference'
    assert report['unresolved_criteria']==['test:1']
    assert report['blocking_issues'][0]['status']=='fail'


def test_later_timeout_keeps_prior_fail_and_marks_unrequested_batch_unknown():
    data=case(13);c,source,*_,items,catalog,rows=data
    rows[0]['status']='fail'
    report=build(data,[exchange(c,source,items,catalog,rows[:12])],TimeoutError('second batch timeout'))
    assert report['verdicts'][0]['status']=='fail'
    assert [r['status'] for r in report['verdicts'][1:12]]==['pass']*11
    assert report['verdicts'][12]['status']=='unknown' and report['unresolved_criteria']==['test:12']


def test_protocol_error_does_not_skip_remaining_review_batches():
    from aiforstudy.blocks.content.review import review_content, ReviewProtocolError
    data=case(13);c,source,_,document,_,_,items,catalog,rows=data
    rows[0]['evidence_refs'][0]['evidence_id']='invalid-first-batch'
    rows[12].update(status='fail',reason='后续批次仍须核对的真实错误。')
    responses=[exchange(c,source,items,catalog,rows[:12])['response'],
               exchange(c,source,items,catalog,rows[12:],offset=12)['response']]
    class Client:
        calls=0
        def chat_json(self,*args,**kwargs):
            self.calls+=1
            return {'data':deepcopy(responses[self.calls-1])}
    client=Client()
    with pytest.raises(ReviewProtocolError) as caught:
        review_content(c,document,source,items,None,client)
    assert client.calls==2
    report=caught.value.partial_report
    assert report['verdicts'][-1]['status']=='fail'
    assert report['unresolved_criteria']==['test:0']


def test_duplicate_criterion_never_chooses_first_fail_or_last_pass():
    data=case();c,source,*_,items,catalog,rows=data
    duplicate=deepcopy(rows[0]);duplicate['status']='fail'
    report=build(data,[exchange(c,source,items,catalog,[duplicate,*rows])])
    assert report['verdicts'][0]['status']=='unknown' and report['verdicts'][1]['status']=='pass'
    assert not any(row['status']=='fail' for row in report['verdicts'])


@pytest.mark.parametrize('damage',['content','source','policy','criteria','catalog','aliases','batch','duplicate_batch'])
def test_exchange_binding_cannot_mix_old_inputs_or_redirect_aliases(damage):
    data=case();c,source,*_,items,catalog,rows=data;ex=exchange(c,source,items,catalog,rows);exchanges=[ex]
    if damage in ('content','source','policy'):ex['binding'][damage+'_hash' if damage!='policy' else 'policy_digest']='sha256:foreign'
    if damage=='source':ex['binding']['source_context_hash']='sha256:foreign';ex['binding'].pop('source_hash',None)
    if damage=='criteria':ex['criteria'][0]['expected']='weakened'
    if damage=='catalog':next(iter(ex['evidence_catalog'].values()))['text']='invented'
    if damage=='aliases':ex['evidence_aliases'][next(iter(ex['evidence_aliases']))]='source:invented'
    if damage=='batch':ex['binding']['batch_index']=99
    if damage=='duplicate_batch':exchanges.append(deepcopy(ex))
    with pytest.raises(ContractError):build(data,exchanges)


def test_partial_report_cannot_authorize_content_even_if_its_rows_all_pass():
    from aiforstudy.blocks.content.content_approval import approve_content
    c,source,policy,document,reports,math=approved_inputs()
    items=reports[1]['review_items'];catalog=reports[1]['evidence_catalog'];rows=reports[1]['verdicts']
    data=(c,source,policy,document,reports,math,items,catalog,rows)
    # 复核是一个**分批**协议（每批 12 条）。把全部判据按同一批大小切成多个交换，
    # 否则新增判据会把一次交换撑破，后面几条没有交换可重建 → 被判 unknown。
    exchanges=[exchange(c,source,items,catalog,rows,offset=start)
               for start in range(0,len(items),12)]
    report=build(data,exchanges)
    assert all(r['status']=='pass' for r in report['verdicts'])
    with pytest.raises(ContractError,match='exactly current local and semantic'):
        approve_content(c,document,[reports[0],report],policy,source_context=source,math_report=math)


@pytest.mark.parametrize('error', [TimeoutError('batch two timeout'),
    ContractError('capacity_failure: finish_reason=length'), ValueError('bad JSON')])
def test_actual_reviewer_later_transport_failure_keeps_prior_current_failure(error):
    from aiforstudy.blocks.content.review import review_content
    data=case(13); c,source,_,document,_,_,items,catalog,rows=data
    rows[0]['status']='fail'
    first=exchange(c,source,items,catalog,rows[:12])
    class Client:
        calls=0
        def chat_json(self,*args,**kwargs):
            self.calls+=1
            if self.calls==2: raise error
            return {'data':deepcopy(first['response'])}
    client=Client();saved=[];captured=[]
    with pytest.raises(type(error)) as caught:
        review_content(c,document,source,items,None,client,
                       response_sink=saved.append,exchange_sink=captured.append)
    assert caught.value is error
    report=caught.value.partial_report
    assert client.calls==2 and len(saved)==len(captured)==1
    assert report['verdicts'][0]==rows[0]
    assert report['verdicts'][-1]['status']=='unknown'
    assert report['exchanges']==captured
    assert report['complete'] is False and report['schema_version']=='PartialReviewReportV1'
