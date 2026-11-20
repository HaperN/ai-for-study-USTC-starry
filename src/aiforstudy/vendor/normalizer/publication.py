"""Publish existing instructional records verbatim, without generating mathematics."""
import re


def _image_syntax_mask(text):
    """Keep offsets while excluding fenced/inline code and HTML comments."""
    masked = list(text)
    fence = None
    offset = 0
    for line in text.splitlines(keepends=True):
        match = re.match(r' {0,3}(`{3,}|~{3,})', line)
        if fence or match:
            masked[offset:offset+len(line)] = ' ' * len(line)
            if fence and match and match[1][0] == fence[0] and len(match[1]) >= len(fence):
                fence = None
            elif not fence:
                fence = match[1]
        offset += len(line)
    visible = ''.join(masked)
    for match in re.finditer(r'<!--.*?-->|(`+)(?!`)(.*?)(?<!`)\1(?!`)', visible, re.S):
        masked[match.start():match.end()] = ' ' * (match.end()-match.start())
    return ''.join(masked)


def _image_unescape(value):
    from html import unescape
    return unescape(re.sub(r'\\([!"#$%&\x27()*+,\-./:;<=>?@\[\]\\^_`{|}~])', r'\1', value))


def _image_label(value):
    return ' '.join(_image_unescape(value).split()).casefold()


def _image_pair_end(text, start, left, right):
    depth, index = 1, start + 1
    quoted = None
    while index < len(text):
        if text[index] == '\\':
            index += 2
            continue
        if left == '(':
            if quoted:
                if text[index] == quoted:
                    quoted = None
                index += 1
                continue
            if text[index] == '<':
                quoted = '>'
                index += 1
                continue
            if text[index] in {'"', "'"} and text[index-1].isspace():
                quoted = text[index]
                index += 1
                continue
        if text[index] == left:
            depth += 1
        elif text[index] == right:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _image_destination(text, start, stop, registered_targets):
    raw = text[start:stop]
    start += len(raw) - len(raw.lstrip())
    stop -= len(raw) - len(raw.rstrip())
    if start >= stop:
        return None
    destination = text[start:stop]
    title = ''
    if text[start] == '<':
        end = text.find('>', start + 1, stop)
        if end < 0:
            return None
        target_start, target_end = start+1, end
        remainder = text[end+1:stop].strip()
    elif _image_unescape(destination) in registered_targets:
        target_start, target_end, remainder = start, stop, ''
    else:
        match = re.match(r'(?:\\.|[^\s])+', text[start:stop])
        if not match:
            return None
        target_start, target_end = start, start + match.end()
        remainder = text[target_end:stop].strip()
    if remainder:
        if len(remainder) < 2 or (remainder[0], remainder[-1]) not in {('"','"'), ("'","'"), ('(',')')}:
            return None
        title = _image_unescape(remainder[1:-1])
    return {'target': _image_unescape(text[target_start:target_end]),
            'target_start': target_start, 'target_end': target_end,
            'title': title, 'destination': destination}


def image_references(text, registered_targets=()):
    """Locate rendered Markdown/HTML images, including reference definitions.

    Offsets refer to the original string. Reference targets point into their
    definition; image start/end point at the use. Local/remote authorization is
    the caller's job. Registered paths retain the legacy bare-space spelling.
    """
    registered_targets = set(registered_targets)
    visible = _image_syntax_mask(text)
    definitions = {}
    for match in re.finditer(r'(?m)^ {0,3}\[([^\]\n]+)\]:[ \t]*(.*)$', visible):
        start, stop = match.start(2), match.end(2)
        following = re.match(r'\r?\n[ \t]+(["\x27(].+?["\x27)])[ \t]*(?=\r?\n|$)', visible[stop:])
        if following:
            stop += following.end()
        value = _image_destination(text, start, stop, registered_targets)
        if value:
            definitions.setdefault(_image_label(match[1]), value)
    found = []
    for match in re.finditer(r'!\[|<img\b(?:[^>"\x27]|"[^"]*"|\x27[^\x27]*\x27)*>', visible, re.I):
        start = match.start()
        preceding = start-1
        while preceding >= 0 and text[preceding] == '\\':
            preceding -= 1
        if (start-preceding-1) % 2:
            continue
        if match[0].lower().startswith('<img'):
            attrs = {}
            for attr in re.finditer(r'([\w:-]+)\s*=\s*(?:"([^"]*)"|\x27([^\x27]*)\x27|([^\s>]+))', match[0]):
                group = next(n for n in (2,3,4) if attr[n] is not None)
                attrs[attr[1].lower()] = (attr[group], start+attr.start(group), start+attr.end(group))
            if 'src' in attrs:
                raw, lo, hi = attrs['src']
                found.append({'kind':'html', 'start':start, 'end':match.end(),
                    'target':_image_unescape(raw), 'target_start':lo, 'target_end':hi,
                    'alt':_image_unescape(attrs.get('alt', ('',))[0]),
                    'title':_image_unescape(attrs.get('title', ('',))[0]), 'destination':raw})
            else:
                found.append({'kind':'html', 'start':start, 'end':match.end(),
                    'target':None, 'target_start':None, 'target_end':None,
                    'alt':_image_unescape(attrs.get('alt', ('',))[0]),
                    'title':_image_unescape(attrs.get('title', ('',))[0]), 'destination':None})
            continue
        close = _image_pair_end(visible, start+1, '[', ']')
        if close is None:
            continue
        alt, end = text[start+2:close], close+1
        value, kind = None, 'reference'
        if visible[end:end+1] == '(':
            final = _image_pair_end(visible, end, '(', ')')
            if final is not None:
                value = _image_destination(text, end+1, final, registered_targets)
                end, kind = final+1, 'inline'
        else:
            next_label = re.match(r'[ \t]*(?:\r?\n[ \t]*)?\[([^\]\n]*)\]', visible[end:])
            label = (next_label[1] or alt) if next_label else alt
            if next_label:
                end += next_label.end()
            value = definitions.get(_image_label(label))
        if value:
            found.append({**value, 'kind':kind, 'start':start, 'end':end, 'alt':alt})
    return found


def expand_reference_images(text):
    """Resolve each question's own references before concatenating questions."""
    for row in reversed(image_references(text)):
        if row['kind'] == 'reference':
            inline = '![' + row['alt'] + '](' + row['destination'] + ')'
            text = text[:row['start']] + inline + text[row['end']:]
    return text

TEXTBOOK_USAGE_GUIDE = [
    "# 怎样使用这份教材 {-}",
    "这份教材按问题推进连续阅读：先读章首路线图，了解前文解决了什么、留下哪些工具，"
    "以及当前要处理的新问题。进入小节后，结合白话和简单例图辨认对象与关系，"
    "再读准确陈述、构造思路和必要推导；右边框帮助回顾当前步骤所需的条件与旧知识。",
    "每幅解释性图后都有“这张图怎么读”的提示。读图时请结合正文辨认对象和条件；"
    "图示不能替代定义、完整证明或对一般情形的论证。章末复习思维导图只梳理知识推进与联系；课外拓展会明确列出需要"
    "另行掌握的内容、关键词和掌握标准。",
]


