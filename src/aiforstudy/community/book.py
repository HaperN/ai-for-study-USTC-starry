"""Annotation source coordinates and the exact scripts served for this session."""
from collections import Counter
from contextlib import ExitStack
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from ..core.digest import digest_of
from ..blocks.render.layout import lex_reader_documents
from ..errors import ContractError
from .store import StoreError


BOOK = 'data/data.js'
PRACTICE = 'assets/practice-data.js'
MEDIA = 'assets/practice-media-data.js'


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _assignment(raw, variable):
    text = raw.decode('utf-8-sig')
    start = re.match(r'(?:\s|/\*.*?\*/)*window\.' + re.escape(variable) + r'\s*=\s*', text, re.S)
    if not start:
        raise ValueError(f'缺少教材数据 {variable}')
    value, end = json.JSONDecoder().raw_decode(text, start.end())
    if not re.fullmatch(r'(?:\s|;|//[^\n]*|/\*.*?\*/)*', text[end:], re.S):
        raise ValueError(f'教材数据 {variable} 格式不正确')
    return value


def _source(path, *, read=False):
    """Open each path component relative to a pinned directory, without following links."""
    directories = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
    try:
        with ExitStack() as stack:
            parent = os.open(path.anchor, directories)
            stack.callback(os.close, parent)
            for part in path.parts[1:-1]:
                parent = os.open(part, directories, dir_fd=parent)
                stack.callback(os.close, parent)
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            stack.callback(os.close, fd)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('教材来源必须是普通文件。')
            signature = info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
            if not read:
                return signature, None
            with os.fdopen(fd, 'rb', closefd=False) as source:
                return signature, source.read()
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ValueError('教材来源路径含有符号链接或无效目录。') from exc
        raise


class BookSnapshot:
    def __init__(self, reader, practice=None):
        self.reader = reader
        self.raw = {}
        self.paths = [reader / name for name in (BOOK, PRACTICE, MEDIA)]
        if practice is not None:
            practice = Path(os.path.abspath(practice))
            self.paths.extend(practice / '教材' / name for name in (PRACTICE, MEDIA))
        if _source(reader / 'index.html')[0] is None:
            raise ValueError('请选择教材 book 目录。')
        self.signature = self.signatures()
        captured = {}
        for path, expected in zip(self.paths, self.signature, strict=True):
            if path in captured:
                continue
            actual, captured[path] = _source(path, read=True)
            if actual != expected:
                raise StoreError(409, '教材文件已更新，请重启服务并刷新页面后再继续。')
        for name in (BOOK, PRACTICE, MEDIA):
            raw = captured[reader / name]
            if raw is not None:
                self.raw[name] = raw
            if practice is not None and name != BOOK:
                other = captured[practice / '教材' / name]
                if name == PRACTICE and other is None:
                    raise ValueError('练习目录缺少教材内嵌练习数据。')
                if other != raw:
                    raise ValueError('练习目录与实际提供的教材练习数据不一致。')
        self.check()
        if BOOK not in self.raw:
            raise ValueError('请选择包含 data/data.js 的教材。')
        data = _assignment(self.raw[BOOK], '__DATA__')
        book, scope = data.get('book'), data.get('scope')
        if (not isinstance(book, str) or not 1 <= len(book) <= 128 or not isinstance(scope, list)
                or not 1 <= len(scope) <= 10000 or any(not isinstance(u, str) or not re.fullmatch(r'[\w-]{1,100}', u, re.ASCII) for u in scope)):
            raise ValueError('教材缺少有效 book/scope。')
        self.context = dict(book=book, revision=_digest(self.raw[BOOK]), units=scope)
        units = data.get('units')
        if not isinstance(units, dict) or any(not isinstance(units.get(uid), dict) for uid in scope):
            raise ValueError('教材缺少单元数据。')
        self.targets = {uid: {} for uid in scope}
        documents = [units[uid].get('reader_document', {}).get('blocks', []) for uid in scope]
        for uid, fields in zip(scope, documents):
            if not isinstance(fields, list) or any(not isinstance(f, dict) for f in fields):
                raise ValueError(f'教材来源字段格式无效：{uid}')
            for key in ('block_id', 'json_pointer'):
                ids = [f.get(key) for f in fields]
                if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
                    raise ValueError(f'教材来源字段缺失或重复：{uid}')
            if any(not isinstance(f.get(k), str) for f in fields for k in ('raw_text', 'markdown')):
                raise ValueError(f'教材来源或显示投影缺失：{uid}')
        try:
            tokenized = lex_reader_documents(documents, excerpts=True)
        except ContractError as exc:
            raise ValueError(f'无法验证教材来源投影：{exc}') from exc
        for uid, blocks in zip(scope, tokenized, strict=True):
            self._reader_targets(uid, units[uid], blocks)
        if PRACTICE in self.raw:
            layers = [(name, _digest(self.raw[name])) for name in (PRACTICE, MEDIA) if name in self.raw]
            self.context['practice_revision'] = _digest(_json(layers).encode())
            self._practice_targets()
        self.check()

    def signatures(self):
        return tuple(_source(path)[0] for path in self.paths)

    def check(self):
        try:
            current = self.signatures()
        except (ValueError, OSError) as exc:
            raise StoreError(409, '教材来源路径发生变化，请重启服务并刷新页面。') from exc
        if current != self.signature:
            raise StoreError(409, '教材文件已更新，请重启服务并刷新页面后再继续。')

    def _add(self, uid, attr, ident, quote, kind='text', *, identity=None, layer=''):
        quote = quote.replace('\r\n', '\n').replace('\r', '\n').strip()
        if not quote:
            return
        anchor = _digest((identity or _json([uid, attr, ident, layer])).encode())
        target = dict(anchor=anchor, kind=kind, quote=quote[:600].rstrip(), locator=dict(attribute=attr, value=ident))
        if uid not in self.targets:
            raise ValueError(f'教材位置缺少单元：{uid} {ident}')
        existing = self.targets[uid].setdefault(anchor, target)
        if any(existing[k] != v for k, v in target.items()):
            raise ValueError(f'教材位置冲突：{uid} {ident}')
        return existing

    def _reader_targets(self, uid, unit, tokens):
        fields = {f['block_id']: f for f in unit.get('reader_document', {}).get('blocks', [])}
        seen = Counter()
        field_hashes = {}
        integrated = {}
        pending = None
        statements = set()
        lesson = unit.get('study_lesson') or {}
        results = {item['id'] for key in ('conclusions', 'supplementary_conclusions') for item in lesson.get(key, [])}
        layout = unit.get('reader_layout', {}).get('blocks', [])
        if len(tokens) != len(layout):
            raise ValueError(f'教材来源块数量失配：{uid}')
        for token, block in zip(tokens, layout):
            # Compare against the browser's entire projection, not only the whole-field hash.
            if any(block.get(k) != v for k, v in token.items() if k != 'excerpt'):
                raise ValueError(f'教材来源布局与显示投影失配：{uid}')
            field = fields[token['field_id']]
            if field.get('raw_source') or field.get('group') == 'title' or field.get('json_pointer') == '/title':
                continue
            raw = field['raw_text']
            if 'field_hash' in block:
                field_id = field['block_id']
                if field_id not in field_hashes:
                    field_hashes[field_id] = digest_of(raw)
                if block['field_hash'] != field_hashes[field_id]:
                    raise ValueError(f'教材来源字段指纹失配：{uid}')
            a, b = block['source_start_cp'], block['source_end_cp']
            if type(a) is not int or type(b) is not int or not 0 <= a < b <= len(raw):
                raise ValueError(f'教材来源区间失配：{uid}')
            quote = token['excerpt']
            opening = re.fullmatch(r'\[\[TEXTBOOK_INTEGRATED:([^\]]+)\]\]', quote)
            if opening:
                if pending:
                    raise ValueError(f'教材来源结论区间嵌套：{uid}')
                ids = opening[1].split('|')
                if not results.issuperset(ids):
                    raise ValueError(f'教材来源结论标识失配：{uid}')
                pending = (field['block_id'], ids, [])
                continue
            if quote == '[[TEXTBOOK_END_INTEGRATED]]':
                if not pending or pending[0] != field['block_id'] or not pending[2]:
                    raise ValueError(f'教材来源结论区间失配：{uid}')
                for ident in pending[1]:
                    integrated.setdefault(ident, '\n\n'.join(pending[2]))
                pending = None
                continue
            statement = re.fullmatch(r'\[\[TEXTBOOK_RESULT:([^\]]+)\]\]', quote)
            if statement:
                statements.add(statement[1])
            if re.fullmatch(r'\[\[[A-Z_]+(?::[^\]]+)?\]\]', quote):
                continue
            if pending:
                if pending[0] != field['block_id']:
                    raise ValueError(f'教材来源结论跨字段：{uid}')
                pending[2].append(quote)
            math = quote.startswith(('$$', r'\[')) and quote.endswith(('$$', r'\]'))
            legacy = None
            if block.get('token_type') == 'paragraph' and not re.search(r'[\\$`*_<>\[\]#&~]', quote):
                # Plain paragraphs have the same normalized text in the old DOM scheme.
                base = _json([uid, 'text', field['json_pointer'], ' '.join(quote.split())])
                legacy = _json([base, seen[base]])
                seen[base] += 1
            target = self._add(uid, 'data-shared-anchor', token['block_id'], quote,
                               'formula' if math else 'text', identity=legacy)
            if target:
                target['source'] = dict(field=token['field_id'], start_cp=a, end_cp=b)
        if pending:
            raise ValueError(f'教材来源结论缺少结束标记：{uid}')
        for key, attr in [('conclusions', 'data-textbook-result'), ('supplementary_conclusions', 'data-textbook-supplement')]:
            for item in lesson.get(key, []):
                ident = item['id']
                if ident in integrated:
                    self._add(uid, attr, ident, integrated[ident])
                elif ident in statements or key == 'supplementary_conclusions':
                    self._add(uid, attr, ident, item.get('statement_markdown', ''))
        for key in ('visuals', 'media'):
            for item in lesson.get(key, []):
                ident = item['id']
                legacy = _json([_json([uid, 'media', ident, '']), 0])
                self._add(uid, 'data-teaching-id', ident, item.get('title') or item.get('model_title') or ident,
                          'media', identity=legacy)

    def _practice_targets(self):
        revision = self.context['practice_revision']
        questions = {}
        for edition, rows in _assignment(self.raw[PRACTICE], 'PRACTICE_EDITIONS').items():
            for q in rows:
                units = [q.get('mount_unit_id') or q['unit_id']]
                units.extend(m['unit_id'] for m in q.get('additional_mounts', []))
                for uid in dict.fromkeys(units):
                    if uid not in self.targets:
                        raise ValueError(f'练习挂载单元不存在：{uid}')
                    target = self._add(uid, 'data-practice-id', q['id'], q['stem'], layer=revision)
                    if not target:
                        raise ValueError(f'练习缺少题干：{uid}')
                    editions = target.setdefault('editions', [])
                    if edition not in editions:
                        editions.append(edition)
                    questions.setdefault(q['id'], {})[uid] = editions
        for item in _assignment(self.raw[MEDIA], 'PRACTICE_MEDIA') if MEDIA in self.raw else ():
            for uid, editions in questions.get(item['question_id'], {}).items():
                target = self._add(uid, 'data-practice-media-id', item['id'], item['title'], 'media', layer=revision)
                if target:
                    target['editions'] = editions

    def validate(self, uid, payload):
        key = payload.get('anchor')
        target = self.targets[uid].get(key) if isinstance(key, str) else None
        if target is None or any(target[k] != payload.get(k) for k in ('kind', 'quote')):
            raise StoreError(400, '教材位置或引文与原文不一致，请重新选择。')
