"""Diagnostics for broken mathematical references; corrections undergo full revalidation."""
from copy import deepcopy
import re

from .generation_corrections import LocalCorrectionRequired
from ..math.contracts import ID_RE, parse_math_model
from ..math.expression import evaluate_expression, normalize_ast, NAME_RE
from ...errors import ContractError

REFERENCE_FIELDS = ('math_object_id', 'lhs_object_id', 'rhs_object_id', 'inverse_object_id', 'expected_object_id')


def _expressions(model, base):
    """Enumerate only contract-defined expression slots, including conditions."""
    for index, obj in enumerate(model.get('objects', [])):
        if not isinstance(obj, dict):
            continue
        path = f'{base}/objects/{index}'
        if obj.get('kind') == 'scalar' and 'expr' in obj:
            yield path+'/expr', obj['expr']
        elif obj.get('kind') == 'vector' and isinstance(obj.get('entries'), list):
            for j, expr in enumerate(obj['entries']): yield f'{path}/entries/{j}', expr
        elif obj.get('kind') == 'matrix' and isinstance(obj.get('rows'), list):
            for j, row in enumerate(obj['rows']):
                if isinstance(row, list):
                    for k, expr in enumerate(row): yield f'{path}/rows/{j}/{k}', expr
    owners = [(base, model)] + [(f'{base}/claims/{i}', row)
        for i, row in enumerate(model.get('claims', [])) if isinstance(row, dict)]
    for path, owner in owners:
        conditions = owner.get('conditions', [])
        if isinstance(conditions, list):
            for i, condition in enumerate(conditions):
                if isinstance(condition, dict):
                    for side in ('left', 'right'):
                        if side in condition: yield f'{path}/conditions/{i}/{side}', condition[side]
        for field in ('left', 'right', 'equation', 'solutions'):
            if isinstance(owner.get(field), list):
                for i, expr in enumerate(owner[field]): yield f'{path}/{field}/{i}', expr


def _var_nodes(expr, path):
    # The strict AST validator has already rejected unsupported operators,
    # unsafe names and oversized trees before this traversal.
    if expr['op'] == 'var':
        yield path, expr['name']
    for key, value in expr.items():
        if isinstance(value, dict): yield from _var_nodes(value, path+'/'+key)


def _scalar_candidate(name, objects, declared, visiting=()):
    if len(visiting) >= 64 or name in visiting or name not in objects or objects[name].get('kind') != 'scalar':
        raise ContractError('object alias is cyclic, missing or non-scalar')
    expr, _, _ = normalize_ast(objects[name]['expr'], allowed_names=None, enforce_limits=False)
    def expand(node):
        if node['op'] == 'var' and node['name'] not in declared:
            return _scalar_candidate(node['name'], objects, declared, (*visiting, name))
        return {key:expand(value) if isinstance(value, dict) else value for key,value in node.items()}
    result = expand(expr)
    return normalize_ast(result, allowed_names=declared, enforce_limits=False)[0]


def _symbol_source(content, model, name, source):
    """An append needs both an explicit teaching declaration and frozen quote.

    This is provenance eligibility, never proof that a chosen domain is true;
    normal CAS and source-fidelity review still inspect the resulting model.
    """
    source = source.as_dict() if hasattr(source, 'as_dict') else source or {}
    text = source.get('source_text', '')
    claim_ids = {row.get('claim_id') for row in model.get('claims', []) if isinstance(row, dict)}
    for index, claim in enumerate(content.get('teaching_plan', {}).get('claims', [])):
        if claim.get('id') not in claim_ids or name not in claim.get('symbols', []):
            continue
        for ref in claim.get('source_refs', []):
            quote = ref.get('quote') if isinstance(ref, dict) else None
            if (isinstance(quote, str) and quote and quote in text
                    and re.search(r'(?<![A-Za-z0-9_])'+re.escape(name)+r'(?![A-Za-z0-9_])', quote)):
                return {'teaching_symbol_path':f'/content/teaching_plan/claims/{index}/symbols',
                        'source_quote':quote}
    return None


def _symbol_errors(content, model, base, source):
    rows = model.get('symbols')
    if not isinstance(rows, list): return [], set()
    declared = {row['name'] for row in rows if isinstance(row, dict)
                and isinstance(row.get('name'), str) and NAME_RE.fullmatch(row['name'])}
    objects = {row['math_object_id']:row for row in model['objects'] if isinstance(row, dict)
               and isinstance(row.get('math_object_id'), str)}
    errors, additions = [], set()
    references = []
    for path, raw in _expressions(model, base):
        try:
            expr, _, _ = normalize_ast(raw, path, allowed_names=None, enforce_limits=False)
        except ContractError:
            # D16：结构非法的表达式**不在这里判死**。它的结构问题会由末尾的
            # parse_math_model 以**可纠正**错误报出；这里跳过该表达式即可。
            # 实测：这条路径比末尾段更早，一穿出就是 0/2 轮判死
            # （`_collect_author_errors` 只接 LocalCorrectionRequired）。
            continue
        references.extend((pointer, name, False) for pointer,name in _var_nodes(expr, path))
    for j, claim in enumerate(model['claims']):
        if isinstance(claim, dict) and 'variable' in claim:
            name = claim['variable']
            if not isinstance(name, str) or not NAME_RE.fullmatch(name):
                # 统一放宽：变成一条可纠正诊断，不在这里判死。
                errors.append(dict(path=f'{base}/claims/{j}/variable', code='unsafe_math_symbol',
                    value=str(name),
                    fix='Use a declared symbol name matching [A-Za-z][A-Za-z0-9_]{0,63}.',
                    passing_criterion='Every referenced symbol name is a declared, safe identifier and the strict '
                                      'mathematical contract parses.'))
                continue
            references.append((f'{base}/claims/{j}/variable', name, True))
    for path, name, scalar_name in references:
        if name in declared: continue
        evidence = None
        if name in objects:
            candidates = []
            if not scalar_name:
                try: candidates = [_scalar_candidate(name, objects, declared)]
                except (ContractError, KeyError): pass
            code = 'object_used_as_symbol'
            fix = ('This name is an existing mathematical object, not an independent symbol. '
                'The supplied scalar-expression candidates show valid alias expansion. '
                'The contract has no object_ref operator. Do not append this name to symbols, replace it with zero, '
                'or treat an object as an independent variable. Related mathematical changes require full revalidation.')
        else:
            candidates = sorted(declared) if scalar_name else [{'op':'var','name':n} for n in sorted(declared)]
            evidence = _symbol_source(content, model, name, source)
            if evidence:
                additions.add(name)
                candidates.append(name if scalar_name else {'op':'var','name':name})
            code = 'undeclared_math_symbol'
            fix = ('Choose only a declared symbol candidate if it expresses the intended unchanged mathematics. '
                'The symbol_declaration_evidence and append_only hints identify grounded missing declarations; '
                'declare real domain/assumptions from the source and validate every additional symbol. '
                'Do not turn a function or composite object into an independent variable to pass validation.')
        row = dict(path=path, code=code, value=name, candidates=candidates,
            existing_symbol_names=sorted(declared), fix=fix,
            passing_criterion='All references resolve in the strict AST contract; existing identities and frozen source facts remain unchanged. The complete candidate still undergoes CAS and source review.')
        if evidence: row['symbol_declaration_evidence'] = evidence
        errors.append(row)
    return errors, additions


def validate_author_math(content, *, source=None):
    models = content.get('math_models')
    if not isinstance(models, list):
        return  # The ordinary content schema owns container/type errors.
    errors, append_only = [], {}
    for i, model in enumerate(models):
        if not isinstance(model, dict):
            continue
        objects, claims = model.get('objects'), model.get('claims')
        if not isinstance(objects, list) or not isinstance(claims, list):
            continue
        ids = {row.get('math_object_id') for row in objects
               if isinstance(row, dict) and isinstance(row.get('math_object_id'), str)}
        by_id = {row.get('math_object_id'): row for row in objects if isinstance(row, dict)}
        symbols_ok = (isinstance(model.get('symbols'), list) and all(
            isinstance(row, dict) and isinstance(row.get('name'), str) for row in model['symbols']))
        missing = set()
        for j, claim in enumerate(claims):
            if not isinstance(claim, dict):
                continue
            for field in REFERENCE_FIELDS:
                ref = claim.get(field)
                if isinstance(ref, str) and ID_RE.fullmatch(ref) and ref not in ids:
                    missing.add(ref)
                    errors.append(dict(path=f'/content/math_models/{i}/claims/{j}/{field}',
                        code='unknown_math_object_id', value=ref,
                        existing_object_ids=sorted(ids),
                        fix='Reference the existing mathematical object intended by this unchanged claim, or append '
                            'a real scalar/vector/matrix definition for its missing object and required symbols. '
                            'Never insert dummy constants. If the claim is genuinely abstract or not computable '
                            '(for example "the complex field admits no order compatible with its operations"), '
                            'do not fabricate an object: remove that relation from the math model and give '
                            'per-item source-backed reasons instead — the CAS is then expected to stay unknown.',
                        passing_criterion='Every reference resolves; existing identities and frozen source facts remain unchanged; the complete candidate undergoes mathematical and semantic checks.'))
            # 注意：变量依赖检查**不在这里**。它在 guard_author_correction（纠正确认后）里跑，
            # 且必须晚于结构解析 —— 否则 {'op':'execute'} 这类会被当成"常量占位"退回，
            # 而不是被当成结构错误处理。

        symbol_errors, symbols = _symbol_errors(content, model, f'/content/math_models/{i}', source)
        errors.extend(symbol_errors)
        if missing or symbols:
            append_only[f'/content/math_models/{i}/objects'] = {
                'object_ids': sorted(missing), 'symbol_names': sorted(symbols),
                'symbols_path': f'/content/math_models/{i}/symbols'}
    if errors:
        raise LocalCorrectionRequired('author mathematical references: ' + '; '.join(
            row['path'] + ': ' + row['code'] + ' ' + row['value'] for row in errors),
            errors=errors, allowed_paths=[row['path'] for row in errors],
            kind='math_references', append_only=append_only)
    for i, model in enumerate(models):
        try:
            parse_math_model(model)
        except ContractError as exc:
            # D16 放宽：结构解析失败一律**退回一轮**，不再首份判死。
            #
            # 这里原来抛普通 ContractError，而 _validate_author_output（generation.py:100-122）
            # 只接 LocalCorrectionRequired，于是它直接穿出 → run_local_generation 判死 → 0/2 轮。
            # 实测死因：run-20261010-161505（重复 claim_id）、run-20261010-170053（未声明符号）。
            #
            # 宿主只解析闭合 AST，不执行任意表达式；可解析但超出计算能力的关系
            # 在验证阶段保留 unknown/capability_limit。
            raise LocalCorrectionRequired(
                f'/content/math_models/{i}: {exc}',
                errors=[dict(path=f'/content/math_models/{i}', code='math_model_invalid',
                    detail=str(exc),
                    fix='仅修复指出的数学结构错误及其必要关联，未涉及的公式、条件、对象和关系保持原样；对照底稿后返回完整内容。'
                        '使用支持的 AST 算子及真实声明的符号；能表达的关系须如实建模。'
                        '确实超出能力的关系说明来源与限制，不使用无关对象充数。',
                    passing_criterion='数学模型可解析，关系、条件和来源含义完整；'
                                      '仍须通过 CAS 与语义审核。')],
                allowed_paths=[f'/content/math_models/{i}'], kind='math_model') from exc


def _variables(value):
    if isinstance(value, dict):
        if value.get('op') == 'var' and isinstance(value.get('name'), str):
            yield value['name']
        for child in value.values():
            yield from _variables(child)
    elif isinstance(value, list):
        for child in value:
            yield from _variables(child)


def _placeholder_correction(code, path, detail, fix, *, append_only=None):
    """D16：占位对象是**模型能改**的判定，必须退回一轮，不能首份判死。

    实测 run-20261011-125024：模型给 4 条抽象主张补了 `{"op":"const","value":0}`，
    闸门**判对了**，但抛的是普通 ContractError → lesson 直接 blocked，
    配置的 2 轮纠正只用了 1 轮。判据保留，改的只是处置方式。

    `append_only` 必须由调用方带上同一个对象：护栏只在"上一轮授权追加的对象"上检查，
    所以如果这一轮的报错不带授权，模型把同一个占位对象原样交回来时**第二轮就没人再看它**了
    （实测：占位会被接受）。带上它，复检就会一直持续到改对或用尽轮次。
    """
    raise LocalCorrectionRequired(f'author mathematical references: {path}: {code} {detail}',
        errors=[dict(path=path, code=code, detail=detail, fix=fix,
            passing_criterion='The abstract relation refers to a genuine mathematical object rather than an '
                              'unrelated constant placeholder, or is explained accurately from its source. '
                              'Concrete equations and identities are checked independently by the math verifier.')],
        allowed_paths=[path], kind='math_references', append_only=append_only or {})


def _require_variable_dependence(obj, symbols, *, path='/', append_only=None):
    """Conservative anti-placeholder gate, never a mathematical verification.

    The existing bounded AST evaluator must find differing finite values. This
    rejects constants disguised as n-n as well as an existing constant selected
    as a new target. Failure to find a witness blocks this automatic correction;
    it does not label the original mathematical statement false.
    """
    import numpy as np

    fix = ('Append a real scalar/vector/matrix object whose value genuinely depends on declared symbols, with '
           'domain and assumptions taken from the source; or, if this relation is genuinely abstract or not '
           'computable, remove it from the math model and give per-item source-backed reasons instead. '
           'Never insert a constant placeholder such as {"op":"const","value":0} to satisfy the reference check.')
    names = sorted(set(_variables(obj)))
    if not names or len(names) > 64:
        _placeholder_correction('constant_placeholder_object', path,
            'the appended object has no variable dependence', fix, append_only=append_only)
    by_name = {row['name']: row for row in symbols}
    if not set(names) <= set(by_name):
        _placeholder_correction('object_uses_undeclared_symbols', path,
            'the appended object uses symbols that are not declared: ' + ', '.join(sorted(set(names) - set(by_name))),
            fix, append_only=append_only)
    baseline = {name: (-1 if set(by_name[name].get('assumptions', [])) &
                      {'negative', 'nonpositive'} else 1) * (i + 2)
                for i, name in enumerate(names)}
    samples = [baseline]
    for name in names:
        for factor in (2, 3):
            samples.append({**baseline, name: baseline[name] * factor})
    first = None
    for values in samples:
        try:
            value = np.asarray(evaluate_expression(obj, symbols, values))
        except ContractError:
            continue
        if first is None:
            first = value
        elif not np.allclose(first, value, rtol=1e-10, atol=1e-12):
            return
    _placeholder_correction('constant_placeholder_object', path,
        'no bounded witness of variable dependence was found; the object behaves like a constant', fix,
        append_only=append_only)


def guard_author_correction(before, after, error):
    """Keep the semantic placeholder check, not an append/field permission table."""
    for pointer in error.append_only:
        index = int(pointer.split('/')[3])
        try:
            current = after['content']['math_models'][index]
            by_id = {row.get('math_object_id'): row for row in current['objects']}
            for row in error.errors:
                parts = row['path'].split('/')
                if len(parts) != 7 or parts[1:3] != ['content', 'math_models'] or parts[3:5] != [str(index), 'claims']:
                    continue
                claim = current['claims'][int(parts[5])]
                if claim.get('kind') != 'abstract':
                    continue
                target = claim.get(parts[6])
                if target in by_id:
                    # 报错路径用 **claim 路径**：复检机制靠它在下轮重新读出同一个对象。
                    # 带上 append_only 授权，模型把占位对象原样交回来时会被再次拦住。
                    _require_variable_dependence(by_id[target], current['symbols'], path=row['path'],
                        append_only={pointer: {'object_ids': [target], 'symbol_names': [],
                                               'symbols_path': f'/content/math_models/{index}/symbols'}})
        except (KeyError, IndexError, TypeError) as exc:
            raise ContractError('math correction removed frozen model structure') from exc


def placeholder_object_findings(content):
    """Reject unrelated scalar placeholders for abstract claims, not equation constants.

    A concrete identity may legitimately contain zero or minus one, or have a
    constant expression such as n-n. Its truth belongs to the existing independent
    mathematical checks; variable dependence cannot establish truth or relevance.
    """
    errors, allowed, append = [], [], {}
    for index, model in enumerate(content.get('math_models') or []):
        if not isinstance(model, dict) or not model.get('symbols'):
            continue
        by_id = {row.get('math_object_id'): row for row in (model.get('objects') or [])
                 if isinstance(row, dict)}
        for claim_index, claim in enumerate(model.get('claims') or []):
            if not isinstance(claim, dict) or claim.get('kind') != 'abstract':
                continue
            target = claim.get('math_object_id')
            obj = by_id.get(target)
            if not isinstance(obj, dict) or 'expr' not in obj:
                continue
            path = f'/content/math_models/{index}/claims/{claim_index}/math_object_id'
            append_only = {f'/content/math_models/{index}/objects': {
                'object_ids': [target], 'symbol_names': [],
                'symbols_path': f'/content/math_models/{index}/symbols'}}
            try:
                _require_variable_dependence(obj, model['symbols'], path=path, append_only=append_only)
            except LocalCorrectionRequired as exc:
                errors.extend(exc.errors)
                allowed.append(path)
                append.update(deepcopy(exc.append_only or {}))
    return errors, sorted(set(allowed)), append
