"""Derive complete syntax-block landings using the browser's local Marked lexer."""
from __future__ import annotations
from collections import defaultdict
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
from ...core.digest import digest_file, digest_of
from ...errors import ContractError
from ...core.review_mode import reviews_frozen


def _assets(workspace_root=None):
    package = Path(__file__).resolve().parents[2]
    candidates = [package/'_legacy_tree/blocks/render', Path(__file__).resolve().parents[4]/'blocks/render']
    if workspace_root is not None:
        candidates.append(Path(workspace_root)/'blocks/render')
    for root in candidates:
        if (root/'assets/reader-markdown.js').is_file() and (root/'vendor/marked.min.js').is_file():
            return root
    raise ContractError('ReaderLayout local Markdown assets missing')


def markdown_engine_hash(workspace_root=None):
    root = _assets(workspace_root)
    return digest_of({name:digest_file(root/name) for name in ('assets/reader-markdown.js','vendor/marked.min.js')})


def lex_reader_documents(documents, workspace_root=None, *, excerpts=False):
    """Tokenize JSON data using the packaged browser lexer, once for the whole batch."""
    root = _assets(workspace_root)
    node = os.environ.get('AIFS_BROWSER_NODE') or shutil.which('node')
    if not node:
        raise ContractError('ReaderLayout requires Node.js on PATH or AIFS_BROWSER_NODE')
    probe = Path(__file__).with_name('reader_layout_probe.cjs')
    try:
        result = subprocess.run(
            [node, str(probe), str(root/'assets/reader-markdown.js'), str(root/'vendor/marked.min.js')],
            input=json.dumps({'documents': documents, 'excerpts': excerpts}, ensure_ascii=False),
            capture_output=True, text=True, encoding='utf-8', timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContractError('ReaderLayout could not run Node.js; check AIFS_BROWSER_NODE: ' + str(exc)) from exc
    if result.returncode:
        raise ContractError('ReaderLayout Markdown probe: ' + result.stderr.strip()[:500])
    return json.loads(result.stdout)['documents']


def _blocks(reader_document, workspace_root=None):
    blocks, = lex_reader_documents([reader_document['blocks']], workspace_root)
    fields={b['json_pointer']:b for b in reader_document['blocks']}
    for block in blocks: block['field_hash']=digest_of(fields[block['target_pointer']]['raw_text'])
    return blocks


def _fail(row, reason):
    raise ContractError(f"{row.get('target_pointer','?')} source_id={row.get('source_id','?')}: {reason}")


def usable_location_span(raw, span=None, anchor=None):
    """Keep an available location; an unusable hint falls back without refusal."""
    valid=(isinstance(span,list) and len(span)==2 and all(type(n)==int for n in span)
           and 0<=span[0]<span[1]<=len(raw))
    if valid:
        quote=raw[span[0]:span[1]]
        if quote.strip() and (not isinstance(anchor,str) or not anchor or anchor==quote):
            return list(span)
    if isinstance(anchor,str) and anchor.strip():
        start=raw.find(anchor)
        if start>=0 and raw.find(anchor,start+1)<0:
            return [start,start+len(anchor)]
    return [0,len(raw)]


def _resolve(reader_document, mounts, blocks):
    fields={b['json_pointer']:b for b in reader_document['blocks']}; result=[]; seen=set(); counts=defaultdict(int)
    # Stable partition implements the approved media-then-goal ordering.
    for row in sorted(mounts,key=lambda m: m.get('source_kind')=='interaction'):
        field=fields.get(row.get('target_pointer'))
        if reviews_frozen():
            field=field or fields.get('/body_markdown') or next(iter(fields.values()),None)
            if field is None: continue
            pointer=field['json_pointer'];raw=field['raw_text']
            available=[b for b in blocks if b['target_pointer']==pointer]
            if not available: continue
            old_span=row.get('requested_span_cp') if pointer==row.get('target_pointer') else None
            span=usable_location_span(raw,old_span,row.get('anchor_text'))
            if not any(b['source_start_cp']<span[1] and b['source_end_cp']>span[0] for b in available):
                span=[0,len(raw)]
            row={**row,'target_pointer':pointer,'unit_id':reader_document['unit_id'],
                 'owner_id':field['owner_id'],'field_hash':digest_of(raw),
                 'requested_span_cp':span,'anchor_text':raw[span[0]:span[1]],
                 'edge':'before' if row.get('edge')=='before' else 'after'}
        if field is None: _fail(row,'target is not a registered reader field')
        raw=field['raw_text'];field_hash=digest_of(raw);span=row.get('requested_span_cp')
        if not reviews_frozen():
            if field.get('raw_source') or field.get('group') == 'exercise_regions':
                _fail(row,'read-only source material cannot host media or interaction mounts')
            raw=field['raw_text']; field_hash=digest_of(raw)
            if row.get('unit_id')!=reader_document['unit_id'] or row.get('owner_id')!=field['owner_id']: _fail(row,'unit/owner mismatch')
            if row.get('source_kind') not in ('media','interaction') or not row.get('source_id') or not row.get('source_hash'): _fail(row,'source identity missing')
            if row['source_id'] in seen: _fail(row,'source_id is claimed more than once')
            seen.add(row['source_id'])
            if row.get('field_hash')!=field_hash: _fail(row,'field_hash is stale')
            span=row.get('requested_span_cp')
            if not isinstance(span,list) or len(span)!=2 or any(type(n)!=int for n in span) or not 0<=span[0]<span[1]<=len(raw): _fail(row,'requested_span_cp required; ambiguous anchor needs exact occurrence/span')
            if row.get('anchor_text')!=raw[span[0]:span[1]]: _fail(row,'anchor_text/span mismatch')
            if row.get('edge') not in ('before','after'): _fail(row,'edge must be before/after')
        candidates=[b for b in blocks if b['target_pointer']==row['target_pointer'] and b['source_start_cp']<span[1] and b['source_end_cp']>span[0]]
        if not candidates: _fail(row,'anchor has no visible syntax block')
        block=candidates[0] if row['edge']=='before' else candidates[-1]
        key=(block['block_id'],row['edge']); order=counts[key];counts[key]+=1
        identity={k:deepcopy(row[k]) for k in ('unit_id','owner_id','target_pointer','source_kind','source_id','source_hash','requested_span_cp','edge','field_hash','anchor_text')}
        ident='mount-'+digest_of({k:v for k,v in identity.items() if k!='source_hash'})[7:27]
        result.append({'schema_version':'ReaderMountV1',**identity,'mount_id':ident,'anchor_id':row.get('anchor_id') or ident,
            'resolved_block_id':block['block_id'],'order_at_anchor':order,
            'resolution_reason':'complete_top_level_block' if len(candidates)==1 else 'span_crosses_complete_blocks'})
    ids=[m['mount_id'] for m in result]; anchors=[m['anchor_id'] for m in result]
    if not reviews_frozen() and (len(ids)!=len(set(ids)) or len(anchors)!=len(set(anchors))): _fail(result[-1],'duplicate mount/anchor identity')
    return result


def build_reader_layout(reader_document: dict, mounts: list[dict], *, workspace_root: Path, display_policy: dict) -> dict:
    if reader_document.get('schema_version')!='ReaderDocumentV1': raise ContractError('ReaderDocumentV1 required')
    blocks=_blocks(reader_document,workspace_root)
    return {'schema_version':'ReaderLayoutV1','unit_id':reader_document['unit_id'],'reader_document_hash':digest_of(reader_document),
        'markdown_engine_hash':markdown_engine_hash(workspace_root),'display_policy_hash':digest_of(display_policy),
        'blocks':blocks,'mounts':_resolve(reader_document,mounts,blocks)}


def layout_from_reader_media(reader_document, reader_media, *, interaction_mounts=None, display_policy=None):
    """Derive slots from current source-bound media rows; never grant approval."""
    if display_policy is None:
        from .display_policy import reader_display_policy
        display_policy=reader_display_policy()
    mounts=[]
    for item in reader_media:
        p=item['placement']
        mounts.append({'unit_id':reader_document['unit_id'],'owner_id':p['owner_id'],
            'target_pointer':p['target_pointer'],'source_kind':'media','source_id':item['request_id'],
            'source_hash':item.get('source_hash') or digest_of(item),'anchor_id':p['anchor_id'],
            'requested_span_cp':deepcopy(p['span']),'edge':p['position'],
            'field_hash':p['field_hash'],'anchor_text':p['anchor_text']})
    return build_reader_layout(reader_document,mounts+list(interaction_mounts or []),workspace_root=_assets().parents[1],display_policy=display_policy)
