"""Graph 5 source-bound content, criterion review and immutable host receipts."""

from __future__ import annotations
import json
import re
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from ..blocks.content.content_contracts import content_digest, as_dict, parse_content
from ..blocks.content.content_policy import (
    load_content_policy,
    check_content,
)
from ..blocks.content.review_document import (
    build_review_document,
)
from ..blocks.content.content_approval import (
    approve_content,
    validate_content_approval,
)
from ..blocks.content.generation import generate_content
from ..blocks.content.review_outcome import content_identity
from ..blocks.audit.contracts import validate_ticket, make_ticket_id
from ..contracts.artifact import ArtifactRef
from ..contracts.stage import StageResult
from ..contracts.enums import QualityState
from ..core.digest import digest_file
from ..core.jsonio import read_json, read_json_soft, write_json_atomic
from ..core.review_mode import reviews_frozen, frozen_review, substantive_reviews_enabled, current_review_mode
from ..errors import ContractError
from .services import reraise_control_error
from .graph import MEDIA_TICKET_STAGES

VERSION = "content-v5-author-media-1"


def _t():
    from . import stage_io

    return stage_io


def _root(ctx):
    return _t()._store_root(ctx) / "products"


def _unit(ctx):
    value = str(ctx.node.scope_key)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value) or value in (".", ".."):
        raise ContractError("invalid content unit path")
    return value


def _generation_journal(ctx, bucket='content', phase='author'):
    """One immutable request/response journal per outer batch, replayable on resume."""
    base = _root(ctx) / bucket / _unit(ctx)
    batch = getattr(ctx, 'repair_batch', None)
    if batch is None:
        path = base / 'generation-journal.json'
    else:
        identity = getattr(batch, 'batch_id', None) or content_digest({
            'round':getattr(batch, 'round', 1),
            'tickets':[tid for job in batch.jobs for tid in job.ticket_ids]})
        path = base / 'rounds' / content_digest({'batch':identity}).split(':')[-1] / (phase+'.json')
    ctx.active_generation_journal = path
    return path


def _generation_input(ctx, journal, value):
    """Freeze the exact draft and feedback for this billable attempt, not future rounds."""
    path = journal.with_suffix('.input.json')
    if path.is_file():
        return read_json(path)
    if callable(value):
        value = value()
    write_json_atomic(path, value)
    return value


def _theorem_names(explanations):
    return list(dict.fromkeys(row['name'] for row in explanations or []
        if isinstance(row, dict) and isinstance(row.get('name'), str) and row['name'].strip()))


def _available_theorem_names(ctx, context):
    from ..blocks.content.theorem_index import available_theorem_explanations
    order = [unit['unit_id'] for unit in context['chapters'].get('lesson_units', [])
             if unit.get('unit_id')]
    return _theorem_names(available_theorem_explanations(
        _root(ctx).parent.parent, _unit(ctx), unit_order=order))


def _previous_rejection(ctx, bucket='content'):
    feedback = getattr(ctx, 'repair_feedback', None)
    media = any(getattr(job,'artifact_id','').startswith('author-media:')
                for job in getattr(getattr(ctx,'repair_batch',None),'jobs',()))
    if (feedback and ('source_context' in Path(feedback.get('generation_journal','')).parts)
            == (bucket=='source_context') and (bucket=='source_context' or
                feedback.get('artifact_id','').startswith('author-media:') == media)):
        return deepcopy(feedback)
    base = _root(ctx) / bucket / _unit(ctx)
    journals = [p for p in base.rglob('*.json') if p.name == 'generation-journal.json'
                or p.parent.parent.name == 'rounds' and not p.name.endswith('.input.json')]
    for path in sorted(journals, key=lambda p:p.stat().st_mtime_ns, reverse=True):
        if bucket=='content' and (path.name=='author-media.json') != media:
            continue
        journal=read_json_soft(path) or {}
        if any(a.get('status')=='accepted' for a in journal.get('attempts',[])):
            return None
        merged = journal.get('merged_feedback')
        if merged:
            return deepcopy(merged)
    return None


def _feedback_artifact(ctx):
    path = getattr(ctx,'active_generation_journal',None)
    if path and Path(path).name == 'author-media.json':
        return 'author-media:'+_unit(ctx)
    return 'lesson:'+_unit(ctx)


def _concept_index_file(ctx):
    """Use this run's merged identities; old parse-only callers remain readable."""
    if getattr(getattr(ctx, 'services', None), 'store', None) is not None:
        product = _root(ctx) / 'concepts' / 'concepts.json'
        if product.is_file():
            return product.resolve()
    return (Path(ctx.context.parse) / 'concepts.json').resolve()


def _collect_source(ctx):
    T = _t()
    unit_id = _unit(ctx)
    unit = T._unit_definition(ctx, unit_id)
    if not unit:
        raise ContractError("missing frozen unit " + unit_id)
    parse = Path(ctx.context.parse).resolve()
    source = (parse / str(unit.get("original_text") or "")).resolve()
    if not source.is_relative_to(parse) or not source.is_file():
        raise ContractError("missing or unsafe textbook source")
    source_text = source.read_text(encoding="utf-8")
    chapters = read_json_soft(parse / "chapters.json") or {}
    concept_file = _concept_index_file(ctx)
    concept_document = read_json_soft(concept_file) or {}
    concepts = concept_document.get('payload', concept_document)
    rows = concepts.get("concepts") or concepts.get("items") or []
    projected = []
    required = []
    for row in rows:
        if not isinstance(row, dict):
            raise ContractError("frozen concept index contains a non-object")
        occurrences = row.get("occurrences") or []
        introduced = next(
            (o.get("unit_key") or o.get("unit_id") for o in occurrences
             if o.get("role") == "introduced" and (o.get("unit_key") or o.get("unit_id"))),
            None,
        )
        owner = row.get('ownership_unit')
        if owner == '<unknown>':
            owner = None
        first = (
            owner
            or row.get("introduced_in")
            or row.get("first_unit")
            or row.get("unit_id")
            or introduced
            or next((o.get("unit_key") or o.get("unit_id") for o in occurrences
                     if o.get("unit_key") or o.get("unit_id")), None)
        )
        item = {
            key: deepcopy(row[key])
            for key in ("id", "name", "type", "kind")
            if key in row
        }
        item["first_unit"] = first
        item["role_here"] = "introduced" if first == unit_id else "reused"
        # A frozen cross-unit reference needs its actual definition. Keeping the
        # catalog complete does not introduce its obligations into this unit:
        # derive_source_obligations still selects first_unit/introduced only.
        for key in ("formal", "definition", "conditions", "source_refs", "source_parts"):
            if key in row:
                item[key] = deepcopy(row[key])
        projected.append(item)
        if first == unit_id:
            required.append(item)
    # The full catalog includes identity and definition, not a prefix sample.
    # Full raw source bytes remain frozen through source_inputs digests.
    catalog = {
        key: deepcopy(chapters[key])
        for key in ("book", "toc", "tree", "nodes")
        if key in chapters
    }
    catalog["lesson_units"] = [
        {
            key: deepcopy(u[key])
            for key in ("unit_id", "title", "anchor", "chapter", "section", "parent_id")
            if key in u
        }
        for u in chapters.get("lesson_units") or []
    ]
    source_inputs = {"textbook": digest_file(source)}
    for filename in (
        "chapters.json",
        "notation.json",
        "book.json",
        "toc.json",
    ):
        path = parse / filename
        if path.is_file():
            source_inputs[filename] = digest_file(path)
    if concept_file.is_file():
        # The selected merged product is an input, not the unused legacy table.
        concept_key = 'concepts.json' if concept_file == parse / 'concepts.json' else str(concept_file)
        source_inputs[concept_key] = digest_file(concept_file)
    from ..core.video_evidence import frozen_video_evidence, load_attached_videos
    try:
        videos = frozen_video_evidence(parse, book_id=ctx.context.book_id, unit_id=unit_id)
        for video_unit, bundle, manifest in load_attached_videos(parse, book_id=ctx.context.book_id):
            if video_unit == unit_id:
                for path in [bundle/'manifest.json', *(bundle / row['path'] for row in manifest['files'])]:
                    source_inputs[str(path.relative_to(parse))] = digest_file(path)
                source_inputs['video-evidence/current.json'] = digest_file(parse/'video-evidence/current.json')
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise ContractError('invalid attached video evidence: ' + str(exc)) from exc
    context = {
        "video_evidence": videos,
        "book_id": ctx.context.book_id,
        "unit_id": unit_id,
        "unit": unit,
        "source_text": source_text,
        "original_text": unit["original_text"],
        "original_sha256": digest_file(source),
        "chapters": catalog,
        "concept_index": {"concepts": projected},
        "source_inputs": source_inputs,
    }
    for filename in ("notation.json", "book.json"):
        payload = read_json_soft(parse / filename)
        if payload:
            context[filename] = payload
    context["source_context_hash"] = content_digest(
        {
            "book_id": ctx.context.book_id,
            "unit_id": unit_id,
            "source_inputs": source_inputs,
            "unit": unit,
        }
    )
    context["concept_ids"] = [str(r.get("id")) for r in projected]
    context["required_concepts"] = required
    from ..blocks.content.source_context import extract_source_examples
    context["source_examples"] = extract_source_examples(source_text)
    return context


def _structural_context(ctx):
    from ..blocks.content.source_context import source_context_from_frozen
    from ..blocks.content.content_policy import load_learner_profile
    return source_context_from_frozen(_collect_source(ctx), learner_profile=load_learner_profile())


def _base_frozen_context(ctx):
    """Reconstruct source authority without making a model call.

    D16 修复（历史回归案例）：实现指纹漂移**不等于**已有的语义盘点作废。
    原实现在指纹不等时直接退回 `structural_only`，于是既存正文的 `source_context_hash`
    与当前 frozen 上下文不一致，`media_plan` 报"内容来源已变化"并连带 blocked 一串节点。
    现在：只要冻结响应能被**当前实现**重新校验通过，就照旧按语义版重放（判据没放松，
    `validate_source_inventory` 仍逐条核对）；真的重放不通过才退回结构版。
    """
    from ..blocks.content.source_inventory import validate_source_inventory
    source = _structural_context(ctx)
    if reviews_frozen():
        return source.as_dict()
    path = _root(ctx) / 'source_context' / _unit(ctx) / 'current.json'
    cached = read_json_soft(path) or {}
    if cached.get('structural_digest') != source.digest:
        # 结构输入本身变了：缓存与当前来源无关，只能用当前结构版。
        return source.as_dict()
    if not cached.get('response'):
        return source.as_dict()
    try:
        refined = validate_source_inventory(source, cached.get('response'))
        if cached.get('semantic_digest') != refined.digest:
            raise ContractError('source inventory cache digest mismatch')
    except ContractError:
        if cached.get('inventory_implementation') == _inventory_identity():
            raise  # 同一实现下摘要对不上 = 缓存损坏，不能静默降级
        return source.as_dict()
    return refined.as_dict()


def _frozen_context(ctx, *, latest_concepts=False):
    """Keep a published draft's exact source; new writing uses reviewed corrections."""
    from ..blocks.content.concept_corrections import load_corrections, overlay_context
    source = _base_frozen_context(ctx)
    head = read_json_soft(_root(ctx)/'content'/_unit(ctx)/'current.json') or {}
    if head and not latest_concepts:
        if head.get('source_context_ref'):
            ref = ArtifactRef.from_dict(head['source_context_ref'])
            expected = (_root(ctx)/'content'/_unit(ctx)/'source-contexts').resolve()
            if Path(ref.path).resolve().parent != expected:
                raise ContractError('source context snapshot outside content store')
            ref.verify()
            frozen = read_json(ref.path)
            # Preserve actual textbook/index drift detection; only run-local
            # concept revisions are intentionally independent of the old source.
            if (frozen.get('source_inputs') == source.get('source_inputs')
                    and frozen.get('learner_profile') == source.get('learner_profile')):
                return frozen
        return source  # Old heads remain readable without fabricating a snapshot.
    return overlay_context(source, load_corrections(_root(ctx)))


def _inventory_identity():
    from ..blocks.content import source_inventory, source_context, generation_corrections
    from ..core import source_boundaries
    return {module.__name__: digest_file(Path(module.__file__))
            for module in (source_inventory, source_context, source_boundaries, generation_corrections)}


def _prepare_source_context(ctx, client):
    from ..blocks.content.source_inventory import extract_source_inventory
    current = _frozen_context(ctx)
    if current['obligation_extraction_status'] == 'semantic_verified':
        return current
    source = _structural_context(ctx)
    base = _root(ctx) / 'source_context' / _unit(ctx)
    def preserve(data):
        directory = base / 'responses'
        number = len(list(directory.glob('*.json'))) + 1
        write_json_atomic(directory / f'{number:06d}.json',
                          {'structural_digest':source.digest,'response':data})
    journal = _generation_journal(ctx, 'source_context', 'inventory')
    frozen = _generation_input(ctx, journal, {'previous_rejection':_previous_rejection(ctx, 'source_context')})
    refined = extract_source_inventory(source, client, response_sink=preserve, correction_journal=journal,
        previous_rejection=frozen['previous_rejection'])
    accepted_response = read_json(journal)['attempts'][-1]['response']
    write_json_atomic(base / 'current.json', {'structural_digest':source.digest,
        'semantic_digest':refined.digest,'response':accepted_response,
        'inventory_implementation':_inventory_identity()})
    return refined.as_dict()


def _artifact(ctx, path, kind, dependency):
    return ArtifactRef(
        book_id=ctx.context.book_id,
        kind=kind,
        logical_key=f"{ctx.node.stage}:{_unit(ctx)}/{Path(path).resolve().relative_to(_root(ctx).resolve()).as_posix()}",
        path=Path(path),
        schema_version="4.0" if kind == "lesson.content" else "1.0",
        producer_stage=ctx.node.stage,
        producer_version=VERSION,
        dependency_digest=dependency,
    ).with_content()


def _source_artifacts(ctx):
    base = _root(ctx) / 'source_context' / _unit(ctx)
    return tuple(_artifact(ctx, path, 'content.source_inventory', digest_file(path))
                 for path in sorted(base.rglob('*.json')) if path.is_file())


def _author_generation_artifacts(ctx):
    base = _root(ctx) / 'content' / _unit(ctx)
    paths = sorted((base / 'responses').glob('*.json'))
    paths.extend(sorted((base / 'host_derivations').glob('*.json')))
    journal = base / 'generation-journal.json'
    if journal.is_file(): paths.append(journal)
    paths.extend(sorted((base / 'rounds').rglob('*.json')))
    kinds={'responses':'lesson.response','host_derivations':'content.host_derivation'}
    return tuple(_artifact(ctx,path,kinds.get(path.parent.name,'lesson.correction_journal'),digest_file(path))
                 for path in paths)


def _generation_attempts(path):
    return len(read_json(path)['attempts']) if path.is_file() else 0


def _publish_revision(ctx, content, context, *, author_media=None, author_response_hash='', previous_author_media=None,
                      defer_media=False):
    unit = _unit(ctx)
    base = _root(ctx) / "content" / unit
    revisions = base / "revisions"
    old = read_json_soft(base / "current.json") or {}
    revision = int(old.get("content_revision") or 0) + 1
    path = revisions / f"{revision:06d}.json"
    frozen = deepcopy(content)
    frozen["revision"] = revision
    frozen = parse_content(frozen).as_dict()
    media=None
    if defer_media:
        # Preserve author intent byte-for-byte. Rebinding belongs to media_plan,
        # after the rewritten body passes both reviews, never to publication.
        previous = deepcopy(previous_author_media or {})
        requirements = deepcopy(previous.get('requirements') if previous else author_media)
        media = {**previous, 'schema_version':'AuthorMediaArtifactV1',
            'book_id':frozen['book_id'], 'unit_id':unit,
            'content_revision':revision, 'content_hash':content_digest(frozen),
            'source_context_hash':frozen['source_context_hash'],
            'author_response_hash':previous.get('author_response_hash') or author_response_hash,
            'requirements':requirements, 'bindings':previous.get('bindings',{}),
            'goal_bindings':previous.get('goal_bindings',{}),
            'errors':previous.get('errors',[]), 'status':'deferred',
            'deferred_previous':previous.get('deferred_previous', previous) if previous else None}
    elif author_media is not None:
        from ..blocks.media.author_requirements import bind_author_requirements
        is_record=isinstance(author_media,dict) and author_media.get('schema_version')=='AuthorMediaArtifactV1'
        media=bind_author_requirements(author_media['requirements'] if is_record else author_media,frozen,
            author_response_hash=author_media['author_response_hash'] if is_record else author_response_hash,
            previous=previous_author_media,allow_stale=previous_author_media is not None)
        if is_record and author_media.get('repair_provenance'):
            media['repair_provenance']=deepcopy(author_media['repair_provenance'])
    media_path=_root(ctx)/'author_media'/unit/'revisions'/f'{revision:06d}.json'
    # A crash may leave either immutable file before the atomic head commit.
    # Reuse only byte-equivalent unpublished values; never overwrite a revision.
    for target,value in ((path,frozen),(media_path,media)):
        if target.exists() and (value is None or read_json(target)!=value):
            raise ContractError('immutable unpublished revision differs from retry: '+str(target))
    if not path.exists(): write_json_atomic(path, frozen)
    ref = _artifact(ctx, path, "lesson.content", context["source_context_hash"])
    source_path = base/'source-contexts'/(content_digest(context).split(':')[-1]+'.json')
    if not source_path.exists():
        write_json_atomic(source_path, context)
    elif read_json(source_path) != context:
        raise ContractError('immutable source context differs from stored snapshot')
    source_ref = _artifact(ctx, source_path, 'content.source_context', context['source_context_hash'])
    head = {
        "schema_version": "ContentHeadV1",
        "book_id": ctx.context.book_id,
        "unit_id": unit,
        "content_revision": revision,
        "content_hash": content_digest(frozen),
        "content_ref": ref.as_dict(),
        "source_context_hash": context["source_context_hash"],
        "source_context_ref":source_ref.as_dict(),
        "policy_digest": load_content_policy(context["learner_profile"]).digest,
        "author_media_status": "missing",
    }
    media_refs=()
    if media is not None:
        if not media_path.exists(): write_json_atomic(media_path,media)
        media_ref=_artifact(ctx,media_path,'lesson.author_media',content_digest(frozen))
        head.update(author_media_ref=media_ref.as_dict(),author_media_hash=content_digest(media),author_media_status=media['status'])
        media_refs=(media_ref,)
    write_json_atomic(base / "current.json", head)
    head_ref = _artifact(
        ctx, base / "current.json", "lesson.content_head", content_digest(head)
    )
    return frozen, head, (ref, source_ref, *media_refs, head_ref)


def load_author_media(ctx,content,*,allow_stale=False,defer_rebind=False):
    from ..blocks.media.author_requirements import validate_author_artifact
    head=read_json(_root(ctx)/'content'/_unit(ctx)/'current.json')
    if not head.get('author_media_ref'):
        if reviews_frozen():
            from ..blocks.media.author_requirements import bind_author_requirements
            return bind_author_requirements({'decisions': [], 'requests': []}, content,
                author_response_hash='')
        raise ContractError('缺作者同期媒体需求；历史候选不能自动补 none，须显式提供作者需求')
    ref=ArtifactRef.from_dict(head['author_media_ref'])
    expected=(_root(ctx)/'author_media'/_unit(ctx)/'revisions'/f"{int(head['content_revision']):06d}.json").resolve()
    if Path(ref.path).resolve()!=expected: raise ContractError('author media head outside immutable revision store')
    ref.verify(); record=read_json(ref.path)
    if head.get('author_media_hash')!=content_digest(record): raise ContractError('author media head hash mismatch')
    if defer_rebind:
        return record
    if record.get('status') == 'deferred':
        from ..blocks.media.author_requirements import bind_author_requirements
        try:
            record=bind_author_requirements(record['requirements'],content,
                author_response_hash=record['author_response_hash'],
                previous=record.get('deferred_previous'),allow_stale=True)
        except ContractError as exc:
            record={**record,'status':'stale','errors':[{'request_key':'',
                'code':'AUTHOR_MEDIA_BINDING','message':str(exc)}]}
    return validate_author_artifact(record,content,allow_stale=allow_stale)


def load_content(ctx, approved=False):
    head_path = _root(ctx) / "content" / _unit(ctx) / "current.json"
    if not head_path.is_file():
        raise ContractError("missing current content revision")
    head = read_json(head_path)
    ref = ArtifactRef.from_dict(head.get("content_ref") or {})
    expected = (
        _root(ctx)
        / "content"
        / _unit(ctx)
        / "revisions"
        / f"{int(head.get('content_revision') or 0):06d}.json"
    ).resolve()
    if Path(ref.path).resolve() != expected:
        raise ContractError("content head outside immutable revision store")
    ref.verify()
    content = read_json(ref.path)
    if (
        content_digest(content) != head.get("content_hash")
        or content.get("unit_id") != _unit(ctx)
        or content.get("book_id") != ctx.context.book_id
    ):
        raise ContractError("content head hash/book/unit mismatch")
    if not approved or reviews_frozen():
        return content, {}
    audit_path = _root(ctx) / "text_audit" / f"{_unit(ctx)}.json"
    if not audit_path.is_file():
        raise ContractError("missing host content approval")
    payload = read_json(audit_path).get("payload") or {}
    approval = payload.get("approval") or {}
    approval_ref = ArtifactRef.from_dict(payload.get("approval_ref") or {})
    receipt_dir = (_root(ctx) / "content" / _unit(ctx) / "approvals").resolve()
    if Path(approval_ref.path).resolve().parent != receipt_dir:
        raise ContractError("approval outside immutable receipt store")
    approval_ref.verify()
    if read_json(approval_ref.path) != approval:
        raise ContractError("approval envelope differs from host receipt bytes")
    context = _frozen_context(ctx)
    policy = load_content_policy(context["learner_profile"])
    document = build_review_document(content, context["reference_catalog"])
    validate_content_approval(
        content, approval, policy, context["source_context_hash"], document
    )
    if (
        approval.get("content_ref") != head["content_ref"]
        or approval.get("content_revision") != head["content_revision"]
    ):
        raise ContractError("content approval revision/ref mismatch")
    reports = payload.get("check_reports") or []
    if approval.get("check_report_refs") != [content_digest(r) for r in reports]:
        raise ContractError("approval check-report digest mismatch")
    regenerated = approve_content(content, document, reports, policy, source_context=context,
        math_report=payload.get("math_report"), content_revision=head["content_revision"],
        content_ref=head["content_ref"], closed_ticket_ids=approval.get("closed_ticket_ids",()),
        repair_authorities=payload.get('repair_authorities',())).as_dict()
    if regenerated != approval:
        raise ContractError("approval differs from current source, math and criterion evidence")
    if any(validate_ticket(t).is_blocking for t in payload.get("tickets") or []):
        raise ContractError("content approval has blocking tickets")
    for origin in MEDIA_TICKET_STAGES:
        media = (read_json_soft(_root(ctx) / origin / f"{_unit(ctx)}.json") or {}).get(
            "payload"
        ) or {}
        for raw in media.get("tickets") or []:
            ticket = validate_ticket(raw)
            if (
                ticket.kind == "text_content"
                and ticket.owner == "lesson"
                and ticket.is_blocking
                and ticket.target.get("artifact_hash") == content_digest(content)
                and ticket.ticket_id not in approval.get("closed_ticket_ids", [])
            ):
                raise ContractError(
                    "current content approval revoked by media evidence"
                )
    return content, approval


def _load_tickets(ctx):
    payload = (
        read_json_soft(_root(ctx) / "text_audit" / f"{_unit(ctx)}.json") or {}
    ).get("payload") or {}
    tickets = {
        t.ticket_id: t
        for t in (validate_ticket(raw) for raw in payload.get("tickets") or [])
    }
    from .concept_repairs import load_body_repair_tickets
    for ticket in load_body_repair_tickets(_root(ctx), _unit(ctx), closed=payload.get('closed_ticket_ids', [])):
        tickets.setdefault(ticket.ticket_id,ticket)
    for raw in (getattr(ctx,'repair_feedback',None) or {}).get('repair_tickets',[]):
        ticket=validate_ticket(raw)
        tickets.setdefault(ticket.ticket_id,ticket)
    wanted = {
        ticket_id
        for job in getattr(getattr(ctx, "repair_batch", None), "jobs", ()) or ()
        if job.owner == "lesson" and job.kind == "text_content"
        for ticket_id in job.ticket_ids
    }
    # A scheduler-selected, persisted current-run ticket is the only bridge.
    for origin in MEDIA_TICKET_STAGES:
        media = (read_json_soft(_root(ctx) / origin / f"{_unit(ctx)}.json") or {}).get(
            "payload"
        ) or {}
        for raw in media.get("tickets") or []:
            if raw.get("ticket_id") not in wanted and (raw.get('target') or {}).get('rule') != 'POST_MEDIA_CONTENT':
                continue
            if raw.get('ticket_id') in payload.get('closed_ticket_ids', []):
                closed = next((t for t in payload.get('frozen_tickets', [])
                               if t.get('ticket_id') == raw.get('ticket_id')), {})
                # A later media review can rediscover this problem on a newer
                # body. Reopen the ticket, never the artifact's repair budget.
                if (not substantive_reviews_enabled() or
                        (closed.get('target') or {}).get('artifact_hash') == (raw.get('target') or {}).get('artifact_hash')):
                    continue
            ticket = validate_ticket(raw)
            if (
                ticket.unit_id != _unit(ctx)
                or ticket.kind != "text_content"
                or ticket.owner != "lesson"
            ):
                raise ContractError("invalid persisted media-to-content ticket")
            existing = tickets.get(ticket.ticket_id)
            if existing is not None and existing.criteria != ticket.criteria:
                raise ContractError("cross-stage ticket criteria conflict")
            tickets.setdefault(ticket.ticket_id, ticket)
    return list(tickets.values()), payload


def _selected_tickets(ctx, tickets, owner):
    batch = getattr(ctx, "repair_batch", None)
    wanted = {
        tid
        for job in getattr(batch, "jobs", ()) or ()
        if job.owner == owner
        for tid in job.ticket_ids
    }
    selected = [t for t in tickets if t.ticket_id in wanted]
    missing = wanted - {t.ticket_id for t in selected}
    # A rejected full response has no published audit ticket of its own. Its
    # persisted generation feedback is the authority, alongside existing issues.
    rejection = _previous_rejection(ctx)
    if missing and rejection and all(t.startswith('ticket-content-rejected-') for t in missing):
        artifact=rejection.get('artifact_id','lesson:'+_unit(ctx))
        return [t for t in tickets if t.owner == owner and t.is_blocking and t.target['artifact_id']==artifact]
    if missing:
        raise ContractError("repair batch references missing persisted content ticket")
    return selected


def _with_routes(result, tickets, origin="text_audit"):
    from .repair import content_ticket_dispatch
    if hasattr(result, "repair_routes"):
        result.repair_routes = tuple(
            {
                "schema_version": "RepairRouteV1",
                "ticket_id": t.ticket_id,
                "origin_stage": origin,
                "target_artifact_kind": content_ticket_dispatch(t)[2],
                "target_artifact_id": t.target["artifact_id"],
            }
            for t in tickets
        )
    return result


def _repair_content(ctx,content,context,policy,client,tickets,preserve_response, *, author_media=None):
    """Repair the previous complete draft in the existing bounded generation.

    Balanced mode may also return explicitly requested media changes; full mode
    retains its content-only path. No changed-field or binding gate is added.
    """
    from ..blocks.content.prompt import build_content_messages
    from ..blocks.content.source_context import SourceContext
    from ..blocks.content.generation import _math_placeholder_guard, _validate_content_only
    from ..blocks.content.generation_corrections import run_local_generation
    source = SourceContext.model_validate(as_dict(context))
    frozen = {'source_context': context, 'content': content,
              'repair_tickets': [t.as_dict() for t in tickets]}
    if substantive_reviews_enabled():
        frozen['author_media'] = deepcopy(author_media)
    # 与首次生成保持完整正文契约，修复轮无需回抄媒体。
    frozen['output_contract'] = {
        'schema_version': 'ContentAuthoringV1',
        'instruction': ('以 frozen_context.content 为底稿返回**完整正文**：顶层 schema_version="ContentAuthoringV1"，'
                        'content 为完整 LessonContentV4（不是补丁、不是差异）。媒体由宿主沿用，无需输出；'
                        '仅修复工单与 previous_rejection 明确指出的问题及必要关联，未涉及内容保持原样；'
                        '完整指输出范围，不代表从头重新创作。输出前自行对照底稿，恢复无关改动。'),
    }
    if substantive_reviews_enabled():
        frozen['output_contract']['instruction'] = (
            '以上一版 content 与 author_media 为底稿，返回完整 content。只修工单明确问题及必要关联；'
            '工单明确指出媒体缺口或原请求必须随正文修正时，同次返回 media_requirements，'
            '保留原请求及 request_key、顺序，更新必要项并在末尾追加新请求；未涉及媒体时省略此字段，由宿主沿用。'
            '完整正文仍须保留推导，不用动画替代解答；输出前自行对照底稿排除无关改动。')
    rejection = _previous_rejection(ctx)
    if rejection is not None: frozen['previous_rejection'] = deepcopy(rejection)
    journal = _generation_journal(ctx, phase='repair')
    def repair_input():
        if not journal.is_file():
            # Reuse this draft's name list, including an explicitly empty one.
            # Existing repair inputs remain untouched; a new repair needs no
            # full explanation even when its old review input contained one.
            review_input = _root(ctx)/'content'/_unit(ctx)/'review-journals'/(
                content_digest(content).split(':')[-1]+'.input.json')
            reviewed = read_json_soft(review_input) or {}
            if 'available_theorem_names' in reviewed:
                names = deepcopy(reviewed['available_theorem_names'])
            elif 'available_theorem_explanations' in reviewed:
                names = _theorem_names(reviewed['available_theorem_explanations'])
            else:
                names = _available_theorem_names(ctx, context)
            frozen['available_theorem_names'] = names
        return frozen
    frozen = _generation_input(ctx, journal, repair_input)
    source = SourceContext.model_validate(as_dict(frozen['source_context']))
    def validate_repair(raw):
        authored = _validate_content_only(raw, source)
        # The loose author adapter supplies empty media defaults. They are not
        # an instruction to erase an existing request when repair omitted it.
        if substantive_reviews_enabled() and (not isinstance(raw, dict) or 'media_requirements' not in raw):
            authored.pop('media_requirements', None)
        return authored

    authored = run_local_generation(
        build_content_messages('repair', frozen, policy, tickets=tickets), client,
        validate_repair,
        tag=f'content-repair:{_unit(ctx)}',
        correction_tag=f'content-repair-correction:{_unit(ctx)}',
        response_sink=preserve_response, correction_guard=False if reviews_frozen() else _math_placeholder_guard,
        correction_journal=journal, max_corrections=2 if substantive_reviews_enabled() else 0)
    authored['_source_context'] = frozen['source_context']
    return authored


def _merge_balanced_media(previous, revised):
    """Keep existing request order while applying the author's supplied changes.

    No binding, coverage or changed-field gate is restored. Partial additions
    cannot silently remove earlier media, and original responses remain in the
    generation journal for the next substantive review.
    """
    from ..blocks.content.json_only import normalize_media_requirements
    before = normalize_media_requirements(previous)
    # Apply supplied fields before defaults, otherwise a caption-only update
    # would acquire kind=image and erase an existing animation's steps.
    after = deepcopy(revised) if isinstance(revised, dict) else normalize_media_requirements(revised)
    result = {**before, **after}
    for field, key in (('requests', 'request_key'), ('decisions', 'target_id')):
        rows = deepcopy(before.get(field)) if isinstance(before.get(field), list) else []
        positions = {row[key]: index for index, row in enumerate(rows)
                     if isinstance(row, dict) and isinstance(row.get(key), str)}
        for row in after.get(field) if isinstance(after.get(field), list) else []:
            identity = row.get(key) if isinstance(row, dict) else None
            if isinstance(identity, str) and identity in positions:
                rows[positions[identity]] = {**rows[positions[identity]], **deepcopy(row)}
            else:
                if isinstance(identity, str):
                    positions[identity] = len(rows)
                rows.append(deepcopy(row))
        result[field] = rows
    return normalize_media_requirements(result)

def _structure_blocked_units(ctx):
    """用户 2026-10-20 定：原文归属有歧义的单元只拦它自己（结构阶段记在 blocked_units）。"""
    path = _root(ctx) / 'structure' / 'structure.json'
    return set(((read_json_soft(path) or {}).get('payload') or {}).get('blocked_units') or ())


def lesson_stage(ctx):
    ctx.services.cancel.checkpoint()
    if _unit(ctx) in _structure_blocked_units(ctx):
        return StageResult.blocked(ctx.node.stage, f'{_unit(ctx)} 的原文归属有未裁决歧义（见结构阶段产物）；'
                                   '只拦本单元，其他单元照常生成。')
    T = _t()
    response_refs = []
    ctx.active_generation_journal = None
    author_media=None; previous_author_media=None; author_response_hash=''
    revised_media = False
    try:
        context = _frozen_context(ctx, latest_concepts=getattr(ctx, 'repair_batch', None) is not None)
        source_calls = 0
        if not reviews_frozen() and context['obligation_extraction_status'] != 'semantic_verified':
            source_client, _ = T._gateway_client(ctx, role='source_inventory', bucket='source_inventory')
            if source_client is None:
                raise ContractError('missing independent source inventory transport')
            source_journal = _generation_journal(ctx, 'source_context', 'inventory')
            previous_source_calls = _generation_attempts(source_journal)
            context = _prepare_source_context(ctx, source_client)
            source_calls = _generation_attempts(source_journal) - previous_source_calls
        policy = load_content_policy(context["learner_profile"])
        batch = getattr(ctx, "repair_batch", None)
        repair_without_draft = False
        if batch is not None:
            author_journal = _generation_journal(ctx)
            repair_without_draft = (not (_root(ctx)/'content'/_unit(ctx)/'current.json').is_file()
                                    or author_journal.is_file())
        rechecking = bool(getattr(ctx, "recheck_only", False)) and not reviews_frozen()
        if rechecking and batch is not None:
            raise ContractError("host policy recheck cannot impersonate a repair job")
        revalidation = False
        if not reviews_frozen() and not rechecking and batch is None:
            from .content_revalidation import request_matches_published_candidate
            revalidation = request_matches_published_candidate(ctx, context, policy)
            rechecking = revalidation
        imported_path = _root(ctx) / "imported_content" / f"{_unit(ctx)}.json"
        imported = not rechecking and batch is None and imported_path.is_file()
        client, transport = (
            (None, "explicit-candidate-import")
            if imported or rechecking
            else T._gateway_client(ctx, role="lesson", bucket="chapter")
        )
        if not imported and not rechecking and client is None:
            raise ContractError("missing lesson model transport")

        def preserve_response(data):
            nonlocal author_response_hash
            author_response_hash=content_digest({'response':data})
            directory = _root(ctx) / "content" / _unit(ctx) / "responses"
            sequence = (
                len(list(directory.glob("*.json"))) + 1 if directory.exists() else 1
            )
            path = directory / f"{sequence:06d}.json"
            if path.exists():
                raise ContractError("content response diagnostic collision")
            write_json_atomic(
                path, {"schema_version": "ContentResponseV1", "response": data}
            )
            response_refs.append(
                _artifact(ctx, path, "lesson.response", context["source_context_hash"])
            )

        if rechecking:
            content, _ = load_content(ctx)
            if content.get("source_context_hash") != context["source_context_hash"]:
                raise ContractError("host recheck candidate source context changed")
            if revalidation:
                from .content_revalidation import validate_published_candidate
                validate_published_candidate(content, load_author_media(ctx, content), context)
            transport = "host-policy-recheck"
        elif imported:
            imported_data = read_json(imported_path)
            content = deepcopy(
                (imported_data.get("payload") or {}).get("content")
                or imported_data.get("content")
                or imported_data
            )
            if not reviews_frozen() and content.get('_meta',{}).get('schema_version') != '4.0':
                raise ContractError('old candidates need explicit V4 field mapping; approval is never imported')
            for key in ("book_id", "unit_id"):
                if not reviews_frozen() and content.get(key) not in (None, context[key]):
                    raise ContractError("imported candidate identity mismatch")
                content[key] = context[key]
            content["source_context_hash"] = context["source_context_hash"]
            content["original_text"] = context["original_text"]
            content["original_sha256"] = context["original_sha256"]
            parse_content(content)
            explicit=(imported_data.get('payload') or imported_data).get('media_requirements')
            if explicit is not None:
                author_media=explicit
                author_response_hash=content_digest(imported_data)
        elif batch is not None and not repair_without_draft:
            content, _ = load_content(ctx)
            persisted, _ = _load_tickets(ctx)
            tickets = _selected_tickets(ctx, persisted, "lesson")
            media_tickets=[t for t in tickets if t.target['artifact_id']=='author-media:'+_unit(ctx)]
            if media_tickets:
                if len(media_tickets)!=len(tickets): raise ContractError('正文与作者媒体需求工单必须分别授权执行')
                # D16-3：作者媒体返修与正文返修同一条路 —— **以上一次 requirements 为底稿整份重写**，
                # 走 run_local_generation 拿"可纠正错误 + 内层轮次"，不再是单次补丁（原来只有 1 次调用，
                # 一拒就整节没有反馈也没有轮次）。判据仍在 rewrite_author_requirements 里逐条执行。
                from ..blocks.media.author_requirements import build_author_media_repair_messages, rewrite_author_requirements
                from ..blocks.content.generation_corrections import run_local_generation
                author_media=load_author_media(ctx,content,allow_stale=True)
                ticket_dicts=[t.as_dict() for t in media_tickets]
                journal=_generation_journal(ctx,phase='author-media')
                frozen=_generation_input(ctx,journal,{'content':content,'author_media':author_media,
                    'repair_tickets':ticket_dicts,'previous_rejection':_previous_rejection(ctx)})
                authored_media=run_local_generation(
                    build_author_media_repair_messages(frozen['content'],frozen['author_media'],frozen['repair_tickets'],policy,
                        previous_rejection=frozen['previous_rejection']), client,
                    lambda raw: rewrite_author_requirements(frozen['author_media'],raw,frozen['repair_tickets'],frozen['content']),
                    tag='author-media-repair:'+_unit(ctx),
                    correction_tag='author-media-repair-correction:'+_unit(ctx),
                    response_sink=preserve_response,
                    # 整份重写的合法性由 `rewrite_author_requirements` 独占判定；
                    # `False` = 不套补丁式 `assert_only_paths`（那会把正当的返修判死）。
                    correction_guard=False,
                    correction_journal=journal)
                author_media=authored_media
            else:
                try: previous_author_media=load_author_media(ctx,content,allow_stale=True,defer_rebind=True)
                except ContractError:
                    head=read_json(_root(ctx)/'content'/_unit(ctx)/'current.json')
                    if head.get('author_media_ref'): raise
                author_media=previous_author_media
                authored = _repair_content(ctx,content,context,policy,client,tickets,preserve_response,
                    author_media=(previous_author_media or {}).get('requirements'))
                content = authored['content']
                context = authored.pop('_source_context', context)
                if substantive_reviews_enabled() and 'media_requirements' in authored:
                    author_media = _merge_balanced_media((previous_author_media or {}).get('requirements'),
                                                         authored['media_requirements'])
                    revised_media = True
                    # Replaying an accepted correction journal does not call
                    # the response sink again; bind the same original response.
                    if not author_response_hash:
                        journal = read_json(_generation_journal(ctx, phase='repair'))
                        author_response_hash = content_digest({'response':journal['attempts'][-1]['response']})
        else:
            from ..blocks.interaction.spec import source_interaction_capabilities
            from .interaction_runtime import interaction_runtime_inputs
            interaction_caps=source_interaction_capabilities(context, **interaction_runtime_inputs(ctx))
            journal = _generation_journal(ctx)
            def author_input():
                value = {'previous_rejection':_previous_rejection(ctx) if batch is not None else None,
                         'source_context':deepcopy(context)}
                if batch is not None:
                    # A new no-draft round reuses the first author's names,
                    # extracting them if that older input held full teaching.
                    initial = read_json_soft(_root(ctx)/'content'/_unit(ctx)/'generation-journal.input.json') or {}
                    if 'available_theorem_names' in initial:
                        value['available_theorem_names'] = deepcopy(initial['available_theorem_names'])
                    elif 'available_theorem_explanations' in initial:
                        value['available_theorem_names'] = _theorem_names(initial['available_theorem_explanations'])
                elif not journal.is_file():
                    value['available_theorem_names'] = _available_theorem_names(ctx, context)
                return value
            frozen_input = _generation_input(ctx, journal, author_input)
            context = frozen_input.get('source_context', context)
            def preserve_derivation(report):
                path=journal.parent/'host_derivations'/(content_digest(report).split(':')[-1]+'.json')
                if path.exists():
                    if read_json(path)!=report:raise ContractError('immutable host derivation differs from replay')
                else:write_json_atomic(path,report)
            authored = generate_content(context, policy, client, preserve_response,
                                        interaction_capabilities=interaction_caps, correction_journal=journal,
                                        derivation_sink=preserve_derivation,
                                        # R3-1/R4：无底稿的返修轮仍是"正文返修"，
                                        # 只校验正文（媒体需求的问题留到媒体阶段）。
                                        media_projection=not repair_without_draft,
                                        previous_rejection=frozen_input['previous_rejection'],
                                        available_theorem_names=frozen_input.get('available_theorem_names'),
                                        available_theorem_explanations=frozen_input.get('available_theorem_explanations'))
            author_response_hash = content_digest({'response':read_json(journal)['attempts'][-1]['response']})
            content=authored['content']; author_media=authored.get('media_requirements')
        if rechecking:
            frozen = content
            head_path = _root(ctx) / "content" / _unit(ctx) / "current.json"
            head = read_json(head_path)
            media_ref=ArtifactRef.from_dict(head['author_media_ref']).verify() if head.get('author_media_ref') else None
            refs = (
                ArtifactRef.from_dict(head["content_ref"]).verify(),
                *((media_ref,) if media_ref else ()),
                _artifact(ctx, head_path, "lesson.content_head", content_digest(head)),
            )
        else:
            frozen, head, refs = _publish_revision(ctx, content, context,author_media=author_media,
                author_response_hash=author_response_hash,previous_author_media=previous_author_media,
                defer_media=repair_without_draft or (previous_author_media is not None and not revised_media))
        checks = None if reviews_frozen() else check_content(frozen, context, policy)
        envelope = T._emit(
            ctx,
            name="lesson",
            kind="lesson.unit",
            file_type="LessonContentV4",
            payload={
                **head,
                "content": frozen,
                "checks": checks.as_dict() if checks else {'status':'not_run', 'review_mode':current_review_mode()},
                "content_identity": content_identity(
                    frozen, context['reference_catalog'], policy_digest=policy.digest),
                "model_calls": source_calls + len(response_refs),
                "policy_digest": policy.digest,
                "generated_policy_digest": head["policy_digest"],
                "recheck_only": rechecking,
                "recheck_reason": (
                    "author request unchanged; current immutable candidate revalidated"
                    if revalidation else getattr(ctx, "recheck_reason", "") if rechecking else ""
                ),
                "transport": transport,
            },
            inputs={
                "content": head["content_hash"],
                "source": context["source_context_hash"],
                "policy": policy.digest,
            },
            path=_root(ctx) / "lesson" / f"{_unit(ctx)}.json",
        )
        return StageResult.ok(
            ctx.node.stage,
            outputs=(*refs, envelope, *_author_generation_artifacts(ctx), *_source_artifacts(ctx)),
            quality_state=(
                QualityState.NOT_CHECKED if reviews_frozen() else QualityState.NEEDS_REVIEW
                if imported or checks.blocking_issues
                else QualityState.ACCEPTED
            ),
            findings=checks.blocking_issues if checks else (),
            input_files=tuple(
                str(Path(name) if Path(name).is_absolute() else Path(ctx.context.parse) / name)
                for name in context["source_inputs"] if name != 'textbook'
            )
            + (str(Path(ctx.context.parse) / context["original_text"]),)
            + (() if 'video-evidence/current.json' in context["source_inputs"] else (str(Path(ctx.context.parse) / 'video-evidence/current.json'),)),
        )
    except Exception as exc:
        reraise_control_error(exc)
        # R2-4 / R3-1：内层纠正轮已取消，答复不合格时 `run_local_generation` 把
        # **可执行反馈**（每条：位置 + 怎么算过）写进 journal 的 `merged_feedback`。
        # 关键：**必须把它交成返修请求**（RepairDirective）——
        # 否则协调器认为"没什么可修的"，一次拒绝就判死整节，外层 6 轮根本不会启动
        # （实测 run-20261012-081727：events 里 repair 计数为 0，两节直接全链阻塞）。
        feedback=_journal_merged_feedback(ctx)
        findings=({'code':'CONTENT_REJECTED','feedback':feedback},) if feedback else ()
        directives, routes = _rejection_directives(ctx, exc, feedback)
        return StageResult.blocked(
            ctx.node.stage,
            # 修改计划 A4(3)：返修答复用尽额度仍不可用时不接收，head 保留上一稿及其审核状态。
            ("返修答复不可用（保留上一稿及其审核状态）：" if getattr(ctx, 'repair_batch', None) is not None else "")
            + f"content author/patch blocked: {type(exc).__name__}: {exc}",
            findings=findings,
            repair_requests=directives,
            repair_routes=routes,
            outputs=(*_author_generation_artifacts(ctx), *_source_artifacts(ctx)),
        )


def _rejection_directives(ctx, exc, feedback):
    """R2-4/R3-1：把"答复被拒"变成一条**内容返修请求**（含路由），让外层轮次真正转起来。

    只在**lesson 阶段、且确实是本轮被拒**时才发：否则会把上一轮遗留的反馈
    当成新失败，制造幽灵工单（实测：决策守卫用例里 text_audit 阶段多出一条 lesson 指令）。
    """
    from .repair import RepairDirective, RepairRouteV1
    # Syntax/format corrections have their own finite journal. Re-dispatching
    # them as substantive repairs would grant a fresh correction budget forever.
    if substantive_reviews_enabled():
        return (), ()
    if getattr(ctx.node, 'stage', '') != 'lesson':
        return (), ()
    unit=_unit(ctx)
    errors=list((feedback or {}).get('errors') or ())
    if not errors:
        # 没有可转的反馈 → 不排返修（但签名必须一致：调用方解包两个值）
        return (), ()
    paths=[str(row.get('path') or '/') for row in errors]
    problem='；'.join(
        f"{row.get('path')}: {row.get('detail') or row.get('msg') or row.get('code')}" for row in errors[:6])
    fix='；'.join(str(row.get('fix') or '') for row in errors[:3] if row.get('fix'))
    expected='；'.join(str(row.get('passing_criterion') or '') for row in errors[:3] if row.get('passing_criterion'))
    from ..core.digest import digest_of
    artifact_id=(feedback or {}).get('artifact_id') or _feedback_artifact(ctx)
    ticket_id='ticket-content-rejected-'+digest_of({'unit':unit,'artifact':artifact_id,'paths':paths})[7:23]
    # 路由必须与 directive 的工单集合完全一致，且 target_stage 要符合 owner 权限
    # （lesson → lesson）。origin_stage 必须是**提交工单的实际阶段**。
    route=RepairRouteV1(ticket_id=ticket_id, origin_stage=ctx.node.stage,
        target_artifact_kind='author_media' if artifact_id.startswith('author-media:') else 'content',
        target_artifact_id=artifact_id).as_dict()
    directive=RepairDirective(unit_id=unit, ticket_ids=(ticket_id,), owner='lesson', kind='text_content',
        reason='本轮生成答复不合格，按反馈完整重交：' + problem,
        artifact_hashes={artifact_id: digest_of({'response':(feedback or {}).get('original_response')})},
        verdict_content={'why': problem, 'fix': fix, 'expected': expected,
                         'allowed_paths': paths,
                         'instruction': str((feedback or {}).get('instruction') or '')})
    return (directive,), (route,)


def _journal_merged_feedback(ctx):
    """把本轮答复的拒绝理由整理成下一轮可用的反馈（含精确位置与通过标准）。"""
    path = getattr(ctx, 'active_generation_journal', None) or _root(ctx) / 'content' / _unit(ctx) / 'generation-journal.json'
    journal = read_json_soft(path) or {}
    merged = journal.get('merged_feedback')
    if not isinstance(merged, dict) or not merged.get('errors'):
        return {}
    return {
        'schema_version': 'ContentFeedbackV1',
        'artifact_id': _feedback_artifact(ctx),
        'repair_tickets':deepcopy((read_json_soft(path.with_suffix('.input.json')) or {}).get('repair_tickets',[])),
        'ticket_refs': [tid for job in getattr(getattr(ctx,'repair_batch',None),'jobs',())
                        if job.owner=='lesson' for tid in job.ticket_ids],
        'original_response': deepcopy(merged.get('original_response')),
        'generation_journal': str(path),
        'feedback_digest': content_digest(merged),
        'reason': str(merged.get('message') or ''),
        # R2-3：把"怎么算过"与授权面一起带给下一次重写
        'instruction': str(merged.get('instruction') or ''),
        'errors': deepcopy(merged['errors']),
        'allowed_paths': list(merged.get('allowed_paths') or ()),
        'append_only': deepcopy(merged.get('append_only') or {}),
    }


def _balanced_issue_ticket(unit, h, row, *, rule=None):
    from .diagnostics import technical_ticket
    raw = technical_ticket(unit_id=unit, artifact_id='lesson:'+unit, artifact_hash=h,
        problem=row['reason'], source=row['reason'], owner='lesson', kind='text_content',
        allowed_paths=('/',), action='以上一版完整正文为底稿，修复此判词明确指出的问题及必要关联：'+row['reason'],
        expected=row['expected'], rule=rule or row['criterion_id']).as_dict()
    raw['source_evidence'] = [{**span, 'artifact_id':'content-review:'+unit,
        'artifact_hash':content_digest(row), 'json_pointer':'/reason'} for span in raw['source_evidence']]
    raw['criteria'][0].update(criterion_id=row['criterion_id'], method='reviewer', evidence_type='text')
    raw['previous_attempt'] = {'criterion_id':row['criterion_id'],
        'confirmed_content_hash':h, 'discovery':deepcopy(row)}
    return validate_ticket(raw)


def _balanced_directives(unit, h, tickets):
    from ..blocks.audit.contracts import ticket_verdict_content
    from .repair import RepairDirective
    return (RepairDirective(unit_id=unit, ticket_ids=tuple(t.ticket_id for t in tickets),
        owner='lesson', kind='text_content', reason='；'.join(t.problem for t in tickets),
        artifact_hashes={'lesson:'+unit:h},
        verdict_content={'issues':[ticket_verdict_content(t) for t in tickets]}),) if tickets else ()


def balanced_media_content_repairs(ctx, content, problems):
    """Route confirmed media findings about prose through its existing budget."""
    if not substantive_reviews_enabled():
        return [], (), ()
    from ..blocks.content.review import BALANCED_REVIEW_ID
    from .repair import RepairRouteV1
    failures = [deepcopy(row) for row in problems if isinstance(row, dict)
        and row.get('target_artifact_kind') == 'content' and row.get('status') == 'fail'
        and isinstance(row.get('reason') or row.get('problem'), str)
        and (row.get('reason') or row.get('problem')).strip()]
    if not failures:
        return [], (), ()
    unit, h = _unit(ctx), content_digest(content)
    row = {'criterion_id':'POST_MEDIA_CONTENT', 'status':'fail',
        'reason':'；'.join(row.get('reason') or row['problem'] for row in failures),
        'expected':'修复媒体审阅指出的正文实质问题，并通过当前正文与媒体需求的完整实质审核。',
        'problems':failures}
    raw = _balanced_issue_ticket(unit, h, row).as_dict()
    raw['previous_attempt']['criterion_ids'] = [BALANCED_REVIEW_ID]
    ticket = validate_ticket(raw)
    route = RepairRouteV1(ticket.ticket_id, ctx.node.stage, 'content', 'lesson:'+unit).as_dict()
    return [ticket.as_dict()], _balanced_directives(unit, h, [ticket]), (route,)


def _balanced_text_audit(ctx, content, context, policy):
    from ..blocks.content.review import review_content, BALANCED_CRITERIA, BALANCED_REVIEW_ID
    from ..blocks.content.review_outcome import outcome
    from ..blocks.content.concept_corrections import proposed_corrections, overlay_context, publish_corrections, load_corrections

    published = load_corrections(_root(ctx))
    context = overlay_context(context, published)
    proposals, correction_conflicts = proposed_corrections(content, context)
    context = overlay_context(context, proposals)
    from ..blocks.content.concept_corrections import correction_listing
    listing = correction_listing(as_dict(content), published, proposals, unit_id=_unit(ctx))

    T, unit, h = _t(), _unit(ctx), content_digest(content)
    previous_tickets, previous = _load_tickets(ctx)
    previous_tickets = [t for t in previous_tickets
                        if t.target['artifact_id'] == 'lesson:'+unit]
    refs = []
    body = as_dict(content).get('body_markdown')
    if not isinstance(body, str) or not body.strip():
        # 修改计划 A4(4)：正文为空不发审核，记为 missing；不花审核调用，也不授予 verified。
        reason = '正文为空，未送审核'
        payload = {'unit_id':unit, 'content_hash':h, 'source_context':context,
            'source_context_hash':context['source_context_hash'], 'policy_digest':policy.digest,
            'review_mode':'balanced', 'review_status':'not_reviewed',
            'review_outcome':outcome('missing', [reason]),
            'review_failure':{'kind':'empty_body', 'reason':reason}, 'approval':None, 'approval_ref':{},
            'shared_concept_corrections':[], 'shared_concept_conflicts':correction_conflicts,
            'check_reports':[], 'math_report':None,
            'tickets':[t.as_dict() for t in previous_tickets],
            'frozen_tickets':deepcopy(previous.get('frozen_tickets', [])),
            'closed_ticket_ids':sorted(previous.get('closed_ticket_ids', [])), 'transport':'not_called',
            'events':[*previous.get('events', []), {'event':'empty_body_not_reviewed', 'content_hash':h}],
            'content_identity':content_identity(content,context['reference_catalog'],policy_digest=policy.digest)}
        ref = T._emit(ctx, name='text_audit', kind='audit.text', file_type='ContentAuditV2', payload=payload,
            inputs={'content':h,'review_mode':'balanced'}, path=_root(ctx)/'text_audit'/f'{unit}.json')
        return StageResult.ok(ctx.node.stage, outputs=(ref,), quality_state=QualityState.NEEDS_REVIEW,
            findings=[{'code':'EMPTY_BODY', 'why':reason}],
            input_files=(str(_root(ctx)/'content'/unit/'current.json'),))

    def preserve(data, *, exchange=False):
        directory = _root(ctx)/'content'/unit/('review_exchanges' if exchange else 'review_responses')
        path = directory/f'{len(list(directory.glob("*.json")))+1:06d}.json'
        write_json_atomic(path, data if exchange else {'content_hash':h, 'response':data})
        refs.append(_artifact(ctx, path, 'content.review_exchange' if exchange else 'content.review_response', h))

    journal_key = h.split(':')[-1]
    lesson = (read_json_soft(_root(ctx)/'lesson'/f'{unit}.json') or {}).get('payload') or {}
    if context['reference_catalog'] != (lesson.get('content_identity') or {}).get('reference_catalog'):
        journal_key += '-shared-' + content_digest(context['reference_catalog']).split(':')[-1]
    journal = _root(ctx)/'content'/unit/'review-journals'/(journal_key+'.json')
    author_media = load_author_media(ctx, content, allow_stale=True)['requirements']
    def review_input():
        value = {'previous_tickets':[t.as_dict() for t in previous_tickets],
                 'author_media':deepcopy(author_media)}
        if listing or correction_conflicts:
            # 修改计划 C5：列出本节引用概念上的全部修正（已发布 / 本节提案 / 本节保留），
            # 带公共原值 before 与现值 after，审核不会把已生效的提案当成多余。
            value['shared_concept_corrections'] = deepcopy(listing)
            value['shared_concept_conflicts'] = deepcopy(correction_conflicts)
        if not journal.is_file():
            value['source_context'] = deepcopy(context)
            value['available_theorem_names'] = _available_theorem_names(ctx, context)
            value['previous_verdicts'] = [deepcopy(verdict)
                for prior in previous.get('check_reports') or []
                if prior.get('review_mode') == 'balanced'
                for verdict in prior.get('verdicts') or []]
            value['previous_content_hash'] = previous.get('content_hash')
        return value
    frozen = _generation_input(ctx, journal, review_input)
    context = frozen.get('source_context', context)
    client, transport = T._gateway_client(ctx, role='text_judge', bucket='text_audit')
    if client is None:
        from ..errors import CapabilityError
        raise CapabilityError('missing content review transport')
    try:
        report = review_content(content, {}, context, (), None, client,
            response_sink=preserve, exchange_sink=lambda data:preserve(data,exchange=True),
            repair_context={key:value for key,value in frozen.items()
                            if key not in ('available_theorem_names', 'available_theorem_explanations', 'source_context')},
            correction_journal=journal, author_media=frozen['author_media'],
            available_theorem_names=frozen.get('available_theorem_names'),
            available_theorem_explanations=frozen.get('available_theorem_explanations')).as_dict()
    except Exception as exc:
        reraise_control_error(exc)
        from ..providers.review_failures import classify_review_failure
        kind = classify_review_failure(exc)
        if kind not in ('network', 'timeout', 'empty_response'):
            return StageResult.blocked(ctx.node.stage,
                f'content review blocked: {type(exc).__name__}: {exc}', outputs=tuple(refs))
        # 用户 2026-10-20 定：审核调用传输失败时记“审核未完成（未核实）”，媒体链照常往下走；
        # 续跑时 runner 见到 REVIEW_INCOMPLETE 不复用本节点，自动补审。
        reason = f'正文审核未完成（{kind}：{type(exc).__name__}）；续跑时自动补审'
        payload = {'unit_id':unit, 'content_hash':h, 'source_context':context,
            'source_context_hash':context['source_context_hash'], 'policy_digest':policy.digest,
            'review_mode':'balanced', 'review_status':'incomplete',
            'review_outcome':outcome('unverified', [reason]),
            'review_failure':{'kind':kind, 'reason':str(exc)[:500]}, 'approval':None, 'approval_ref':{},
            'shared_concept_corrections':proposals, 'shared_concept_conflicts':correction_conflicts,
            'check_reports':[], 'math_report':None,
            'tickets':[t.as_dict() for t in previous_tickets],
            'frozen_tickets':deepcopy(previous.get('frozen_tickets', [])),
            'closed_ticket_ids':sorted(previous.get('closed_ticket_ids', [])), 'transport':transport,
            'events':[*previous.get('events', []), {'event':'review_incomplete', 'content_hash':h, 'kind':kind}],
            'content_identity':content_identity(content,context['reference_catalog'],policy_digest=policy.digest)}
        ref = T._emit(ctx, name='text_audit', kind='audit.text', file_type='ContentAuditV2', payload=payload,
            inputs={'content':h,'review_mode':'balanced'}, path=_root(ctx)/'text_audit'/f'{unit}.json')
        return StageResult.ok(ctx.node.stage, outputs=(ref,*refs), quality_state=QualityState.NEEDS_REVIEW,
            findings=[{'code':'REVIEW_INCOMPLETE', 'why':reason}],
            input_files=(str(_root(ctx)/'content'/unit/'current.json'),))

    fresh = []
    if report['blocking_issues']:
        # One stable existing lesson ticket carries all actual findings. The
        # reviewer need not reproduce IDs or merge several errors into one row.
        row = {'criterion_id':BALANCED_REVIEW_ID, 'status':'fail',
            'reason':'；'.join(dict.fromkeys(r['reason'] for r in report['blocking_issues'])),
            'expected':'；'.join(expected for _, expected in BALANCED_CRITERIA),
            'verdicts':deepcopy(report['blocking_issues'])}
        fresh.append(_balanced_issue_ticket(unit, h, row))
    closed = set(previous.get('closed_ticket_ids', []))
    retained = []
    for ticket in previous_tickets:
        if report['decision'] == 'accepted':
            closed.add(ticket.ticket_id)
        elif ticket.ticket_id not in {t.ticket_id for t in fresh}:
            retained.append(ticket)
    tickets = [*retained, *fresh]
    closed.difference_update(t.ticket_id for t in tickets)
    known = fresh or [t for t in retained if (t.previous_attempt or {}).get('confirmed_content_hash') == h]
    accepted = report['decision'] == 'accepted' and not tickets
    if accepted:
        _, concurrent_conflicts = publish_corrections(_root(ctx), proposals, content=content,
            reviewed_context=context, report=report, tickets=[t.as_dict() for t in previous_tickets])
        correction_conflicts.extend(concurrent_conflicts)
    reasons = [t.problem for t in tickets] + [row['reason'] for row in report['verdicts']
        if row['status'] in ('unknown', 'not_run')]
    if report['review_failure']:
        reasons.append(report['review_failure']['reason'])
    frozen_tickets = {t['ticket_id']:t for t in previous.get('frozen_tickets', [])}
    frozen_tickets.update({t.ticket_id:t.as_dict() for t in previous_tickets+tickets})
    for tid in closed:
        if tid in frozen_tickets:
            frozen_tickets[tid]['status'] = 'closed'
    payload = {'unit_id':unit, 'content_hash':h, 'source_context':context,
        'source_context_hash':context['source_context_hash'], 'policy_digest':policy.digest,
        'review_mode':'balanced', 'review_status':'reviewed',
        'review_outcome':outcome('verified' if accepted else 'known_error' if known else 'unverified', reasons),
        'review_failure':report['review_failure'], 'approval':None, 'approval_ref':{},
        'shared_concept_corrections':proposals, 'shared_concept_conflicts':correction_conflicts,
        'check_reports':[report], 'math_report':None,
        'tickets':[t.as_dict() for t in tickets], 'frozen_tickets':list(frozen_tickets.values()),
        'closed_ticket_ids':sorted(closed), 'transport':transport,
        'events':[*previous.get('events', []), {'event':'criterion_review', 'content_hash':h,
            'ticket_ids':[t.ticket_id for t in tickets], 'closed_ticket_ids':sorted(closed)}],
        'content_identity':content_identity(content,context['reference_catalog'],policy_digest=policy.digest)}
    ref = T._emit(ctx, name='text_audit', kind='audit.text', file_type='ContentAuditV2', payload=payload,
        inputs={'content':h,'review_mode':'balanced'}, path=_root(ctx)/'text_audit'/f'{unit}.json')
    directives = _balanced_directives(unit, h, fresh)
    return _with_routes(StageResult.ok(ctx.node.stage, outputs=(ref,*refs),
        quality_state=QualityState.ACCEPTED if accepted else QualityState.NEEDS_REVIEW,
        findings=[{'ticket_id':t.ticket_id,'code':t.target['rule'],'why':t.problem} for t in tickets],
        repair_requests=directives,
        input_files=(str(_root(ctx)/'content'/unit/'current.json'),)), fresh)


def text_audit_stage(ctx):
    """One host check/review attempt; retry budgets stay in RepairCoordinator."""
    from ..blocks.content.content_policy import applicable_items
    from ..blocks.content.review import review_content, build_evidence_catalog, external_review_items
    from ..blocks.content.math_checks import check_content_math, math_review_items, NO_FALLBACK
    from ..blocks.content.review_partial import build_partial_review_report
    from ..blocks.content.review_aliases import decode_evidence_response
    from ..blocks.content.repair import finding_ticket, reconcile_tickets
    from .diagnostics import technical_ticket
    from ..blocks.content.review_outcome import content_identity, outcome
    ctx.services.cancel.checkpoint()
    T, unit = _t(), _unit(ctx)
    response_refs = []
    try:
        content, _ = load_content(ctx)
        context = _frozen_context(ctx)
        policy = load_content_policy(context['learner_profile'])
        if substantive_reviews_enabled():
            return _balanced_text_audit(ctx, content, context, policy)
        if reviews_frozen():
            h = content_digest(content)
            payload = {'unit_id':unit, 'content_hash':h, 'source_context':context,
                'source_context_hash':context['source_context_hash'], 'policy_digest':policy.digest,
                'review_mode':'json_only', 'review_status':'not_run',
                'review_outcome':frozen_review(), 'approval':None, 'approval_ref':{},
                'check_reports':[], 'math_report':None, 'tickets':[],
                'content_identity':content_identity(content,context['reference_catalog'],policy_digest=policy.digest)}
            ref = T._emit(ctx,name='text_audit',kind='audit.text',file_type='ContentAuditV2',payload=payload,
                inputs={'content':h,'review_mode':'json_only'},path=_root(ctx)/'text_audit'/f'{unit}.json')
            return StageResult.ok(ctx.node.stage,outputs=(ref,),quality_state=QualityState.NOT_CHECKED,
                input_files=(str(_root(ctx)/'content'/unit/'current.json'),))
        if content.get('source_context_hash') != context['source_context_hash']:
            raise ContractError('content source context changed')
        doc = build_review_document(content, context['reference_catalog'])
        local = check_content(content, context, policy)
        previous_tickets, previous = _load_tickets(ctx)
        # An author-media batch reruns text_audit after publishing its revision.
        # Its tickets belong to media_plan's intent/anchor recheck, never to the
        # body reviewer or its lesson:<unit> directive/hash namespace.
        previous_tickets=[t for t in previous_tickets
                          if t.target['artifact_id']!='author-media:'+unit]
        authorities = {t['ticket_id']: t for t in previous.get('repair_authorities', [])}
        authorities.update({t.ticket_id: t.as_dict() for t in previous_tickets
                            if t.target.get('rule') == 'POST_MEDIA_CONTENT'})
        repair_authorities = list(authorities.values())
        h = content_digest(content)
        catalog = build_evidence_catalog(content, doc, context)
        fresh = [finding_ticket(content,context,policy,row,catalog,deterministic=True)
                 for row in local.blocking_issues]
        reports, math_report, semantic = [local.as_dict()], {}, None
        unresolved_reasons, review_failure = [], {}
        current_external_failures = set()
        transport = 'host-local-checks'
        def review_problem(problem, rule='CONTENT-REVIEW-PROTOCOL'):
            ticket=technical_ticket(unit_id=unit,artifact_id='lesson:'+unit,artifact_hash=h,
                problem=problem,source=context['source_text'],owner='reviewer',kind='evidence_format',
                allowed_paths=('/review/verdicts',),action='仅重试当前正文的审核并补齐证据；禁止改正文。',
                expected='返回绑定当前来源与逐标准证据的有效审核；无法核实必须如实说明。',rule=rule)
            fresh.append(replace(ticket,previous_attempt={'protocol':True,'closure_reason':problem}))
        # R2-1（用户选 A）：**本地判据不再短路复核**。原来 `if not local.blocking_issues:`
        # 让"结构/覆盖/依据/自测完整性"等机器判据一旦失败就整节 known_error，
        # 模型质量复核**一条都不跑**（实测 run-20261011-220452：SS01 没有 ReviewReportV2、
        # 没有 review_exchanges）。现在机器判据只产**可执行反馈**（工单带位置 + 怎么算过），
        # 复核照常进行；两者合并后交给生成模型重写。
        math_report = check_content_math(content,cancel=lambda:bool(getattr(ctx.services.cancel,'cancelled',False))).as_dict()
        ctx.services.cancel.checkpoint()
        for row in math_report['checks']:
            if row['status']=='fail':
                fresh.append(finding_ticket(content,context,policy,{
                    'rule_id':'MATH-REPRESENTATION','subject_id':row['claim_id'],
                    'issue_key':'math:'+row['model_id']+':'+row['relation_id'],
                    'problem':row['reason'],'expected':'同一模型主张经数学验证通过，或保留准确条件并给出独立来源论证。',
                    'repair_hint':'核对所绑定数学对象、定义域、条件和预期结果；修复后重跑独立数学检查。'},
                    catalog,deterministic=True))
            elif row['status'] not in {'pass','unknown'} or row.get('reason_code') in NO_FALLBACK:
                reason_code = row.get('reason_code') or 'not_run'
                reason = 'mathematical verification incomplete: '+row.get('reason','unknown')
                detail = {'kind': 'timeout' if reason_code == 'timeout' else
                          'cancelled' if reason_code == 'cancelled' else 'internal',
                          'reason':reason, 'reason_code':reason_code,
                          'model_id':row.get('model_id'), 'relation_id':row.get('relation_id')}
                if not review_failure:
                    review_failure = {**detail, 'failures':[]}
                review_failure['failures'].append(detail)
                unresolved_reasons.append(reason)
        if not review_failure:
            exchanges=[]
            review_items=applicable_items(content,context,policy)+external_review_items(content,policy,repair_authorities)
            frozen_review_items=[*review_items,*math_review_items(content,policy,math_report)]
            review_catalog=build_evidence_catalog(content,doc,context,math_report)
            def preserve(data):
                directory=_root(ctx)/'content'/unit/'review_responses'
                sequence=len(list(directory.glob('*.json')))+1
                path=directory/f'{sequence:06d}.json'
                write_json_atomic(path,{'content_hash':h,'response':data})
                response_refs.append(_artifact(ctx,path,'content.review_response',h))
            def preserve_exchange(data):
                directory=_root(ctx)/'content'/unit/'review_exchanges'
                sequence=len(list(directory.glob('*.json')))+1
                path=directory/f'{sequence:06d}.json'
                write_json_atomic(path,data)
                exchanges.append(deepcopy(data))
                response_refs.append(_artifact(ctx,path,'content.review_exchange',h))
            try:
                client,transport=T._gateway_client(ctx,role='text_judge',bucket='text_audit')
                if client is None:
                    from ..errors import CapabilityError
                    raise CapabilityError('missing content review transport')
                semantic=review_content(content,doc,context,review_items,
                    math_report,client,response_sink=preserve,exchange_sink=preserve_exchange,
                    available_theorem_names=_available_theorem_names(ctx, context),
                    repair_context={'previous_tickets':[t.as_dict() for t in previous_tickets],
                                    'machine_findings':local.as_dict(), 'math_findings':math_report}).as_dict()
            except Exception as exc:
                reraise_control_error(exc)
                from ..providers.review_failures import classify_review_failure
                failure_kind = classify_review_failure(exc)
                reason = type(exc).__name__+': '+str(exc)
                review_failure = {'kind':failure_kind, 'reason':reason}
                if failure_kind == 'protocol':
                    review_problem(reason)
                else:
                    unresolved_reasons.append(reason)
                # Retain only rows revalidated against this invocation's
                # host-captured exchanges. Never trust a partial report
                # attached by a transport, or reuse an older content head.
                semantic=build_partial_review_report(content,context,
                    frozen_review_items,review_catalog,exchanges,exc,
                    decode_response=decode_evidence_response)
            if semantic:
                reports.append(semantic)
                fresh.extend(finding_ticket(content,context,policy,row,semantic['evidence_catalog'])
                             for row in semantic['blocking_issues']+semantic['warnings']
                             if row['rule_id'] != 'POST_MEDIA_CONTENT' and row.get('status')=='fail')
                uncertain=[row for row in semantic['blocking_issues'] if row.get('status')!='fail']
                unresolved_reasons.extend(row.get('reason','审核无法核实') for row in uncertain)
                for row in uncertain:
                    if (row.get('status')=='unknown' and row['rule_id']!='POST_MEDIA_CONTENT'
                            and row['criterion_id'] not in semantic.get('unresolved_criteria',[])):
                        ticket=finding_ticket(content,context,policy,row,semantic['evidence_catalog'])
                        fresh.append(replace(ticket,previous_attempt={**ticket.previous_attempt,'unverified':True}))
                current_external_failures = {ticket.ticket_id for ticket in previous_tickets
                    if ticket.target.get('rule') == 'POST_MEDIA_CONTENT' and any(
                        row.get('status') == 'fail' and row.get('criterion_id') ==
                        f'external:{ticket.ticket_id}:{criterion.criterion_id}'
                        for row in semantic['blocking_issues']
                        for criterion in ticket.criteria if criterion.required)}
        # An open historical ticket is evidence to retain, not a new verdict.
        # Dispatch requires this invocation's validated failure/protocol defect.
        current_ticket_ids = {ticket.ticket_id for ticket in fresh} | current_external_failures
        known_error_ids = {ticket.ticket_id for ticket in fresh
            if ticket.kind != 'evidence_format' and ticket.owner != 'reviewer'
            and not (ticket.previous_attempt or {}).get('unverified')} | current_external_failures
        # Reconciliation retargets retained tickets to the current repair head.
        # Keep the independently confirmed failure version separate so a later
        # unknown cannot turn that retargeting into evidence against this head.
        previous_tickets = [replace(ticket,previous_attempt={**(ticket.previous_attempt or {}),
            'confirmed_content_hash':h if ticket.ticket_id in current_external_failures else
                (ticket.previous_attempt or {}).get('confirmed_content_hash',None if
                    (ticket.previous_attempt or {}).get('unverified') else ticket.target.get('artifact_hash'))})
            if ticket.kind != 'evidence_format' and ticket.owner != 'reviewer' else ticket
            for ticket in previous_tickets]
        confirmed={t.ticket_id:(t.previous_attempt or {}).get('confirmed_content_hash') for t in previous_tickets}
        fresh = [replace(ticket,previous_attempt={**(ticket.previous_attempt or {}),
            'unverified':bool((ticket.previous_attempt or {}).get('unverified')),
            'confirmed_content_hash':confirmed.get(ticket.ticket_id) if
                (ticket.previous_attempt or {}).get('unverified') else h})
            if ticket.kind != 'evidence_format' and ticket.owner != 'reviewer'
            else ticket
            for ticket in fresh]
        known_error_ids.update(ticket.ticket_id for ticket in previous_tickets
            if (ticket.previous_attempt or {}).get('confirmed_content_hash') == h
            and ticket.kind != 'evidence_format' and ticket.owner != 'reviewer')
        protocol_closed=[t.ticket_id for t in previous_tickets if (t.previous_attempt or {}).get('protocol') and semantic and semantic['decision']=='accepted']
        previous_active=[t for t in previous_tickets if t.ticket_id not in protocol_closed]
        executed = {'rule:'+r['rule_id'] for r in local.rule_results
                    if r['executed'] and r['status'] in ('pass','fail')}
        executed.update('math:'+r['model_id']+':'+r['relation_id'] for r in math_report.get('checks',[])
                        if r['status'] == 'pass')
        tickets,closed=reconcile_tickets(fresh,previous_active,semantic['verdicts'] if semantic else [],content_hash=h,executed_checks=executed)
        closed_ids=(set(previous.get('closed_ticket_ids',[])) | set(closed) | set(protocol_closed)) - {t.ticket_id for t in tickets}
        blocking=[t for t in tickets if t.is_blocking]
        frozen={t['ticket_id']:t for t in previous.get('frozen_tickets',[])}
        frozen.update({t.ticket_id:t.as_dict() for t in previous_tickets+tickets})
        for tid in closed_ids:
            if tid in frozen: frozen[tid]['status']='closed'
        approval,approval_ref={},None
        if not blocking and semantic and semantic['decision']=='accepted':
            head=read_json(_root(ctx)/'content'/unit/'current.json')
            approval=approve_content(content,doc,reports,policy,source_context=context,math_report=math_report,
                book_id=ctx.context.book_id,content_revision=head['content_revision'],
                content_ref=head['content_ref'],closed_ticket_ids=closed_ids,repair_authorities=repair_authorities).as_dict()
            path=_root(ctx)/'content'/unit/'approvals'/(content_digest(approval).replace('sha256:','')+'.json')
            if path.exists() and read_json(path)!=approval: raise ContractError('immutable receipt collision')
            if not path.exists():write_json_atomic(path,approval)
            approval_ref=_artifact(ctx,path,'content.approval',h)
        history=[*previous.get('events',[]),{'event':'criterion_review','content_hash':h,
            'ticket_ids':[t.ticket_id for t in tickets],'closed_ticket_ids':closed+protocol_closed}]
        payload={'unit_id':unit,'content_hash':h,'policy_digest':policy.digest,
            'source_context':context,
            'content_identity':content_identity(content,context['reference_catalog'],policy_digest=policy.digest),
            'review_outcome':outcome('verified' if approval else 'known_error' if any(
                t.ticket_id in known_error_ids for t in blocking) else 'unverified',
                list(dict.fromkeys([t.problem for t in blocking] + unresolved_reasons))),
            'review_failure':review_failure,
            'source_context_hash':context['source_context_hash'],'review_document':doc.as_dict(),
            'check_reports':reports,'math_report':math_report,'approval':approval,
            'approval_ref':approval_ref.as_dict() if approval_ref else {},
            'tickets':[t.as_dict() for t in tickets],'frozen_tickets':list(frozen.values()),
            'closed_ticket_ids':sorted(closed_ids),'events':history,'transport':transport,
            'repair_authorities':repair_authorities}
        ref=T._emit(ctx,name='text_audit',kind='audit.text',file_type='ContentAuditV2',payload=payload,
            inputs={'content':h,'source':context['source_context_hash'],'policy':policy.digest},
            path=_root(ctx)/'text_audit'/f'{unit}.json')
        # A current, validated content failure takes precedence over rechecking
        # an unrelated protocol defect on the same known-bad body. Preserve all
        # tickets; the next current-head audit still evaluates technical issues.
        from .repair import content_ticket_dispatch
        current_blocking=[t for t in blocking if t.ticket_id in current_ticket_ids]
        content_repairs=[t for t in current_blocking if content_ticket_dispatch(t)[2]=='content']
        dispatch=content_repairs or current_blocking
        return _with_routes(StageResult.ok(ctx.node.stage,outputs=(ref,*((approval_ref,) if approval_ref else ()),*response_refs),
            quality_state=QualityState.ACCEPTED if approval else QualityState.NEEDS_REVIEW,
            findings=[{'ticket_id':t.ticket_id,'code':t.target['rule'],'why':t.problem} for t in blocking],
            repair_requests=tuple(T._ticket_directives(dispatch,unit,h)),
            input_files=(str(_root(ctx)/'content'/unit/'current.json'),
                str(_root(ctx)/'source_context'/unit/'current.json'),str(Path(ctx.context.parse)/context['original_text']))),dispatch)
    except Exception as exc:
        reraise_control_error(exc)
        return StageResult.blocked(ctx.node.stage,f'content review blocked: {type(exc).__name__}: {exc}',outputs=tuple(response_refs))
