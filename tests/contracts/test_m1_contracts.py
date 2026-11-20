"""M1 契约回归：统一契约（方案 §6）的**构造即校验**。

为什么这些断言值得单独写：方案 §9 的两轴状态（execution_state / quality_state）如果只写在
文档里，代码里一定会退回"一个 bool"。所以每条规则都在这里钉成"这样构造必须报错"。

对应验收项：A05（非法范围明确失败）、A19（审核未关不能 accepted）、A26（blocked 不算通过）。
"""
from __future__ import annotations

import pytest

from aiforstudy.contracts import (ArtifactRef, DeliveryFormat, ExecutionState, Goal,
                                  PreparationTask, Profile, QualityState, ReleaseManifest,
                                  RunRequest, ScopePlan, Selectors, SelectorKind, StageResult)
from aiforstudy.core.digest import DigestError, digest_of
from aiforstudy.errors import ContractError


# ────────────────────────────────────────── RunRequest：范围三选一、必须显式给书

def test_all_selector_rejects_values():
    """`--all` 带 values 是自相矛盾：既要整本又指定了子集。"""
    with pytest.raises(ContractError):
        Selectors(kind=SelectorKind.ALL, values=("CH01",))


def test_chapters_selector_requires_values():
    """空范围必须失败 —— 否则会被下游误读成"跑整本"（方案 §4）。"""
    with pytest.raises(ContractError, match="空范围"):
        Selectors(kind=SelectorKind.CHAPTERS, values=())


def test_request_requires_book_id():
    """方案 §4：省略 --book 不退回历史默认书。"""
    req = RunRequest(book_id="", selectors=Selectors(kind=SelectorKind.ALL))
    with pytest.raises(ContractError, match="book_id"):
        req.validate()


def test_parallelism_is_bounded():
    """方案 §11：首期默认 2、上限 8，不使用无界线程池。"""
    with pytest.raises(ContractError, match="parallelism"):
        RunRequest(book_id="b", selectors=Selectors(kind=SelectorKind.ALL),
                   parallelism=64).validate()


def test_release_request_has_no_cumulative_budget_contract():
    request = RunRequest(book_id="b", selectors=Selectors(kind=SelectorKind.ALL),
                         profile=Profile.RELEASE).validate()
    assert "budget" not in request.as_dict()
    with pytest.raises(ContractError, match="不认识的字段"):
        RunRequest.from_dict({**request.as_dict(), "budget": {"max_tokens": 1000}})


def test_request_rejects_unknown_fields():
    """拼错的字段不能被静默忽略 —— 那会让用户以为它生效了。"""
    with pytest.raises(ContractError, match="不认识的字段"):
        RunRequest.from_dict({"book_id": "b", "selectors": {"kind": "all", "values": []},
                              "budget": {"max_tokens": 1, "max_minutes": 1},
                              "gannt": True})


def test_digest_ignores_idempotency_and_resume():
    """方案 §5：CLI 与 HTTP 的**等价请求必须生成相同 plan digest**。

    同一个请求重发一次（换了幂等键）不能变成"另一个计划"。
    """
    base = dict(book_id="b", selectors=Selectors(kind=SelectorKind.CHAPTERS, values=("CH01",)))
    a = RunRequest(**base, idempotency_key="k1")
    b = RunRequest(**base, idempotency_key="k2", resume="run-1")
    assert a.digest() == b.digest()


def test_digest_changes_with_scope():
    a = RunRequest(book_id="b", selectors=Selectors(kind=SelectorKind.CHAPTERS, values=("CH01",)))
    b = RunRequest(book_id="b", selectors=Selectors(kind=SelectorKind.CHAPTERS, values=("CH02",)))
    assert a.digest() != b.digest()


@pytest.mark.parametrize('format',['scope-html','zip','single-html'])
def test_unsupported_delivery_format_cannot_be_requested(format):
    with pytest.raises(ContractError):
        RunRequest.from_dict({'book_id':'B','selectors':{'kind':'all','values':[]},'delivery_formats':[format]})


def test_digest_refuses_run_scoped_fields():
    """指纹输入里混进 run_id/时间 = 缓存永远命中不了。**运行期直接报错**，不靠注释。"""
    with pytest.raises(DigestError):
        digest_of({"stage": "lesson", "run_id": "x"})


def test_request_roundtrips_through_json():
    a = RunRequest(book_id="b", selectors=Selectors(kind=SelectorKind.UNITS, values=("U1",)),
                   goal=Goal.PREPARE, profile=Profile.SMOKE)
    b = RunRequest.from_dict(a.as_dict())
    assert b.digest() == a.digest()
    assert b.goal is Goal.PREPARE and b.profile is Profile.SMOKE


# ────────────────────────────────────────── ScopePlan：冷启动不许编造

def test_cold_start_plan_reports_unknown_not_zero():
    """方案 §6：冷启动把 resolved_scope 标 null，**不能为让计划好看而虚构单元数**。"""
    plan = ScopePlan(request_digest="sha256:" + "0" * 64, book_id="b", goal=Goal.DELIVER,
                     profile=Profile.RELEASE, selectors=Selectors(kind=SelectorKind.ALL),
                     resolved_scope=None, requires_preparation=True)
    assert plan.is_cold_start
    assert plan.target_count is None          # ← 不是 0
    assert plan.as_dict()["target_count"] is None


def test_plan_without_scope_must_require_preparation():
    with pytest.raises(ContractError, match="requires_preparation"):
        ScopePlan(request_digest="sha256:" + "0" * 64, book_id="b", goal=Goal.DELIVER,
                  profile=Profile.RELEASE, selectors=Selectors(kind=SelectorKind.ALL),
                  resolved_scope=None, requires_preparation=False)


def test_plan_rejects_duplicate_units():
    """范围必须先解析、去重、再固定（方案 §4）。"""
    with pytest.raises(ContractError, match="重复"):
        ScopePlan(request_digest="sha256:" + "0" * 64, book_id="b", goal=Goal.DELIVER,
                  profile=Profile.RELEASE, selectors=Selectors(kind=SelectorKind.ALL),
                  resolved_scope=("U1", "U1"))


def test_plan_digest_excludes_absolute_paths():
    """方案 §11：digest 不含绝对工作目录 —— 挪工作区不该让缓存全失效。"""
    common = dict(request_digest="sha256:" + "0" * 64, book_id="b", goal=Goal.DELIVER,
                  profile=Profile.RELEASE, selectors=Selectors(kind=SelectorKind.ALL),
                  resolved_scope=("U1",))
    a = ScopePlan(**common, read_dependencies=("/work/a/data/parse",),
                  write_targets=("/work/a/data/lesson",))
    b = ScopePlan(**common, read_dependencies=("/work/b/data/parse",),
                  write_targets=("/work/b/data/lesson",))
    assert a.digest() == b.digest()


# ────────────────────────────────────────── StageResult：两轴状态

def test_blocked_requires_an_error():
    """方案 §9：只说"没成"却不说为什么，下游只能重跑或静默跳过。"""
    with pytest.raises(ContractError, match="error"):
        StageResult(stage="lesson", execution_state=ExecutionState.BLOCKED)


@pytest.mark.parametrize("state", [ExecutionState.BLOCKED, ExecutionState.FAILED,
                                   ExecutionState.CANCELLED])
def test_every_unfinished_state_carries_a_reason(state):
    result = StageResult(stage="s", execution_state=state, error={"reason": "演示"})
    assert result.error and not result.feeds_downstream and result.blocks_release


def test_blocked_is_not_a_pass():
    """方案 §15 A26：缺必需能力算 blocked，**不许用 skipped 当通过**。"""
    result = StageResult.blocked("figure", "缺 pdflatex")
    assert result.execution_state is ExecutionState.BLOCKED
    assert result.blocks_release


def test_not_applicable_needs_policy_and_evidence():
    """方案 §9：not_applicable 只能由明确政策与证据产生，不允许"缺工具/没运行"冒充。"""
    with pytest.raises(ContractError, match="policy"):
        StageResult(stage="interaction", execution_state=ExecutionState.NOT_APPLICABLE)
    ok = StageResult.not_applicable("interaction", policy="无适用交互",
                                    evidence="规则 scan 未发现可交互量")
    assert ok.execution_state is ExecutionState.NOT_APPLICABLE
    assert not ok.blocks_release              # 合法的"不适用"不该阻塞发布


def test_accepted_cannot_sit_on_top_of_failure():
    with pytest.raises(ContractError, match="accepted"):
        StageResult(stage="audit", execution_state=ExecutionState.FAILED,
                    error={"reason": "x"}, quality_state=QualityState.ACCEPTED)


def test_succeeded_with_rejected_is_a_valid_combination():
    """方案 §9 的原话：生成了错误图属于 succeeded + rejected。"""
    result = StageResult(stage="figure", execution_state=ExecutionState.SUCCEEDED,
                         quality_state=QualityState.REJECTED)
    assert result.feeds_downstream and result.blocks_release


def test_stage_fingerprint_excludes_timing():
    a = StageResult.ok("s", attempt=1, started_at=1.0, finished_at=2.0)
    b = StageResult.ok("s", attempt=3, started_at=9.0, finished_at=99.0)
    assert a.fingerprints() == b.fingerprints()


# ────────────────────────────────────────── ArtifactRef / ReleaseManifest

def test_artifact_ref_needs_logical_identity(tmp_path):
    """方案 §6：不能只给一个"latest"路径。"""
    with pytest.raises(ContractError):
        ArtifactRef(book_id="b", kind="", logical_key="k", path=tmp_path / "x.json")
    with pytest.raises(ContractError):
        ArtifactRef(book_id="b", kind="lesson.unit", logical_key="", path=tmp_path / "x.json")


def test_artifact_verify_detects_tampering(tmp_path):
    """方案 §11：读取前验证产物哈希与来源，**缓存损坏会失效**。"""
    path = tmp_path / "x.json"
    path.write_text('{"a":1}', encoding="utf-8")
    ref = ArtifactRef(book_id="b", kind="lesson.unit", logical_key="u1", path=path).with_content()
    ref.verify()                                     # 现在对得上
    path.write_text('{"a":2}', encoding="utf-8")
    with pytest.raises(ContractError, match="指纹对不上"):
        ref.verify()


def test_artifact_without_digest_is_refused(tmp_path):
    path = tmp_path / "x.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ContractError, match="content_digest"):
        ArtifactRef(book_id="b", kind="k", logical_key="l", path=path).verify()


def test_release_manifest_set_criterion():
    """方案 §15：requested = 教案 = 审核 = 交互 = 页面 = 包。"""
    def ref(kind, key):
        return ArtifactRef(book_id="b", kind=kind, logical_key=key, path="/dev/null")

    units = [ref("requested", "U1"), ref("requested", "U2"),
             ref("lesson", "U1"), ref("lesson", "U2"),
             ref("audit", "U1"), ref("audit", "U2"),
             ref("interaction", "U1"), ref("interaction", "U2"),
             ref("page", "U1"), ref("page", "U2"),
             ref("package", "U1"), ref("package", "U2")]
    manifest = ReleaseManifest(release_id="r1", book_id="b", scope_kind="book",
                               selectors={"kind": "all"}, parse_revision="p1",
                               context_revision="c1", units=tuple(units))
    assert manifest.verify_sets()["requested"] == 2

    # 少一个交互 → 必须报错（"少东西也不会报错"那一族的总闸）
    short = ReleaseManifest(release_id="r2", book_id="b", scope_kind="book",
                            selectors={"kind": "all"}, parse_revision="p1",
                            context_revision="c1",
                            units=tuple(u for u in units
                                        if not (u.kind == "interaction" and u.logical_key == "U2")))
    with pytest.raises(ContractError, match="集合判据"):
        short.verify_sets()


def test_unit_release_cannot_claim_full_book_ready():
    """方案 §10：单章成功可 scope_accepted=true，但不代表 full_book_ready。"""
    with pytest.raises(ContractError, match="full_book_ready"):
        ReleaseManifest(release_id="r", book_id="b", scope_kind="chapters",
                        selectors={"kind": "chapters", "values": ["CH01"]},
                        parse_revision="p", context_revision="c",
                        scope_accepted=True, full_book_ready=True)


def test_scope_accepted_cannot_hide_a_failed_check():
    """方案 §9：全部必要检查结果进入最外层结论。"""
    with pytest.raises(ContractError, match="检查未通过"):
        ReleaseManifest(release_id="r", book_id="b", scope_kind="book",
                        selectors={"kind": "all"}, parse_revision="p", context_revision="c",
                        checks=({"名称": "离线打开", "通过": False},), scope_accepted=True)


def test_preparation_and_estimate_must_be_honest():
    from aiforstudy.contracts import Estimate
    with pytest.raises(ContractError, match="unknown_reason"):
        Estimate(bucket="chapter")               # 既没区间也没说明 → 不许悄悄填 0
    assert Estimate(bucket="chapter", unknown_reason="无历史样本").tokens_low is None
    with pytest.raises(ContractError, match="区间反了"):
        Estimate(bucket="chapter", tokens_low=100, tokens_high=5)
    assert PreparationTask(stage="mineru", reason="没有产物").scope == "book"
