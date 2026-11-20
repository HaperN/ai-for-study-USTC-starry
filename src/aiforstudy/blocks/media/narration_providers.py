"""Provider-specific, non-streaming TTS contracts. No credentials in receipts.

Both providers are asked for PCM WAV. Payloads, decoded frames and timestamps
are checked before publishing a file. Missing alignment stays missing; timings
are provider observations, not evidence of correct pronunciation or mathematics.
"""
from copy import deepcopy
import base64
import binascii
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
import wave

from ...errors import ConfigError

VERSION = 'provider-narration-1'
MAX_AUDIO_BYTES = 32 * 1024 * 1024
MAX_JSON_BYTES = MAX_AUDIO_BYTES * 2 + 2 * 1024 * 1024


def identity(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False).encode()).hexdigest()


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward a subscription key to another host.


def _https(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ConfigError('TTS endpoint/subtitle URL must be HTTPS without embedded credentials')
    return url


def _request_json(request):
    try:
        with build_opener(_NoRedirect()).open(request, timeout=180) as response:
            raw = response.read(MAX_JSON_BYTES + 1)
    except HTTPError as exc:
        raise ValueError(f'TTS HTTP {exc.code}; check provider credentials, quota and request settings') from None
    except (URLError, OSError, TimeoutError):
        raise ValueError('TTS network request failed; no automatic provider fallback') from None
    if not raw or len(raw) > MAX_JSON_BYTES:
        raise ValueError('TTS JSON response is empty or exceeds the size limit')
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        raise ValueError('TTS returned malformed JSON') from None


def _wave_info(data):
    if not data or len(data) > MAX_AUDIO_BYTES:
        raise ValueError('TTS audio is empty or exceeds 32 MiB')
    try:
        with wave.open(io.BytesIO(data), 'rb') as voice:
            rate, count = voice.getframerate(), voice.getnframes()
            if (voice.getcomptype() != 'NONE' or voice.getsampwidth() != 2
                    or voice.getnchannels() != 1 or not 16000 <= rate <= 48000
                    or not 0 < count / rate <= 1800):
                raise ValueError('TTS WAV must be bounded, mono 16-bit PCM')
            if len(voice.readframes(count)) != count * 2:
                raise ValueError('TTS WAV contains truncated PCM')
    except (wave.Error, EOFError):
        raise ValueError('TTS response is not a valid PCM WAV') from None
    return {'sample_rate': rate, 'sample_count': count, 'duration_seconds': count / rate}


def _time(value, scale, duration):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('TTS alignment time must be a finite numeric value')
    seconds = value / scale
    if not 0 <= seconds <= duration:
        raise ValueError('TTS alignment lies outside decoded audio')
    return seconds


def _token(text, begin, end, start, finish, scale, duration):
    missing_offsets = begin is None and end is None
    if (not isinstance(text, str) or not text or (not missing_offsets and
            (type(begin) is not int or type(end) is not int or not 0 <= begin < end))):
        raise ValueError('TTS alignment text or offsets are malformed')
    a, b = _time(start, scale, duration), _time(finish, scale, duration)
    if b < a:
        raise ValueError('TTS alignment end precedes its start')
    return {'text': text, 'text_start': begin, 'text_end': end,
            'start_seconds': a, 'end_seconds': b}


def _track(tokens, *, basis, granularity, text, offset_unit):
    last_start, last_end, last_offset = 0, 0, 0
    for token in tokens:
        if (token['start_seconds'] < last_start or token['end_seconds'] < last_end
                or (token['text_start'] is not None and token['text_start'] < last_offset)):
            raise ValueError('TTS alignment is not monotonic')
        last_start, last_end = token['start_seconds'], token['end_seconds']
        if token['text_end'] is not None:
            last_offset = token['text_end']
    matches = bool(tokens) and all(t['text_start'] is not None and text[t['text_start']:t['text_end']] == t['text'] for t in tokens)
    return {'text_basis': basis, 'granularity': granularity, 'offset_unit': offset_unit,
            'canonical_match': matches, 'tokens': tokens}


def _alignment(tracks):
    return {'status': 'available' if any(t['tokens'] for t in tracks) else 'unavailable',
            'time_unit': 'seconds', 'clock': 'segment_audio', 'tracks': tracks,
            'content_review': 'not_verified_by_alignment'}


def _eleven_alignment(response, text, duration):
    tracks = []
    for field, basis in [('alignment', 'provider_input'), ('normalized_alignment', 'provider_normalized')]:
        raw = response.get(field)
        if raw is None:
            continue
        if not isinstance(raw, dict):
            raise ValueError('Eleven alignment must be an object')
        arrays = [raw.get(key) for key in ('characters', 'character_start_times_seconds', 'character_end_times_seconds')]
        if any(not isinstance(a, list) for a in arrays) or len({len(a) for a in arrays}) != 1:
            raise ValueError('Eleven character alignment arrays have unequal lengths')
        tokens, offset = [], 0
        for char, start, end in zip(*arrays):
            if not isinstance(char, str) or not char:
                raise ValueError('Eleven alignment contains an empty character')
            tokens.append(_token(char, offset, offset + len(char), start, end, 1, duration))
            offset += len(char)
        track = _track(tokens, basis=basis, granularity='character', text=text,
                       offset_unit='unicode_codepoints_half_open')
        # Full equality avoids mapping a normalized prefix onto a longer input.
        track['canonical_match'] = ''.join(arrays[0]) == text and bool(tokens)
        tracks.append(track)
    return _alignment(tracks)


def _minimax_alignment(rows, text, duration):
    if not isinstance(rows, list):
        raise ValueError('MiniMax subtitle file must contain a JSON array')
    sentences, words = [], []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('MiniMax subtitle entry must be an object')
        sentences.append(_token(row.get('text'), row.get('text_begin'), row.get('text_end'),
            row.get('time_begin'), row.get('time_end'), 1000, duration))
        children = row.get('timestamped_words') or []
        if not isinstance(children, list):
            raise ValueError('MiniMax timestamped_words must be an array')
        for word in children:
            if not isinstance(word, dict):
                raise ValueError('MiniMax word entry must be an object')
            token = _token(word.get('word'), word.get('word_begin'), word.get('word_end'),
                word.get('time_begin'), word.get('time_end'), 1000, duration)
            if (token['start_seconds'] < sentences[-1]['start_seconds']
                    or token['end_seconds'] > sentences[-1]['end_seconds']):
                raise ValueError('MiniMax word lies outside its subtitle segment')
            words.append(token)
    return _alignment([_track(tokens, basis='provider_synthesized', granularity=kind, text=text,
        offset_unit='provider_characters_half_open') for kind, tokens in [('sentence', sentences), ('word', words)] if tokens])


class PremiumTTSBackend:
    """Configured through the existing narration factory; supports old synthesize callers."""
    def __init__(self, config):
        self.config = deepcopy(config)
        kind = config['backend']
        common = {'backend', 'enabled', 'voice', 'model', 'endpoint', 'token_env', 'stream', 'dictionary_revision'}
        supported = ({'pronunciation_dict', 'speed', 'volume', 'pitch', 'subtitle_type'} if kind == 'minimax'
                     else {'pronunciation_dictionary_locators', 'voice_settings'})
        if set(config) - common - supported:
            raise ConfigError('Unsupported premium narration setting; use token_env for credentials')
        if config.get('stream', False) is not False:
            raise ConfigError('Premium narration currently supports non-streaming synthesis only')
        self.kind = kind
        self.voice = config.get('voice')
        if not isinstance(self.voice, str) or not self.voice.strip():
            raise ConfigError('Premium narration requires an explicit voice ID')
        self.model = config.get('model', 'speech-2.8-hd' if kind == 'minimax' else 'eleven_v4')
        if self.model != ('speech-2.8-hd' if kind == 'minimax' else 'eleven_v4'):
            raise ConfigError('This adapter requires speech-2.8-hd or eleven_v4 respectively')
        self.token_env = config.get('token_env', 'MINIMAX_API_KEY' if kind == 'minimax' else 'ELEVENLABS_API_KEY')
        if not isinstance(self.token_env, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', self.token_env):
            raise ConfigError('token_env must name an environment variable')
        self.endpoint = _https(config.get('endpoint', 'https://api.minimax.io/v1/t2a_v2' if kind == 'minimax'
                                         else 'https://api.elevenlabs.io/v1/text-to-speech'))
        if urlsplit(self.endpoint).query:
            raise ConfigError('Premium endpoint must not contain query parameters')
        self.dictionary = config.get('pronunciation_dict', {}) if kind == 'minimax' else config.get('pronunciation_dictionary_locators', [])
        if kind == 'minimax':
            if (not isinstance(self.dictionary, dict) or set(self.dictionary) - {'tone'}
                    or not isinstance(self.dictionary.get('tone', []), list)
                    or any(not isinstance(t, str) or '/' not in t or not all(t.split('/', 1)) for t in self.dictionary.get('tone', []))):
                raise ConfigError('MiniMax pronunciation_dict.tone requires original/replacement strings')
            if config.get('subtitle_type', 'word') not in ('sentence', 'word'):
                raise ConfigError('word_streaming requires streaming and is not supported here')
            for key, default, low, high in [('speed', 1, .5, 2), ('volume', 1, 0, 10), ('pitch', 0, -12, 12)]:
                value = config.get(key, default)
                if (type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high
                        or (key == 'volume' and value <= 0) or (key == 'pitch' and type(value) is not int)):
                    raise ConfigError('MiniMax '+key+' is outside its supported range')
        else:
            if (not isinstance(self.dictionary, list) or len(self.dictionary) > 3
                    or any(not isinstance(d, dict) or set(d) != {'pronunciation_dictionary_id', 'version_id'}
                           or not all(isinstance(v, str) and v.strip() for v in d.values()) for d in self.dictionary)):
                raise ConfigError('Eleven dictionaries require pinned pronunciation_dictionary_id and version_id (maximum 3)')
            settings = config.get('voice_settings', {})
            if not isinstance(settings, dict) or set(settings) - {'stability', 'similarity_boost', 'use_speaker_boost', 'clone_strength'}:
                raise ConfigError('Unsupported Eleven v4 voice settings; speed/style are not applied')
            for key, value in settings.items():
                if key == 'use_speaker_boost':
                    valid = type(value) is bool
                else:
                    valid = type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1
                if not valid:
                    raise ConfigError('Invalid Eleven voice setting: '+key)

    @property
    def source(self):
        # No environment values are read while hashing/configuring a backend.
        public = {k: v for k, v in self.config.items() if k not in ('token_env', 'endpoint', 'enabled')}
        return {'backend': self.kind, 'model': self.model, 'voice': self.voice, 'adapter_version': VERSION,
                'endpoint_hash': identity(self.endpoint), 'settings_sha256': identity(public),
                'dictionary_sha256': identity(self.dictionary), 'dictionary_revision': self.config.get('dictionary_revision'),
                'language': 'zh', 'output_format': 'pcm_wav', 'timing_source': 'provider_response'}

    def synthesize(self, text, destination):
        token = os.environ.get(self.token_env, '').strip()
        if not token:
            raise ConfigError(f'{self.kind} narration requires environment variable {self.token_env}; no fallback was used')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Premium narration requires nonempty spoken text')
        headers = {'Content-Type': 'application/json'}
        if self.kind == 'minimax':
            if len(text) >= 10000:
                raise ValueError('MiniMax non-streaming segment must contain fewer than 10000 characters')
            headers['Authorization'] = 'Bearer '+token
            endpoint = self.endpoint
            payload = {'model': self.model, 'text': text, 'stream': False, 'output_format': 'hex',
                'language_boost': 'Chinese', 'subtitle_enable': True, 'subtitle_type': self.config.get('subtitle_type', 'word'),
                'voice_setting': {'voice_id': self.voice, 'speed': self.config.get('speed', 1),
                                  'vol': self.config.get('volume', 1), 'pitch': self.config.get('pitch', 0)},
                'audio_setting': {'format': 'wav', 'sample_rate': 24000, 'channel': 1},
                'pronunciation_dict': self.dictionary}
        else:
            headers['xi-api-key'] = token
            endpoint = self.endpoint.rstrip('/')+'/'+quote(self.voice, safe='')+'/with-timestamps?output_format=wav_24000'
            payload = {'model_id': self.model, 'text': text, 'language_code': 'zh',
                       'pronunciation_dictionary_locators': self.dictionary}
            if self.config.get('voice_settings'):
                payload['voice_settings'] = self.config['voice_settings']
        response = _request_json(Request(endpoint, data=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode(),
                                         headers=headers, method='POST'))
        if not isinstance(response, dict):
            raise ValueError('TTS response must be a JSON object')
        if self.kind == 'minimax':
            status = response.get('base_resp')
            if not isinstance(status, dict) or type(status.get('status_code')) is not int:
                raise ValueError('MiniMax response lacks base_resp.status_code')
            if status['status_code'] != 0:
                raise ValueError(f"MiniMax API error {status['status_code']}; check provider account and request")
            data = response.get('data')
            if not isinstance(data, dict) or data.get('status') != 2:
                raise ValueError('MiniMax did not return completed non-streaming audio')
            try:
                audio = bytes.fromhex(data['audio'])
            except (KeyError, TypeError, ValueError):
                raise ValueError('MiniMax audio is not valid hexadecimal data') from None
        else:
            try:
                audio = base64.b64decode(response['audio_base64'], validate=True)
            except (KeyError, TypeError, ValueError, binascii.Error):
                raise ValueError('Eleven audio_base64 is missing or invalid') from None
        info = _wave_info(audio)
        if self.kind == 'minimax':
            subtitle_url = data.get('subtitle_file')
            alignment = (_minimax_alignment(_request_json(Request(_https(subtitle_url))), text, info['duration_seconds'])
                         if subtitle_url else _alignment([]))
        else:
            alignment = _eleven_alignment(response, text, info['duration_seconds'])
        Path(destination).write_bytes(audio)
        return {**info, 'alignment': alignment}
