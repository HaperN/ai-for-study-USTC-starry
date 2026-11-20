"""Role routing without mutable shared current-role state."""
from ..errors import ConfigError


class RoleTransportRouter:
    def __init__(self, transports):
        self.transports = dict(transports)

    def for_role(self, role):
        try:
            return self.transports[role]
        except KeyError:
            raise ConfigError(f'No transport configured for role {role!r}') from None
