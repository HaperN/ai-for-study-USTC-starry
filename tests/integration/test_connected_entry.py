"""Default entry points must assemble real providers and resolve cold scope before generation."""
import json
from pathlib import Path

from aiforstudy.contracts.enums import Profile, QualityState
from aiforstudy.contracts.stage import StageResult
from aiforstudy.pipeline.engine import build_engine, execute
from aiforstudy.pipeline.services import Providers, StageRegistry
from test_m2_engine import make_workspace, request_for


def _register_directory_pdf(root, book_id, *, with_exercise=False):
    """The cold fixture includes both its directory and a separate body PDF page."""
    import pymupdf
    from aiforstudy.core.digest import digest_file
    from aiforstudy.core.jsonio import write_json_atomic
    source = root / 'source.pdf'
    document = pymupdf.open()
    document.new_page().insert_text(
        (40, 40), '目录\n第一章 复数 1\n1.1 复数的定义 1', fontname='china-s')
    body = ('第一章 复数\n这一章介绍复数的基本运算。\n1.1 复数的定义\n'
            '复数由实部和虚部构成，记为 z=x+iy，其中 i 的平方等于负一。')
    if with_exercise:
        body += '\n习题\n1. 求复数的实部。'
    document.new_page().insert_text((40, 40), body, fontname='china-s')
    document.save(source)
    document.close()
    registry = json.loads((root / 'config/books.json').read_text())
    registry['books'][book_id]['sources'] = [
        {'kind': 'pdf', 'path': 'source.pdf', 'sha256': digest_file(source)}]
    write_json_atomic(root / 'config/books.json', registry)
    return source


class _DirectoryTransport:
    """Controlled responses for this small source book; real adapters execute."""
    def __init__(self):
        self.calls = []

    def __call__(self, *, messages, images, **kwargs):
        data = json.loads(messages[-1]['content'])
        self.calls.append({'data': data, 'images': images})
        if set(data) == {'pdf_pages'}:
            assert data['pdf_pages'] == [1]
            assert len(images) == 1
            assert Path(images[0]).read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
            response = {'schema_version': '1.0', 'complete': True,
                        'pages': [{'pdf_page': 1, 'status': 'toc'}], 'entries': [
                {'title': '复数', 'number': '第一章', 'level': 1, 'printed_page': '1',
                 'toc_page': 1, 'category': 'teaching', 'reason': '受控目录图第一章行'},
                {'title': '复数的定义', 'number': '1.1', 'level': 2, 'printed_page': '1',
                 'toc_page': 1, 'category': 'teaching', 'reason': '受控目录图第一节行'}]}
        else:
            assert set(data) == {'mineru', 'visual', 'pdf_pages'}
            assert data['pdf_pages'] == [1]
            assert not images
            assert [entry['title'] for entry in data['mineru']] == ['复数', '复数的定义']
            assert [entry['title'] for entry in data['visual']] == ['复数', '复数的定义']
            response = {'schema_version': '1.0', 'matches': [
                {'mineru_id': 'M0001', 'visual_id': 'V0001', 'status': 'same'},
                {'mineru_id': 'M0002', 'visual_id': 'V0002', 'status': 'same'}]}
        return json.dumps(response, ensure_ascii=False), {'total_tokens': 17}


def test_default_release_assembles_configured_transport_without_network(tmp_path, monkeypatch):
    ws, book = make_workspace(tmp_path)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key-do-not-send')
    engine = build_engine(request_for(book.book_id, profile=Profile.RELEASE), ws)
    assert callable(engine.services.providers.llm)
    assert (engine.services.control.policy.api_deadline_seconds is None
            or engine.services.control.policy.api_deadline_seconds > 0)
    assert not hasattr(engine.services.providers.llm, 'approved_budget_tokens')
    assert not (tmp_path / 'data/books' / book.book_id / 'runs').exists()


def test_smoke_never_assembles_real_transport_even_with_credentials(tmp_path, monkeypatch):
    ws, book = make_workspace(tmp_path)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key-do-not-send')
    assert build_engine(request_for(book.book_id), ws).services.providers.llm is None


def test_cold_run_replans_same_run_before_generation(tmp_path):
    ws, book = make_workspace(tmp_path)
    catalog = json.loads((book.parse / 'chapters.json').read_text())
    (book.parse / 'chapters.json').unlink()
    visited = []
    def stage(ctx):
        visited.append(ctx.node.stage)
        if ctx.node.stage == 'export':
            (ctx.context.parse / 'chapters.json').write_text(json.dumps(catalog))
        if ctx.node.stage == 'concepts':
            assert ctx.plan.resolved_scope == ('CH01-S01',)
        return StageResult.ok(ctx.node.stage, quality_state=QualityState.ACCEPTED)
    names = ('mineru', 'toc', 'structure', 'export', 'body', 'concepts', 'lesson',
             'text_audit', 'media_plan', 'figures', 'audit', 'interaction', 'render', 'delivery')
    engine = build_engine(request_for(book.book_id, ('CH01-S01',)), ws,
                          stage_registry=StageRegistry({n: stage for n in names}))
    assert engine.plan.resolved_scope is None
    result, store = execute(engine)
    assert result.status == 'succeeded'
    assert engine.graph.graph_version == store.load_state()['graph_version'] == '7.0'
    assert visited == list(names)
    assert json.loads(store.plan_path.read_text())['resolved_scope'] == ['CH01-S01']
    assert len(list(store.root.parent.iterdir())) == 1


def test_invalid_cold_selector_stops_before_concepts(tmp_path):
    ws, book = make_workspace(tmp_path)
    catalog = (book.parse / 'chapters.json').read_text()
    (book.parse / 'chapters.json').unlink()
    called = []
    def stage(ctx):
        called.append(ctx.node.stage)
        if ctx.node.stage == 'export':
            (ctx.context.parse / 'chapters.json').write_text(catalog)
        return StageResult.ok(ctx.node.stage, quality_state=QualityState.ACCEPTED)
    names = ('mineru', 'toc', 'structure', 'export', 'body', 'concepts', 'lesson')
    engine = build_engine(request_for(book.book_id, ('CH99-S01',)), ws,
                          stage_registry=StageRegistry({n: stage for n in names}))
    result, store = execute(engine)
    assert result.status == 'failed'
    assert result.failure_stage == 'scope'
    assert 'concepts' not in called and 'lesson' not in called
    assert store.load_state()['status'] == 'failed'


def test_local_mineru_cold_start_builds_catalog_without_old_products(tmp_path):
    import shutil
    ws, book = make_workspace(tmp_path)
    _register_directory_pdf(tmp_path, book.book_id)
    shutil.rmtree(book.parse)
    shutil.rmtree(book.lessons)
    mineru = book.parse / 'mineru'
    mineru.mkdir(parents=True)
    def block(kind, content):
        return {'type': kind, 'bbox': [0, 0, 200, 30], 'lines': [
            {'bbox': [0, 0, 200, 30], 'spans': [{'type': 'text', 'content': content}]}]}
    rows = [block('title', '第一章 复数'), block('text', '这一章介绍复数的基本运算。'),
            block('title', '1.1 复数的定义'),
            block('text', '复数由实部和虚部构成，记为 z=x+iy，其中 i 的平方等于负一。')]
    layout = {'_version_name': '3.4.4', '_backend': 'hybrid', '_ocr_enable': True,
              'pdf_info': [
                  {'page_idx': 0, 'page_size': [595, 842], 'para_blocks': [
                      block('title', '目录'), block('text', '第一章 复数 1\n1.1 复数的定义 1')],
                   'discarded_blocks': []},
                  {'page_idx': 1, 'page_size': [595, 842], 'para_blocks': rows,
                   'preproc_blocks': rows, 'discarded_blocks': []}]}
    (mineru / 'layout.json').write_text(json.dumps(layout, ensure_ascii=False))
    transport = _DirectoryTransport()
    engine = build_engine(request_for(book.book_id, ('CH01-S01',), profile=Profile.RELEASE),
                          ws, providers=Providers(llm=transport, is_stub=True), until='body')
    result, store = execute(engine)
    assert not result.failed_nodes, json.loads(store.state_path.read_text())
    assert not result.blocked_nodes, result.blocked_nodes
    assert len(transport.calls) == 2
    assert all('phase' not in c['data'] for c in transport.calls)
    assert 'exercises' not in store.node_states()
    assert (engine.services.context.parse / 'source_materials.json').is_file()
    assert store.node_states()['toc'].quality_state.value == 'accepted'
    assert engine.plan.resolved_scope == ('CH01-S01',)
    assert (engine.services.context.parse / 'chapters.json').is_file()
    assert not (book.parse / 'chapters.json').exists(), '原始导入目录保持不变'


def test_missing_concept_part_is_generated_and_next_run_reuses_preparation(tmp_path):
    from support.preparation_book import _mini_book, _request
    ws = _mini_book(tmp_path)
    original = ws.root / 'book/parse'
    (original / 'concepts.parts/CH01-S01.json').unlink()
    calls = []
    def transport(**kwargs):
        calls.append(kwargs)
        return json.dumps({'concepts': [{'name': '甲定义', 'type': 'definition',
                                        'formal': '甲', 'source_quote': '甲之一节'}],
                           'named_results': ['甲定义']}), {'total_tokens': 10}
    engine = build_engine(_request('mini-book'), ws, until='concepts',
                          providers=Providers(llm=transport, is_stub=True))
    result, store = execute(engine)
    assert store.node_states()['concepts'].execution_state.value == 'succeeded'
    assert len(calls) == 1
    assert not (original / 'concepts.parts/CH01-S01.json').exists()
    assert (engine.services.context.parse / 'concepts.parts/CH01-S01.json').is_file()
    second = build_engine(_request('mini-book'), ws, until='concepts',
                          providers=Providers(llm=transport, is_stub=True))
    execute(second)
    assert len(calls) == 1, '后续章节必须复用新准备的全书索引'


def test_changed_concept_text_does_not_reuse_old_complete_part(tmp_path):
    from support.preparation_book import _mini_book, _request
    ws = _mini_book(tmp_path)
    (ws.root / 'book/parse/original_text/CH01-S01.md').write_text('新定义，与旧文本不同')
    result, store = execute(build_engine(_request('mini-book'), ws, until='concepts',
                                        providers=Providers()))
    assert store.node_states()['concepts'].execution_state.value == 'blocked'


def test_resume_preserves_execution_history_without_repeating_completed_stages(tmp_path):
    from aiforstudy.pipeline.engine import run_request, resume_request
    ws, book = make_workspace(tmp_path)
    request = request_for(book.book_id, ('CH01-S01',))
    result, store, engine = run_request(request, ws)
    assert result.status == 'succeeded'
    assert engine.graph.graph_version == '7.0'
    completed = {node_id: state.attempt for node_id, state in store.node_states().items()}
    assert len(completed) == 14 and 'media_plan:CH01-S01' in completed and 'exercises' not in completed
    before = json.loads((store.root/'execution.json').read_text())
    result, same_store, _ = resume_request(request, store.run_id, ws)
    assert result.status == 'succeeded'
    generation = {node_id for node_id, state in same_store.node_states().items()
                  if state.stage not in {'render', 'delivery'}}
    assert len(generation) == 12
    assert all(same_store.node_states()[node_id].execution_state.value == 'cached' for node_id in generation)
    assert all(same_store.node_states()[node_id].attempt == completed[node_id] for node_id in generation)
    # Smoke has no real ReaderProjection, so render/delivery must revalidate it.
    assert same_store.run_id == store.run_id
    assert json.loads((store.root/'execution.json').read_text()) == before
    assert 'budget' not in json.loads(store.request_path.read_text())
    assert not (store.root/'budget-history.jsonl').exists()


def test_ensure_restores_missing_original_text_in_a_run_copy(tmp_path):
    import shutil
    test_local_mineru_cold_start_builds_catalog_without_old_products(tmp_path)
    prepared = next(tmp_path.glob('data/books/synth-book/runs/*/work/prepared-parse'))
    original = tmp_path / 'data/parse/synth-book'
    # Use the actual registered path instead of depending on fixture layout.
    registry = json.loads((tmp_path / 'config/books.json').read_text())
    original = tmp_path / registry['books']['synth-book']['parse']
    shutil.rmtree(original)
    shutil.copytree(prepared, original)
    catalog = json.loads((original / 'chapters.json').read_text())
    relative = catalog['lesson_units'][0]['original_text']
    (original / relative).unlink()
    from aiforstudy.core.workspace import Workspace
    engine = build_engine(request_for('synth-book', ('CH01-S01',), profile=Profile.RELEASE),
                          Workspace(root=tmp_path), providers=Providers(), until='body')
    result, store = execute(engine)
    assert not result.failed_nodes and not result.blocked_nodes
    assert (engine.services.context.parse / relative).is_file()
    assert not (original / relative).exists()
