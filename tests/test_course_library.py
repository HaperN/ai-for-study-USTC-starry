"""Course sources stay portable, verified and attached to real reader snapshots."""
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from aiforstudy.core.course_library import import_materials, load_library, project_library

FIXTURES = Path(__file__).parent / 'fixtures/course_library'


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


@pytest.fixture
def materials(tmp_path):
    import pymupdf
    root = tmp_path / 'input'
    exams = json.loads((FIXTURES / 'exams.json').read_text())
    book = json.loads((FIXTURES / 'textbook.json').read_text())
    for row, relative, count in [(exams['files'][0], 'ustc-exams/' + exams['files'][0]['local_path'], 2),
                                  (book['source'], 'textbook/textbook.pdf', 239)]:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with pymupdf.open() as pdf:
            for _ in range(count):
                pdf.new_page()
            pdf.save(path)
        row['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    save(root / 'ustc-exams/manifest.json', exams)
    save(root / 'textbook/homework-index.json', book)
    return root


def test_import_and_reader_projection_are_portable(materials, tmp_path):
    bundle = tmp_path / 'parse/course-library'
    report = import_materials(materials, bundle)
    assert report['checks']['actual_pdf_page_counts_checked'] == 2
    assert report['checks']['locator_ranges_checked'] == 30
    shutil.rmtree(materials)
    moved = tmp_path / 'moved'
    shutil.move(bundle, moved)
    catalog, sources = load_library(moved)
    assert len(sources) == 2 and catalog['summary']['textbook_top_level_questions'] == 129
    data = {}
    paths = project_library(data, moved)
    from aiforstudy.blocks.render.source_materials import copy_source_assets, source_asset_names
    copy_source_assets(paths, source_asset_names(paths), tmp_path / 'reader')
    for row in data['course_library']['sources']:
        assert (tmp_path / 'reader' / row['url']).is_file()
    assert str(materials) not in json.dumps(data['course_library'])


@pytest.mark.parametrize('damage', ['traversal', 'page_count', 'range', 'hash', 'duplicate', 'assignment', 'schema', 'version'])
def test_bad_import_never_replaces_valid_bundle(materials, tmp_path, damage):
    bundle = tmp_path / 'bundle'
    import_materials(materials, bundle)
    before = {str(p.relative_to(bundle)): p.read_bytes() for p in bundle.rglob('*') if p.is_file()}
    path = materials / 'ustc-exams/manifest.json'
    exams = json.loads(path.read_text())
    if damage == 'traversal': exams['files'][0]['local_path'] = '../../escape.pdf'
    if damage == 'page_count': exams['files'][0]['pages'] = 200
    if damage == 'range': exams['exam_sessions'][0]['pdf_pages'] = [0, 500]
    if damage == 'hash': exams['files'][0]['sha256'] = '0' * 64
    if damage == 'duplicate': exams['exam_sessions'].append(exams['exam_sessions'][0])
    if damage == 'schema': exams['files'] = None
    if damage == 'version': exams['manifest_version'] = '999'
    save(path, exams)
    if damage == 'assignment':
        p = materials / 'textbook/homework-index.json'
        book = json.loads(p.read_text()); book['assignment_selected'] = 'yes'; save(p, book)
    with pytest.raises(ValueError):
        import_materials(materials, bundle)
    assert before == {str(p.relative_to(bundle)): p.read_bytes() for p in bundle.rglob('*') if p.is_file()}


def test_tampered_bundle_is_not_projected(materials, tmp_path):
    bundle = tmp_path / 'bundle'; import_materials(materials, bundle)
    catalog, sources = load_library(bundle)
    next(iter(sources.values())).write_bytes(b'%PDF-corrupt')
    with pytest.raises(ValueError, match='HASH'):
        project_library({}, bundle)


def test_source_projection_and_preparation_copy_keep_course_library(materials, tmp_path):
    from aiforstudy.blocks.render.source_materials import project_source_materials
    from aiforstudy.pipeline.preparation import _mutable, source_fingerprint
    from dataclasses import make_dataclass
    from types import SimpleNamespace as NS
    parse = tmp_path / 'parse'; import_materials(materials, parse / 'course-library')
    Context = make_dataclass('Context', 'parse workspace book_id revision sources'.split())
    context = Context(parse, tmp_path, 'book', '1', [])
    before = source_fingerprint(context)
    work = tmp_path / 'run/work'; work.mkdir(parents=True)
    services = NS(store=NS(work=work), registry=NS(resolve=lambda _: context))
    ctx = NS(context=context, services=services, request=NS(prepare_policy=NS(value='ensure')))
    copied = _mutable(ctx)
    assert copied != parse and source_fingerprint(context) == before
    shutil.rmtree(parse)
    data = {'tree': [], 'units': {}, 'counts': {}}
    paths = project_source_materials(data, copied)
    assert len(paths) == 2 and len(data['course_library']['exam_sessions']) == 1
    assert all(p.is_relative_to(copied) for p in paths.values())


def test_wrong_supplied_index_does_not_advance_pointer(materials, tmp_path):
    bundle = tmp_path / 'bundle'; import_materials(materials, bundle)
    before = (bundle / 'current.json').read_bytes()
    catalog, _ = load_library(bundle)
    catalog['exam_sessions'][0]['course'] = '伪造课程'
    index = tmp_path / 'index.json'; save(index, catalog)
    with pytest.raises(ValueError, match='ORIGIN_RECORD_MISMATCH'):
        import_materials(materials, bundle, index_path=index)
    assert (bundle / 'current.json').read_bytes() == before


@pytest.mark.parametrize('external', [False, True])
def test_real_reader_snapshot_copies_pdfs_and_tracks_library_identity(materials, tmp_path, external):
    from support.content_case import teaching_case
    from aiforstudy.blocks.render.diagnostics import build_reader_snapshot
    from aiforstudy.blocks.content.content_contracts import parse_content
    from aiforstudy.core.digest import digest_file, digest_of
    content = parse_content(teaching_case()[0]).as_dict()
    run = tmp_path / 'run-test'
    save(run / 'state.json', {'book_id':'B', 'run_id':'run-test'})
    save(run / 'work/products/lesson/U.json', {'payload':{
        'content':content, 'content_hash':digest_of(content)}})
    save(run / 'work/products/media_plan/U.json', {'payload':{
        'content_hash':digest_of(content), 'requests':[], 'decisions':[]}})
    parse = tmp_path / 'external-parse' if external else run / 'work/prepared-parse'
    import_materials(materials, parse / 'course-library')
    report = build_reader_snapshot(run, unit_ids=['U'], out_dir=tmp_path / 'reader', parse_root=parse)
    output = Path(report['output_dir'])
    data = json.loads((output / 'data/data.js').read_text().split('window.__DATA__ = ', 1)[1].rstrip().removesuffix(';'))
    assert len(data['course_library']['sources']) == 2
    assert 'course-library-open' in (output / 'index.html').read_text()
    for source in data['course_library']['sources']:
        assert digest_file(output / source['url']) == 'sha256:' + source['sha256']
    assert any('course-library/current.json' in key for key in report['source_hashes'])
    assert any(key.endswith('catalog.json') for key in report['source_hashes'])
    assert (output / 'assets/course-library.js').is_file()
    assert (output / 'assets/course-library.css').is_file()
    from aiforstudy.blocks.render.snapshot_sources import validate_snapshot_sources
    assert validate_snapshot_sources(report, run, parse)


def test_existing_bundle_validates_all_imported_pdf_pages():
    from aiforstudy.core.course_library import _validated_catalog
    bundle = Path(__file__).resolve().parents[1] / 'data/parse/complex-analysis-v2/course-library'
    if not bundle.exists():
        pytest.skip('The optional complex-analysis corpus is not installed')
    pointer = json.loads((bundle / 'current.json').read_text())
    path = bundle / pointer['catalog']
    catalog, report = _validated_catalog(path.parent, json.loads(path.read_text()), check_pdf=True)
    assert report['checks']['actual_pdf_page_counts_checked'] == 40
    assert report['checks']['locator_ranges_checked'] == 133
    assert catalog['summary']['exam_sessions_by_course'] == {'复变函数A':7,'复变函数B':6,'复分析':38}


def test_wholebook_rejects_explicit_bad_library_before_touching_delivery(tmp_path):
    from tools.build_wholebook_delivery import build
    output = tmp_path / 'new-delivery'
    with pytest.raises(ValueError, match='Invalid course library bundle'):
        build(tmp_path / 'source', tmp_path / 'old-delivery', output,
              course_library=tmp_path / 'missing-library')
    assert not output.exists()


def test_normal_render_and_delivery_include_course_library_without_model_calls(materials, tmp_path):
    from dataclasses import replace
    from support.runner_v5 import run_book
    from aiforstudy.core.jsonio import read_json
    from aiforstudy.pipeline.graph import Node
    from aiforstudy.pipeline.services import StageContext
    from aiforstudy.pipeline.media_stages import render_stage
    from aiforstudy.pipeline.delivery_stage import delivery_stage, validate_reader_delivery
    result, store, engine, transport = run_book(tmp_path, until='interaction')
    assert result.status == 'succeeded', result.recovery_hint
    parse = tmp_path / 'reused-parse'
    shutil.copytree(engine.services.context.parse, parse)
    import_materials(materials, parse / 'course-library')
    context = replace(engine.services.context, parse=parse)
    services = replace(engine.services, context=context)
    ctx = StageContext(node=Node('render:CH01','render','CH01'), request=engine.request,
        plan=engine.plan, workspace=engine.services.workspace, context=context, services=services)
    calls = len(transport.calls)
    rendered = render_stage(ctx)
    assert rendered.execution_state.value == 'succeeded', rendered.error
    payload = read_json(store.work / 'products/render/CH01.json')['payload']
    output = Path(payload['out_dir'])
    data = json.loads((output / 'data/data.js').read_text().split('window.__DATA__ = ', 1)[1].rstrip().removesuffix(';'))
    assert len(data['course_library']['sources']) == 2
    assert all((output / s['url']).is_file() for s in data['course_library']['sources'])
    assert validate_reader_delivery(ctx)
    delivered = delivery_stage(replace(ctx, node=Node('delivery','delivery','book')))
    assert delivered.execution_state.value == 'succeeded', delivered.error
    assert len(transport.calls) == calls


@pytest.mark.parametrize('damage', [
    'past_pdf', 'outside_chapter', 'float_page', 'bool_page', 'duplicate_page',
    'missing_question', 'duplicate_question', 'string_question', 'bool_question',
    'outside_question_range', 'invalid_continuation', 'future_continuation',
    'duplicate_continuation',
])
def test_invalid_numbered_question_links_preserve_valid_snapshot(materials, tmp_path, damage):
    bundle = tmp_path / 'bundle'
    import_materials(materials, bundle)
    before = {str(p.relative_to(bundle)): p.read_bytes() for p in bundle.rglob('*') if p.is_file()}
    path = materials / 'textbook/homework-index.json'
    book = json.loads(path.read_text())
    pages = book['chapters'][0]['pages']
    if damage == 'past_pdf': pages[0]['pdf_page'] = 999
    if damage == 'outside_chapter': pages[0]['pdf_page'] = 25
    if damage == 'float_page': pages[0]['pdf_page'] = 26.0
    if damage == 'bool_page': pages[0]['pdf_page'] = True
    if damage == 'duplicate_page': pages[1]['pdf_page'] = pages[0]['pdf_page']
    if damage == 'missing_question': pages[0]['new_question_numbers'].pop()
    if damage == 'duplicate_question': pages[1]['new_question_numbers'].append(1)
    if damage == 'string_question': pages[0]['new_question_numbers'][0] = '1'
    if damage == 'bool_question': pages[0]['new_question_numbers'][0] = True
    if damage == 'outside_question_range': pages[0]['new_question_numbers'][0] = 21
    if damage == 'invalid_continuation': pages[1]['continued_question_numbers'] = [999]
    if damage == 'future_continuation': pages[0]['continued_question_numbers'] = [20]
    if damage == 'duplicate_continuation': pages[1]['continued_question_numbers'] = [9, 9]
    save(path, book)
    with pytest.raises(ValueError, match='TEXTBOOK_QUESTION_INDEX_INVALID'):
        import_materials(materials, bundle)
    assert before == {str(p.relative_to(bundle)): p.read_bytes() for p in bundle.rglob('*') if p.is_file()}


@pytest.mark.parametrize('damage', [
    'course', 'course_track', 'solutions_from_questions', 'source_question_bounds',
    'source_answer_bounds', 'session_question_bounds', 'session_answer_bounds',
    'full_paper_flag', 'answer_flag', 'duplicate_file', 'missing_both_roles',
])
def test_exam_session_must_agree_with_its_source_file(materials, tmp_path, damage):
    bundle = tmp_path / 'bundle'
    import_materials(materials, bundle)
    before = {str(p.relative_to(bundle)): p.read_bytes() for p in bundle.rglob('*') if p.is_file()}
    path = materials / 'ustc-exams/manifest.json'
    exams = json.loads(path.read_text())
    file, session = exams['files'][0], exams['exam_sessions'][0]
    if damage == 'course': session['course'] = '复变函数A'
    if damage == 'course_track': session['course_track'] = '非数学专业通修'
    if damage == 'solutions_from_questions':
        session.update(course='复变函数A', question_pages=[], answer_pages=[1, 2],
                       has_answer=True, complete_question_paper_present=False)
    if damage == 'source_question_bounds': file['question_pages'] = [1, 1]
    if damage == 'source_answer_bounds':
        file['answer_pages'] = [2, 2]
        session.update(answer_pages=[1, 2], has_answer=True)
    if damage == 'session_question_bounds': session['pdf_pages'] = [1, 1]
    if damage == 'session_answer_bounds':
        file['answer_pages'] = [1, 2]
        session.update(pdf_pages=[1, 1], question_pages=[1, 1], answer_pages=[1, 2], has_answer=True)
    if damage == 'full_paper_flag': session['complete_question_paper_present'] = False
    if damage == 'answer_flag': session['has_answer'] = True
    if damage == 'duplicate_file': exams['files'].append(dict(file, course='复变函数A'))
    if damage == 'missing_both_roles':
        session.update(question_pages=[], answer_pages=[], has_answer=False,
                       complete_question_paper_present=False)
    save(path, exams)
    with pytest.raises(ValueError, match='EXAM_SOURCE_MISMATCH'):
        import_materials(materials, bundle)
    assert before == {str(p.relative_to(bundle)): p.read_bytes() for p in bundle.rglob('*') if p.is_file()}


def test_collection_members_and_cross_page_continuations_remain_valid(materials, tmp_path):
    from copy import deepcopy
    path = materials / 'ustc-exams/manifest.json'
    exams = json.loads(path.read_text())
    file, session = exams['files'][0], exams['exam_sessions'][0]
    file.update(course='复变函数A、B', course_track='非数学专业通修')
    session.update(course='复变函数A', course_track='非数学专业通修', pdf_pages=[1, 1], question_pages=[1, 1])
    another = deepcopy(session)
    another.update(id='collection-B', course='复变函数B', pdf_pages=[2, 2], question_pages=[2, 2])
    exams['exam_sessions'].append(another)
    save(path, exams)
    output = tmp_path / 'bundle'
    import_materials(materials, output)
    catalog, _ = load_library(output)
    assert catalog['summary']['exam_sessions_by_course'] == {'复变函数A':1, '复变函数B':1}
    chapter = catalog['textbook_pool']['chapters'][0]
    assert chapter['pages'][1]['continued_question_numbers'] == [9]
    assert chapter['pages'][2]['continued_question_numbers'] == [17]
    assert sum(len(p['new_question_numbers']) for c in catalog['textbook_pool']['chapters'] for p in c['pages']) == 129
