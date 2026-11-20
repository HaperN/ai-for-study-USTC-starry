"""Offline production-path smoke test: Chinese, MathTex, movement and a test tone.

The tone validates media plumbing; it is deliberately not a voice quality test.
No provider API or TTS backend is invoked. Generated source runs only inside the
same OS-isolated worker used in production. Output keeps the complete evidence.
"""
from pathlib import Path
import argparse
import json
import subprocess
from aiforstudy.blocks.media.code_runtime import render_generated_code
from aiforstudy.providers.capabilities import probe
from aiforstudy.core.workspace import Workspace

SOURCE = r'''from manim import Scene, MathTex, Text, VGroup, DOWN, RIGHT
from aifs_media_runtime import media_step
from pathlib import Path
import math, struct, wave
class GeneratedScene(Scene):
    def construct(self):
        tone = Path('test-tone.wav')
        with wave.open(str(tone), 'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(24000)
            audio.writeframes(b''.join(struct.pack('<h',int(1000*math.sin(2*math.pi*440*i/24000))) for i in range(28800)))
        group = VGroup(MathTex(r'\frac{1}{2}+x^2'), Text('中文公式与音视频验证',font_size=32)).arrange(DOWN)
        self.add(group)
        with media_step(self, 'P1'):
            self.add_sound(str(tone))
            self.play(group.animate.shift(.25*RIGHT),run_time=1.2)
'''


def main():
    args = argparse.ArgumentParser()
    args.add_argument('--out',required=True)
    out = Path(args.parse_args().out).absolute(); out.mkdir(parents=True,exist_ok=True)
    root = Path(__file__).resolve().parents[2]
    request = {'request_id':'local-render-verification','kind':'animation','object_refs':[],
               'animation':{'steps':['观察中文、公式及测试音']}}
    program = {'schema_version':'GeneratedMediaCodeV1','request_id':request['request_id'],
               'backend':'manim','code':SOURCE,'object_bindings':[],
               'steps':[{'step_id':'P1','author_step_index':0,'caption':'观察中文、公式及测试音',
                         'observation':'中文与分数同时可见','expected_change':'整组向右平移'}]}
    result = render_generated_code(program, context={'request':request,'approved_content':{},'approved_models':{}},
        out_dir=out,workspace_root=root,profile='draft',narration={'enabled':False})
    (out/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    if result['status'] not in ('rendered','needs_review'):
        raise SystemExit(result.get('reason') or result['status'])
    metadata=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(out/'video.mp4')]))
    (out/'media-metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
    assert any(row['codec_type']=='audio' for row in metadata['streams']), 'test tone missing'
    assert result['technical_report']['ffmpeg_decode_ok'] is True
    capabilities=probe(Workspace(root=root)).as_dict()
    (out/'capabilities.json').write_text(json.dumps(capabilities,ensure_ascii=False,indent=2)+'\n')
    assert 'manim' in capabilities['render_backends']
    print(json.dumps({'status':'ok','backend':'manim','isolation':result['technical_report']['isolation']['backend'],
                      'decoded_frames':result['technical_report']['decoded_frames'],'out':str(out)},ensure_ascii=False))


if __name__=='__main__':
    main()
