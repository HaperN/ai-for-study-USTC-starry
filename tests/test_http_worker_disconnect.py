"""Real local HTTP disconnects are transport failures, never review protocol tickets."""
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest

from aiforstudy.errors import ContractError
from aiforstudy.providers.review_failures import classify_review_failure
from aiforstudy.providers.transport import BASE_URL_ENV, RealTransport


@pytest.mark.parametrize('wire',['chunked','content_length','complete_invalid_json'])
def test_http_framing_failure_is_distinct_from_complete_invalid_json(monkeypatch,wire):
    monkeypatch.delenv(BASE_URL_ENV,raising=False)
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            body=b'{"choices":['
            self.send_response(200)
            if wire=='chunked': self.send_header('Transfer-Encoding','chunked')
            else: self.send_header('Content-Length',str(len(body)+(20 if wire=='content_length' else 0)))
            self.end_headers()
            self.wfile.write(b'40\r\n'+body if wire=='chunked' else body)
            self.wfile.flush();self.close_connection=True
        def log_message(self,*args): pass
    server=HTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        transport=RealTransport(model='offline',api_key='offline-placeholder',base_url=f'http://127.0.0.1:{server.server_port}')
        expected=ContractError if wire=='complete_invalid_json' else ConnectionError
        with pytest.raises(expected) as error:
            transport(model='offline',effort='off',messages=[],images=[],max_tokens=1,timeout=3)
        assert classify_review_failure(error.value)==('protocol' if wire=='complete_invalid_json' else 'network')
        details = error.value.transport_details
        assert details['phase'] == ('parse_response' if wire=='complete_invalid_json' else 'read_body')
        assert details['http_status'] == 200
        assert 'http_status=200' in str(error.value)
        assert 'offline-placeholder' not in str(error.value)
        assert '{"choices":[' not in str(error.value)
        assert transport.calls==0
    finally:
        server.shutdown();server.server_close();thread.join(2)
