"""Real persistence/ownership boundaries for public, nickname-only notes."""
import hashlib
import sqlite3
import uuid
import pytest

A = 'a' * 64
B = 'b' * 64
ANCHOR = '1' * 64


def store(path, revision='v1'):
    from aiforstudy.community.store import AnnotationStore
    return AnnotationStore(path, 'complex', revision)


def payload(**updates):
    value = dict(anchor=ANCHOR, kind='text', quote='复数的模表示距离。',
                 nickname='小明', text='把它看成到原点的距离就好理解了。', request_id=str(uuid.uuid4()))
    value.update(updates)
    return value


def test_another_browser_and_reopened_store_can_read_saved_note(tmp_path):
    db = tmp_path / 'notes.sqlite3'
    first = store(db)
    note, created = first.create('CH01', payload(), A)
    assert created and note['is_mine']
    second = store(db)
    result = second.list('CH01', B)
    assert result['total'] == 1
    assert result['items'][0]['text'] == '把它看成到原点的距离就好理解了。'
    assert result['items'][0]['is_mine'] is False
    assert result['counts'] == {ANCHOR: 1}
    assert not ({'author_hash', 'token', 'request_id'} & result['items'][0].keys())
    with sqlite3.connect(db) as conn:
        row = conn.execute('SELECT author_hash FROM annotations').fetchone()
    assert row[0] == hashlib.sha256(A.encode()).hexdigest()
    assert A.encode() not in db.read_bytes()


def test_same_nickname_does_not_allow_deleting_another_authors_note(tmp_path):
    from aiforstudy.community.store import StoreError
    s = store(tmp_path / 'notes.db')
    first, _ = s.create('CH01', payload(), A)
    second, _ = s.create('CH01', payload(), B)
    with pytest.raises(StoreError) as exc:
        s.delete(first['id'], B)
    assert exc.value.status == 403
    assert s.list('CH01')['total'] == 2
    assert s.delete(first['id'], A)
    assert s.list('CH01')['items'][0]['id'] == second['id']


def test_versions_units_and_books_do_not_mix(tmp_path):
    from aiforstudy.community.store import AnnotationStore, StoreError
    db = tmp_path / 'notes.db'; s = store(db)
    note, _ = s.create('CH01', payload(), A)
    assert s.list('CH02')['total'] == 0
    other = store(db, 'v2')
    assert other.list('CH01')['total'] == 0
    assert AnnotationStore(db, 'other-book', 'v1').list('CH01')['total'] == 0
    with pytest.raises(StoreError) as exc: other.delete(note['id'], A)
    assert exc.value.status == 404


def test_uncertain_transport_retry_is_idempotent_and_conflict_is_rejected(tmp_path):
    from aiforstudy.community.store import StoreError
    s = store(tmp_path / 'notes.db'); body = payload()
    one, made = s.create('CH01', body, A)
    two, made_again = s.create('CH01', body, A)
    assert made and not made_again and one['id'] == two['id']
    with pytest.raises(StoreError) as exc:
        s.create('CH01', dict(body, text='另一条内容'), A)
    assert exc.value.status == 409
    assert s.list('CH01')['total'] == 1


@pytest.mark.parametrize('changes', [dict(nickname='  '), dict(nickname='甲'*31),
    dict(text='\n\t'), dict(text='字'*2001), dict(quote=''), dict(quote='字'*601),
    dict(anchor='bad'), dict(kind='html'), dict(request_id='x'), dict(text=7),
    dict(nickname='a\x00b')])
def test_invalid_content_never_creates_a_row(tmp_path, changes):
    from aiforstudy.community.store import StoreError
    s = store(tmp_path / 'notes.db')
    with pytest.raises(StoreError) as exc: s.create('CH01', payload(**changes), A)
    assert exc.value.status == 400
    assert s.list('CH01')['total'] == 0


def test_token_required_and_plain_html_is_saved_as_text(tmp_path):
    from aiforstudy.community.store import StoreError
    s = store(tmp_path / 'notes.db')
    with pytest.raises(StoreError): s.create('CH01', payload(), '')
    with pytest.raises(StoreError): s.create('CH01', payload(), 'same-nickname')
    note, _ = s.create('CH01', payload(text='<img src=x onerror=alert(1)>'), A)
    assert note['text'] == '<img src=x onerror=alert(1)>'


def test_pagination_counts_and_newest_first_are_independent_of_page_size(tmp_path):
    s = store(tmp_path / 'notes.db')
    ids = [s.create('CH01', payload(text=str(i)), A)[0]['id'] for i in range(7)]
    page = s.list('CH01', A, limit=3)
    assert [n['id'] for n in page['items']] == ids[-1:-4:-1]
    assert page['total'] == 7 and page['counts'] == {ANCHOR: 7}
    rest = s.list('CH01', A, before=page['next_before'], limit=50)
    assert [n['id'] for n in rest['items']] == ids[3::-1]
    assert rest['next_before'] is None
    assert s.list('CH01', anchor='2'*64)['items'] == []


def test_rate_limit_keeps_existing_notes_and_allows_idempotent_retry(tmp_path):
    from aiforstudy.community.store import StoreError
    s = store(tmp_path / 'notes.db'); last = None
    for i in range(20):
        last = payload(text=str(i)); s.create('CH01', last, A)
    with pytest.raises(StoreError) as exc: s.create('CH01', payload(), A)
    assert exc.value.status == 429
    assert s.create('CH01', last, A)[1] is False
    assert s.list('CH01')['total'] == 20
