"""Real runner up to text_audit: media-only changes share the lesson budget."""
import json

from aiforstudy.core.jsonio import read_json, write_json_atomic
from aiforstudy.pipeline.engine import build_engine, execute
from aiforstudy.pipeline.services import Providers
from support.runner_v5 import setup_book, _request, TeachingTransport, UNIT


class MediaOnlyRepairTransport(TeachingTransport):
    def _answer(self, system, user):
        payload = json.loads(user)
        task = payload.get('role_task')
        frozen = payload.get('frozen_context') or {}
        if task == 'author':
            self.author_calls += 1
            return json.dumps({'content': {'title': '约定', 'body_markdown': '正文始终保持准确。'},
                'media_requirements': {'requests': [], 'decisions': []}}, ensure_ascii=False)
        if task == 'reviewer':
            self.review_calls += 1
            return json.dumps({'verdicts': [{'status': 'fail',
                'reason': '例题的步骤对应尚不清楚，请补完整动画需求。'}]}, ensure_ascii=False)
        if task == 'repair':
            self.content_repairs += 1
            assert frozen['content']['body_markdown'] == '正文始终保持准确。'
            return json.dumps({'content': frozen['content'], 'media_requirements': {
                'requests': [{'request_key': f'EXAMPLE-{self.content_repairs}', 'kind': 'animation',
                    'learning_goal': f'第{self.content_repairs}个过程的可见对应',
                    'animation': {'steps': ['先显示来源，再呈现对应关系。']}}], 'decisions': []}}, ensure_ascii=False)
        raise AssertionError('unexpected paid role: '+str(task))


def test_media_only_progress_can_use_two_existing_rounds_then_keeps_unresolved_html(tmp_path):
    workspace = setup_book(tmp_path)
    write_json_atomic(workspace.root/'config/review_mode.json', {'mode': 'balanced'})
    transport = MediaOnlyRepairTransport()
    result, store = execute(build_engine(_request(), workspace,
        providers=Providers(llm=transport), until='text_audit'))
    state = store.load_state()
    assert not result.failed_nodes and not result.blocked_nodes, result.as_dict()
    assert transport.content_repairs == 2
    assert transport.review_calls == 3
    repairs = state['repair_states'][UNIT]
    assert repairs['content_rounds_by_artifact']['lesson:'+UNIT] == 2
    assert '次数已用尽' in repairs['stopped_reason']
    audit = read_json(store.work/'products/text_audit'/f'{UNIT}.json')['payload']
    assert audit['review_outcome']['status'] == 'known_error'
    head = read_json(store.work/'products/content'/UNIT/'current.json')
    media = read_json(head['author_media_ref']['path'])
    assert [r['request_key'] for r in media['requirements']['requests']] == ['EXAMPLE-1', 'EXAMPLE-2']
    assert len(audit['tickets']) == 1

    # Use the existing offline assembler, not a browser or a fake acceptance.
    from aiforstudy.blocks.render.diagnostics import build_reader_snapshot
    from aiforstudy.core.review_mode import review_mode
    with review_mode('balanced'):
        snapshot = build_reader_snapshot(store.root, unit_ids=[UNIT], out_dir=tmp_path/'reader-preview',
            parse_root=workspace.root/'book/parse')
    from pathlib import Path
    output = Path(snapshot['output_dir'])
    assert (output/'index.html').is_file()
    assert '正文始终保持准确。' in (output/'data/data.js').read_text()
    assert not snapshot['production_accepted']
