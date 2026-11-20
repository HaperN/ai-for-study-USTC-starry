"""M5 回归：图请求终态、受限求值、有界返修、交互版本绑定。

方案 §8 点名的三件事，各有一组断言：

1. **每个图请求有终态**，而 `pending/unsupported/failed` **都不是正式完成**；
   终审还要核对"挂图与服务对象一致"，光有 SVG 不算；
2. **不得执行模型生成的代码** —— 规格可以由模型生成，所以表达式求值必须是
   AST 白名单。这里用 14 个逃逸/危险写法逐条钉住；
3. **承诺交互必须可验** —— 参数动一下，可观测量得跟着变。

方案 §6/§9 的两件事：

4. **有界返修**：轮次不可超，独立验收失败**消耗同一轮次池**（不许隐藏追加）；
5. **交互消费最终教案版本**：教案改过而交互没重生成，页面会拿旧正文的交互去渲染，
   而它看起来完全正常 —— 所以是硬断言。
"""
from __future__ import annotations

import numpy as np
import pytest

from aiforstudy.blocks.audit.loop import (DEFAULT_MAX_REPAIR_ROUNDS,
                                          DOWNSTREAM_ON_BODY_CHANGE, SEV_P0, SEV_P1, SEV_P2,
                                          TICKET_WONTFIX, Finding, Ticket, run_bounded_loop)
from aiforstudy.blocks.figures.request import (FigureLedger, FigureRequest, FigureResource,
                                               FigureResult, choose_backend, spec_digest)
from aiforstudy.blocks.figures.safe_eval import (MAX_ABS_VALUE, compile_expression, evaluate,
                                                 numpy_functions)
from aiforstudy.blocks.interaction.spec import (Expectation, InteractionResult,
                                                InteractionSpec,
                                                assert_consumes_final_revision,
                                                assert_ledger_releaseable, not_applicable,
                                                verify_expected_state_change)
from aiforstudy.errors import ContractError


# ────────────────────────────────────────── 受限求值（安全）

DANGEROUS = [
    '__import__("os")',
    'open("/etc/passwd")',
    'eval("1+1")',
    'exec("x=1")',
    'globals()',
    '().__class__',
    'x.__class__.__bases__',
    'np.sin(x)',
    '[i for i in range(3)]',
    'x if y else z',
    'lambda: 1',
    'x[0]',
    'f"{x}"',
    'print(1)',
]


@pytest.mark.parametrize("expr", DANGEROUS)
def test_dangerous_expressions_are_rejected_at_compile_time(expr):
    """**编译期就拒**，不等到求值 —— "两道口子只关一道"是沙箱最常见的失效方式。"""
    with pytest.raises(ContractError):
        compile_expression(expr)


def test_the_specific_escape_that_defeats_the_old_implementation():
    """旧实现是 `eval(expr, {"__builtins__": {}}, allowed)`。

    清空 builtins **不是沙箱**：属性链可以走到任意类。属性访问与下标现在整类禁掉，
    所以这条路在语法层面就不存在。
    """
    with pytest.raises(ContractError, match="不允许的语法|属性访问"):
        compile_expression("().__class__.__bases__[0].__subclasses__()")


def test_legitimate_expressions_still_work():
    assert evaluate("R*i", {"R": 2.0, "i": 3.0}) == 6.0
    assert evaluate("sqrt(x**2+1)", {"x": 0.0}) == 1.0
    assert abs(evaluate("2*pi*x", {"x": 0.5}) - 3.141592653589793) < 1e-12
    assert evaluate("max(x, 0)", {"x": -1.0}) == 0.0


def test_numpy_functions_are_passed_explicitly_not_mutated_globally():
    """求值器不该有跨调用的隐藏状态（并行 worker 会互相看见）。"""
    from aiforstudy.blocks.figures.safe_eval import SAFE_FUNCTIONS
    before = SAFE_FUNCTIONS["sin"]
    values = evaluate("sin(x)", {"x": np.linspace(0, 1, 3)}, numpy_functions())
    assert isinstance(values, np.ndarray)
    assert SAFE_FUNCTIONS["sin"] is before, "全局函数表不许被求值调用改动"


def test_limits_are_enforced():
    with pytest.raises(ContractError, match="指数过大"):
        evaluate("x ** 1e9", {"x": 2.0})
    with pytest.raises(ContractError, match="除数为 0"):
        evaluate("1/0", {})
    with pytest.raises(ContractError, match="未声明的变量"):
        evaluate("y + 1", {"x": 1.0})
    # ⚠️ 括号**不产生** AST 节点，所以用一元运算符叠出真正的深度
    deep = "-" * 30 + "x"
    with pytest.raises(ContractError, match="嵌套太深"):
        compile_expression(deep)
    with pytest.raises(ContractError, match="节点太多"):
        compile_expression("+".join(["x"] * 300))
    with pytest.raises(ContractError, match="只允许数字常量"):
        compile_expression('"字符串"')


# ────────────────────────────────────────── 图请求终态

def good_result(request_id="R1") -> FigureResult:
    return FigureResult(
        request_id=request_id, state="fulfilled", backend="vector",
        resources=(FigureResource("svg", "/tmp/a.svg", spec_digest="sha256:x"),),
        checks={"ok": True}, binding={"serves": "向量加法", "unit_key": "CH01-S01"})


def test_request_requires_the_fields_that_make_it_actionable():
    with pytest.raises(ContractError, match="request_id"):
        FigureRequest(request_id="", unit_key="CH01", serves="甲")
    with pytest.raises(ContractError, match="serves"):
        FigureRequest(request_id="R1", unit_key="CH01", serves="")


def test_fulfilled_needs_resources_passing_checks_and_a_binding():
    """§8：终审不能只验证 SVG 文件存在。"""
    with pytest.raises(ContractError, match="资源 refs"):
        FigureResult(request_id="R1", state="fulfilled", checks={"ok": True},
                     binding={"serves": "甲"})
    with pytest.raises(ContractError, match="几何/数值检查"):
        FigureResult(request_id="R1", state="fulfilled",
                     resources=(FigureResource("svg", "/tmp/a.svg"),),
                     checks={"ok": False}, binding={"serves": "甲"})
    with pytest.raises(ContractError, match="引用绑定"):
        FigureResult(request_id="R1", state="fulfilled",
                     resources=(FigureResource("svg", "/tmp/a.svg"),), checks={"ok": True})
    assert good_result().is_complete


def test_only_fulfilled_and_approved_not_applicable_count_as_complete():
    """§8：unsupported/failed/pending 均**不是**正式完成。"""
    for state in ("pending", "matched", "specced", "drawn", "verified"):
        result = FigureResult(request_id="R", state=state)
        assert result.is_complete is False and result.blocks_release
    for state in ("unsupported", "failed", "blocked"):
        result = FigureResult(request_id="R", state=state, reason="表达不了")
        assert result.is_complete is False
    na = FigureResult(request_id="R", state="not_applicable", policy="本节无需配图",
                      evidence="规则扫描未发现需要图示的量")
    assert na.is_complete and not na.blocks_release


def test_figure_not_applicable_needs_policy_and_evidence():
    """`FigureResult` 的 not_applicable 也必须带 policy + evidence。

    ⚠️ 这个函数原来叫 `test_not_applicable_needs_policy_and_evidence`，而**同一个文件里
    后面还有一份同名定义**（测的是 `InteractionResult`）—— Python 让后一个静默胜出，
    于是**这一份从来没跑过**：`FigureResult` 那条判据实际上处于**无覆盖**状态。
    由 `test_suite_hygiene.py::test_no_module_defines_the_same_top_level_name_twice`
    抓到（2026-10-07，台账 §9.45）。改名而不是删除 —— 它守的是一条**独立的**判据。
    """
    with pytest.raises(ContractError, match="policy"):
        FigureResult(request_id="R", state="not_applicable", policy="", evidence="x")


def test_ledger_refuses_release_while_any_request_is_unfinished():
    ledger = FigureLedger()
    ledger.record(good_result("R1"))
    ledger.record(FigureResult(request_id="R2", state="pending"))
    assert ledger.by_state() == {"fulfilled": 1, "pending": 1}
    with pytest.raises(ContractError, match="没有到终态"):
        ledger.assert_releaseable()
    ledger.record(FigureResult(request_id="R2", state="not_applicable",
                               policy="无需配图", evidence="规则扫描为空"))
    ledger.assert_releaseable()          # 不抛


def test_binding_check_catches_a_figure_hung_on_the_wrong_concept():
    """P-020/P-032 那一类：图存在、但挂错了概念，页面看起来完全正常。"""
    ledger = FigureLedger()
    ledger.record(FigureResult(request_id="R1", state="fulfilled", backend="vector",
                               resources=(FigureResource("svg", "/tmp/a.svg"),),
                               checks={"ok": True},
                               binding={"serves": "相等向量", "unit_key": "CH02-S01"}))
    problems = ledger.verify_binding("CH02-S01", expected_serves="同解方程组",
                                     catalog_names={"同解方程组"})
    assert problems and "相等向量" in problems[0]
    unknown = ledger.verify_binding("CH02-S01", expected_serves="",
                                    catalog_names={"同解方程组"})
    assert unknown and "不在当前概念表里" in unknown[0]


def test_spec_digest_is_by_content_not_filename():
    """§8：复用与落盘按**规格哈希**，不按容易重名的文件名。"""
    a = {"name": "fig-a", "points": {"O": [0, 0]}}
    b = {"name": "fig-b", "points": {"O": [0, 0]}}
    assert spec_digest(a) == spec_digest({**a, "name": "fig-a"})
    assert spec_digest(a) != spec_digest(b)


def test_backend_is_chosen_only_from_what_is_actually_installed():
    """§8：未安装的后端不作为可选项；表达不了就 blocked，**不换一张'差不多'的图**。"""
    assert choose_backend("画一个向量加法的几何图", ("vector",)) == "vector"
    assert choose_backend("画一个向量加法的几何图", ("plot",)) is None
    assert choose_backend("画函数的图像", ("plot", "vector")) == "plot"


# ────────────────────────────────────────── 有界返修

def finding(code, severity=SEV_P0, unit="CH01-S01") -> Finding:
    return Finding(code=code, severity=severity, unit_key=unit, why=f"{code} 有问题")


def test_loop_stops_at_the_bound_and_reports_needs_review():
    """§6：达到返修上限仍未通过则 needs_review，**不发布**。"""
    state = {"round": 0}

    def repair(index, open_tickets):
        state["round"] = index
        return True                      # 每轮都"改了"，但问题没解决

    outcome = run_bounded_loop(unit_key="CH01-S01", initial_findings=(finding("X"),),
                               repair_fn=repair, verify_fn=lambda: (finding("X"),),
                               max_rounds=2)
    assert outcome.max_rounds_used == 2, "轮次不许超过上限"
    assert outcome.quality_state.value == "needs_review"
    assert outcome.execution_state.value == "succeeded"
    assert outcome.can_freeze is False
    assert outcome.blocking_open and len(outcome.blocking_open) == 1


def test_loop_passes_when_the_finding_is_actually_gone():
    state = {"fixed": False}

    def repair(index, open_tickets):
        state["fixed"] = True
        return True

    outcome = run_bounded_loop(unit_key="U", initial_findings=(finding("X"),),
                               repair_fn=repair,
                               verify_fn=lambda: () if state["fixed"] else (finding("X"),))
    assert outcome.quality_state.value == "accepted"
    assert outcome.can_freeze is True and outcome.frozen_report
    assert outcome.max_rounds_used == 1


def test_no_findings_means_no_repair_rounds_wasted():
    """§6：审核是**有界循环**，但没发现问题就该直接过，不为"跑满"而返修。"""
    outcome = run_bounded_loop(unit_key="U", initial_findings=(),
                               repair_fn=lambda i, t: pytest.fail("不该返修"),
                               verify_fn=lambda: ())
    assert outcome.max_rounds_used == 0
    assert outcome.quality_state.value == "accepted"
    assert outcome.rounds[0].note == "发现阶段没有 P0/P1"


def test_independent_acceptance_failure_consumes_the_same_round_pool():
    """§6：**独立验收失败所引起的修订也消耗剩余轮次，不允许隐藏追加**。"""
    outcome = run_bounded_loop(
        unit_key="U", initial_findings=(finding("X"),),
        repair_fn=lambda i, t: True, verify_fn=lambda: (),
        accept_fn=lambda: (False, "独立验收：讲解深度不足"),
        max_rounds=1)
    assert outcome.quality_state.value == "needs_review"
    assert outcome.max_rounds_used == 1, "独立验收不许另开一轮"
    assert "不允许隐藏追加" in outcome.reason
    assert "1/1" in outcome.reason


def test_body_change_invalidates_downstream():
    """§6：交互检查要求改正文 → 回到内容循环并**使下游失效**。"""
    outcome = run_bounded_loop(unit_key="U", initial_findings=(finding("X"),),
                               repair_fn=lambda i, t: True, verify_fn=lambda: ())
    assert outcome.downstream_invalidated == DOWNSTREAM_ON_BODY_CHANGE
    assert "lesson" in outcome.downstream_invalidated


def test_wontfix_must_state_a_reason():
    """§6：`wontfix` 与"忘了改"在数据上必须可区分。"""
    with pytest.raises(ContractError, match="必须写理由"):
        Ticket(finding=finding("X"), state=TICKET_WONTFIX, reason="")


def test_a_no_op_round_stops_instead_of_burning_budget():
    outcome = run_bounded_loop(unit_key="U", initial_findings=(finding("X"),),
                               repair_fn=lambda i, t: False,
                               verify_fn=lambda: (finding("X"),), max_rounds=2)
    assert outcome.max_rounds_used == 1
    assert "没有产生任何变化" in outcome.rounds[-1].note


def test_default_repair_rounds_is_two():
    assert DEFAULT_MAX_REPAIR_ROUNDS == 2


# ────────────────────────────────────────── 交互

def spec(revision=3, *, expectations=None) -> InteractionSpec:
    return InteractionSpec(
        unit_key="CH01-S01", source_lesson_revision=revision, kind="param-slider",
        params=({"name": "R", "min": 0.1, "max": 10.0, "default": 1.0},),
        expectations=expectations or (Expectation(param="R", observable="current",
                                                  direction="increase"),))


def test_interaction_must_be_bound_to_the_final_lesson_revision():
    """§6：`source_lesson_revision` 必须等于**最终**教案版本。"""
    with pytest.raises(ContractError, match="必须等于最终教案版本"):
        assert_consumes_final_revision(spec(2), 3)
    assert_consumes_final_revision(spec(3), 3) is None


def test_interaction_spec_requires_params_and_expectations():
    """§8：不强迫所有段落产生空模型；但**承诺了**就必须可验。"""
    with pytest.raises(ContractError, match="至少一个参数"):
        InteractionSpec(unit_key="U", source_lesson_revision=1, kind="param-slider",
                        params=(), expectations=(Expectation("R", "y"),))
    with pytest.raises(ContractError, match="expectations"):
        InteractionSpec(unit_key="U", source_lesson_revision=1, kind="param-slider",
                        params=({"name": "R", "default": 1.0},), expectations=())
    with pytest.raises(ContractError, match="未声明的参数"):
        InteractionSpec(unit_key="U", source_lesson_revision=1, kind="param-slider",
                        params=({"name": "R", "default": 1.0},),
                        expectations=(Expectation("S", "y"),))


def test_a_dead_interaction_is_caught_by_actually_moving_the_parameter():
    """§8：承诺交互的单元**必须验证参数变化导致预期状态变化**。"""
    alive = verify_expected_state_change(spec(), lambda p: {"current": p["R"] * 2})
    assert alive.state == "verified" and alive.checks["ok"]
    dead = verify_expected_state_change(spec(), lambda p: {"current": 5.0})
    assert dead.state == "failed" and "其实是死的" in dead.reason


def test_missing_observable_is_a_failure_not_a_pass():
    result = verify_expected_state_change(spec(), lambda p: {"other": p["R"]})
    assert result.state == "failed" and "没有观测量的'验证'等于没验" in str(result.checks)


def test_not_applicable_needs_policy_and_evidence():
    result = not_applicable("U", policy="本节无可交互量", evidence="规则扫描为空")
    assert result.is_complete and result.state == "not_applicable"
    with pytest.raises(ContractError, match="policy"):
        InteractionResult(unit_key="U", state="not_applicable", policy="", evidence="x")


def test_ledger_refuses_release_on_a_revision_mismatch():
    """教案改过而交互没重生成 —— 页面会拿旧正文的交互渲染，而它看起来完全正常。"""
    results = {"U": InteractionResult(unit_key="U", state="verified", source_lesson_revision=2)}
    with pytest.raises(ContractError, match="最终教案是 rev 3"):
        assert_ledger_releaseable(results, {"U": 3})
    assert_ledger_releaseable({"U": InteractionResult(unit_key="U", state="verified",
                                                      source_lesson_revision=3)},
                              {"U": 3}) is None


def test_ledger_refuses_release_on_an_unfinished_unit():
    results = {"U": InteractionResult(unit_key="U", state="generated")}
    with pytest.raises(ContractError, match="不能发布"):
        assert_ledger_releaseable(results, {"U": 1})
