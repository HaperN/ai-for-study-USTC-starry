"""Page-scoped model work retains global hierarchy and complete source accounting."""
import copy
import json
from pathlib import Path

import pytest

from blocks.parse.toc import dual_source as D
from blocks.parse.toc.replay import capture, replay


@pytest.fixture
def directory(tmp_path, monkeypatch):
    rows = [
        {'text': '第一章 向量', 'toc_page': 1, 'from_page': 3, 'level': 1},
        {'text': '1.1 线性运算', 'toc_page': 2, 'from_page': 3, 'level': 2},
        {'text': '1.1.1 加法', 'toc_page': 3, 'from_page': 4, 'level': 3},
        {'text': '习题1', 'toc_page': 4, 'from_page': 4, 'level': 2},
        {'text': '第二章 矩阵', 'toc_page': 5, 'from_page': 5, 'level': 1},
    ]
    (tmp_path / 'toc.json').write_text(json.dumps({'printed_entries': rows}))
    (tmp_path / 'document.md').write_text('第一章 向量\n正文')
    pdf = tmp_path / 'book.pdf'
    pdf.write_bytes(b'offline-pdf')
    from blocks.parse.toc import cli
    monkeypatch.setattr(cli, '_find_toc_pages', lambda _pdf: [3, 4, 5])
    def render(_pdf, pages, output):
        shots = []
        for page in pages:
            image = tmp_path / f'page-{page}.png'
            image.write_bytes(f'image-{page}'.encode())
            shots.append({'pdf_page': page, 'path': str(image)})
        return shots
    monkeypatch.setattr(D, 'render_toc_pages', render)
    return tmp_path, pdf


class PageClient:
    def __init__(self, root, *, omit_last=False, fail_compare=False):
        self.rows = D._mineru(json.loads((root / 'toc.json').read_text()), [3, 4, 5])
        self.calls = []
        self.omit_last, self.fail_compare = omit_last, fail_compare
        self.resource_id = 'before-directory'

    def chat_json(self, messages, *, tag, images=None):
        data = json.loads(messages[-1]['content'])
        self.calls.append((tag, data, images))
        if tag.startswith('toc-visual:'):
            rows = copy.deepcopy([r for r in self.rows if r['toc_page'] in data['pdf_pages']])
            if rows:
                rows[0]['title'] = '空间向量'
            response = {'schema_version': '1.0', 'complete': True, 'issues': [],
                        'pages': [{'pdf_page': p, 'status': 'toc'} for p in data['pdf_pages']],
                        'entries': rows}
        elif tag.startswith('toc-compare:'):
            if self.fail_compare:
                raise TimeoutError('offline deadline simulation')
            response = {'schema_version': '1.0', 'matches': [
                {'mineru_id': m['entry_id'], 'visual_id': v['entry_id'],
                 'status': 'different', 'reason': '需按原图核对父级与标题'}
                for m, v in zip(data['mineru'], data['visual'])], 'issues': []}
        else:
            response = {'schema_version': '1.0', 'entries': [
                {**v, 'source_ids': [m['entry_id'], v['entry_id']], 'basis': 'visual',
                 'reason': f"PDF第{v['toc_page']}页该行已逐项核对"}
                for m, v in zip(data['mineru'], data['visual'])], 'discarded': [], 'issues': []}
            if self.omit_last and 5 in data['pdf_pages']:
                response['entries'] = []
        return {'data': response, 'usage': {'total_tokens': 1}, 'cached': False}


def test_requests_are_page_scoped_and_continue_verified_parent_hierarchy(directory):
    root, pdf = directory
    client = PageClient(root)
    result = D.generate_directory(root, pdf, client)
    assert result['status'] == 'accepted', result
    compares = [data for tag, data, _ in client.calls if tag.startswith('toc-compare:')]
    assert [{r['toc_page'] for r in data['mineru'] + data['visual']} for data in compares] == [{3}, {4}, {5}]
    assert compares[1]['visual'][0]['parent_key'] == '1:空间向量/1.1:线性运算'
    arbitrations = [(data, images) for tag, data, images in client.calls if tag.startswith('toc-arbitrate:')]
    assert [data['pdf_pages'] for data, _ in arbitrations] == [[3], [4], [5]]
    assert all(len(images) == 1 for _, images in arbitrations)
    assert [r['title'] for r in arbitrations[1][0]['context']['preceding_ancestors']] == ['空间向量', '线性运算']
    assert all('entry_id' not in r for r in arbitrations[1][0]['context']['preceding_ancestors'])
    toc = json.loads((root / 'toc.v2.json').read_text())
    assert [r['title'] for r in toc['nodes']] == ['空间向量', '线性运算', '加法', '矩阵']
    assert toc['nodes'][2]['parent'] == toc['nodes'][1]['toc_id']
    excluded = json.loads((root / 'toc.excluded.json').read_text())
    assert [r['title'] for r in excluded['entries']] == ['习题1']
    assert len({r['entry_id'] for r in excluded['all_entries']}) == 5
    assert sorted(i for r in excluded['all_entries'] for i in r['source_ids']) == [
        'M0001', 'M0002', 'M0003', 'M0004', 'M0005', 'V0001', 'V0002', 'V0003', 'V0004', 'V0005']


def test_batched_evidence_replays_after_body_change_without_network(directory):
    root, pdf = directory
    assert D.generate_directory(root, pdf, PageClient(root))['status'] == 'accepted'
    evidence = capture(root, pdf)
    assert len(evidence['outputs']['toc.comparison.json']['batches']) == 3
    (root / 'document.md').write_text('已修复正文')
    result = replay(root, pdf, evidence)
    assert result['status'] == 'accepted', result
    assert D.is_current(root, pdf)
    assert all(c['cached'] and c['usage']['total_tokens'] == 0 for c in result['calls'])


def test_missing_source_in_later_arbitration_never_publishes(directory):
    root, pdf = directory
    result = D.generate_directory(root, pdf, PageClient(root, omit_last=True))
    assert result['status'] == 'needs_review'
    assert '全部源条目' in str(result['problems'])
    assert not (root / 'toc.v2.json').exists()


def test_failed_compare_retains_bounded_request_diagnostics(directory):
    root, pdf = directory
    client = PageClient(root, fail_compare=True)
    result = D.generate_directory(root, pdf, client)
    failure = result['calls'][-1]
    assert failure['status'] == 'failed'
    assert failure['tag'].startswith('toc-compare:')
    assert failure['pdf_pages'] == [3] and failure['entry_counts'] == {'mineru': 2, 'visual': 2}
    assert 'TimeoutError' in failure['error'] and failure['seconds'] >= 0
    assert client.resource_id == 'before-directory'
    assert not (root / 'toc.v2.json').exists()


@pytest.mark.parametrize('oversize', ['entries', 'characters'])
def test_pathological_single_page_is_blocked_before_comparison(directory, oversize, monkeypatch):
    root, pdf = directory
    if oversize == 'entries':
        rows = [{'text': f'第{n}章 标题{n}', 'toc_page': n, 'from_page': 3, 'level': 1}
                for n in range(1, 80)]
    else:
        rows = [{'text': '第一章 ' + '向量' * 15000, 'toc_page': 1, 'from_page': 3, 'level': 1}]
    (root / 'toc.json').write_text(json.dumps({'printed_entries': rows}))
    # Use only the populated page for this source.
    monkeypatch.setattr(D, '_pages', lambda *_: [3])
    client = PageClient(root)
    result = D.generate_directory(root, pdf, client)
    assert result['status'] == 'needs_review'
    assert 'review_batch_limit' in str(result['problems'])
    assert len(client.calls) == 1
    assert not (root / 'toc.v2.json').exists()


def test_real_timeout_input_keeps_all_ids_and_cross_page_parents_with_smaller_requests():
    evidence = json.loads((Path(__file__).parent / 'quality' / 'fixtures' / 'toc' /
                           'linear-algebra-compare-timeout.json').read_text())
    mineru, visual = evidence['mineru'], evidence['visual']
    assert len(mineru) == len(visual) == 109
    full_chars = len(json.dumps({'mineru': mineru, 'visual': visual}, ensure_ascii=False))
    groups = D._review_batches(mineru, visual, [3, 4, 5, 6])
    assert [len(g['mineru']) + len(g['visual']) for g in groups] == [62, 64, 66, 26]
    assert [r for g in groups for r in g['mineru']] == mineru
    assert [r for g in groups for r in g['visual']] == visual
    assert groups[2]['visual'][0]['parent_key'] == '5:线性空间/5.5:线性方程组解集的结构'
    assert groups[3]['visual'][0]['parent_key'] == '7:内积空间及变换/7.4:一般的欧氏空间'
    assert max(D._review_size(g)[1] for g in groups) < full_chars * .32


def test_empty_discovered_pages_keep_distinct_batch_evidence_and_replay(directory, monkeypatch):
    root, pdf = directory
    monkeypatch.setattr(D, '_pages', lambda *_: [3, 4, 5, 6, 7])
    class WithEmptyPages(PageClient):
        def chat_json(self, messages, *, tag, images=None):
            result = super().chat_json(messages, tag=tag, images=images)
            if tag.startswith('toc-visual:'):
                for page in result['data']['pages']:
                    if page['pdf_page'] in (6, 7):
                        page['status'] = 'not_toc'
            return result
    client = WithEmptyPages(root)
    result = D.generate_directory(root, pdf, client)
    assert result['status'] == 'accepted', result
    artifact = json.loads((root / 'toc.comparison.json').read_text())
    assert artifact['batches'][-2]['request_fingerprint'] != artifact['batches'][-1]['request_fingerprint']
    evidence = capture(root, pdf)
    (root / 'document.md').write_text('正文修复')
    assert replay(root, pdf, evidence)['status'] == 'accepted'


def test_comparison_cannot_reference_a_source_id_from_another_page(directory):
    root, pdf = directory
    class CrossPageId(PageClient):
        def chat_json(self, messages, *, tag, images=None):
            result = super().chat_json(messages, tag=tag, images=images)
            if tag.startswith('toc-compare:'):
                result['data']['matches'][0]['visual_id'] = 'V0005'
            return result
    result = D.generate_directory(root, pdf, CrossPageId(root))
    assert result['status'] == 'needs_review'
    assert '不存在的源 ID' in str(result['problems'])
    assert not (root / 'toc.v2.json').exists()


def test_both_cannot_claim_consensus_when_previous_page_parent_was_corrected(directory):
    root, pdf = directory
    class ChangedParent(PageClient):
        def chat_json(self, messages, *, tag, images=None):
            result = super().chat_json(messages, tag=tag, images=images)
            if tag.startswith('toc-visual:'):
                result['data']['entries'][0]['title'] = '向量'
            elif tag.startswith('toc-arbitrate:'):
                for row in result['data']['entries']:
                    if row['number'] == '1':
                        row['title'] = '空间向量'
                    elif row['number'] == '1.1.1':
                        row['basis'] = 'both'
            return result
    result = D.generate_directory(root, pdf, ChangedParent(root))
    assert result['status'] == 'needs_review'
    assert 'both_parent_conflict' in str(result['problems'])
    assert not (root / 'toc.v2.json').exists()
