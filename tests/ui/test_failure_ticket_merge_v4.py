"""Failure cards retain all current-run phases and versioned closure authority."""
import pytest
from aiforstudy.core.jsonio import write_json_atomic
from aiforstudy.ui.feedback import collect_failure_cards


def ticket(identity, version='v1', *, status='ready', problem=None):
    return {'ticket_id': identity, 'status': status, 'problem': problem or identity,
            'target': {'artifact_id': 'lesson:U', 'artifact_hash': version}}


def save(root, stage, tickets=(), frozen=()):
    write_json_atomic(root / 'work/products' / stage / 'U.json',
                      {'payload': {'tickets': list(tickets), 'frozen_tickets': list(frozen)}})


def cards(root):
    return collect_failure_cards(store_root=root, state={'status': 'needs_review',
        'nodes': {'figures:U': {'scope_key': 'U', 'quality_state': 'needs_review'}}})


def test_closed_content_ticket_does_not_hide_current_media_issue(tmp_path):
    save(tmp_path, 'text_audit', [ticket('content', status='closed')])
    save(tmp_path, 'figures', [ticket('media')])
    assert [row['ticket_id'] for row in cards(tmp_path)] == ['media']


def test_all_current_phases_are_merged_and_same_version_duplicates_collapse(tmp_path):
    for stage in ('text_audit', 'media_plan', 'figures', 'audit', 'interaction', 'render'):
        save(tmp_path, stage, [ticket(stage), ticket('shared')])
    rows = cards(tmp_path)
    assert {row['ticket_id'] for row in rows} == {
        'text_audit', 'media_plan', 'figures', 'audit', 'interaction', 'render', 'shared'}
    assert len(rows) == 7


@pytest.mark.parametrize('closed_location', ['tickets', 'frozen_tickets'])
def test_authoritative_content_closure_cannot_be_revived_by_old_media_ticket(tmp_path, closed_location):
    closed = ticket('T', status='closed')
    save(tmp_path, 'text_audit', [closed] if closed_location == 'tickets' else [],
         [closed] if closed_location == 'frozen_tickets' else [])
    save(tmp_path, 'figures', [ticket('T')])
    save(tmp_path, 'audit', [ticket('unrelated')])
    assert [row['ticket_id'] for row in cards(tmp_path)] == ['unrelated']


def test_new_content_hash_can_reopen_same_ticket_without_old_closure_hiding_it(tmp_path):
    save(tmp_path, 'text_audit', [ticket('T', 'old', status='closed')])
    save(tmp_path, 'figures', [ticket('T', 'new', problem='新版需要修复')])
    rows = cards(tmp_path)
    assert len(rows) == 1 and rows[0]['ticket_id'] == 'T'
    assert rows[0]['fields']['blocker'] == '新版需要修复'
    assert rows[0]['fields']['current_version'].endswith('@ new')


def test_unnamed_or_unversioned_malformed_tickets_are_not_silently_merged(tmp_path):
    save(tmp_path, 'text_audit', [{'problem': '未具名正文问题'}, ticket('T', '', status='closed')])
    save(tmp_path, 'figures', [{'problem': '未具名媒体问题'}, ticket('T', '', problem='缺版本待核验')])
    rows = cards(tmp_path)
    assert [row['fields']['blocker'] for row in rows] == [
        '未具名正文问题', '未具名媒体问题', '缺版本待核验']
    assert all(row['missing'] for row in rows)


def test_current_text_audit_open_ticket_outranks_stale_downstream_closure(tmp_path):
    save(tmp_path, 'text_audit', [ticket('T', problem='当前正文问题')])
    save(tmp_path, 'figures', [ticket('T', status='closed')])
    assert [row['fields']['blocker'] for row in cards(tmp_path)] == ['当前正文问题']


def test_media_audit_closure_outranks_old_generation_ticket(tmp_path):
    save(tmp_path, 'figures', [ticket('media')])
    save(tmp_path, 'audit', [ticket('media', status='closed')])
    assert cards(tmp_path) == []
