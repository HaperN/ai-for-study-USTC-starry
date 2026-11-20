"""检查块（方案 §3 的 `checks/`：解析、内容、范围、新鲜度、交付检查）。

M7 落地：

* `offline.py` —— 交付前检查：外部引用 = 0、丢图 = 0；
  缺必需能力标 `blocked`（**不用 skipped 算通过**）；T10 另加
  `check_animation_playback()`：在独立导出目录上起本机服务，用真实 Chromium 把动画**播一遍**
  （`poster` 可见／HTTP 200／文件非空都不算通过）；
* `quality.py` —— 问题汇总并入质量报告与**交付声明**，按当前 scope 自动重算（§6）。

本包只做**检查**。`repair` 是显式动作、单独入口（方案 §11：check 与 repair 分离）。
"""
from __future__ import annotations

from .offline import (OfflineCheck, OfflineReport, check_animation_playback,
                      check_browser_available, check_no_missing_images,
                      require_capabilities_for_release,
                      run_offline_acceptance)
from .quality import Finding as QualityFinding, QualityReport, build_quality_report

__all__ = ["OfflineCheck", "OfflineReport", "check_animation_playback",
           "check_browser_available", "check_no_missing_images",
           "require_capabilities_for_release",
           "run_offline_acceptance",
           "QualityFinding", "QualityReport", "build_quality_report"]
