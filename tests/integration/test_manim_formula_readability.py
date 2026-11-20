"""Measure host-rendered formula ink at the width available on a 390px page."""
import json
from pathlib import Path
import subprocess

import pytest

from aiforstudy.blocks.visual import manim_adapter as adapter
from aiforstudy.blocks.visual import worker


ROOT = Path(__file__).resolve().parents[2]
WORKER_DIR = ROOT / 'tools/manim_worker'

# Fixed, local formulas only; no model response or untrusted TeX enters this probe.
PROBE = r'''
import json
from pathlib import Path
import sys
import numpy as np
from PIL import Image
from manim import config
import policy
import scene_builder

root=Path(sys.argv[1])
for name in ('tex','media'):(root/name).mkdir(parents=True,exist_ok=True)
config.tex_dir=str(root/'tex');config.media_dir=str(root/'media')
config.pixel_width=1280;config.pixel_height=720;config.frame_height=8
config.frame_width=1280/720*8
config.background_color='#ffffff';config.verbosity='ERROR'
config.write_to_movie=False;config.save_last_frame=False
plan={'three_d':False,'axes3d':None,
      'axes2d':{'x_range':[-1,1],'y_range':[-1,1]},
      'format':'visual-scene-2.0'}
scene=scene_builder._build_scene_class(plan,font_family=policy.FONT_FAMILY,
                                     pinned_font=policy.FONT_FILE)()
scene.camera.background_color='#ffffff'
scene.play=lambda *args,**kwargs:None
scene.frame_fixed_ids=set();scene.mobjects_by_id={}
measurements={}
for digit in ('0','3'):
    mob=scene._make(digit,{'kind':'formula','rendering':'trusted_mathtex','tex':digit})
    scene.camera.reset();scene.camera.capture_mobjects([mob])
    pixels=scene.camera.pixel_array
    Image.fromarray(pixels).save(root/(digit+'.png'))
    ink=np.min(pixels[:,:,:3],axis=2)<128
    ys,xs=np.where(ink)
    height=int(ys.max()-ys.min()+1)
    measurements[digit]={'ink_height':height,'mobile_height':height*360/1280,
                         'bbox':[int(xs.min()),int(ys.min()),int(xs.max()+1),int(ys.max()+1)]}
    scene.mobjects_by_id[digit]=mob

for slot,digit in enumerate(('0','3')):
    scene._play_action({'action':'reveal','object_id':digit,'formula_slot':slot},0.1)
left,right=(scene.mobjects_by_id[k] for k in ('0','3'))
measurements['slots']={'left':left.get_left().tolist(),'right':right.get_left().tolist(),
                       'left_right_edge':float(left.get_right()[0]),
                       'right_left_edge':float(right.get_left()[0])}
for label,tex in [('wide','1234567890'*6),
                  ('tall',r'\frac{\frac{1}{2}}{\frac{3}{4}}')]:
    mob=scene._make(label,{'kind':'formula','rendering':'trusted_mathtex','tex':tex})
    measurements[label]={'width':float(mob.width),'height':float(mob.height)}
(root/'measurements.json').write_text(json.dumps(measurements))
'''


@pytest.fixture(scope='module')
def formula_measurements(tmp_path_factory):
    root=tmp_path_factory.mktemp('formula-readability')
    services=adapter.default_services(workspace_root=ROOT)
    policy=worker.load_asset(WORKER_DIR,'policy')
    env=policy.worker_env(interpreter=services.interpreter,worker_dir=str(WORKER_DIR),
                          scratch=str(root))
    result=subprocess.run([services.interpreter,'-c',PROBE,str(root)],env=env,cwd=root,
                          capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
    return json.loads((root/'measurements.json').read_text())


def test_short_trusted_formula_is_readable_on_a_390px_page(formula_measurements):
    # A 390px viewport leaves 360px for video after page padding. Test visible ink,
    # not MathTex's nominal font_size or the surrounding formula bounding box.
    for digit in ('0','3'):
        assert formula_measurements[digit]['mobile_height']>=14,formula_measurements


def test_larger_formula_preserves_slot_positions_and_size_caps(formula_measurements):
    slots=formula_measurements['slots']
    assert slots['right'][0]-slots['left'][0]==pytest.approx(6.8)
    assert slots['right'][1]==pytest.approx(slots['left'][1])
    assert slots['left_right_edge']<slots['right_left_edge']
    for label in ('wide','tall'):
        assert formula_measurements[label]['width']<=6+1e-9
        assert formula_measurements[label]['height']<=1.25+1e-9
