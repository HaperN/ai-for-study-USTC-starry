"""Versioned mathematical claims, binding, and evidence contracts."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from typing import Any

from ...core.digest import canonical_json, digest_of
from ...errors import ContractError
from .expression import (NAME_RE, normalize_ast, AST_FIELDS, MAX_AST_DEPTH,
                         MAX_AST_NODES, MAX_MATRIX_SIZE, MIN_INTEGER_POWER, MAX_INTEGER_POWER)

SCHEMA_VERSION = '1.0'
VERIFIER_VERSION = '2.0'
REPORT_SCHEMA_VERSION = '2.0'
STATUSES = frozenset({'pass', 'fail', 'unknown', 'not_applicable', 'not_run'})
ASSUMPTIONS = frozenset({'positive', 'nonnegative', 'negative', 'nonpositive', 'nonzero'})
SYMBOL_DOMAINS = frozenset({'real', 'integer', 'complex'})
OBJECT_FIELDS = {'scalar': 'expr', 'vector': 'entries', 'matrix': 'rows'}
COMPARISON_OPERATORS = frozenset({'eq', 'ne', 'gt', 'ge', 'lt', 'le'})
MODEL_FIELDS = frozenset({'schema_version', 'model_id', 'symbols', 'objects', 'conditions', 'claims', 'source_refs'})
ID_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,127}$')
CLAIM_FIELDS = {
    'identity': {'lhs_object_id', 'rhs_object_id'},
    'equation_equivalent': {'left', 'right', 'variable'},
    'solution_set': {'equation', 'variable', 'solutions'},
    'matrix_rank': {'expected'}, 'matrix_inverse': {'inverse_object_id'},
    'derivative': {'variable', 'order', 'expected_object_id'},
    'abstract': set(),
}


def _id(value: Any, path: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ContractError(f'{path}: stable identifier required')
    return value


def _fields(data: Any, required: set[str], optional: set[str], path: str) -> None:
    if not isinstance(data, dict):
        raise ContractError(f'{path}: object required')
    missing, extra = required - set(data), set(data) - required - optional
    if missing or extra:
        raise ContractError(f'{path}: missing fields {sorted(missing)}, unsupported fields {sorted(extra)}')


def _list(data: Any, path: str, limit: int = 256) -> list:
    if not isinstance(data, list) or len(data) > limit:
        raise ContractError(f'{path}: list required (maximum {limit})')
    return data


@dataclass(frozen=True)
class MathModelV1:
    """Canonical immutable JSON storage prevents post-approval nested mutations.

    Public dictionary properties return copies. Direct construction validates as
    strictly as parse_math_model, so a forged dataclass cannot bypass AST safety.
    """
    _canonical: str

    def __post_init__(self) -> None:
        data = json.loads(self._canonical)
        normalized = _normalize_model(data)
        object.__setattr__(self, '_canonical', canonical_json(normalized))

    def as_dict(self) -> dict:
        return json.loads(self._canonical)

    @property
    def schema_version(self) -> str:
        return SCHEMA_VERSION

    @property
    def model_id(self) -> str:
        return self.as_dict()['model_id']

    @property
    def symbols(self) -> tuple[dict, ...]:
        return tuple(self.as_dict()['symbols'])

    @property
    def objects(self) -> tuple[dict, ...]:
        return tuple(self.as_dict()['objects'])

    @property
    def conditions(self) -> tuple[dict, ...]:
        return tuple(self.as_dict()['conditions'])

    @property
    def claims(self) -> tuple[dict, ...]:
        return tuple(self.as_dict()['claims'])

    @property
    def source_refs(self) -> tuple[str, ...]:
        return tuple(self.as_dict()['source_refs'])

    @property
    def digest(self) -> str:
        return digest_of(self.as_dict())

    def object_by_id(self, math_object_id: str) -> dict:
        for item in self.objects:
            if item['math_object_id'] == math_object_id:
                return item
        raise ContractError(f'math_object_id: missing {math_object_id!r}')

    def claim_by_id(self, claim_id: str) -> dict:
        rows = self.relations_for_claim(claim_id)
        if len(rows) != 1:
            raise ContractError(f'claim_id: ambiguous multiple relations for {claim_id!r}; select relation_id')
        return rows[0]

    def relations_for_claim(self, claim_id: str) -> tuple[dict, ...]:
        rows = tuple(item for item in self.claims if item['claim_id'] == claim_id)
        if not rows:
            raise ContractError(f'claim_id: missing {claim_id!r}')
        return rows

    def relation_by_id(self, relation_id: str) -> dict:
        for item in self.claims:
            if item['relation_id'] == relation_id:
                return item
        raise ContractError(f'relation_id: missing {relation_id!r}')


def _normalize_model(data: Any) -> dict:
    required = set(MODEL_FIELDS)
    _fields(data, required, set(), 'math_model')
    if data['schema_version'] != SCHEMA_VERSION:
        raise ContractError('math_model.schema_version: expected 1.0')
    # Rebuild only after validating each field; deepcopy of an untrusted deeply
    # nested AST would recurse before the expression parser can enforce bounds.
    result = {'schema_version': data['schema_version'], 'model_id': data['model_id'],
              'symbols': [], 'objects': [], 'conditions': [], 'claims': [], 'source_refs': []}
    _id(data['model_id'], 'math_model.model_id')
    symbols = _list(data['symbols'], 'symbols', 64)
    names = set()
    for i, symbol in enumerate(symbols):
        path = f'symbols[{i}]'
        _fields(symbol, {'name', 'domain'}, {'assumptions'}, path)
        name = _id(symbol['name'], path + '.name')
        if not NAME_RE.fullmatch(name):
            raise ContractError(path + '.name: safe mathematical symbol required')
        if name in names:
            raise ContractError(path + '.name: duplicate symbol')
        names.add(name)
        if not isinstance(symbol['domain'], str) or symbol['domain'] not in SYMBOL_DOMAINS:
            raise ContractError(path + '.domain: real/integer/complex required')
        assumptions = _list(symbol.get('assumptions', []), path + '.assumptions', 5)
        if any(not isinstance(item, str) or item not in ASSUMPTIONS for item in assumptions) or len(set(assumptions)) != len(assumptions):
            raise ContractError(path + '.assumptions: closed unique assumptions required')
        result['symbols'].append(dict(symbol, assumptions=sorted(assumptions)))
    def expr(node: Any, path: str) -> dict:
        return normalize_ast(node, path, allowed_names=names, enforce_limits=False)[0]
    def conditions(items: Any, path: str) -> list:
        out = []
        for i, item in enumerate(_list(items, path, 64)):
            where = f'{path}[{i}]'
            _fields(item, {'op', 'left', 'right'}, set(), where)
            if not isinstance(item['op'], str) or item['op'] not in COMPARISON_OPERATORS:
                raise ContractError(where + '.op: closed comparison required')
            out.append({'op': item['op'], 'left': expr(item['left'], where + '.left'),
                        'right': expr(item['right'], where + '.right')})
        return out
    result['conditions'] = conditions(data['conditions'], 'conditions')
    objects = _list(data['objects'], 'objects')
    by_id = {}
    for i, obj in enumerate(objects):
        path = f'objects[{i}]'
        if not isinstance(obj, dict) or not isinstance(obj.get('kind'), str) or obj.get('kind') not in OBJECT_FIELDS:
            raise ContractError(path + '.kind: scalar/vector/matrix required')
        field = OBJECT_FIELDS[obj['kind']]
        _fields(obj, {'math_object_id', 'kind', field}, set(), path)
        ident = _id(obj['math_object_id'], path + '.math_object_id')
        if ident in by_id:
            raise ContractError(path + '.math_object_id: duplicate object')
        normalized = {'math_object_id': ident, 'kind': obj['kind']}
        if obj['kind'] == 'scalar':
            normalized['expr'] = expr(obj['expr'], path + '.expr')
        else:
            rows = _list(obj['rows'] if obj['kind'] == 'matrix' else [obj['entries']], path + '.rows', 128)
            if not rows:
                raise ContractError(path + '.rows: nonempty matrix required')
            width = None
            entries = []
            for j, row in enumerate(rows):
                values = _list(row, path + f'.rows[{j}]', 128)
                if not values or (width is not None and len(values) != width):
                    raise ContractError(path + '.rows: rectangular nonempty matrix required')
                width = len(values)
                entries.append([expr(value, path + f'.rows[{j}][{k}]') for k, value in enumerate(values)])
            normalized[field] = entries if obj['kind'] == 'matrix' else entries[0]
        by_id[ident] = normalized
        result['objects'].append(normalized)
    relation_ids = set()
    for i, claim in enumerate(_list(data['claims'], 'claims')):
        path = f'claims[{i}]'
        if not isinstance(claim, dict) or not isinstance(claim.get('kind'), str) or claim.get('kind') not in CLAIM_FIELDS:
            raise ContractError(path + '.kind: unsupported claim kind')
        kind = claim['kind']
        _fields(claim, {'claim_id', 'kind', 'math_object_id'} | CLAIM_FIELDS[kind],
                {'conditions', 'reason', 'relation_id'} if kind == 'abstract' else {'conditions', 'relation_id'}, path)
        _id(claim['claim_id'], path + '.claim_id')
        normalized = dict(claim)
        normalized['conditions'] = conditions(claim.get('conditions', []), path + '.conditions')
        for field in ['math_object_id', 'lhs_object_id', 'rhs_object_id', 'inverse_object_id', 'expected_object_id']:
            if field in claim:
                _id(claim[field], path + '.' + field)
                if claim[field] not in by_id:
                    raise ContractError(path + '.' + field + ': unknown math_object_id')
        if kind in {'matrix_rank', 'matrix_inverse'} and by_id[claim['math_object_id']]['kind'] != 'matrix':
            raise ContractError(path + '.math_object_id: matrix required')
        if kind == 'matrix_inverse' and by_id[claim['inverse_object_id']]['kind'] != 'matrix':
            raise ContractError(path + '.inverse_object_id: matrix required')
        if kind == 'derivative' and (by_id[claim['math_object_id']]['kind'] != 'scalar'
                                    or by_id[claim['expected_object_id']]['kind'] != 'scalar'):
            raise ContractError(path + ': derivative scalar objects required')
        if 'variable' in claim and (not isinstance(claim['variable'], str) or claim['variable'] not in names):
            raise ContractError(path + '.variable: declared symbol required')
        if kind == 'derivative' and (type(claim['order']) is not int or not 1 <= claim['order'] <= 8):
            raise ContractError(path + '.order: integer 1..8 required')
        if kind == 'matrix_rank' and (type(claim['expected']) is not int or not 0 <= claim['expected'] <= 128):
            raise ContractError(path + '.expected: nonnegative bounded integer required')
        for field in ['left', 'right', 'equation']:
            if field in CLAIM_FIELDS[kind]:
                pair = _list(claim[field], path + '.' + field, 2)
                if len(pair) != 2:
                    raise ContractError(path + '.' + field + ': equation requires two ASTs')
                normalized[field] = [expr(value, path + '.' + field) for value in pair]
        if kind == 'solution_set':
            normalized['solutions'] = [expr(value, path + '.solutions')
                                       for value in _list(claim['solutions'], path + '.solutions', 256)]
        if 'reason' in claim and (not isinstance(claim['reason'], str) or len(claim['reason']) > 2048):
            raise ContractError(path + '.reason: short text required')
        relation_id = claim.get('relation_id')
        if 'relation_id' not in claim:
            relation_id = 'R-' + digest_of(normalized).split(':', 1)[1]
        _id(relation_id, path + '.relation_id')
        if relation_id in relation_ids:
            raise ContractError(path + '.relation_id: duplicate relation identity')
        relation_ids.add(relation_id)
        normalized['relation_id'] = relation_id
        result['claims'].append(normalized)
    refs = _list(data['source_refs'], 'source_refs', 256)
    if any(not isinstance(item, str) or not item or len(item) > 256 for item in refs) or len(set(refs)) != len(refs):
        raise ContractError('source_refs: unique nonempty stable strings required')
    result['source_refs'] = list(refs)
    return result


def parse_math_model(data: dict) -> MathModelV1:
    return MathModelV1(canonical_json(_normalize_model(data)))


@dataclass(frozen=True)
class VerificationLimits:
    cpu_seconds: int = 2
    wall_seconds: float = 3.0
    memory_mib: int = 512
    total_seconds: float | None = None

    def __post_init__(self) -> None:
        if type(self.cpu_seconds) is not int or not 1 <= self.cpu_seconds <= 60:
            raise ContractError('limits.cpu_seconds: integer 1..60 required')
        if type(self.memory_mib) is not int or not 32 <= self.memory_mib <= 4096:
            raise ContractError('limits.memory_mib: integer 32..4096 required')
        for key, value in [('wall_seconds', self.wall_seconds), ('total_seconds', self.total_seconds)]:
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not math.isfinite(value) or value <= 0):
                raise ContractError(f'limits.{key}: positive finite duration required')

    def as_dict(self) -> dict:
        return {'cpu_seconds': self.cpu_seconds, 'wall_seconds': self.wall_seconds,
                'memory_mib': self.memory_mib, 'total_seconds': self.total_seconds}


@dataclass(frozen=True)
class MathCheck:
    claim_id: str
    math_object_ids: tuple[str, ...]
    rule_id: str
    status: str
    reason_code: str
    reason: str
    method: str
    _evidence_json: str
    relation_id: str = ''

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ContractError('math_check.status: unknown status')
        if self.method not in {'symbolic_proof', 'concrete_calculation', 'numerical_counterexample',
                               'sample_no_counterexample', 'none'}:
            raise ContractError('math_check.method: unknown evidence method')
        if self.status == 'pass' and self.method not in {'symbolic_proof', 'concrete_calculation'}:
            raise ContractError('math_check: samples cannot be promoted to proof')

    @property
    def evidence(self) -> dict:
        return json.loads(self._evidence_json)

    @property
    def evidence_hash(self) -> str:
        return digest_of({'claim_id': self.claim_id, 'relation_id': self.relation_id, 'objects': self.math_object_ids,
                          'rule_id': self.rule_id, 'status': self.status,
                          'reason_code': self.reason_code, 'method': self.method,
                          'evidence': self.evidence})

    def as_dict(self) -> dict:
        return {'claim_id': self.claim_id, 'relation_id': self.relation_id, 'math_object_ids': list(self.math_object_ids),
                'rule_id': self.rule_id, 'status': self.status, 'reason_code': self.reason_code,
                'reason': self.reason, 'method': self.method, 'evidence': self.evidence,
                'evidence_hash': self.evidence_hash}


@dataclass(frozen=True)
class MathCheckReport:
    checks: tuple[MathCheck, ...]
    status: str
    model_digest: str
    reason_code: str = ''

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ContractError('math_report.status: unknown status')
        relation_ids = [item.relation_id for item in self.checks]
        if any(not identity for identity in relation_ids) or len(relation_ids) != len(set(relation_ids)):
            raise ContractError('math_report: missing or duplicate relation identity')
        if self.status == 'pass' and (not self.checks or any(item.status != 'pass' for item in self.checks)):
            raise ContractError('math_report: pass requires nonempty all-pass checks')

    @property
    def digest(self) -> str:
        return digest_of(self.as_dict())

    def by_claim(self, claim_id: str) -> MathCheck | None:
        rows = self.checks_for_claim(claim_id)
        if len(rows) > 1:
            raise ContractError('ambiguous multiple checks for claim; select relation_id')
        return rows[0] if rows else None

    def checks_for_claim(self, claim_id: str) -> tuple[MathCheck, ...]:
        return tuple(item for item in self.checks if item.claim_id == claim_id)

    def by_relation(self, relation_id: str) -> MathCheck | None:
        return next((item for item in self.checks if item.relation_id == relation_id), None)

    def as_dict(self) -> dict:
        return {'schema_version': REPORT_SCHEMA_VERSION, 'verifier_version': VERIFIER_VERSION,
                'status': self.status, 'reason_code': self.reason_code,
                'model_digest': self.model_digest, 'checks': [item.as_dict() for item in self.checks]}


def describe_math_contract() -> dict:
    """Authoritative JSON Schema for supported generation, derived from validators.

    This schema guides prompt generation toward the current finite capability.
    Parser defenses can preserve a safe larger model for unknown/capability_limit;
    it must never be silently reduced to satisfy this generation schema.
    """
    expression = {'$ref': '#/$defs/expression'}
    ident = {'type': 'string', 'pattern': ID_RE.pattern}
    symbol = {'type': 'string', 'pattern': NAME_RE.pattern,
              'description': 'A declared independent symbols[].name, never a math_object_id. The AST has no object_ref operator: inline a scalar object expression when using its value inside another expression or condition. Do not declare a composite object as a new independent symbol.'}
    def object_schema(properties, required):
        return {'type': 'object', 'properties': properties, 'required': sorted(required),
                'additionalProperties': False}
    condition = object_schema({'op': {'type': 'string', 'enum': sorted(COMPARISON_OPERATORS)},
                               'left': expression, 'right': expression}, {'op', 'left', 'right'})
    conditions = {'type': 'array', 'items': condition, 'maxItems': 64}
    ast_branches = []
    for op, fields in sorted(AST_FIELDS.items()):
        properties = {'op': {'const': op}}
        for field in sorted(fields - {'op'}):
            properties[field] = ({'type': 'number', 'description': 'Finite numeric constant; no booleans or strings.'}
                                 if field == 'value' else symbol if field == 'name' else
                                 {'type': 'integer', 'minimum': MIN_INTEGER_POWER, 'maximum': MAX_INTEGER_POWER}
                                 if field == 'exponent' else expression)
        ast_branches.append(object_schema(properties, fields))
    array_entries = {'type': 'array', 'items': expression, 'minItems': 1, 'maxItems': MAX_MATRIX_SIZE}
    objects = []
    for kind, field in OBJECT_FIELDS.items():
        value_schema = expression if kind == 'scalar' else array_entries if kind == 'vector' else {
            'type': 'array', 'items': array_entries, 'minItems': 1, 'maxItems': MAX_MATRIX_SIZE,
            'description': 'Rectangular matrix; every row must have the same length.'}
        objects.append(object_schema({'math_object_id': ident, 'kind': {'const': kind},
                                      field: value_schema}, {'math_object_id', 'kind', field}))
    equation = {'type': 'array', 'items': expression, 'minItems': 2, 'maxItems': 2}
    claims = []
    for kind, fields in CLAIM_FIELDS.items():
        properties = {'claim_id': ident, 'kind': {'const': kind}, 'math_object_id': ident,
                      'conditions': conditions}
        for field in sorted(fields):
            properties[field] = (symbol if field == 'variable' else
                                 equation if field in {'left', 'right', 'equation'} else
                                 {'type': 'array', 'items': expression, 'maxItems': 256} if field == 'solutions' else
                                 {'type': 'integer', 'minimum': 1, 'maximum': 8} if field == 'order' else
                                 {'type': 'integer', 'minimum': 0, 'maximum': MAX_MATRIX_SIZE} if field == 'expected' else ident)
        if kind == 'abstract':
            properties['reason'] = {'type': 'string', 'maxLength': 2048}
        properties['relation_id'] = {**ident, 'description': 'Unique relation identity within this model; may be omitted for host derivation from the complete normalized relation. claim_id binds the teaching claim and may repeat across distinct relations.'}
        claims.append(object_schema(properties, {'claim_id', 'kind', 'math_object_id'} | fields))
    schema = object_schema({
        'schema_version': {'const': SCHEMA_VERSION}, 'model_id': ident,
        'symbols': {'type': 'array', 'maxItems': 64, 'items': object_schema({
            'name': symbol, 'domain': {'type': 'string', 'enum': sorted(SYMBOL_DOMAINS)},
            'assumptions': {'type': 'array', 'items': {'type': 'string', 'enum': sorted(ASSUMPTIONS)},
                            'uniqueItems': True, 'maxItems': len(ASSUMPTIONS)}}, {'name', 'domain'})},
        'objects': {'type': 'array', 'maxItems': 256, 'items': {'oneOf': objects}},
        'conditions': conditions,
        'claims': {'type': 'array', 'maxItems': 256, 'items': {'oneOf': claims}},
        'source_refs': {'type': 'array', 'uniqueItems': True, 'maxItems': 256,
                        'items': {'type': 'string', 'minLength': 1, 'maxLength': 256},
                        'description': 'Stable frozen source obligation IDs, resolved by the content contract.'},
    }, MODEL_FIELDS)
    schema.update({'$schema': 'https://json-schema.org/draft/2020-12/schema', 'title': 'MathModelV1',
                   '$defs': {'expression': {'oneOf': ast_branches}},
                   'description': ('Closed mathematical data, never executable text or TeX. All symbols and object IDs '
                                   'must resolve in separate namespaces: var.name and relation variable select declared symbols; object IDs are selected only by explicit *_object_id fields. Inline scalar expressions inside ASTs and conditions, never var(name=object_id). '
                                   'relation_id is unique, while multiple relations may bind one teaching claim_id. Conditions add to the model domain. Every numerical '
                                   'relation binds its content claim_id and math objects. All relations must be checked. Abstract claims yield unknown. '
                                   f'AST depth <= {MAX_AST_DEPTH}, node count <= {MAX_AST_NODES}; '
                                   'sampling without counterexamples is not a proof.')})
    return schema


def math_schema_example() -> dict:
    """Minimal domain-conditioned example, ready for JSON prompt serialization."""
    variable = {'op': 'var', 'name': 'x'}
    constant = lambda value: {'op': 'const', 'value': value}
    return parse_math_model({
        'schema_version': SCHEMA_VERSION, 'model_id': 'ratio-domain',
        'symbols': [{'name': 'x', 'domain': 'real', 'assumptions': []}],
        'objects': [
            {'math_object_id': 'ratio', 'kind': 'scalar',
             'expr': {'op': 'div', 'left': variable, 'right': variable}},
            {'math_object_id': 'one', 'kind': 'scalar', 'expr': constant(1)},
        ],
        'conditions': [{'op': 'ne', 'left': variable, 'right': constant(0)}],
        'claims': [{'claim_id': 'c-ratio', 'kind': 'identity', 'math_object_id': 'ratio',
                    'lhs_object_id': 'ratio', 'rhs_object_id': 'one', 'conditions': []}],
        'source_refs': ['source-obligation-1'],
    }).as_dict()
