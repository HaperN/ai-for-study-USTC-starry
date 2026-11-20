from copy import deepcopy
import json

from aiforstudy.practice.render import build_page, attach_reader
from test_practice_catalog import approved

def test_question_paper_contains_no_answer_even_in_embedded_data():
 q=approved();q['answer']='SECRET_ANSWER';q['solution']='SECRET_SOLUTION'
 h=build_page('题册',[q],question_only=True)
 assert 'SECRET_ANSWER' not in h and 'SECRET_SOLUTION' not in h
 assert '求 z' in h

def test_embedded_content_cannot_end_script():
 q=approved();q['stem']='</script><script>alert(1)</script>'
 h=build_page('习题',[q]);assert '</script><script>alert' not in h
 assert '\\u003c/script' in h

def test_reader_attachment_preserves_original_and_is_idempotent(tmp_path):
 book=tmp_path/'book';book.mkdir();(book/'index.html').write_text('<html><body>ORIGINAL</body></html>')
 attach_reader(book,{'basic':[approved()],'full':[approved()]})
 first=(book/'index.html').read_text();attach_reader(book,{'basic':[approved()],'full':[approved()]})
 assert first==(book/'index.html').read_text()
 assert 'ORIGINAL' in first and first.count('practice-reader.js')==1

def test_nested_paper_carries_asset_base_for_solution_illustrations():
 q=approved();q['solution']='![图](assets/explanation.svg)'
 h=build_page('解析',[q],paper={'id':'p'},assets='../../assets')
 assert '"asset_base":"../../assets"' in h


def test_embedded_question_projection_preserves_learning_content_and_mount(tmp_path):
    q = approved()
    q.update(
        stem=r'求 $\Log(1-i)$', answer=r'$-\pi i/4$', solution=r'由 $z^2=-1$。',
        public_source_path='试卷包/exam.pdf',
        public_textbook_path='教材/index.html?practice=full#/REAL-UNIT',
        mount_unit_id='REAL-UNIT', anchor_text='唯一教学锚点',
        source_file_sha256='PRIVATE_PDF_HASH', model_response='PRIVATE_RESPONSE',
    )
    q['source'].update(
        pdf_path='/private/source.pdf', public_path='试卷包/source.pdf',
        review={'metadata': 'PRIVATE_SOURCE_REVIEW'}, source_quote='PRIVATE_SOURCE_QUOTE',
    )
    placement = {
        'unit_id': 'REAL-UNIT', 'quote': '唯一教学锚点',
        'paragraph_sha256': 'a' * 64, 'method': 'exact phrase',
    }
    q['placement'] = {
        **placement, 'pdf_path': '/private/placement.pdf',
        'review': {'model_evidence': 'PRIVATE_PLACEMENT_REVIEW'},
    }
    original = deepcopy(q)
    page = build_page('习题', [q])
    (tmp_path / 'index.html').write_text('<body></body>')
    attach_reader(tmp_path, {'full': [q]})
    reader = (tmp_path / 'assets/practice-data.js').read_text()
    standalone = json.loads(page.split('window.PRACTICE_DATA=', 1)[1].split(';</script>', 1)[0])['questions'][0]
    mounted = json.loads(reader.split('=', 1)[1].strip().removesuffix(';'))['full'][0]
    assert standalone == mounted
    for field in ('stem', 'answer', 'solution', 'public_source_path', 'public_textbook_path',
                  'anchor_text', 'unit_id', 'mount_unit_id'):
        assert standalone[field] == q[field]
    assert standalone['placement'] == placement
    assert standalone['source']['public_path'] == q['source']['public_path']
    assert 'review' not in standalone
    assert 'pdf_path' not in standalone['source']
    assert 'PRIVATE_' not in json.dumps(standalone)
    assert q == original



def test_malformed_placement_value_does_not_leak_nested_records():
    q = approved()
    q['placement'] = {
        'unit_id': 'REAL-UNIT', 'quote': '唯一教学锚点', 'paragraph_sha256': 'a' * 64,
        'method': {'model_note': 'PRIVATE_PLACEMENT_METHOD'},
    }
    page = build_page('习题', [q])
    assert 'PRIVATE_PLACEMENT_METHOD' not in page
    assert '"placement":' not in page
