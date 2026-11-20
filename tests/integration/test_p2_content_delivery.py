"""P2 regressions: explicit requests, final-version wiring and runtime acceptance."""


from dataclasses import replace


import json

import pytest

from pathlib import Path


from types import SimpleNamespace


from aiforstudy.core.jsonio import write_json_atomic


from aiforstudy.core.digest import digest_file


from aiforstudy.pipeline import media_stages as t


from aiforstudy.pipeline.graph import Node


from aiforstudy.pipeline.services import StageContext


def setup_run(tmp_path):
    from support.runner_v5 import run_book
    result,store,engine,_=run_book(tmp_path,until='interaction')
    assert result.status=='succeeded',result.recovery_hint
    return store,engine,engine.services.workspace


def ctx(engine, workspace, stage, key):
    return StageContext(node=Node(node_id=f'{stage}:{key}', stage=stage, scope_key=key,
                                 requires=()), request=engine.request, plan=engine.plan,
                        workspace=workspace, context=engine.services.context,
                        services=engine.services)


def test_render_cannot_shrink_a_selected_scope(tmp_path):
    store, engine, workspace = setup_run(tmp_path)
    c = ctx(engine, workspace, 'render', 'CH01')
    c = replace(c, plan=replace(c.plan, resolved_scope=('CH01', 'CH01-S01')))
    result = t.render_stage(c)
    assert result.execution_state.value == 'succeeded'
    assert result.quality_state.value == 'needs_review'
    payload=json.loads((store.work/'products/render/CH01.json').read_text())['payload']
    assert payload['units']==['CH01','CH01-S01']
    assert any(row['unit_id']=='CH01' for row in payload['reader_snapshot']['missing'])
    assert c.plan.resolved_scope == ('CH01', 'CH01-S01')


def test_browser_runtime_detects_dynamic_missing_image_and_js_error(tmp_path):
    from aiforstudy.checks.offline import run_offline_acceptance
    import shutil
    page = tmp_path / 'index.html'
    page.write_text('<html><body><script>const i=new Image();i.src="gone.png";document.body.append(i);throw new Error("P2 injected error");</script></body></html>')
    report = run_offline_acceptance(tree_root=tmp_path, node=shutil.which('node'),
                                    jsdom=Path(__file__).resolve().parents[2] / 'node_modules')
    assert not report.passed
    runtime = next(c for c in report.checks if c.name == 'browser-execution')
    assert runtime.status == 'failed'
    assert runtime.evidence['runtime_errors']
    assert runtime.evidence['missing_images']


def test_browser_blocks_external_requests(tmp_path):
    from aiforstudy.checks.offline import run_offline_acceptance
    import shutil
    (tmp_path / 'index.html').write_text('<html><script src="https://example.invalid/test.js"></script></html>')
    report = run_offline_acceptance(tree_root=tmp_path, node=shutil.which('node'))
    assert not report.passed
    execution = next(c for c in report.checks if c.name == 'browser-execution')
    assert execution.evidence['external_requests'] == ['https://example.invalid/test.js']


def test_selected_unit_disappeared_from_catalog_cannot_be_dropped(tmp_path):
    store, engine, workspace = setup_run(tmp_path)
    c = ctx(engine, workspace, 'render', 'CH01')
    c = replace(c, plan=replace(c.plan, resolved_scope=('CH01-S01', 'CH01-S02')))
    result = t.render_stage(c)
    assert result.execution_state.value == 'blocked'
    assert '所选单元已从当前目录消失' in result.error['reason']
    assert not (store.work/'legacy/render/CH01/index.html').exists()
    assert c.plan.resolved_scope == ('CH01-S01', 'CH01-S02')


def test_every_rendered_resource_is_hash_registered(tmp_path):
    store, engine, workspace = setup_run(tmp_path)
    result = t.render_stage(ctx(engine, workspace, 'render', 'CH01'))
    root = store.work / 'legacy/render/CH01'
    refs = {r.path: r for r in result.outputs}
    assert all(p in refs for p in root.rglob('*') if p.is_file())
    app = root / 'assets/app.js'
    assert refs[app].kind == 'render.resource'
    app.write_text('throw Error("modified")')
    import pytest
    from aiforstudy.errors import ContractError
    with pytest.raises(ContractError):
        refs[app].verify()


def test_publish_candidate_copies_verified_html_and_isolates_partial_scope(tmp_path):
    from aiforstudy.delivery.publish import publish_candidate, read_current, rollback
    from support.runner_v5 import run_book
    result,store,engine,_=run_book(tmp_path,until='delivery')
    assert result.status=='succeeded',result.recovery_hint
    workspace=engine.services.workspace
    from aiforstudy.core.registry import BookRegistry
    context = BookRegistry.load(workspace).resolve('mini-book')
    publication = publish_candidate(store=store, context=context, scope=('CH01-S01',), scope_kind='units')
    assert publication['switched'] is True
    assert publication['namespace'] == 'selection'
    assert Path(publication['pointer']).is_file()
    assert not (context.releases_root / 'current.json').exists()
    released = Path(publication['release_dir'])
    assert (released / 'CH01/index.html').is_file()
    manifest = json.loads((released / 'manifest.json').read_text())
    assert manifest['parse_revision'] == context.revision
    # Idempotent resume verifies and reuses the immutable same-run release.
    again = publish_candidate(store=store, context=context, scope=('CH01-S01',), scope_kind='units')
    assert again['release_dir'] == publication['release_dir']
    before_rollback = read_current(released.parent)
    assert rollback(released.parent, publication['release_id']).switched
    assert read_current(released.parent)['chapters'] == before_rollback['chapters']


def test_publish_candidate_rejects_modified_candidate_before_switch(tmp_path):
    import pytest
    from aiforstudy.delivery.publish import publish_candidate
    from aiforstudy.errors import ContractError
    from support.runner_v5 import run_book
    result,store,engine,_=run_book(tmp_path,until='delivery')
    assert result.status=='succeeded',result.recovery_hint
    workspace=engine.services.workspace
    from aiforstudy.core.registry import BookRegistry
    context = BookRegistry.load(workspace).resolve('mini-book')
    (store.work / 'legacy/render/CH01/assets/app.js').write_text('changed after acceptance')
    with pytest.raises(ContractError):
        publish_candidate(store=store, context=context, scope=('CH01-S01',), scope_kind='units')
    assert not (context.releases_root / 'current.json').exists()


@pytest.mark.parametrize('reuse_staged_release', [False, True])
def test_publish_rejects_asset_and_manifest_changed_in_final_hook(tmp_path, reuse_staged_release):
    from aiforstudy.delivery.publish import publish_candidate
    from aiforstudy.errors import Cancelled, ContractError
    from support.runner_v5 import run_book

    result, store, engine, _ = run_book(tmp_path, until='delivery')
    assert result.status == 'succeeded', result.recovery_hint
    context = engine.services.context
    args = {'store': store, 'context': context, 'scope': ('CH01-S01',), 'scope_kind': 'units'}
    if reuse_staged_release:
        def stop():
            raise Cancelled('stop after staging')

        with pytest.raises(Cancelled):
            publish_candidate(**args, before_switch=stop)
    assert not list(context.releases_root.rglob('current.json'))

    def rewrite_after_verification():
        manifest_path, = context.releases_root.rglob('manifest.json')
        asset = manifest_path.parent / 'CH01/assets/app.js'
        asset.write_bytes(b'/* bytes outside the accepted delivery */')
        manifest = json.loads(manifest_path.read_text())
        manifest['members']['CH01/assets/app.js'] = digest_file(asset)
        write_json_atomic(manifest_path, manifest)

    with pytest.raises(ContractError):
        publish_candidate(**args, before_switch=rewrite_after_verification)
    assert not list(context.releases_root.rglob('current.json'))


def test_same_run_republish_refuses_a_changed_parse_revision(tmp_path):
    import pytest
    from aiforstudy.delivery.publish import publish_candidate
    from aiforstudy.errors import ContractError
    from support.runner_v5 import run_book

    result, store, engine, _ = run_book(tmp_path, until='delivery')
    assert result.status == 'succeeded', result.recovery_hint
    context = engine.services.context
    args = {'store': store, 'scope': ('CH01-S01',), 'scope_kind': 'units'}
    publication = publish_candidate(context=context, **args)
    pointer = Path(publication['pointer'])
    before = pointer.read_bytes()
    with pytest.raises(ContractError):
        publish_candidate(context=replace(context, revision='different-parse'), **args)
    assert pointer.read_bytes() == before

    manifest_file = Path(publication['manifest'])
    manifest = json.loads(manifest_file.read_text())
    manifest['parse_revision'] = 'written-after-publication'
    write_json_atomic(manifest_file, manifest)
    with pytest.raises(ContractError):
        publish_candidate(context=context, **args)
    assert pointer.read_bytes() == before


def test_legacy_republish_attests_the_original_release_from_the_run_record(tmp_path):
    from aiforstudy.delivery.publish import publish_candidate, read_current, rollback
    from support.runner_v5 import run_book

    result, store, engine, _ = run_book(tmp_path, until='delivery')
    assert result.status == 'succeeded', result.recovery_hint
    context = engine.services.context
    args = {'store': store, 'context': context, 'scope': ('CH01-S01',), 'scope_kind': 'units'}
    publication = publish_candidate(**args)
    manifest_file = Path(publication['manifest'])
    manifest = json.loads(manifest_file.read_text())
    manifest.pop('parse_revision')
    write_json_atomic(manifest_file, manifest)
    publication['member_hashes']['manifest.json'] = digest_file(manifest_file)
    state = store.load_state()
    store.save_state({**state, 'publication': publication})

    pointer = Path(publication['pointer'])
    history = read_current(pointer.parent)
    history.pop('publication_receipts')
    write_json_atomic(pointer, history)
    assert publish_candidate(**args)['switched']
    assert manifest == json.loads(manifest_file.read_text())

    # The verified reactivation persists proof usable by rollback independently of the old journal.
    store.save_state(state)
    assert rollback(pointer.parent, publication['release_id']).switched
    assert read_current(pointer.parent)['chapters'][0]['parse_revision'] == context.revision


def test_browser_blocks_websocket_before_local_handshake(tmp_path):
    import shutil
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from aiforstudy.checks.offline import run_offline_acceptance
    received = []
    class Listener(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.path)
            self.send_response(101)
            self.send_header('Upgrade', 'websocket')
            self.send_header('Connection', 'Upgrade')
            self.end_headers()
        def log_message(self, *a):
            pass
    server = HTTPServer(('127.0.0.1', 0), Listener)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    url = f'ws://127.0.0.1:{server.server_port}/probe'
    (tmp_path / 'index.html').write_text(f'<html><script>new WebSocket({json.dumps(url)})</script></html>')
    try:
        report = run_offline_acceptance(tree_root=tmp_path, node=shutil.which('node'))
    finally:
        server.shutdown(); server.server_close(); worker.join(2)
    assert not received, 'offline Chromium must not reach a local websocket service'
    assert not report.passed
    execution = next(c for c in report.checks if c.name == 'browser-execution')
    assert url in execution.evidence['external_requests']


def test_browser_discovery_uses_workspace_when_installed(tmp_path, monkeypatch):
    from aiforstudy.checks.offline import _browser_executable
    path = tmp_path / '.devtools/chrome/chrome-linux64/chrome'
    path.parent.mkdir(parents=True)
    path.write_text('fixture executable location')
    path.chmod(0o700)
    monkeypatch.delenv('AIFS_BROWSER_EXECUTABLE', raising=False)
    assert _browser_executable(tmp_path) == path


def test_publish_checkpoint_failure_does_not_switch_pointer(tmp_path):
    import pytest
    from aiforstudy.delivery.publish import publish_candidate
    from aiforstudy.errors import Cancelled
    from support.runner_v5 import run_book
    result,store,engine,_=run_book(tmp_path,until='delivery')
    assert result.status=='succeeded',result.recovery_hint
    workspace=engine.services.workspace
    from aiforstudy.core.registry import BookRegistry
    context = BookRegistry.load(workspace).resolve('mini-book')
    def cancelled():
        raise Cancelled('cancel during publication')
    with pytest.raises(Cancelled):
        publish_candidate(store=store, context=context, scope=('CH01-S01',),
                          scope_kind='units', before_switch=cancelled)
    assert not list(context.releases_root.rglob('current.json'))
