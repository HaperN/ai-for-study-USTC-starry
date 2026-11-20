"""Versioned pure content contracts; runtime receipts never enter content hashes."""

from __future__ import annotations
from copy import deepcopy
import re
from collections.abc import Mapping
from ...core.digest import digest_of
from ...errors import ContractError
from .reader_fields import teaching_pointer_pattern

# Legacy media attachments are stripped on import and rejected in pure content.
LEGACY_MEDIA_FIELDS = frozenset({'figure_requests', 'visual_requests', 'diagrams',
    'figures', 'animations', 'visual_spec'})
# Suggested claim edits; these fields do not restrict an author's repair scope.
CLAIM_REPAIR_FIELDS = ('conditions', 'conclusion', 'symbols')


class JsonRecord(Mapping):
    def __init__(self, data=None, **kw):
        self._data = deepcopy(dict(data or {}, **kw))

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def __getattr__(self, key):
        try:
            return self._data[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def as_dict(self):
        return deepcopy(self._data)


class LessonContentV3(JsonRecord):
    pass


class ReviewDocumentV1(JsonRecord):
    pass


class ContentApprovalV1(JsonRecord):
    pass


class ContentPatchV1(JsonRecord):
    pass


class ContentCheckReport(JsonRecord):
    pass


class ContentPatchResult(JsonRecord):
    pass


class PatchAuthorization(JsonRecord):
    pass


class ContentPolicy(JsonRecord):
    @property
    def digest(self):
        return content_digest(self._data)

    @property
    def policy_digest(self):
        return self.digest


def as_dict(value):
    return value.as_dict() if hasattr(value, "as_dict") else deepcopy(dict(value))


def content_digest(data):
    return digest_of(as_dict(data), check=False)


def parse_content(data: dict) -> LessonContentV3:
    if not isinstance(data, dict):
        raise ContractError("LessonContentV3 must be an object")
    if (data.get("_meta") or {}).get("schema_version") != "3.0":
        raise ContractError("LessonContentV3 _meta.schema_version must be 3.0")
    for key in ("unit_id", "title", "body_markdown"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ContractError(f"LessonContentV3 /{key} required")
    for key in ("annotations", "explainables", "examples", "concepts"):
        if not isinstance(data.get(key), list):
            raise ContractError(f"LessonContentV3 /{key} must be array")
    if not isinstance(data.get("teaching_plan"), dict):
        raise ContractError("LessonContentV3 /teaching_plan required")
    plan = data["teaching_plan"]
    for key in ("claims", "steps"):
        if not isinstance(plan.get(key), list):
            raise ContractError(f"LessonContentV3 /teaching_plan/{key} must be array")
        ids = set()
        for row in plan[key]:
            oid = (
                (row.get("id") or row.get("claim_id") or row.get("step_id"))
                if isinstance(row, dict)
                else None
            )
            if not oid or oid in ids:
                raise ContractError(
                    f"LessonContentV3 /teaching_plan/{key} requires unique object IDs"
                )
            ids.add(oid)
            if key == "claims" and not isinstance(row.get("text"), str):
                raise ContractError("claim text must be string")
    for group in ("annotations", "explainables", "examples"):
        for row in data[group]:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                raise ContractError(f"{group} requires stable string object ID")
            for key in (
                "what",
                "why",
                "markdown",
                "title",
                "body",
                "basis",
                "statement",
                "conclusion",
            ):
                if key in row and not isinstance(row[key], str):
                    raise ContractError(f'{group}/{row["id"]}/{key} must be string')
            if "warmup" in row and not isinstance(row["warmup"], (str, list)):
                raise ContractError(
                    f'{group}/{row["id"]}/warmup must be string or array'
                )
            for key in ("walkthrough", "steps", "key_points", "pitfalls"):
                if key in row and not isinstance(row[key], list):
                    raise ContractError(f'{group}/{row["id"]}/{key} must be array')

    def walk(v, p=""):
        if isinstance(v, dict):
            for k, x in v.items():
                if k in LEGACY_MEDIA_FIELDS:
                    raise ContractError(f"content contains media field {p}/{k}")
                walk(x, p + "/" + k)
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk(x, p + "/" + str(i))
        elif isinstance(v, str):
            import re

            if re.search(r"\[\[(?:FIGURE|MEDIA|DIAGRAM):|\bFIG-\w+", v):
                raise ContractError(f"content contains generated media at {p}")

    walk(data)
    return LessonContentV3(data)


# Shared Python grammar, also exported by legacy lib.refs for renderer parity.
REFERENCE_KINDS = (
    "CONCEPT",
    "EXPLAIN",
    "NOTE",
    "DIAGRAM",
    "FIGURE",
    "MODEL",
    "EXAMPLE",
)
REFERENCE_PATTERN = re.compile(
    r"\[\[("
    + "|".join(REFERENCE_KINDS)
    + r")\s*:\s*([^\]|]*?)\s*(?:\|\s*([^\]]*?)\s*)?\]\]"
)

# Version 4 is deliberately separate from the permissive historical V3 reader.
from typing import Literal, Any
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator, field_validator


class StrictContentModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, populate_by_name=True)

    def as_dict(self):
        return self.model_dump(mode='json', by_alias=True)

    def get(self, key, default=None):
        return self.as_dict().get(key, default)

    def __getitem__(self, key):
        return self.as_dict()[key]


class ContentMetaV4(StrictContentModel):
    schema_version: Literal['4.0']
    file_type: Literal['LessonContentV4'] = 'LessonContentV4'
    generator: str = ''


class SourceRefV1(StrictContentModel):
    kind: str
    id: str = ''
    unit_id: str = ''
    concept_id: str = ''
    example_id: str = ''
    claim_id: str = ''
    obligation_id: str = ''
    path: str = ''
    json_pointer: str = ''
    quote: str = Field(default='', description='来源引文，尽量沿用原文。有效 obligation_id、concept_id 或 example_id 可独立定位冻结来源；字面差异不等于来源不存在。未给可定位身份时须能按原句找到来源。语义忠实仍由审核判断，不能伪造来源或漏改条件。')
    start: int | None = None
    end: int | None = None
    source_line: int | None = None
    sha256: str = ''


class ReaderRecapV1(StrictContentModel):
    id: str = Field(min_length=1)
    title: str = ''
    markdown: str
    source_refs: list[SourceRefV1] = Field(default_factory=list)


class ReaderStepV1(StrictContentModel):
    id: str = Field(min_length=1)
    no: int | None = None
    markdown: str
    basis: str = ''
    basis_refs: list[str] = Field(default_factory=list)
    explanation_refs: list[str] = Field(default_factory=list)
    claim_refs: list[str] = Field(default_factory=list)
    source_refs: list[SourceRefV1] = Field(default_factory=list)


class AnnotationV4(StrictContentModel):
    id: str = Field(min_length=1)
    kind: str = 'note'
    title: str = ''
    markdown: str
    source_refs: list[SourceRefV1] = Field(default_factory=list)
    claim_refs: list[str] = Field(default_factory=list)


class ExplainableV4(StrictContentModel):
    id: str = Field(min_length=1)
    kind: Literal['definition','theorem','procedure','example','convention','formula','concept','notation']
    name: str
    concept_id: str = ''
    what: str = ''
    why: str = ''
    markdown: str
    warmup: str = ''
    steps: list[ReaderStepV1] = Field(default_factory=list)
    key_points: list[str] = Field(default_factory=list)
    pitfalls: list[str] = Field(default_factory=list)
    not_applicable_reason: str = ''
    source_refs: list[SourceRefV1] = Field(default_factory=list)
    claim_refs: list[str] = Field(default_factory=list)


class ExampleV4(StrictContentModel):
    id: str = Field(min_length=1)
    kind: Literal['source','derived_example']
    label: str = ''
    title: str = ''
    statement: str
    approach: str = ''
    warmup: str = ''
    basis: str = ''
    basis_refs: list[str] = Field(default_factory=list)
    explanation_refs: list[str] = Field(default_factory=list)
    steps: list[ReaderStepV1] = Field(default_factory=list)
    conclusion: str = ''
    key_points: list[str] = Field(default_factory=list)
    pitfalls: list[str] = Field(default_factory=list)
    source_refs: list[SourceRefV1] = Field(default_factory=list)
    book_solution: str = ''
    source_line: int | None = None


class ConceptUseV4(StrictContentModel):
    id: str = Field(min_length=1)
    role: Literal['introduced','reused','prerequisite']
    explained_where: str = Field(default='', description='相对于 content 的非空读者文本叶 JSON Pointer；数组用从0开始的数字索引，不能用对象ID或容器。无定位可省略。',
        json_schema_extra={'pattern': '^(?:$|' + teaching_pointer_pattern()[1:-1] + ')$'})


class VisualRequirementV1(StrictContentModel):
    required: bool
    reason: str = Field(min_length=1)

    @model_validator(mode='after')
    def justified(self):
        if not self.reason.strip():
            raise ValueError('visual requirement needs a substantive reason')
        return self


class TeachingGoalV1(StrictContentModel):
    id: str = Field(min_length=1)
    observable_task: str = Field(min_length=1)
    concept_refs: list[str] = Field(default_factory=list)
    claim_refs: list[str] = Field(default_factory=list)
    level: Literal['core','support']
    visual_requirement: VisualRequirementV1 | None = None


class PrerequisiteV1(StrictContentModel):
    id: str = Field(min_length=1)
    dependency_id: str
    treatment: Literal['assumed','recap','new_here']
    recap_id: str = ''


class TeachingClaimV1(StrictContentModel):
    id: str = Field(min_length=1)
    kind: Literal['definition','theorem','procedure','example','convention']
    source_refs: list[SourceRefV1]
    conditions: list[str]
    conclusion: str
    symbols: list[str]
    math_model_refs: list[str] = Field(default_factory=list,description=
        'Host derives missing reverse indexes from existing math_models whose claims.claim_id exactly equals this id. '
        'May be omitted; explicit references must all be unique existing models containing that exact claim_id. '
        'This index does not certify mathematical correctness.')


class TeachingPlanV1(StrictContentModel):
    goals: list[TeachingGoalV1]
    prerequisites: list[PrerequisiteV1]
    claims: list[TeachingClaimV1]


class TeachingEvidenceV1(StrictContentModel):
    goal_id: str
    purpose: Literal['definition','example','contrast','boundary','motivation','derivation','understanding_check']
    reader_pointer: str = Field(description='相对于 content 的已存在、非空读者文本叶 JSON Pointer，例如 /examples/0/steps/0/markdown；数组用数字索引，不能用对象ID、容器或原书习题资料。',
        json_schema_extra={'pattern': teaching_pointer_pattern()})
    claim_refs: list[str]
    quote: str = ''
    reader_role: Literal['main', 'proof', 'worked_example'] | None = Field(default=None, exclude_if=lambda value: value is None, description='省略表示兼容主线，不覆盖旧内容折叠；显式 main 保证正文可见。')


class LearningCheckCaseV1(StrictContentModel):
    id: str = Field(min_length=1)
    input: str | bool | float
    expected: Literal['correct', 'incorrect', 'invalid', 'manual']
    feedback: str = Field(default='', description='可选的预期 misconception_feedback.id；省略或空字符串表示只断言 expected 评分，不要求没有反馈。非空时必须精确匹配实际触发的错因分支 ID；每个错因分支仍须有实际触发的错误测试。')
    coverage: Literal['correct', 'wrong', 'boundary']


class LearningCheckOptionV1(StrictContentModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)


class LearningCheckInputV1(StrictContentModel):
    options: list[LearningCheckOptionV1] = Field(default_factory=list)
    placeholder: str = ''


class LearningCheckScoringV1(StrictContentModel):
    accepted_inputs: list[str | bool | float]
    absolute_tolerance: float = Field(default=0, ge=0, allow_inf_nan=False)
    relative_tolerance: float = Field(default=0, ge=0, allow_inf_nan=False)
    normalization: Literal['exact', 'trim', 'casefold'] = 'trim'


class LearningCheckMisconceptionV1(StrictContentModel):
    id: str = Field(min_length=1)
    inputs: list[str | bool | float] = Field(min_length=1)
    feedback: str = Field(min_length=1)


class LearningCheckV1(StrictContentModel):
    # JSON Schema must express the same input branch as declarative_input.
    # Without this, schema-guided authors are invited to put options on booleans.
    model_config = ConfigDict(json_schema_extra={'allOf':[
        {'if':{'properties':{'kind':{'const':'choice'}}},
         'then':{'properties':{'input_spec':{'properties':{'options':{'minItems':2}},'required':['options']}}},
         'else':{'properties':{'input_spec':{'properties':{'options':{'maxItems':0}}}}}},
        {'if':{'properties':{'kind':{'const':'boolean'}}},
         'then':{'properties':{'scoring':{'properties':{'accepted_inputs':{'items':{'type':'boolean'}}}}}}},
    ]})
    id: str = Field(min_length=1)
    kind: Literal['choice', 'boolean', 'numeric', 'key_step', 'self_explanation']
    source_kind: Literal['derived']
    goal_refs: list[str]
    source_refs: list[SourceRefV1]
    statement: str = Field(min_length=1)
    input_spec: LearningCheckInputV1
    scoring: LearningCheckScoringV1
    hints: list[str]
    answer: str = Field(min_length=1)
    misconception_feedback: list[LearningCheckMisconceptionV1]
    test_cases: list[LearningCheckCaseV1]

    @model_validator(mode='after')
    def declarative_input(self):
        import math
        accepted = self.scoring.accepted_inputs
        texts = [self.statement, self.answer] + [x.label for x in self.input_spec.options] + [x.feedback for x in self.misconception_feedback]
        if any(not text.strip() for text in texts):
            raise ValueError('learning check required reader text must be nonempty')
        if self.kind == 'self_explanation':
            if accepted or self.misconception_feedback:
                raise ValueError('self_explanation is manual; machine scoring forbidden')
            if any(case.expected not in ('manual', 'invalid') for case in self.test_cases):
                raise ValueError('self_explanation cannot declare machine correctness')
        elif not accepted:
            raise ValueError('automatic learning check requires accepted_inputs')
        options = [row.id for row in self.input_spec.options]
        if len(options) != len(set(options)):
            raise ValueError('duplicate learning check option ID')
        if self.kind == 'choice':
            if len(options) < 2 or any(type(x) is not str or x not in options for x in accepted):
                raise ValueError('choice requires two options and accepted option IDs')
        elif options:
            raise ValueError('options only allowed for choice')
        if self.kind == 'boolean' and any(type(x) is not bool for x in accepted):
            raise ValueError('boolean accepted_inputs must be booleans')
        if self.kind == 'numeric' and any(type(x) not in (int, float) or not math.isfinite(x) for x in accepted):
            raise ValueError('numeric accepted_inputs must be finite numbers')
        if self.kind == 'numeric' and any(not math.isfinite(self.scoring.relative_tolerance * abs(x)) for x in accepted):
            raise ValueError('numeric effective tolerance must be finite')
        if self.kind == 'key_step' and any(type(x) is not str or not x.strip() for x in accepted):
            raise ValueError('key_step accepted_inputs must be nonempty literal steps')
        if self.kind != 'numeric' and (self.scoring.absolute_tolerance or self.scoring.relative_tolerance):
            raise ValueError('tolerance only allowed for numeric')
        for name in ('test_cases', 'misconception_feedback'):
            ids = [row.id for row in getattr(self, name)]
            if len(ids) != len(set(ids)):
                raise ValueError('duplicate learning check ' + name + ' ID')
        for value in [case.input for case in self.test_cases] + [x for m in self.misconception_feedback for x in m.inputs]:
            if type(value) is float and not math.isfinite(value):
                raise ValueError('learning check input must be finite JSON scalar')
        return self


class NotationV4(StrictContentModel):
    id: str
    symbol: str
    meaning: str
    source_refs: list[SourceRefV1] = Field(default_factory=list)


class CorrectionV4(StrictContentModel):
    id: str
    original: str
    corrected: str
    reason: str
    source_refs: list[SourceRefV1] = Field(default_factory=list)


class ExerciseRegionV1(StrictContentModel):
    """A source range, never a parsed question, solution or teaching object."""
    id: str = Field(default='', description='宿主按冻结来源和范围计算；作者省略', json_schema_extra={'readOnly': True})
    title: str = Field(min_length=1)
    kind: Literal['exercise', 'answers']
    source_lines: list[int] = Field(min_length=2, max_length=2, description='当前完整 source_text 的1-based闭区间 [首行,末行]')
    raw_markdown: str = Field(default='', description='宿主逐字复制原文；作者省略，不解题不改写', json_schema_extra={'readOnly': True})
    source_sha256: str = Field(default='', description='宿主计算原文片段 UTF-8 bytes 的 sha256: 摘要；作者省略', json_schema_extra={'readOnly': True})


class LessonContentV4(StrictContentModel):
    meta: ContentMetaV4 = Field(alias='_meta')
    unit_id: str = Field(min_length=1)
    book_id: str = ''
    title: str = Field(min_length=1)
    reader_intro: str
    prerequisite_recaps: list[ReaderRecapV1]
    body_markdown: str = Field(min_length=1)
    summary: str
    annotations: list[AnnotationV4]
    explainables: list[ExplainableV4]
    examples: list[ExampleV4]
    concepts: list[ConceptUseV4]
    teaching_plan: TeachingPlanV1
    teaching_evidence: list[TeachingEvidenceV1]
    math_models: list[dict[str, Any]]
    learning_checks: list[LearningCheckV1]
    exercise_regions: list[ExerciseRegionV1] = Field(default_factory=list)
    notation: list[NotationV4] = Field(default_factory=list)
    corrections: list[CorrectionV4] = Field(default_factory=list)
    one_line: str = ''
    original_text: str = ''
    original_sha256: str = ''
    source_context_hash: str = ''
    revision: int = 0
    anchor: str = ''
    chapter: str = ''
    section: str = ''
    level: str | int | None = None

    @field_validator('math_models')
    @classmethod
    def validate_math_models(cls, rows):
        if rows:
            from ..math.contracts import parse_math_model
            return [as_dict(parse_math_model(row)) for row in rows]
        return rows

    @model_validator(mode='after')
    def validate_identity_and_markers(self):
        data = self.as_dict()
        local = {'NOTE': {x.id for x in self.annotations}, 'EXPLAIN': {x.id for x in self.explainables}, 'EXAMPLE': {x.id for x in self.examples}, 'CONCEPT': {x.id for x in self.concepts}}

        def unique_objects(rows, pointer):
            ids = [row.id for row in rows]
            if len(ids) != len(set(ids)):
                raise ValueError('duplicate object ID at ' + pointer)

        # Only these typed collections own identities. SourceRefV1.id is optional
        # provenance, so several independent quotes may correctly omit it.
        for name in ('prerequisite_recaps', 'annotations', 'explainables', 'examples',
                     'concepts', 'learning_checks', 'notation', 'corrections', 'exercise_regions'):
            unique_objects(getattr(self, name), '/' + name)
        for name in ('goals', 'prerequisites', 'claims'):
            unique_objects(getattr(self.teaching_plan, name), '/teaching_plan/' + name)
        for name in ('explainables', 'examples'):
            for index, row in enumerate(getattr(self, name)):
                unique_objects(row.steps, f'/{name}/{index}/steps')
        for index, row in enumerate(self.learning_checks):
            unique_objects(row.test_cases, f'/learning_checks/{index}/test_cases')

        def walk(value, pointer=''):
            if isinstance(value, list):
                for i,x in enumerate(value): walk(x, pointer+'/'+str(i))
            elif isinstance(value,dict):
                for k,x in value.items(): walk(x,pointer+'/'+k)
            elif isinstance(value,str):
                if re.fullmatch(r'/exercise_regions/\d+/raw_markdown', pointer):
                    return  # Frozen source syntax is literal, never a host reference.
                for match in REFERENCE_PATTERN.finditer(value):
                    kind,ident=match.group(1),match.group(2).strip()
                    if kind in ('FIGURE','DIAGRAM','MODEL'): raise ValueError('media marker forbidden in content '+pointer)
                    if kind in local and ident not in local[kind]: raise ValueError('unknown '+kind+' reference '+ident+' at '+pointer)
                if re.search(r'\[\[MEDIA\s*:',value): raise ValueError('media marker forbidden in content '+pointer)
        walk(data)
        return self


class AuthorPlacementV1(StrictContentModel):
    target_pointer: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    step_id: str = ''
    anchor_text: str = Field(min_length=1)
    occurrence: int | None = Field(default=None, ge=1)
    position: Literal['before','after']


AuthorMediaEvidence = Literal['rendered_frame', 'request', 'caption', 'alt_text', 'source_refs',
    'source_provenance', 'object_bindings', 'mathematical_report', 'captions', 'steps']


class AuthorCriterionV1(StrictContentModel):
    criterion_id: str = Field(min_length=1)
    version: Literal['1']
    kind: Literal['mathematical','pedagogical','visual','source']
    statement: str = Field(min_length=1)
    evidence_required: list[AuthorMediaEvidence] = Field(min_length=1,
        description='仅使用协议证据键；具体标注、观察要求和数学条件写在冻结 statement 中，不能以自由文字替代证据键。')


class AuthorObjectRefV1(StrictContentModel):
    model_id: str = Field(min_length=1)
    object_id: str = Field(min_length=1)


class AuthorAnimationV1(StrictContentModel):
    initial_state: str = Field(min_length=1)
    steps: list[str] = Field(min_length=1)
    key_pauses: list[str] = Field(min_length=1)
    final_state: str = Field(min_length=1)


class AuthorMediaRequestV1(StrictContentModel):
    request_key: str = Field(min_length=1)
    kind: Literal['image','animation']
    goal_refs: list[str] = Field(min_length=1)
    claim_ids: list[str]
    math_model_ids: list[str]
    object_refs: list[AuthorObjectRefV1]
    learning_goal: str = Field(min_length=1)
    observe: str = Field(min_length=1)
    misconception: str
    mathematical_objects: str = Field(min_length=1)
    conditions: list[str]
    placement: AuthorPlacementV1
    acceptance_criteria: list[AuthorCriterionV1] = Field(min_length=1)
    caption: str
    alt_text: str = Field(min_length=1)
    animation: AuthorAnimationV1 | None = None

    @model_validator(mode='after')
    def animation_matches_kind(self):
        if (self.kind == 'animation') != (self.animation is not None):
            raise ValueError('animation narrative is required only for animation requests')
        for key in ('goal_refs','claim_ids','math_model_ids'):
            values=getattr(self,key)
            if len(values)!=len(set(values)) or any(not x.strip() for x in values):
                raise ValueError(key+' must contain unique nonempty IDs')
        return self


class AuthorMediaDecisionV1(StrictContentModel):
    goal_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    requirement: Literal['required','recommended','none']
    reason: str = Field(min_length=1)
    benefit: str = Field(min_length=1)
    cost: str = Field(min_length=1)
    text_alternative: str = Field(min_length=1)
    request_ids: list[str]
    handled_by: Literal['media','interaction']
    not_produced_reason: str = ''


class AuthorMediaRequirementsV1(StrictContentModel):
    schema_version: Literal['AuthorMediaRequirementsV1']
    decisions: list[AuthorMediaDecisionV1]
    requests: list[AuthorMediaRequestV1]


class ContentAuthoringV1(StrictContentModel):
    schema_version: Literal['ContentAuthoringV1']
    content: LessonContentV4
    media_requirements: AuthorMediaRequirementsV1


_parse_content_v3 = parse_content


def parse_content(data: dict) -> LessonContentV3 | LessonContentV4:
    """Explicit version boundary; historical v3 remains readable during graph 4 transition."""
    from ...core.review_mode import reviews_frozen
    if reviews_frozen():
        from .json_only import content_record
        return content_record(data)
    if not isinstance(data, dict): raise ContractError('content must be an object')
    version = (data.get('_meta') or {}).get('schema_version')
    if version == '3.0': return _parse_content_v3(data)
    if version != '4.0': raise ContractError('unsupported content schema version '+str(version))
    try:
        return LessonContentV4.model_validate(data)
    except (ValidationError, ValueError) as exc:
        raise ContractError('LessonContentV4: '+str(exc)) from exc


def import_candidate_v4(data: dict, *, v4_fields: dict) -> dict:
    """Explicit candidate-only import. Never creates an approval or infers pedagogy.

    The host supplies new typed goals/claims/reader fields in ``v4_fields``. Missing
    fields and unmapped extras fail normal V4 validation; nothing is discarded.
    Every replacement retains its old value in the migration report.
    """
    candidate=deepcopy(data); changes=[]
    source_version=(candidate.get('_meta') or {}).get('schema_version')
    if source_version not in ('2.0','3.0'):
        raise ContractError('candidate import requires an explicit historical schema 2.0/3.0')
    def change(pointer,before,after):
        changes.append({'json_pointer':pointer,'before':deepcopy(before),'after':deepcopy(after)})
    def rename(row,old,new,pointer):
        if old not in row: return
        if new in row: raise ContractError('candidate alias conflict at '+pointer+'/'+new)
        value=row.pop(old); row[new]=value
        changes.append({'json_pointer':pointer+'/'+new,'source_pointer':pointer+'/'+old,'before':deepcopy(value),'after':deepcopy(value)})
    for i,row in enumerate(candidate.get('annotations') or []):
        rename(row,'body','markdown',f'/annotations/{i}')
    for group in ('examples','explainables'):
        for i,row in enumerate(candidate.get(group) or []):
            pointer=f'/{group}/{i}'
            if group=='examples': rename(row,'thought','approach',pointer)
            if group=='explainables':
                rename(row,'title','name',pointer)
                rename(row,'walkthrough','steps',pointer)
            for j,step in enumerate(row.get('steps') or []):
                for alias in ('claim','text'):
                    rename(step,alias,'markdown',pointer+f'/steps/{j}')
    protected={'unit_id','book_id','original_text','original_sha256','source_context_hash','revision','_meta'}
    for key,value in v4_fields.items():
        if key in protected: raise ContractError('candidate importer cannot overwrite provenance '+key)
        change('/'+key,candidate.get(key),value); candidate[key]=deepcopy(value)
    meta={**(candidate.get('_meta') or {}),'schema_version':'4.0','file_type':'LessonContentV4'}
    change('/_meta',candidate.get('_meta'),meta); candidate['_meta']=meta
    parsed=parse_content(candidate)
    return {'content':parsed.as_dict(),'migration_report':{'schema_version':'CandidateImportReportV1',
        'source_schema_version':source_version,'source_hash':content_digest(data),'candidate_hash':content_digest(parsed),
        'changes':changes,'approval_inherited':False}}
