"""Replaceable, server-owned identity boundary for nickname or school sessions.

A future school adapter validates its own session/SSO credentials and returns a
Principal with a stable subject. Client nickname/user-id fields are never used
as that subject. No school protocol or login endpoint is implemented here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
from typing import Mapping, Protocol


class IdentityError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


@dataclass(frozen=True)
class Principal:
    namespace: str
    subject: str = field(repr=False)
    display_name: str | None = None

    def __post_init__(self):
        if not isinstance(self.namespace, str) or not re.fullmatch(r'[A-Za-z0-9:._-]{1,100}', self.namespace):
            raise ValueError('Identity namespace must be a server-controlled stable label')
        if not isinstance(self.subject, str) or not 1 <= len(self.subject) <= 512 or '\x00' in self.subject:
            raise ValueError('Identity subject must be a stable non-empty server-side ID')
        if self.namespace == 'nickname' and not re.fullmatch(r'[a-f0-9]{64}', self.subject):
            raise ValueError('Nickname principal must contain the browser credential digest')
        if self.display_name is not None and (
                not isinstance(self.display_name, str) or not 1 <= len(self.display_name.strip()) <= 30
                or any(ord(c) < 32 or ord(c) == 127 for c in self.display_name)):
            raise ValueError('Display name must contain 1–30 printable characters')
        if self.display_name is not None:
            object.__setattr__(self, 'display_name', self.display_name.strip())

    @property
    def storage_key(self) -> str:
        # Preserve existing nickname-owned rows when enabling this adapter layer.
        if self.namespace == 'nickname':
            return self.subject
        return hashlib.sha256((self.namespace + '\x00' + self.subject).encode('utf-8')).hexdigest()

    @property
    def authenticated(self) -> bool:
        return self.namespace != 'nickname'


class IdentityProvider(Protocol):
    mode: str
    login_path: str | None

    def resolve(self, headers: Mapping[str, str], *, required: bool = False) -> Principal | None:
        """Verify trusted credentials. Raise IdentityError(401, ...) if required but absent."""
        ...


class NicknameIdentityProvider:
    mode = 'nickname'
    login_path = None

    def resolve(self, headers: Mapping[str, str], *, required: bool = False) -> Principal | None:
        authorization = headers.get('Authorization', '')
        if not authorization:
            if required:
                raise IdentityError(401, '请在可保存数据的浏览器中发布。')
            return None
        match = re.fullmatch(r'(?i:Bearer) ([a-f0-9]{64})', authorization)
        if not match:
            raise IdentityError(401, '发布凭证无效，请重新打开页面。')
        return Principal('nickname', hashlib.sha256(match[1].encode('ascii')).hexdigest())
