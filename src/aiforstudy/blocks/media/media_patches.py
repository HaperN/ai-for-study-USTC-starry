"""Atomic, request-scoped layout repairs under the original RepairTicket v2."""
from copy import deepcopy
from ...core.digest import digest_of
from ...errors import ContractError
from ..audit.contracts import validate_ticket
from .static_common import fields


def apply_media_patch(request,spec,patch,ticket,*,math_context):
    from .execution import compile_static
    authority=validate_ticket(ticket)
    fields(patch,('schema_version','request_id','base_spec_hash','ticket_id','operations'))
    if patch['schema_version']!='MediaPatchV2' or patch['request_id']!=request['request_id'] or patch['ticket_id']!=authority.ticket_id:
        raise ContractError('media patch identity is not authorized by original ticket')
    if authority.status!='ready' or authority.kind!='visual_layout' or authority.target['artifact_id']!='media:'+request['request_id']:
        raise ContractError('media layout patch needs a ready original visual_layout ticket for this request')
    current=digest_of(spec)
    if patch['base_spec_hash']!=current or authority.target.get('artifact_hash')!=current:
        raise ContractError('media patch or original ticket refers to a stale spec')
    if math_context.request!=request: raise ContractError('patch mathematical authority belongs to another request')
    operations=patch['operations']
    if not isinstance(operations,list) or not 1<=len(operations)<=32: raise ContractError('bounded media operations required')
    updated=deepcopy(spec); paths=[]
    for operation in operations:
        fields(operation,('op','path','value')); path=operation['path']
        if operation['op']!='replace' or not isinstance(path,str) or not path.startswith('/layout/') or any(x in path for x in ('~','..','\\',':','//')):
            raise ContractError('only exact layout replacements are allowed; mathematical repairs return to content')
        authorized='/spec'+path
        if not any(authorized==p or authorized.startswith(p.rstrip('/')+'/') for p in authority.allowed_paths):
            raise ContractError('media patch path exceeds original ticket authorization')
        if any(p.startswith('/') and (authorized==p or authorized.startswith(p.rstrip('/')+'/')) for p in authority.preserve): raise ContractError('media patch violates preserved field')
        if any(path==p or path.startswith(p+'/') or p.startswith(path+'/') for p in paths): raise ContractError('overlapping media patch operations')
        paths.append(path); parts=path.lstrip('/').split('/'); target=updated
        for part in parts[:-1]:
            if not isinstance(target,dict) or part not in target: raise ContractError('media patch parent is absent')
            target=target[part]
        if not isinstance(target,dict) or parts[-1] not in target: raise ContractError('media patch replacement target is absent')
        target[parts[-1]]=deepcopy(operation['value'])
    before=compile_static(request['kind'],spec,math_context); after=compile_static(request['kind'],updated,math_context)
    if before['data']!=after['data'] or before['object_bindings']!=after['object_bindings'] or before['relations']!=after['relations']:
        raise ContractError('layout repair changed mathematical data or object identity')
    return updated


def apply_media_patches(request, spec, patches, tickets, *, math_context):
    """Validate one original authority set before applying a disjoint batch."""
    frozen = {t['ticket_id']: validate_ticket(t) for t in tickets}
    if len(frozen) != len(tickets) or not isinstance(patches, list) or len(patches) != len(frozen):
        raise ContractError('media patch batch must cover each original ticket exactly once')
    seen, paths = set(), []
    base = digest_of(spec)
    for patch in patches:
        tid = patch.get('ticket_id')
        if tid not in frozen or tid in seen or patch.get('base_spec_hash') != base or frozen[tid].target['artifact_hash'] != base:
            raise ContractError('media patch batch contains stale/unknown/duplicate authority')
        seen.add(tid)
        for op in patch.get('operations', []):
            path = op.get('path', '')
            if not path or any(path == p or path.startswith(p+'/') or p.startswith(path+'/') for p in paths):
                raise ContractError('media patch batch has overlapping paths')
            paths.append(path)
    result = deepcopy(spec)
    for patch in patches:
        ticket = frozen[patch['ticket_id']].as_dict()
        current = digest_of(result)
        ticket['target']['artifact_hash'] = current
        result = apply_media_patch(request, result, {**patch, 'base_spec_hash': current}, ticket, math_context=math_context)
    return result
