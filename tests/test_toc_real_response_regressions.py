"""Offline regressions copied from failed preparation, with immutable provenance."""
import copy
import json
from pathlib import Path

import pytest

from blocks.parse.toc import dual_source as D
from blocks.parse.toc.align import align

FIXTURES = Path(__file__).parent / 'quality' / 'fixtures' / 'toc'


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def replay_alignment(tmp_path, title=None):
    data = fixture('linear-algebra-7.3.json')
    candidate = data['candidate']
    if title is not None:
        candidate['text'] = '§7.3 ' + title
    lines = [''] * 3650
    lines[0] = '[第 231 页]'
    lines[3649] = data['body_span']['text'] if title is None else candidate['text']
    (tmp_path / 'document.md').write_text('\n'.join(lines))
    (tmp_path / 'headings.json').write_text(json.dumps({'headings': [candidate]}))
    (tmp_path / 'toc.excluded.json').write_text(json.dumps({'all_entries': [{'toc_page': 5}]}))
    return align({'nodes': [data['authority']]}, tmp_path, resolve_regions=False)


def test_real_linear_7_3_unicode_tex_title_preserves_body_landing(tmp_path):
    result = replay_alignment(tmp_path)
    assert result['unmatched_authority'] == []
    match, = result['matches']
    assert (match['toc_id'], match['line'], match['page']) == ('T-0091', 3650, 231)
    assert match['authority_title'] == 'ℝⁿ上的线性变换'
    assert match['candidate_text'] == r'§7.3 \mathbb{R}^n 上的线性变换'


@pytest.mark.parametrize('title', [r'$\mathbb{R}^m$ 上的线性变换',
                                   r'$\mathbb{R}^{n+1}$ 上的线性变换',
                                   r'$R^n$ 上的线性变换', 'ℝn上的线性变换',
                                   'ℝₙ上的线性变换'])
def test_linear_7_3_rejects_changed_space_or_exponent(tmp_path, title):
    result = replay_alignment(tmp_path, title)
    assert result['matches'] == []
    assert result['unmatched_authority'][0]['number'] == '7.3'


def source_entry(title, source, **changes):
    return D.normalize_entry(dict(number='7.3', title=title, level=2,
        printed_page='231', toc_page=5, category='teaching', reason='PDF第5页该行清晰',
        **changes), source=source, index=1)


@pytest.mark.parametrize(('mineru', 'visual'), [
    (r'$\mathbb{R}^n$ 上的线性变换', 'ℝⁿ上的线性变换'),
    (r'$x^{12}+\alpha_1$ 型', 'x¹²+α₁型'),
    (r'$\left|x\right| \leq 1$ 型', '|x|≤1型'),
])
def test_equivalent_math_titles_are_same_without_rewriting_source(mineru, visual):
    m, v = source_entry(mineru, 'M'), source_entry(visual, 'V')
    differences = D._compare({'schema_version': '1.0', 'matches': [
        {'mineru_id': 'M0001', 'visual_id': 'V0001', 'status': 'same'}]}, [m], [v])
    assert differences == []
    assert m['title'] == mineru and v['title'] == visual


@pytest.mark.parametrize(('mineru', 'visual'), [
    ('x', 'y'), ('x²', 'x2'), ('x²', 'x³'), ('xᵐ', 'xⁿ'),
    (r'x^{a+b}', 'x^a+b'), (r'x_{12}', 'x_1 2'),
    (r'\unknown{x}', 'x'), (r'\unknown x', r'\unknownx'),
    ('x+y', 'x-y'), ('f(x,y)', 'f(xy)'),
    (r'\mathbf{x}', 'x'),
])
def test_same_claim_cannot_hide_math_distinctions(mineru, visual):
    m, v = source_entry(mineru, 'M'), source_entry(visual, 'V')
    assert D._compare({'schema_version': '1.0', 'matches': [
        {'mineru_id': 'M0001', 'visual_id': 'V0001', 'status': 'same'}]}, [m], [v])


def both_response(title='ℝⁿ上的线性变换'):
    return {'schema_version': '1.0', 'entries': [dict(source_entry(title, 'A'),
        source_ids=['M0001', 'V0001'], basis='both', reason='PDF第5页该行清晰')],
        'discarded': [], 'issues': []}


def test_both_accepts_equivalent_pair_and_keeps_source_evidence():
    m = source_entry(r'$\mathbb{R}^n$ 上的线性变换', 'M')
    v = source_entry('ℝⁿ上的线性变换', 'V')
    row, = D._arbitrate(both_response(), [m], [v], [5])
    assert row['source_ids'] == ['M0001', 'V0001']
    assert row['basis'] == 'both' and row['agreed'] is True


def test_real_complex_response_rejects_ambiguous_both_with_actionable_evidence():
    data = fixture('complex-analysis-arbitration.json')
    assert sum(r['basis'] == 'both' for r in data['response']['entries']) == 32
    with pytest.raises(D.ContractError, match='both_conflict') as caught:
        D._arbitrate(data['response'], data['mineru'], data['visual'], data['pages'])
    message = str(caught.value)
    assert 'M' in message and 'V' in message
    assert '下一步' in message and 'visual' in message and 'mineru' in message


def test_real_matching_both_subset_accepts_original_response_without_enum_rewrite():
    data = fixture('complex-analysis-arbitration.json')
    raw = copy.deepcopy(data['response'])
    raw['entries'] = [r for r in raw['entries'] if r['number'] in ('1', '1.1')]
    wanted = {i for r in raw['entries'] for i in r['source_ids']}
    rows = D._arbitrate(raw, [r for r in data['mineru'] if r['entry_id'] in wanted],
        [r for r in data['visual'] if r['entry_id'] in wanted], data['pages'])
    assert [r['title'] for r in rows] == ['复数和平面点集', '复数']
    assert all(r['basis'] == 'both' and r['agreed'] is True for r in rows)
    toc, excluded = D.make_authority(rows, book_title='复变', slug='complex', pdf_hash='p', document_hash='d')
    assert all(n['evidence']['agree'] for n in toc['nodes'])
    assert excluded['all_entries'][0]['source_ids'] == ['M0003', 'V0003']


def test_explicit_mineru_choice_retains_actual_evidence_in_authority():
    m = source_entry('线性变换', 'M')
    v = source_entry('变换', 'V')
    raw = both_response('线性变换')
    raw['entries'][0].update(basis='mineru', number='7', level=1,
        reason='PDF第5页章标题清晰为线性变换，视觉漏读线性两字')
    rows = D._arbitrate(raw, [m], [v], [5])
    toc, excluded = D.make_authority(rows, book_title='书', slug='book', pdf_hash='p', document_hash='d')
    assert toc['nodes'][0]['evidence']['from'] == ['printed-toc']
    assert toc['nodes'][0]['evidence']['agree'] is False
    assert excluded['all_entries'][0]['basis'] == 'mineru'


@pytest.mark.parametrize('change', ['title', 'level', 'printed_page', 'toc_page', 'category', 'parent_key', 'output', 'one_source'])
def test_both_requires_pair_and_output_to_agree_on_substance(change):
    m, v = source_entry('ℝⁿ上的线性变换', 'M'), source_entry('ℝⁿ上的线性变换', 'V')
    raw = both_response()
    if change == 'output':
        raw['entries'][0]['title'] = 'ℝᵐ上的线性变换'
    elif change == 'one_source':
        raw['entries'][0]['source_ids'] = ['M0001']
        raw['discarded'] = [{'source_ids': ['V0001'], 'toc_page': 5, 'reason': '图上误读'}]
    else:
        v[change] = {'title': 'ℝᵐ上的线性变换', 'level': 3, 'printed_page': '232',
            'toc_page': 6, 'category': 'exercise', 'parent_key': '8:另一章'}[change]
    with pytest.raises(D.ContractError, match='both_conflict'):
        D._arbitrate(raw, [m], [v], [5, 6])
