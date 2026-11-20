"""Feed targeted shared-concept context checks to the existing repair coordinator."""
from copy import deepcopy
from pathlib import Path

from ..blocks.content.concept_corrections import load_corrections, affected_body_paths, overlay_catalog
from ..blocks.content.content_contracts import content_digest
from ..blocks.audit.contracts import validate_ticket
from ..contracts.stage import StageResult
from ..contracts.enums import QualityState
from ..core.jsonio import read_json, write_json_atomic
from .diagnostics import technical_ticket
from .repair import RepairRouteV1


def collect_body_repairs(products):
    from .content_stages import _balanced_directives
    products = Path(products)
    revisions = load_corrections(products)
    latest = {(r['concept_id'],r['field']):r for r in revisions}
    current_entries = {r['concept_id']:r for r in revisions}
    # A busy unit may still contain A after the public entry advanced A→B→C.
    # Match every obsolete value, but authorize one repair to the final version.
    obsolete = {}
    for revision in revisions:
        current = latest[(revision['concept_id'],revision['field'])]
        if revision['before'] == current['after']:
            continue
        obsolete[(revision['concept_id'],revision['field'],revision['before'])] = {
            **current, 'before':revision['before'],
            'version':current_entries[revision['concept_id']]['version']}
    pending = {}
    for path in sorted((products/'lesson').glob('*.json')) if revisions else ():
        lesson = read_json(path).get('payload') or {}
        content = lesson.get('content') or {}
        unit, h = content.get('unit_id'), lesson.get('content_hash')
        if unit != path.stem or content_digest(content) != h:
            continue
        catalog = (lesson.get('content_identity') or {}).get('reference_catalog') or {}
        audit = read_json(products/'text_audit'/path.name, default={}).get('payload') or {}
        # A current body review already inspected this version of the entry;
        # any remaining body errors are its own ordinary repair tickets.
        if audit.get('content_hash') == h:
            catalog = (audit.get('content_identity') or {}).get('reference_catalog') or catalog
        versions = {row.get('id'):row.get('version') for row in catalog.get('entries', [])}
        affected = [(revision, affected_body_paths(content,revision)) for revision in obsolete.values()
                    if versions.get(revision['concept_id']) != revision['version']]
        affected = [(revision,paths) for revision,paths in affected if paths]
        if not affected:
            continue
        correction_versions = sorted({r['version'] for r,_ in affected})
        notice_key = content_digest({'content_hash':h,
            'versions':correction_versions}).split(':')[-1]
        notice_path = products/'concepts/body-repairs'/unit/(notice_key+'.json')
        notice = read_json(notice_path, default={})
        if notice.get('dispatched'):
            continue
        reasons = [f"公共概念 {r['concept_id']} 已经现有正文审核确认修正：{r['before']} → {r['after']}。"
                   f"当前正文 {', '.join(paths)} 含有旧式，需核对它是否被当作正确结论使用；{r['correction']['reason']}"
                   for r,paths in affected]
        problem = '；'.join(reasons)
        ticket = technical_ticket(unit_id=unit, artifact_id='lesson:'+unit, artifact_hash=h,
            problem=problem, source=problem, owner='lesson', kind='text_content',
            allowed_paths=tuple(dict.fromkeys(p for _,paths in affected for p in paths)),
            action='以上一稿为底稿，核对已定位旧式的实际语境。仅在正文误用时修正及调整必要关联；明确的错误反例或勘误可保留，无实际误用允许不改，其他正确内容保持。',
            expected='沿既有正文审核核实当前语境；静态命中只提供待核线索，不证明正文有错，不要求无误用时改动字段。',
            rule='SHARED_CONCEPT_CONTENT').as_dict()
        ticket['previous_attempt'] = {'candidate_content_hash':h, 'context_check_required':True,
            'shared_concept_versions':correction_versions}
        ticket = validate_ticket(ticket)
        notice = {'unit_id':unit, 'content_hash':h, 'tickets':[ticket.as_dict()],
                  'correction_versions':correction_versions, 'dispatched':False}
        write_json_atomic(notice_path,notice)
        route = RepairRouteV1(ticket.ticket_id,'text_audit','content','lesson:'+unit).as_dict()
        pending[unit] = {'path':notice_path, 'notice':notice,
            'result':StageResult.ok('text_audit',quality_state=QualityState.NEEDS_REVIEW,
                repair_requests=_balanced_directives(unit,h,[ticket]),repair_routes=(route,))}
    return pending


def load_body_repair_tickets(products, unit, *, closed=()):
    tickets = {}
    for path in sorted((Path(products)/'concepts/body-repairs'/unit).glob('*.json')):
        for row in read_json(path).get('tickets') or []:
            ticket = validate_ticket(row)
            if ticket.ticket_id not in closed:
                tickets[ticket.ticket_id] = ticket
    return list(tickets.values())


def collect_projection_refreshes(products):
    """Find changed public cards without declaring their prose erroneous."""
    from ..blocks.content.reader_document import project_reader
    products = Path(products)
    revisions = load_corrections(products)
    refreshes = {}
    for path in sorted((products/'lesson').glob('*.json')) if revisions else ():
        lesson = read_json(path).get('payload') or {}
        content = lesson.get('content') or {}
        if content.get('unit_id') != path.stem:
            continue
        catalog = (lesson.get('content_identity') or {}).get('reference_catalog') or {}
        updated = overlay_catalog(catalog,revisions)
        if updated == catalog:
            continue
        used = {b['owner_id'] for b in project_reader(content,updated).as_dict()['blocks']
                if b.get('group') == 'references'}
        changed = [row for row in updated.get('entries',[]) if row.get('id') in used
                   and row not in catalog.get('entries',[])]
        if changed:
            key = content_digest({'references':changed}).split(':')[-1]
            receipt = products/'concepts/projection-refreshes'/path.stem/(key+'.json')
            if not receipt.is_file():
                refreshes[path.stem] = receipt
    return refreshes


def collect_review_refreshes(products):
    """修改计划 C3：审核已通过的单元，其实际引用的卡发布了新版本 → 定向重审一次。

    Returns ``{unit: receipt_path}``. A receipt per (unit, content, card versions)
    makes each new card version trigger at most one re-review of that unit; a
    unit that does not reference the changed card is never selected.
    """
    from ..blocks.content.reader_document import project_reader
    products = Path(products)
    revisions = load_corrections(products)
    out = {}
    for path in sorted((products/'lesson').glob('*.json')) if revisions else ():
        lesson = read_json(path).get('payload') or {}
        content = lesson.get('content') or {}
        unit, h = content.get('unit_id'), lesson.get('content_hash')
        if unit != path.stem or content_digest(content) != h:
            continue
        audit = read_json(products/'text_audit'/path.name, default={}).get('payload') or {}
        if audit.get('content_hash') != h or (audit.get('review_outcome') or {}).get('status') != 'verified':
            continue
        reviewed = (audit.get('content_identity') or {}).get('reference_catalog') or {}
        updated = overlay_catalog(reviewed, revisions)
        if updated == reviewed:
            continue
        used = {b['owner_id'] for b in project_reader(content, updated).as_dict()['blocks']
                if b.get('group') == 'references'}
        changed = sorted((row.get('id'), row.get('version')) for row in updated.get('entries', [])
                         if row.get('id') in used and row not in reviewed.get('entries', []))
        if not changed:
            continue
        key = content_digest({'content_hash': h, 'versions': changed}).split(':')[-1]
        receipt = products/'concepts/review-refreshes'/unit/(key+'.json')
        if not receipt.is_file():
            out[unit] = {'path': receipt, 'content_hash': h, 'changed': [list(row) for row in changed]}
    return out


def drain_body_repairs(runner, pending, in_flight):
    """Wait for a unit's workers, then reuse its existing bounded repair path."""
    if runner._review_mode != 'balanced' or runner.services.cancel.cancelled:
        return
    products = runner.store.work/'products'
    registry = products/'concepts/corrections.json'
    if not registry.is_file():
        return
    paths = [registry, *sorted((products/'lesson').glob('*.json'))]
    signature = tuple((str(p),p.stat().st_mtime_ns) for p in paths)
    if signature != getattr(runner,'_shared_concept_signature',None):
        runner._shared_concept_repairs = collect_body_repairs(products)
        runner._shared_concept_refreshes = collect_projection_refreshes(products)
        runner._shared_concept_rereviews = collect_review_refreshes(products)
        runner._shared_concept_signature = signature
    nodes = runner.graph.by_id()
    busy = {nodes[nid].scope_key for nid in in_flight}
    for unit, item in list(getattr(runner,'_shared_concept_repairs',{}).items()):
        if unit in busy:
            continue
        origin = runner._unit_node_ids(unit).get('text_audit')
        if origin is None:
            continue  # Never extend the requested run scope.
        runner._handle_repair_requests(nodes[origin],item['result'],pending)
        write_json_atomic(item['path'],{**item['notice'],'dispatched':True})
        runner._shared_concept_repairs.pop(unit,None)
    for unit, receipt in list(getattr(runner,'_shared_concept_refreshes',{}).items()):
        if unit in busy:
            continue
        render = runner._unit_node_ids(unit).get('render')
        if render is None:
            continue
        # Refresh only deterministic page projection and delivery. No source,
        # author, body review or media generation is invalidated by a card edit.
        targets = {render} | {nid for nid in runner._dependent_closure({render})
                              if nodes[nid].stage in ('render','delivery')}
        if targets & in_flight:
            continue
        for nid in targets:
            if nid in pending:
                continue
            with runner._lock:
                runner._generations[nid] = runner._generations.get(nid,0)+1
                previous = runner._states.pop(nid,None)
                if previous is not None:
                    runner._attempt_carry[nid] = int(previous.attempt or 0)
            pending[nid] = nodes[nid]
        write_json_atomic(receipt,{'unit_id':unit,'queued_nodes':sorted(targets)})
        runner._shared_concept_refreshes.pop(unit,None)
    for unit, item in list(getattr(runner,'_shared_concept_rereviews',{}).items()):
        # 修改计划 C3：重审不占返修轮次；判 fail 时由原有返修规则处理（有轮次就修，没有记 known_error）。
        if unit in busy or unit in getattr(runner,'_shared_concept_repairs',{}):
            continue  # Body repairs already re-review their new candidate.
        origin = runner._unit_node_ids(unit).get('text_audit')
        if origin is None:
            runner._shared_concept_rereviews.pop(unit,None)
            continue  # Only the current run scope.
        targets = {origin} | {nid for nid in runner._dependent_closure({origin})
                              if nodes[nid].stage in ('render','delivery')}
        if targets & in_flight:
            continue
        for nid in targets:
            if nid in pending:
                continue
            with runner._lock:
                runner._generations[nid] = runner._generations.get(nid,0)+1
                previous = runner._states.pop(nid,None)
                if previous is not None:
                    runner._attempt_carry[nid] = int(previous.attempt or 0)
            pending[nid] = nodes[nid]
        write_json_atomic(item['path'],{'unit_id':unit,'content_hash':item['content_hash'],
            'changed_cards':item['changed'],'queued_nodes':sorted(targets)})
        from .events import KIND_NOTE
        runner.store.emit(KIND_NOTE, stage='text_audit', node_id=origin, scope_key=unit,
            message=f'{unit} 引用的公共卡已更新，自动定向重审一次',
            shared_concept_rereview=item['changed'])
        runner._shared_concept_rereviews.pop(unit,None)
