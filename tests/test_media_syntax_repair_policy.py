"""Media JSON repair uses the shared syntax budget without rebuying candidates."""
import json
from pathlib import Path

import pytest

from aiforstudy.core.review_mode import review_mode
from test_syntax_repair_policy import client_for
from test_media_code_contracts import approved_case


@pytest.mark.parametrize('mode', ['json_only', 'full', 'balanced'])
def test_media_code_syntax_uses_shared_five_round_journal(tmp_path, mode):
    from aiforstudy.blocks.media.code_generation import generate_media_code
    content, approval, request, program = approved_case()
    wire = json.dumps(program, ensure_ascii=False)
    client, calls = client_for(tmp_path, [wire + '}'] * 5 + [wire])
    journal = tmp_path/'code-corrections.json'
    with review_mode(mode):
        result = generate_media_code(request, content=content, approval=approval,
            client=client, correction_journal=journal)
        replayed = generate_media_code(request, content=content, approval=approval,
            client=client, correction_journal=journal)
    assert result == replayed == program
    assert len(calls) == 6 and all(c['effort'] == 'low' for c in calls[1:])
    assert 'approved_content' not in calls[1]['messages'][-1]['content']
    assert len(json.loads(journal.read_text())['attempts']) == 6


@pytest.mark.parametrize('mode', ['json_only', 'full', 'balanced'])
def test_media_syntax_exhaustion_cannot_open_more_outer_batches(tmp_path, monkeypatch, mode):
    from aiforstudy.blocks.media.code_execution import execute_code
    from aiforstudy.blocks.media import code_runtime
    content, approval, request, _ = approved_case()
    client, calls = client_for(tmp_path, ['{"code":'] * 30)
    monkeypatch.setattr(code_runtime, 'preflight_generated_code', lambda *a, **k: {'ok': True})
    with review_mode(mode):
        result = execute_code(request, content=content, approval=approval,
            out_dir=tmp_path/'media', client=client, reviewer=None)
        replayed = execute_code(request, content=content, approval=approval,
            out_dir=tmp_path/'media', client=client, reviewer=None, previous=result)
    assert len(calls) == 6
    assert len(result['code_attempts']) == len(replayed['code_attempts']) == 1
    journals = list((tmp_path/'media').rglob('code-corrections.json'))
    assert len(journals) == 1 and len(json.loads(journals[0].read_text())['attempts']) == 6


def test_media_review_repairs_json_without_resending_images(tmp_path, monkeypatch):
    from aiforstudy.blocks.media import validation
    from test_balanced_media_review import picture, REQUEST, CONTENT
    from PIL import Image
    from aiforstudy.core.digest import digest_file
    image = tmp_path/'display.png'
    Image.new('RGB', (100, 100), 'navy').save(image)
    def capture(path, *, profiles, **kwargs):
        return [{'image_path': str(image), 'image_hash': digest_file(image),
                 'profile_id': p['profile_id'], 'viewport_width': p['width'],
                 'media_width': p['content_width'], 'overflow': False, 'labels': []}
                for p in profiles]
    monkeypatch.setattr(validation, 'capture_display', capture)
    request, row = REQUEST, picture(tmp_path)
    good = {'verdicts': [{'status': 'pass', 'reason': '实际画面对应正文。'}]}
    text = json.dumps(good, ensure_ascii=False)
    client, calls = client_for(tmp_path, [text + '}', text])
    with review_mode('balanced'):
        report = validation.validate_media(request, row, client=client, content=CONTENT,
            workspace_root=Path(__file__).parents[1])
        validation.validate_media(request, row, client=client, content=CONTENT,
            workspace_root=Path(__file__).parents[1])
    assert len(calls) == 2, report
    assert calls[0]['images'] and calls[1]['images'] is None
    assert calls[1]['effort'] == 'low'
    assert 'evidence_catalog' not in calls[1]['messages'][-1]['content']
    assert not report['review_failure'], report
