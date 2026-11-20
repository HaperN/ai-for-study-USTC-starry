"""Durable public annotations with anonymous-browser deletion capability."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
import time
import uuid

from .identity import Principal


class StoreError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def author_digest(token: str | Principal, *, optional: bool = False) -> str:
    if isinstance(token, Principal):
        return token.storage_key
    if not token and optional:
        return ''
    if not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{64}', token):
        raise StoreError(401, '发布凭证无效，请在可保存数据的浏览器中重试。')
    return hashlib.sha256(token.encode('ascii')).hexdigest()


def bounded_text(value, name: str, maximum: int, *, multiline: bool = False) -> str:
    if not isinstance(value, str):
        raise StoreError(400, f'{name}必须是文字。')
    value = value.replace('\r\n', '\n').replace('\r', '\n').strip()
    allowed_controls = '\n\t' if multiline else ''
    if not 1 <= len(value) <= maximum or any(
            (ord(c) < 32 and c not in allowed_controls) or ord(c) == 127 for c in value):
        raise StoreError(400, f'{name}请填写 1–{maximum} 个有效字符。')
    return value


def validate_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise StoreError(400, '发布内容格式不正确。')
    anchor = payload.get('anchor')
    if not isinstance(anchor, str) or not re.fullmatch(r'[0-9a-f]{64}', anchor):
        raise StoreError(400, '教材位置无效，请重新选择原文。')
    if payload.get('kind') not in ('text', 'formula', 'media'):
        raise StoreError(400, '批注类型无效。')
    request_id = payload.get('request_id')
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', request_id):
        raise StoreError(400, '发布请求标识无效。')
    images = payload.get('images', [])
    if not isinstance(images, list) or len(images) > 3:
        raise StoreError(400, '每条批注最多添加 3 张图片。')
    refs = []
    for item in images:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not re.fullmatch(r'[a-f0-9]{32}', item['id']):
            raise StoreError(400, '图片标识无效。')
        mode = item.get('presentation', 'image')
        if mode not in ('image', 'recognized'):
            raise StoreError(400, '图片呈现方式无效。')
        refs.append({'id': item['id'], 'presentation': mode,
                     'confirmed': item.get('confirmed') is True if mode == 'recognized' else False})
    if len({r['id'] for r in refs}) != len(refs):
        raise StoreError(400, '不能重复添加同一张图片。')
    text = payload.get('text', '')
    if not isinstance(text, str): raise StoreError(400, '感悟必须是文字。')
    text = bounded_text(text, '感悟', 2000, multiline=True) if text.strip() else ''
    if not text and not refs: raise StoreError(400, '请写文字或添加图片。')
    return dict(anchor=anchor, kind=payload['kind'], request_id=request_id, images=refs,
                quote=bounded_text(payload.get('quote'), '原文摘录', 600, multiline=True),
                nickname=bounded_text(payload.get('nickname'), '昵称', 30),
                text=text)


class AnnotationStore:
    def __init__(self, path: Path, book_id: str, revision: str):
        self.path = Path(path)
        self.book_id, self.revision = str(book_id), str(revision)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS annotations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    book_id TEXT NOT NULL, revision TEXT NOT NULL,
                    unit_id TEXT NOT NULL, anchor TEXT NOT NULL, kind TEXT NOT NULL,
                    quote TEXT NOT NULL, nickname TEXT NOT NULL, text TEXT NOT NULL,
                    created_at REAL NOT NULL, author_hash TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    UNIQUE(book_id, revision, author_hash, request_id)
                );
                CREATE INDEX IF NOT EXISTS annotation_scope
                    ON annotations(book_id, revision, unit_id, id DESC);
                CREATE INDEX IF NOT EXISTS annotation_author_time
                    ON annotations(author_hash, created_at);
                CREATE TABLE IF NOT EXISTS annotation_images (
                    id TEXT PRIMARY KEY, book_id TEXT NOT NULL, revision TEXT NOT NULL,
                    unit_id TEXT NOT NULL, author_hash TEXT NOT NULL, request_id TEXT NOT NULL,
                    mime TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
                    bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, data BLOB NOT NULL,
                    created_at REAL NOT NULL, annotation_id INTEGER REFERENCES annotations(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL DEFAULT 0, presentation TEXT NOT NULL DEFAULT 'image',
                    accepted INTEGER NOT NULL DEFAULT 0, recognition_text TEXT,
                    recognition_engine TEXT, recognition_complete INTEGER NOT NULL DEFAULT 0,
                    recognition_truncated INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(book_id, revision, author_hash, request_id)
                );
                CREATE INDEX IF NOT EXISTS image_note ON annotation_images(annotation_id, ordinal);
                CREATE INDEX IF NOT EXISTS image_owner ON annotation_images(author_hash, created_at);
            ''')
            self._migrate(db)
        self.path.chmod(0o600)

    @staticmethod
    def _name_digest(name):
        return hashlib.sha256(name.encode('utf-8')).hexdigest()

    @staticmethod
    def _fingerprint(unit, value, images):
        fields = [unit, value['anchor'], value['kind'], value['quote'], value['text'], images]
        return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()

    def _migrate(self, db):
        db.execute('BEGIN IMMEDIATE')
        columns = {row['name'] for row in db.execute('PRAGMA table_info(annotations)')}
        if 'edit_revision' not in columns:
            db.execute('ALTER TABLE annotations ADD COLUMN edit_revision INTEGER NOT NULL DEFAULT 1')
        if 'updated_at' not in columns:
            db.execute('ALTER TABLE annotations ADD COLUMN updated_at REAL')
            db.execute('UPDATE annotations SET updated_at=created_at')
        db.execute("""CREATE TABLE IF NOT EXISTS annotation_requests (
            book_id TEXT, revision TEXT, author_hash TEXT, request_id TEXT,
            fingerprint TEXT NOT NULL, annotation_id INTEGER REFERENCES annotations(id) ON DELETE SET NULL,
            PRIMARY KEY(book_id,revision,author_hash,request_id))""")
        receipt_columns = {r['name'] for r in db.execute('PRAGMA table_info(annotation_requests)')}
        if 'nickname_digest' not in receipt_columns:
            db.execute('ALTER TABLE annotation_requests ADD COLUMN nickname_digest TEXT')
            # Existing live receipts can recover the immutable creation nickname.
            # Old tombstones deliberately no longer contain the author's nickname.
            for row in db.execute('SELECT r.rowid AS receipt,a.nickname FROM annotation_requests r JOIN annotations a ON a.id=r.annotation_id'):
                db.execute('UPDATE annotation_requests SET nickname_digest=? WHERE rowid=?',
                           (self._name_digest(row['nickname']), row['receipt']))
        for row in db.execute("""SELECT a.* FROM annotations a LEFT JOIN annotation_requests r
                USING(book_id,revision,author_hash,request_id) WHERE r.request_id IS NULL"""):
            images = [dict(id=r['id'], presentation=r['presentation'], confirmed=bool(r['accepted']))
                      for r in db.execute('SELECT * FROM annotation_images WHERE annotation_id=? ORDER BY ordinal', (row['id'],))]
            db.execute('INSERT INTO annotation_requests VALUES (?,?,?,?,?,?,?)',
                       (row['book_id'], row['revision'], row['author_hash'], row['request_id'],
                        self._fingerprint(row['unit_id'], row, images), row['id'], self._name_digest(row['nickname'])))

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _record(row, author: str, images=()):
        names = ('id', 'unit_id', 'anchor', 'kind', 'quote', 'nickname', 'text', 'created_at')
        return {**{name: row[name] for name in names},
                'revision': row['edit_revision'], 'updated_at': row['updated_at'],
                'is_mine': bool(author and secrets.compare_digest(row['author_hash'], author)), 'images': list(images)}

    def create(self, unit_id: str, payload: dict, token: str | Principal, *, validate_target=None) -> tuple[dict, bool]:
        author = author_digest(token)
        server_name = token.display_name if isinstance(token, Principal) else None
        supplied = dict(payload, nickname=server_name) if server_name is not None and isinstance(payload, dict) else payload
        value = validate_payload(supplied)
        refs = value.pop('images')
        unit_id = bounded_text(unit_id, '教学单元', 100)
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            scope = (self.book_id, self.revision, author, value['request_id'])
            fingerprint = self._fingerprint(unit_id, value, refs)
            request = db.execute("""SELECT * FROM annotation_requests
                WHERE book_id=? AND revision=? AND author_hash=? AND request_id=?""", scope).fetchone()
            if request:
                nickname_changed = (server_name is None and request['nickname_digest'] is not None
                                    and request['nickname_digest'] != self._name_digest(value['nickname']))
                if fingerprint != request['fingerprint'] or nickname_changed:
                    raise StoreError(409, '这次发布标识已用于另一条内容，请重新发布。')
                if request['annotation_id'] is None:
                    raise StoreError(410, '这条感悟已删除。')
                existing = db.execute('SELECT * FROM annotations WHERE id=?', (request['annotation_id'],)).fetchone()
                return self._record(existing, author, self._attached(db, existing['id'])), False
            if validate_target is not None:
                validate_target(unit_id, payload)
            for ref in refs:
                image = self._owned_image(db, ref['id'], author, pending=True)
                if image['unit_id'] != unit_id: raise StoreError(400, '图片属于另一个教学单元。')
                if ref['presentation'] == 'recognized' and (not ref['confirmed'] or not image['recognition_complete'] or image['recognition_truncated']):
                    raise StoreError(400, '请先完成识别并核对结果，或选择直接图片。')
            now = time.time()
            count = db.execute('SELECT COUNT(*) FROM annotations WHERE author_hash=? AND created_at>?',
                               (author, now - 60)).fetchone()[0]
            if count >= 20:
                raise StoreError(429, '发布得有些频繁，请稍等一分钟再试。')
            cursor = db.execute('''INSERT INTO annotations
                (book_id, revision, unit_id, anchor, kind, quote, nickname, text,
                 created_at, updated_at, author_hash, request_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                (self.book_id, self.revision, unit_id, value['anchor'], value['kind'],
                 value['quote'], value['nickname'], value['text'], now, now, author, value['request_id']))
            db.execute('INSERT INTO annotation_requests VALUES (?,?,?,?,?,?,?)', (*scope, fingerprint, cursor.lastrowid, self._name_digest(value['nickname'])))
            for ordinal, ref in enumerate(refs):
                db.execute('UPDATE annotation_images SET annotation_id=?,ordinal=?,presentation=?,accepted=? WHERE id=?',
                           (cursor.lastrowid, ordinal, ref['presentation'], int(ref['confirmed']), ref['id']))
            row = db.execute('SELECT * FROM annotations WHERE id=?', (cursor.lastrowid,)).fetchone()
            return self._record(row, author, self._attached(db, row['id'])), True

    def list(self, unit_id: str, token: str | Principal = '', *, anchor: str | None = None,
             before: int | None = None, limit: int = 50, targets: dict | None = None) -> dict:
        author = author_digest(token, optional=True)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise StoreError(400, '每页数量必须在 1–50 之间。')
        if before is not None and (type(before) is not int or not 1 <= before <= 2**63-1):
            raise StoreError(400, '分页位置无效。')
        if anchor is not None and (not isinstance(anchor, str) or not re.fullmatch(r'[0-9a-f]{64}', anchor)):
            raise StoreError(400, '教材位置无效。')
        scope = 'book_id=? AND revision=? AND unit_id=?'
        args = [self.book_id, self.revision, unit_id]
        with self._connection() as db:
            db.execute('BEGIN')
            counts = {}
            unit_total = 0
            groups = db.execute(f"""SELECT anchor, kind, quote, COUNT(*) AS n FROM annotations
                WHERE {scope} GROUP BY anchor, kind, quote""", args)
            for row in groups:
                unit_total += row['n']
                key = row['anchor']
                if targets is not None:
                    target = targets.get(key)
                    if not target or any(row[k] != target[k] for k in ('kind', 'quote')):
                        continue
                counts[key] = counts.get(key, 0) + row['n']
            total = unit_total
            query, selected = scope, list(args)
            if anchor is not None:
                total = counts.get(anchor, 0)
                query += ' AND anchor=?'
                selected.append(anchor)
                if targets is not None:
                    target = targets.get(anchor)
                    if target is None:
                        return dict(items=[], counts=counts, total=0, unit_total=unit_total, next_before=None)
                    query += ' AND kind=? AND quote=?'
                    selected.extend((target['kind'], target['quote']))
            if before is not None:
                query += ' AND id<?'; selected.append(before)
            rows = db.execute(f'SELECT * FROM annotations WHERE {query} ORDER BY id DESC LIMIT ?',
                              [*selected, limit + 1]).fetchall()
            more = len(rows) > limit
            rows = rows[:limit]
            return dict(items=[self._record(r, author, self._attached(db, r['id'])) for r in rows], counts=counts, total=total, unit_total=unit_total,
                        next_before=rows[-1]['id'] if more else None)

    @staticmethod
    def _image_metadata(row):
        return {key: row[key] for key in ('id', 'mime', 'width', 'height', 'bytes')} | {
            'url': '/api/annotation-images/' + row['id']}

    def _attached(self, db, note_id):
        rows = db.execute('SELECT id,mime,width,height,bytes,presentation,accepted,recognition_text FROM annotation_images WHERE annotation_id=? ORDER BY ordinal', (note_id,))
        result = []
        for row in rows:
            item = self._image_metadata(row)
            item['presentation'] = row['presentation']
            if row['presentation'] == 'recognized' and row['accepted']:
                item['recognized_text'] = row['recognition_text']
            result.append(item)
        return result

    def _owned_image(self, db, image_id, author, *, pending=False, allow_expired=False):
        row = db.execute('SELECT * FROM annotation_images WHERE id=? AND book_id=? AND revision=?',
                         (image_id, self.book_id, self.revision)).fetchone()
        if row is None or not secrets.compare_digest(row['author_hash'], author):
            raise StoreError(404, '图片不存在或不属于当前作者。')
        if pending and row['annotation_id'] is not None:
            raise StoreError(409, '该图片已发布，请操作整条批注。')
        if pending and not allow_expired and row['created_at'] < time.time() - 86400:
            raise StoreError(400, '未发布的图片已过期，请重新添加。')
        return row

    def upload_image(self, unit_id, image, request_id, token):
        author = author_digest(token)
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', request_id):
            raise StoreError(400, '上传请求标识无效。')
        unit_id = bounded_text(unit_id, '教学单元', 100)
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM annotation_images WHERE annotation_id IS NULL AND created_at<?', (time.time()-86400,))
            old = db.execute('SELECT * FROM annotation_images WHERE book_id=? AND revision=? AND author_hash=? AND request_id=?',
                             (self.book_id,self.revision,author,request_id)).fetchone()
            if old:
                if old['sha256'] != image.sha256 or old['unit_id'] != unit_id:
                    raise StoreError(409, '这次上传标识已用于另一张图片。')
                return self._image_metadata(old)
            count = db.execute('SELECT COUNT(*) FROM annotation_images WHERE author_hash=? AND book_id=? AND revision=? AND annotation_id IS NULL', (author,self.book_id,self.revision)).fetchone()[0]
            if count >= 12: raise StoreError(429, '待发布图片较多，请先发布或移除已有图片。')
            ident = uuid.uuid4().hex
            db.execute('INSERT INTO annotation_images (id,book_id,revision,unit_id,author_hash,request_id,mime,width,height,bytes,sha256,data,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (ident,self.book_id,self.revision,unit_id,author,request_id,image.mime,image.width,image.height,len(image.data),image.sha256,image.data,time.time()))
            return self._image_metadata(db.execute('SELECT * FROM annotation_images WHERE id=?', (ident,)).fetchone())

    def image(self, image_id, token=''):
        author = author_digest(token, optional=True)
        with self._connection() as db:
            row = db.execute('SELECT * FROM annotation_images WHERE id=? AND book_id=? AND revision=?', (image_id,self.book_id,self.revision)).fetchone()
            if row is None or (row['annotation_id'] is None and (row['created_at'] < time.time()-86400 or not author or not secrets.compare_digest(row['author_hash'],author))):
                raise StoreError(404, '图片不存在。')
            return self._image_metadata(row) | {'data': bytes(row['data'])}

    def pending_images(self, token):
        author = author_digest(token)
        with self._connection() as db:
            db.execute('DELETE FROM annotation_images WHERE annotation_id IS NULL AND created_at<?', (time.time()-86400,))
            rows = db.execute('SELECT id,mime,width,height,bytes,unit_id,created_at FROM annotation_images WHERE book_id=? AND revision=? AND author_hash=? AND annotation_id IS NULL ORDER BY created_at DESC',
                              (self.book_id,self.revision,author))
            return [self._image_metadata(r) | {'unit_id':r['unit_id'],'created_at':r['created_at']} for r in rows]

    def recognition(self, image_id, token):
        author = author_digest(token)
        with self._connection() as db:
            row = self._owned_image(db, image_id, author, pending=True)
            if row['recognition_text'] is None: return None
            return {'text': row['recognition_text'], 'engine': row['recognition_engine'],
                    'truncated': bool(row['recognition_truncated']), 'requires_confirmation': True}

    def save_recognition(self, image_id, token, result):
        author = author_digest(token)
        if not isinstance(result, dict) or type(result.get('truncated')) is not bool:
            raise StoreError(503, '识别结果格式无效，请使用图片方式。')
        text = bounded_text(result.get('text'), '识别文本', 20_000, multiline=True)
        engine = bounded_text(result.get('engine'), '识别来源', 256)
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._owned_image(db, image_id, author, pending=True)
            if row['recognition_text'] is None:
                db.execute('UPDATE annotation_images SET recognition_text=?,recognition_engine=?,recognition_complete=1,recognition_truncated=? WHERE id=?',
                           (text,engine,int(result['truncated']),image_id))
            else:
                text,engine = row['recognition_text'],row['recognition_engine']
                result = {'truncated': bool(row['recognition_truncated'])}
            return {'text': text, 'engine': engine, 'truncated': result['truncated'], 'requires_confirmation': True}

    def remove_image(self, image_id, token):
        author = author_digest(token)
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self._owned_image(db,image_id,author,pending=True,allow_expired=True)
            db.execute('DELETE FROM annotation_images WHERE id=?', (image_id,))

    def edit(self, note_id, payload, token):
        author = author_digest(token)
        if type(note_id) is not int or not 1 <= note_id <= 2**63-1:
            raise StoreError(400, '感悟编号无效。')
        if set(payload) != {'text', 'revision'} or type(payload['revision']) is not int or payload['revision'] < 1:
            raise StoreError(400, '请提交通知中的版本号和修改后的文字。')
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM annotations WHERE id=? AND book_id=? AND revision=?',
                             (note_id, self.book_id, self.revision)).fetchone()
            if row is None:
                raise StoreError(404, '这条感悟已不存在。')
            if not secrets.compare_digest(row['author_hash'], author):
                raise StoreError(403, '只能编辑自己的感悟。')
            if row['edit_revision'] != payload['revision']:
                raise StoreError(409, '感悟已被修改，请核对最新内容后再保存。')
            images = self._attached(db, note_id)
            text = payload['text']
            if images and isinstance(text, str) and not text.strip():
                text = ''
            else:
                text = bounded_text(text, '感悟', 2000, multiline=True)
            db.execute('UPDATE annotations SET text=?, edit_revision=edit_revision+1, updated_at=? WHERE id=?',
                       (text, time.time(), note_id))
            return self._record(db.execute('SELECT * FROM annotations WHERE id=?', (note_id,)).fetchone(), author, images)

    def delete(self, note_id: int, token: str | Principal) -> bool:
        author = author_digest(token)
        if type(note_id) is not int or not 1 <= note_id <= 2**63-1:
            raise StoreError(400, '感悟编号无效。')
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT author_hash FROM annotations WHERE id=? AND book_id=? AND revision=?',
                             (note_id, self.book_id, self.revision)).fetchone()
            if row is None:
                raise StoreError(404, '这条感悟已不存在。')
            if not secrets.compare_digest(row['author_hash'], author):
                raise StoreError(403, '只能删除这个浏览器发布的感悟。')
            db.execute('DELETE FROM annotations WHERE id=?', (note_id,))
            return True
