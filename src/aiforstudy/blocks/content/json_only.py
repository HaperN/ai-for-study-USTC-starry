"""Non-reviewing adapters for consuming JSON while reviews are frozen.

Defaults only fill absent fields; a supplied host context owns run identity and
source metadata. Prose, mathematical data and unknown fields remain intact.
"""
from copy import deepcopy

from .content_contracts import LessonContentV4

_TEACHING_GROUPS = ('prerequisite_recaps', 'annotations', 'explainables', 'examples', 'concepts',
                    'teaching_evidence', 'math_models', 'learning_checks', 'exercise_regions',
                    'notation', 'corrections')


def _mapping(value):
    if hasattr(value, 'as_dict'):
        value = value.as_dict()
    return deepcopy(value) if isinstance(value, dict) else {}


def normalize_content(value, source=None):
    data = _mapping(value)
    if not isinstance(value, dict) and not hasattr(value, 'as_dict'):
        data['unreviewed_content'] = deepcopy(value)
        if isinstance(value, str):
            data['body_markdown'] = value
    context = _mapping(source)
    data.setdefault('_meta', {'schema_version': '4.0'})
    for field in ('unit_id', 'book_id', 'original_text', 'original_sha256',
                  'source_context_hash', 'anchor', 'chapter', 'section'):
        data.setdefault(field, context.get(field, ''))
    if source is not None:
        # These values route/store the result; model-authored identifiers must
        # not redirect a lesson or make its next read fail. The original model
        # response remains in its journal/sink, and reader-only parsing does not
        # supply a host context or change identifiers.
        for field in ('unit_id', 'book_id', 'original_text', 'original_sha256'):
            data[field] = deepcopy(context.get(field, ''))
        data['source_context_hash'] = getattr(source, 'digest', context.get('source_context_hash', ''))
        unit = context.get('unit')
        if isinstance(unit, dict):
            for field in ('anchor', 'chapter', 'section', 'level'):
                if field in unit:
                    data[field] = deepcopy(unit[field])
    for field in ('title', 'reader_intro', 'body_markdown', 'summary', 'one_line'):
        data.setdefault(field, '')
    if source is not None and (not isinstance(data['title'], str) or not data['title'].strip()):
        unit = context.get('unit') or {}
        title = unit.get('title') if isinstance(unit, dict) else None
        if isinstance(title, str) and title.strip():
            data['title'] = title
    data.setdefault('revision', 0)
    data.setdefault('level', None)
    for field in _TEACHING_GROUPS:
        data.setdefault(field, [])
    data.setdefault('teaching_plan', {})
    if isinstance(data['teaching_plan'], dict):
        for field in ('goals', 'claims', 'prerequisites'):
            data['teaching_plan'].setdefault(field, [])
    # Reader object defaults are structural conveniences, never invented lessons.
    for group in ('prerequisite_recaps', 'annotations', 'explainables', 'examples',
                  'concepts', 'learning_checks', 'exercise_regions', 'notation', 'corrections'):
        rows = data[group] if isinstance(data[group], list) else []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            row.setdefault('id', f'unreviewed-{group}-{index}')
            if group == 'learning_checks' and (row.get('answer') is None or row.get('answer') == ''):
                # Display an explicitly selected answer, without inventing an
                # accepted-input rule, check kind, or automatic grader.
                selected, options = row.get('answer_index'), row.get('options')
                if (type(selected) is int and isinstance(options, list)
                        and 0 <= selected < len(options) and isinstance(options[selected], str)):
                    row['answer'] = options[selected]
            if group != 'learning_checks':
                for field in ('title', 'name', 'label', 'markdown'):
                    row.setdefault(field, '')
            if group in ('examples', 'explainables'):
                for field in ('steps', 'key_points', 'pitfalls', 'source_refs', 'basis_refs'):
                    row.setdefault(field, [])
                for field in ('what', 'why', 'warmup', 'approach', 'statement', 'basis',
                              'conclusion', 'book_solution'):
                    row.setdefault(field, '')
                for step_index, step in enumerate(row['steps'] if isinstance(row['steps'], list) else []):
                    if isinstance(step, dict):
                        step.setdefault('id', f'{row["id"]}-step-{step_index}')
                        step.setdefault('no', step_index + 1)
                        step.setdefault('markdown', '')
                        step.setdefault('basis', '')
                        step.setdefault('basis_refs', [])
    return data


class UnreviewedContent(LessonContentV4):
    """Keep the reader's V4 interface without running V4 validators."""
    def as_dict(self):
        return deepcopy(self._json_only_data)


def content_record(value):
    data = normalize_content(value)
    record = UnreviewedContent.model_construct(**data)
    object.__setattr__(record, '_json_only_data', data)
    return record


def normalize_author_output(raw, source=None):
    from .generation_corrections import is_correction_wrapper, shape_error
    if is_correction_wrapper(raw) or is_correction_wrapper(_mapping(raw).get('content')):
        # 修改计划 A4：纠正请求包装不是正文；不规范化成一份空正文。
        raise shape_error('答复顶层是纠正请求包装，不是正文对象', code='correction_wrapper_returned',
                          fix='请只返回修好的完整 JSON：顶层为 {"content": {...正文...}}，不要返回纠正请求本身。',
                          passing_criterion='顶层是正文信封，content.body_markdown 非空。')
    envelope = _mapping(raw)
    content = envelope.get('content', raw)
    if isinstance(content, str):
        content = {'body_markdown':content, 'unreviewed_content':content}
    if isinstance(content, dict) and 'content' in envelope:
        content = deepcopy(content)
        # The loose envelope may place teaching material next to content. Keep
        # nested order, then retain root-only material; IDs/titles are not a
        # deduplication key and repeated root rows remain repeated.
        for field in (*_TEACHING_GROUPS, 'teaching_plan', 'title', 'reader_intro',
                      'body_markdown', 'summary', 'one_line',
                      # 修改计划 B7：作者对工单的内部说明可写在根或 content 里，统一保留在 content。
                      'ticket_responses'):
            if field not in envelope:
                continue
            if field in content:
                content[field] = _merge_teaching_value(content[field], envelope[field],
                    join_text=field in ('reader_intro', 'body_markdown', 'summary', 'one_line'))
            else:
                content[field] = deepcopy(envelope[field])
    if (isinstance(content, dict)
            and (not isinstance(content.get('title'), str) or not content['title'].strip())
            and isinstance(envelope.get('title'), str) and envelope['title'].strip()):
        content = {**content, 'title':envelope['title']}
    envelope['content'] = normalize_content(content, source)
    body = envelope['content'].get('body_markdown')
    if not isinstance(body, str) or not body.strip():
        raise shape_error('字段形状错误：规范化后正文 body_markdown 为空', code='empty_body',
                          path='/content/body_markdown',
                          fix='返回完整正文对象：content.body_markdown 写出本节完整正文，不要只返回包装或片段。',
                          passing_criterion='content.body_markdown 为非空的完整正文。')
    if source is not None:
        from .concept_corrections import correction_shape_errors
        shared = correction_shape_errors(envelope['content'], _mapping(source))
        if shared:
            # 修改计划 C2（用户 2026-10-20 定放宽）：无效的公共卡修正不打回整稿，只记警告；
            # proposed_corrections 本来就不会发布它，审核材料里能看到。
            envelope['correction_warnings'] = [row['detail'] for row in shared]
    envelope.setdefault('schema_version', 'ContentAuthoringV1')
    envelope.setdefault('media_requirements', {'schema_version': 'AuthorMediaRequirementsV1',
                                                'decisions': [], 'requests': []})
    if not isinstance(raw, dict):
        envelope['unreviewed_response'] = deepcopy(raw)
    return envelope


def _merge_teaching_value(nested, root, *, join_text=False):
    if nested is None or nested == '' or nested == [] or nested == {}:
        return deepcopy(root)
    if isinstance(nested, list) and isinstance(root, list):
        result, unmatched = deepcopy(nested), list(nested)
        for item in root:
            if item in unmatched:
                unmatched.remove(item)
            else:
                result.append(deepcopy(item))
        return result
    if isinstance(nested, dict) and isinstance(root, dict):
        result = deepcopy(nested)
        for key, value in root.items():
            result[key] = (_merge_teaching_value(result[key], value)
                           if key in result else deepcopy(value))
        return result
    if join_text and isinstance(nested, str) and isinstance(root, str) and root.strip() and root != nested:
        return nested + '\n\n' + root
    return deepcopy(nested)


def normalize_media_requirements(value):
    data = _mapping(value)
    if not isinstance(value, dict):
        data['unreviewed_requirements'] = deepcopy(value)
    data.setdefault('schema_version', 'AuthorMediaRequirementsV1')
    data.setdefault('decisions', [])
    data.setdefault('requests', [])
    rows = data['requests'] if isinstance(data['requests'], list) else []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        row.setdefault('request_key', f'unreviewed-request-{index}')
        row.setdefault('kind', 'animation' if row.get('type') == 'animation' else 'image')
        for field in ('goal_refs', 'claim_ids', 'math_model_ids', 'object_refs', 'conditions',
                      'acceptance_criteria'):
            row.setdefault(field, [])
        for field in ('learning_goal', 'observe', 'misconception', 'mathematical_objects',
                      'caption', 'alt_text'):
            row.setdefault(field, '')
        row.setdefault('placement', {})
        row.setdefault('animation', None)
    return data


def source_context_record(value):
    from .source_context import (SourceContext, FrozenReferenceCatalog, ReferenceEntryV1,
                                 LearnerProfileV1, SourceExampleV1, SourceObligationV1)
    from .content_contracts import SourceRefV1
    data = _mapping(value)
    for field in ('book_id', 'unit_id', 'source_text', 'original_text', 'original_sha256',
                  'source_context_hash'):
        data.setdefault(field, '')
    for field in ('source_inputs', 'unit', 'chapters', 'concept_index'):
        data.setdefault(field, {})
    data.setdefault('reference_catalog', {'entries': []})
    data.setdefault('learner_profile', {})
    data.setdefault('source_examples', [])
    data.setdefault('source_obligations', [])
    data.setdefault('schema_version', 'SourceContextV1')
    data.setdefault('obligation_extraction_status', 'structural_only')
    fields = deepcopy(data)
    catalog = _mapping(fields['reference_catalog'])
    catalog['entries'] = [ReferenceEntryV1.model_construct(**row) for row in catalog.get('entries', [])
                          if isinstance(row, dict)]
    fields['reference_catalog'] = FrozenReferenceCatalog.model_construct(**catalog)
    fields['learner_profile'] = LearnerProfileV1.model_construct(**_mapping(fields['learner_profile']))
    fields['source_examples'] = [SourceExampleV1.model_construct(**row)
                                for row in fields['source_examples'] if isinstance(row, dict)]
    obligations = []
    for row in fields['source_obligations']:
        if not isinstance(row, dict):
            continue
        row = deepcopy(row)
        row['source_refs'] = [SourceRefV1.model_construct(**ref) for ref in row.get('source_refs', [])
                              if isinstance(ref, dict)]
        obligations.append(SourceObligationV1.model_construct(**row))
    fields['source_obligations'] = obligations

    class UnreviewedSource(SourceContext):
        def as_dict(self):
            return deepcopy(self._json_only_data)

        def model_copy(self, *, update=None, deep=False):
            return source_context_record({**self.as_dict(), **deepcopy(update or {})})

    record = UnreviewedSource.model_construct(**fields)
    object.__setattr__(record, '_json_only_data', data)
    return record
