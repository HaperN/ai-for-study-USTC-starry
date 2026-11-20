"""Counterexamples use hand-derived answers, never the rendering evaluator."""
import sys
import threading
import time
import pytest
from test_math_model import api, c, v, binary, model_data


def verify(data, **kwargs):
    return api().verify_math(api().parse_math_model(data), **kwargs)


def identity(expr, expected, *, conditions=(), assumptions=()):
    data = model_data(expr)
    data['symbols'][0]['assumptions'] = list(assumptions)
    data['objects'].append({'math_object_id': 'g', 'kind': 'scalar', 'expr': expected})
    data['conditions'] = list(conditions)
    data['claims'] = [{'claim_id': 'identity', 'kind': 'identity', 'math_object_id': 'f',
                       'lhs_object_id': 'f', 'rhs_object_id': 'g'}]
    return data


def test_cancellation_of_division_retains_required_nonzero_condition():
    expr = binary('div', v(), v())
    check = verify(identity(expr, c(1))).by_claim('identity')
    assert check.status == 'fail' and check.reason_code == 'domain_violation'
    assert check.evidence['counterexample'] == {'x': 0}
    check = verify(identity(expr, c(1), conditions=[binary('ne', v(), c(0))])).checks[0]
    assert check.status == 'pass' and check.method == 'symbolic_proof'


def test_sqrt_square_needs_sign_assumption_and_returns_real_counterexample():
    expr = {'op': 'sqrt', 'arg': {'op': 'pow', 'base': v(), 'exponent': 2}}
    check = verify(identity(expr, v())).checks[0]
    assert check.status == 'fail' and check.method == 'numerical_counterexample'
    assert check.evidence['counterexample']['x'] < 0
    check = verify(identity(expr, v(), assumptions=['nonnegative'])).checks[0]
    assert check.status == 'pass' and check.method == 'symbolic_proof'
    assert verify(identity(expr, v(), conditions=[binary('ge', v(), c(0))])).status == 'pass'


@pytest.mark.parametrize('left,right', [
    ([binary('mul', v(), binary('sub', v(), c(1))), c(0)],
     [binary('sub', v(), c(1)), c(0)]),
    ([v(), c(1)], [{'op': 'pow', 'base': v(), 'exponent': 2}, c(1)]),
])
def test_equation_rewrite_detects_lost_or_added_roots(left, right):
    data = model_data()
    data['claims'] = [{'claim_id': 'rewrite', 'kind': 'equation_equivalent',
                       'math_object_id': 'f', 'left': left, 'right': right, 'variable': 'x'}]
    check = verify(data).checks[0]
    assert check.status == 'fail'
    assert check.reason_code == 'solution_sets_differ'
    assert check.evidence['left_solutions'] != check.evidence['right_solutions']
    data['conditions'] = [binary('gt', v(), c(0))]
    assert verify(data).status == 'pass'


def matrix_data(rows, claim, *, conditions=()):
    data = model_data()
    data['objects'] = [{'math_object_id': 'A', 'kind': 'matrix', 'rows': rows}]
    data['conditions'] = list(conditions)
    data['claims'] = [dict(claim, claim_id='matrix', math_object_id='A')]
    return data


def test_rank_checks_special_parameter_values_instead_of_generic_rank():
    data = matrix_data([[v(), c(0)], [c(0), c(1)]], {'kind': 'matrix_rank', 'expected': 2})
    check = verify(data).checks[0]
    assert check.status == 'fail' and check.evidence['counterexample'] == {'x': 0}
    data['conditions'] = [binary('ne', v(), c(0))]
    assert verify(data).checks[0].method == 'symbolic_proof'
    assert verify(data).status == 'pass'


def test_wrong_inverse_and_singular_matrix_have_independent_evidence():
    data = matrix_data([[c(1), c(2)], [c(3), c(4)]],
                       {'kind': 'matrix_inverse', 'inverse_object_id': 'B'})
    data['objects'].append({'math_object_id': 'B', 'kind': 'matrix',
                            'rows': [[c(1), c(0)], [c(0), c(1)]]})
    assert verify(data).status == 'fail'
    data['objects'][1]['rows'] = [[c(-2), c(1)], [c(1.5), c(-.5)]]
    check = verify(data).checks[0]
    assert check.status == 'pass' and check.method == 'concrete_calculation'
    data['objects'][0]['rows'] = [[c(1), c(2)], [c(2), c(4)]]
    assert verify(data).checks[0].reason_code == 'singular_matrix'


def test_exact_derivative_and_explicit_solution_set_are_supported():
    data = model_data({'op': 'pow', 'base': v(), 'exponent': 3})
    data['objects'].append({'math_object_id': 'df', 'kind': 'scalar',
                            'expr': binary('mul', c(3), {'op': 'pow', 'base': v(), 'exponent': 2})})
    data['claims'] = [{'claim_id': 'derivative', 'kind': 'derivative', 'math_object_id': 'f',
                       'variable': 'x', 'order': 1, 'expected_object_id': 'df'},
                      {'claim_id': 'roots', 'kind': 'solution_set', 'math_object_id': 'f',
                       'equation': [{'op': 'pow', 'base': v(), 'exponent': 2}, c(1)],
                       'variable': 'x', 'solutions': [c(-1), c(1)]}]
    assert verify(data).status == 'pass'
    data['claims'][1]['solutions'] = [c(1)]
    assert verify(data).by_claim('roots').status == 'fail'


def test_abstract_claim_and_empty_report_never_pass():
    data = model_data()
    data['claims'] = [{'claim_id': 'abstract', 'kind': 'abstract', 'math_object_id': 'f',
                       'reason': 'an abstract existence proof needs textual review'}]
    check = verify(data).checks[0]
    assert check.status == 'unknown' and check.reason_code == 'unsupported'
    assert verify(model_data()).status == 'unknown'


def test_supported_claim_cannot_omit_expression_or_change_frozen_claim():
    data = identity(v(), v())
    data['claims'][0].pop('rhs_object_id')
    with pytest.raises(Exception, match='rhs_object_id'):
        verify(data)
    model = api().parse_math_model(identity(v(), v()))
    with pytest.raises(Exception, match='frozen'):
        api().verify_math(model, [dict(model.claim_by_id('identity'), rhs_object_id='f')])


def test_safe_models_above_capability_are_unknown_not_simplified():
    data = identity({'op': 'pow', 'base': v(), 'exponent': 9}, c(1))
    check = verify(data).checks[0]
    assert check.status == 'unknown' and check.reason_code == 'capability_limit'
    expr = v()
    for _ in range(13):
        expr = binary('add', expr, c(0))
    assert verify(identity(expr, v())).checks[0].reason_code == 'capability_limit'


def test_cancel_and_real_worker_timeout_return_unknown_and_leave_no_children():
    canceled = threading.Event()
    canceled.set()
    check = verify(identity(v(), v()), cancel=canceled).checks[0]
    assert check.status == 'unknown' and check.reason_code == 'cancelled'
    start = time.monotonic()
    limits = api().VerificationLimits(wall_seconds=.001)
    check = verify(identity(v(), v()), limits=limits).checks[0]
    assert check.status == 'unknown' and check.reason_code == 'timeout'
    assert time.monotonic() - start < 2


@pytest.mark.parametrize('outcome', ['pass', 'cancelled', 'timeout'])
def test_large_valid_model_progresses_after_delayed_worker_read_and_reaps(monkeypatch, outcome):
    """An actual blocked pipe must resume writing and deliver EOF to the CAS worker."""
    import os
    import json
    import subprocess
    import sys

    data = identity(v(), v())
    data['source_refs'] = [f'source-{index:03d}-' + 'x' * 240 for index in range(256)]
    data['objects'].extend(
        {'math_object_id': f'context{index}', 'kind': 'matrix',
         'rows': [[c(column) for column in range(8)] for _ in range(8)]}
        for index in range(32)
    )
    model = api().parse_math_model(data)
    model_bytes = json.dumps(model.as_dict()).encode()
    # Measure a real pipe without Linux-only F_GETPIPE_SZ.
    read_fd, write_fd = os.pipe()
    pipe_capacity = 0
    try:
        os.set_blocking(write_fd, False)
        while True:
            try:
                pipe_capacity += os.write(write_fd, b'x' * 65536)
            except BlockingIOError:
                break
    finally:
        os.close(read_fd)
        os.close(write_fd)
    assert len(model_bytes) > pipe_capacity
    original = subprocess.Popen
    children = []
    timers = []
    cancelled = threading.Event()
    delay = .15 if outcome == 'pass' else 1
    source = f'''
import json, sys, time
time.sleep({delay})
from aiforstudy.blocks.math.verify import _worker
print(json.dumps(_worker(json.loads(sys.stdin.read()))))
'''

    def spawn(args, **kwargs):
        if args[-1] != '--worker':
            return original(args, **kwargs)
        child = original([sys.executable, '-c', source], **kwargs)
        children.append(child)
        if outcome == 'cancelled':
            timer = threading.Timer(.05, cancelled.set)
            timers.append(timer)
            timer.start()
        return child

    monkeypatch.setattr(subprocess, 'Popen', spawn)
    limits = api().VerificationLimits(wall_seconds=.08 if outcome == 'timeout' else 2)
    started = time.monotonic()
    try:
        report = api().verify_math(model, limits=limits, cancel=cancelled)
    finally:
        for timer in timers:
            timer.join()
    check = report.checks[0]
    assert children and all(child.poll() is not None for child in children)
    assert time.monotonic() - started < 3
    assert check.evidence['model_digest'] == model.digest
    if outcome == 'pass':
        assert check.status == 'pass', (check.reason_code, check.reason)
        assert check.method == 'symbolic_proof'
    else:
        assert check.status == 'unknown' and check.reason_code == outcome
    assert all(stream.closed for child in children
               for stream in (child.stdin, child.stdout, child.stderr))


def test_verification_does_not_call_numeric_renderer_or_string_parsers(monkeypatch):
    import sympy
    import aiforstudy.blocks.math.expression as expression
    def denied(*args, **kwargs):
        raise AssertionError('unsafe or dependent evaluator called')
    monkeypatch.setattr(expression, 'evaluate_expression', denied)
    monkeypatch.setattr(sympy, 'parse_expr', denied)
    monkeypatch.setattr(sympy, 'lambdify', denied)
    report = verify(identity(binary('add', v(), c(1)), binary('add', c(1), v())))
    assert report.status == 'pass'
    assert report.as_dict()['verifier_version'] == '2.0'
    assert report.checks[0].evidence_hash.startswith('sha256:')


def test_equation_domains_are_checked_per_side_before_comparing_roots():
    data = model_data()
    data['claims'] = [{'claim_id': 'rewrite', 'kind': 'equation_equivalent',
                       'math_object_id': 'f', 'variable': 'x',
                       'left': [binary('mul', v(), binary('sub', v(), c(1))), c(0)],
                       'right': [binary('mul', binary('div', v(), v()), binary('sub', v(), c(1))), c(0)]}]
    check = verify(data).checks[0]
    assert check.status == 'fail'
    assert check.evidence['left_solutions'] == '{0, 1}'
    assert check.evidence['right_solutions'] == '{1}'


def test_inconsistent_conditions_do_not_get_vacuous_proof():
    data = identity(v(), v(), conditions=[binary('gt', v(), c(1)), binary('lt', v(), c(0))])
    check = verify(data).checks[0]
    assert check.status == 'unknown' and check.reason_code == 'inconsistent_conditions'


def test_real_log_identity_preserves_positive_domain():
    expression = {'op': 'exp', 'arg': {'op': 'log', 'arg': v()}}
    assert verify(identity(expression, v())).checks[0].reason_code == 'domain_violation'
    assert verify(identity(expression, v(), conditions=[binary('gt', v(), c(0))])).status == 'pass'


def test_integer_solution_sets_reject_noninteger_roots():
    data = model_data()
    data['symbols'][0]['domain'] = 'integer'
    data['claims'] = [{'claim_id': 'roots', 'kind': 'solution_set', 'math_object_id': 'f',
                       'variable': 'x', 'equation': [binary('mul', c(2), v()), c(1)], 'solutions': []}]
    assert verify(data).status == 'pass'
    data['claims'][0]['solutions'] = [c(.5)]
    assert verify(data).status == 'fail'


def test_abstract_and_unproved_sampling_never_claim_symbolic_proof():
    data = identity({'op': 'sin', 'arg': v()}, v())
    check = verify(data).checks[0]
    assert check.status == 'fail' and check.method == 'numerical_counterexample'
    data = model_data()
    data['claims'] = [{'claim_id': 'roots', 'kind': 'solution_set', 'math_object_id': 'f',
                       'variable': 'x', 'equation': [{'op': 'sin', 'arg': v()}, c(0)], 'solutions': [c(0)]}]
    check = verify(data).checks[0]
    assert check.status == 'unknown' and check.reason_code == 'unsupported'


def test_nonzero_complex_symbols_do_not_become_real_symbols():
    expression = {'op': 'sqrt', 'arg': {'op': 'pow', 'base': v(), 'exponent': 2}}
    data = identity(expression, {'op': 'abs', 'arg': v()}, assumptions=['nonzero'])
    data['symbols'][0]['domain'] = 'complex'
    # At x=i sqrt(x*x)=i while abs(i)=1, so this is false over complex x.
    assert verify(data).status != 'pass'


def test_domain_counterexample_finds_denominator_root_between_grid_points():
    expression = binary('div', binary('sub', binary('mul', c(7), v()), c(1)),
                        binary('sub', binary('mul', c(7), v()), c(1)))
    check = verify(identity(expression, c(1))).checks[0]
    assert check.status == 'fail' and check.reason_code == 'domain_violation'
    assert check.evidence['counterexample']['x'] == pytest.approx(1 / 7)


def test_report_cannot_be_forged_pass_from_empty_or_sampling_evidence():
    from aiforstudy.errors import ContractError
    with pytest.raises(ContractError):
        api().MathCheckReport((), 'pass', 'sha256:empty')
    with pytest.raises(ContractError):
        api().MathCheck('fake', ('f',), 'math.identity.v1', 'pass', 'ok', '',
                        'sample_no_counterexample', '{}')


def test_worker_denies_actual_file_and_network_operations_in_its_process(tmp_path):
    import json
    import os
    import subprocess
    import sys
    secret = tmp_path / 'private.txt'
    secret.write_text('not mathematical data', encoding='utf-8')
    payload = {'model': model_data(), 'claim': {'claim_id': 'probe', 'kind': 'abstract',
               'math_object_id': 'f', 'conditions': []}, 'limits': api().VerificationLimits().as_dict()}
    # This trusted test probe runs inside the real worker after import/resource
    # setup; production has no test controls or user-executable escape hatch.
    code = '''
import json, socket, sys
from aiforstudy.blocks.math import verify as module
payload=json.loads(sys.stdin.read())
def probe(self):
    denied=[]
    for name, operation in [('file', lambda: open(sys.argv[1]).read()),
                            ('network', lambda: socket.socket())]:
        try: operation()
        except PermissionError: denied.append(name)
    return {'status':'unknown','reason_code':'probe','reason':'','method':'none','evidence':{'denied':denied}}
module._CAS.run=probe
print(json.dumps(module._worker(payload)))
'''
    env = dict(os.environ, OPENBLAS_NUM_THREADS='1')
    result = subprocess.run([sys.executable, '-c', code, str(secret)], input=json.dumps(payload),
                            text=True, capture_output=True, env=env, timeout=3)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['evidence']['denied'] == ['file', 'network']
    assert secret.read_text(encoding='utf-8') == 'not mathematical data'


def test_worker_cpu_limit_terminates_real_infinite_computation():
    import json
    import os
    import signal
    import subprocess
    import sys
    payload = {'model': model_data(), 'claim': {'claim_id': 'probe', 'kind': 'abstract',
               'math_object_id': 'f', 'conditions': []},
               'limits': api().VerificationLimits(cpu_seconds=1, wall_seconds=3).as_dict()}
    code = '''
import json, sys
from aiforstudy.blocks.math import verify as module
def compute(self):
    while True: pass
module._CAS.run=compute
module._worker(json.loads(sys.stdin.read()))
'''
    result = subprocess.run([sys.executable, '-c', code], input=json.dumps(payload), text=True,
                            capture_output=True, env=dict(os.environ, OPENBLAS_NUM_THREADS='1'), timeout=3)
    assert result.returncode == -signal.SIGXCPU


@pytest.mark.skipif(sys.platform == 'darwin',
                    reason='kernel RLIMIT_AS probe; Darwin host enforcement is tested in test_math_resource_limits')
def test_worker_memory_limit_returns_unknown_instead_of_pass():
    import json
    import os
    import subprocess
    import sys
    payload = {'model': model_data(), 'claim': {'claim_id': 'probe', 'kind': 'abstract',
               'math_object_id': 'f', 'conditions': []}, 'limits': api().VerificationLimits().as_dict()}
    code = '''
import json, sys
from aiforstudy.blocks.math import verify as module
def allocate(self):
    enormous=bytearray(700*1024*1024)
    return {'status':'pass'}
module._CAS.run=allocate
print(json.dumps(module._worker(json.loads(sys.stdin.read()))))
'''
    result = subprocess.run([sys.executable, '-c', code], input=json.dumps(payload), text=True,
                            capture_output=True, env=dict(os.environ, OPENBLAS_NUM_THREADS='1'), timeout=3)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['reason_code'] == 'memory_limit'


def test_cancel_after_worker_start_reaps_process(monkeypatch):
    import subprocess
    event = threading.Event()
    original = subprocess.Popen
    children = []
    def spawn(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    timer = threading.Timer(.05, event.set)
    timer.start()
    try:
        check = verify(identity(v(), v()), cancel=event).checks[0]
    finally:
        timer.join()
    assert check.reason_code == 'cancelled'
    assert children and all(child.poll() is not None for child in children)


def test_exact_identity_can_use_declared_equalities_instead_of_sampling():
    data = identity(binary('mul', v(), v()), c(4), conditions=[binary('eq', v(), c(2))])
    check = verify(data).checks[0]
    assert check.status == 'pass' and check.method == 'symbolic_proof'


def test_constant_failure_and_parameter_inverse_do_not_get_unknown():
    check = verify(identity(c(1), c(2))).checks[0]
    assert check.status == 'fail' and check.method == 'concrete_calculation'
    data = matrix_data([[v()]], {'kind': 'matrix_inverse', 'inverse_object_id': 'B'},
                       conditions=[binary('ne', v(), c(0))])
    data['objects'].append({'math_object_id': 'B', 'kind': 'matrix',
                            'rows': [[binary('div', c(1), v())]]})
    assert verify(data).status == 'pass'


def test_math_proof_evidence_binds_frozen_model_and_claim():
    model = api().parse_math_model(identity(v(), v()))
    check = api().verify_math(model).checks[0]
    assert check.evidence['model_digest'] == model.digest
    assert check.evidence['claim_digest'].startswith('sha256:')


def test_matrix_and_ast_node_capability_limits_are_explicit_unknown():
    data = matrix_data([[c(1) for _ in range(9)] for _ in range(9)],
                       {'kind': 'matrix_rank', 'expected': 1})
    assert verify(data).checks[0].reason_code == 'capability_limit'
    def balanced(depth):
        return v() if depth == 0 else binary('add', balanced(depth - 1), balanced(depth - 1))
    assert verify(identity(balanced(8), c(1))).checks[0].reason_code == 'capability_limit'


def test_trusted_CAS_constructor_path_never_calls_render_interpreter(monkeypatch):
    import sympy
    from aiforstudy.blocks.math.verify import _CAS
    import aiforstudy.blocks.math.expression as expression
    def denied(*args, **kwargs):
        raise AssertionError('renderer/text parser is not an independent verification path')
    monkeypatch.setattr(expression, 'evaluate_expression', denied)
    monkeypatch.setattr(sympy, 'parse_expr', denied)
    monkeypatch.setattr(sympy, 'lambdify', denied)
    model = api().parse_math_model(identity(binary('add', v(), c(1)), binary('add', c(1), v())))
    result = _CAS(model, model.claim_by_id('identity'), sympy).run()
    assert result['status'] == 'pass' and result['method'] == 'symbolic_proof'


def derivative_model(expression, expected, *, conditions=(), assumptions=()):
    data = model_data(expression)
    data['symbols'][0]['assumptions'] = list(assumptions)
    data['objects'].append({'math_object_id': 'df', 'kind': 'scalar', 'expr': expected})
    data['conditions'] = list(conditions)
    data['claims'] = [{'claim_id': 'derivative', 'kind': 'derivative', 'math_object_id': 'f',
                       'variable': 'x', 'order': 1, 'expected_object_id': 'df'}]
    return data


def test_abs_derivative_at_zero_cannot_pass_by_substituting_into_sign():
    data = derivative_model({'op': 'abs', 'arg': v()}, c(0),
                            conditions=[binary('eq', v(), c(0))])
    check = verify(data).checks[0]
    assert check.status == 'unknown' and check.reason_code == 'differentiability_unproven'
    assert check.method != 'symbolic_proof'


@pytest.mark.parametrize('use_assumption', [False, True])
def test_sqrt_square_derivative_cannot_pass_at_closed_nonnegative_boundary(use_assumption):
    expression = {'op': 'sqrt', 'arg': {'op': 'pow', 'base': v(), 'exponent': 2}}
    data = derivative_model(expression, c(1),
                            conditions=[] if use_assumption else [binary('ge', v(), c(0))],
                            assumptions=['nonnegative'] if use_assumption else [])
    check = verify(data).checks[0]
    assert check.status == 'unknown' and check.reason_code == 'differentiability_unproven'


@pytest.mark.parametrize('expression', [
    {'op': 'abs', 'arg': v()},
    {'op': 'sqrt', 'arg': {'op': 'pow', 'base': v(), 'exponent': 2}},
])
def test_derivative_on_open_positive_domain_keeps_valid_proof(expression):
    data = derivative_model(expression, c(1), conditions=[binary('gt', v(), c(0))])
    check = verify(data).checks[0]
    assert check.status == 'pass' and check.method == 'symbolic_proof'


def test_complex_positive_solution_filter_keeps_actual_positive_root():
    data = model_data()
    data['symbols'][0].update(domain='complex', assumptions=['positive'])
    data['claims'] = [{'claim_id': 'roots', 'kind': 'solution_set', 'math_object_id': 'f',
                       'variable': 'x', 'equation': [v(), c(1)], 'solutions': []}]
    check = verify(data).checks[0]
    assert check.status == 'fail' and check.evidence['left_solutions'] == '{1}'
    data['claims'][0]['solutions'] = [c(1)]
    assert verify(data).status == 'pass'


def test_complex_nonnegative_solution_filter_preserves_zero_boundary_root():
    data = model_data()
    data['symbols'][0].update(domain='complex', assumptions=['nonnegative'])
    data['claims'] = [{'claim_id': 'roots', 'kind': 'solution_set', 'math_object_id': 'f',
                       'variable': 'x', 'equation': [v(), c(0)], 'solutions': [c(0)]}]
    assert verify(data).status == 'pass'


@pytest.mark.parametrize('assumption,root', [('positive', -1), ('negative', 1),
                                            ('nonnegative', -1), ('nonpositive', 1)])
def test_complex_sign_assumptions_reject_opposite_sign_roots(assumption, root):
    data = model_data()
    data['symbols'][0].update(domain='complex', assumptions=[assumption])
    data['claims'] = [{'claim_id': 'roots', 'kind': 'solution_set', 'math_object_id': 'f',
                       'variable': 'x', 'equation': [v(), c(root)], 'solutions': []}]
    assert verify(data).status == 'pass'
