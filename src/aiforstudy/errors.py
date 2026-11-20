"""错误分类与**最外层退出码**。

方案 §9 把退出码写死，理由和 `tools/doctor.py` 那条一样：**崩溃和告警长得一模一样**
的时候，真失败会藏在一片假失败里。所以每一类错误自带它的退出码，`main()` 只做映射。

| 退出码 | 含义 |
|---|---|
| 0 | 请求目标完成；**只有** goal=deliver、profile=release 且 release_ready=true 才代表正式交付通过 |
| 1 | 质量未达标 / 需要复核 / 目标未完成 |
| 2 | 输入、配置、契约、工具或执行错误（含必要能力缺失） |
| 3 | 预算停止或时间上限触发 |
| 130 | 用户取消 / 中断 |

⚠️ 注意 `Blocked` 归 2 而不是 1：方案 §15 A26 要求"缺浏览器/Node/必需后端或缓存回放 miss
时 release/replay 明确 blocked，**不用 skipped 当通过**"。blocked 是**没跑成**，不是"跑了但不够好"。
"""
from __future__ import annotations

EXIT_OK = 0
EXIT_QUALITY = 1
EXIT_USAGE = 2
EXIT_BUDGET = 3
EXIT_CANCELLED = 130


class AifsError(RuntimeError):
    """所有本引擎错误的基类。`exit_code` 是它该以什么码退出。"""
    exit_code = EXIT_USAGE


class ConfigError(AifsError):
    """配置/工作区/注册表本身有问题（缺文件、坏 JSON、要的书不在里面）。"""
    exit_code = EXIT_USAGE


class ContractError(AifsError):
    """契约不满足（必填字段缺失、枚举非法、范围自相矛盾）。"""
    exit_code = EXIT_USAGE


class InputError(AifsError):
    """用户输入有问题（未知章号、反向区间、空范围、跨书单元）。"""
    exit_code = EXIT_USAGE


class CapabilityError(AifsError):
    """必需能力缺失（后端、浏览器、Node、模型密钥）。**不许用 skipped 当通过。**"""
    exit_code = EXIT_USAGE


class QualityError(AifsError):
    """质量未达标 / 需要复核。"""
    exit_code = EXIT_QUALITY


class BudgetExceeded(AifsError):
    """预算（token 或时间）停止派发。"""
    exit_code = EXIT_BUDGET


class Cancelled(AifsError):
    """用户取消 / 中断。"""
    exit_code = EXIT_CANCELLED


#: 全部对外错误类型（测试要遍历它，确保每个都有退出码）
ALL_ERRORS = (ConfigError, ContractError, InputError, CapabilityError,
              QualityError, BudgetExceeded, Cancelled)

__all__ = ["EXIT_OK", "EXIT_QUALITY", "EXIT_USAGE", "EXIT_BUDGET", "EXIT_CANCELLED",
           "AifsError", "ConfigError", "ContractError", "InputError", "CapabilityError",
           "QualityError", "BudgetExceeded", "Cancelled", "ALL_ERRORS"]
