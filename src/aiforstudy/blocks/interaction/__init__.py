"""教学交互块（方案 §3 的 `blocks/interaction/`，原 `blocks/model`）。

M5 落地 `spec.py` —— 交互规格**消费最终教案版本**（`source_lesson_revision` 硬断言），
并真的把参数动一下来验证"参数变化导致预期状态变化"（§6/§8）。
"没有适用交互"走经政策认可的 `not_applicable`，不产出没有参数的空模型。
"""
from __future__ import annotations

from .spec import (KINDS, Expectation, InteractionResult, InteractionSpec,
                   assert_consumes_final_revision, assert_ledger_releaseable, not_applicable,
                   verify_expected_state_change)

__all__ = ["KINDS", "Expectation", "InteractionResult", "InteractionSpec",
           "assert_consumes_final_revision", "assert_ledger_releaseable", "not_applicable",
           "verify_expected_state_change"]
