"""Natural speech is separate from visual TeX and must contain real samples."""
from pathlib import Path
import subprocess
import wave
import pytest
from aiforstudy.blocks.media import narration as n


def test_explicit_edge_backend_remains_available():
    backend=n.configured_backend({"backend":"edge-tts"})
    assert backend.source['backend']=='edge-tts'
    assert backend.source['voice']=='zh-CN-XiaoxiaoNeural'


def test_drawing_instructions_are_replaced_by_the_observed_mathematics():
    text=n.spoken_text({'caption':'对 M₁ 画半径递增的邻域，停在完全落在 E 内的那一个。',
        'observation':'看蓝色圆：它从很小逐步变大，但始终没有碰到灰色区域 E 的边界，整体都在 E 里面。'})
    assert '画半径' not in text and '停在' not in text
    assert '边界' in text and '里面' in text


def test_authored_speech_reads_unicode_and_tex_mathematics():
    text=n.spoken_text({'narration_text':r'辐角为 φ₁+φ₂，半径是 $\sqrt{2}$，ζ 趋于 ∞。'})
    assert '斐' in text and '泽塔' in text and '无穷大' in text
    assert all(c not in text for c in '$\\{}₁₂')


def test_visual_tex_preserves_existing_tex_and_normalizes_bare_equations():
    text=n.visual_latex('结果 z₁z₂=r₁r₂ e^{i(φ₁+φ₂)}，以及 $\\frac{1}{2}$。')
    assert '$\\frac{1}{2}$' in text
    assert '\\varphi' in text and '_{1}' in text
    assert '₁' not in text
    assert text.count('$')==4


class Tone:
    source={'backend':'recorded-test','voice':'tone'}
    def __init__(self, silent=False): self.silent=silent
    def synthesize(self,text,destination):
        import imageio_ffmpeg
        signal='anullsrc=r=24000:cl=mono' if self.silent else 'sine=frequency=440:sample_rate=24000'
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-y','-f','lavfi','-i',signal,
            '-t','1.8','-c:a','pcm_s16le',str(destination)],check=True)


def source_video(tmp_path):
    import imageio_ffmpeg
    path=tmp_path/'source.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-y','-f','lavfi','-i',
        'testsrc=size=160x90:rate=10:duration=1','-c:v','libx264','-pix_fmt','yuv420p',str(path)],check=True)
    return path


def test_silent_backend_is_rejected_before_a_delivery_is_written(tmp_path):
    with pytest.raises(ValueError,match='silent|静音'):
        n.narrate_animation(source_video(tmp_path),[{'caption':'模长不变','start':0,'end':1}],
            tmp_path/'out',backend=Tone(silent=True))
    assert not (tmp_path/'out/narrated.mp4').exists()


def test_visual_transcript_and_real_voice_have_distinct_text_and_one_clock(tmp_path):
    import av
    result=n.narrate_animation(source_video(tmp_path),[{'caption':r'模长 $|z|=1$。',
        'narration_text':'请留意，这个点始终在单位圆上。','start':0,'end':1}],tmp_path/'out',backend=Tone())
    assert '$|z|=1$' in Path(result['transcript_path']).read_text()
    assert '单位圆' in result['steps'][0]['narration_text']
    assert result['audio_quality']['non_silent'] is True
    with av.open(result['video_path']) as c:
        decoded=list(c.decode(video=0))
        assert len(decoded)==21
    assert result['video_timing']['video_seconds']==pytest.approx(2.1,abs=.01)


def test_visual_tex_keeps_absolute_value_and_full_superscript_in_one_formula():
    text=n.visual_latex('比值 |(z-a)/(ζ-a)|，另有 (ζ-a)ⁿ⁺¹。')
    assert '比值 $|(z-a)/(\\zeta -a)|$' in text
    assert '^{n+1}' in text
    assert '⁺' not in text


def test_authored_bare_tex_formula_gets_one_math_delimiter_pair():
    text=n.visual_latex(r'\sup_{|z|<1}|z^n|=1')
    assert text==r'$\sup_{|z|<1}|z^n|=1$'
    assert n.visual_latex(r'z=\mathrm i/2\mapsto t=(-3-4\mathrm i)/5')==r'$z=\mathrm i/2\mapsto t=(-3-4\mathrm i)/5$'


def test_generated_display_uses_explanatory_observation_instead_of_drawing_order(tmp_path):
    result=n.narrate_animation(source_video(tmp_path),[{'caption':'对 M₁ 画圆，停在完全落在 E 内的一个。',
        'observation':'M₁ 的邻域完全包含在 E 中，因此它是内点。','start':0,'end':1}],
        tmp_path/'out',backend=Tone())
    display=result['steps'][0]['display_markdown']
    assert '画圆' not in display and '停在' not in display
    assert '$M_{1}$' in display and '$E$' in display
    assert '内点' in display


def test_tau_and_other_greek_symbols_are_explicitly_spoken_chinese():
    text=n.spoken_text({'narration_text':r'延迟 τ，另有 $\tau$、χ、ψ、ξ。'})
    assert 'τ' not in text and 'tau' not in text and '陶' in text
    assert all(symbol not in text for symbol in 'χψξ')


def test_cached_audio_rechecks_the_current_spoken_script(tmp_path):
    import json
    source=source_video(tmp_path);steps=[{'caption':'观察单位圆','start':0,'end':1}]
    n.narrate_animation(source,steps,tmp_path/'out',backend=Tone())
    path=tmp_path/'out/narrated.narration.json';receipt=json.loads(path.read_text())
    receipt['steps'][0]['narration_text']='这是过时的口语稿。'
    path.write_text(json.dumps(receipt))
    result=n.narrate_animation(source,steps,tmp_path/'out',backend=Tone())
    assert result['cache_hit'] is False
    assert '过时' not in result['steps'][0]['narration_text']
