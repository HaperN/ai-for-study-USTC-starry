"""Source-only contract for approval-bound Matplotlib and Manim programs.

AST inspection checks syntax and the public entry point, never Python safety or
mathematical truth. Only the isolated worker may execute the resulting source.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ...errors import ContractError
from ...core.review_mode import reviews_frozen
from ..content.content_contracts import as_dict

MAX_CODE_CHARS = 100_000
BACKENDS = {'drawing':'manim', 'animation':'manim'}


class _CodeRecord(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    @field_validator('*')
    @classmethod
    def nonblank_text(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError('text must not be blank')
        return value

    def as_dict(self):
        return self.model_dump(mode='json', exclude_unset=True)


class MediaCodeStepV1(_CodeRecord):
    step_id: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    author_step_index: int = Field(ge=0)
    caption: str = Field(min_length=1)
    observation: str = Field(min_length=1)
    expected_change: str = Field(min_length=1)
    # Optional authored layers. Legacy steps remain valid; review metadata is
    # an editorial assertion, never a mathematical or pronunciation proof.
    narration_text: str | None = None
    canonical_spoken_text: str | None = None
    display_markdown: str | None = None
    director_notes: str | None = None
    spoken_review: dict[str, str] | None = None


class MediaCodeObjectBindingV1(_CodeRecord):
    display_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    object_id: str = Field(min_length=1)


class GeneratedMediaCodeV1(_CodeRecord):
    schema_version: Literal['GeneratedMediaCodeV1']
    request_id: str = Field(min_length=1)
    backend: Literal['matplotlib','manim']
    code: str = Field(min_length=1, max_length=MAX_CODE_CHARS)
    steps: list[MediaCodeStepV1]
    object_bindings: list[MediaCodeObjectBindingV1]


def _source_entry(code, backend):
    try:
        tree = ast.parse(code, filename='<generated-media>', mode='exec')
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise ContractError('GeneratedMediaCodeV1 Python syntax: '+str(exc)) from exc
    name = 'build_figure' if backend == 'matplotlib' else 'GeneratedScene'
    entries = [node for node in tree.body if getattr(node, 'name', None) == name]
    if len(entries) != 1:
        raise ContractError('generated code requires one top-level '+name+' entry')
    entry = entries[0]
    if backend == 'matplotlib':
        if not isinstance(entry, ast.FunctionDef):
            raise ContractError('build_figure(context) must be a synchronous function')
        args = entry.args
        positional = [*args.posonlyargs, *args.args]
        if (len(positional) != 1 or positional[0].arg != 'context'
                or args.kwonlyargs or args.vararg or args.kwarg or args.defaults):
            raise ContractError('static entry must have signature build_figure(context)')
    elif not isinstance(entry, ast.ClassDef) or not entry.bases:
        raise ContractError('GeneratedScene must be a class inheriting a Manim scene or a custom scene base')


def _requested_objects(request):
    refs = request.get('object_refs')
    if not isinstance(refs, list):
        raise ContractError('request.object_refs must be an explicit array')
    objects = set()
    for ref in refs:
        if (not isinstance(ref, dict) or set(ref) != {'model_id','object_id'}
                or any(not isinstance(ref[key], str) or not ref[key].strip() for key in ref)):
            raise ContractError('request object_refs require model_id/object_id')
        pair = (ref['model_id'], ref['object_id'])
        if pair in objects:
            raise ContractError('request object_refs must be unique')
        objects.add(pair)
    return objects


def validate_media_code(program, request, content=None):
    """Return a defensive dict, or raise ContractError; never import/exec source.

    ``content`` optionally checks approved model digests and object existence.
    Receipt/dependency/placement validation is the generation caller's boundary;
    actual media_step coverage and scene type are checked by the worker.
    """
    if reviews_frozen():
        raw = program.as_dict() if hasattr(program, 'as_dict') else program
        try:
            return json.loads(json.dumps(raw, ensure_ascii=False, allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise ContractError('Generated media JSON: '+str(exc)) from exc
    if not isinstance(request, dict) or request.get('kind') not in BACKENDS:
        raise ContractError('generated code requires request kind drawing or animation')
    try:
        raw = program.as_dict() if hasattr(program, 'as_dict') else program
        result = GeneratedMediaCodeV1.model_validate(raw).as_dict()
    except (ValidationError, TypeError, ValueError) as exc:
        raise ContractError('GeneratedMediaCodeV1: '+str(exc)) from exc
    if result['request_id'] != request.get('request_id'):
        raise ContractError('generated code request_id differs from frozen request')
    # Existing Matplotlib candidates remain readable/replayable. New generation
    # always selects BACKENDS; the request kind distinguishes stills from video.
    legacy_static = request['kind'] == 'drawing' and result['backend'] == 'matplotlib'
    if result['backend'] != BACKENDS[request['kind']] and not legacy_static:
        raise ContractError('generated code backend differs from frozen request kind')
    expected = _requested_objects(request)
    bindings = result['object_bindings']
    displays = [binding['display_id'] for binding in bindings]
    if len(displays) != len(set(displays)):
        raise ContractError('object_bindings display_id must be unique')
    actual = {(binding['model_id'], binding['object_id']) for binding in bindings}
    if actual != expected:
        def refs_json(pairs):
            return json.dumps([{'model_id':model_id, 'object_id':object_id}
                               for model_id, object_id in sorted(pairs)], ensure_ascii=False)
        raise ContractError(
            'object_bindings must cover exactly the approved request.object_refs; '
            f'missing={refs_json(expected - actual)}; extra={refs_json(actual - expected)}; '
            f'allowed={refs_json(expected)}. '
            '补齐 missing、移除 extra；即使完整正文含有其他对象，也不能新增绑定。'
            '允许同一批准对象对应多个唯一 display_id，不得修改冻结 request.object_refs。')
    if content is not None:
        from .media_requirements import _validate_math
        try:
            _validate_math(request, as_dict(content))
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractError('generated code approved mathematical context: '+str(exc)) from exc
    steps = result['steps']
    if request['kind'] == 'drawing':
        if steps:
            raise ContractError('static generated code steps must be []')
    else:
        author = request.get('animation')
        author_steps = author.get('steps') if isinstance(author, dict) else None
        if (not isinstance(author_steps, list) or not author_steps
                or any(not isinstance(step, str) or not step.strip() for step in author_steps)):
            raise ContractError('animation requires the frozen author animation.steps')
        ids = [step['step_id'] for step in steps]
        indices = [step['author_step_index'] for step in steps]
        if len(ids) != len(set(ids)):
            raise ContractError('animation step_id must be unique')
        allowed = list(range(len(author_steps)))
        if indices != sorted(indices) or set(indices) != set(allowed):
            invalid = [f'steps[{i}].author_step_index received={index}'
                       for i, index in enumerate(indices) if index not in allowed]
            out_of_order = [f'steps[{i}].author_step_index received={indices[i]} '
                            f'follows steps[{i - 1}] received={indices[i - 1]}'
                            for i in range(1, len(indices)) if indices[i] < indices[i - 1]]
            missing = sorted(set(allowed) - set(indices))
            raise ContractError(
                'author_step_index must cover every frozen author step in order from 0; '
                f'received_indices={indices}; allowed={allowed}; missing={missing}; '
                f'invalid={invalid}; out_of_order={out_of_order}. '
                '每个制作步骤必须归属于现有作者步骤，并按作者顺序完整覆盖；'
                '额外收尾请并入现有作者末步，或作为其子步骤并使用 '
                f'author_step_index={allowed[-1]}；不能增加作者步骤或修改冻结 request.animation.steps。')
    _source_entry(result['code'], result['backend'])
    return deepcopy(result)
