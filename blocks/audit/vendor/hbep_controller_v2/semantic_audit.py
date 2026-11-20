from __future__ import annotations

from dataclasses import dataclass
import hashlib
from itertools import combinations
import json
from typing import Iterable
from .numeric_reasoning import _numeric_equality_status

from .production_contract import (
    ProductionContract,
    render_rule_declaration,
    bind_rule_pattern,
)


@dataclass(frozen=True, slots=True)
class SemanticRule:
    rule_id: str
    premises: tuple[str, ...]
    conclusion: str
    relation_kind: str = "causal_bridge"

    def declaration(self) -> str:
        premises = "；".join(self.premises)
        return (
            f"【桥梁:{self.rule_id}|{self.relation_kind}】"
            f"若 {premises}，则 {self.conclusion}"
        )


@dataclass(frozen=True, slots=True)
class SemanticStep:
    step_id: str
    unit_id: str
    premises: tuple[str, ...]
    conclusion: str
    rule_id: str | None
    verification_kind: str = "rule"


@dataclass(frozen=True, slots=True)
class SemanticGraph:
    initial_facts: tuple[str, ...]
    visible_initial_facts: tuple[str, ...]
    rules: tuple[SemanticRule, ...]
    steps: tuple[SemanticStep, ...]
    target_facts: tuple[str, ...]
    visible_rule_uses: tuple[tuple[str, str], ...]
    diagram_facts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NoviceTrace:
    reachable_facts: tuple[str, ...]
    applied_step_ids: tuple[str, ...]
    blocked_targets: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BridgeRequirement:
    unit_id: str
    rule_id: str
    relation_kind: str
    declaration: str


@dataclass(frozen=True, slots=True)
class MinimalBridge:
    target_fact: str
    requirements: tuple[BridgeRequirement, ...]
    sufficient: bool
    every_requirement_necessary: bool


@dataclass(frozen=True, slots=True)
class BridgeFamily:
    target_fact: str
    alternatives: tuple[tuple[BridgeRequirement, ...], ...]
    minimum_cardinality: int
    search_complete: bool


@dataclass(frozen=True, slots=True)
class BlockedFrontier:
    target_fact: str
    blocked_step_ids: tuple[str, ...]
    unshown_rule_uses: tuple[tuple[str, str], ...]
    missing_facts: tuple[str, ...]
    diagram_dependencies: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OpaqueRule:
    rule_token: str
    premise_tokens: tuple[str, ...]
    conclusion_token: str
    kind_token: str


@dataclass(frozen=True, slots=True)
class OpaqueStep:
    step_token: str
    unit_token: str
    premise_tokens: tuple[str, ...]
    conclusion_token: str
    rule_token: str | None
    verification_token: str


@dataclass(frozen=True, slots=True)
class ShadowWorld:
    initial_fact_tokens: tuple[str, ...]
    visible_initial_fact_tokens: tuple[str, ...]
    rules: tuple[OpaqueRule, ...]
    steps: tuple[OpaqueStep, ...]
    target_tokens: tuple[str, ...]
    visible_rule_uses: tuple[tuple[str, str], ...]
    diagram_fact_tokens: tuple[str, ...]
    source_topology_sha256: str
    shadow_topology_sha256: str
    verified_step_tokens: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "verified_step_tokens": list(self.verified_step_tokens),
            "initial_fact_tokens": list(self.initial_fact_tokens),
            "visible_initial_fact_tokens": list(self.visible_initial_fact_tokens),
            "rules": [
                {
                    "rule_token": item.rule_token,
                    "premise_tokens": list(item.premise_tokens),
                    "conclusion_token": item.conclusion_token,
                    "kind_token": item.kind_token,
                }
                for item in self.rules
            ],
            "steps": [
                {
                    "step_token": item.step_token,
                    "unit_token": item.unit_token,
                    "premise_tokens": list(item.premise_tokens),
                    "conclusion_token": item.conclusion_token,
                    "rule_token": item.rule_token,
                    "verification_token": item.verification_token,
                }
                for item in self.steps
            ],
            "target_tokens": list(self.target_tokens),
            "visible_rule_uses": [list(item) for item in self.visible_rule_uses],
            "diagram_fact_tokens": list(self.diagram_fact_tokens),
            "source_topology_sha256": self.source_topology_sha256,
            "shadow_topology_sha256": self.shadow_topology_sha256,
        }


@dataclass(frozen=True, slots=True)
class ShadowNoviceTrace:
    reachable_fact_tokens: tuple[str, ...]
    applied_step_tokens: tuple[str, ...]
    blocked_target_tokens: tuple[str, ...]


_MAX_EXACT_BRIDGE_CANDIDATES = 16


def build_semantic_graph(contract: ProductionContract) -> SemanticGraph:
    rules = tuple(
        SemanticRule(
            item.rule_id,
            item.premises,
            item.conclusion,
            item.relation_kind,
        )
        for item in contract.spec.trusted_rules
    )
    rules_by_id = {item.rule_id: item for item in contract.spec.trusted_rules}
    visible_initial = {
        premise.fact
        for step in contract.steps
        for premise in step.premises
        if premise.source_kind == "initial_fact"
    }
    visible_rule_uses: set[tuple[str, str]] = set()
    steps: list[SemanticStep] = []
    for step in contract.steps:
        if step.verification_kind in {"given", "numeric_equality"}:
            visible_initial.add(step.conclusion)
        if step.rule_id is not None:
            rule = rules_by_id.get(step.rule_id)
            unit = contract.unit(step.unit_id)
            if rule is not None and unit is not None:
                declaration = render_rule_declaration(rule)
                source = contract.sources[step.artifact_path]
                declaration_start = source.find(
                    declaration, unit.start, min(step.start, unit.end)
                )
                if declaration_start >= 0:
                    visible_rule_uses.add((step.unit_id, step.rule_id))
        steps.append(
            SemanticStep(
                step.step_id,
                step.unit_id,
                tuple(item.fact for item in step.premises),
                step.conclusion,
                step.rule_id,
                step.verification_kind,
            )
        )
    targets = (
        tuple(item.target_fact for item in contract.spec.learning_obligations)
        if contract.spec.learning_obligations
        else tuple(
            conclusion
            for unit in contract.spec.required_units
            for conclusion in unit.required_conclusions
        )
    )
    diagram_facts = tuple(
        relation
        for evidence in contract.spec.diagram_evidence
        if evidence.extraction_kind == "structured_relations"
        for relation in evidence.relations
    )
    return SemanticGraph(
        initial_facts=contract.spec.initial_facts,
        visible_initial_facts=tuple(
            fact for fact in contract.spec.initial_facts if fact in visible_initial
        ),
        rules=rules,
        steps=tuple(steps),
        target_facts=tuple(dict.fromkeys(targets)),
        visible_rule_uses=tuple(sorted(visible_rule_uses)),
        diagram_facts=diagram_facts,
    )


def _verified_step(graph: SemanticGraph, step: SemanticStep) -> bool:
    if step.verification_kind == "given":
        return step.conclusion in graph.visible_initial_facts and step.conclusion in graph.initial_facts
    if step.verification_kind == "numeric_equality":
        return _numeric_equality_status(step.conclusion) == "TRUE"
    rule = next((r for r in graph.rules if r.rule_id == step.rule_id), None)
    if rule is None or len(rule.premises) != len(step.premises):
        return False
    bindings = {}
    for pattern, actual in zip((*rule.premises, rule.conclusion), (*step.premises, step.conclusion)):
        bindings = bind_rule_pattern(pattern, actual, bindings)
        if bindings is None:
            return False
    return True


def run_restricted_novice(
    graph: SemanticGraph,
    *,
    enabled_rule_uses: Iterable[tuple[str, str]] | None = None,
) -> NoviceTrace:
    enabled = set(
        graph.visible_rule_uses
        if enabled_rule_uses is None
        else enabled_rule_uses
    )
    reachable = set(graph.visible_initial_facts) & set(graph.initial_facts)
    applied: list[str] = []
    pending = list(graph.steps)
    while pending:
        changed = False
        next_pending: list[SemanticStep] = []
        for step in pending:
            if step.conclusion in reachable:
                continue
            if step.verification_kind in {"given", "numeric_equality"} and _verified_step(graph, step):
                reachable.add(step.conclusion)
                applied.append(step.step_id)
                changed = True
                continue
            if (
                step.rule_id is not None
                and _verified_step(graph, step)
                and (step.unit_id, step.rule_id) in enabled
                and all(premise in reachable for premise in step.premises)
            ):
                reachable.add(step.conclusion)
                applied.append(step.step_id)
                changed = True
                continue
            next_pending.append(step)
        if not changed:
            break
        pending = next_pending
    return NoviceTrace(
        reachable_facts=tuple(sorted(reachable)),
        applied_step_ids=tuple(applied),
        blocked_targets=tuple(
            target for target in graph.target_facts if target not in reachable
        ),
    )


def _candidate_rule_uses(graph: SemanticGraph) -> tuple[tuple[str, str], ...]:
    visible = set(graph.visible_rule_uses)
    return tuple(
        sorted(
        {
            (step.unit_id, step.rule_id)
            for step in graph.steps
            if step.rule_id is not None
        }
        - visible
        )
    )


def _bridge_requirements(
    graph: SemanticGraph, uses: Iterable[tuple[str, str]]
) -> tuple[BridgeRequirement, ...]:
    rules_by_id = {rule.rule_id: rule for rule in graph.rules}
    return tuple(
        BridgeRequirement(
            unit_id=unit_id,
            rule_id=rule_id,
            relation_kind=rules_by_id[rule_id].relation_kind,
            declaration=rules_by_id[rule_id].declaration(),
        )
        for unit_id, rule_id in uses
    )


def _deletion_minimal_uses(
    graph: SemanticGraph,
    target_fact: str,
    candidates: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    visible = set(graph.visible_rule_uses)
    minimal = list(candidates)
    for candidate in candidates:
        trial = [item for item in minimal if item != candidate]
        if target_fact in run_restricted_novice(
            graph, enabled_rule_uses=visible | set(trial)
        ).reachable_facts:
            minimal = trial
    return tuple(minimal)


def find_minimal_bridge_family(
    graph: SemanticGraph,
    target_fact: str,
    *,
    max_exact_candidates: int = _MAX_EXACT_BRIDGE_CANDIDATES,
) -> BridgeFamily | None:
    if not isinstance(max_exact_candidates, int) or max_exact_candidates < 0:
        raise ValueError("max_exact_candidates must be a non-negative integer")
    visible = set(graph.visible_rule_uses)
    candidates = _candidate_rule_uses(graph)
    if target_fact in run_restricted_novice(graph).reachable_facts:
        return BridgeFamily(target_fact, ((),), 0, True)
    if target_fact not in run_restricted_novice(
        graph, enabled_rule_uses=visible | set(candidates)
    ).reachable_facts:
        return None
    if len(candidates) > max_exact_candidates:
        fallback = _deletion_minimal_uses(graph, target_fact, candidates)
        return BridgeFamily(
            target_fact,
            (_bridge_requirements(graph, fallback),),
            len(fallback),
            False,
        )
    for cardinality in range(1, len(candidates) + 1):
        alternatives = tuple(
            _bridge_requirements(graph, subset)
            for subset in combinations(candidates, cardinality)
            if target_fact
            in run_restricted_novice(
                graph, enabled_rule_uses=visible | set(subset)
            ).reachable_facts
        )
        if alternatives:
            return BridgeFamily(target_fact, alternatives, cardinality, True)
    return None


def find_minimal_bridge(
    graph: SemanticGraph, target_fact: str
) -> MinimalBridge | None:
    family = find_minimal_bridge_family(graph, target_fact)
    if family is None:
        return None
    requirements = family.alternatives[0]
    uses = {(item.unit_id, item.rule_id) for item in requirements}
    visible = set(graph.visible_rule_uses)
    sufficient = target_fact in run_restricted_novice(
        graph, enabled_rule_uses=visible | uses
    ).reachable_facts
    necessary = all(
        target_fact
        not in run_restricted_novice(
            graph,
            enabled_rule_uses=visible
            | {item for item in uses if item != removed},
        ).reachable_facts
        for removed in uses
    )
    return MinimalBridge(target_fact, requirements, sufficient, necessary)


def find_blocked_frontier(
    graph: SemanticGraph, target_fact: str
) -> BlockedFrontier:
    reachable = set(run_restricted_novice(graph).reachable_facts)
    visible = set(graph.visible_rule_uses)
    diagram = set(graph.diagram_facts)
    steps_by_conclusion: dict[str, list[SemanticStep]] = {}
    for step in graph.steps:
        steps_by_conclusion.setdefault(step.conclusion, []).append(step)

    blocked_steps: list[str] = []
    unshown_uses: list[tuple[str, str]] = []
    missing_facts: list[str] = []
    diagram_dependencies: list[str] = []
    pending = [target_fact]
    visited: set[str] = set()
    while pending:
        fact = pending.pop(0)
        if fact in visited or fact in reachable:
            continue
        visited.add(fact)
        producers = steps_by_conclusion.get(fact, [])
        if not producers:
            missing_facts.append(fact)
            if fact in diagram:
                diagram_dependencies.append(fact)
            continue
        for step in producers:
            blocked_steps.append(step.step_id)
            if (
                step.rule_id is not None
                and _verified_step(graph, step)
                and (step.unit_id, step.rule_id) not in visible
            ):
                unshown_uses.append((step.unit_id, step.rule_id))
            for premise in step.premises:
                if premise in diagram:
                    diagram_dependencies.append(premise)
                if premise not in reachable:
                    pending.append(premise)
    return BlockedFrontier(
        target_fact=target_fact,
        blocked_step_ids=_ordered_unique(blocked_steps),
        unshown_rule_uses=_ordered_unique(unshown_uses),
        missing_facts=_ordered_unique(missing_facts),
        diagram_dependencies=_ordered_unique(diagram_dependencies),
    )


def _token_maps(
    graph: SemanticGraph,
) -> tuple[
    dict[str, str],
    dict[str, str],
    dict[str, str],
    dict[str, str],
    dict[str, str],
]:
    facts = _ordered_unique(
        (
            *graph.initial_facts,
            *graph.visible_initial_facts,
            *(fact for rule in graph.rules for fact in rule.premises),
            *(rule.conclusion for rule in graph.rules),
            *(fact for step in graph.steps for fact in step.premises),
            *(step.conclusion for step in graph.steps),
            *graph.target_facts,
            *graph.diagram_facts,
        )
    )
    units = _ordered_unique(
        (
            *(step.unit_id for step in graph.steps),
            *(unit_id for unit_id, _ in graph.visible_rule_uses),
        )
    )
    rules = _ordered_unique(
        (
            *(rule.rule_id for rule in graph.rules),
            *(
                step.rule_id
                for step in graph.steps
                if step.rule_id is not None
            ),
            *(rule_id for _, rule_id in graph.visible_rule_uses),
        )
    )
    kinds = _ordered_unique(rule.relation_kind for rule in graph.rules)
    verifications = _ordered_unique(step.verification_kind for step in graph.steps)
    return (
        {value: f"F{index:08d}" for index, value in enumerate(facts, 1)},
        {value: f"U{index:08d}" for index, value in enumerate(units, 1)},
        {value: f"R{index:08d}" for index, value in enumerate(rules, 1)},
        {value: f"K{index:08d}" for index, value in enumerate(kinds, 1)},
        {value: f"V{index:08d}" for index, value in enumerate(verifications, 1)},
    )


def _topology_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def compile_shadow_world(graph: SemanticGraph) -> ShadowWorld:
    (
        fact_tokens,
        unit_tokens,
        rule_tokens,
        kind_tokens,
        verification_tokens,
    ) = _token_maps(graph)
    opaque_rules = tuple(
        OpaqueRule(
            rule_tokens[rule.rule_id],
            tuple(fact_tokens[item] for item in rule.premises),
            fact_tokens[rule.conclusion],
            kind_tokens[rule.relation_kind],
        )
        for rule in graph.rules
    )
    opaque_steps = tuple(
        OpaqueStep(
            f"S{index:08d}",
            unit_tokens[step.unit_id],
            tuple(fact_tokens[item] for item in step.premises),
            fact_tokens[step.conclusion],
            None if step.rule_id is None else rule_tokens[step.rule_id],
            verification_tokens[step.verification_kind],
        )
        for index, step in enumerate(graph.steps, 1)
    )
    verified = tuple(f"S{i:08d}" for i, step in enumerate(graph.steps, 1) if _verified_step(graph, step))
    source_topology = {
        "verified_steps": list(verified),
        "initial": [fact_tokens[item] for item in graph.initial_facts],
        "visible_initial": [fact_tokens[item] for item in graph.visible_initial_facts],
        "rules": [
            [
                rule_tokens[item.rule_id],
                [fact_tokens[fact] for fact in item.premises],
                fact_tokens[item.conclusion],
                kind_tokens[item.relation_kind],
            ]
            for item in graph.rules
        ],
        "steps": [
            [
                f"S{index:08d}",
                unit_tokens[item.unit_id],
                [fact_tokens[fact] for fact in item.premises],
                fact_tokens[item.conclusion],
                None if item.rule_id is None else rule_tokens[item.rule_id],
                verification_tokens[item.verification_kind],
            ]
            for index, item in enumerate(graph.steps, 1)
        ],
        "targets": [fact_tokens[item] for item in graph.target_facts],
        "visible_rule_uses": [
            [unit_tokens[unit], rule_tokens[rule]]
            for unit, rule in graph.visible_rule_uses
        ],
        "diagram": [fact_tokens[item] for item in graph.diagram_facts],
    }
    shadow_topology = {
        "verified_steps": list(verified),
        "initial": [fact_tokens[item] for item in graph.initial_facts],
        "visible_initial": [fact_tokens[item] for item in graph.visible_initial_facts],
        "rules": [
            [
                item.rule_token,
                list(item.premise_tokens),
                item.conclusion_token,
                item.kind_token,
            ]
            for item in opaque_rules
        ],
        "steps": [
            [
                item.step_token,
                item.unit_token,
                list(item.premise_tokens),
                item.conclusion_token,
                item.rule_token,
                item.verification_token,
            ]
            for item in opaque_steps
        ],
        "targets": [fact_tokens[item] for item in graph.target_facts],
        "visible_rule_uses": [
            [unit_tokens[unit], rule_tokens[rule]]
            for unit, rule in graph.visible_rule_uses
        ],
        "diagram": [fact_tokens[item] for item in graph.diagram_facts],
    }
    return ShadowWorld(
        verified_step_tokens=verified,
        initial_fact_tokens=tuple(fact_tokens[item] for item in graph.initial_facts),
        visible_initial_fact_tokens=tuple(
            fact_tokens[item] for item in graph.visible_initial_facts
        ),
        rules=opaque_rules,
        steps=opaque_steps,
        target_tokens=tuple(fact_tokens[item] for item in graph.target_facts),
        visible_rule_uses=tuple(
            (unit_tokens[unit], rule_tokens[rule])
            for unit, rule in graph.visible_rule_uses
        ),
        diagram_fact_tokens=tuple(fact_tokens[item] for item in graph.diagram_facts),
        source_topology_sha256=_topology_sha256(source_topology),
        shadow_topology_sha256=_topology_sha256(shadow_topology),
    )


def shadow_is_isomorphic(graph: SemanticGraph, shadow: ShadowWorld) -> bool:
    (
        fact_tokens,
        unit_tokens,
        rule_tokens,
        kind_tokens,
        verification_tokens,
    ) = _token_maps(graph)
    expected = {
        "verified_steps": [f"S{i:08d}" for i, step in enumerate(graph.steps, 1) if _verified_step(graph, step)],
        "initial": [fact_tokens[item] for item in graph.initial_facts],
        "visible_initial": [fact_tokens[item] for item in graph.visible_initial_facts],
        "rules": [
            [
                rule_tokens[item.rule_id],
                [fact_tokens[fact] for fact in item.premises],
                fact_tokens[item.conclusion],
                kind_tokens[item.relation_kind],
            ]
            for item in graph.rules
        ],
        "steps": [
            [
                f"S{index:08d}",
                unit_tokens[item.unit_id],
                [fact_tokens[fact] for fact in item.premises],
                fact_tokens[item.conclusion],
                None if item.rule_id is None else rule_tokens[item.rule_id],
                verification_tokens[item.verification_kind],
            ]
            for index, item in enumerate(graph.steps, 1)
        ],
        "targets": [fact_tokens[item] for item in graph.target_facts],
        "visible_rule_uses": [
            [unit_tokens[unit], rule_tokens[rule]]
            for unit, rule in graph.visible_rule_uses
        ],
        "diagram": [fact_tokens[item] for item in graph.diagram_facts],
    }
    actual = {
        "verified_steps": list(shadow.verified_step_tokens),
        "initial": list(shadow.initial_fact_tokens),
        "visible_initial": list(shadow.visible_initial_fact_tokens),
        "rules": [
            [
                item.rule_token,
                list(item.premise_tokens),
                item.conclusion_token,
                item.kind_token,
            ]
            for item in shadow.rules
        ],
        "steps": [
            [
                item.step_token,
                item.unit_token,
                list(item.premise_tokens),
                item.conclusion_token,
                item.rule_token,
                item.verification_token,
            ]
            for item in shadow.steps
        ],
        "targets": list(shadow.target_tokens),
        "visible_rule_uses": [list(item) for item in shadow.visible_rule_uses],
        "diagram": list(shadow.diagram_fact_tokens),
    }
    expected_sha = _topology_sha256(expected)
    actual_sha = _topology_sha256(actual)
    return (
        expected_sha == actual_sha
        and shadow.source_topology_sha256 == expected_sha
        and shadow.shadow_topology_sha256 == actual_sha
    )


def run_shadow_novice(shadow: ShadowWorld) -> ShadowNoviceTrace:
    enabled = set(shadow.visible_rule_uses)
    reachable = set(shadow.visible_initial_fact_tokens) & set(shadow.initial_fact_tokens)
    applied: list[str] = []
    pending = list(shadow.steps)
    verified = set(shadow.verified_step_tokens)
    while pending:
        changed = False
        next_pending: list[OpaqueStep] = []
        for step in pending:
            if step.conclusion_token in reachable:
                continue
            if step.rule_token is None and step.step_token in verified:
                reachable.add(step.conclusion_token)
                applied.append(step.step_token)
                changed = True
                continue
            if (
                step.rule_token is not None
                and step.step_token in verified
                and (step.unit_token, step.rule_token) in enabled
                and all(item in reachable for item in step.premise_tokens)
            ):
                reachable.add(step.conclusion_token)
                applied.append(step.step_token)
                changed = True
                continue
            next_pending.append(step)
        if not changed:
            break
        pending = next_pending
    return ShadowNoviceTrace(
        reachable_fact_tokens=tuple(sorted(reachable)),
        applied_step_tokens=tuple(applied),
        blocked_target_tokens=tuple(
            item for item in shadow.target_tokens if item not in reachable
        ),
    )


def _ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


__all__ = [
    "BlockedFrontier",
    "BridgeRequirement",
    "BridgeFamily",
    "MinimalBridge",
    "NoviceTrace",
    "SemanticGraph",
    "SemanticRule",
    "SemanticStep",
    "ShadowNoviceTrace",
    "ShadowWorld",
    "build_semantic_graph",
    "compile_shadow_world",
    "find_blocked_frontier",
    "find_minimal_bridge",
    "find_minimal_bridge_family",
    "run_restricted_novice",
    "run_shadow_novice",
    "shadow_is_isomorphic",
]
