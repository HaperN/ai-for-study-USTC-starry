"""Offline provider contracts: generated tones are fixtures, never voice-quality evidence."""
import base64
from copy import deepcopy
import io
import json
from pathlib import Path
import wave

import pytest
from aiforstudy.blocks.media import narration as n
from aiforstudy.errors import ConfigError


def wav_bytes(seconds=1):
    target = io.BytesIO()
    with wave.open(target, 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(24000)
        stream.writeframes(b'\x40\x10\xc0\xef' * int(12000 * seconds))
    return target.getvalue()


def install_transport(monkeypatch, replies):
    from aiforstudy.blocks.media import narration_providers as p
    requests = []
    class Response(io.BytesIO):
        pass
    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return Response(json.dumps(reply).encode())
    monkeypatch.setattr(p, 'build_opener', lambda *args: Opener())
    return requests


def eleven_response(text='负一。'):
    return {'audio_base64': base64.b64encode(wav_bytes()).decode(), 'alignment': {
        'characters': list(text), 'character_start_times_seconds': [0, .2, .4],
        'character_end_times_seconds': [.2, .4, .6]}}


def test_minimax_real_request_hex_wave_and_millisecond_subtitle(monkeypatch, tmp_path):
    monkeypatch.setenv('TEST_TTS_TOKEN', 'fixture-secret')
    response = {'base_resp': {'status_code': 0}, 'data': {'status': 2,
        'audio': wav_bytes().hex(), 'subtitle_file': 'https://cdn.minimax.io/subtitle.json'}}
    subtitles = [{'text': '负一。', 'text_begin': 0, 'text_end': 3, 'time_begin': 0,
                  'time_end': 600, 'timestamped_words': [
                      {'word': '负一', 'word_begin': 0, 'word_end': 2, 'time_begin': 0, 'time_end': 400}]}]
    calls = install_transport(monkeypatch, [response, subtitles])
    backend = n.configured_backend({'backend': 'minimax', 'voice': 'teacher',
        'token_env': 'TEST_TTS_TOKEN', 'pronunciation_dict': {'tone': ['辐角/幅角']}, 'dictionary_revision': 'math-1'})
    result = backend.synthesize('负一。', tmp_path/'voice.wav')
    payload = json.loads(calls[0].data)
    assert payload['model'] == 'speech-2.8-hd'
    assert payload['voice_setting']['voice_id'] == 'teacher'
    assert payload['audio_setting']['format'] == 'wav'
    assert payload['subtitle_type'] == 'word' and payload['stream'] is False
    assert payload['pronunciation_dict'] == {'tone': ['辐角/幅角']}
    assert calls[0].get_header('Authorization') == 'Bearer fixture-secret'
    assert calls[1].get_header('Authorization') is None
    assert result['alignment']['tracks'][0]['tokens'][0]['end_seconds'] == .6
    assert result['alignment']['tracks'][1]['tokens'][0]['end_seconds'] == .4
    assert 'fixture-secret' not in json.dumps(backend.source)
    with wave.open(str(tmp_path/'voice.wav')) as voice:
        assert voice.getnframes() == 24000


def test_eleven_v4_seconds_and_distinct_normalized_basis(monkeypatch, tmp_path):
    monkeypatch.setenv('TEST_TTS_TOKEN', 'fixture-secret')
    response = eleven_response()
    response['normalized_alignment'] = {**response['alignment'], 'characters': ['负', '壹', '。']}
    calls = install_transport(monkeypatch, [response])
    config = {'backend': 'elevenlabs', 'voice': 'teacher', 'token_env': 'TEST_TTS_TOKEN',
        'pronunciation_dictionary_locators': [{'pronunciation_dictionary_id': 'math', 'version_id': 'v1'}]}
    backend = n.configured_backend(config)
    result = backend.synthesize('负一。', tmp_path/'voice.wav')
    payload = json.loads(calls[0].data)
    assert payload['model_id'] == 'eleven_v4' and payload['language_code'] == 'zh'
    assert '/teacher/with-timestamps?output_format=wav_24000' in calls[0].full_url
    assert calls[0].get_header('Xi-api-key') == 'fixture-secret'
    assert payload['pronunciation_dictionary_locators'] == config['pronunciation_dictionary_locators']
    assert result['alignment']['tracks'][0]['tokens'][1]['start_seconds'] == .2
    assert result['alignment']['tracks'][0]['canonical_match'] is True
    assert result['alignment']['tracks'][1]['canonical_match'] is False


@pytest.mark.parametrize('backend', ['minimax', 'elevenlabs'])
def test_missing_key_is_explicit_configuration_error_without_network(monkeypatch, tmp_path, backend):
    monkeypatch.delenv('NEVER_CONFIGURED_TTS_KEY', raising=False)
    calls = install_transport(monkeypatch, [])
    adapter = n.configured_backend({'backend': backend, 'voice': 'teacher', 'token_env': 'NEVER_CONFIGURED_TTS_KEY'})
    with pytest.raises(ConfigError, match='NEVER_CONFIGURED_TTS_KEY'):
        adapter.synthesize('负一。', tmp_path/'voice.wav')
    assert not calls and not (tmp_path/'voice.wav').exists()


@pytest.mark.parametrize('mutation', ['bad_audio', 'truncated_wav', 'bad_base64', 'unequal_lengths', 'nan', 'boolean', 'out_of_audio', 'reversed'])
def test_eleven_rejects_malformed_audio_or_alignment(monkeypatch, tmp_path, mutation):
    monkeypatch.setenv('TEST_TTS_TOKEN', 'fixture-secret')
    response = eleven_response()
    if mutation == 'bad_audio': response['audio_base64'] = base64.b64encode(b'{"error":"oops"}').decode()
    elif mutation == 'truncated_wav': response['audio_base64'] = base64.b64encode(wav_bytes()[:-20]).decode()
    elif mutation == 'bad_base64': response['audio_base64'] = 'not-base64!!'
    elif mutation == 'unequal_lengths': response['alignment']['characters'].pop()
    else:
        response['alignment']['character_end_times_seconds'][0] = {'nan': float('nan'), 'boolean': True, 'out_of_audio': 4, 'reversed': -.1}[mutation]
    install_transport(monkeypatch, [response])
    with pytest.raises(ValueError):
        n.configured_backend({'backend': 'elevenlabs', 'voice': 'teacher', 'token_env': 'TEST_TTS_TOKEN'}).synthesize('负一。', tmp_path/'voice.wav')
    assert not (tmp_path/'voice.wav').exists()


def test_provider_errors_do_not_expose_response_or_secrets(monkeypatch, tmp_path):
    from urllib.error import HTTPError
    monkeypatch.setenv('TEST_TTS_TOKEN', 'fixture-secret')
    install_transport(monkeypatch, [HTTPError('https://api.elevenlabs.io', 401, 'fixture-secret', {}, io.BytesIO(b'fixture-secret'))])
    with pytest.raises(ValueError, match='401') as error:
        n.configured_backend({'backend':'elevenlabs','voice':'teacher','token_env':'TEST_TTS_TOKEN'}).synthesize('负一。', tmp_path/'voice.wav')
    assert 'fixture-secret' not in str(error.value)
    install_transport(monkeypatch, [{'base_resp': {'status_code': 1004, 'status_msg': 'fixture-secret'}}])
    with pytest.raises(ValueError, match='1004') as error:
        n.configured_backend({'backend':'minimax','voice':'teacher','token_env':'TEST_TTS_TOKEN'}).synthesize('负一。', tmp_path/'voice.wav')
    assert 'fixture-secret' not in str(error.value)


@pytest.mark.parametrize('extra', [{'stream': True}, {'api_key': 'secret'}, {'pronunciation_dictionary_locators': [{'pronunciation_dictionary_id':'math'}]}, {'voice_settings': {'speed': .9}}])
def test_eleven_unsupported_or_unpinned_configuration_is_not_silently_ignored(extra):
    with pytest.raises(ConfigError):
        n.configured_backend({'backend': 'elevenlabs', 'voice': 'teacher', **extra})


def test_script_display_and_direction_survive_production_preparation(monkeypatch, tmp_path):
    monkeypatch.setenv('TEST_TTS_TOKEN', 'fixture-secret')
    calls = install_transport(monkeypatch, [eleven_response()])
    backend = n.configured_backend({'backend':'elevenlabs','voice':'teacher','token_env':'TEST_TTS_TOKEN'})
    step = {'step_id':'s1', 'caption': '显示旧标题', 'display_markdown': r'$a_{-1}$',
            'canonical_spoken_text': '负一。', 'spoken_review': {'status': 'approved', 'reviewer': 'teacher'},
            'director_notes': '强调负号，不要念出这条备注。'}
    result = n.prepare_step_audio([step], tmp_path, backend=backend)
    row = result['steps'][0]
    assert json.loads(calls[0].data)['text'] == '负一。'
    assert row['text'] == '负一。' and row['display_markdown'] == r'$a_{-1}$'
    assert row['director_notes'] == step['director_notes'] and row['direction_status'] == 'not_applied'
    assert row['sample_count'] == 24000 and row['alignment']['status'] == 'available'
    assert row['spoken_review'] == step['spoken_review']
    shifted = n.bind_narration_timeline(result, [{'step_id':'s1','start':2,'end':3.25}])
    cue = n.narration_anchor(shifted, step_id='s1', text_start=0, text_end=2, expected_text='负一')
    assert cue['status'] == 'aligned' and cue['start_seconds'] == 2 and cue['end_seconds'] == 2.4
    assert n.narration_anchor(shifted, step_id='s1', text_start=0, text_end=2, expected_text='正一')['status'] == 'unresolved'
    assert n.narration_anchor(shifted, step_id='missing', text_start=0, text_end=2, expected_text='负一')['status'] == 'unresolved'
    # Source script edits and pinned dictionary edits invalidate identity.
    changed = n.configured_backend({'backend':'elevenlabs','voice':'teacher','token_env':'TEST_TTS_TOKEN',
        'pronunciation_dictionary_locators':[{'pronunciation_dictionary_id':'math','version_id':'v2'}]})
    assert changed.source != backend.source
    assert row['script_sha256'] == n.script_identity(step)
    assert row['script_sha256'] != n.script_identity({**step, 'canonical_spoken_text': '正一。'})


def test_canonical_text_needs_review_and_is_not_run_through_latex_rewriter():
    with pytest.raises(ValueError, match='approved'):
        n.spoken_text({'canonical_spoken_text': '负一。'})
    assert n.spoken_text({'canonical_spoken_text': 'a下标负一。', 'spoken_review': {'status':'approved'}}) == 'a下标负一。'


def test_minimax_sentence_only_file_keeps_real_time_without_inventing_offsets(monkeypatch, tmp_path):
    monkeypatch.setenv('TEST_TTS_TOKEN', 'fixture-secret')
    response = {'base_resp': {'status_code': 0}, 'data': {'status': 2,
        'audio': wav_bytes().hex(), 'subtitle_file': 'https://cdn.minimax.io/subtitle.json'}}
    install_transport(monkeypatch, [response, [{'text':'负一。', 'time_begin':100, 'time_end':700}]])
    result = n.configured_backend({'backend':'minimax','voice':'teacher','token_env':'TEST_TTS_TOKEN'}).synthesize('负一。', tmp_path/'voice.wav')
    track = result['alignment']['tracks'][0]
    assert track['canonical_match'] is False
    assert track['tokens'][0]['start_seconds'] == .1
    assert track['tokens'][0]['text_start'] is None


def test_runtime_packaging_persists_actual_media_clock_and_display_text(monkeypatch, tmp_path):
    """Packaging boundary: stub media decode, inspect the actual published receipt bytes."""
    from types import SimpleNamespace
    from aiforstudy.blocks.media import code_runtime as runtime
    from aiforstudy.core.digest import digest_file
    from PIL import Image
    staging, output = tmp_path/'staging', tmp_path/'output'
    staging.mkdir(); (output/'source'/'narration').mkdir(parents=True)
    (staging/'video.mp4').write_bytes(b'fixture-video-boundary')
    (output/'source'/'narration'/'step-001.wav').write_bytes(wav_bytes())
    step = {'step_id':'s1','caption':'画图命令','display_markdown':r'$a_{-1}$','observation':'负一','expected_change':'高亮','author_step_index':0}
    monkeypatch.setattr(runtime, '_video', lambda *a, **kw: ({}, 1.25, 30))
    def frame(video, path, at, ffmpeg):
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (20, 20), 'blue').save(path)
        return {'variation': 2}
    monkeypatch.setattr(runtime, '_frame', frame)
    technical = {'narration': {'source': {'backend':'test'}, 'steps': [{'step_id':'s1', 'duration':1, 'text':'负一。'}]}}
    resources, steps = runtime._animation_resources({'steps':[step]}, staging, output,
        {'steps':[{'step_id':'s1','start':0,'end':1.25}]}, SimpleNamespace(ffmpeg='unused'), {'frame_rate':24}, technical)
    receipt = json.loads((output/'source'/'narration.json').read_text())
    assert receipt['steps'][0]['timeline'] == {'start_seconds':0,'end_seconds':1.25,'time_unit':'seconds','clock':'media'}
    assert '$a_{-1}$' in (output/'captions.vtt').read_text()
    resource = next(r for r in resources if r['kind']=='narration')
    assert resource['sha256'] == digest_file(output/'source'/'narration.json')


@pytest.mark.parametrize('kind', ['minimax', 'elevenlabs'])
def test_configured_premium_adapter_reaches_existing_worker_boundary(monkeypatch, tmp_path, kind):
    from types import SimpleNamespace
    from aiforstudy.blocks.media import code_runtime as runtime
    monkeypatch.setenv('TEST_TTS_TOKEN', 'fixture-secret')
    response = eleven_response() if kind == 'elevenlabs' else {
        'base_resp': {'status_code':0}, 'data': {'status':2, 'audio':wav_bytes().hex()}}
    calls = install_transport(monkeypatch, [response])
    services = SimpleNamespace(worker_dir=Path(__file__).resolve().parents[1]/'tools/manim_worker',
        scratch_root=tmp_path/'scratch', ffmpeg=n.resolve_ffmpeg(), worker_services=lambda:None)
    monkeypatch.setattr(runtime, '_validate_program', lambda program, context:program)
    monkeypatch.setattr(runtime, 'default_services', lambda **kw:services)
    monkeypatch.setattr(runtime, 'limits_for_profile', lambda *a:SimpleNamespace(timeout_seconds=120,
        cpu_cores=1, memory_bytes=1024**3, output_bytes=128*1024**2))
    monkeypatch.setattr(runtime, 'probe_isolation', lambda **kw:{'outcome':{'status':'ok'},
        'isolation':{'safe':True,'applied':{'landlock':{'backend':'test'}}}})
    monkeypatch.setattr(runtime, 'environment_hash', lambda *a, **kw:'fixture-environment')
    captured = []
    def dispatch(**kwargs):
        captured.append(kwargs['job'])
        return {'status':'blocked','reason':'fixture dispatch boundary'}
    monkeypatch.setattr(runtime, 'run_worker_job', dispatch)
    result = runtime.render_generated_code({'backend':'manim','code':'never execute on host',
        'steps':[{'step_id':'s1','canonical_spoken_text':'负一。','spoken_review':{'status':'approved'}}]},
        context={'request':{'kind':'animation'}}, out_dir=tmp_path/'out',
        narration={'enabled':True, 'backend':kind, 'voice':'teacher','token_env':'TEST_TTS_TOKEN'})
    assert result['reason'] == 'fixture dispatch boundary'
    assert len(captured) == len(calls) == 1
    assert captured[0]['narration']['source']['backend'] == kind
    assert Path(captured[0]['narration']['steps'][0]['path']).is_file()
    assert 'fixture-secret' not in json.dumps(result) + json.dumps(captured)
    assert result['technical_report']['narration']['steps'][0]['sample_count'] == 24000


def test_runtime_isolation_gate_requires_full_macos_signal_boundary():
    from aiforstudy.blocks.media.code_runtime import _verified_isolation
    valid = {'safe':True, 'applied':{'seatbelt':{'backend':'seatbelt','signal_scope':'same-sandbox',
        'denied_syscalls':['setpgid','setsid','posix_spawn']}}}
    assert _verified_isolation(valid)
    for key in ('signal_scope', 'denied_syscalls'):
        incomplete = deepcopy(valid); incomplete['applied']['seatbelt'].pop(key)
        assert not _verified_isolation(incomplete)
    assert not _verified_isolation({**valid, 'safe':False})
    assert _verified_isolation({'safe':True,'applied':{'landlock':{'backend':'landlock'}}})


@pytest.mark.parametrize('response', [
    {'base_resp':{'status_code':0}, 'data':{'status':1, 'audio':'00'}},
    {'base_resp':{'status_code':0}, 'data':{'status':2, 'audio':'not-hex'}},
    {'base_resp':{'status_code':0}, 'data':{'status':2, 'audio':b'not-wave'.hex()}},
    {'base_resp':{'status_code':True}, 'data':{'status':2, 'audio':'00'}},
    {'data':{'status':2, 'audio':'00'}},
])
def test_minimax_rejects_unfinished_error_or_corrupt_audio(monkeypatch, tmp_path, response):
    monkeypatch.setenv('TEST_TTS_TOKEN', 'fixture-secret')
    install_transport(monkeypatch, [response])
    with pytest.raises(ValueError):
        n.configured_backend({'backend':'minimax','voice':'teacher','token_env':'TEST_TTS_TOKEN'}).synthesize('负一。', tmp_path/'voice.wav')
    assert not (tmp_path/'voice.wav').exists()


def test_normalized_text_cannot_supply_a_canonical_anchor(monkeypatch, tmp_path):
    monkeypatch.setenv('TEST_TTS_TOKEN', 'fixture-secret')
    response = eleven_response()
    response['normalized_alignment'] = response.pop('alignment')
    response['normalized_alignment']['characters'] = ['负','壹','。']
    install_transport(monkeypatch, [response])
    receipt = n.prepare_step_audio([{'step_id':'s1','canonical_spoken_text':'负一。',
        'spoken_review':{'status':'approved'}}], tmp_path,
        backend=n.configured_backend({'backend':'elevenlabs','voice':'teacher','token_env':'TEST_TTS_TOKEN'}))
    receipt = n.bind_narration_timeline(receipt, [{'step_id':'s1','start':0,'end':1.25}])
    cue = n.narration_anchor(receipt, step_id='s1', text_start=0, text_end=2, expected_text='负一')
    assert cue['status'] == 'unresolved' and 'start_seconds' not in cue


def test_prompt_does_not_allow_model_to_award_its_own_script_review():
    from aiforstudy.blocks.media.code_generation import media_code_prompt
    from test_media_code_contracts import approved_case
    content, approval, req, _ = approved_case('animation')
    payload = json.loads(media_code_prompt(req, content=content, approval=approval)[-1]['content'])
    properties = payload['output_json_schema']['$defs']['MediaCodeStepV1']['properties']
    assert 'spoken_review' not in properties and 'canonical_spoken_text' not in properties
    assert {'narration_text', 'display_markdown', 'director_notes'} <= set(properties)


def test_fixed_runtime_preflight_explicitly_disables_narration(monkeypatch, tmp_path):
    from aiforstudy.blocks.media import code_runtime, validation
    seen = []
    def render(program, **kwargs):
        seen.append(kwargs)
        return {'status':'rendered','reason':'fixture', 'technical_report':{
            'environment':{'tools':{'ffmpeg':{'available':True}}}}}
    monkeypatch.setattr(code_runtime, 'render_generated_code', render)
    monkeypatch.setattr(validation, 'capture_preflight', lambda *a, **kw:None)
    assert code_runtime.preflight_generated_code('manim', workspace_root=tmp_path)['status'] == 'ok'
    assert seen[0]['narration'] == {'enabled':False}


@pytest.mark.parametrize('mode', ['full', 'balanced', 'json_only'])
@pytest.mark.parametrize('entry', ['generate', 'fix'])
@pytest.mark.parametrize('field,value', [
    ('canonical_spoken_text', '模型自行审定的口语。'),
    ('spoken_review', {'status':'approved','reviewer':'model'}),
])
def test_model_cannot_add_editorial_narration_fields(mode, entry, field, value):
    from aiforstudy.blocks.media.code_generation import generate_media_code, fix_media_code
    from aiforstudy.core.review_mode import review_mode
    from aiforstudy.errors import ContractError
    from test_media_code_contracts import approved_case, Client
    content, approval, req, original = approved_case('animation')
    forged = deepcopy(original)
    forged['steps'][0][field] = value
    client = Client(forged)
    with review_mode(mode), pytest.raises(ContractError, match='editorial|审定'):
        if entry == 'generate':
            generate_media_code(req, content=content, approval=approval, client=client)
        else:
            fix_media_code(req, content=content, approval=approval, client=client, program=original, error='fixture syntax repair')


@pytest.mark.parametrize('mode', ['full', 'balanced', 'json_only'])
@pytest.mark.parametrize('change', ['new_words', 'changed_review', 'changed_caption', 'changed_id'])
def test_technical_fix_cannot_reuse_review_for_modified_speech(mode, change):
    from aiforstudy.blocks.media.code_generation import fix_media_code
    from aiforstudy.core.review_mode import review_mode
    from aiforstudy.errors import ContractError
    from test_media_code_contracts import approved_case, Client
    content, approval, req, original = approved_case('animation')
    original['steps'][0].update(canonical_spoken_text='负一次方项。',
        spoken_review={'status':'approved','reviewer':'editor'}, display_markdown=r'$a_{-1}$')
    forged = deepcopy(original)
    if change == 'new_words': forged['steps'][0]['canonical_spoken_text'] = '正一次方项。'
    elif change == 'changed_review': forged['steps'][0]['spoken_review']['reviewer'] = 'model'
    elif change == 'changed_caption': forged['steps'][0]['caption'] = '改变了口语关联内容'
    else: forged['steps'][0]['step_id'] = 'new_step'
    snapshot = deepcopy(original)
    with review_mode(mode), pytest.raises(ContractError, match='editorial|审定'):
        fix_media_code(req, content=content, approval=approval, client=Client(forged), program=original, error='fix code')
    assert original == snapshot


@pytest.mark.parametrize('mode', ['full', 'balanced', 'json_only'])
def test_technical_fix_restores_editorial_script_from_trusted_original(mode):
    from aiforstudy.blocks.media.code_generation import fix_media_code
    from aiforstudy.core.review_mode import review_mode
    from test_media_code_contracts import approved_case, Client
    content, approval, req, original = approved_case('animation')
    original['steps'][0].update(canonical_spoken_text='负一次方项。',
        spoken_review={'status':'approved','reviewer':'editor'}, display_markdown=r'$a_{-1}$')
    repaired = deepcopy(original)
    repaired['code'] += '\n# repaired syntax boundary\n'
    repaired['steps'][0].pop('canonical_spoken_text')
    repaired['steps'][0].pop('spoken_review')
    client = Client(repaired)
    with review_mode(mode):
        result = fix_media_code(req, content=content, approval=approval, client=client, program=original, error='fix code')
    assert result['code'] == repaired['code']
    assert result['steps'][0]['canonical_spoken_text'] == original['steps'][0]['canonical_spoken_text']
    assert result['steps'][0]['spoken_review'] == original['steps'][0]['spoken_review']
    result['steps'][0]['spoken_review']['reviewer'] = 'changed result'
    assert original['steps'][0]['spoken_review']['reviewer'] == 'editor'
    payload = json.loads(client.calls[0][0][-1]['content'])
    properties = payload['output_json_schema']['$defs']['MediaCodeStepV1']['properties']
    assert 'canonical_spoken_text' not in properties and 'spoken_review' not in properties


@pytest.mark.parametrize('settings', [{'volume':0}, {'pitch':.5}, {'pitch':True}])
def test_minimax_volume_is_positive_and_pitch_is_integer(settings):
    with pytest.raises(ConfigError):
        n.configured_backend({'backend':'minimax','voice':'teacher', **settings})


@pytest.mark.parametrize('mode', ['full', 'balanced', 'json_only'])
@pytest.mark.parametrize('entry', ['generate', 'fix'])
def test_ordinary_model_narration_needs_no_editorial_approval(mode, entry):
    from aiforstudy.blocks.media.code_generation import generate_media_code, fix_media_code
    from aiforstudy.core.review_mode import review_mode
    from test_media_code_contracts import approved_case, Client
    content, approval, req, original = approved_case('animation')
    candidate = deepcopy(original)
    candidate['steps'][0]['narration_text'] = '保持向量的方向与长度。'
    with review_mode(mode):
        if entry == 'generate':
            result = generate_media_code(req, content=content, approval=approval, client=Client(candidate))
        else:
            result = fix_media_code(req, content=content, approval=approval, client=Client(candidate),
                                    program=original, error='fix code')
    assert result['steps'][0]['narration_text'] == candidate['steps'][0]['narration_text']
    assert 'spoken_review' not in result['steps'][0]
