"""Paper-only views keep independent evidence without inflating study counts."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from aiforstudy.practice.catalog import validate_review_files
from tools.build_practice_delivery import build
from tools.publish_expanded_student import publish_student
from test_practice_builder import setup, page_questions
from test_practice_publication_guards import bound


def inputs(tmp_path, *, basic=True):
    _, _, papers, reader = setup(tmp_path)
    text = {"book": {"id": "fixture", "title": "Fixture"}, "units": {"CH01-S01-SS04": {}},
            "course_library": {"sources": []}, "animations": {}, "media_purposes": {"items": []}}
    (reader / "data/data.js").write_text("window.__DATA__=" + json.dumps(text) + ";")
    pdf = reader / "assets/textbook.pdf"; pdf.write_bytes(b"%PDF-1.4 source fixture")
    common, separate = tmp_path / "common", tmp_path / "views"
    common.mkdir(); separate.mkdir()
    source = {"kind": "textbook_exercise", "course": "Fixture", "pdf_path": str(pdf),
              "pdf_page": 1, "question_number": "8", "source_quote": "(1) VIEW_QUESTION (2) OTHER_QUESTION"}
    parent = bound(common, {"id": "PARENT", "source": source, "stem": source["source_quote"],
                           "basic": True, "anchor_text": "正文锚点", "subquestion_count": 10,
                           "source_file_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()})
    child_source = {**source, "question_number": "8(1)", "source_quote": "(1) VIEW_QUESTION",
                    "source_parent_question_id": parent["id"],
                    "parent_content_sha256": parent["review"]["content_sha256"]}
    view = bound(separate, {"id": "VIEW-1", "source": child_source, "stem": "VIEW_QUESTION",
                           "answer": "VIEW_SECRET_ANSWER", "solution": "VIEW_SECRET_SOLUTION",
                           "basic": basic, "paper_only": True, "subquestion_count": 1,
                           "source_parent_question_id": parent["id"],
                           "parent_content_sha256": parent["review"]["content_sha256"],
                           "source_subquestion_numbers": [1], "new_source_materials": 0,
                           "source_file_sha256": parent["source_file_sha256"]})
    catalog = common / "catalog.json"; catalog.write_text(json.dumps([parent]))
    paper_catalog = separate / "catalog.json"; paper_catalog.write_text(json.dumps([view]))
    paper = {"id": "paper", "kind": "coverage", "edition": "full", "title": "Paper",
             "purpose": "Practice", "items": [{"question_id": view["id"], "points": 10}]}
    papers.write_text(json.dumps([paper]))
    return parent, view, catalog, paper_catalog, papers, reader


def test_view_renders_in_paper_only_and_exports_portable_evidence(tmp_path):
    parent, view, catalog, views, papers, reader = inputs(tmp_path)
    out = tmp_path / "out"
    report = build(catalog, papers, reader, out, paper_catalog=views)
    assert report["editions"]["full"]["questions"] == 1
    assert report["paper_views"] == 1 and report["paper_view_new_source_materials"] == 0
    assert set(page_questions(out / "完整版-习题集.html")) == {"PARENT"}
    assert "VIEW-1" not in (out / "教材/assets/practice-data.js").read_text()
    assert "VIEW-1" not in (out / "章节练习/CH01-S01-SS04.html").read_text()
    answer = page_questions(out / "试卷包/生成试卷/paper-解析.html")["VIEW-1"]
    assert answer["answer"] == "VIEW_SECRET_ANSWER"
    assert answer["source"]["question_number"] == "8(1)"
    assert answer["public_source_path"] == "教材/assets/textbook.pdf"
    questions_only = (out / "试卷包/生成试卷/paper-题卷.html").read_text()
    assert "VIEW_QUESTION" in questions_only
    assert "VIEW_SECRET" not in questions_only and "practice-media-data.js" not in questions_only
    exported = json.loads((out / "paper-catalog.json").read_text())
    assert len(exported) == 1 and validate_review_files(exported, out)
    assert json.loads((out / "catalog.json").read_text())[0]["id"] == parent["id"]


def test_full_only_view_cannot_enter_basic_paper(tmp_path):
    _, _, catalog, views, papers, reader = inputs(tmp_path, basic=False)
    rows = json.loads(papers.read_text()); rows[0]["edition"] = "basic"; papers.write_text(json.dumps(rows))
    with pytest.raises(ValueError, match="basic|edition"):
        build(catalog, papers, reader, tmp_path / "out", paper_catalog=views)
    assert not (tmp_path / "out").exists()


def test_reviewed_view_cannot_silently_inflate_the_main_catalog(tmp_path):
    from tools.build_practice_delivery import export_reviewed_catalog
    parent, view, catalog, views, papers, reader = inputs(tmp_path)
    view['anchor_text'] = '正文锚点'
    export_reviewed_catalog([view], views.parent, catalog.parent, 'view-export.json')
    catalog.write_text(json.dumps([parent, view]))
    with pytest.raises(ValueError, match='paper-only.*main'):
        build(catalog, papers, reader, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


def test_parent_and_its_view_cannot_share_a_paper(tmp_path):
    _, _, catalog, views, papers, reader = inputs(tmp_path)
    rows = json.loads(papers.read_text()); rows[0]["items"].append({"question_id": "PARENT", "points": 10})
    papers.write_text(json.dumps(rows))
    with pytest.raises(ValueError, match="parent.*view|view.*parent"):
        build(catalog, papers, reader, tmp_path / "out", paper_catalog=views)
    assert not (tmp_path / "out").exists()


def test_view_parent_hash_must_bind_current_main_catalog(tmp_path):
    _, view, catalog, views, papers, reader = inputs(tmp_path)
    view["parent_content_sha256"] = "a" * 64
    views.write_text(json.dumps([view]))
    with pytest.raises(ValueError, match="parent"):
        build(catalog, papers, reader, tmp_path / "out", paper_catalog=views)


def test_view_review_evidence_is_required_before_output(tmp_path):
    _, view, catalog, views, papers, reader = inputs(tmp_path)
    (views.parent / view["review"]["evidence_files"][0]["path"]).write_text("changed")
    with pytest.raises(ValueError, match="evidence"):
        build(catalog, papers, reader, tmp_path / "out", paper_catalog=views)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize('stale_field', ['paper', 'selection'])
def test_builder_rejects_explicit_stale_hash_for_fully_reviewed_view(tmp_path, stale_field):
    _, view, catalog, views, papers, reader = inputs(tmp_path)
    rows = json.loads(papers.read_text()); p = rows[0]
    p.update(kind='pressure', obstacle='先检查条件', question_hashes={view['id']:view['review']['content_sha256']})
    p['items'][0]['slot_id'] = 's1'
    p['task_slots'] = [{'id':'s1','question_ids':[view['id']], 'selection_evidence':{
        'question_ids':[view['id']], 'question_hashes':{view['id']:view['review']['content_sha256']}}}]
    target = p if stale_field == 'paper' else p['task_slots'][0]['selection_evidence']
    target['question_hashes'][view['id']] = '0' * 64
    papers.write_text(json.dumps(rows))
    with pytest.raises(ValueError, match='hash'):
        build(catalog, papers, reader, tmp_path/'out', paper_catalog=views)
    assert not (tmp_path/'out').exists()


def test_student_publisher_does_not_refresh_explicit_stale_paper_hashes(tmp_path):
    _, _, catalog, views, papers, reader = inputs(tmp_path)
    out = tmp_path/'out'; build(catalog,papers,reader,out,paper_catalog=views)
    (out/'三维图解.html').write_text('<html><body>Fixture</body></html>')
    saved = json.loads((out/'papers.json').read_text()); saved[0]['question_hashes']['VIEW-1'] = '0' * 64
    (out/'papers.json').write_text(json.dumps(saved))
    files = [{'path':p.relative_to(out).as_posix(),'bytes':p.stat().st_size,
              'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in out.rglob('*') if p.is_file()]
    (out/'文件校验清单.json').write_text(json.dumps({'files':files}))
    with pytest.raises(ValueError, match='hash'):
        publish_student(out,tmp_path/'student')
    assert not (tmp_path/'student').exists()


def excerpt_view(parent, view, catalog, views):
    """An actual parent B fixture, with an auditable host excerpt record."""
    raw_ref = parent['review']['evidence_files'][0]
    raw = (catalog.parent / raw_ref['path']).read_bytes()
    original = json.loads(raw)[0]
    raw_path = views.parent / 'evidence' / (raw_ref['sha256'] + '.json')
    raw_path.parent.mkdir(); raw_path.write_bytes(raw)
    excerpts = []
    for field in ('answer', 'solution'):
        value = original[field]
        excerpts.append({'json_pointer': '/0/' + field, 'char_range_0_based_half_open': [0, len(value)],
                         'text': value, 'excerpt_sha256': hashlib.sha256(value.encode()).hexdigest()})
    record = {'id': view['id'], 'status': 'solved', 'answer': original['answer'], 'solution': original['solution'],
              'issues': [], 'engine': 'host_excerpt_of_actual_independent_solution',
              'provenance': {'source_parent_question_id': parent['id'],
                             'parent_content_sha256': parent['review']['content_sha256'],
                             'parent_blind_solution_sha256': parent['review']['blind_solution_sha256'],
                             'parent_blind_solver': parent['review']['blind_solver'],
                             'parent_blind_row_pointer': '/0',
                             'raw_evidence': {'path': 'evidence/' + raw_path.name, 'sha256': raw_ref['sha256']},
                             'excerpts': excerpts, 'new_model_call': False}}
    blind_path = views.parent / view['review']['evidence_files'][0]['path']
    blind_path.write_text(json.dumps({'questions': [record]}))
    view['review']['blind_solution_sha256'] = hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    view['review']['evidence_files'][0]['sha256'] = hashlib.sha256(blind_path.read_bytes()).hexdigest()
    view['review']['evidence_files'].append({'path': 'evidence/' + raw_path.name, 'sha256': raw_ref['sha256']})
    views.write_text(json.dumps([view]))
    return record, blind_path


def test_actual_independent_excerpt_provenance_survives_export(tmp_path):
    parent, view, catalog, views, papers, reader = inputs(tmp_path)
    excerpt_view(parent, view, catalog, views)
    out = tmp_path / 'out'
    build(catalog, papers, reader, out, paper_catalog=views)
    assert len(list((out / 'evidence').glob('*.json'))) >= 4
    from tools.build_practice_delivery import validate_paper_view_excerpts
    validate_paper_view_excerpts(json.loads((out / 'catalog.json').read_text()),
                                json.loads((out / 'paper-catalog.json').read_text()), out)


def test_export_resolves_historical_raw_B_filename_by_verified_sha(tmp_path):
    parent, view, catalog, views, papers, reader = inputs(tmp_path)
    record, blind_path = excerpt_view(parent, view, catalog, views)
    raw_ref = record['provenance']['raw_evidence']
    historical = 'evidence/original-independent-B.json'
    (views.parent/raw_ref['path']).rename(views.parent/historical)
    for ref in view['review']['evidence_files']:
        if ref['sha256'] == raw_ref['sha256']:ref['path'] = historical
    raw_ref['path'] = historical
    blind_path.write_text(json.dumps({'questions':[record]}))
    raw_excerpt_bytes = blind_path.read_bytes()
    view['review']['blind_solution_sha256'] = hashlib.sha256(json.dumps(record,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    view['review']['evidence_files'][0]['sha256'] = hashlib.sha256(raw_excerpt_bytes).hexdigest()
    views.write_text(json.dumps([view]))
    out = tmp_path/'out'; build(catalog,papers,reader,out,paper_catalog=views)
    exported = json.loads((out/'paper-catalog.json').read_text())[0]
    assert not (out/historical).exists()
    copied = next(ref for ref in exported['review']['evidence_files']
                  if ref['sha256'] == hashlib.sha256(raw_excerpt_bytes).hexdigest())
    assert (out/copied['path']).read_bytes() == raw_excerpt_bytes
    from tools.build_practice_delivery import validate_paper_view_excerpts
    validate_paper_view_excerpts(json.loads((out/'catalog.json').read_text()),[exported],out)


def test_rehashed_excerpt_cannot_claim_text_absent_from_actual_parent_B(tmp_path):
    parent, view, catalog, views, papers, reader = inputs(tmp_path)
    record, blind_path = excerpt_view(parent, view, catalog, views)
    record['provenance']['excerpts'][0]['text'] = 'invented excerpt'
    blind_path.write_text(json.dumps({'questions': [record]}))
    view['review']['blind_solution_sha256'] = hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    view['review']['evidence_files'][0]['sha256'] = hashlib.sha256(blind_path.read_bytes()).hexdigest()
    views.write_text(json.dumps([view]))
    with pytest.raises(ValueError, match='excerpt'):
        build(catalog, papers, reader, tmp_path / 'out', paper_catalog=views)
    assert not (tmp_path / 'out').exists()


def test_custom_view_requires_explicit_include_and_stays_out_of_workbook(tmp_path):
    _, _, catalog, views, papers, reader = inputs(tmp_path)
    rows = json.loads(papers.read_text()); rows[0]["edition"] = "custom"; papers.write_text(json.dumps(rows))
    profile = tmp_path / "profile.json"; profile.write_text("{}")
    with pytest.raises(ValueError, match="edition"):
        build(catalog, papers, reader, tmp_path / "missing", paper_catalog=views, profile=profile)
    profile.write_text(json.dumps({"include_ids": ["VIEW-1"]}))
    out = tmp_path / "explicit"
    build(catalog, papers, reader, out, paper_catalog=views, profile=profile)
    assert set(page_questions(out / "定制版-习题集.html")) == {"PARENT"}
    assert set(page_questions(out / "试卷包/生成试卷/paper-题卷.html")) == {"VIEW-1"}


def test_custom_can_explicitly_select_only_a_paper_view(tmp_path):
    _, _, catalog, views, papers, reader = inputs(tmp_path)
    rows = json.loads(papers.read_text()); rows[0]['edition'] = 'custom'; papers.write_text(json.dumps(rows))
    profile = tmp_path / 'profile.json'
    profile.write_text(json.dumps({'include_ids': ['VIEW-1'], 'exclude_ids': ['PARENT']}))
    out = tmp_path / 'out'
    report = build(catalog, papers, reader, out, paper_catalog=views, profile=profile)
    assert report['editions']['custom']['questions'] == 0
    assert page_questions(out / '定制版-习题集.html') == {}
    assert set(page_questions(out / '试卷包/生成试卷/paper-题卷.html')) == {'VIEW-1'}


def test_moved_delivery_rebuild_discovers_saved_paper_catalog(tmp_path):
    _, _, catalog, views, papers, reader = inputs(tmp_path)
    out = tmp_path / "out"; build(catalog, papers, reader, out, paper_catalog=views)
    moved = tmp_path / "moved"; out.rename(moved)
    shutil.rmtree(catalog.parent); shutil.rmtree(views.parent); shutil.rmtree(reader)
    rebuilt = tmp_path / "rebuilt"
    run = subprocess.run([sys.executable, "tools/rebuild_practice_delivery.py", "--delivery", str(moved),
                          "--out", str(rebuilt)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert set(page_questions(rebuilt / "完整版-习题集.html")) == {"PARENT"}
    assert set(page_questions(rebuilt / "试卷包/生成试卷/paper-解析.html")) == {"VIEW-1"}
    assert validate_review_files(json.loads((rebuilt / "paper-catalog.json").read_text()), rebuilt)


def test_configured_release_date_is_reported_and_preserved_on_rebuild(tmp_path):
    _, _, catalog, views, papers, reader = inputs(tmp_path)
    out = tmp_path / 'dated'
    report = build(catalog, papers, reader, out, paper_catalog=views, release_date='2026-11-22')
    assert report['release_date'] == '2026-11-22'
    # The final entry template is date-free; the saved release report owns the
    # date and rebuilds must preserve it rather than substituting today's date.
    assert '2026-11-20 · 本地离线阅读' not in (out / 'index.html').read_text()
    rebuilt = tmp_path / 'rebuilt'
    run = subprocess.run([sys.executable, 'tools/rebuild_practice_delivery.py', '--delivery', str(out),
                          '--out', str(rebuilt)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert json.loads((rebuilt / 'build-report.json').read_text())['release_date'] == '2026-11-22'


def test_undated_build_does_not_invent_today_as_its_release_date(tmp_path):
    _, _, catalog, views, papers, reader = inputs(tmp_path)
    out = tmp_path / 'undated'
    report = build(catalog, papers, reader, out, paper_catalog=views)
    assert report['release_date'] is None
    assert '2026-11-20 · 本地离线阅读' not in (out / 'index.html').read_text()


def test_student_release_keeps_view_paper_and_excludes_private_catalog(tmp_path):
    _, _, catalog, views, papers, reader = inputs(tmp_path)
    out = tmp_path / "out"; build(catalog, papers, reader, out, paper_catalog=views)
    (out / "三维图解.html").write_text("<html><body>Fixture</body></html>")
    files = [{"path": p.relative_to(out).as_posix(), "bytes": p.stat().st_size,
              "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in out.rglob("*") if p.is_file()]
    (out / "文件校验清单.json").write_text(json.dumps({"files": files}))
    public = tmp_path / "student"; report = publish_student(out, public)
    assert report["questions"] == 1 and report["paper_views"] == 1
    course = public / "复变函数A"
    assert set(page_questions(course / "试卷包/生成试卷/paper-解析.html")) == {"VIEW-1"}
    assert not (course / "paper-catalog.json").exists() and not (course / "evidence").exists()
    assert "VIEW_SECRET" not in (course / "试卷包/生成试卷/paper-题卷.html").read_text()
