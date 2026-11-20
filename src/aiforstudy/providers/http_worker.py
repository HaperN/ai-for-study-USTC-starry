"""One HTTP exchange. Connect timeout and model response deadline are distinct.

Credentials arrive only over stdin; the parent owns cancellation and total deadline.
Redirects are not followed and provider error bodies are never logged.
"""
import http.client
import json
import re
import ssl
import sys
from urllib.parse import urlsplit


def main():
    request = json.load(sys.stdin)
    url = urlsplit(request['url'])
    connection = (http.client.HTTPSConnection if url.scheme == 'https' else http.client.HTTPConnection)(
        url.hostname, url.port, timeout=request['connect_timeout'])
    phase = 'connect'
    status = None
    try:
        connection.connect()
        connection.sock.settimeout(request.get('read_timeout', 300))
        path = (url.path or '/') + ('?' + url.query if url.query else '')
        phase = 'send_request'
        connection.request('POST', path, body=json.dumps(request['body']).encode(),
            headers={'Content-Type':'application/json', **({'Authorization':'Bearer ' + request['key']} if request['key'] else {})})
        phase = 'receive_headers'
        response = connection.getresponse()
        status = response.status
        if response.status >= 300:
            result = {'error':'ConnectionError' if response.status == 429 or response.status >= 500 else 'ConfigError',
                      'message':f'模型服务 HTTP {response.status}',
                      'transport_details': {'phase': phase, 'http_status': status}}
        else:
            expected = response.length
            capacity = 32 * 1024 * 1024
            phase = 'read_body'
            raw = response.read(capacity + 1)
            if len(raw) > capacity:
                raise ValueError('HTTP response exceeds capacity')
            # A sized HTTPResponse.read() can return EOF without raising for a
            # short Content-Length body. Check framing before parsing model JSON.
            if expected is not None and len(raw) < expected and response.length:
                raise http.client.IncompleteRead(raw, expected - len(raw))
            phase = 'parse_response'
            body = json.loads(raw.decode())
            choices = body.get('choices') or []
            choice = choices[0] if choices else {}
            text = str((choice.get('message') or {}).get('content') or '')
            from .response import response_metadata
            result = {'text':text, 'usage':body.get('usage') or {},
                      'metadata':response_metadata({'finish_reason':choice.get('finish_reason'),
                          'response_id':body.get('id'), 'model':body.get('model')})}
    except Exception as exc:
        kind = ('TimeoutError' if isinstance(exc, TimeoutError) else
                'ConnectionError' if isinstance(exc, (OSError, http.client.HTTPException)) else 'ContractError')
        # Only transport metadata, never str(exc): exceptions may embed URLs,
        # headers, partial response bytes or other private request material.
        details = {'phase': phase, 'cause_type': type(exc).__name__}
        if status is not None:
            details['http_status'] = status
        if type(getattr(exc, 'errno', None)) is int:
            details['errno'] = exc.errno
        if isinstance(exc, ssl.SSLError):
            reason = getattr(exc, 'reason', '')
            if isinstance(reason, str) and re.fullmatch(r'[A-Z0-9_]{1,80}', reason):
                details['ssl_reason'] = reason
        result = {'error':kind, 'message':f'模型请求失败：{type(exc).__name__}',
                  'cause_type':type(exc).__name__, 'transport_details': details}
    finally:
        connection.close()
    sys.stdout.write(json.dumps(result))


if __name__ == '__main__':
    main()
