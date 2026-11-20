"""T05：资源级依赖、最终证书与旧运行边界（§6.2／§6.3）。

红证据（行为缺失，不是 `ModuleNotFoundError`）：
`文档/02-记录与报告/Manim与审核闭环改造证据-20261007-0035/T05/T05-red-behavior-probe.log`
—— 用**改造前**的 `pipeline/cache.py` 证明"整篇教案 hash 是唯一的图缓存键"：
改一段与三个图请求都无关的正文，三个请求的缓存命中从 3/3 掉到 **0/3**。

本文件的 10 组行为对应 T05 的 10 条必写测试：

1. `test_unrelated_text_change_recomputes_nothing`
2. `test_relevant_parameter_change_invalidates_only_that_request`
3. `test_evidence_format_change_recomputes_zero_renders`
4. `test_font_change_rerenders_without_touching_math_spec`
5. `test_missing_dependency_or_vanished_path_fails_conservatively_once`
6. `test_tampered_same_name_file_is_rejected_by_hash`
7. `test_correct_but_stale_certificate_cannot_certify_new_lesson_or_rubric`
8. `test_legacy_run_is_readable_but_refuses_in_place_resume_without_touching_it`
9. `test_base_artifacts_are_reusable_only_after_hash_verification`
10. `test_certificate_is_idempotent_and_cannot_promote_needs_review`
   （"两次 resume 不重复收费"的运行级断言在
   `tests/integration/test_finalized_resume.py`，本文件给出不变量侧的对应版本）
"""
from __future__ import annotations

import dataclasses
import inspect
import json
from pathlib import Path

import pytest

from aiforstudy.blocks.audit.contracts import ArtifactRevision, RevisionLedger
from aiforstudy.core.digest import digest_file, digest_text
from aiforstudy.errors import ContractError
from aiforstudy.pipeline import resource_binding as rb
from aiforstudy.pipeline.graph import GRAPH_VERSION

UNIT = "CH01-S01"

FRAG_PARAMS = "fragments/saddle-parameters.md"
FRAG_CUT = "fragments/cut-fixed-y.md"
FRAG_DERIV = "fragments/second-derivative.md"

P1, P2 = digest_text("a=1,h=0,k=0"), digest_text("a=2,h=0.5,k=-0.5")
C1, C2 = digest_text("固定 y 的截线"), digest_text("固定 y=1 的截线")
D1, D2 = digest_text("u_xx=2,u_yy=-2"), digest_text("u_xx=4,u_yy=-4")
ENV_V1, ENV_V2 = digest_text("fonts-v1"), digest_text("fonts-v2")
COMPILER_V1, COMPILER_V2 = digest_text("compiler-v1"), digest_text("compiler-v2")
SRC_V1, SRC_V2 = digest_text("教案 v1"), digest_text("教案 v2（只改了无关段落）")
SPEC_SADDLE = digest_text("spec-saddle")
SPEC_CUT = digest_text("spec-cut")
SPEC_DERIV = digest_text("spec-derivative")
RES_A, RES_B = digest_text("saddle.mp4"), digest_text("cut.mp4")
LESSON_L1, LESSON_L2 = digest_text("lesson L1"), digest_text("lesson L2")
RUBRIC_H1, RUBRIC_H2 = digest_text("rubric v1"), digest_text("rubric v2")
INTERACTION_H = digest_text("interaction")
POLICY_DIGEST = digest_text("repair-policy-v1")


# ────────────────────────────────────────── 装置

def _context(*, env=ENV_V1, compiler=COMPILER_V1, params=P1, cut=C1, deriv=D1):
    """当前事实。传 `None` = 这个片段在新上下文里**不存在**（路径消失）。"""
    fragments = {}
    for path, value in ((FRAG_PARAMS, params), (FRAG_CUT, cut), (FRAG_DERIV, deriv)):
        if value is not None:
            fragments[path] = value
    return rb.encode_context(environment_hash=env, compiler_hash=compiler,
                             fragments=fragments)


def _revision(revision: int, source_hash: str, context: str) -> ArtifactRevision:
    return ArtifactRevision(artifact_id="lesson.unit", unit_id=UNIT, revision=revision,
                            hash=source_hash, context_digest=context)


def _bindings(*, source_hash=SRC_V1, params=P1, cut=C1, deriv=D1, env=ENV_V1,
              compiler=COMPILER_V1):
    """四个资源，刻意做出"相关／不相关"与"依赖环境／不依赖环境"的区分。"""
    return {
        # 鞍面画面：依赖参数片段 + 渲染环境
        "R1-saddle-render": rb.ResourceBinding(
            "R1-saddle-render", ("鞍面参数 a,h,k",), {FRAG_PARAMS: params},
            source_hash, SPEC_SADDLE, compiler, env),
        # 截线画面：依赖另一个片段 + 渲染环境
        "R2-cut-render": rb.ResourceBinding(
            "R2-cut-render", ("固定 y 的截线",), {FRAG_CUT: cut},
            source_hash, SPEC_CUT, compiler, env),
        # 导数**数学规格**：不依赖字体／环境（§6.2 第 3 行要复用数学规格）
        "R3-derivative-spec": rb.ResourceBinding(
            "R3-derivative-spec", ("u_xx=2,u_yy=-2",), {FRAG_DERIV: deriv},
            source_hash, SPEC_DERIV, "", ""),
        # 导数画面：同一数学规格，但要渲染
        "R4-derivative-render": rb.ResourceBinding(
            "R4-derivative-render", ("u_xx 的画面",), {FRAG_DERIV: deriv},
            source_hash, SPEC_DERIV, compiler, env),
    }


def _policy(*, version=1, rubric="1", graph=GRAPH_VERSION, digest=POLICY_DIGEST):
    return {"policy_version": version, "policy_digest": digest, "graph_version": graph,
            "rubric_version": rubric}


def _artifacts(*, lesson=LESSON_L1, lesson_revision=1, rubric=RUBRIC_H1, rubric_version="1",
               interaction=INTERACTION_H, extra=None):
    out = {"lesson.unit": {"sha256": lesson, "revision": lesson_revision},
           "rubric": {"sha256": rubric, "version": rubric_version},
           "interaction.unit": {"sha256": interaction}}
    out.update(extra or {})
    return out


def _cert(*, lesson=LESSON_L1, lesson_revision=1, rubric=RUBRIC_H1, rubric_version="1",
          interaction=INTERACTION_H, quality="accepted", resources=None,
          policy_version=1, policy_digest=POLICY_DIGEST, graph_version=GRAPH_VERSION,
          kind=rb.KIND_UNIT_FINAL, reports=None, base_artifacts=None):
    cert = {
        "schema_version": rb.CERTIFICATE_SCHEMA,
        "kind": kind,
        "unit_id": UNIT,
        "graph_version": graph_version,
        "policy": {"version": policy_version, "digest": policy_digest},
        "lesson": {"artifact_id": "lesson.unit", "revision": lesson_revision, "hash": lesson},
        "rubric": {"version": rubric_version, "hash": rubric},
        "interaction": {"artifact_id": "interaction.unit", "hash": interaction},
        "resources": (resources if resources is not None else [
            {"request_id": "R1-saddle-render", "path": "figures/saddle.mp4",
             "sha256": RES_A, "bytes": 10},
            {"request_id": "R2-cut-render", "path": "figures/cut.mp4",
             "sha256": RES_B, "bytes": 20},
        ]),
        "quality": quality,
        "reports": dict(reports or {"math": {"status": "pass"}, "frames": {"status": "pass"}}),
    }
    if base_artifacts is not None:
        cert["base_artifacts"] = base_artifacts
    return cert


def _cert_artifacts():
    return _artifacts(extra={"figures/saddle.mp4": {"sha256": RES_A, "bytes": 10},
                             "figures/cut.mp4": {"sha256": RES_B, "bytes": 20}})


def _tree(root: Path) -> dict:
    return {str(p.relative_to(root)): digest_file(p)
            for p in sorted(Path(root).rglob("*")) if p.is_file()}


# ══════════════════════════════════════════ 1) §6.2 失效表逐行
@pytest.mark.parametrize("change,row_label,reuse,recompute,forbidden,requests", [
    (rb.UNRELATED_TEXT, "无关正文",
     [rb.R_MATH_SPEC, rb.R_FIGURES, rb.R_ANIMATION_BYTES, rb.R_VALID_MATH_EVIDENCE],
     [rb.R_LESSON_BINDING, rb.R_FREE_REFERENCE_CHECK], [], 0),
    (rb.RELEVANT_PARAMETER, "相关条件／参数／目标公式",
     [rb.R_UNRELATED_RESOURCES],
     [rb.R_AFFECTED_SPECS, rb.R_AFFECTED_RENDERS, rb.R_AFFECTED_CRITERIA], [],
     "affected-only"),
    (rb.FONT_LAYOUT_COMPILER, "字体／布局／编译器",
     [rb.R_SOURCE, rb.R_MATH_SPEC, rb.R_UNAFFECTED_MATH_EVIDENCE],
     [rb.R_AFFECTED_FRAMES, rb.R_FRAME_CHECKS, rb.R_BROWSER_CHECKS], [], 0),
    (rb.EVIDENCE_FORMAT, "仅 reviewer span／证据格式",
     [rb.R_CONTENT, rb.R_ALL_MEDIA_BYTES],
     [rb.R_EVIDENCE_LOCATION, rb.R_CLOSURE_VERIFICATION], [], "evidence-only"),
    (rb.MEDIA_LOST_OR_TAMPERED, "媒体文件丢失／篡改",
     [rb.R_OTHER_RESOURCES_VALID], [rb.R_DAMAGED_RESOURCE], [rb.R_REUSE_OLD_ACCEPTED],
     "damaged-only"),
])
def test_invalidation_table_has_one_row_per_plan_line(change, row_label, reuse, recompute,
                                                      forbidden, requests):
    row = rb.invalidation_row(change)
    assert row["row"] == row_label
    assert row["reuse"] == reuse
    assert row["recompute"] == recompute
    assert row["forbidden"] == forbidden
    assert row["model_requests"] == requests


def test_unknown_change_kind_is_rejected():
    with pytest.raises(ContractError):
        rb.invalidation_row("looks-related")


# ══════════════════════════════════════════ 2) 冻结接口
def test_frozen_signatures_and_fields_are_unchanged():
    assert list(inspect.signature(rb.affected_resources).parameters) == [
        "old_revision", "new_revision", "bindings"]
    assert list(inspect.signature(rb.verify_certificate).parameters) == [
        "certificate", "artifacts", "policy"]
    assert [f.name for f in dataclasses.fields(rb.ResourceBinding)] == [
        "request_id", "source_claims", "dependency_paths", "source_hash", "spec_hash",
        "compiler_hash", "environment_hash"]
    assert [f.name for f in dataclasses.fields(rb.ValidationReport)] == [
        "valid", "errors", "verified_hashes"]


def test_binding_rejects_unsafe_or_untraceable_dependencies():
    with pytest.raises(ContractError):
        rb.ResourceBinding("R", ("claim",), {"/abs/frag.md": C1}, SRC_V1, SPEC_CUT)
    with pytest.raises(ContractError):
        rb.ResourceBinding("R", ("claim",), {"../escape.md": C1}, SRC_V1, SPEC_CUT)
    with pytest.raises(ContractError):
        rb.ResourceBinding("R", ("claim",), {FRAG_CUT: "not-a-digest"}, SRC_V1, SPEC_CUT)
    with pytest.raises(ContractError):
        rb.ResourceBinding.from_dict({"request_id": "R", "unknown_field": 1})
    roundtrip = rb.ResourceBinding("R", ("claim",), {FRAG_CUT: C1}, SRC_V1, SPEC_CUT,
                                   COMPILER_V1, ENV_V1)
    assert rb.ResourceBinding.from_dict(roundtrip.as_dict()) == roundtrip


# ══════════════════════════════════════════ 3) 无关正文 → 0 次重算
def test_unrelated_text_change_recomputes_nothing():
    old = _revision(1, SRC_V1, _context())
    new = _revision(2, SRC_V2, _context())          # 教案 hash 变了，片段一个没变
    bindings = _bindings()

    assert rb.affected_resources(old, new, bindings) == set()

    plan = rb.plan_reuse(old, new, bindings)
    assert plan.recompute == ()                     # 图生成／渲染新增 = 0
    assert set(plan.reuse) == set(bindings)
    assert plan.paid_requests == 0
    assert rb.invalidation_row(rb.UNRELATED_TEXT)["model_requests"] == 0

    # 必须重算的是"新教案绑定 + 免费引用一致性检查"，两者都不花钱
    assert plan.rebound["R3-derivative-spec"].source_hash == SRC_V2
    assert plan.rebound["R3-derivative-spec"].spec_hash == SPEC_DERIV
    assert {check["status"] for check in plan.free_checks} == {"pass"}
    assert all(check["paid"] is False for check in plan.free_checks)
    # 整篇教案 hash 仍保留追溯（source_hash 更新了），但不再是缓存键
    assert "R3-derivative-spec" not in plan.recompute


def test_identical_revisions_are_idempotent():
    old = _revision(1, SRC_V1, _context())
    same = _revision(1, SRC_V1, _context())
    assert rb.affected_resources(old, same, _bindings()) == set()
    assert rb.plan_reuse(old, same, _bindings()).recompute == ()


def test_revision_chain_keeps_history_and_locates_the_change():
    """§6.2：旧教案 hash 保留追溯 —— 在不可变 revision 链上判定失效。

    用法与运行一致：每次复用后把绑定升到新的 head（`plan_reuse().rebound`）；
    忘了升级绑定就等于"绑定不属于这一版来源"，会保守失效（本测试末尾就是这个反例）。
    """
    ledger = RevisionLedger(artifact_id="lesson.unit", unit_id=UNIT)
    ledger.append(hash=SRC_V1, context_digest=_context())
    bindings = _bindings()
    assert rb.affected_since_previous(ledger, bindings) == set()   # 只有一版，无从比较

    # v2：无关正文改动（依赖片段一个都没变）→ 0 次重算，绑定升到 v2
    ledger.append(hash=SRC_V2, context_digest=_context())
    assert rb.affected_since_previous(ledger, bindings) == set()
    fresh = rb.plan_reuse(ledger.revisions[-2], ledger.revisions[-1], bindings)
    assert fresh.recompute == ()
    bindings = fresh.rebound

    # v3：相关参数改动（R1 依赖的参数片段变了）→ 只有 R1 失效
    ledger.append(hash=digest_text("教案 v3"), context_digest=_context(params=P2))
    assert rb.affected_since_previous(ledger, bindings) == {"R1-saddle-render"}

    # 忘了升级绑定 = 绑定不属于这一版来源 → 保守失效，不许猜"无关"
    assert rb.affected_since_previous(ledger, _bindings()) == {
        "R1-saddle-render", "R2-cut-render", "R3-derivative-spec", "R4-derivative-render"}

    # 链仍然完整可追溯：历史没有被改写
    assert [item.hash for item in ledger.revisions] == [SRC_V1, SRC_V2,
                                                        digest_text("教案 v3")]
    assert ledger.revisions[1].parent_hash == SRC_V1


# ══════════════════════════════════════════ 4) 相关参数 → 只有对应 request
def test_relevant_parameter_change_invalidates_only_that_request():
    old = _revision(1, SRC_V1, _context())
    new = _revision(2, SRC_V2, _context(params=P2))
    bindings = _bindings()

    affected = rb.affected_resources(old, new, bindings)
    assert affected == {"R1-saddle-render"}
    plan = rb.plan_reuse(old, new, bindings)
    assert set(plan.reuse) == {"R2-cut-render", "R3-derivative-spec",
                               "R4-derivative-render"}
    assert "依赖片段内容变化" in plan.reasons["R1-saddle-render"]
    assert FRAG_PARAMS in plan.reasons["R1-saddle-render"]


def test_shared_fragment_invalidates_every_dependent_request():
    old = _revision(1, SRC_V1, _context())
    new = _revision(2, SRC_V2, _context(deriv=D2))
    assert rb.affected_resources(old, new, _bindings()) == {
        "R3-derivative-spec", "R4-derivative-render"}


# ══════════════════════════════════════════ 5) 证据格式 → 渲染 0
def test_evidence_format_change_recomputes_zero_renders():
    old = _revision(1, SRC_V1, _context())
    # 证据（reviewer span）只在 verdict／audit 产物里，教案与媒体**没有新版本**
    assert rb.affected_resources(old, old, _bindings()) == set()
    plan = rb.plan_reuse(old, old, _bindings())
    assert plan.recompute == ()
    assert plan.paid_requests == 0

    row = rb.invalidation_row(rb.EVIDENCE_FORMAT)
    assert row["reuse"] == [rb.R_CONTENT, rb.R_ALL_MEDIA_BYTES]
    assert row["recompute"] == [rb.R_EVIDENCE_LOCATION, rb.R_CLOSURE_VERIFICATION]
    assert not any("render" in item or "media" in item for item in row["recompute"])


# ══════════════════════════════════════════ 6) 字体变化 → 重渲染，规格不变
def test_font_change_rerenders_without_touching_math_spec():
    old = _revision(1, SRC_V1, _context(env=ENV_V1))
    # 同一教案、同一片段，只换字体／环境
    new = _revision(2, SRC_V1, _context(env=ENV_V2))
    bindings = _bindings()

    affected = rb.affected_resources(old, new, bindings)
    assert affected == {"R1-saddle-render", "R2-cut-render", "R4-derivative-render"}
    plan = rb.plan_reuse(old, new, bindings)
    assert plan.reuse == ("R3-derivative-spec",)          # 数学规格可复用
    assert "环境／字体指纹变化" in plan.reasons["R1-saddle-render"]
    # 数学规格指纹不变
    assert plan.rebound["R3-derivative-spec"].spec_hash == SPEC_DERIV
    assert plan.rebound["R3-derivative-spec"].as_dict()["spec_hash"] == \
        bindings["R3-derivative-spec"].as_dict()["spec_hash"]
    assert rb.invalidation_row(rb.FONT_LAYOUT_COMPILER)["model_requests"] == 0


def test_compiler_change_rerenders_without_touching_math_spec():
    old = _revision(1, SRC_V1, _context(compiler=COMPILER_V1))
    new = _revision(2, SRC_V1, _context(compiler=COMPILER_V2))
    affected = rb.affected_resources(old, new, _bindings())
    assert affected == {"R1-saddle-render", "R2-cut-render", "R4-derivative-render"}
    assert "编译器指纹变化" in rb.explain_affected(old, new, _bindings())["R2-cut-render"]


# ══════════════════════════════════════════ 7) 缺依赖／路径消失 → 保守失效一次
def test_missing_dependency_or_vanished_path_fails_conservatively_once():
    old = _revision(1, SRC_V1, _context())

    # (a) 没有依赖声明的旧资源：不许猜"它无关"
    blind = rb.ResourceBinding("R-blind", ("看过正文",), {}, SRC_V1, SPEC_CUT)
    assert not blind.trustworthy and blind.unreliable_reason == "no-dependency-paths"
    assert rb.affected_resources(old, _revision(2, SRC_V2, _context()),
                                 {"R-blind": blind}) == {"R-blind"}

    # (b) 声明过的片段路径在新上下文里消失
    vanished = _revision(2, SRC_V2, _context(cut=None))
    affected = rb.affected_resources(old, vanished, _bindings())
    assert affected == {"R2-cut-render"}
    assert "消失" in rb.explain_affected(old, vanished, _bindings())["R2-cut-render"]

    # (c) 绑定不属于这一版来源（source_hash 对不上）
    wrong = _bindings(source_hash=SRC_V2)
    assert rb.affected_resources(old, _revision(2, SRC_V2, _context()), wrong) == set(wrong)

    # (d) 当前上下文不可解析 → 全部保守失效
    broken = _revision(2, SRC_V2, "not-a-context")
    assert rb.affected_resources(old, broken, _bindings()) == set(_bindings())

    # "一次"：集合幂等，重算不会反复累加
    first = rb.affected_resources(old, vanished, _bindings())
    second = rb.affected_resources(old, vanished, _bindings())
    assert first == second == {"R2-cut-render"}


def test_unreliable_binding_reasons_are_explicit():
    no_source = rb.ResourceBinding("R", ("claim",), {FRAG_CUT: C1}, "", SPEC_CUT)
    assert no_source.unreliable_reason == "no-source-hash"
    no_claims = rb.ResourceBinding("R", (), {FRAG_CUT: C1}, SRC_V1, SPEC_CUT)
    assert no_claims.unreliable_reason == "no-source-claims"
    good = rb.ResourceBinding("R", ("claim",), {FRAG_CUT: C1}, SRC_V1, SPEC_CUT)
    assert good.trustworthy and good.unreliable_reason == ""


# ══════════════════════════════════════════ 8) 同名文件被篡改 → 拒绝
def test_tampered_same_name_file_is_rejected_by_hash():
    cert = _cert()
    baseline = rb.verify_certificate(cert, _cert_artifacts(), _policy())
    assert baseline.valid, baseline.errors
    assert baseline.verified_hashes["figures/cut.mp4"] == RES_B

    tampered = _cert_artifacts()
    tampered["figures/saddle.mp4"] = {"sha256": digest_text("换了内容"), "bytes": 11}
    report = rb.verify_certificate(cert, tampered, _policy())
    assert report.valid is False
    assert report.has("media_tampered") and report.has("stale_accepted_forbidden")
    assert "figures/saddle.mp4" not in report.verified_hashes
    assert report.verified_hashes["figures/cut.mp4"] == RES_B      # 其它资源仍独立有效


def test_missing_media_and_unsafe_paths_are_rejected():
    missing = _cert_artifacts()
    missing.pop("figures/cut.mp4")
    report = rb.verify_certificate(_cert(), missing, _policy())
    assert report.has("missing_resource") and report.has("stale_accepted_forbidden")

    unsafe = _cert(resources=[{"request_id": "R1", "path": "../escape.mp4",
                               "sha256": RES_A}])
    assert rb.verify_certificate(unsafe, _cert_artifacts(), _policy()).has(
        "unsafe_resource_path")


# ══════════════════════════════════════════ 9) 正确但旧的证书不能证明新版本
def test_correct_but_stale_certificate_cannot_certify_new_lesson_or_rubric():
    cert = _cert()
    assert rb.verify_certificate(cert, _cert_artifacts(), _policy()).valid is True

    # 新教案：证书本身没毛病，但它证明的是**上一版**教案
    stale_lesson = rb.verify_certificate(
        cert, _artifacts(lesson=LESSON_L2, lesson_revision=2, extra={
            "figures/saddle.mp4": {"sha256": RES_A, "bytes": 10},
            "figures/cut.mp4": {"sha256": RES_B, "bytes": 20}}), _policy())
    assert stale_lesson.valid is False and stale_lesson.has("stale_lesson_hash")

    # 新 rubric（版本号变了）
    new_rubric = rb.verify_certificate(
        cert, _artifacts(rubric=RUBRIC_H2, rubric_version="2", extra={
            "figures/saddle.mp4": {"sha256": RES_A, "bytes": 10},
            "figures/cut.mp4": {"sha256": RES_B, "bytes": 20}}),
        _policy(rubric="2"))
    assert new_rubric.valid is False and new_rubric.has("rubric_version_mismatch")

    # 版本号没变但标准内容变了（暗改）
    dark = rb.verify_certificate(
        cert, _artifacts(rubric=RUBRIC_H2, rubric_version="1", extra={
            "figures/saddle.mp4": {"sha256": RES_A, "bytes": 10},
            "figures/cut.mp4": {"sha256": RES_B, "bytes": 20}}), _policy())
    assert dark.valid is False and dark.has("rubric_content_changed")


def test_certificate_rejects_legacy_schema_and_graph_policy_drift():
    legacy = {**_cert(), "schema_version": "0.9"}
    assert rb.verify_certificate(legacy, _cert_artifacts(), _policy()).has(
        "certificate_schema_version")

    no_graph = _cert()
    no_graph.pop("graph_version")
    assert rb.verify_certificate(no_graph, _cert_artifacts(), _policy()).has(
        "certificate_missing_field")

    assert rb.verify_certificate(_cert(), _cert_artifacts(),
                                 _policy(graph="2.0")).has("graph_version_mismatch")
    assert rb.verify_certificate(_cert(), _cert_artifacts(),
                                 _policy(version=2)).has("policy_version_mismatch")
    assert rb.verify_certificate(_cert(), _cert_artifacts(),
                                 _policy(digest=digest_text("别的护栏"))).has(
        "policy_digest_mismatch")


def test_interaction_binding_and_not_applicable_evidence():
    stale = _cert(interaction=digest_text("旧的交互"))
    assert rb.verify_certificate(stale, _cert_artifacts(), _policy()).has(
        "stale_interaction_hash")

    lax = _cert()
    lax["interaction"] = {"status": "not_applicable"}
    assert rb.verify_certificate(lax, _cert_artifacts(), _policy()).has(
        "unsupported_not_applicable")

    legal = _cert()
    legal["interaction"] = {"status": "not_applicable",
                            "policy_ref": "config/budget.json#interaction",
                            "evidence_hash": digest_text("政策+证据")}
    assert rb.verify_certificate(legal, _cert_artifacts(), _policy()).valid is True


# ══════════════════════════════════════════ 10) 幂等 + 不许升级 needs_review
def test_certificate_is_idempotent_and_cannot_promote_needs_review():
    cert = _cert(quality="needs_review",
                 reports={"math": {"status": "pass"}, "frames": {"status": "needs_review"}})
    first = rb.verify_certificate(cert, _cert_artifacts(), _policy())
    second = rb.verify_certificate(cert, _cert_artifacts(), _policy())
    assert first.valid and first.as_dict() == second.as_dict()

    promoted = {**cert, "quality": "accepted"}
    report = rb.verify_certificate(promoted, _cert_artifacts(), _policy())
    assert report.valid is False and report.has("quality_not_promotable")


# ══════════════════════════════════════════ 11) 新 run 仅按 hash 复用基础产物
def test_base_artifacts_are_reusable_only_after_hash_verification(tmp_path):
    parse = tmp_path / "book" / "parse"
    parse.mkdir(parents=True)
    files = {"mineru/layout.json": '{"pdf_info": []}',
             "chapters.json": '{"lesson_units": []}',
             "concepts.json": '{"concepts": []}'}
    for name, text in files.items():
        path = parse / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    identifiers = {"parse.mineru": "mineru/layout.json", "parse.catalog": "chapters.json",
                   "parse.concepts": "concepts.json"}
    cert = _cert(kind=rb.KIND_BASE_INPUTS, base_artifacts=[
        {"artifact_id": key, "path": name,
         "sha256": digest_file(parse / name)} for key, name in identifiers.items()])
    artifacts = {name: {"sha256": digest_file(parse / name)} for name in files}
    report = rb.verify_certificate(cert, artifacts, _policy())
    assert report.valid, report.errors
    assert set(report.verified_hashes) == set(identifiers)

    # 同名文件被改：路径没变，但内容摘要对不上 → 不许复用
    (parse / "chapters.json").write_text('{"lesson_units": [1]}', encoding="utf-8")
    changed = {name: {"sha256": digest_file(parse / name)} for name in files}
    report = rb.verify_certificate(cert, changed, _policy())
    assert report.valid is False and report.has("base_artifact_changed")
    assert "parse.catalog" not in report.verified_hashes
    assert "parse.mineru" in report.verified_hashes

    # 缺清单／缺 graph_version 的基础证书不能用
    assert rb.verify_certificate({**_cert(kind=rb.KIND_BASE_INPUTS), "base_artifacts": []},
                                 artifacts, _policy()).has("certificate_missing_field")
    legacy = _cert(kind=rb.KIND_BASE_INPUTS, base_artifacts=[
        {"artifact_id": "parse.mineru", "path": "mineru/layout.json",
         "sha256": digest_file(parse / "mineru/layout.json")}], graph_version="")
    assert rb.verify_certificate(legacy, artifacts, _policy()).valid is False


# ══════════════════════════════════════════ 12) legacy run：可读、拒绝原地 resume
def _legacy_run(tmp_path: Path, graph_version: str = "") -> Path:
    run_dir = tmp_path / "runs" / "run-20000101-000000-c36a42"
    run_dir.mkdir(parents=True)
    state = {"schema_version": "1.0", "run_id": run_dir.name, "book_id": "mini-book",
             "goal": "deliver", "profile": "release", "status": "stopped",
             "nodes": {"audit:CH01-S01": {"execution_state": "succeeded"}}}
    if graph_version:
        state["graph_version"] = graph_version
    (run_dir / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    (run_dir / "plan.json").write_text(json.dumps(
        {"resolved_scope": [UNIT], "book_id": "mini-book"}), encoding="utf-8")
    (run_dir / "request.json").write_text(json.dumps({"book_id": "mini-book"}),
                                          encoding="utf-8")
    (run_dir / "products").mkdir()
    (run_dir / "products" / "audit.json").write_text('{"payload": {}}', encoding="utf-8")
    return run_dir


def test_legacy_run_is_readable_but_refuses_in_place_resume_without_touching_it(tmp_path):
    run_dir = _legacy_run(tmp_path)
    before = _tree(run_dir)

    identity = rb.read_run_identity(run_dir)            # 可读
    assert identity["has_state"] is True
    assert identity["book_id"] == "mini-book"
    assert identity["graph_version"] == ""              # 缺 graph_version = legacy
    assert identity["resolved_scope"] == [UNIT]

    decision = rb.resume_guard(state=json.loads((run_dir / "state.json").read_text()),
                               plan=json.loads((run_dir / "plan.json").read_text()),
                               run_id=run_dir.name)
    assert decision.allowed is False
    assert decision.code == rb.LEGACY_GRAPH_CODE == "legacy_graph_requires_new_run"
    assert decision.command == ("aifs run --book mini-book --units CH01-S01 "
                               "--goal deliver --profile release")
    assert "不许原地 resume" in decision.message

    with pytest.raises(rb.LegacyRunError) as excinfo:
        rb.assert_resumable(state=json.loads((run_dir / "state.json").read_text()),
                            plan=json.loads((run_dir / "plan.json").read_text()),
                            run_id=run_dir.name)
    assert excinfo.value.code == rb.LEGACY_GRAPH_CODE
    assert "aifs run" in str(excinfo.value)

    # **不修改旧目录**：0 字节改动，也没有新建 events.jsonl
    assert not (run_dir / "events.jsonl").exists()
    assert _tree(run_dir) == before


def test_current_run_is_resumable_and_older_graph_version_is_not(tmp_path):
    current = _legacy_run(tmp_path / "a", graph_version=GRAPH_VERSION)
    assert rb.resume_guard(state=json.loads((current / "state.json").read_text()),
                           plan={}, run_id=current.name).allowed is True
    assert rb.read_run_identity(current)["graph_version"] == GRAPH_VERSION

    older = _legacy_run(tmp_path / "b", graph_version="2.0")
    decision = rb.resume_guard(state=json.loads((older / "state.json").read_text()),
                               run_id=older.name)
    assert decision.allowed is False
    assert decision.code == rb.GRAPH_VERSION_CHANGED_CODE
    assert decision.command.startswith("aifs run --book mini-book --all")


def test_a_run_that_does_not_exist_is_not_reported_as_legacy(tmp_path):
    """§6.3 管的是"**存在但老**的 run"。敲错 run_id 不是 legacy，也不该建议新建。

    把"没有这个 run"报成"旧图需要新建"，用户会照着新建 —— 既没找到原来的 run，
    又白建一个。所以这一档**放行**（交给 `RunStore.open` 报"没有这次运行的记录"）
    且**不给新建命令**。
    """
    missing_run_id = "run-不存在-000000-000000"

    decision = rb.resume_guard(state=None, plan=None, run_id=missing_run_id)
    assert decision.allowed is True
    assert decision.code == rb.RUN_NOT_FOUND_CODE == "run_not_found"
    assert decision.code != rb.LEGACY_GRAPH_CODE
    assert decision.command == "", "没有这个 run 时不许给出新建 run 的命令建议"
    assert "没有这次运行" in decision.message
    assert decision.run_id == missing_run_id

    # 空字典（read_json_soft 读不到时给的就是空值）同样不是 legacy
    assert rb.resume_guard(state={}, plan={}, run_id=missing_run_id).code == \
        rb.RUN_NOT_FOUND_CODE
    # `read_json_soft` 也可能读到"不是对象"的 JSON：那是坏记录，不是 legacy
    assert rb.resume_guard(state=[1, 2], plan="nonsense").code == rb.RUN_NOT_FOUND_CODE

    # `assert_resumable` 不把它当 legacy 抛；由读取方报"没有这次运行的记录"
    assert rb.assert_resumable(state={}, plan={}, run_id=missing_run_id) == ""
    assert rb.assert_resumable(state=None, plan=None, run_id=missing_run_id) == ""
    # 反过来，**存在但缺 graph_version** 的 record 仍然要抛 legacy（别把修复做过头）
    with pytest.raises(rb.LegacyRunError) as excinfo:
        rb.assert_resumable(state={"book_id": "mini-book"}, plan={}, run_id="r")
    assert excinfo.value.code == rb.LEGACY_GRAPH_CODE

    # 目录不存在时，只读读取如实报告"没找到"，而不是给出图版本
    identity = rb.read_run_identity(tmp_path / "runs" / missing_run_id)
    assert identity["found"] is False and identity["has_state"] is False
    assert identity["graph_version"] == ""
