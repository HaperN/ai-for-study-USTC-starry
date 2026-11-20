"""教学交互规格：**消费最终教案版本**，并验证参数变化真的改变状态。

方案 §6：

> 交互：原 model 改名 interaction；`source_lesson_revision` 必须等于**最终教案版本**；
> **不再只看 `generated=true`**。

方案 §8：

> 交互规格在**最终教案冻结后**生成，校验公式、参数、锚点、前置知识和响应范围。
> 没有适用交互时可以记录经过规则认可的 `not_applicable`，不强迫所有段落产生空模型；
> **承诺交互的单元必须验证参数变化导致预期状态变化**。模型或图的来源变更使后续页面与交付失效。

两处最容易做错的地方，这里都做成结构上做不到：

1. **`source_lesson_revision` 必须对上**。教案改过一轮（rev 3），而交互还是 rev 2 生成的 ——
   页面会显示"按旧正文写出来的交互"，而它看起来完全正常。所以绑定检查是硬断言，
   并且比的是**冻结后的最终版本**，不是"生成时看到的那个"。
2. **"承诺了交互"必须可验**：`params` 里至少有一个参数，且 `expectations` 声明了
   "参数变化 → 哪个可观测量怎么变"。只有 `generated=true` 不算 ——
   那正是方案点名要废掉的判据。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ...errors import ContractError

#: 交互种类（声明式，由页面渲染器执行；**不由模型产出代码**）
KINDS = ("param-slider", "plot-explore", "geometric-drag", "matrix-apply", "stepper")

STATE_PENDING = "pending"
STATE_GENERATED = "generated"
STATE_VERIFIED = "verified"
STATE_NOT_APPLICABLE = "not_applicable"
STATE_FAILED = "failed"
STATES = (STATE_PENDING, STATE_GENERATED, STATE_VERIFIED, STATE_NOT_APPLICABLE, STATE_FAILED)

#: 只有这两个算正式完成
TERMINAL_OK = (STATE_VERIFIED, STATE_NOT_APPLICABLE)


@dataclass(frozen=True)
class Expectation:
    """一条"参数变化 → 状态变化"的可验证声明。"""
    param: str
    observable: str
    direction: str = "any"          # increase | decrease | any
    tolerance: float = 1e-9

    def __post_init__(self) -> None:
        if not self.param.strip() or not self.observable.strip():
            raise ContractError("expectation 必须同时给出 param 与 observable")
        if self.direction not in ("increase", "decrease", "any"):
            raise ContractError(f"direction 必须是 increase/decrease/any，实际 {self.direction!r}")

    def as_dict(self) -> dict:
        return {"param": self.param, "observable": self.observable,
                "direction": self.direction, "tolerance": self.tolerance}


@dataclass(frozen=True)
class InteractionSpec:
    unit_key: str
    source_lesson_revision: int
    kind: str
    params: tuple[dict, ...] = ()
    expectations: tuple[Expectation, ...] = ()
    prerequisites: tuple[str, ...] = ()
    anchors: tuple[str, ...] = ()
    formula: str = ""
    range_: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.unit_key.strip():
            raise ContractError("InteractionSpec.unit_key 必填")
        if not isinstance(self.source_lesson_revision, int) or self.source_lesson_revision < 1:
            raise ContractError(
                f"source_lesson_revision 必须是正整数（教案版本号），"
                f"实际 {self.source_lesson_revision!r}。方案 §6：它**必须等于最终教案版本**")
        if self.kind not in KINDS:
            raise ContractError(f"交互种类必须是 {KINDS} 之一，实际 {self.kind!r}")
        if not self.params:
            raise ContractError(
                "承诺交互的单元必须声明至少一个参数 —— 方案 §8："
                "没有适用交互时应记 not_applicable，而不是产出一个没有参数的空模型")
        if not self.expectations:
            raise ContractError(
                "必须声明'参数变化 → 状态变化'的期望（expectations）。"
                "方案 §8：承诺交互的单元**必须验证参数变化导致预期状态变化**")
        declared = {str(p.get("name")) for p in self.params}
        for expectation in self.expectations:
            if expectation.param not in declared:
                raise ContractError(
                    f"expectation 引用了未声明的参数 {expectation.param!r}；"
                    f"已声明 {sorted(declared)}")

    def as_dict(self) -> dict:
        return {"unit_key": self.unit_key,
                "source_lesson_revision": self.source_lesson_revision,
                "kind": self.kind, "params": [dict(p) for p in self.params],
                "expectations": [e.as_dict() for e in self.expectations],
                "prerequisites": list(self.prerequisites), "anchors": list(self.anchors),
                "formula": self.formula, "range": dict(self.range_)}


@dataclass(frozen=True)
class InteractionResult:
    unit_key: str
    state: str
    source_lesson_revision: int | None = None
    checks: dict = field(default_factory=dict)
    reason: str = ""
    policy: str = ""
    evidence: str = ""

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ContractError(f"交互状态必须是 {STATES} 之一，实际 {self.state!r}")
        if self.state == STATE_NOT_APPLICABLE and (not self.policy or not self.evidence):
            raise ContractError(
                "not_applicable 必须同时给出 policy 与 evidence（不允许'没跑'冒充不适用）")
        if self.state == STATE_VERIFIED and self.source_lesson_revision is None:
            raise ContractError("verified 必须记录它消费的教案版本")

    @property
    def is_complete(self) -> bool:
        return self.state in TERMINAL_OK

    def as_dict(self) -> dict:
        return {"unit_key": self.unit_key, "state": self.state,
                "source_lesson_revision": self.source_lesson_revision,
                "checks": dict(self.checks), "reason": self.reason, "policy": self.policy,
                "evidence": self.evidence, "is_complete": self.is_complete}


def assert_consumes_final_revision(spec: InteractionSpec, final_lesson_revision: int) -> None:
    """**硬断言**：交互必须消费最终教案版本（§6）。"""
    if spec.source_lesson_revision != final_lesson_revision:
        raise ContractError(
            f"{spec.unit_key}: 交互规格基于教案 rev {spec.source_lesson_revision}，"
            f"而最终教案是 rev {final_lesson_revision}。方案 §6："
            f"`source_lesson_revision` **必须等于最终教案版本** —— "
            f"教案改过而交互没重生成，页面会拿旧正文的交互去渲染，而它看起来完全正常。")


def verify_expected_state_change(spec: InteractionSpec,
                                 sample: Callable[[dict], dict]) -> InteractionResult:
    """真的把参数动一下，看可观测量是否按预期变化。

    `sample(params) -> {observable: value}` 由页面/后端注入（替身可离线跑）。
    这不是"生成时看一眼"，而是**执行一次真实的状态计算**：
    方案 §8 要的是"参数变化**导致预期状态变化**"，只有算一遍才知道。
    """
    base_params = {str(p["name"]): p.get("default", p.get("min", 0.0)) for p in spec.params}
    base = sample(base_params)
    checks: dict = {"ok": True, "detail": []}
    for expectation in spec.expectations:
        param = expectation.param
        if param not in base_params:
            checks["ok"] = False
            checks["detail"].append(f"参数 {param} 没有默认值，无法采样")
            continue
        bumped = dict(base_params)
        spec_min = next((p.get("min") for p in spec.params if str(p.get("name")) == param), None)
        spec_max = next((p.get("max") for p in spec.params if str(p.get("name")) == param), None)
        delta = ((float(spec_max) - float(spec_min)) / 10.0
                 if spec_min is not None and spec_max is not None else 0.1)
        bumped[param] = float(base_params[param]) + (delta or 0.1)
        after = sample(bumped)
        before_value = base.get(expectation.observable)
        after_value = after.get(expectation.observable)
        if before_value is None or after_value is None:
            checks["ok"] = False
            checks["detail"].append(
                f"可观测量 {expectation.observable!r} 在采样里没有出现 —— "
                f"没有观测量的'验证'等于没验")
            continue
        diff = float(after_value) - float(before_value)
        ok = (expectation.direction == "any" and abs(diff) > expectation.tolerance) or \
             (expectation.direction == "increase" and diff > expectation.tolerance) or \
             (expectation.direction == "decrease" and diff < -expectation.tolerance)
        checks["detail"].append(
            {"param": param, "observable": expectation.observable,
             "before": before_value, "after": after_value, "direction": expectation.direction,
             "ok": ok})
        if not ok:
            checks["ok"] = False
    if not checks["ok"]:
        return InteractionResult(unit_key=spec.unit_key, state=STATE_FAILED,
                                 source_lesson_revision=spec.source_lesson_revision,
                                 checks=checks,
                                 reason="参数变化未导致预期状态变化 —— 承诺的交互其实是死的")
    return InteractionResult(unit_key=spec.unit_key, state=STATE_VERIFIED,
                             source_lesson_revision=spec.source_lesson_revision,
                             checks=checks)


def not_applicable(unit_key: str, *, policy: str, evidence: str) -> InteractionResult:
    """经规则认可的"没有适用交互"。**不强迫所有段落产生空模型**（§8）。"""
    return InteractionResult(unit_key=unit_key, state=STATE_NOT_APPLICABLE,
                             policy=policy, evidence=evidence)


def assert_ledger_releaseable(results: dict[str, InteractionResult],
                              final_revisions: dict[str, int]) -> None:
    """发布前总检查：状态到位 **且**版本对上。"""
    problems: list[str] = []
    for unit_key, result in results.items():
        if not result.is_complete:
            problems.append(f"{unit_key}: 状态 {result.state}（{result.reason or result.policy}）")
            continue
        if result.state == STATE_VERIFIED:
            want = final_revisions.get(unit_key)
            if want is None:
                problems.append(f"{unit_key}: 缺最终教案版本，无法核对")
            elif result.source_lesson_revision != want:
                problems.append(
                    f"{unit_key}: 交互基于 rev {result.source_lesson_revision}，"
                    f"最终教案是 rev {want}")
    if problems:
        raise ContractError(
            f"交互层不能发布：{len(problems)} 处不合格 → {problems[:5]}。"
            f"方案 §6：不再只看 generated=true；§8：来源变更使页面与交付失效。")


__all__ = ["KINDS", "STATE_PENDING", "STATE_GENERATED", "STATE_VERIFIED",
           "STATE_NOT_APPLICABLE", "STATE_FAILED", "STATES", "TERMINAL_OK", "Expectation",
           "InteractionSpec", "InteractionResult", "assert_consumes_final_revision",
           "verify_expected_state_change", "not_applicable", "assert_ledger_releaseable"]


def validate_assignment_coverage(requirements, interaction_results, content_hash):
    """Reconcile explicit target delegation; never infer assignments from a legacy lesson."""
    from ...core.digest import digest_of
    problems=[]
    if requirements.get('content_hash')!=content_hash or interaction_results.get('content_hash')!=content_hash:
        problems.append('交互 content_hash 绑定过期')
    if interaction_results.get('requirements_hash')!=digest_of(requirements): problems.append('交互 requirements_hash 绑定过期')
    expected={d['target_id']:d for d in requirements.get('decisions',[]) if d.get('handled_by')=='interaction'}
    rows=interaction_results.get('results',[])
    models=interaction_results.get('models',[])
    if not isinstance(models,list) or any(not isinstance(model,dict) or not model.get('id') for model in models):
        problems.append('交互 models 缺合法模型身份'); models=[]
    model_ids=[model['id'] for model in models]
    if len(model_ids)!=len(set(model_ids)): problems.append('交互模型 ID 重复')
    model_map={model['id']:model for model in models}; consumed=[]
    ids=[r.get('target_id') for r in rows]
    if len(ids)!=len(set(ids)) or set(ids)!=set(expected): problems.append('交互委派目标遗漏、重复或新增未登记目标')
    for row in rows:
        d=expected.get(row.get('target_id'))
        if not d: continue
        if row.get('state')=='verified':
            if not row.get('spec') or (row.get('checks') or {}).get('ok') is not True: problems.append('交互未保存实际规格/状态验证：'+row['target_id'])
            model_id=row.get('model_id'); model=model_map.get(model_id)
            if not model_id or model is None or row.get('spec')!=model:
                problems.append('交互 verified 缺对应 models 或实际规格不同：'+row['target_id'])
            else:
                consumed.append(model_id)
                from ..media.interaction_adapter import verify_template_response
                if model.get('binds_to')!=row['target_id'] or not verify_template_response(model)['ok']:
                    problems.append('交互模型未绑定目标或实际状态变化失败：'+row['target_id'])
        elif row.get('state')=='not_applicable':
            if d.get('requirement')=='required': problems.append('必要交互不得撤销；回 media_plan 重新核验：'+row['target_id'])
            if not row.get('policy') or not row.get('evidence'): problems.append('交互不适用缺依据：'+row['target_id'])
        else: problems.append('交互未完成：'+row['target_id'])
    if len(consumed)!=len(set(consumed)) or set(consumed)!=set(model_ids):
        problems.append('交互 results/models 必须一一对应，不能遗漏或登记未挂载模型')
    from ..media.check_report import check_report
    return check_report(inputs={'requirements':requirements,'results':interaction_results},rules=('INTERACTION_COVERAGE',),issues=[{'code':'INTERACTION_COVERAGE','rule_id':'INTERACTION_COVERAGE','message':p} for p in problems],expected=list(expected),checked=ids,target_ids=list(expected),content_hash=content_hash,requirements_hash=digest_of(requirements))


def interaction_capabilities(content, *, transport_available, reader_available=True, context=None):
    """Conservative per-goal templates, using only that goal's reader evidence."""
    from ..media.media_requirements import candidates
    from ...pipeline.stage_io import _legacy
    generator = _legacy('model_generate')
    targets = {}
    for target in candidates(content):
        pointers = {row['pointer'] for row in target['reader_evidence']}
        explainables = [row for i,row in enumerate(content.get('explainables', []))
                       if any(pointer.startswith('/explainables/'+str(i)+'/') for pointer in pointers)]
        text = '\n'.join(row['text'] for row in target['reader_evidence'])
        # These references describe actual registered owners, not invented body anchors.
        refs = ''.join('[[EXPLAIN:'+row['id']+']]' for row in explainables)
        local = {'body_markdown':text+refs, 'explainables':explainables, 'concepts':[]}
        plan = generator.plan_models(local, diagrams=[], context=context or {})
        templates = plan['templates'] if transport_available and reader_available else []
        targets[target['target_id']] = {'templates':templates, 'reader_pointers':sorted(pointers),
            'bindings':{name:[target['target_id']] for name in templates},
            'reason':plan['evidence']['decision'] if templates else '目标证据无适用模板或运输/阅读器能力不可用'}
    return {'schema_version':'InteractionCapabilitiesV1', 'available':any(row['templates'] for row in targets.values()),
            'transport_available':bool(transport_available), 'reader_protocol':'ReaderMountV1' if reader_available else '',
            'targets':targets}


def source_interaction_capabilities(source, *, transport_available, reader_available=True):
    """Pre-author catalog keyed by frozen source obligations, never future goal IDs."""
    from ...pipeline.stage_io import _legacy
    generator = _legacy('model_generate')
    targets = {}
    for obligation in source.get('source_obligations', []):
        plan = generator.plan_models({'body_markdown':obligation['source_excerpt']}, diagrams=[], context={})
        templates = plan['templates'] if transport_available and reader_available else []
        targets[obligation['id']] = {'templates':templates, 'source_excerpt':obligation['source_excerpt']}
    return {'schema_version':'InteractionCapabilitiesV1', 'scope':'source_obligation',
            'available':any(row['templates'] for row in targets.values()),
            'transport_available':bool(transport_available), 'reader_protocol':'ReaderMountV1' if reader_available else '',
            'targets':targets}
