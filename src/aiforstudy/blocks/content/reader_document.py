"""One explicit reader-field registry projects both render and review inputs."""
from __future__ import annotations
import re
from .content_contracts import JsonRecord, LessonContentV4, as_dict, parse_content, content_digest, REFERENCE_KINDS
from .source_context import FrozenReferenceCatalog
from ...errors import ContractError
from ...core.review_mode import reviews_frozen

from .reader_fields import READER_FIELD_REGISTRY, _registry_tree, _leaves

# Display compatibility only: authored ANNOTATION markers name an existing
# annotation just as NOTE does. Keep the author contract and review gates intact.
REFERENCE_PATTERN = re.compile(
    r'\[\[(' + '|'.join((*REFERENCE_KINDS, 'ANNOTATION'))
    + r')\s*:\s*([^\]|]*?)\s*(?:\|\s*([^\]]*?)\s*)?\]\]')


class ReaderDocumentV1(JsonRecord):
    @property
    def digest(self): return content_digest(self)


def _annotation_label(kind):
    return {'note':'说明','intuition':'直觉','pitfall':'易错','derive':'补充推导'}.get(kind,'说明') if isinstance(kind,str) else '说明'


def _reader_math_ranges(text):
    """Match reader-markdown.js protectMath boundaries before prose merging.

    Inline dollars may span lines. Dollars in literal code must not steal a
    later formula's opening delimiter or make ordinary prose look like math.
    """
    code_ranges=[]; offset=0; fence=None; list_indent=None
    for line in text.splitlines(keepends=True):
        content=re.sub(r'^(?: {0,3}> ?)+','',line)
        item=re.match(r'^( *)(?:[-+*]|\d+[.)]) +',content)
        if item:
            list_indent=len(item[0]) if list_indent is None else min(list_indent,len(item[0]))
        elif re.match(r'^\S',content):
            list_indent=None
        marker=re.match(r'^\s*(`{3,}|~{3,})',content)
        if fence:
            code_ranges.append((offset,offset+len(line)))
            if marker and marker[1][0]==fence[0] and len(marker[1])>=len(fence): fence=None
        elif marker:
            fence=marker[1]; code_ranges.append((offset,offset+len(line)))
        elif not item and re.match(r'^(?: {4}|\t)',content) and (
                list_indent is None or len(re.match(r'^ *',content)[0])>=list_indent+4):
            code_ranges.append((offset,offset+len(line)))
        offset+=len(line)
    code_ranges.extend(match.span() for match in re.finditer(r'(`+)([\s\S]*?)\1',text))
    def in_code(index): return any(start<=index<end for start,end in code_ranges)
    ranges=[]; cursor=0
    opening=re.compile(r'\\\(|\\\[|(?<!\\)\$\$?')
    while match:=opening.search(text,cursor):
        start=match.start(); delimiter=match[0]; cursor=match.end()
        if in_code(start): continue
        closing={r'\(':r'\)',r'\[':r'\]'}.get(delimiter,delimiter)
        end=text.find(closing,cursor)
        if end<0 or in_code(end): continue
        cursor=end+len(closing); ranges.append((start,cursor))
    return ranges


def _project_text(raw, references):
    # Raw HTML remains visible literal prose. The dedicated reader Markdown
    # renderer escapes all HTML tokens; it never trusts source display controls.
    chars=[]; mapping=[]; spans=[]; markdown=[]; cursor=0
    math_ranges = _reader_math_ranges(raw)
    for match in REFERENCE_PATTERN.finditer(raw):
        key=(match.group(1),match.group(2).strip())
        if key not in references:
            if reviews_frozen() or key[0] == 'ANNOTATION':
                continue  # Preserve the literal marker and all surrounding prose.
            raise ContractError('unknown reader reference '+':'.join(key))
        ref=references[key]; label=match.group(3) if match.group(3) is not None else ref['label']
        before=raw[cursor:match.start()]; indices=list(range(cursor,match.start()))
        # A linked variable is an actual factor/term inside mathematics. The
        # prose-only name merge must not turn x[[CONCEPT:C|x]] into just x.
        in_math = any(start <= match.start() < end for start, end in math_ranges)
        if key[0]=='CONCEPT' and label and before.rstrip().endswith(label) and not in_math:
            keep=len(before.rstrip())-len(label); before=before[:keep]; indices=indices[:keep]
        chars.extend(before); mapping.extend(indices); markdown.append(before)
        start=len(chars); chars.extend(label)
        mapping.extend([match.start()]*len(label) if match.group(3) is None else range(match.start(3),match.start(3)+len(label)))
        spans.append(dict(visible_start=start,visible_end=len(chars),raw_start=match.start(),raw_end=match.end(),synthetic=match.group(3) is None))
        safe=label.replace('[',r'\[').replace(']',r'\]')
        markdown.append('['+safe+'](#'+ref['anchor']+')')
        cursor=match.end()
    chars.extend(raw[cursor:]); mapping.extend(range(cursor,len(raw))); markdown.append(raw[cursor:])
    return ''.join(chars),mapping,spans,''.join(markdown)


def _anchor(group, identity):
    return 'reader-'+content_digest({'group':group,'identity':identity}).split(':')[-1][:20]


def project_reader(content: LessonContentV4, reference_catalog: FrozenReferenceCatalog) -> ReaderDocumentV1:
    frozen=reviews_frozen()
    parsed=as_dict(content) if frozen else parse_content(as_dict(content))
    if not frozen and not isinstance(parsed,LessonContentV4): raise ContractError('project_reader requires LessonContentV4')
    catalog=FrozenReferenceCatalog.model_validate(as_dict(reference_catalog))
    c=as_dict(parsed); refs={}; blocks=[]
    if frozen:
        c={**c}
        for group in ('concepts','annotations','explainables','examples','exercise_regions','learning_checks'):
            if c.get(group) is None: c[group]=[]
    used_concepts={x['id'] for x in c['concepts']}
    for entry in catalog.entries:
        refs[(entry.kind,entry.id)]={'label':entry.label,'anchor':_anchor('references',entry.id)}
    for kind,group,label_field in [('NOTE','annotations','title'),('EXPLAIN','explainables','name'),('EXAMPLE','examples','label')]:
        for row in c[group]:
            if kind == 'NOTE':
                category = _annotation_label(row.get('kind'))
                title = row.get('title') or ''
                label = category + ('·' + title if title else '')
            else:
                label = row[label_field] or row['id']
            refs[(kind,row['id'])]={'label':label,'anchor':_anchor(group,row['id'])}
            if kind == 'NOTE' and sum(other['id'] == row['id'] for other in c[group]) == 1:
                refs[('ANNOTATION',row['id'])] = refs[(kind,row['id'])]
    for ident in used_concepts:
        if ('CONCEPT',ident) not in refs and not frozen: raise ContractError('missing frozen CONCEPT '+ident)

    # The finite frozen catalog bounds this closure. A visited set also makes
    # cross-chapter cycles harmless while retaining every reachable target.
    concept_entries = {entry.id: entry for entry in catalog.entries if entry.kind == 'CONCEPT'}
    if frozen:
        # An authored link already identifies its target; an optional concepts
        # list must not hide that target. Unknown IDs remain readable text.
        for pointer, _, _, raw, _ in _leaves(c, _registry_tree()):
            if pointer.startswith('/exercise_regions/'): continue
            used_concepts.update(match.group(2).strip() for match in REFERENCE_PATTERN.finditer(raw)
                if match.group(1)=='CONCEPT' and match.group(2).strip() in concept_entries)
    pending = list(used_concepts)
    while pending:
        entry = concept_entries.get(pending.pop())
        if entry is None and frozen:
            continue
        for raw in (entry.label, entry.markdown):
            for match in REFERENCE_PATTERN.finditer(raw):
                kind, ident = match.group(1), match.group(2).strip()
                if (kind, ident) not in refs:
                    if frozen or kind == 'ANNOTATION': continue
                    raise ContractError('unknown reader reference ' + kind + ':' + ident)
                if kind == 'CONCEPT' and ident not in used_concepts:
                    used_concepts.add(ident)
                    pending.append(ident)

    referenced_anchors = set()
    def add(pointer,identity,owner,raw,group,label,anchor=None):
        is_source = group == 'exercise_regions'
        visible,mapping,spans,markdown = (raw,list(range(len(raw))),[],raw) if is_source else _project_text(raw,refs)
        for match in (() if is_source else REFERENCE_PATTERN.finditer(raw)):
            reference=refs.get((match.group(1), match.group(2).strip()))
            if reference: referenced_anchors.add(reference['anchor'])
        step_id, step_no = '', None
        if group == 'annotations':
            label = _annotation_label(c['annotations'][int(pointer.split('/')[2])].get('kind'))
        step_match = re.fullmatch(r'/(examples|explainables)/(\d+)/steps/(\d+)/(markdown|basis)', pointer)
        if step_match:
            group_name, row_index, step_index, field = step_match.groups()
            step = c[group_name][int(row_index)]['steps'][int(step_index)]
            step_id = step['id']
            step_no = step.get('no') if step.get('no') is not None else int(step_index) + 1
            label = f'第 {step_no} 步' + ('依据' if field == 'basis' else '')
        blocks.append(dict(block_id=content_digest({'unit':c['unit_id'],'identity':identity}),owner_id=owner or c['unit_id'],
            json_pointer=pointer,raw_text=raw,visible_text=visible,source_map=mapping,marker_spans=spans,markdown=markdown,
            group=group,field_label=label,anchor=anchor or _anchor(group,owner or pointer),
            step_id=step_id,step_no=step_no,
            collapsed=group in ('annotations','explainables','examples','learning_checks','references','exercise_regions')))
        if group == 'explainables':
            explanation = c['explainables'][int(pointer.split('/')[2])]
            if explanation.get('kind') == 'theorem':
                # Presentation metadata only; source identity and the frozen
                # reference/source maps above remain untouched.
                blocks[-1].update(disclosure_kind='theorem', disclosure_part=(
                    'core' if pointer.rsplit('/', 1)[-1] in ('name', 'what', 'why')
                    and '/steps/' not in pointer else 'proof'))
        if is_source:
            region = c['exercise_regions'][int(pointer.split('/')[2])]
            if frozen:
                blocks[-1].update(source_lines=region.get('source_lines',[]),
                    source_sha256=region.get('source_sha256',''),source_kind=region.get('kind',''),raw_source=True)
            else:
                blocks[-1].update(source_lines=region['source_lines'], source_sha256=region['source_sha256'],
                                  source_kind=region['kind'], raw_source=True)
    for pointer,identity,owner,raw,label in _leaves(c, _registry_tree()):
        add(pointer,identity,owner,raw,pointer.split('/')[1],label)
    for i,entry in enumerate(catalog.entries):
        if entry.kind!='CONCEPT' or entry.id not in used_concepts: continue
        for key,label in [('label','概念'),('markdown','定义')]:
            raw=getattr(entry,key)
            if raw: add('/reference_catalog/entries/'+str(i)+'/'+key,'references/'+entry.id+'/'+key,entry.id,raw,'references',label)
    missing_targets = referenced_anchors - {block['anchor'] for block in blocks}
    if missing_targets and not frozen:
        raise ContractError('reader reference has no display target: ' + ', '.join(sorted(missing_targets)))
    from .learning_checks import project_learning_check_runtime
    runtime=[]
    for row in c['learning_checks']:
        try:
            runtime.append(project_learning_check_runtime(row))
        except (ContractError,ValueError,KeyError,TypeError):
            if not frozen: raise
            # The question/answer text remains in blocks; unavailable scoring
            # must not remove the lesson or invent a successful check.
    return ReaderDocumentV1(schema_version='ReaderDocumentV1',unit_id=c['unit_id'],content_hash=content_digest(c),
        learning_checks=runtime,
        reference_catalog_hash=catalog.digest,reference_catalog=catalog.as_dict(),blocks=blocks,
        required_block_ids=[b['block_id'] for b in blocks],field_registry_version='1.0')
