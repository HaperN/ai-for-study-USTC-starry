"""Frozen inputs and source-derived obligations, independent of candidate authors."""
from __future__ import annotations
import re
from typing import Any, Literal
from pydantic import Field, ConfigDict, model_validator
from .content_contracts import StrictContentModel, SourceRefV1, content_digest, as_dict
from ...errors import ContractError
from ...core.source_boundaries import MATERIAL_HEADING, TEACHING_BOUNDARY, source_material_spans


class ReferenceEntryV1(StrictContentModel):
    id: str
    kind: Literal['CONCEPT','UNIT']
    label: str
    version: str
    unit_id: str = ''
    markdown: str = ''


class FrozenReferenceCatalog(StrictContentModel):
    schema_version: Literal['FrozenReferenceCatalogV1'] = 'FrozenReferenceCatalogV1'
    entries: list[ReferenceEntryV1] = Field(default_factory=list)

    @property
    def digest(self): return content_digest(self)

    @model_validator(mode='after')
    def unique(self):
        keys=[(x.kind,x.id) for x in self.entries]
        if len(keys)!=len(set(keys)): raise ValueError('duplicate reference identity')
        return self


class LearnerProfileV1(StrictContentModel):
    schema_version: Literal['LearnerProfileV1'] = 'LearnerProfileV1'
    profile_id: str = 'unspecified'
    description: str = ''
    assumed_prerequisite_ids: list[str] = Field(default_factory=list)
    discipline: str = ''
    level: str = ''
    language: str = 'zh-CN'


class SourceExampleV1(StrictContentModel):
    id: str
    label: str
    statement: str
    book_solution: str
    source_line: int


class SourceObligationV1(StrictContentModel):
    id: str
    kind: Literal['definition','theorem','procedure','example','convention']
    level: Literal['core','support'] = 'core'
    concept_id: str = ''
    conditions: list[str] = Field(default_factory=list)
    conclusion: str = ''
    source_excerpt: str
    source_refs: list[SourceRefV1]
    extraction_method: Literal['concept_index','source_heading','source_example','semantic']


class SourceContext(StrictContentModel):
    schema_version: Literal['SourceContextV1'] = 'SourceContextV1'
    book_id: str
    unit_id: str
    source_text: str
    original_text: str
    original_sha256: str
    source_inputs: dict[str,str]
    video_evidence: list[dict[str,Any]] = Field(default_factory=list)
    # Exact frozen upstream documents: intentionally opaque, not author containers.
    unit: dict[str,Any]
    chapters: dict[str,Any]
    concept_index: dict[str,Any]
    reference_catalog: FrozenReferenceCatalog
    learner_profile: LearnerProfileV1
    source_examples: list[SourceExampleV1]
    source_obligations: list[SourceObligationV1]
    source_context_hash: str = ''
    obligation_extraction_status: Literal['structural_only','semantic_verified'] = 'structural_only'

    @classmethod
    def model_validate(cls, obj, *args, **kwargs):
        from ...core.review_mode import reviews_frozen
        if reviews_frozen():
            from .json_only import source_context_record
            return source_context_record(obj)
        return super().model_validate(obj, *args, **kwargs)

    @property
    def digest(self):
        data=self.as_dict(); data.pop('source_context_hash',None)
        if not data.get('video_evidence'): data.pop('video_evidence',None)
        return content_digest(data)


EXAMPLE_START = re.compile(r'(?:\*\*)?例[ \t]*(\d+(?:[.．\-－—]\d+)*)(?=(?:\*\*)?(?:\s|[.．、:：]|$))')
EXAMPLE_BOUNDARY = TEACHING_BOUNDARY
SOLUTION_MARK = re.compile(r'(?:^|\n|(?<=[。.!?！？;；$]))[ \t]*(?:\*\*)?(解|证明|证)(?:\*\*)?[ \t]*[.．:：]?[ \t]+')


def _example_boundary(line):
    return EXAMPLE_BOUNDARY.match(line) or MATERIAL_HEADING.match(line)


def _balance_math_delimiters(text):
    out=str(text or '').rstrip()
    if out.count('$$')%2: return out+'$$'
    if out.replace('$$','').count('$')%2: return out+'$'
    return out


def _split_example(chunk):
    math=_math_spans(chunk)
    marker=next((m for m in SOLUTION_MARK.finditer(chunk)
                 if not any(start<=m.start(1)<end for start,end in math)),None)
    if marker: return _balance_math_delimiters(chunk[:marker.start()].strip()),chunk[marker.start():].strip()
    blocks=list(re.finditer(r'\$\$.*?\$\$',chunk,re.S))
    if blocks:
        # A complete task sentence before the first display is an explicit
        # boundary (e.g. "例3 求实部、虚部及模. $$推导$$").
        before=chunk[:blocks[0].start()].rstrip()
        if before.endswith(('。','.','！','!','？','?')):
            return before,chunk[blocks[0].start():].strip()
        # Retain the established displayed transformation-chain layout. Without
        # a marker or a display-only chain, preserve the full source unseparated.
        tail=chunk[blocks[0].end():]
        if '\\xrightarrow' not in blocks[0].group() and (len(blocks)<2 or re.sub(r'\$\$.*?\$\$','',tail,flags=re.S).strip()):
            return chunk,''
        end=blocks[0].end(); head,tail=chunk[:end],chunk[end:]
        arrow=head.find('\\xrightarrow')
        if arrow>0:
            head,tail=head[:arrow].rstrip(),head[arrow:]+tail
            head=_balance_math_delimiters(head)
        return head.strip(),tail.strip()
    return chunk,''


def _math_spans(text):
    spans=[]; start=None; delimiter=None
    for token in re.finditer(r'(?<!\\)(?:\$\$|\$)',text):
        if start is None: start,delimiter=token.start(),token.group()
        elif token.group()==delimiter:
            spans.append((start,token.end())); start=delimiter=None
    if start is not None: spans.append((start,len(text)))
    return spans


def _label_boundary(text,start):
    line=text[text.rfind('\n',0,start)+1:start]
    return bool(re.fullmatch(r'[ \t]*(?:#{1,6}[ \t]*)?',line)
                or text[:start].rstrip().endswith(('。','.','！','!','？','?','；',';','$')))


def _source_example_spans(original):
    """Exact character spans: OCR line merging never creates synthetic lines."""
    text=str(original or ''); math=_math_spans(text); materials=source_material_spans(text)
    outside=lambda start: not any(left<=start<right for left,right in math+materials)
    starts=[m for m in EXAMPLE_START.finditer(text) if _label_boundary(text,m.start()) and outside(m.start())]
    boundaries=[]; offset=0
    for line in text.splitlines(keepends=True):
        if _example_boundary(line) or re.match(r'^\s*#{1,6}\s',line): boundaries.append(offset)
        offset+=len(line)
    # A named teaching block can also start inside a MinerU-merged line.
    for match in re.finditer(r'(?:\*\*)?(?:定义|定理|命题|推论|引理|注)[ \t]*\d+',text):
        if _label_boundary(text,match.start()) and outside(match.start()): boundaries.append(match.start())
    rows=[]
    for order,match in enumerate(starts,1):
        start=match.start(); end=starts[order].start() if order<len(starts) else len(text)
        end=min([end]+[boundary for boundary in boundaries if start<boundary<end])
        while end>start and text[end-1].isspace(): end-=1
        statement,solution=_split_example(text[start:end])
        row=dict(id=f'E-{order:02d}',label=f'例{match.group(1)}',statement=statement,
                 book_solution=solution,source_line=text.count('\n',0,start)+1)
        rows.append((row,start,end))
    return rows


def extract_source_examples(original: str) -> list[dict]:
    return [row for row,_,_ in _source_example_spans(original)]


def reference_catalog_from_context(context) -> FrozenReferenceCatalog:
    rows=[]
    # 修改计划 E4：每条的 version 是它自身内容的摘要，而不是本单元来源摘要。
    # 这样各单元的目录前缀相同（可命中缓存前缀），只有真正改了的条目才换 version。
    for c in (context.get('concept_index') or {}).get('concepts',[]):
        version=content_digest({key:c.get(key) for key in ('id','name','formal','definition')})
        rows.append(dict(id=str(c['id']),kind='CONCEPT',label=str(c.get('name') or c['id']),version=version,
            unit_id=str(c.get('first_unit') or c.get('unit_id') or ''), markdown=str(c.get('formal') or c.get('definition') or '')))
    for unit in (context.get('chapters') or {}).get('lesson_units',[]):
        version=content_digest({'id':unit['unit_id'],'title':unit.get('title')})
        rows.append(dict(id=unit['unit_id'],kind='UNIT',label=unit.get('title') or unit['unit_id'],version=version,unit_id=unit['unit_id']))
    return FrozenReferenceCatalog(entries=rows)


def derive_source_obligations(context) -> list[SourceObligationV1]:
    """Structural inventory only. Semantic extraction may add validated obligations;
    callers must not interpret this inventory as exhaustive semantic verification.
    """
    text=context['source_text']; unit=context['unit_id']; rows=[]
    material_spans=source_material_spans(text)
    def add(identity,kind,excerpt,method,concept='',conditions=None,conclusion='',span=None):
        if not excerpt: raise ContractError('source obligation lacks frozen excerpt '+identity)
        start,end=span if span is not None else (text.find(excerpt),text.find(excerpt)+len(excerpt))
        if start<0 or text[start:end]!=excerpt: raise ContractError('source obligation quotation absent from frozen source '+identity)
        rows.append(SourceObligationV1(id='SO-'+content_digest({'unit':unit,'identity':identity}).split(':')[-1][:20],kind=kind,
            concept_id=concept,conditions=conditions or [],conclusion=conclusion,source_excerpt=excerpt,
            source_refs=[SourceRefV1(kind='textbook',unit_id=unit,path=context['original_text'],quote=excerpt,start=start,end=end,sha256=context['original_sha256'])],extraction_method=method))
    kind_map={'定义':'definition','定理':'theorem','命题':'theorem','推论':'theorem','引理':'theorem','方法':'procedure','算法':'procedure','记号':'convention'}
    for c in (context.get('concept_index') or {}).get('concepts',[]):
        if c.get('first_unit')!=unit and c.get('role_here')!='introduced': continue
        kind=c.get('kind') or c.get('type') or 'definition'; kind=kind_map.get(kind,kind)
        if kind not in ('definition','theorem','procedure','convention'): kind='definition'
        formal=c.get('formal') or c.get('definition') or ''
        # Keep the actual source when an index paraphrase is not a literal quote.
        excerpt=formal if isinstance(formal,str) and formal and formal in text else text
        conditions=c.get('conditions') or []
        if isinstance(conditions,str): conditions=[conditions]
        add('concept:'+c['id'],kind,excerpt,'concept_index',c['id'],conditions,str(formal))
    headings=list(re.finditer(r'(?m)^\s*(?:#{1,6}\s*|\*\*)?(定义|定理|命题|推论|引理|方法|算法|记号)\s*[^\n]*',text))
    headings=[heading for heading in headings if not any(start<=heading.start(1)<end for start,end in material_spans)]
    material_starts=[]
    offset=0
    for line in text.splitlines(keepends=True):
        if MATERIAL_HEADING.match(line): material_starts.append(offset)
        offset+=len(line)
    for i,m in enumerate(headings):
        end=headings[i+1].start() if i+1<len(headings) else len(text)
        end=min([end]+[start for start in material_starts if m.start()<start<end])
        excerpt=text[m.start():end].strip()
        add('heading:'+str(m.start()),kind_map[m.group(1)],excerpt,'source_heading')
    spans=_source_example_spans(text)
    for example in context.get('source_examples') or [row for row,_,_ in spans]:
        matches=[(row,start,end) for row,start,end in spans if all(row[key]==example[key]
                 for key in ('label','source_line','statement','book_solution'))]
        # Host-generated order IDs disambiguate byte-identical examples sharing
        # a line; preserve both positions instead of text.find's first match.
        if len(matches)>1: matches=[span for span in matches if span[0]['id']==example['id']]
        if len(matches)!=1: raise ContractError('source example has no unique exact source span: '+example['id'])
        _,start,end=matches[0]
        add('example:'+example['id'],'example',text[start:end],'source_example',span=(start,end))
    return rows


def source_context_from_frozen(context, *, learner_profile=None) -> SourceContext:
    profile=learner_profile or context.get('learner_profile') or LearnerProfileV1()
    obj=SourceContext(**{k:context[k] for k in ('book_id','unit_id','source_text','original_text','original_sha256','source_inputs','unit','chapters','concept_index')},
        video_evidence=context.get('video_evidence') or [], reference_catalog=reference_catalog_from_context(context),learner_profile=profile,
        source_examples=context.get('source_examples') or extract_source_examples(context['source_text']),source_obligations=derive_source_obligations(context))
    return obj.model_copy(update={'source_context_hash':obj.digest})
