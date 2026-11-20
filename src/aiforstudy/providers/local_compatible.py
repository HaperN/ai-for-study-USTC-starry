"""Explicit OpenAI-compatible local endpoint, independent of cloud credentials."""
from urllib.parse import urlsplit
from ipaddress import ip_address
from .transport import RealTransport, _messages_with_images, image_payloads
from ..errors import ConfigError


def endpoint_identity(url):
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ConfigError('Endpoint requires HTTP(S), host, and no embedded credentials/query/fragment')
    return url.rstrip('/')


class LocalCompatibleTransport(RealTransport):
    def __post_init__(self):
        self.base_url = endpoint_identity(self.base_url)
        host = urlsplit(self.base_url).hostname
        try:
            loopback = ip_address(host).is_loopback
        except ValueError:
            loopback = host == 'localhost'
        if not self.api_key and not loopback:
            raise ConfigError('Unauthenticated local endpoint must be loopback')
        self._key_ref = 'explicit' if self.api_key else 'none'

    @property
    def cache_identity(self):
        return {'provider':'local-compatible', 'endpoint':self.base_url, 'version':1}

    def request_body(self, *, messages, images=None, max_tokens=1024, effort='high', model=''):
        self.last_image_payloads = image_payloads(images or [])
        return {'model':model or self.model, 'messages':_messages_with_images(messages, images or [], max_tokens), 'max_tokens':max(1, int(max_tokens or 1024))}
