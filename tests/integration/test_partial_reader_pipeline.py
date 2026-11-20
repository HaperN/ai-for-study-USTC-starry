"""The actual Runner, bounded reviews and reader use one offline production chain."""
import json
from copy import deepcopy
from pathlib import Path
import pytest

from aiforstudy.core.jsonio import read_json
from support.runner_v5 import TeachingTransport, run_book
from support.book_factory import UNIT


class UnknownReviewer(TeachingTransport):
    """"如实说不知道"的复核模型：对**每一条**判据都回 unknown。

    按输入的 review_items 逐条回应，而不是回空数组 —— 判据集合会随策略增加，
    写死条数会让这个测试在策略变化时变成协议错（那是测试脆弱，不是链路问题）。
    """

    def _answer(self, system, user):
        data=json.loads(user)
        if data.get('role_task')=='reviewer':
            self.review_calls+=1
            frozen=data.get('frozen_context') or {}
            items=[row for row in (frozen.get('criteria') or frozen.get('review_items') or [])
                   if isinstance(row,dict)]
            catalog=frozen.get('evidence_catalog') or {}
            # 正文复核的判词是五字段：criterion_id/version/status/reason/evidence_refs，
            # 且不得带额外字段（`why`/`problem`/`action`/`expected` 是**媒体**侧的八字段口径）。
            allowed=frozen.get('criterion_evidence_ids') or {}
            def refs_for(cid):
                out=[]
                for eid in (allowed.get(cid) or []):
                    entry=catalog.get(eid) or {}
                    quote=entry.get('text') if isinstance(entry, dict) else ''
                    if isinstance(quote, str) and quote.strip():
                        out.append({'evidence_id':eid,'quote':quote[:120]})
                    if len(out)>=2: break
                return out
            verdicts=[{'criterion_id':row.get('criterion_id'),'version':row.get('version'),
                       'status':'unknown','reason':'现有证据无法核实这条判据。',
                       'evidence_refs':refs_for(row.get('criterion_id'))} for row in items]
            return json.dumps({'verdicts':verdicts})
        return super()._answer(system,user)


class OneBadUnit(TeachingTransport):
    def _answer(self, system, user):
        data=json.loads(user); frozen=data.get('frozen_context') or {}
        unit=frozen.get('unit_id') or (frozen.get('source_context') or {}).get('unit_id')
        self.reject_first=unit==UNIT
        self.refuse_repair=unit==UNIT
        return super()._answer(system,user)


@pytest.mark.parametrize('unknown',[False,True])
def test_real_chain_delivers_verified_or_explicit_unknown_reader(tmp_path,unknown):
    transport=UnknownReviewer() if unknown else TeachingTransport()
    result,store,engine,transport=run_book(tmp_path,transport=transport,until='delivery')
    render=Path(store.work)/'products/render/CH01.json'
    assert render.is_file(),result.as_dict()
    payload=read_json(render)['payload']
    assert payload['schema_version']=='ReaderProjectionV1',payload
    out=Path(payload['out_dir'])
    assert (out/'index.html').is_file() and (out/'data/data.js').is_file()
    data=json.loads((out/'data/data.js').read_text().split('window.__DATA__ = ',1)[1].rsplit(';',1)[0])
    assert data['units'][UNIT]['reader_document']['content_hash']
    expected='unverified' if unknown else 'verified'
    assert payload['reader_snapshot']['preview_only'] is False
    assert payload['reader_snapshot']['production_accepted'] is (not unknown)
    assert data['units'][UNIT]['review_outcome']['status']==expected, data['units'][UNIT]['review_outcome']
    assert transport.author_calls==1
    assert transport.patch_calls==(2 if unknown else 0)
    if unknown:
        # R1（用户选 A）：第一道审核没通过（unverified / known_error / missing）→ **不进入媒体**。
        # 于是作者媒体意图审核不跑、该单元没有媒体计划；正文仍照常渲染（R9）。
        assert transport.intent_review_calls==0, '正文未过审时不得进入媒体'
        assert not (Path(store.work)/'products/media_plan'/f'{UNIT}.json').exists()
    else:
        assert transport.intent_review_calls==1
        plan=read_json(Path(store.work)/'products/media_plan'/f'{UNIT}.json')['payload']
        assert plan['intent_review']['review_outcome']['status']=='verified'
        assert {row['goal_id'] for row in plan['intent_review']['verdicts']}=={
            decision['goal_id'] for decision in plan['author_media']['requirements']['decisions']}
    delivery=[node for node in store.load_state()['nodes'].values() if node['stage']=='delivery']
    assert delivery and delivery[0]['execution_state']=='succeeded',delivery
    from aiforstudy.checks.offline import check_browser_execution
    import shutil
    browser=check_browser_execution(out,node=shutil.which('node'),workspace_root=Path(__file__).resolve().parents[2])
    assert browser.ok,browser.as_dict()
    if unknown:
        assert not result.scope_accepted
        audit=read_json(Path(store.work)/'products/text_audit'/f'{UNIT}.json')['payload']
        reviewed=next(r for r in audit['check_reports'] if 'review_items' in r)
        assert transport.review_calls==(transport.patch_calls+1)*((len(reviewed['review_items'])+11)//12)
        assert not audit['approval']
    else:
        assert result.scope_accepted,result.as_dict()


def test_local_repairs_retain_bad_unit_and_identify_it_without_stopping_good_unit(tmp_path):
    from dataclasses import replace
    from aiforstudy.contracts.request import Selectors
    from aiforstudy.pipeline.engine import run_request
    from aiforstudy.pipeline.services import Providers
    from support.runner_v5 import setup_book, _request
    workspace=setup_book(tmp_path);transport=OneBadUnit()
    request=replace(_request(),selectors=Selectors('units',('CH01',UNIT)))
    result,store,_=run_request(request,workspace,providers=Providers(llm=transport),until='delivery')
    path=Path(store.work)/'products/render/CH01.json'
    assert path.is_file(),result.as_dict()
    payload=read_json(path)['payload']
    assert payload['schema_version']=='ReaderProjectionV1',payload
    data=json.loads((Path(payload['out_dir'])/'data/data.js').read_text().split('window.__DATA__ = ',1)[1].rsplit(';',1)[0])
    assert data['units']['CH01']['review_outcome']['status']=='verified'
    assert data['units']['CH01']['reader_document']
    assert data['units'][UNIT]['reader_document']
    problem=next(row for row in data['problem_summary'] if row['unit_id']==UNIT)
    assert problem['status']=='known_error'
    assert data['units'][UNIT]['automatic_processing']['content_rounds_by_artifact']['lesson:'+UNIT]>=1
    # 首次返修改了正文，第二次原样交回；正文与判词均不变才停止。
    assert transport.patch_calls==2
    # R1（用户选 A）：正文第一道审核**没过的那个单元**不得进入媒体。
    # 注意章节级 `media_plan:CH01` 仍会跑一次（本章整体 verified），所以计数不为 0；
    # 被拦住的是单元级：产物里只有章节级计划，没有该单元的媒体计划。
    assert transport.intent_review_calls==1, '章节级一次意图审核；出问题的单元不得再进媒体'
    assert not (Path(store.work)/'products/media_plan'/f'{UNIT}.json').exists(), \
        'R1：正文未过审的单元不得产出媒体计划'
    assert not result.scope_accepted
    # R9/R7：坏单元照样保留当前正文并渲染；但页面上**不再有问题清单**（R7）。
    # `partial` 的口径是"有单元根本没有阅读产物"
    # （`bool(snapshot['missing'] or browser=='failed')`），所以这里为 False 是对的 ——
    # 不能再用它当"这一章有已知问题"的判据。
    # R1 之后该单元不做媒体，但**正文照样渲染**（R9）—— 所以它仍在 missing 列表之外。
    # `partial` 的口径是"有单元根本没有阅读产物"，与"该单元没有媒体"无关。
    assert payload['partial'] is False, '单元已渲染，partial 只表示缺产物，不表示有已知问题'
    assert payload['reader_snapshot']['missing']==[]
    # R7：诊断仍在 data.js / render 产物里，只是不再进读者页
    assert problem['label']=='确定错误'


def test_all_known_bad_units_keep_html_with_explicit_problem_summary(tmp_path):
    transport=TeachingTransport(reject_first=True,refuse_repair=True)
    result,store,_,_=run_book(tmp_path,transport=transport,until='delivery')
    record=read_json(Path(store.work)/'products/render/CH01.json')['payload']
    assert record.get('schema_version')=='ReaderProjectionV1'
    assert list((Path(store.work)/'legacy/render').rglob('*.html'))
    # R7（用户："不要放了"）：读者页不再输出问题清单与运行状态横幅。
    page=(Path(record['out_dir'])/'index.html').read_text()
    assert '确定错误' not in page, 'R7：问题清单不再进读者页'
    assert 'reader-problem-summary' not in page and 'reader-run-status' not in page
    assert transport.patch_calls==2
    assert transport.intent_review_calls==0, 'R1：正文确定错误时不进入媒体'
    nodes=store.load_state()['nodes']
    assert nodes['render:CH01']['execution_state']=='succeeded'
    assert nodes['render:CH01']['quality_state']=='needs_review'


def test_teaching_does_not_reuse_old_product_after_current_head_changes(tmp_path):
    from copy import deepcopy
    from aiforstudy.blocks.render.diagnostics import build_reader_snapshot
    from aiforstudy.core.jsonio import write_json_atomic
    from aiforstudy.errors import ContractError
    _,store,_,_=run_book(tmp_path,until='media_plan')
    run=Path(store.work).parent
    head_path=Path(store.work)/'products/content'/UNIT/'current.json'
    original=read_json(head_path)
    for field in ('content_hash','author_media_hash'):
        changed=deepcopy(original);changed[field]='sha256:newer-version'
        write_json_atomic(head_path,changed)
        out=tmp_path/field
        report=build_reader_snapshot(run,unit_ids=[UNIT],out_dir=out,require_complete_units=True)
        assert report['rendered_units']==([] if field=='content_hash' else [UNIT])
        assert report['partial'] and report['production_accepted'] is False
