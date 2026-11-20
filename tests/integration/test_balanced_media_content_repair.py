"""A media discovery uses the existing lesson budget, with real worker/browser."""
import json
from pathlib import Path

from aiforstudy.core.jsonio import read_json, write_json_atomic
from aiforstudy.pipeline.engine import build_engine, execute
from aiforstudy.pipeline.services import Providers
from support.runner_v5 import setup_book, _request, UNIT
from test_balanced_end_to_end import BalancedTransport


class MediaContentTransport(BalancedTransport):
    def __init__(self):
        super().__init__(False)

    def _answer(self, system, user):
        if json.loads(user).get('role_task') == 'media_substantive_review':
            self.roles.append('media_substantive_review')
            return json.dumps({'verdicts': [], 'problems': [{
                'target_artifact_kind': 'content', 'status': 'fail',
                'reason': '正文首句的适用条件仍未说明，请在正文补清条件。'}]}, ensure_ascii=False)
        return super()._answer(system, user)


def test_media_content_discovery_uses_two_lesson_rounds_and_keeps_html(tmp_path, monkeypatch):
    workspace = setup_book(tmp_path)
    from aiforstudy.providers.capabilities import resolve_manim_interpreter
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv('AIFORSTUDY_MANIM_PYTHON', resolve_manim_interpreter(root))
    monkeypatch.setenv('AIFS_PLAYWRIGHT_MODULE', str(root/'node_modules/playwright'))
    caps = read_json(workspace.root/'config/capabilities.json')
    caps['render_environment']['worker_dir'] = str(root/'tools/manim_worker')
    write_json_atomic(workspace.root/'config/capabilities.json', caps)
    write_json_atomic(workspace.root/'config/review_mode.json', {'mode': 'balanced'})
    transport = MediaContentTransport()
    result, store = execute(build_engine(_request(), workspace, providers=Providers(llm=transport)))
    assert result.status == 'needs_review', result.as_dict()
    assert transport.content_repairs == 2
    assert transport.roles.count('media_code_generation') == 3
    assert transport.roles.count('media_substantive_review') == 3
    for repair in transport.repair_inputs:
        assert '正文首句的适用条件仍未说明' in str(repair['repair_tickets'])
    state = store.load_state()['repair_states'][UNIT]
    assert state['content_rounds_by_artifact']['lesson:'+UNIT] == 2
    figures = read_json(store.work/'products/figures'/f'{UNIT}.json')['payload']
    assert figures['requests'][0]['review_outcome']['status'] == 'known_error'
    assert figures['review_outcome']['status'] == 'known_error'
    audit = read_json(store.work/'products/audit'/f'{UNIT}.json')['payload']
    assert audit['review_outcome']['status'] == 'known_error'
    assert audit['review_status'] == 'reused'
    assert result.preview_available
    assert not result.blocked_nodes and not result.failed_nodes
    assert next((store.work/'legacy/render').glob('*/index.html')).is_file()
