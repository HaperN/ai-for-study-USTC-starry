"""Model role configuration and secret references for the shared production gateway.

Credentials are resolved only inside the configured transport. This module never
creates a second model client or returns secret values to the UI.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..core.workspace import Workspace
from ..errors import CapabilityError, ConfigError


@dataclass(frozen=True)
class RoleConfig:
    role: str
    model: str
    effort: str
    max_tokens: int
    note: str = ""
    length_retry_max_tokens: int = 0

    def as_dict(self) -> dict:
        return {"role": self.role, "model": self.model, "effort": self.effort,
                "max_tokens": self.max_tokens, "note": self.note,
                "length_retry_max_tokens": self.length_retry_max_tokens}


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    base_url: str
    api_key_ref: str
    secrets_file: Path | None
    roles: dict[str, RoleConfig]
    retry_limit: int = 3

    def role(self, name: str) -> RoleConfig:
        if name not in self.roles:
            raise ConfigError(
                f"config/models.json 里没有角色 {name!r}；有的是：{sorted(self.roles)}")
        return self.roles[name]

    def as_dict(self) -> dict:
        # ⚠️ **不回传密钥**（方案 §5）：这里只有引用名与文件位置
        return {"provider": self.provider, "base_url": self.base_url,
                "api_key_ref": self.api_key_ref,
                "secrets_file": (str(self.secrets_file) if self.secrets_file else None),
                "retry_limit": self.retry_limit,
                "roles": {k: v.as_dict() for k, v in sorted(self.roles.items())}}


def load_config(workspace: Workspace) -> ProviderConfig:
    cfg = workspace.config_json("models.json")
    roles_raw = cfg.get("roles")
    if not isinstance(roles_raw, dict) or not roles_raw:
        raise ConfigError(f"{workspace.config / 'models.json'} 里没有非空的 roles")
    roles: dict[str, RoleConfig] = {}
    for name, spec in roles_raw.items():
        if not isinstance(spec, dict):
            raise ConfigError(f"角色 {name!r} 的配置必须是对象")
        roles[str(name)] = RoleConfig(
            role=str(name), model=str(spec.get("model") or ""),
            effort=str(spec.get("effort") or "high"),
            max_tokens=int(spec.get("max_tokens") or 0),
            note=str(spec.get("note") or ""),
            length_retry_max_tokens=spec.get("length_retry_max_tokens", 0))
    secrets = cfg.get("secrets_file")
    return ProviderConfig(
        provider=str(cfg.get("provider") or "deepseek"),
        base_url=str(cfg.get("base_url") or ""),
        api_key_ref=str(cfg.get("api_key_ref") or "env:DEEPSEEK_API_KEY"),
        secrets_file=(workspace.path(secrets) if secrets else None),
        roles=roles,
        retry_limit=int((cfg.get("retry") or {}).get("network_and_format") or 3))


def api_key_from_ref(ref: str, secrets_file: Path | None) -> str:
    """按 `env:NAME` 取密钥：环境变量优先，其次 `.secrets/`。**密钥不回传、不写日志。**"""
    if not ref.startswith("env:"):
        raise ConfigError(f"api_key_ref 只支持 env:NAME 形式，实际是 {ref!r}")
    name = ref[4:]
    value = os.environ.get(name)
    if value:
        return value.strip()
    if secrets_file and secrets_file.is_file():
        for line in secrets_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key.strip() == name:
                return val.strip()
    raise CapabilityError(
        f"没有拿到 {name}：请设置环境变量，或写进 {secrets_file}（该文件已被 .gitignore 排除）。"
        f"密钥只走引用，不进 config/、不进日志、不回传浏览器（方案 §5）。")


__all__ = ["RoleConfig", "ProviderConfig", "load_config", "api_key_from_ref"]
