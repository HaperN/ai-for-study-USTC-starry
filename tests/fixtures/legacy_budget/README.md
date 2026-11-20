# 旧预算/护栏配置（仅测试用）

`budget.json`（总 token/时长预算）与 `repair_policy.json`（Manim 1 路、单次 300 秒等）描述的是已废止的预算机制，
与正式规则矛盾。2026-10-16 经用户确认从 `config/` 移到这里，只供 `pipeline/budget.py` 的本地测试显式读取。

正式运行规则只看 `config/execution.json`（并发、超时、有限尝试）与 `config/models.json`（模型角色）。
