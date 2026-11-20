"""Default services shared by CLI and HTTP; construction never sends a request."""
from __future__ import annotations
import os
import re
from ..errors import ConfigError
from .transport import API_KEY_ENV, RealTransport, resolve_api_key
from .local_compatible import LocalCompatibleTransport
from .dsh_account import DshAccountTransport
from .routing import RoleTransportRouter


def _explicit_key(spec, config, workspace_root):
    """Return (credential, safe source), or None when no reference is declared."""
    if 'api_key_ref' not in spec and 'api_key_ref' not in config:
        return None
    ref = spec.get('api_key_ref') if 'api_key_ref' in spec else config['api_key_ref']
    if not isinstance(ref, str) or not re.fullmatch(r'env:[A-Za-z_][A-Za-z0-9_]*', ref):
        raise ConfigError('api_key_ref requires env:NAME')
    if ref == f'env:{API_KEY_ENV}':
        # This standard reference has always included the workspace secret file.
        key, source = resolve_api_key(workspace_root)
    else:
        key = os.environ.get(ref[4:], '').strip()
        source = ref
    if not key:
        raise ConfigError('Configured credential is missing')
    return key, source


def configured_providers(request, workspace):
    from ..pipeline.services import Providers
    if request.profile.value != 'release':
        return Providers(is_stub=request.profile.value == 'smoke')
    config = workspace.config_json('models.json')
    routes = {}
    for role, spec in config.get('roles', {}).items():
        if not isinstance(spec, dict):
            raise ConfigError('Model role must be an object')
        provider = spec.get('provider', config.get('provider', 'deepseek'))
        model = str(spec.get('model') or '')
        if not model:
            raise ConfigError('Model role requires a model')
        if provider == 'deepseek-account':
            routes[role] = DshAccountTransport(model=model,workspace_root=workspace.root)
        elif provider == 'local-compatible':
            base = spec.get('base_url',config.get('base_url'))
            if not base:
                raise ConfigError('Local transport requires explicit base_url')
            key, source = _explicit_key(spec, config, workspace.root) or ('', 'none')
            routes[role] = LocalCompatibleTransport(model=model,base_url=str(base),api_key=key)
            routes[role]._key_ref = source
        elif provider == 'deepseek':
            resolved = _explicit_key(spec, config, workspace.root)
            key, source = resolved if resolved is not None else resolve_api_key(workspace.root)
            if not key:
                # Validate the other roles before deciding whether the legacy
                # missing-key capability report is appropriate for this config.
                routes[role] = None
                continue
            routes[role] = RealTransport(
                model=model,
                base_url=str(spec.get('base_url', config.get('base_url')) or 'https://api.deepseek.com'),
                api_key=key, _key_ref=source, workspace_root=workspace.root, network_wait=True)
        else:
            raise ConfigError('Unsupported model provider')
    if routes and any(route is None for route in routes.values()):
        if all(route is None for route in routes.values()):
            return Providers(llm=None)
        raise ConfigError('Cloud role requires a configured API key')
    if routes:
        first = next(iter(routes.values()))
        # Dataclass equality compares the entire effective configuration,
        # including credentials, in memory. Public cache identities intentionally
        # omit credentials and must never decide transport interchangeability.
        if all(type(route) is type(first) and route == first for route in routes.values()):
            return Providers(llm=first)
    return Providers(llm=RoleTransportRouter(routes) if routes else None)
