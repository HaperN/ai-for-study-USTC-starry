"""Current concept-reference impact, read-only planning and preparation ordering."""
import importlib.util
import json
from pathlib import Path

from support.runner_v5 import setup_book,_request,UNIT

ROOT=Path(__file__).resolve().parents[1]


def _tree(tmp_path, refs):
    parse=tmp_path/'parse';parse.mkdir()
    (parse/'concepts.json').write_text(json.dumps({'concepts':[
        {'id':'C-1','name':'向量','occurrences':[{'unit_id':'CH01'}]},
        {'id':'C-2','name':'矩阵','occurrences':[{'unit_id':'CH01'}]}]},ensure_ascii=False))
    lessons=tmp_path/'lesson';(lessons/'lessons').mkdir(parents=True)
    (lessons/'lessons/CH01.json').write_text(json.dumps({'unit_id':'CH01','concepts':[{'id':cid} for cid in refs]}))
    return parse,lessons


def _impact(tmp_path,refs,monkeypatch):
    parse,lessons=_tree(tmp_path,refs)
    spec=importlib.util.spec_from_file_location('concept_reference_doctor',ROOT/'tools/doctor.py')
    doctor=importlib.util.module_from_spec(spec);spec.loader.exec_module(doctor)
    monkeypatch.setattr(doctor,'PARSE',parse);monkeypatch.setattr(doctor,'LESSONS',lessons)
    before={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result=doctor.inv_concept_registration_is_complete()
    assert before=={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    return result


def test_impact_identifies_the_unit_and_missing_concept_without_rewriting(tmp_path,monkeypatch):
    findings=_impact(tmp_path,['C-1','C-9999'],monkeypatch)
    assert len(findings)==1 and findings[0]['不变量']=='概念登记完整'
    assert '1 处引用' in findings[0]['说明'] and 'CH01→C-9999' in findings[0]['说明']


def test_impact_is_empty_when_every_reference_resolves(tmp_path,monkeypatch):
    assert _impact(tmp_path,['C-1','C-2'],monkeypatch)==[]


def test_current_plan_with_missing_concepts_is_read_only_and_admits_preparation(tmp_path,capsys,monkeypatch):
    from aiforstudy.cli import main
    from aiforstudy.providers.gateway import Gateway
    ws=setup_book(tmp_path);(ws.root/'book/parse/concepts.json').unlink()
    before={str(p.relative_to(ws.root)):(p.read_bytes(),p.stat().st_mtime_ns) for p in ws.root.rglob('*') if p.is_file()}
    monkeypatch.setattr(Gateway,'call',lambda *a,**k:(_ for _ in ()).throw(AssertionError('plan cannot regenerate concepts')))
    assert main(['plan','--workspace',str(ws.root),'--book','mini-book','--units',UNIT,'--json'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['requires_preparation'] is True
    assert 'concepts' in [r['stage'] for r in result['preparation']]
    assert before=={str(p.relative_to(ws.root)):(p.read_bytes(),p.stat().st_mtime_ns) for p in ws.root.rglob('*') if p.is_file()}


def test_current_graph_prepares_concepts_before_any_author_node(tmp_path):
    from aiforstudy.pipeline.engine import build_engine
    ws=setup_book(tmp_path);engine=build_engine(_request(),ws)
    order=list(engine.graph.order())
    assert 'concepts' in order
    lessons=[n for n in engine.graph.nodes if n.stage=='lesson']
    assert lessons
    assert all('concepts' in n.requires for n in lessons)
    assert all(order.index('concepts')<order.index(n.node_id) for n in lessons)
