"""Isolated supported Harness SDK composition. Secrets never enter Python or argv."""
from dataclasses import dataclass
from pathlib import Path
import json
import os
import re
import subprocess
import tempfile
import time
from .transport import _messages_with_images, image_payloads, reasoning_options
from .response import TransportResponse
from ..errors import ConfigError, Cancelled, BudgetExceeded
from ..core.process_io import communicate_bounded


def raise_bridge_error(payload):
    code = payload.get('code', '')
    code = code if re.fullmatch(r'[A-Z_]{1,80}', str(code)) else 'ACCOUNT_BRIDGE_FAILED'
    if code in ('ABORTED', 'CANCELLED'):
        raise Cancelled(code)
    if code == 'TIMEOUT':
        raise TimeoutError(code)
    if code in ('TRANSPORT', 'SERVER', 'RATE_LIMIT'):
        raise ConnectionError(code)
    raise ConfigError(code)


def normalize_usage(payload):
    fields = ('inputTokens', 'outputTokens', 'cacheReadTokens', 'cacheWriteTokens')
    supplied = {k:v for k,v in payload.items() if k in fields and type(v) is int and v >= 0}
    if not supplied:
        return {}
    result = {}
    if 'inputTokens' in supplied:
        prompt = supplied['inputTokens'] + supplied.get('cacheReadTokens', 0) + supplied.get('cacheWriteTokens', 0)
        result.update(prompt_tokens=prompt, prompt_cache_miss_tokens=supplied['inputTokens'])
    if 'cacheReadTokens' in supplied:
        result['prompt_cache_hit_tokens'] = supplied['cacheReadTokens']
    if 'cacheWriteTokens' in supplied:
        result['prompt_cache_write_tokens'] = supplied['cacheWriteTokens']
    if 'outputTokens' in supplied:
        result['completion_tokens'] = supplied['outputTokens']
    if 'prompt_tokens' in result and 'completion_tokens' in result:
        result['total_tokens'] = result['prompt_tokens'] + result['completion_tokens']
    return result


@dataclass
class DshAccountTransport:
    model: str
    workspace_root: Path | None = None
    harness_app: str = '/Applications/DeepSeek Harness.app'
    account_home: Path | None = None
    cancel: object | None = None
    cancel_grace_seconds: float = 2.0
    connect_timeout_seconds: float = 15.0
    calls: int = 0
    image_calls: int = 0
    last_image_payloads: tuple = ()
    last_remote_unconfirmed: bool = False

    @property
    def cache_identity(self):
        return {'provider':'deepseek-account', 'endpoint':'https://api.deepseek.com', 'bridge_version':1}

    def request_body(self, *, messages, images=None, model='', effort='high', max_tokens=1024):
        for message in messages:
            if message.get('role') not in ('system','user','assistant') or set(message) - {'role','content','image_labels'}:
                raise ConfigError('Unsupported account message role or metadata')
            content = message.get('content')
            if not isinstance(content, (str,list)):
                raise ConfigError('Unsupported account content')
            if isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict) or part.get('type') != 'text' or not isinstance(part.get('text'), str):
                        raise ConfigError('Images must be supplied as local image files; audio/tools unsupported')
        self.last_image_payloads = image_payloads(images or [])
        return {'model':model or self.model, 'messages':_messages_with_images(messages, images or [], max_tokens), 'max_tokens':max(1,int(max_tokens or 1024)), 'reasoning_effort':reasoning_options(effort)['reasoning_effort']}

    def __call__(self, *, model, effort, messages, images, max_tokens, timeout):
        if self.cancel:
            self.cancel.checkpoint()
        body = self.request_body(model=model, effort=effort, messages=messages, images=images, max_tokens=max_tokens)
        executable = Path(self.harness_app)/'Contents/MacOS/DeepSeek Harness'
        if not executable.is_file() or not Path('/usr/bin/sandbox-exec').is_file():
            raise ConfigError('ACCOUNT_BRIDGE_RUNTIME_UNAVAILABLE')
        original = (self.account_home or Path.home()/'.dsh').resolve()
        deadline = None if timeout is None else time.monotonic()+timeout
        self.last_remote_unconfirmed = False
        with tempfile.TemporaryDirectory(prefix='aifs-account-') as directory:
            os.chmod(directory, 0o700)
            env = dict(os.environ, ELECTRON_RUN_AS_NODE='1', DSH_HOME=directory)
            payload = dict(body, account_home=str(original), isolated_home=directory, harness_app=self.harness_app)
            policy = '(version 1)(allow default)(deny file-write* (subpath '+json.dumps(str(original))+'))'
            process = subprocess.Popen(['/usr/bin/sandbox-exec','-p',policy,str(executable),str(Path(__file__).with_name('dsh_bridge.cjs'))], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, cwd=directory)
            try:
                output,_ = communicate_bounded(process,json.dumps(payload).encode(),deadline=deadline,checkpoint=self.cancel.checkpoint if self.cancel else None)
                if process.returncode:
                    raise ConfigError('ACCOUNT_BRIDGE_PROCESS_FAILED')
                result = json.loads(output)
                if 'error' in result:
                    raise_bridge_error(result['error'])
                self.calls += 1
                self.image_calls += bool(images)
                return TransportResponse(result['text'], normalize_usage(result.get('usage',{})), metadata={'model':model,'finish_reason':result['finish_reason']})
            except (Cancelled, BudgetExceeded, TimeoutError, ConnectionError):
                self.last_remote_unconfirmed = True
                raise
            finally:
                if process.poll() is None:
                    process.terminate()
                    try: process.wait(timeout=min(2,self.cancel_grace_seconds))
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait(timeout=1)
                for stream in (process.stdin, process.stdout):
                    if stream: stream.close()

    def report(self):
        return {'calls':self.calls,'image_calls':self.image_calls,'provider':'deepseek-account', 'last_image_hashes':[p.sha256 for p in self.last_image_payloads]}
