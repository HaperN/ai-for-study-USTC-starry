"""Portable course evidence import and reader projection.

The normalizer preserves verified source metadata; it never infers dates,
assignments, question types or correctness. Bundles contain their own PDFs and
original manifests. Only a fully validated snapshot updates current.json.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

INPUTS = ['ustc-exams/manifest.json', 'textbook/homework-index.json']


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def local_file(root, relative):
    """Confine all imported local links to the material directory."""
    if not isinstance(relative, str) or '\\' in relative:
        raise ValueError('Local path must be a POSIX relative path')
    if not relative or any(c in relative for c in ('#', '?', '%', ':', '\x00')):
        raise ValueError('Unsafe local path')
    p = PurePosixPath(relative)
    if p.is_absolute() or '..' in p.parts or not p.parts:
        raise ValueError('Unsafe local path')
    result = (root / p).resolve()
    result.relative_to(root.resolve())
    return result


def load_inputs(materials):
    materials = Path(materials).resolve()
    return [json.loads(local_file(materials, name).read_text(encoding='utf-8')) for name in INPUTS]


def _integer_range(values):
    return (isinstance(values, list) and len(values) == 2
            and all(type(value) is int for value in values)
            and 1 <= values[0] <= values[1])


def _page_range(pages, count):
    return _integer_range(pages) and type(count) is int and pages[1] <= count


def _validate_exam_sources(exams):
    """Bind each session to its declared file, not its own copied origin fields."""
    files = {}
    for source in exams['files']:
        name = source['local_path']
        if name in files:
            raise ValueError('EXAM_SOURCE_MISMATCH: duplicate file ' + name)
        files[name] = source
        for role in ('question_pages', 'answer_pages'):
            pages = source.get(role) or []
            if pages and not _page_range(pages, source['pages']):
                raise ValueError('EXAM_SOURCE_MISMATCH: invalid source ' + role + ': ' + name)
    for session in exams['exam_sessions']:
        source = files.get(session['file'])
        identity = str(session['id'])
        if source is None:
            raise ValueError('EXAM_SOURCE_MISMATCH: missing file for ' + identity)
        # The verified general-course collection contains two separately named
        # courses. An A/B suffix on any other file does not establish membership.
        courses = ({'复变函数A', '复变函数B'} if source['course'] == '复变函数A、B'
                   else {source['course']})
        if (session['course'] not in courses
                or session['course_track'] != source['course_track']):
            raise ValueError('EXAM_SOURCE_MISMATCH: course or track for ' + identity)
        full = session['pdf_pages']
        if not _page_range(full, source['pages']):
            raise ValueError('EXAM_SOURCE_MISMATCH: full page range for ' + identity)
        for role in ('question_pages', 'answer_pages'):
            pages = session[role]
            source_pages = source.get(role) or []
            if not isinstance(pages, list) or (pages and (
                    not _page_range(pages, source['pages']) or not source_pages
                    or not source_pages[0] <= pages[0] <= pages[1] <= source_pages[1]
                    or not full[0] <= pages[0] <= pages[1] <= full[1])):
                raise ValueError('EXAM_SOURCE_MISMATCH: ' + role + ' for ' + identity)
        if (not (session['question_pages'] or session['answer_pages'])
                or session['has_answer'] is not bool(session['answer_pages'])
                or session['complete_question_paper_present'] is not bool(session['question_pages'])):
            raise ValueError('EXAM_SOURCE_MISMATCH: question/answer availability for ' + identity)


def _validate_question_pages(book):
    """Verify the per-question links the UI uses, including cross-page continuations."""
    count = book['source']['pdf_page_count']
    for chapter in book['chapters']:
        identity = str(chapter['chapter'])
        error = 'TEXTBOOK_QUESTION_INDEX_INVALID: chapter ' + identity
        bounds = chapter['exercise_pdf_pages']
        numbers = chapter['top_level_question_range']
        total = chapter['top_level_question_count']
        if (not _page_range(bounds, count) or not _integer_range(numbers)
                or type(total) is not int or total != numbers[1] - numbers[0] + 1):
            raise ValueError(error + ': exercise range')
        rows = chapter['pages']
        if not isinstance(rows, list) or not rows:
            raise ValueError(error + ': missing question pages')
        pages = set()
        starts = {}
        for row in rows:
            page = row['pdf_page']
            if type(page) is not int or not bounds[0] <= page <= bounds[1] or page in pages:
                raise ValueError(error + ': PDF page must be unique and inside this chapter exercise range')
            pages.add(page)
            for field in ('new_question_numbers', 'continued_question_numbers'):
                values = row[field]
                if (not isinstance(values, list)
                        or any(type(n) is not int or not numbers[0] <= n <= numbers[1] for n in values)
                        or len(set(values)) != len(values)):
                    raise ValueError(error + ': invalid ' + field)
            for number in row['new_question_numbers']:
                if number in starts:
                    raise ValueError(error + ': duplicate question start')
                starts[number] = page
        # Starts are already unique and inside the declared integer range;
        # matching its size proves complete coverage without allocating it.
        if len(starts) != total:
            raise ValueError(error + ': every top-level question needs exactly one start')
        for row in rows:
            if any(starts[number] >= row['pdf_page'] for number in row['continued_question_numbers']):
                raise ValueError(error + ': continued question must start on an earlier page')


def build_catalog(materials):
    materials = Path(materials).resolve()
    exams, book = load_inputs(materials)
    if exams.get('manifest_version') != '1.0' or book.get('schema_version') != '1.0':
        raise ValueError('Unsupported course input manifest version')
    _validate_exam_sources(exams)
    _validate_question_pages(book)
    inputs = [{'local_path': name, 'sha256': sha256(materials / name)} for name in INPUTS]
    catalog = {
        'schema_version': '1.0',
        'artifact_type': 'CourseMaterialLibraryV1',
        'snapshot_id': hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest(),
        'input_manifests': inputs,
        'page_number_convention': '1-based physical PDF pages; ranges include both endpoints',
        'available_at_policy': 'null means unknown; retrieval time, academic year and printed year are not availability evidence',
        'limitations': [
            '本目录复用已核验元数据，不声称已自动理解题目、完成OCR或验算解答。',
            '所有历史可得时间 available_at 均未知；不能据抓取时间倒推考试前可见。',
            '通修A、通修B、数学专业复分析分别统计；网页同一行教师不等于每场授课教师。',
            '教材课后习题是题源池，实际布置题号未知；参考结果/提示不等于完整解答。',
            '没有课件；原始课堂视频尚未提供。目录不代表题目已被自动理解。',
        ],
        'sources': [], 'locators': [], 'exam_sessions': [],
    }
    by_hash = {}
    by_exam_path = {}

    def add_source(sha, relative, page_count, roles, provenance):
        if sha not in by_hash:
            item = {'id': 'sha256:' + sha, 'sha256': sha,
                    'local_paths': [], 'pdf_page_count': page_count,
                    'roles': [], 'available_at': None,
                    'available_at_status': 'unknown', 'provenance': []}
            by_hash[sha] = item
            catalog['sources'].append(item)
        item = by_hash[sha]
        if item['pdf_page_count'] != page_count:
            raise ValueError('Identical hashes have conflicting page counts')
        if relative not in item['local_paths']:
            item['local_paths'].append(relative)
        item['roles'] = sorted(set(item['roles']) | set(roles))
        if provenance not in item['provenance']:
            item['provenance'].append(provenance)
        return item

    def locator(identifier, source, pages, role, **extra):
        if not pages:
            return None
        result = {'id': identifier, 'source_id': source['id'],
                  'source_sha256': source['sha256'], 'pdf_pages': list(pages),
                  'role': role, **extra}
        catalog['locators'].append(result)
        return identifier

    for f in exams['files']:
        roles = []
        if f.get('question_pages'):
            roles.append('question_paper')
        if f.get('answer_pages'):
            roles.append('solutions')
        provenance = {key: f.get(key) for key in (
            'source_url', 'source_page', 'resolved_url', 'retrieved_at_utc',
            'original_filename', 'course', 'course_track', 'class_label',
            'class_evidence', 'teacher_specific', 'teachers_row_as_listed',
            'role_from_page', 'role_verified', 'answer_status', 'answer_note',
            'answer_correctness_verified', 'content_quality_notes')}
        provenance['source_kind'] = exams['source_kind']
        source = add_source(f['sha256'], 'ustc-exams/' + f['local_path'],
                            f['pages'], roles, provenance)
        by_exam_path[f['local_path']] = source

    for s in exams['exam_sessions']:
        source = by_exam_path[s['file']]
        item = {k: s.get(k) for k in (
            'id', 'course', 'course_track', 'academic_year', 'year_as_listed',
            'semester', 'exam_type', 'makeup', 'class_label', 'teacher_specific',
            'teacher_note', 'teachers_row_as_listed', 'source_url', 'source_page',
            'has_answer', 'answer_status', 'complete_question_paper_present')}
        item.update({
            'source_id': source['id'], 'available_at': None,
            'exam_date': None, 'exam_date_evidence': None,
            'known_defective_items': s.get('known_defective_items', []),
            'content_quality_notes': s.get('content_quality_notes', []),
            'origin': {k: s.get(k) for k in (
                'question_pages', 'answer_pages', 'complete_question_paper_present',
                'academic_year', 'year_as_listed')},
            'full_locator_id': locator(s['id'] + ':full', source, s['pdf_pages'], 'exam_material'),
            'question_locator_id': locator(s['id'] + ':questions', source, s['question_pages'], 'question_paper'),
            'answer_locator_id': locator(s['id'] + ':answers', source, s['answer_pages'], 'solutions'),
        })
        catalog['exam_sessions'].append(item)

    raw_source = book['source']
    book_source = add_source(raw_source['sha256'], 'textbook/' + raw_source['local_copy_relative_path'],
        raw_source['pdf_page_count'], ['textbook', 'exercise_pool', 'reference_answers'], {
            'source_url': None, 'source_url_status': 'user_supplied_local_pdf_no_public_url',
            'source_kind': '用户提供教材；本地副本与项目原件及用户文件哈希一致',
            'bibliography': book['bibliography'],
            'original_filename': '复变函数.pdf', 'retrieved_at_utc': None,
        })
    pool = {
        'source_id': book_source['id'], 'pool_type': book['pool_type'],
        'assignment_selected': book['assignment_selected'],
        'assigned_question_count': None, 'full_solution_count': None,
        'bibliography': book['bibliography'], 'summary': book['summary'],
        'course_scope': book['course_scope'], 'chapters': [],
        'toc_locator_id': locator('textbook:toc', book_source, book['toc']['pdf_pages'], 'table_of_contents'),
    }
    for c in book['chapters']:
        item = dict(c)
        prefix = 'textbook:chapter:' + str(c['chapter'])
        item['chapter_locator_id'] = locator(prefix, book_source,
            [c['chapter_start']['pdf_page']] * 2, 'chapter_start')
        item['exercise_locator_id'] = locator(prefix + ':exercises', book_source,
            c['exercise_pdf_pages'], 'exercise_pool', printed_pages=c['exercise_printed_pages'])
        item['answer_locator_id'] = locator(prefix + ':answers', book_source,
            c['reference_answers']['pdf_pages'], 'reference_answers',
            printed_pages=c['reference_answers']['printed_pages'])
        pool['chapters'].append(item)
    catalog['textbook_pool'] = pool
    sessions = catalog['exam_sessions']
    catalog['summary'] = {
        'unique_physical_pdf_files': len(catalog['sources']),
        'unique_exam_pdf_files': len({s['source_id'] for s in sessions}),
        'exam_sessions': len(sessions),
        'exam_sessions_by_course': dict(Counter(s['course'] for s in sessions)),
        'sessions_with_question_paper': sum(bool(s['question_locator_id']) for s in sessions),
        'sessions_with_solutions': sum(bool(s['answer_locator_id']) for s in sessions),
        'sessions_with_solutions_only': sum(bool(s['answer_locator_id']) and not s['question_locator_id'] for s in sessions),
        'textbook_chapters': len(pool['chapters']),
        'textbook_top_level_questions': sum(c['top_level_question_count'] for c in pool['chapters']),
        'assigned_question_count': None,
        'locators': len(catalog['locators']),
        'total_unique_pdf_pages': sum(s['pdf_page_count'] for s in catalog['sources']),
    }
    return catalog


def validate_catalog(catalog, materials_dir, *, check_pdf=False):
    materials_dir = Path(materials_dir)
    errors = []
    checks = Counter()
    def error(code, entity, detail):
        errors.append({'code': code, 'entity': entity, 'detail': detail})
    sources = {s['id']: s for s in catalog['sources']}
    locators = {loc['id']: loc for loc in catalog['locators']}
    if len(sources) != len(catalog['sources']) or len({s['sha256'] for s in sources.values()}) != len(sources):
        error('DUPLICATE_SOURCE', 'sources', 'Each content hash must have a single source; roles and origins belong on it')
    if len(locators) != len(catalog['locators']):
        error('DUPLICATE_LOCATOR', 'locators', 'Locator IDs must be unique')
    if len({s['id'] for s in catalog['exam_sessions']}) != len(catalog['exam_sessions']):
        error('DUPLICATE_SESSION', 'exam_sessions', 'Count sessions by ID, not role links')
    pdf_engine = None
    pdf_version = None
    if check_pdf:
        try:
            import pymupdf as pdf_engine
            pdf_version = pdf_engine.VersionBind
        except ImportError:
            error('PDF_DEPENDENCY_MISSING', 'PyMuPDF', '--check-pdf requires an installed PyMuPDF; no fallback regex page counting')
    observed_pages = {}
    for s in sources.values():
        if s.get('available_at') is not None:
            error('AVAILABLE_AT_UNSUPPORTED', s['id'], 'Historical availability is unknown for this corpus')
        for relative in s['local_paths']:
            try:
                path = local_file(materials_dir, relative)
                if not path.is_file():
                    raise FileNotFoundError(relative)
                actual_hash = sha256(path)
                checks['file_hashes_checked'] += 1
                if actual_hash != s['sha256']:
                    error('SOURCE_HASH_MISMATCH', s['id'], relative)
                with path.open('rb') as f:
                    if not f.read(5) == b'%PDF-':
                        error('PDF_HEADER_INVALID', s['id'], relative)
                if pdf_engine:
                    with pdf_engine.open(path) as pdf:
                        count = len(pdf)
                    checks['actual_pdf_page_counts_checked'] += 1
                    observed_pages[s['id']] = count
                    if count != s['pdf_page_count']:
                        error('PAGE_COUNT_MISMATCH', s['id'], f'manifest={s["pdf_page_count"]}, actual={count}')
            except (OSError, ValueError, RuntimeError) as exc:
                error('LOCAL_SOURCE_UNREADABLE', s['id'], str(exc))
    for loc in locators.values():
        s = sources.get(loc['source_id'])
        if s is None:
            error('LOCATOR_SOURCE_MISSING', loc['id'], loc['source_id'])
            continue
        checks['locator_ranges_checked'] += 1
        pages = loc['pdf_pages']
        count = observed_pages.get(s['id'], s['pdf_page_count'])
        if not (isinstance(pages, list) and len(pages) == 2 and
                all(type(p) is int for p in pages) and 1 <= pages[0] <= pages[1] <= count):
            error('PAGE_RANGE_INVALID', loc['id'], f'{pages!r}, PDF pages={count}')
        if loc['source_sha256'] != s['sha256']:
            error('LOCATOR_HASH_MISMATCH', loc['id'], 'Locator must refer to this immutable source hash')
    for s in catalog['exam_sessions']:
        checks['exam_sessions_checked'] += 1
        origin = s['origin']
        q = locators.get(s['question_locator_id'])
        a = locators.get(s['answer_locator_id'])
        if (s['complete_question_paper_present'] != origin['complete_question_paper_present']
            or bool(q) != bool(origin['question_pages'])
            or bool(a) != bool(origin['answer_pages'])
            or (q and (q['role'] != 'question_paper' or q['pdf_pages'] != origin['question_pages']))
            or (a and (a['role'] != 'solutions' or a['pdf_pages'] != origin['answer_pages']))
            or any(loc and loc['source_id'] != s['source_id'] for loc in (q, a))):
            error('EXAM_ROLE_MISMATCH', s['id'], 'Question paper and solutions must preserve separately verified roles and ranges')
        if s['exam_date'] is not None or s.get('exam_date_evidence') is not None:
            error('EXAM_DATE_UNSUPPORTED', s['id'], 'This input has no independently verified exact exam dates; academic year is not a date')
        if any(s.get(k) != origin.get(k) for k in ('academic_year', 'year_as_listed')):
            error('YEAR_PROVENANCE_MISMATCH', s['id'], 'Preserve academic year and website year separately')
        if s.get('available_at') is not None:
            error('AVAILABLE_AT_UNSUPPORTED', s['id'], 'Retrieval date or exam year cannot establish historical availability')
    pool = catalog['textbook_pool']
    if (pool['pool_type'] != 'textbook_exercise_pool'
        or pool['assignment_selected'] != 'unknown'
        or pool['assigned_question_count'] is not None
        or any(c['assignment_selected'] != 'unknown' for c in pool['chapters'])):
        error('ASSIGNMENT_UNSUPPORTED', 'textbook_pool', 'No actual assignment list was provided')
    checks['textbook_chapters_checked'] = len(pool['chapters'])
    # Verify against the separately stored original manifests, not merely the
    # duplicated origin fields. This catches coordinated catalog edits as well.
    if catalog.get('input_manifests'):
        for original in catalog['input_manifests']:
            try:
                path = local_file(materials_dir, original['local_path'])
                checks['input_manifest_hashes_checked'] += 1
                if sha256(path) != original['sha256']:
                    error('INPUT_MANIFEST_CHANGED', original['local_path'], 'Rebuild and review the new snapshot')
            except (OSError, ValueError) as exc:
                error('INPUT_MANIFEST_UNREADABLE', original['local_path'], str(exc))
        try:
            expected = build_catalog(materials_dir)
            for key in ('sources', 'locators', 'exam_sessions', 'textbook_pool', 'summary', 'snapshot_id'):
                if catalog.get(key) != expected[key]:
                    error('ORIGIN_RECORD_MISMATCH', key, 'Catalog differs from canonical normalization of original input manifests')
        except (OSError, ValueError, KeyError) as exc:
            error('ORIGIN_VALIDATION_FAILED', 'input_manifests', str(exc))
    return {'ok': not errors, 'checked_at_utc': datetime.now(timezone.utc).isoformat(),
            'checks': dict(checks), 'pdf_page_count_verification': 'actual_pdf_open' if pdf_engine else 'manifest_bounds_only',
            'pdf_library': {'name': 'PyMuPDF', 'version': pdf_version} if pdf_engine else None,
            'errors': errors,
            'not_checked': ['question semantics / OCR accuracy', 'solution correctness',
                            'historical availability', 'actual assigned exercises', 'learning or prediction benefit']}



def _validated_catalog(root, catalog=None, *, check_pdf=False):
    """Reject malformed and inconsistent imports before any published mutation."""
    try:
        canonical = build_catalog(root)
        if catalog is not None:
            for key in ('sources', 'locators', 'exam_sessions', 'textbook_pool', 'summary', 'snapshot_id'):
                if catalog.get(key) != canonical[key]:
                    raise ValueError('ORIGIN_RECORD_MISMATCH: ' + key)
        for source in canonical['sources']:
            if (source['id'] != 'sha256:' + source['sha256']
                    or len(source['sha256']) != 64
                    or any(c not in '0123456789abcdef' for c in source['sha256'])
                    or not source['local_paths']
                    or type(source['pdf_page_count']) is not int or source['pdf_page_count'] < 1):
                raise ValueError('Invalid source identity or page count')
        for session in canonical['exam_sessions']:
            if not session['full_locator_id']:
                raise ValueError('Missing exam material page range')
        seen = set()
        for chapter in canonical['textbook_pool']['chapters']:
            number = chapter['chapter']
            first, last = chapter['top_level_question_range']
            if (number in seen or type(number) is not int or number < 1
                    or not 1 <= first <= last
                    or chapter['top_level_question_count'] != last - first + 1):
                raise ValueError('Invalid chapter exercise count or identity')
            seen.add(number)
        report = validate_catalog(canonical, root, check_pdf=check_pdf)
        if not report['ok']:
            raise ValueError('; '.join(e['code'] + ': ' + e['entity'] for e in report['errors']))
        return canonical, report
    except (KeyError, TypeError, AttributeError, IndexError, OSError, RuntimeError) as exc:
        raise ValueError('Invalid course material input: ' + str(exc)) from exc


def import_materials(materials, output, *, index_path=None):
    """Import two manifests and PDFs, then atomically advance a portable pointer.

    Optionally compare an existing catalog to the original manifests. The
    caller supplies input/output paths; there are no research-directory defaults.
    """
    import shutil
    import tempfile
    from .jsonio import write_json_atomic

    materials, output = Path(materials).resolve(), Path(output).resolve()
    supplied = json.loads(Path(index_path).read_text(encoding='utf-8')) if index_path else None
    catalog, report = _validated_catalog(materials, supplied, check_pdf=True)
    snapshot = catalog['snapshot_id']
    output.mkdir(parents=True, exist_ok=True)
    snapshots = output / 'snapshots'
    snapshots.mkdir(exist_ok=True)
    final = snapshots / snapshot
    with tempfile.TemporaryDirectory(prefix='.import-', dir=output) as temporary:
        pending = Path(temporary) / 'snapshot'
        pending.mkdir()
        names = {m['local_path'] for m in catalog['input_manifests']}
        names.update(p for source in catalog['sources'] for p in source['local_paths'])
        for name in sorted(names):
            target = pending / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_file(materials, name), target)
        # Verify copied bytes too: a changing source cannot enter a valid bundle.
        _validated_catalog(pending, catalog, check_pdf=True)
        write_json_atomic(pending / 'catalog.json', catalog)
        write_json_atomic(pending / 'validation.json', report)
        if final.exists():
            existing = json.loads((final / 'catalog.json').read_text(encoding='utf-8'))
            _validated_catalog(final, existing)
            if existing != catalog:
                raise ValueError('Existing course library snapshot has different content')
        else:
            pending.rename(final)
    write_json_atomic(output / 'current.json', {
        'schema_version': 'CourseMaterialLibraryPointerV1',
        'catalog': f'snapshots/{snapshot}/catalog.json',
        'catalog_sha256': sha256(final / 'catalog.json'),
    })
    return {**report, 'summary': catalog['summary'], 'snapshot_id': snapshot}


def library_input_files(bundle):
    """Return exact pointer, manifests and PDFs for preparation cache identity."""
    bundle = Path(bundle)
    pointer = bundle / 'current.json'
    if not pointer.exists():
        return [pointer]
    meta = json.loads(pointer.read_text(encoding='utf-8'))
    catalog_path = local_file(bundle, meta['catalog'])
    catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
    annotation = bundle / 'exam-annotations.json'
    return [pointer, catalog_path, *([annotation] if annotation.is_file() else []),
            *(local_file(catalog_path.parent, m['local_path']) for m in catalog['input_manifests']),
            *(local_file(catalog_path.parent, p) for s in catalog['sources'] for p in s['local_paths'])]


def load_library(bundle):
    """Read an imported bundle, checking canonical metadata and every PDF hash."""
    try:
        bundle = Path(bundle).resolve()
        meta = json.loads((bundle / 'current.json').read_text(encoding='utf-8'))
        if meta['schema_version'] != 'CourseMaterialLibraryPointerV1':
            raise ValueError('Unsupported course library pointer')
        path = local_file(bundle, meta['catalog'])
        if sha256(path) != meta['catalog_sha256']:
            raise ValueError('CATALOG_HASH_MISMATCH')
        saved = json.loads(path.read_text(encoding='utf-8'))
        if saved['artifact_type'] != 'CourseMaterialLibraryV1':
            raise ValueError('Unsupported course library catalog')
        catalog, _ = _validated_catalog(path.parent, saved)
        return catalog, {s['id']: local_file(path.parent, s['local_paths'][0]) for s in catalog['sources']}
    except (KeyError, TypeError, OSError) as exc:
        raise ValueError('Invalid course library bundle: ' + str(exc)) from exc


def project_library(data, bundle):
    """Attach data and reuse the reader's content-addressed source asset copier."""
    catalog, paths = load_library(bundle)
    for source in catalog['sources']:
        source['url'] = 'assets/source/' + source['sha256'] + '.pdf'
    from .exam_analysis import analyze, load_annotations
    annotation = Path(bundle) / 'exam-annotations.json'
    if annotation.is_file():
        annotations = load_annotations(annotation)
        catalog['exam_study'] = analyze(catalog, annotations)
    data['course_library'] = catalog
    return {'course-library/' + source['sha256'] + '.pdf': paths[source['id']]
            for source in catalog['sources']}
