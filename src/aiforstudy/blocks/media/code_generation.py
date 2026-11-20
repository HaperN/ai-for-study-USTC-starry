"""One existing-gateway call produces open Python; the host owns all authority."""
from __future__ import annotations

from copy import deepcopy
import json

from ...core.digest import digest_of
from ...core.prompt_assets import compose_prompt_assets
from ...core.review_mode import reviews_frozen, substantive_reviews_enabled
from ...errors import CapabilityError, ContractError
from ..content.content_contracts import as_dict
from .code_contracts import BACKENDS, MAX_CODE_CHARS, GeneratedMediaCodeV1, validate_media_code
from .animation_example import cross_terms_example
from .static_example import conjugate_static_example
from .math_context import ApprovedMathContext
from .media_context import focused_media_context
from .media_requirements import resolve_bound_placement
from .review_policy import DEFAULT_DISPLAY_PROFILES, build_media_quality_contract


TRANSPORT_FAILURE_KINDS = frozenset({'network', 'timeout'})
TRANSPORT_MARKERS = ('IncompleteRead', 'SSLEOF', 'ConnectionError', 'TimeoutError', 'RemoteDisconnected',
                     'ConnectionReset', 'BrokenPipe')


class MediaCodeGenerationError(ContractError):
    """Keep the exact failed candidate for persistence and a bounded repair."""
    def __init__(self, message, candidate):
        super().__init__(message)
        self.candidate = deepcopy(candidate)



def _example(kind):
    """Executable examples have their own context, separate from this lesson."""
    return conjugate_static_example() if kind == 'drawing' else cross_terms_example()


def _inputs(request, content, approval):
    if not isinstance(request, dict) or request.get('kind') not in BACKENDS:
        raise CapabilityError('media code generation requires drawing or animation; no legacy fallback')
    request, content, approval = deepcopy(request), as_dict(content), as_dict(approval)
    if reviews_frozen():
        return request, content, approval
    context = ApprovedMathContext.from_content_binding(content, approval, request)
    resolve_bound_placement(content, request.get('placement'))
    return context.request, content, approval


def _repair(feedback, previous, request):
    feedback = deepcopy(feedback)
    previous = deepcopy(previous.as_dict() if hasattr(previous, 'as_dict') else previous)
    images = []
    image_labels = []
    if isinstance(feedback, dict):
        images = feedback.pop('images', [])
        image_labels = feedback.pop('image_labels', [])
        if not isinstance(images, list) or any(not isinstance(path, str) or not path.strip() for path in images):
            raise ContractError('repair feedback images must be a list of actual frame paths')
    if previous is not None:
        if not isinstance(previous, dict) and not substantive_reviews_enabled():
            raise ContractError('repair previous must be the original generated program object')
        # Invalid candidates can have wrong IDs/backends. Preserve them as raw
        # diagnostic evidence without granting their metadata any authority.
    return {'previous_program':previous,
            'original_code':previous.get('code') if isinstance(previous, dict) else None,
            'feedback':feedback, 'attached_image_count':len(images),
            'instruction':'previous_program 可能结构无效、ID或后端错误，只是原始返修证据，不能覆盖本次冻结 request。修复报告中的制作问题，完整保持本次请求身份、全部作者步骤、批准数学依据和原验收标准。'}, images, image_labels


_EDITORIAL_SPEECH_FIELDS = ('canonical_spoken_text', 'spoken_review')
_SPEECH_CONTEXT_FIELDS = ('author_step_index', 'caption', 'observation', 'expected_change',
                          'narration_text', 'display_markdown', 'director_notes')


def _model_output_schema(backend):
    schema = GeneratedMediaCodeV1.model_json_schema()
    schema['properties']['backend']['enum'] = [backend]
    for field in _EDITORIAL_SPEECH_FIELDS:
        schema['$defs']['MediaCodeStepV1']['properties'].pop(field, None)
    return schema


def _model_candidate(raw, request, content, *, editorial_original=None):
    """Keep editorial authority out of model responses, in every review mode.

    A technical fix can copy or omit existing editorial fields, but the host
    restores their values from its captured original, never from model output.
    Restoring them requires unchanged step identity and associated speech text.
    New generation has no authority to create an editorial approval.
    """
    candidate = deepcopy(raw)
    originals = {}
    original_steps = (editorial_original or {}).get('steps') or []
    for step in original_steps if isinstance(original_steps, list) else []:
        if not isinstance(step, dict) or not any(key in step for key in _EDITORIAL_SPEECH_FIELDS):
            continue
        step_id = step.get('step_id')
        if not isinstance(step_id, str) or step_id in originals:
            raise ContractError('editorial narration requires unique original step_id values')
        originals[step_id] = step
    steps = candidate.get('steps') if isinstance(candidate, dict) else None
    restored = set()
    for step in steps if isinstance(steps, list) else []:
        if not isinstance(step, dict):
            continue
        original = originals.get(step.get('step_id'))
        for key in _EDITORIAL_SPEECH_FIELDS:
            if key in step and (original is None or key not in original or step[key] != original[key]):
                raise ContractError('model cannot add or change editorial narration fields: '+key)
        if original is None:
            continue
        step_id = step['step_id']
        if step_id in restored:
            raise ContractError('editorial narration cannot be copied onto duplicate step_id values')
        if any(step.get(key) != original.get(key) for key in _SPEECH_CONTEXT_FIELDS):
            raise ContractError('editorial narration cannot be reused after speech-related content changes')
        for key in _EDITORIAL_SPEECH_FIELDS:
            if key in original:
                step[key] = deepcopy(original[key])
        restored.add(step_id)
    if restored != set(originals):
        raise ContractError('technical fix removed or renamed a step carrying editorial narration')
    return validate_media_code(candidate, request, content)


def _messages(request, content, approval, repair):
    backend = BACKENDS[request['kind']]
    display_profiles = deepcopy(list(DEFAULT_DISPLAY_PROFILES))
    quality_contract = build_media_quality_contract(request['kind'])
    quality_contract['display_profiles'] = deepcopy(display_profiles)
    model_ids = {ref['model_id'] for ref in request.get('math_model_refs',[]) if isinstance(ref,dict) and 'model_id' in ref}
    animation = request['kind'] == 'animation'
    author_steps = (request.get('animation') or {}).get('steps',[]) if animation else []
    allowed_indices = list(range(len(author_steps)))
    output_schema = _model_output_schema(backend)
    if animation:
        output_schema['$defs']['MediaCodeStepV1']['properties']['author_step_index']['enum'] = allowed_indices
    focused = focused_media_context(content, approval, request)
    payload = {'role_task':'media_code_generation','backend':backend,
               'request':request,
               # Prompt input only: the passage this media serves, a one-line
               # outline of the section and the entries it links to. The full
               # content/approval stay with the host for validation and runtime.
               'approved_content':{'section_outline':focused['section_outline'],
                                   'related_excerpts':focused['related_excerpts'],
                                   'referenced_entries':focused['referenced_entries']},
               'approval':focused['approval_binding'],
               'approved_models':[deepcopy(model) for model in content.get('math_models',[]) if model.get('model_id') in model_ids],
               'placement':deepcopy(request.get('placement')),
               'acceptance_criteria':deepcopy(request.get('acceptance_criteria',[])),
               'criteria_digest':request.get('criteria_digest'), 'request_digest':digest_of(request),
               'quality_contract':quality_contract, 'display_profiles':display_profiles,
               'allowed_object_bindings':deepcopy(request.get('object_refs',[])),
               'allowed_author_step_indices':allowed_indices,
               'author_step_mapping':[{'author_step_index':index, 'author_step':step}
                                      for index, step in enumerate(author_steps)],
               'output_json_schema':output_schema,
               'max_code_characters':MAX_CODE_CHARS, 'example':_example(request['kind'])}
    if repair is not None:
        payload['repair'] = repair
    prompt = compose_prompt_assets('media/code-common.md',
        'media/code-animation.md' if animation else 'media/code-static.md')
    desktop = display_profiles[0]
    width, minimum = desktop['content_width'], desktop['min_font_px']
    sizing = (f'mobject.height * {width} / config.frame_width；'
              f'不把每个字形包围盒高度等同于{minimum} CSS px字号门槛。'
              '这是显示比例参考；补充说明应小于公式和关键对象标签，不能把所有文字统一放大。')
    return [{'role':'system','content':prompt.text+'\n宿主电脑显示字号换算：'+sizing},
            {'role':'user','content':json.dumps(payload, ensure_ascii=False)}]


def media_code_prompt(request, *, content, approval, feedback=None, previous=None):
    """Build reviewable Chinese prompts from validated, frozen inputs."""
    request, content, approval = _inputs(request, content, approval)
    repair, _, image_labels = _repair(feedback, previous, request)
    messages = _messages(request, content, approval, repair if feedback is not None or previous is not None else None)
    if image_labels:
        messages[-1]['image_labels'] = image_labels
    return messages


def _new_backend(candidate):
    # Legacy artifacts remain executable through the runtime, but a newly
    # generated program must not silently select the retired drawing backend.
    # This is an executable-format error, outside the JSON correction loop.
    if isinstance(candidate, dict) and candidate.get('backend') == 'matplotlib':
        raise MediaCodeGenerationError(
            '新媒体制作已统一为 Manim：请返回 backend=manim 和 GeneratedScene；'
            '静态 drawing 的 steps=[]，由 worker 保存 PNG。保留原图意，勿返回 build_figure。', candidate)
    return candidate


def generate_media_code(request, *, content, approval, client, feedback=None, previous=None, correction_journal=None):
    """Generate one candidate with bounded JSON repair; never render it here.

    The orchestrator owns its global bounded attempt count. Failed candidates are
    retained on MediaCodeGenerationError.candidate; there is no template fallback.
    """
    request, content, approval = _inputs(request, content, approval)
    _, images, _ = _repair(feedback, previous, request)
    if client is None or not callable(getattr(client, 'chat_json', None)):
        raise CapabilityError('media code generation requires the existing figures API client')
    messages = media_code_prompt(request, content=content, approval=approval,
                                 feedback=feedback, previous=previous)
    from ..content.generation_corrections import (
        LocalCorrectionRequired, run_local_generation, _accepts_protocol_errors)
    from ...pipeline.services import reraise_control_error
    raw_candidate = [None]
    def remember(raw):
        raw_candidate[0] = deepcopy(raw)
    class GenerationClient:
        def chat_json(self, messages, **extra):
            for option in ('protocol_errors', 'syntax_repair'):
                if not _accepts_protocol_errors(client, option):
                    extra.pop(option, None)
            if images:
                extra['images'] = images
            return client.chat_json(messages, **extra)
    try:
        candidate = run_local_generation(messages, GenerationClient(),
            lambda raw: _model_candidate(raw, request, content), tag='media-code:' + request['request_id'],
            correction_tag='media-code-json:' + request['request_id'],
            max_corrections=2 if substantive_reviews_enabled() else 0,
            correction_journal=correction_journal, correction_guard=False, response_sink=remember)
    except Exception as exc:
        reraise_control_error(exc)
        failure = MediaCodeGenerationError(type(exc).__name__ + ': ' + str(exc), raw_candidate[0])
        # Exhausted syntax must not buy another outer program batch. Existing
        # full-mode semantic failures retain their original candidate allowance.
        syntax_exhausted = (isinstance(exc.__cause__, LocalCorrectionRequired)
                            and exc.__cause__.kind == 'syntax')
        # 网络/超时类失败不含任何“模型做不出合格答复”的信息：不停止，继续用剩余候选（用户 2026-10-20 定放宽）。
        from ...providers.review_failures import classify_review_failure
        transport = classify_review_failure(exc) in TRANSPORT_FAILURE_KINDS
        failure.generation_stopped = syntax_exhausted or (substantive_reviews_enabled() and not transport)
        raise failure from exc
    return _new_backend(candidate)


#: 修改计划 D6：每项媒体合计至多这么多次轻量技术修复，不另开候选；用完后技术错误照旧占候选。
MAX_TECHNICAL_FIXES = 2
TECHNICAL_ERROR_TAIL = 3000


def fix_media_code(request, *, content, approval, client, program, error, correction_journal=None):
    """One lightweight technical repair: the original program and the error tail only.

    The answer is validated exactly like a generated candidate. A missing or
    malformed program is a shape error inside the existing protocol corrections,
    never another technical fix.
    """
    request, content, approval = _inputs(request, content, approval)
    if client is None or not callable(getattr(client, 'chat_json', None)):
        raise CapabilityError('media technical fix requires the existing figures API client')
    backend = BACKENDS[request['kind']]
    schema = _model_output_schema(backend)
    editorial_original = deepcopy(program)
    payload = {'role_task': 'media_code_technical_fix', 'backend': backend,
               'request_id': request['request_id'], 'kind': request['kind'],
               'original_program': deepcopy(program),
               'error_tail': str(error or '')[-TECHNICAL_ERROR_TAIL:],
               'output_json_schema': schema, 'max_code_characters': MAX_CODE_CHARS}
    messages = [{'role': 'system', 'content': compose_prompt_assets('media/code-fix.md').text},
                {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}]
    from ..content.generation_corrections import run_local_generation, _accepts_protocol_errors

    class FixClient:
        def chat_json(self, messages, **extra):
            for option in ('protocol_errors', 'syntax_repair'):
                if not _accepts_protocol_errors(client, option):
                    extra.pop(option, None)
            return client.chat_json(messages, **extra)
    candidate = run_local_generation(messages, FixClient(),
        lambda raw: _model_candidate(raw, request, content, editorial_original=editorial_original), tag='media-code-fix:' + request['request_id'],
        correction_tag='media-code-fix-json:' + request['request_id'], max_corrections=2,
        correction_journal=correction_journal, correction_guard=False)
    return _new_backend(candidate)
