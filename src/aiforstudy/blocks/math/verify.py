"""Independent CAS relationship checking in disposable resource-limited workers.

Only trusted code constructs SymPy objects. Neither rendering results nor its
numerical AST evaluator are imported here. Numerical substitutions can disprove
claims; failure to find a counterexample never proves a theorem.
"""
from __future__ import annotations

import itertools
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any

from ...core.digest import canonical_json, digest_of, digest_file
from ...core.process_io import communicate_bounded
from ...errors import ContractError
from .contracts import (MathModelV1, MathCheck, MathCheckReport, VerificationLimits,
                        parse_math_model, VERIFIER_VERSION)
from .expression import normalize_ast, ast_names, MathCapabilityError, UNARY_OPS

SAMPLE_VALUES = (0, -1, 1, -2, 2, -0.5, 0.5, 3)


class _VerificationCancelled(Exception):
    """Stop local pipe I/O while retaining the verifier's unknown result contract."""


class _WorkerResourceLimit(Exception):
    def __init__(self, reason_code: str):
        self.reason_code = reason_code


def _darwin_group_rss_bytes(pgid: int, *, timeout: float) -> int:
    """Host census in bytes; failures must never silently remove the RSS guard.

    macOS rejects finite RLIMIT_AS. Sample the isolated worker group via ps,
    excluding zombies. This is a sampled resident-memory limit, not a virtual
    address-space ceiling: brief allocation spikes between samples can escape.
    """
    result = subprocess.run(['/bin/ps', '-axo', 'pgid=,rss=,stat='],
                            capture_output=True, text=True, check=True, timeout=timeout)
    total = 0
    for line in result.stdout.splitlines():
        group, rss, state = line.split()
        if int(group) == pgid and not state.startswith(('Z', 'X')):
            total += int(rss) * 1024
    return total


def _object_ids(claim: dict) -> tuple[str, ...]:
    return tuple(sorted({value for key, value in claim.items()
                         if key in {'math_object_id', 'lhs_object_id', 'rhs_object_id',
                                    'inverse_object_id', 'expected_object_id'}}))


def _make_check(claim: dict, status: str, reason_code: str, reason: str,
                method: str = 'none', evidence: dict | None = None) -> MathCheck:
    return MathCheck(claim['claim_id'], _object_ids(claim), 'math.' + claim['kind'] + '.v1',
                     status, reason_code, reason, method, canonical_json(evidence or {}), claim['relation_id'])


def _cancelled(cancel) -> bool:
    return bool(cancel.is_set() if hasattr(cancel, 'is_set') else cancel()) if cancel is not None else False


def _capability(model: MathModelV1, claim: dict) -> None:
    names = {symbol['name'] for symbol in model.symbols}
    def traverse(value: Any) -> None:
        if isinstance(value, dict):
            if 'op' in value and value['op'] not in {'eq', 'ne', 'ge', 'gt', 'le', 'lt'}:
                normalize_ast(value, allowed_names=names)
                return
            for item in value.values():
                traverse(item)
        elif isinstance(value, list):
            for item in value:
                traverse(item)
    for ident in _object_ids(claim):
        obj = model.object_by_id(ident)
        if obj['kind'] == 'matrix' and (len(obj['rows']) > 8 or len(obj['rows'][0]) > 8):
            raise MathCapabilityError('matrix size exceeds 8x8')
        if obj['kind'] == 'vector' and len(obj['entries']) > 8:
            raise MathCapabilityError('vector size exceeds 8')
        traverse(obj)
    traverse(claim)
    traverse(list(model.conditions))


def _provenance() -> dict:
    return {'verifier_version': VERIFIER_VERSION, 'ast_version': '1.0',
            'algorithm': 'sympy-constructors-domain-guards-counterexamples-v1',
            'component_digest': digest_of({
                'verify': digest_file(__file__),
                'expression': digest_file(Path(__file__).with_name('expression.py')),
                'contracts': digest_file(Path(__file__).with_name('contracts.py'))})}


def verify_math(model: MathModelV1 | dict, claims=None, *, limits=None, cancel=None) -> MathCheckReport:
    """Verify frozen claims with per-claim CPU/memory/wall and total budgets.

    Passing a subset selects claims; it cannot override the approved claim bytes.
    Parent polls cancellation while workers execute and always reaps children.
    """
    if isinstance(model, dict):
        model = parse_math_model(model)
    if not isinstance(model, MathModelV1):
        raise ContractError('model: MathModelV1 required')
    limits = VerificationLimits() if limits is None else limits
    if not isinstance(limits, VerificationLimits):
        raise ContractError('limits: VerificationLimits required')
    selected = list(model.claims) if claims is None else []
    if claims is not None:
        for value in claims:
            if isinstance(value, str):
                selected.extend(model.relations_for_claim(value))
                continue
            if not isinstance(value, dict):
                raise ContractError('claims: claim ID or explicit relation selection required')
            if 'relation_id' in value:
                frozen = model.relation_by_id(value['relation_id'])
                if value.get('claim_id', frozen['claim_id']) != frozen['claim_id']:
                    raise ContractError('claims: relation belongs to a different teaching claim')
                if set(value) <= {'relation_id', 'claim_id'}:
                    selected.append(frozen)
                    continue
            else:
                frozen = model.claim_by_id(value.get('claim_id'))
            if value != frozen:
                # Normalize omitted optional conditions consistently, without allowing content changes.
                normalized = dict(value, conditions=value.get('conditions', []),
                                  relation_id=value.get('relation_id', frozen['relation_id']))
                if normalized != frozen:
                    raise ContractError('claims: cannot modify frozen model claim')
            selected.append(frozen)
    if len({item['relation_id'] for item in selected}) != len(selected):
        raise ContractError('claims: duplicate selected relation')
    if not selected:
        return MathCheckReport((), 'unknown', model.digest, 'no_claims')
    start = time.monotonic()
    base_provenance = dict(_provenance(), model_digest=model.digest)
    checks = []
    for claim in selected:
        provenance = dict(base_provenance, claim_digest=digest_of(claim))
        if _cancelled(cancel):
            checks.append(_make_check(claim, 'unknown', 'cancelled', 'verification cancelled', evidence=provenance))
            continue
        remaining = None if limits.total_seconds is None else limits.total_seconds - (time.monotonic() - start)
        if remaining is not None and remaining <= 0:
            checks.append(_make_check(claim, 'unknown', 'budget_exhausted', 'total verification budget exhausted', evidence=provenance))
            continue
        try:
            _capability(model, claim)
        except MathCapabilityError as exc:
            checks.append(_make_check(claim, 'unknown', 'capability_limit', str(exc), evidence=provenance))
            continue
        deadline = time.monotonic() + min(limits.wall_seconds, remaining if remaining is not None else limits.wall_seconds)
        payload = canonical_json({'model': model.as_dict(), 'claim': claim,
                                  'limits': limits.as_dict(), 'provenance': provenance}).encode('utf-8')
        env = dict(os.environ, OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1')
        # Point workers at the installed package location even when caller cwd differs.
        source_root = str(Path(__file__).resolve().parents[3])
        env['PYTHONPATH'] = source_root + os.pathsep + env.get('PYTHONPATH', '')
        proc = subprocess.Popen([sys.executable, '-m', 'aiforstudy.blocks.math.verify', '--worker'],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env, start_new_session=True)

        peak_rss = 0

        def checkpoint():
            nonlocal peak_rss
            if _cancelled(cancel):
                raise _VerificationCancelled()
            if sys.platform == 'darwin':
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                try:
                    rss = _darwin_group_rss_bytes(proc.pid, timeout=min(.2, remaining))
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    if time.monotonic() >= deadline:
                        raise TimeoutError() from exc
                    raise _WorkerResourceLimit('resource_monitor_failure') from exc
                peak_rss = max(peak_rss, rss)
                if rss > limits.memory_mib * 1024 * 1024:
                    raise _WorkerResourceLimit('memory_limit')

        try:
            stdout, stderr = communicate_bounded(proc, payload, deadline=deadline,
                                                 checkpoint=checkpoint, poll_seconds=.03)
            if proc.returncode != 0:
                code = 'cpu_limit' if proc.returncode == -signal.SIGXCPU else 'resource_failure'
                result = {'status': 'unknown', 'reason_code': code,
                          'reason': f'worker exited {proc.returncode}', 'method': 'none',
                          'evidence': {'worker_diagnostic': stderr.decode('utf-8', errors='replace')[-1000:]}}
            else:
                result = json.loads(stdout)
        except _WorkerResourceLimit as exc:
            result = {'status': 'unknown', 'reason_code': exc.reason_code,
                      'reason': ('worker resident memory limit reached' if exc.reason_code == 'memory_limit'
                                 else 'worker memory monitor unavailable'),
                      'method': 'none', 'evidence': {}}
        except (_VerificationCancelled, TimeoutError) as exc:
            reason = 'cancelled' if isinstance(exc, _VerificationCancelled) else 'timeout'
            result = {'status': 'unknown', 'reason_code': reason,
                      'reason': 'verification cancelled' if reason == 'cancelled' else 'worker wall time exceeded',
                      'method': 'none', 'evidence': {}}
        finally:
            try:
                if proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait(timeout=1)
            finally:
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    if stream is not None:
                        stream.close()
        if sys.platform == 'darwin':
            result['evidence'].update(memory_policy='host-process-group-rss', peak_rss_bytes=peak_rss)
        checks.append(_make_check(claim, result['status'], result['reason_code'], result['reason'],
                                  result['method'], dict(provenance, **result['evidence'])))
    status = 'fail' if any(item.status == 'fail' for item in checks) else 'pass' if all(item.status == 'pass' for item in checks) else 'unknown'
    return MathCheckReport(tuple(checks), status, model.digest)


class _CAS:
    def __init__(self, model: MathModelV1, claim: dict, sp):
        self.model, self.claim, self.sp = model, claim, sp
        self.symbol_defs = model.symbols
        self.conditions_ast = list(model.conditions) + claim.get('conditions', [])
        self.symbols = {}
        for definition in self.symbol_defs:
            name, domain = definition['name'], definition['domain']
            assumptions = {item: True for item in definition['assumptions']}
            if domain == 'complex' and assumptions.pop('nonzero', False):
                # SymPy's nonzero=True means a nonzero REAL number. Complex
                # exclusion of zero must preserve non-real values such as i.
                assumptions['zero'] = False
            if domain in {'real', 'integer'}:
                assumptions[domain] = True
            else:
                assumptions['complex'] = True
            # Simple bounds refine symbols before expressions construct sqrt/Abs.
            for condition in self.conditions_ast:
                left, right = condition['left'], condition['right']
                if left == {'op': 'var', 'name': name} and right == {'op': 'const', 'value': 0}:
                    refinement = {'ne': 'nonzero', 'ge': 'nonnegative', 'gt': 'positive',
                                  'le': 'nonpositive', 'lt': 'negative'}.get(condition['op'])
                    if refinement and (domain != 'complex' or condition['op'] == 'ne'):
                        assumptions['zero' if domain == 'complex' else refinement] = False if domain == 'complex' else True
            self.symbols[name] = sp.Symbol(name, **assumptions)
        self.conditions = [self.relation(item) for item in self.conditions_ast]
        self.substitutions = {}
        for condition in self.conditions:
            if isinstance(condition, sp.Equality):
                if isinstance(condition.lhs, sp.Symbol) and condition.lhs not in condition.rhs.free_symbols:
                    self.substitutions[condition.lhs] = condition.rhs
                elif isinstance(condition.rhs, sp.Symbol) and condition.rhs not in condition.lhs.free_symbols:
                    self.substitutions[condition.rhs] = condition.lhs
        self.guards = []
        for condition in self.conditions_ast:
            self.expr(condition['left'], guarded=True)
            self.expr(condition['right'], guarded=True)
        self.condition_guards = list(self.guards)
        for ident in _object_ids(claim):
            self.object(ident, guarded=True)
        for field in ['left', 'right', 'equation', 'solutions']:
            if field in claim:
                for expression in claim[field]:
                    self.expr(expression, guarded=True)

    def number(self, value):
        if isinstance(value, self.sp.Expr):
            return value
        if isinstance(value, complex):
            return self.sp.Add(self.number(value.real), self.sp.Mul(self.number(value.imag), self.sp.I))
        if isinstance(value, int):
            return self.sp.Integer(value)
        from decimal import Decimal
        numerator, denominator = Decimal(str(value)).as_integer_ratio()
        return self.sp.Rational(numerator, denominator)

    def expr(self, node, guarded=False):
        sp, op = self.sp, node['op']
        if op == 'const':
            return self.number(node['value'])
        if op == 'var':
            return self.symbols[node['name']]
        if op == 'pow':
            base = self.expr(node['base'], guarded)
            if guarded and node['exponent'] < 0:
                self.guards.append(sp.Ne(base, 0))
            return sp.Pow(base, self.number(node['exponent']))
        if op in UNARY_OPS:
            arg = self.expr(node['arg'], guarded)
            if guarded and op in {'sqrt', 'log'}:
                real = arg.is_real is True
                if op == 'sqrt' and real:
                    self.guards.append(sp.Ge(arg, 0))
                if op == 'log':
                    self.guards.append(sp.Gt(arg, 0) if real else sp.Ne(arg, 0))
            return {'sin': sp.sin, 'cos': sp.cos, 'exp': sp.exp, 'log': sp.log,
                    'sqrt': sp.sqrt, 'abs': sp.Abs}[op](arg)
        left, right = self.expr(node['left'], guarded), self.expr(node['right'], guarded)
        if op == 'div':
            if guarded:
                self.guards.append(sp.Ne(right, 0))
            return sp.Mul(left, sp.Pow(right, -1))
        return {'add': lambda: sp.Add(left, right),
                'sub': lambda: sp.Add(left, sp.Mul(-1, right)),
                'mul': lambda: sp.Mul(left, right)}[op]()

    def relation(self, node):
        sp = self.sp
        left, right = self.expr(node['left']), self.expr(node['right'])
        return {'eq': sp.Eq, 'ne': sp.Ne, 'gt': sp.Gt, 'ge': sp.Ge,
                'lt': sp.Lt, 'le': sp.Le}[node['op']](left, right, evaluate=False)

    def object(self, ident, guarded=False):
        obj = self.model.object_by_id(ident)
        if obj['kind'] == 'scalar':
            return self.expr(obj['expr'], guarded)
        rows = obj['rows'] if obj['kind'] == 'matrix' else [[value] for value in obj['entries']]
        return self.sp.Matrix([[self.expr(value, guarded) for value in row] for row in rows])

    def prove(self, relation):
        sp = self.sp
        simplified = sp.simplify(relation.subs(self.substitutions, simultaneous=True))
        if simplified is sp.true:
            return True
        if relation in self.conditions:
            return True
        variables = sorted(relation.free_symbols | set().union(*(item.free_symbols for item in self.conditions)), key=str)
        if len(variables) == 1 and variables[0].is_real:
            try:
                return sp.reduce_inequalities(self.conditions + [sp.Not(relation)], variables) is sp.false
            except (NotImplementedError, TypeError, ValueError):
                pass
        return False

    def checked_point(self, point):
        sp = self.sp
        names = list(self.symbols)
        subs = {self.symbols[name]: self.number(value) for name, value in zip(names, point)}
        for definition in self.symbol_defs:
            value = subs[self.symbols[definition['name']]]
            if definition['domain'] == 'integer' and value.is_integer is not True:
                return None
            if definition['domain'] == 'real' and value.is_real is not True:
                return None
            for assumption in definition['assumptions']:
                if assumption == 'nonzero':
                    if value.is_zero is not False:
                        return None
                elif getattr(value, 'is_' + assumption) is not True:
                    return None
        if any(sp.simplify(relation.subs(subs)) is not sp.true for relation in self.conditions):
            return None
        env = {}
        for name in names:
            value = subs[self.symbols[name]]
            env[name] = int(value) if value.is_Integer else float(value) if value.is_real else str(value)
        return env, subs

    def points(self):
        names = list(self.symbols)
        # Bounded deterministic sampling covers each parameter independently and
        # includes complex values when the domain admits them.
        values = [SAMPLE_VALUES + ((1j, -1j, 1 + 1j) if definition['domain'] == 'complex' else ())
                  for definition in self.symbol_defs]
        candidates = itertools.product(*values) if len(names) <= 3 else (
            tuple(value if i == j else 1 for i in range(len(names)))
            for j in range(len(names)) for value in values[j])
        for point in itertools.islice(candidates, 256):
            valid = self.checked_point(point)
            if valid is not None:
                yield valid

    def outcome(self, status, reason_code, reason, method='none', **evidence):
        return {'status': status, 'reason_code': reason_code, 'reason': reason,
                'method': method, 'evidence': dict(evidence, cas='sympy', cas_version=self.sp.__version__)}

    def guard_check(self):
        unresolved = [guard for guard in self.guards if not self.prove(guard)]
        if not unresolved:
            return None
        for env, subs in self.points():
            for guard in unresolved:
                if guard.subs(subs) is self.sp.false:
                    return self.outcome('fail', 'domain_violation', 'claim omits a required expression domain condition',
                                        'numerical_counterexample', counterexample=env, missing_condition=str(guard))
        # Grid sampling cannot see arbitrary algebraic singularities. Solve the
        # boundary exactly and only report witnesses satisfying frozen conditions.
        for guard in unresolved:
            if len(guard.free_symbols) != 1 or not isinstance(guard, self.sp.Unequality):
                continue
            symbol = next(iter(guard.free_symbols))
            roots = self.sp.solveset(guard.lhs - guard.rhs, symbol,
                                    domain=self.sp.S.Reals if symbol.is_real else self.sp.S.Complexes)
            if not isinstance(roots, self.sp.FiniteSet):
                continue
            for root in roots:
                if root.free_symbols:
                    continue
                point = tuple(root if item == symbol else 1 for item in self.symbols.values())
                valid = self.checked_point(point)
                if valid is not None and guard.subs(valid[1]) is self.sp.false:
                    return self.outcome('fail', 'domain_violation', 'exact algebraic singularity violates the declared domain',
                                        'numerical_counterexample', counterexample=valid[0], missing_condition=str(guard),
                                        exact_substitution={str(key): str(value) for key, value in valid[1].items()})
        return self.outcome('unknown', 'domain_unproven', 'required expression domain condition is not proved',
                            'sample_no_counterexample', missing_conditions=[str(item) for item in unresolved])

    def identity(self, left, right):
        sp = self.sp
        if isinstance(left, sp.MatrixBase) != isinstance(right, sp.MatrixBase):
            return self.outcome('fail', 'shape_mismatch', 'scalar/matrix shapes differ', 'concrete_calculation')
        if isinstance(left, sp.MatrixBase) and left.shape != right.shape:
            return self.outcome('fail', 'shape_mismatch', 'matrix shapes differ', 'concrete_calculation')
        differences = list(left - right) if isinstance(left, sp.MatrixBase) else [left - right]
        refined = [sp.simplify(value.subs(self.substitutions, simultaneous=True)) for value in differences]
        if all(value == 0 for value in refined):
            # Simplification may erase x from x/x; evidence still proves a
            # universally quantified relationship, not a concrete arithmetic fact.
            def depends(value):
                if isinstance(value, dict):
                    if value.get('op') == 'var':
                        return True
                    return any(depends(item) for item in value.values())
                return any(depends(item) for item in value) if isinstance(value, list) else False
            concrete = not any(depends(self.model.object_by_id(ident)) for ident in _object_ids(self.claim))
            return self.outcome('pass', 'proved', 'relationship holds with declared domain and conditions',
                                'concrete_calculation' if concrete else 'symbolic_proof',
                                left=str(left), right=str(right), domain_conditions=[str(item) for item in self.conditions])
        if not (left.free_symbols | right.free_symbols) and any(value.is_zero is False for value in refined):
            return self.outcome('fail', 'counterexample', 'exact concrete values disprove the relationship',
                                'concrete_calculation', left=str(left), right=str(right),
                                residuals=[str(value) for value in refined])
        for env, subs in self.points():
            for difference in differences:
                value = difference.subs(subs)
                if value.is_zero is False and value.is_finite is True:
                    return self.outcome('fail', 'counterexample', 'a valid-domain substitution disproves the identity',
                                        'numerical_counterexample', counterexample=env, residual=str(value))
        return self.outcome('unknown', 'unproved', 'CAS did not prove the identity; samples are not a proof',
                            'sample_no_counterexample', left=str(left), right=str(right))

    def solution_set(self, pair, variable):
        sp = self.sp
        symbol = self.symbols[variable]
        definition = next(item for item in self.symbol_defs if item['name'] == variable)
        domain = {'real': sp.S.Reals, 'complex': sp.S.Complexes, 'integer': sp.S.Integers}[definition['domain']]
        previous_guards = self.guards
        self.guards = []
        left, right = [self.expr(item, guarded=True) for item in pair]
        equation_guards = self.guards
        self.guards = previous_guards
        solved = sp.solveset(left - right, symbol, domain=domain)
        # ConditionSet remains unknown; never iterate an infinite unknown set.
        if isinstance(solved, sp.ConditionSet):
            return None
        if not isinstance(solved, sp.FiniteSet) and solved != sp.S.EmptySet:
            return None
        accepted = []
        for root in solved:
            if root.free_symbols:
                return None
            subs = {symbol: root}
            constraints = self.conditions + self.condition_guards + equation_guards
            # Solve on the actual frozen domain, including assumptions retained
            # by Symbol constructors (solveset's domain alone does not do this).
            assumptions = definition['assumptions']
            # A complex declaration can still impose real sign restrictions.
            # Inspect exact predicates instead of dropping all non-nonzero
            # assumptions or trying ordered comparisons on non-real roots.
            verdicts = []
            for item in assumptions:
                if item == 'nonzero':
                    verdicts.append(None if root.is_zero is None else not root.is_zero)
                else:
                    verdicts.append(getattr(root, 'is_' + item))
            if any(item is False for item in verdicts):
                continue
            if any(item is None for item in verdicts):
                return None
            verdicts = [item.subs(subs) for item in constraints]
            if any(item is sp.false for item in verdicts):
                continue
            if any(item is not sp.true for item in verdicts):
                return None
            accepted.append(root)
        return sp.FiniteSet(*accepted)

    def differentiation_regularity(self):
        """Conservative smooth-domain proof BEFORE assumption simplification.

        A formula for a formal derivative (e.g. sign(0)) is not evidence that
        the original function is differentiable. Walking the original AST keeps
        sqrt/abs nodes that constructors may simplify away under sign assumptions.
        Strict guards establish a smooth open neighborhood; unresolved boundary
        or branch behavior remains unknown even if formal expressions simplify.
        """
        sp = self.sp
        variable = self.claim['variable']
        source = self.model.object_by_id(self.claim['math_object_id'])['expr']
        requirements = []
        unsupported = []
        def visit(node, path):
            if variable not in ast_names(node):
                return
            op = node['op']
            if op in {'abs', 'sqrt', 'log'}:
                arg = self.expr(node['arg'])
                if arg.is_real is not True:
                    # Complex Abs is not holomorphic and the principal sqrt/log
                    # branches need a richer branch-domain contract than v1.
                    unsupported.append(path + ': complex branch/absolute-value regularity')
                elif op == 'abs':
                    requirements.append((path, sp.Ne(arg, 0)))
                else:
                    requirements.append((path, sp.Gt(arg, 0)))
            for key in ['left', 'right', 'base', 'arg']:
                if key in node:
                    visit(node[key], path + '.' + key)
        visit(source, 'expr')
        unresolved = [(path, guard) for path, guard in requirements if not self.prove(guard)]
        if unsupported or unresolved:
            return self.outcome('unknown', 'differentiability_unproven',
                                'ordinary differentiability of the original AST is not established on the full declared domain',
                                missing_regularity=[{'path': path, 'required': str(guard)} for path, guard in unresolved],
                                unsupported_regularity=unsupported)
        return None

    def rank(self, matrix, expected):
        sp = self.sp
        if expected > min(matrix.shape):
            return self.outcome('fail', 'rank_mismatch', 'expected rank exceeds matrix dimensions', 'concrete_calculation')
        if not matrix.free_symbols:
            actual = matrix.rank()
            return self.outcome('pass' if actual == expected else 'fail', 'proved' if actual == expected else 'rank_mismatch',
                                'exact concrete matrix rank', 'concrete_calculation', actual=actual, expected=expected)
        for env, subs in self.points():
            actual = matrix.subs(subs).rank()
            if actual != expected:
                return self.outcome('fail', 'rank_mismatch', 'special parameter value changes the claimed rank',
                                    'numerical_counterexample', counterexample=env, actual=actual, expected=expected)
        def minors(size):
            for rows in itertools.combinations(range(matrix.rows), size):
                for cols in itertools.combinations(range(matrix.cols), size):
                    yield sp.simplify(matrix.extract(rows, cols).det())
        upper = expected == min(matrix.shape) or all(value == 0 for value in minors(expected + 1))
        lower = expected == 0 or any(self.prove(sp.Ne(value, 0)) for value in minors(expected))
        if upper and lower:
            return self.outcome('pass', 'proved', 'rank bounded exactly by zero and nonzero minors',
                                'symbolic_proof', expected=expected)
        return self.outcome('unknown', 'rank_unproved', 'parameter-dependent minors are not proved on the full domain',
                            'sample_no_counterexample', expected=expected)

    def run(self):
        sp, claim = self.sp, self.claim
        kind = claim['kind']
        if kind == 'abstract':
            return self.outcome('unknown', 'unsupported', claim.get('reason', 'abstract claim needs evidence-backed review'))
        if any(item is sp.false for item in self.conditions):
            return self.outcome('unknown', 'inconsistent_conditions', 'declared conditions have no admissible domain')
        # Contradictory joint conditions must not produce vacuous mathematical pass.
        vars_in_conditions = set().union(*(item.free_symbols for item in self.conditions))
        if len(vars_in_conditions) == 1:
            try:
                if sp.reduce_inequalities(self.conditions, list(vars_in_conditions)) is sp.false:
                    return self.outcome('unknown', 'inconsistent_conditions', 'declared conditions are contradictory')
            except (NotImplementedError, TypeError, ValueError):
                pass
        guards = self.guard_check()
        if guards is not None and kind not in {'solution_set', 'equation_equivalent'}:
            return guards
        if kind == 'identity':
            return self.identity(self.object(claim['lhs_object_id']), self.object(claim['rhs_object_id']))
        if kind == 'derivative':
            regularity = self.differentiation_regularity()
            if regularity is not None:
                return regularity
            expression = self.object(claim['math_object_id'])
            actual = sp.diff(expression, self.symbols[claim['variable']], claim['order'])
            return self.identity(actual, self.object(claim['expected_object_id']))
        if kind in {'equation_equivalent', 'solution_set'}:
            left = self.solution_set(claim['left'] if kind == 'equation_equivalent' else claim['equation'], claim['variable'])
            right = self.solution_set(claim['right'], claim['variable']) if kind == 'equation_equivalent' else sp.FiniteSet(*[self.expr(item) for item in claim['solutions']])
            if left is None or right is None:
                return self.outcome('unknown', 'unsupported', 'solution set is not a supported finite unconditional set')
            equal = left == right
            return self.outcome('pass' if equal else 'fail', 'proved' if equal else 'solution_sets_differ',
                                'exact solution sets with declared domain and conditions', 'symbolic_proof',
                                left_solutions=str(left), right_solutions=str(right))
        if kind == 'matrix_rank':
            return self.rank(self.object(claim['math_object_id']), claim['expected'])
        if kind == 'matrix_inverse':
            matrix, inverse = self.object(claim['math_object_id']), self.object(claim['inverse_object_id'])
            if matrix.rows != matrix.cols or matrix.shape != inverse.shape:
                return self.outcome('fail', 'shape_mismatch', 'inverse requires matching square matrices', 'concrete_calculation')
            determinant = sp.simplify(matrix.det())
            if determinant == 0:
                return self.outcome('fail', 'singular_matrix', 'matrix is singular and has no inverse', 'concrete_calculation')
            for env, subs in self.points():
                if determinant.subs(subs) == 0:
                    return self.outcome('fail', 'singular_matrix', 'matrix is singular at an admissible parameter value',
                                        'numerical_counterexample', counterexample=env)
            if not self.prove(sp.Ne(determinant, 0)):
                return self.outcome('unknown', 'invertibility_unproved', 'determinant is not proved nonzero on the full domain', 'sample_no_counterexample')
            left = self.identity(matrix * inverse, sp.eye(matrix.rows))
            if left['status'] != 'pass':
                return left
            return self.identity(inverse * matrix, sp.eye(matrix.rows))
        return self.outcome('unknown', 'unsupported', 'claim type is unsupported')


def _deny_io(event, args):
    # Trusted imports happen first. No mathematical operation can then access
    # arbitrary files/network, launch processes, or dynamically load code.
    if event == 'open' or event.startswith(('socket.', 'subprocess.', 'ctypes.')) or event in {
        'os.system', 'os.exec', 'os.posix_spawn', 'os.fork', 'os.forkpty'}:
        raise PermissionError(f'math worker denies {event}: {args[0] if args else ""}')


def _worker(payload):
    import resource
    limits = VerificationLimits(**payload['limits'])
    resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds + 1))
    # Darwin is supervised by the parent's bounded RSS checkpoint; Linux keeps
    # the kernel virtual-address-space ceiling in addition to the other guards.
    if sys.platform != 'darwin':
        resource.setrlimit(resource.RLIMIT_AS, (limits.memory_mib * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    import sympy as sp
    # Preload trusted CAS paths before the worker loses file access.
    from sympy.solvers.solveset import solveset
    from sympy.solvers.inequalities import reduce_inequalities
    from sympy.matrices import Matrix
    from sympy.tensor.tensor import Tensor
    from sympy.sets.handlers import issubset, intersection
    warm_x = sp.Symbol('_warm_x', real=True)
    sp.simplify(sp.sqrt(warm_x ** 2) - warm_x)
    sp.solveset(warm_x ** 2 - 1, warm_x, domain=sp.S.Reals)
    sp.reduce_inequalities([sp.Ne(warm_x, 0)], warm_x)
    sp.Matrix([[warm_x, 0], [0, 1]]).rank()
    model = parse_math_model(payload['model'])
    _capability(model, payload['claim'])
    sys.addaudithook(_deny_io)
    try:
        return _CAS(model, payload['claim'], sp).run()
    except MemoryError:
        return {'status': 'unknown', 'reason_code': 'memory_limit', 'reason': 'worker memory limit reached', 'method': 'none', 'evidence': {}}
    except (ValueError, TypeError, NotImplementedError) as exc:
        return {'status': 'unknown', 'reason_code': 'unsupported', 'reason': str(exc), 'method': 'none', 'evidence': {}}
    except PermissionError as exc:
        return {'status': 'unknown', 'reason_code': 'io_denied', 'reason': str(exc), 'method': 'none', 'evidence': {}}
    except Exception as exc:
        return {'status': 'unknown', 'reason_code': 'cas_failure', 'reason': f'{type(exc).__name__}: {exc}', 'method': 'none', 'evidence': {}}


if __name__ == '__main__':
    if sys.argv[1:] != ['--worker']:
        raise SystemExit('internal mathematical worker only')
    payload = json.loads(sys.stdin.read(2 * 1024 * 1024))
    print(canonical_json(_worker(payload)))
