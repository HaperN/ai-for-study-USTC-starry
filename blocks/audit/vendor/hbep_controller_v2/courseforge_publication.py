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


def check_generated_usage_guide(row, text, pieces, root):
    """Check the publisher's exact navigation promises without a math call."""
    if compact(row['text']) != compact('\n\n'.join(TEXTBOOK_USAGE_GUIDE)):
        return None
    if any(marker in text for marker in ('\\includegraphics', '<img')):
        return None  # Do not claim coverage of an unparsed image representation.
    sections = {}
    for piece in pieces:
        if piece.get('subsection_id'):
            sections.setdefault(piece['subsection_id'], []).append(piece['text'])
    missing = ['start-here:' + key for key, bodies in sections.items()
               if not any('::: start-here' in body for body in bodies)]
    images = image_references(text)
    for image in images:
        if not re.match(r'\s*(?:图[^\n]+\n\s*)?::: figure-reading\b', text[image['end']:]):
            missing.append('figure-reading:' + str(image['start']))
    diagrams = list(re.finditer(r'\\begin\{tikzpicture\}.*?\\end\{tikzpicture\}', text, re.S))
    for diagram in diagrams:
        tail = re.sub(r'^\s*\\end\{center\}', '', text[diagram.end():], count=1).lstrip()
        caption = tail.split('\n\n', 1)[0]
        if not (caption.startswith('::: figure-reading') or
                (caption.startswith(('图', '补充图')) and
                 ('读图' in caption or re.search(r'[:：].+[。；].+', caption)))):
            return None  # Its actual mathematical reading guide needs inspection.
        # Legacy TikZ captions can contain the complete reading explanation
        # without the modern wrapper/title. Here only its presence is checked;
        # the diagram and explanation retain their existing semantic audit.
    import json
    from pathlib import Path
    from courseforge_content import normalize_content
    mother_dir = Path(root)/'content/mother'/Path(row['file']).stem
    extensions = [extension for path in mother_dir.glob('*.json')
                  for extension in normalize_content(json.loads(path.read_text(encoding='utf-8-sig')))['extensions']]
    for extension in extensions:
        for field in ('mastery_aspects', 'search_keywords', 'mastery_criteria'):
            if not extension.get(field):
                missing.append(str(extension.get('extension_id')) + ':' + field)
    # The plan's mandatory publication-retention check binds these actual
    # extension fields to published text; their semantics stay in local reviews.
    return {'passed': not missing, 'missing': missing,
            'subsections': len(sections), 'images': len(images) + len(diagrams), 'extensions': len(extensions)}


def compact(text):
    # Comparison only. Never apply this normalization to published mathematics.
    return re.sub(r'\s+', '', str(text))


def _student_key(value):
    """Ignore prose wrapping while retaining every byte inside TeX math."""
    text=str(value)
    def prose_key(part):
        # A bare TeX control word also needs its separator from a following
        # ASCII letter. Chinese line wrapping still carries no extra content.
        def whitespace(match):
            adjacent=part[match.start()-1:match.start()]+part[match.end():match.end()+1]
            return ' ' if len(adjacent)==2 and adjacent.isascii() and adjacent.isalpha() else ''
        return re.sub(r'\s+',whitespace,part)
    def unescaped(index):
        preceding=index-1
        while preceding>=0 and text[preceding]=='\\':
            preceding-=1
        return (index-preceding-1)%2==0
    parts=[]; cursor=0; index=0
    while index<len(text):
        pair=next(((left,right) for left,right in
                   (('$$','$$'),('$','$'),(r'\(',r'\)'),(r'\[',r'\]'))
                   if text.startswith(left,index) and unescaped(index)),None)
        if pair is None:
            index+=1
            continue
        left,right=pair
        end=text.find(right,index+len(left))
        while end>=0 and not unescaped(end):
            end=text.find(right,end+len(right))
        # Unclosed math is not permission to erase its command boundaries.
        stop=len(text) if end<0 else end+len(right)
        parts.extend((prose_key(text[cursor:index]),text[index:stop]))
        cursor=index=stop
    parts.append(prose_key(text[cursor:]))
    return ''.join(parts)


FIELD_LABELS={'title':'主题','learning_purpose':'学习目标','intuition':'直观理解',
 'prerequisite_questions':'前置知识','relations':'知识联系','rigor_checks':'条件与边界',
 'conditions':'适用条件','conclusion':'结论','proof_strategy':'证明路线',
 'assumption_usage':'条件的作用','verification':'核验','body':'补充说明',
 'recognition_signals':'识别线索','method_selection':'何时使用','condition_checks':'使用前检查',
 'problem':'问题','solution':'完整解答','condition_check':'条件检查',
 'caption':'图题','reading_guide':'读图说明','why_self_study':'补充学习的原因',
 'mastery_aspects':'应掌握的内容','search_keywords':'检索线索','mastery_criteria':'掌握标准',
 'relation_to_textbook':'与正文的联系','student_text':'学习说明','verified_content':'已核验内容',
 'topic':'学习主题','why_needed':'为什么需要','specific_mastery_targets':'具体目标',
 'use_point':'使用位置','suggested_resource_type':'资料类型','correction':'纠正方法'}


def restore_mapped_textbook_fields(narrative, mother):
    from courseforge_content import student_records
    normalized, rows=student_records(mother)
    seen=_student_key(narrative)
    extras=[]
    for row in rows:
        if row['volume']!='textbook': continue
        key=_student_key(row['text'])
        if key in seen: continue
        label=FIELD_LABELS[row['field']]
        prefix = ''
        if row['collection'] == 'theorems' and row['field'] == 'conclusion':
            theorem = next(t for t in normalized['theorems']
                           if t.get('theorem_id', t.get('id')) == row['record_id'])
            prefix = '**对应定理：** ' + str(theorem.get('title') or row['record_id']) + '\n\n'
            conditions = theorem.get('conditions', [])
            if conditions:
                prefix += '**适用条件：** ' + ('；'.join(conditions) if isinstance(conditions, list) else str(conditions)) + '\n\n'
        extras.append(prefix+'**'+label+'：** '+row['text'])
        seen+=key
    if extras: narrative+='\n\n#### 补充理解与核对 {-}\n\n'+'\n\n'.join(extras)
    # Publish navigation only to exercises and prerequisite questions already
    # present in this mother. Do not invent extra training or outside sources.
    section_ids=normalized.get('metadata',{}).get('knowledge_point_ids',[])
    if normalized.get('exercises') and section_ids and '**对应训练：**' not in narrative:
        narrative+='\n\n#### 对应训练与按需补基础 {-}\n\n**对应训练：** 同步习题册 '+ '、'.join('§'+str(x) for x in section_ids)+' 的习题、答案与详细解析。'
        prior=[]
        for record in normalized.get('knowledge_points',[]):
            values=record.get('prerequisite_questions',[])
            if isinstance(values,str): values=[values]
            for value in values:
                if isinstance(value,str) and value.strip() and value not in prior: prior.append(value)
        if prior:
            narrative+='\n\n**按需补基础：** 如果以下前置问题还不能解释清楚，先回看所列小节，再继续当前训练：\n\n'+'\n'.join('- '+x for x in prior)
    return narrative


def _bound_student_record(collection, identity, body):
    # Invisible boundaries bind actual output, not an unverified manifest claim.
    # Hex avoids allowing an ID to inject a closing HTML comment.
    token = str(identity).encode('utf-8').hex()
    return [f'<!-- cf-record:{collection}:{token} -->', *body,
            f'<!-- /cf-record:{collection}:{token} -->']


def render_worked_example_fields(example):
    body = [f"#### 小例子：{str(example['title']).strip()} {{-}}",
            '**问题：** ' + str(example['problem']).strip(),
            '**先检查条件：** ' + str(example['condition_check']).strip(),
            '**完整解答：** ' + str(example['solution']).strip(),
            '**回头核验：** ' + str(example['verification']).strip()]
    return _bound_student_record('worked_examples', example['example_id'], body)


def render_exercise_fields(exercise, problem):
    lines=[]
    conditions=[str(v).strip() for v in exercise['known_conditions'] if _student_key(v) not in _student_key(problem)]
    if conditions: lines.append('**已知条件：** '+'；'.join(conditions))
    if _student_key(exercise['goal']) not in _student_key(problem): lines.append('**目标：** '+exercise['goal'])
    lines.extend([problem,'**答案：** '+str(exercise['answer']).strip()])
    labels={'condition_check':'条件与定义域检查','full_reasoning':'完整解法','key_steps':'关键步骤',
            'verification':'验证','common_errors':'常见错误'}
    solution=exercise['solution']
    if set(solution)-set(labels): raise ValueError('Unmapped solution fields: '+str(sorted(set(solution)-set(labels))))
    for field,label in labels.items():
        if field not in solution: continue
        value=solution[field]
        text='；'.join(str(v) for v in value) if isinstance(value,list) else str(value)
        if text.strip(): lines.append('**'+label+'：** '+text.strip())
    signals=exercise.get('recognition_signals',[])
    missing=[str(v) for v in signals if _student_key(v) not in _student_key('\n'.join(lines))]
    if missing: lines.append('**识别线索：** '+'；'.join(missing))
    return _bound_student_record('exercises', exercise['exercise_id'], lines)


ROUTE_READING_GUIDE = '按小节编号定位正文。实线组织图中的树形布局，不单独表示数学依赖；虚线提示跨节点的知识关系，具体起止、方向和条件见图后关系列表。曲线交叉本身不代表节点或新的关系。路线图不能替代正文的定义与证明。'


def map_relation_notes(source):
    import json
    from pathlib import Path
    data_path = Path(source).parent.parent/'src/知识树数据.json'
    if not data_path.is_file(): return ''
    payload=json.loads(data_path.read_text(encoding='utf-8-sig'))
    if payload.get('relation_label_mode')!='listed': return ''
    names={}
    def walk(node):
        names[node['data']['uid']]=node['data']['text']
        for child in node['children']: walk(child)
    walk(payload['root'])
    lines=[f"- R{i}：{names[row['from']]} → {names[row['to']]}；{row['type']}。{row['label']}"
           for i,row in enumerate(payload['staticRelations'],1)]
    return '\n\n**图中知识关系（方向以此表为准）：**\n\n'+'\n'.join(lines) if lines else ''


def generated_finding_owner(root, finding):
    """Identify publisher-owned content without assigning it to a mother."""
    import json
    from pathlib import Path
    root = Path(root)
    chapter = Path(finding['file']).stem
    if not re.fullmatch(r'CH\d+', chapter): return None
    quote = finding['quote']
    if finding.get('category') in {'公式排版与内容截断','公式可读性','跨页断裂','关键图示缺失'}:
        file=Path(finding['file']); body=file.read_text(encoding='utf-8-sig')
        start,end=finding.get('start'),finding.get('end')
        if (file.is_relative_to(root/'content') and type(start) is int and type(end) is int
                and body[start:end]==quote):
            from stem_runner.publisher import _plain_formula_ranges, _align_figure_reading_intro
            fix=None
            if finding['category'] in {'公式排版与内容截断','公式可读性'} and _plain_formula_ranges(quote):
                fix='Protect unchanged bare exponent/subscript tokens in Math nodes before Markdown interpretation'
            elif finding['category']=='跨页断裂' and re.search(r'####\s+(?:完整证明|小例子)',quote):
                fix='Keep the existing proof/example heading with its first substantive step'
            elif finding['category']=='关键图示缺失' and _align_figure_reading_intro(body)!=body:
                fix='Place the already published image beside its first exact caption and reading guide'
            if fix: return {'kind':'map_publication','chapter':chapter,'repair':fix}
    if (Path(finding['file']).parent.name == 'exercise-book'
            and finding.get('category') == '内容截断与公式不可读'
            and '已知条件' in finding.get('reason','')
            and re.search(r'越过.*边界|被裁|裁掉',finding.get('reason',''))):
        body = Path(finding['file']).read_text(encoding='utf-8-sig')
        start, end = finding.get('start'), finding.get('end')
        if type(start) is int and type(end) is int and body[start:end] == quote:
            lo = body.rfind('\n\n',0,start) + 2
            hi = body.find('\n\n',start)
            paragraph = body[lo:hi if hi >= 0 else len(body)]
            if paragraph.startswith('**已知条件：** ') and '；' in paragraph:
                return {'kind':'map_publication','chapter':chapter,
                        'repair':'Break existing condition separators in the PDF AST; retain manuscript and mathematical nodes'}
    if quote.startswith('**结论：** ') and finding.get('category') != 'mathematical_correctness':
        body = Path(finding['file']).read_text(encoding='utf-8-sig')
        start, end = finding.get('start'), finding.get('end')
        if type(start) is int and type(end) is int and body[start:end] == quote:
            headers = list(re.finditer(r'^## (\d+)\.(\d+)\.(\d+) [^\n]+$', body[:start], re.M))
            if headers and '#### 补充理解与核对' in body[headers[-1].end():start]:
                c, s, ss = map(int, headers[-1].groups())
                mother_path = root/'content/mother'/chapter/f'CH{c:02d}-S{s:02d}-SS{ss:02d}.json'
                if mother_path.is_file():
                    from courseforge_content import normalize_content
                    mother = normalize_content(json.loads(mother_path.read_text(encoding='utf-8-sig')))
                    conclusions = {'**结论：** '+t['conclusion'] for t in mother['theorems']
                                   if t.get('conclusion') and t.get('conditions') and t.get('body')}
                    paragraphs = [p for p in quote.split('\n\n') if p.strip()]
                    if paragraphs and all(p in conclusions for p in paragraphs):
                        return {'kind':'map_publication','chapter':chapter,
                                'repair':'Restore theorem identity and existing conditions beside supplemental conclusions'}
    old_guide = '先按图中标明的阅读顺序查看各节点，再分别核对箭头连接的知识联系；箭头不必逐对连接相邻节点。'
    reverse_guide = '复习时从结论反向追到所需条件和前置概念；任何追不回去的节点都应回到正文补读。'
    if quote==reverse_guide:
        source=Path(finding['file']);body=source.read_text(encoding='utf-8-sig')
        start,end=finding.get('start'),finding.get('end')
        if (source.parent==root/'content/textbook' and type(start) is int and type(end) is int
                and body[start:end]==quote):
            return {'kind':'map_publication','chapter':chapter}
    if quote in (old_guide, ROUTE_READING_GUIDE) or re.fullmatch(r'!\[[^\]]*\]\(assets/mind-map-CH\d+-[^)]*\)\{[^}]*\}', quote):
        return {'kind':'map_publication','chapter':chapter}
    path = root/'work/knowledge-architecture'/f'{chapter}.json'
    if path.is_file():
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if any(c['chapter_id']==chapter and quote in c['grand_narrative'] for c in data['chapters']):
            return {'kind':'chapter_narrative','chapter':chapter}
        # A prepared correction can precede reassembly. The saved finding then
        # still quotes the old student artifact; retain its generated owner
        # only when the exact approved local replacement is in the new builder.
        errata = root/'data/architecture-errata.json'
        if errata.is_file():
            for row in json.loads(errata.read_text(encoding='utf-8-sig')):
                replacement = row.get('replacement')
                if (row.get('chapter')==chapter
                        and row.get('finding_quote',row.get('quote'))==quote
                        and quote in row.get('quote','')
                        and isinstance(replacement,str) and replacement.strip()
                        and any(c['chapter_id']==chapter and replacement in c['grand_narrative']
                                for c in data['chapters'])):
                    return {'kind':'chapter_narrative','chapter':chapter}
    return None


def check_publication(mothers, texts):
    from courseforge_content import student_records
    rows=[]; missing=[]
    # Comments cannot substitute for visible student content.
    normalized={key:_student_key(re.sub(r'<!--.*?-->', '', value, flags=re.S))
                for key,value in texts.items()}
    bound = {}
    boundaries = re.compile(r'<!-- cf-record:([^:]+):([0-9a-f]+) -->(.*?)'
                            r'<!-- /cf-record:\1:\2 -->', re.S)
    for volume, text in texts.items():
        for match in boundaries.finditer(text):
            key=(volume, match[1], match[2])
            bound.setdefault(key, []).append(match[3])
    expected_ids = set()
    for mother in mothers:
        canonical,records=student_records(mother)
        role_checks = {}
        for collection in ('exercises', 'worked_examples'):
            for item in canonical[collection]:
                identity=item['exercise_id' if collection=='exercises' else 'example_id']
                volume='exercise-book' if collection=='exercises' else 'textbook'
                key=(volume, collection, str(identity).encode('utf-8').hex())
                duplicates=key in expected_ids
                expected_ids.add(key)
                actual=bound.get(key, [])
                body=actual[0] if len(actual)==1 else ''
                # The image itself is checked by the publisher/visual gate.
                # Here replace only that exact record's staged figure expansion
                # with its source placeholder before checking unchanged roles.
                for figure in canonical['figures']:
                    if not figure.get('publish_sha256') or not figure.get('placeholder'):
                        continue
                    image=(r'!\[\]\(assets/' + re.escape(str(figure['publish_sha256'])) +
                           r'\.png\)\{[^}\n]*\}\s*图：' + re.escape(str(figure['caption']).strip()))
                    body=re.sub(image, lambda _:str(figure['placeholder']), body)
                rendered=(render_exercise_fields(item, str(item['problem']).strip())
                          if collection=='exercises' else render_worked_example_fields(item))
                expected='\n\n'.join(rendered[1:-1])
                # Renderer output is deterministic here. Preserve whitespace
                # within TeX: ``\sin x`` and ``\sinx`` are different commands.
                # Only transport line endings and the boundary's outer padding
                # may differ; neither changes a mathematical token boundary.
                actual_text = body.replace('\r\n', '\n').strip()
                expected_text = expected.replace('\r\n', '\n').strip()
                # Instructional restoration may prepend already-authored prose
                # before an example heading. Its complete labelled example must
                # still occur once at the end of this exact record boundary.
                matches=(actual_text==expected_text if collection=='exercises' else
                         actual_text.endswith(expected_text) and actual_text.count(expected_text)==1)
                passed=(len(actual)==1 and not duplicates and matches)
                role_checks[(collection,identity)]=(passed, len(actual))
        for record in records:
            text=_student_key(record['text']); position=normalized[record['volume']].find(text)
            row={key:value for key,value in record.items() if key!='text'}
            role_check=role_checks.get((record['collection'],record['record_id']))
            written=role_check[0] if role_check is not None else position>=0
            row.update(written=written, normalized_text_offset=position)
            if role_check is not None:
                row.update(binding='record_and_rendered_roles', occurrences=role_check[1])
                if not written:
                    row['reason']='missing_or_duplicate_record_binding_or_changed_role_content'
            rows.append(row)
            if not written: missing.append(row)
    return {'passed':not missing,'scope':'mother_student_fields_to_publication',
            'written':len(rows)-len(missing),'unhandled':missing,'records':rows,
            'mathematical_and_teaching_sufficiency':'checked_by_existing_semantic_audit'}


def check_delivery_retention(project, scope, sources, publish_reports):
    """Bind the canonical content map to the actual assembled sources and PDFs.

    PDF text probes check prose retention, not mathematical equivalence. Formula
    rendering and images remain mandatory checks in the existing release audit.
    """
    import json
    from pathlib import Path
    from pypdf import PdfReader
    from courseforge_content import student_records
    root = Path(project)
    assembly = json.loads((root/'state'/f'assembly-{scope}.json').read_text(encoding='utf-8-sig'))
    mothers = [json.loads(Path(item['path']).read_text(encoding='utf-8-sig'))
               for item in assembly.get('mother_artifacts', [])]
    def role(key):
        value=key.replace('_','-')
        return 'exercise-book' if value == 'exercise' else value
    texts = {role(key):Path(path).read_text(encoding='utf-8-sig') for key,path in sources.items()}
    result = check_publication(mothers, texts)
    unavailable = [key for key,report in publish_reports.items()
                   if not report.get('pdf') or not Path(report['pdf']).is_file()]
    if unavailable:
        result.update(passed=False, pdf_prose_missing=[{'volume':key,'reason':'publisher did not produce a PDF'}
                      for key in unavailable], pdf_prose_locations=[])
        return result
    def body_pages(report):
        from collections import Counter
        # A fraction in the running title can span several extracted lines.
        # Identify that margin text from this PDF's actual TeX geometry, rather
        # than leaving a denominator between prose split across two pages.
        tex_path = Path(report['pdf']).with_suffix('.tex')
        tex = tex_path.read_text(encoding='utf-8') if tex_path.is_file() else ''
        geometry = re.search(r'\\usepackage\[([^\]]+)\]\{geometry\}', tex)
        top = re.search(r'(?:^|,)\s*top=(\d+(?:\.\d+)?)mm(?:,|$)', geometry[1]) if geometry else None
        lines = []; margin_headers = []
        for page in PdfReader(report['pdf']).pages:
            header = []; position = [0.0]
            boundary = float(page.mediabox.top) - float(top[1])*72/25.4 if top else None
            def collect_margin(text, cm, tm, font, size):
                # pypdf emits inferred spaces with an identity text matrix.
                if tm[4] or tm[5]:
                    position[0] = tm[4]*cm[1] + tm[5]*cm[3] + cm[5]
                if boundary is not None and position[0] > boundary:
                    header.append(text)
            lines.append((page.extract_text(visitor_text=collect_margin) or '').splitlines())
            margin_headers.append(compact(''.join(header)))
        headers = Counter(rows[0] for rows in lines if rows)
        def running_title(line):
            parts = re.split(r'\s+§\d+(?:\.\d+)*\s', line, maxsplit=1)
            return parts[0] if len(parts) == 2 and len(parts[0]) >= 12 else None
        running_titles = Counter(running_title(rows[0]) for rows in lines if rows)
        bodies=[]
        for rows, margin_header in zip(lines, margin_headers):
            # The fixed template repeats running headers and page numbers.
            # Remove those only from comparison, so prose crossing a page break
            # remains contiguous; the PDF itself is never rewritten here.
            raw_body = compact('\n'.join(rows[:-1] if rows and rows[-1].strip().isdigit() else rows))
            if margin_header and raw_body.startswith(margin_header):
                bodies.append(raw_body[len(margin_header):])
                continue
            if rows and (headers[rows[0]]>1
                    or (running_title(rows[0]) is not None and running_titles[running_title(rows[0])] > 1)
                    or re.match(r'^\d+(?:\.\d+)+\s+\d+(?:\.\d+)+\s',rows[0])):
                rows=rows[1:]
            if rows and rows[-1].strip().isdigit(): rows=rows[:-1]
            bodies.append(compact('\n'.join(rows)))
        return bodies
    pages = {role(key):body_pages(report) for key,report in publish_reports.items()}
    missing = []; located = []
    for mother in mothers:
        _, records = student_records(mother)
        for record in records:
            # Compare text, never rewrite a formula to make extraction match.
            probes = re.findall(r'[\u4e00-\u9fff]{8,}', record['text'])
            joined = ''.join(pages[record['volume']])
            for probe in probes:
                found = [n+1 for n,text in enumerate(pages[record['volume']]) if probe in text]
                row = {k:record[k] for k in ('record_id','field','item','volume')}
                row.update(text=probe, pages=found)
                if probe not in joined: missing.append(row)
                else: located.append(row)
    result.update(passed=result['passed'] and not missing,
                  pdf_prose_missing=missing, pdf_prose_locations=located,
                  formula_and_image_validation='existing_publisher_and_visual_checks_required')
    return result


def restore_existing_fields(narrative, mother):
    seen = _student_key(narrative)
    def missing(value):
        nonlocal seen
        text = str(value).strip()
        key = _student_key(text)
        if not key or key in seen:
            return ''
        seen += key
        return text
    def labeled(label, value):
        values = value if isinstance(value, list) else [value]
        remaining = [text for v in values if (text := missing(v))]
        return '**' + label + '：** ' + '；'.join(remaining) if remaining else ''

    opening = []
    for point in mother['knowledge_points']:
        for label, field in [('本节要解决什么','learning_purpose'), ('先有一个直观印象','intuition'),
                             ('带着旧知识进入本节','prerequisite_questions')]:
            text = labeled(label, point[field])
            if text: opening.append(text)
    if opening:
        start = re.search(r'::: start-here\b[^\n]*\n.*?^:::\s*$', narrative, re.M|re.S)
        pos = start.end() if start else (narrative.find('\n') + 1)
        narrative = narrative[:pos] + '\n\n' + '\n\n'.join(opening) + '\n\n' + narrative[pos:]

    records = [(row,kind) for kind in ('explanations','methods') for row in mother[kind]]
    records.sort(key=lambda pair: int(pair[0]['source_order']))
    extra = []
    for row, kind in records:
        parts = []
        text = missing(row['body'])
        if text: parts.append(text)
        if kind == 'methods':
            for label, field in [('什么时候用','method_selection'), ('识别线索','recognition_signals'),
                                 ('使用前检查','condition_checks')]:
                text = labeled(label, row[field])
                if text: parts.append(text)
        if parts:
            extra.append('#### ' + str(row['title']) + ' {-}\n\n' + '\n\n'.join(parts))
    if extra:
        example = re.search(r'^#### 小例子：', narrative, re.M)
        pos = example.start() if example else len(narrative)
        narrative = narrative[:pos] + '\n\n' + '\n\n'.join(extra) + '\n\n' + narrative[pos:]

    closing = []
    for error in mother['common_errors']:
        text = missing(error['body'])
        if text: closing.append('**' + str(error.get('title') or '易错提醒') + '：** ' + text)
        correction = labeled('纠正方法', error.get('correction',''))
        if correction: closing.append(correction)
    for point in mother['knowledge_points']:
        for label, field in [('条件与边界核对','rigor_checks'), ('这节知识怎样接下去','relations')]:
            text = labeled(label, point[field])
            if text: closing.append(text)
    if closing:
        narrative += '\n\n#### 易错点与使用边界 {-}\n\n' + '\n\n'.join(closing)
    return narrative
