"""Strict author envelope with persistent, bounded local correction authority."""
from __future__ import annotations
from copy import deepcopy
from pydantic import ValidationError
from .content_contracts import ContentAuthoringV1, LessonContentV4, as_dict
from .source_context import SourceContext
from .prompt import build_content_messages, content_schema_example
from ...core.digest import digest_of
from ...errors import ContractError
from .generation_corrections import LocalCorrectionRequired, run_local_generation
from .math_corrections import guard_author_correction, validate_author_math

# Compatibility name for callers inspecting the example; this is now valid V4.
CONTENT_SCHEMA = content_schema_example()


def _clean_author_errors(errors):
    """收敛反馈：更精确的子路径已报时不再报其父路径；同一路径只留最先出现的一条。"""
    precise=[str(row.get('path') or '') for row in errors if row.get('code') != 'schema']
    if precise:
        def _ancestor_only(path, others):
            return bool(path) and path != '/' and any(o.startswith(path + '/') for o in others)
        errors=[row for row in errors
                if not (row.get('code') == 'schema'
                        and _ancestor_only(str(row.get('path') or ''), precise))]
    deduped, seen = [], set()
    for row in errors:
        path=str(row.get('path') or '')
        if path and path in seen:
            continue
        seen.add(path); deduped.append(row)
    return deduped


def _raise_author_errors(errors, append_only):
    errors = _clean_author_errors(list(errors))
    if errors:
        # 更精确的子路径已经在报时，不再重复报它的**父路径**（父路径没有新信息，
        # 还会把"只修这一处"的反馈稀释成两处）。schema 行尤其常见。
        precise=[str(row.get('path') or '') for row in errors if row.get('code') != 'schema']
        if precise:
            def _ancestor_only(path, others):
                return bool(path) and path != '/' and any(o.startswith(path + '/') for o in others)
            errors=[row for row in errors
                    if not (row.get('code') == 'schema'
                            and _ancestor_only(str(row.get('path') or ''), precise))]
        # 同一路径只保留最先出现的那条（更精确的判定优先），避免同一处被报两遍。
        deduped, seen_paths = [], set()
        for row in errors:
            path=str(row.get('path') or '')
            if path and path in seen_paths:
                continue
            seen_paths.add(path); deduped.append(row)
        errors = deduped
        raise LocalCorrectionRequired('ContentAuthoringV1 local correction required: ' + '; '.join(
            row['path'] + ': ' + row.get('msg', row.get('code', 'invalid reference')) for row in errors),
            errors=errors, allowed_paths=[row['path'] for row in errors],
            kind='math_references' if append_only else 'schema' if any(row.get('code') == 'schema' for row in errors) else 'references',
            append_only=append_only)


def _contract_feedback(message, path):
    return LocalCorrectionRequired(message, errors=[dict(path=path, code='contract',
        detail=message, fix='按输出契约重交完整正文，身份与来源字段使用宿主冻结值。',
        passing_criterion='完整 ContentAuthoringV1 信封内的正文符合 LessonContentV4，冻结字段与宿主一致。')],
        allowed_paths=[path], kind='schema')


def _validate_author_output(raw,source,*,derivation_sink=None,media_projection=True,validate_media=True):
    from ...core.review_mode import reviews_frozen
    if reviews_frozen():
        from .json_only import normalize_author_output
        return normalize_author_output(raw, source)
    if not isinstance(raw,dict) or raw.get('schema_version')!='ContentAuthoringV1':
        raise _contract_feedback('content author must return ContentAuthoringV1', '/schema_version')
    envelope=deepcopy(raw)
    data=envelope.get('content')
    if not isinstance(data,dict): raise _contract_feedback('ContentAuthoringV1.content must be an object', '/content')
    # Never manufacture a schema version or silently upgrade an author response.
    if not isinstance(data.get('_meta'),dict) or data['_meta'].get('schema_version')!='4.0':
        raise _contract_feedback('content author must return explicit LessonContentV4 schema version 4.0', '/content/_meta/schema_version')
    host={k:getattr(source,k) for k in ('unit_id','book_id','original_text','original_sha256')}
    host['source_context_hash']=source.digest
    for key in ('anchor','chapter','section','level'):
        if key in source.unit: host[key]=source.unit[key]
    for key,value in host.items():
        if key in data and data[key]!=value:
            raise _contract_feedback('author changed frozen '+key, '/content/'+key)
        data[key]=value
    from .exercise_regions import bind_exercise_regions
    from .math_backlinks import derive_math_backlinks
    envelope['content'],derivation=derive_math_backlinks(data)
    errors, append_only = _collect_author_errors(envelope, source, validate_media=validate_media)
    envelope['content']['exercise_regions'] = bind_exercise_regions(
        envelope['content'].get('exercise_regions', []), source)
    # Source examples are verified by policy. Never overwrite a wrong original
    # statement to hide a model mistake, nor erase its false source attribution.
    try:
        authored=(ContentAuthoringV1.model_validate(envelope).as_dict() if validate_media else
                  {**envelope, 'content':LessonContentV4.model_validate(envelope['content']).as_dict()})
    except ContractError as exc:
        # 数学模型在**信封校验**阶段就不可解析（如未声明符号）—— 同样要变成反馈行，
        # 否则 R2-3 的"告诉模型怎么改"在最先一步就断了。
        # Keep the already collected exact model/object paths. The schema error
        # has lost the model index and must not overwrite those diagnostics.
        _raise_author_errors(errors, append_only)
        raise LocalCorrectionRequired('ContentAuthoringV1 local correction required: ' + str(exc),
            errors=[dict(path=_dotted_to_pointer(str(exc)), code='math_model_invalid', detail=str(exc),
                fix='按冻结数学契约修正：符号必须先声明再使用，只引用真实存在的对象与条件。',
                passing_criterion='数学模型可被严格解析，全部符号已声明、引用命中真实对象。')],
            allowed_paths=[_dotted_to_pointer(str(exc))], kind='math_references') from exc
    except ValidationError as exc:
        # 信封/媒体需求不符合契约时**不能**把 pydantic 异常漏出去（那样反馈就丢了）。
        # 收成可纠正错误行，并给每条一个可修位置。
        for error, path in zip(exc.errors(), _schema_paths([{'loc':list(e['loc']) if validate_media else ['content',*e['loc']],'msg':e['msg']} for e in exc.errors()])):
            pointer = '/' + '/'.join(str(part).replace('~','~0').replace('/','~1') for part in path)
            if not any(row['path'] == pointer for row in errors):
                errors.append({'loc':list(error['loc']),'type':error['type'],'msg':error['msg'],
                    'path':pointer,'code':'schema',
                    'fix':'按 ContentAuthoringV1 契约修正该字段；保留身份、冻结来源与此前已成立的内容。',
                    'passing_criterion':'严格 ContentAuthoringV1 与全部数学/来源/媒体检查通过。'})
        _raise_author_errors(errors, append_only)
    if validate_media and media_projection:
        from ..media.author_requirements import author_binding_findings, project_author_requests
        try:
            project_author_requests(authored['media_requirements'],authored['content'])
        except ContractError:
            # R2-3：绑定失败时把"改哪里"一并交给下一轮返修（裸抛会让反馈断掉）。
            covered={str(row.get('path') or '') for row in errors if row.get('path')}
            findings=[f for f in author_binding_findings(authored['media_requirements'],authored['content'])
                      if not any(str(f['path'])==c or str(f['path']).startswith(c+'/') or c.startswith(str(f['path'])+'/')
                                 for c in covered)]
            if findings:
                errors.extend(findings)
            elif covered:
                errors.extend({'path':c, 'code':'media_binding',
                    'detail':'作者媒体需求与当前正文/目标不一致（见同路径的其它诊断）。',
                    'fix':'按反馈补齐或修正该位置的作者媒体需求，保留其它已成立的内容。',
                    'passing_criterion':'作者媒体需求与当前正文、目标、主张和数学模型完全一致。'}
                    for c in sorted(covered))
            else:
                raise
    _raise_author_errors(errors, append_only)
    if derivation_sink is not None:
        derivation_sink({**derivation,'raw_response_hash':digest_of({'response':raw}),
                         'accepted_content_hash':digest_of(authored['content'])})
    return authored


def _validate_content_only(raw, source):
    """R4：正文返修的校验器 —— **只校验正文**，不校验 `media_requirements`。

    为什么：返修走"整份重写"，若连媒体需求一起校验，模型抄漏一个字段就会把
    **已经修好的正文**连坐丢掉（历史回归案例 的 SS01 正是如此：
    例题解析已补好，却因 `media_requirements/decisions/0/target_id` 缺字段整次失败）。
    媒体需求的问题留到媒体阶段处理。
    """
    return _validate_author_output(raw, source, media_projection=False, validate_media=False)



def _dotted_to_pointer(message):
    """把 `claims[0].math_object_id` 这类点分路径变成 JSON Pointer（`/content/...`）。"""
    head, _, _ = message.partition(':')
    head = head.strip()
    if not head:
        return '/content/math_models'
    pointer = ''
    for token in head.split('.'):
        name, _, index = token.partition('[')
        if name:
            pointer += '/' + name
        if index:
            pointer += '/' + index.rstrip(']')
    return '/content/math_models' + pointer


def _schema_paths(errors):
    paths=[]
    for error in errors:
        path=tuple(error['loc'])
        if 'options only allowed for choice' in error['msg']: path+=('input_spec','options')
        elif not path:
            path=('content',)  # Diagnostic location, never a field whitelist.
        paths.append(path)
    return paths


def _collect_author_errors(envelope, source, *, validate_media=True):
    """Collect independent local diagnostics before spending a correction round.

    用户 2026-10-11 拍板删除的检查（"不要再因为一些无所谓的卡住"）：
    reader_pointer 锚点、source_refs 逐字引文、读者文字控制字符、习题区范围/边界。
    这些检查连同 preserve 护栏一起删掉；剩下的可纠正错误只有
    **数学结构（AST/符号名/对象引用）+ 作者媒体诊断 + schema** 三类。

    A schema-only copy omits an already diagnosed math model solely to let
    Pydantic expose other fields; it is never returned or used for acceptance.
    The full original model must pass the unchanged strict validators afterward.
    """
    from ..media.author_diagnostics import author_dependency_errors, author_grammar_errors, author_decision_errors
    errors = list(author_dependency_errors(envelope)) if validate_media else []
    if validate_media: errors.extend(author_decision_errors(envelope))
    grammar_errors = author_grammar_errors(envelope) if validate_media else []
    errors.extend(grammar_errors)
    append_only = {}
    schema_candidate = deepcopy(envelope)
    # Region identities/bytes are always host-owned: bind them on the diagnostic copy
    # (原 exercise_region_errors 的候选恢复随该检查一起删除).
    from .exercise_regions import bind_exercise_regions
    schema_candidate['content']['exercise_regions'] = bind_exercise_regions(
        envelope['content'].get('exercise_regions', []), source)
    if grammar_errors:
        # A diagnostic-only copy exposes unrelated schema faults without a
        # container-level validator hiding exact, frozen grammar candidates.
        # This copy is never accepted: the author must return each correction.
        for error in grammar_errors:
            parts = error['path'].lstrip('/').split('/')
            parent = schema_candidate
            for token in parts[:-1]:
                parent = parent[int(token)] if isinstance(parent, list) else parent[token]
            parent[parts[-1]] = deepcopy(error['candidates'][0])
    try:
        validate_author_math(envelope['content'], source=source)
    except LocalCorrectionRequired as exc:
        errors.extend(exc.errors)
        append_only.update(exc.append_only)
        schema_candidate = deepcopy(schema_candidate)
        schema_candidate['content']['math_models'] = []
    except ContractError as exc:
        # R2-3：数学模型的**解析**错误（如 `claims[0].math_object_id: unknown math_object_id`）
        # 原来是裸抛 → lesson 直接 blocked、journal 里没有 merged_feedback，
        # 下一轮返修看不到"改哪里才算过"。转成带 JSON Pointer 的反馈行。
        errors.append(dict(path=_dotted_to_pointer(str(exc)), code='math_model_invalid',
            detail=str(exc),
            fix='按冻结数学契约修正该对象/引用：只引用真实存在的 math_object_id、为缺失对象补真实定义、保留符号与条件。',
            passing_criterion='数学模型可被严格解析，全部引用命中真实对象，符号已声明且条件保留。'))
        schema_candidate = deepcopy(schema_candidate)
        schema_candidate['content']['math_models'] = []

    # R3-1：常量占位检查必须**每轮都跑**。删掉内层纠正轮后，占位护栏只在"上一轮已授权追加"
    # 时才被调用，模型首份就交 `{"op":"const","value":0}` 时会直接通过（实测复现）。
    from .math_corrections import placeholder_object_findings
    placeholder_errors, _placeholder_paths, placeholder_append = placeholder_object_findings(envelope['content'])
    for error in placeholder_errors:
        if not any(row['path'] == error['path'] for row in errors):
            errors.append(error)
    append_only.update(placeholder_append)
    try:
        if validate_media: ContentAuthoringV1.model_validate(schema_candidate)
        else: LessonContentV4.model_validate(schema_candidate['content'])
    except ValidationError as exc:
        schema_errors = [{'loc':list(e['loc']) if validate_media else ['content',*e['loc']], 'type':e['type'], 'msg':e['msg']} for e in exc.errors()]
        for error, path in zip(schema_errors, _schema_paths(schema_errors)):
            pointer = '/' + '/'.join(str(part).replace('~','~0').replace('/','~1') for part in path)
            if not any(row['path'] == pointer for row in errors):
                errors.append(dict(error, path=pointer, code='schema',
                    fix='Repair the schema and related fields as needed; preserve existing identity and frozen source facts.',
                    passing_criterion='Strict ContentAuthoringV1 and all mathematical/source checks pass.'))
    return errors, append_only


def _math_placeholder_guard(before, after, error):
    """纠正轮里的**数学**复检：占位对象必须真的变成依赖变量的对象。

    原来是 `_guard_author_correction`（它把 preserve 逐叶比对 + 这条数学复检耦在一起）。
    preserve 已按用户决定删除，这条数学复检**保留** —— 它防的是"把常量占位换个名字交回来"，
    否则 CAS 校验会被绕过。这里直接转发到 `math_corrections.guard_author_correction`。
    """
    from ...core.review_mode import reviews_frozen
    if not reviews_frozen():
        guard_author_correction(before, after, error)


def generate_content(context, policy, client, response_sink=None, *, interaction_capabilities=None,
                     correction_journal=None, max_corrections=0, derivation_sink=None,
                     media_projection=True, previous_rejection=None,
                     available_theorem_names=None, available_theorem_explanations=None):
    from ...core.review_mode import substantive_reviews_enabled
    if substantive_reviews_enabled():
        max_corrections = 2
    source=SourceContext.model_validate(as_dict(context))
    prompt_context=as_dict(context)
    if interaction_capabilities is not None:
        prompt_context={**prompt_context,'interaction_capabilities':deepcopy(interaction_capabilities)}
    if available_theorem_names is not None:
        prompt_context['available_theorem_names'] = deepcopy(available_theorem_names)
    # Only older frozen requests use the full material field.
    if available_theorem_explanations is not None:
        prompt_context['available_theorem_explanations'] = deepcopy(available_theorem_explanations)
    if previous_rejection:
        prompt_context['previous_rejection']=deepcopy(previous_rejection)
    messages=build_content_messages('author',prompt_context,policy)
    # preserve 护栏（逐叶身份/来源比对）已按用户决定删除；纠正轮只保留**数学**占位复检。
    validator=(lambda raw: _validate_author_output(raw, source, derivation_sink=derivation_sink)) if media_projection else \
        (lambda raw: _validate_content_only(raw, source))
    return run_local_generation(messages, client, validator,
        tag=f'content-author:{source.unit_id}', correction_tag=f'content-structure-repair:{source.unit_id}',
        response_sink=response_sink, correction_journal=correction_journal, max_corrections=max_corrections,
        correction_guard=_math_placeholder_guard)
