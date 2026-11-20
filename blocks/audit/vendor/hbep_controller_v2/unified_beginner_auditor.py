from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath
import re
from tempfile import TemporaryDirectory

from .numeric_reasoning import (
    _contains_quantitative_claim,
    _numeric_equality_status,
    _numeric_rule_fact_status,
)
from .production_contract import (
    ProductionContract,
    ReasoningStep,
    load_production_contract,
    parse_content_spec_bytes,
    render_reasoning_step,
    render_numeric_check,
    render_rule_declaration,
    verify_rule_step,
)
from .production_review import (
    AuditorWorker,
    ReviewIssue,
    ReviewTask,
    completed_review_report,
)
from .semantic_audit import (
    build_semantic_graph,
    compile_shadow_world,
    find_blocked_frontier,
    find_minimal_bridge_family,
    run_restricted_novice,
    run_shadow_novice,
    shadow_is_isomorphic,
)


UNIFIED_AUDITOR_ID = "unified-beginner-shadow-program-v2"
_LEARNER_SELF_CLAIM = re.compile(
    r"(?:初学者|学生|读者)(?:已经|就|都|必然|一定|能够|可以|会)*"
    r"(?:理解|掌握|看懂|学会|知道|明白)"
)
_COVERAGE_ITEMS = (
    "visible-reasoning-v2",
    "shadow-world-v2",
    "executable-minimal-bridges-v2",
    "mathematical-replay-v2",
    "diagram-observation-v1",
    "learner-self-claim-v1",
)


@dataclass(frozen=True, slots=True)
class DiagramObservation:
    observer_id: str
    image_sha256: str
    ocr_labels: tuple[str, ...]
    relations: tuple[str, ...]
    complete: bool

    def __post_init__(self) -> None:
        if not isinstance(self.observer_id, str) or not self.observer_id.strip():
            raise ValueError("diagram observer_id must be non-empty")
        if (
            not isinstance(self.image_sha256, str)
            or len(self.image_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.image_sha256)
        ):
            raise ValueError("diagram image_sha256 must be a SHA-256 value")
        for values in (self.ocr_labels, self.relations):
            if (
                not isinstance(values, tuple)
                or len(values) != len(set(values))
                or any(not isinstance(item, str) or not item for item in values)
            ):
                raise ValueError("diagram observations must be unique non-empty strings")
        if self.complete is not True:
            raise ValueError("diagram observation must be complete")


DiagramObserver = Callable[[str, bytes], DiagramObservation]


@dataclass(frozen=True, slots=True)
class UnifiedBeginnerAuditorConfig:
    artifact_manifest: str
    analysis_contract: str
    content_spec_bytes: bytes
    work_root: Path
    diagram_observer: DiagramObserver | None = None

    def __post_init__(self) -> None:
        for value in (self.artifact_manifest, self.analysis_contract):
            path = PurePosixPath(value)
            if (
                not value
                or "\\" in value
                or ":" in value
                or path.is_absolute()
                or ".." in path.parts
                or str(path) != value
            ):
                raise ValueError("audit contract paths must stay inside the package")
        if not isinstance(self.content_spec_bytes, bytes) or not self.content_spec_bytes:
            raise ValueError("content_spec_bytes must be non-empty bytes")
        parse_content_spec_bytes(self.content_spec_bytes)
        if self.diagram_observer is not None and not callable(self.diagram_observer):
            raise TypeError("diagram_observer must be callable")
        object.__setattr__(self, "work_root", Path(self.work_root).resolve())


def build_unified_beginner_auditor(
    config: UnifiedBeginnerAuditorConfig,
) -> AuditorWorker:
    if not isinstance(config, UnifiedBeginnerAuditorConfig):
        raise TypeError("config must be UnifiedBeginnerAuditorConfig")
    spec = parse_content_spec_bytes(config.content_spec_bytes)

    def review(task: ReviewTask):
        config.work_root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="unified-audit-", dir=config.work_root) as temporary:
            root = task.package.materialize(Path(temporary) / "analysis")
            contract = load_production_contract(
                project=root,
                canonical_sources=spec.reviewed_artifacts,
                content_spec=config.content_spec_bytes,
                analysis_contract=root / config.analysis_contract,
                artifact_manifest=root / config.artifact_manifest,
            )
            issues = _audit_contract(contract, root, config.diagram_observer)
        return completed_review_report(task, issues, coverage_items=_COVERAGE_ITEMS)

    return AuditorWorker(auditor_id=UNIFIED_AUDITOR_ID, audit=review)


def _audit_contract(
    contract: ProductionContract,
    project_root: Path,
    diagram_observer: DiagramObserver | None,
) -> tuple[ReviewIssue, ...]:
    issues: list[ReviewIssue] = []
    issues.extend(_check_visible_reasoning(contract))
    issues.extend(_check_shadow_world(contract))
    issues.extend(_check_executable_shadow_and_minimal_bridges(contract))
    issues.extend(_check_mathematical_replay(contract))
    issues.extend(_check_diagram_observations(contract, project_root, diagram_observer))
    issues.extend(_check_learner_self_claims(contract))
    return _deduplicate(tuple(issues))


def _check_diagram_observations(
    contract: ProductionContract,
    project_root: Path,
    observer: DiagramObserver | None,
) -> tuple[ReviewIssue, ...]:
    evidence_items = contract.spec.diagram_evidence
    if not evidence_items:
        return ()
    anchor_unit_id = contract.units[0].unit_id
    if observer is None:
        return (
            _unit_issue(
                contract,
                anchor_unit_id,
                "DIAGRAM_OBSERVER_UNAVAILABLE",
                "图像关系没有经过独立图像观察器复核",
                "配置只接收冻结图像字节的独立观察器，再核对文字标签和结构关系",
            ),
        )

    issues: list[ReviewIssue] = []
    for evidence in evidence_items:
        image_bytes = (project_root / evidence.image_path).read_bytes()
        try:
            observation = observer(evidence.image_path, image_bytes)
            if not isinstance(observation, DiagramObservation):
                raise TypeError("observer returned the wrong value type")
        except Exception as exc:
            issue = _unit_issue(
                contract,
                anchor_unit_id,
                "DIAGRAM_OBSERVATION_FAILED",
                "图像观察器没有产生完整可核对的结果",
                "修复图像观察器后重新审核这一版解析",
            )
            issues.append(
                _replace_evidence(
                    issue,
                    evidence.evidence_id,
                    f"OBSERVER_ERROR:{type(exc).__name__}",
                )
            )
            continue
        actual_sha256 = hashlib.sha256(image_bytes).hexdigest()
        if observation.image_sha256 != actual_sha256:
            issues.append(
                _replace_evidence(
                    _unit_issue(
                        contract,
                        anchor_unit_id,
                        "DIAGRAM_OBSERVATION_IMAGE_MISMATCH",
                        "图像观察结果不属于当前冻结图像",
                        "用当前图像字节重新运行独立观察器",
                    ),
                    evidence.evidence_id,
                    observation.observer_id,
                )
            )
            continue
        if set(observation.ocr_labels) != set(evidence.ocr_labels):
            issues.append(
                _replace_evidence(
                    _unit_issue(
                        contract,
                        anchor_unit_id,
                        "DIAGRAM_LABEL_MISMATCH",
                        "图中实际识别到的文字标签与解析登记不一致",
                        "按图像观察结果修正标签或解析中对图的引用",
                    ),
                    evidence.evidence_id,
                    observation.observer_id,
                )
            )
        if set(observation.relations) != set(evidence.relations):
            issues.append(
                _replace_evidence(
                    _unit_issue(
                        contract,
                        anchor_unit_id,
                        "DIAGRAM_RELATION_MISMATCH",
                        "图中实际结构关系与解析所依赖的关系不一致",
                        "修正解析的图像关系，或更换为能真实支持该关系的图",
                    ),
                    evidence.evidence_id,
                    observation.observer_id,
                )
            )
    return tuple(issues)


def _check_learner_self_claims(
    contract: ProductionContract,
) -> tuple[ReviewIssue, ...]:
    return tuple(
        _step_issue(
            step,
            "LEARNER_CAPABILITY_SELF_ASSERTED",
            "解析把初学者是否理解当成了自己可以证明的结论",
            "删除对学习者能力的自证，改为显示具体定义、关系和中间步骤",
        )
        for step in contract.steps
        if _LEARNER_SELF_CLAIM.search(step.conclusion)
    )


def _check_executable_shadow_and_minimal_bridges(
    contract: ProductionContract,
) -> tuple[ReviewIssue, ...]:
    graph = build_semantic_graph(contract)
    shadow = compile_shadow_world(graph)
    real_trace = run_restricted_novice(graph)
    shadow_trace = run_shadow_novice(shadow)
    real_blocked_shape = tuple(
        target in set(real_trace.blocked_targets) for target in graph.target_facts
    )
    shadow_blocked_shape = tuple(
        target in set(shadow_trace.blocked_target_tokens)
        for target in shadow.target_tokens
    )
    if (
        not shadow_is_isomorphic(graph, shadow)
        or real_blocked_shape != shadow_blocked_shape
    ):
        first = contract.units[0]
        return (
            _unit_issue(
                contract,
                first.unit_id,
                "SHADOW_WORLD_NOT_ISOMORPHIC",
                "陌生世界没有保留原关系结构和受限推演结果",
                "保持节点、关系、推理步骤和可见规则一一对应后重新编译影子世界",
            ),
        )

    obligation_kind = {
        item.target_fact: item.kind for item in contract.spec.learning_obligations
    }
    target_unit = {
        conclusion: unit.unit_id
        for unit in contract.spec.required_units
        for conclusion in unit.required_conclusions
    }
    step_by_conclusion = {step.conclusion: step for step in contract.steps}
    issues: list[ReviewIssue] = []
    for target in real_trace.blocked_targets:
        family = find_minimal_bridge_family(graph, target)
        frontier = find_blocked_frontier(graph, target)
        kind = obligation_kind.get(target, "causal_bridge")
        code_suffix = kind.upper()
        frontier_evidence = (
            *(f"BLOCKED_STEP:{item}" for item in frontier.blocked_step_ids),
            *(
                f"UNSHOWN_RULE_USE:{unit_id}:{rule_id}"
                for unit_id, rule_id in frontier.unshown_rule_uses
            ),
            *(f"MISSING_FACT:{item}" for item in frontier.missing_facts),
            *(
                f"DIAGRAM_DEPENDENCY:{item}"
                for item in frontier.diagram_dependencies
            ),
        )
        if family is None:
            problem = "受限初学者从正文已经出现的条件和关系无法走到该结论"
            required_change = "补入有来源的关系或中间结论，使整条关系链能够逐步重放"
            evidence = (
                target,
                "NO_SUFFICIENT_BRIDGE_IN_FROZEN_RULE_BASE",
                *frontier_evidence,
            )
        elif not family.search_complete:
            problem = "候选关系过多，程序无法证明返回结果是全局最短桥梁"
            required_change = "缩小当前单元的候选关系范围，或显式给出可核验的中间目标"
            evidence = (
                target,
                f"BRIDGE_SEARCH_INCOMPLETE:{family.minimum_cardinality}",
                *frontier_evidence,
            )
        else:
            alternatives = tuple(
                tuple(item.declaration for item in alternative)
                for alternative in family.alternatives
            )
            displayed = alternatives[:8]
            rendered = "；或者：".join(
                " + ".join(alternative) for alternative in displayed
            )
            if len(alternatives) > len(displayed):
                rendered += f"；另有{len(alternatives) - len(displayed)}条同长路径"
            problem = "正文没有显式给出受限初学者继续所需的最短关系桥梁"
            required_change = "在对应推理之前选择一条完整最短路径并逐字显示：" + rendered
            evidence = (
                target,
                f"MINIMUM_CARDINALITY:{family.minimum_cardinality}",
                f"EQUAL_MINIMUM_ALTERNATIVES:{len(alternatives)}",
                *(
                    f"ALTERNATIVE_{index}:" + " || ".join(alternative)
                    for index, alternative in enumerate(alternatives[:32], 1)
                ),
                *frontier_evidence,
            )
        step = step_by_conclusion.get(target)
        if step is not None:
            issue = _step_issue(
                step,
                f"MINIMAL_BRIDGE_MISSING_{code_suffix}",
                problem,
                required_change,
            )
            issues.append(
                ReviewIssue(
                    code=issue.code,
                    location=issue.location,
                    exact_quote=issue.exact_quote,
                    problem=issue.problem,
                    required_change=issue.required_change,
                    severity=issue.severity,
                    producers=issue.producers,
                    evidence=evidence,
                )
            )
            continue
        unit_id = target_unit.get(target)
        if unit_id is not None:
            issues.append(
                _unit_issue(
                    contract,
                    unit_id,
                    f"MINIMAL_BRIDGE_MISSING_{code_suffix}",
                    problem,
                    required_change,
                )
            )
    return tuple(issues)


def _check_visible_reasoning(contract: ProductionContract) -> tuple[ReviewIssue, ...]:
    issues: list[ReviewIssue] = []
    steps_by_unit: dict[str, list[ReasoningStep]] = {}
    for step in contract.steps:
        steps_by_unit.setdefault(step.unit_id, []).append(step)
        try:
            visible = render_reasoning_step(step)
        except ValueError:
            issues.append(_step_issue(step, "VISIBLE_REASONING_INVALID", "这一步没有可显示的推理结构", "把前提、来源和结论写成可逐项检查的步骤"))
            continue
        if visible != step.exact_quote:
            issues.append(_step_issue(step, "VISIBLE_REASONING_MISMATCH", "正文没有完整显示登记的推理关系", "让正文逐字显示前提来源和结论"))
        if step.verification_kind == "rule" and step.rule_id is not None:
            rule = contract.spec.rule(step.rule_id)
            unit = contract.unit(step.unit_id)
            if rule is None or unit is None:
                continue
            source = contract.sources[step.artifact_path]
            visible_before_step = source[unit.start:step.start]
            declaration = render_rule_declaration(rule).split("\n", 1)[0]
            if declaration not in visible_before_step:
                issues.append(
                    _step_issue(
                        step,
                        "INFERENCE_RULE_NOT_VISIBLE",
                        "这一步引用了推理规则，但正文在使用前没有显示规则本身",
                        "在使用该规则之前显式给出它的前提、结论和适用关系",
                    )
                )
            for index, check in enumerate(rule.numeric_checks, 1):
                if render_numeric_check(rule, index, check) not in visible_before_step:
                    issues.append(
                        _step_issue(
                            step,
                            "NUMERIC_DERIVATION_NOT_VISIBLE",
                            "规则中的数值结论没有显示可逐项复算的中间计算",
                            "在规则使用前显示每项计算式及其绑定结果",
                        )
                    )

    for required in contract.spec.required_units:
        unit = contract.unit(required.unit_id)
        if unit is None:
            continue
        conclusions = {step.conclusion for step in steps_by_unit.get(required.unit_id, ())}
        for conclusion in required.required_conclusions:
            if conclusion not in conclusions:
                issues.append(_unit_issue(contract, unit.unit_id, "REQUIRED_CONCLUSION_MISSING", "本单元没有推出要求得到的结论", f"补出通向结论 {conclusion} 的完整关系链"))
    return tuple(issues)


def _check_shadow_world(contract: ProductionContract) -> tuple[ReviewIssue, ...]:
    issues: list[ReviewIssue] = []
    step_by_id = {step.step_id: step for step in contract.steps}
    allowed_units = _dependency_closures(contract)
    used_dependencies: dict[str, set[str]] = {unit.unit_id: set() for unit in contract.units}
    for step in contract.steps:
        for premise in step.premises:
            if premise.source_kind == "initial_fact":
                continue
            producer = step_by_id.get(premise.source_id)
            if producer is None:
                issues.append(_step_issue(step, "IMPLICIT_SOURCE_MISSING", "这一步使用了没有在前文建立的内容", "先建立该前提，再在当前步骤中明确引用"))
                continue
            if producer.unit_id != step.unit_id:
                used_dependencies[step.unit_id].add(producer.unit_id)
                if producer.unit_id not in allowed_units.get(step.unit_id, set()):
                    issues.append(_step_issue(step, "UNDECLARED_CROSS_UNIT_LINK", "这一步依赖了没有声明的前置单元", "声明并显示这条跨单元联系，或在本单元内补足前提"))

    for unit in contract.units:
        missing = set(unit.depends_on) - used_dependencies.get(unit.unit_id, set())
        if missing:
            issues.append(_unit_issue(contract, unit.unit_id, "DECLARED_LINK_NOT_SHOWN", "声明的前后联系没有真正出现在推理步骤中", "在正文步骤中明确引用前置单元建立的事实"))

    for required in contract.spec.required_units:
        targets = [
            step
            for step in contract.steps
            if step.unit_id == required.unit_id
            and step.conclusion in set(required.required_conclusions)
        ]
        for target in targets:
            if not _has_complete_visible_chain(target, step_by_id, set()):
                issues.append(_step_issue(target, "IMPLICIT_CHAIN_BROKEN", "这个结论的前提链不能一直追溯到明确的已知条件", "补齐所有中间关系并逐步写明来源"))
    return tuple(issues)


def _check_mathematical_replay(contract: ProductionContract) -> tuple[ReviewIssue, ...]:
    issues: list[ReviewIssue] = []
    initial = set(contract.spec.initial_facts)
    for step in contract.steps:
        if step.verification_kind == "given":
            if step.conclusion not in initial:
                issues.append(_step_issue(step, "UNDECLARED_GIVEN", "这条已知条件不在冻结的输入中", "把它列入有来源的输入，或给出从现有前提推出它的步骤"))
            continue
        if step.verification_kind == "numeric_equality":
            status = _numeric_equality_status(step.conclusion)
            if status != "TRUE":
                code = {
                    "FALSE": "NUMERIC_EQUALITY_FALSE",
                    "UNDEFINED": "NUMERIC_EXPRESSION_UNDEFINED",
                }.get(status, "NUMERIC_EQUALITY_UNSUPPORTED")
                issues.append(_step_issue(step, code, "数值等式没有通过精确重算", "重新计算并改正这个等式"))
            continue
        if step.verification_kind == "rule":
            rule = contract.spec.rule(step.rule_id or "")
            if rule is None:
                issues.append(_step_issue(step, "RULE_UNAVAILABLE", "这一步引用了不存在的规则", "使用冻结规则或补充有来源的规则"))
                continue
            valid, _ = verify_rule_step(step, rule)
            if not valid:
                issues.append(_step_issue(step, "RULE_REPLAY_FAILED", "按登记规则无法从这些前提得到当前结论", "修正前提、规则或结论，使推导可以逐项重放"))
                continue
            numeric_status = _numeric_rule_fact_status(step.conclusion)
            if numeric_status in {"FALSE", "UNDEFINED", "UNSUPPORTED"}:
                code = "NUMERIC_EQUALITY_FALSE" if numeric_status == "FALSE" else "RULE_NUMERIC_CONCLUSION_INVALID"
                issues.append(_step_issue(step, code, "规则推导出的数值结论没有通过独立重算", "改正数值结论及其后续关系"))
                continue
            if _contains_quantitative_claim(step.conclusion) and not rule.numeric_checks:
                issues.append(
                    _step_issue(
                        step,
                        "RULE_NUMERIC_CHECK_MISSING",
                        "这条定量结论没有可执行的独立重算依据",
                        "为该规则登记精确等式和结果与单位在结论中的绑定文本",
                    )
                )
                continue
            for check in rule.numeric_checks:
                check_status = _numeric_equality_status(check.expression)
                if check_status != "TRUE":
                    code = (
                        "RULE_NUMERIC_CHECK_FALSE"
                        if check_status == "FALSE"
                        else "RULE_NUMERIC_CHECK_UNEXECUTABLE"
                    )
                    issues.append(
                        _step_issue(
                            step,
                            code,
                            "定量结论登记的独立重算等式没有通过",
                            "改正计算式、数值结果和相关后续推导",
                        )
                    )
                if check.result_binding not in step.conclusion:
                    issues.append(
                        _step_issue(
                            step,
                            "RULE_NUMERIC_RESULT_BINDING_MISSING",
                            "重算结果没有与结论中的数值和单位绑定",
                            "让规则登记的结果文本与结论中的数值和单位逐字一致",
                        )
                    )
    return tuple(issues)


def _dependency_closures(contract: ProductionContract) -> dict[str, set[str]]:
    direct = {unit.unit_id: set(unit.depends_on) for unit in contract.units}
    result: dict[str, set[str]] = {}
    for unit_id in direct:
        closure: set[str] = set()
        pending = list(direct[unit_id])
        while pending:
            current = pending.pop()
            if current in closure:
                continue
            closure.add(current)
            pending.extend(direct.get(current, ()))
        result[unit_id] = closure
    return result


def _has_complete_visible_chain(
    step: ReasoningStep,
    step_by_id: dict[str, ReasoningStep],
    visiting: set[str],
) -> bool:
    if step.step_id in visiting:
        return False
    next_visiting = {*visiting, step.step_id}
    for premise in step.premises:
        if premise.source_kind == "initial_fact":
            continue
        producer = step_by_id.get(premise.source_id)
        if producer is None or producer.conclusion != premise.fact:
            return False
        if not _has_complete_visible_chain(producer, step_by_id, next_visiting):
            return False
    return True


def _step_issue(step: ReasoningStep, code: str, problem: str, required_change: str) -> ReviewIssue:
    return ReviewIssue(
        code=code,
        location=f"{step.artifact_path}:{step.start}-{step.end}",
        exact_quote=step.exact_quote,
        problem=problem,
        required_change=required_change,
        severity="P1",
        producers=(UNIFIED_AUDITOR_ID,),
        evidence=(step.step_id,),
    )


def _unit_issue(contract: ProductionContract, unit_id: str, code: str, problem: str, required_change: str) -> ReviewIssue:
    unit = contract.unit(unit_id)
    if unit is None:
        raise ValueError("unit is unavailable")
    quote = unit.exact_quote[: min(len(unit.exact_quote), 256)]
    return ReviewIssue(
        code=code,
        location=f"{unit.artifact_path}:{unit.start}-{unit.start + len(quote)}",
        exact_quote=quote,
        problem=problem,
        required_change=required_change,
        severity="P1",
        producers=(UNIFIED_AUDITOR_ID,),
        evidence=(unit_id,),
    )


def _replace_evidence(issue: ReviewIssue, *evidence: str) -> ReviewIssue:
    return ReviewIssue(
        code=issue.code,
        location=issue.location,
        exact_quote=issue.exact_quote,
        problem=issue.problem,
        required_change=issue.required_change,
        severity=issue.severity,
        producers=issue.producers,
        evidence=tuple(evidence),
    )


def _deduplicate(issues: tuple[ReviewIssue, ...]) -> tuple[ReviewIssue, ...]:
    result: list[ReviewIssue] = []
    seen: set[tuple[str, str, str]] = set()
    for issue in issues:
        key = (issue.code, issue.location, issue.exact_quote)
        if key not in seen:
            seen.add(key)
            result.append(issue)
    return tuple(result)


__all__ = [
    "DiagramObservation",
    "DiagramObserver",
    "UNIFIED_AUDITOR_ID",
    "UnifiedBeginnerAuditorConfig",
    "build_unified_beginner_auditor",
]
