import json
from aiforstudy.integrated.server import reader_media_names

def test_reviewed_motion_assets_are_selected_from_declared_animation_only():
    base='assets/narrated/CH08-S04-SS02/MR-123abc-motion-20261122/'
    row={'base':base,'resources':{'mp4':{'relative_path':'narrated.mp4'},'svg':{'relative_path':'arc-strip.svg'},'poster':{'relative_path':'poster.png'},'source':{'relative_path':'private.py'}},
         'playback':{'video_src':base+'picture.mp4','audio':{'src':base+'voice.mp3'}}}
    raw=('window.__DATA__='+json.dumps({'animations':{'CH08-S04-SS02':[row]}})+';').encode()
    names=reader_media_names(raw)
    assert {base+n for n in ['narrated.mp4','arc-strip.svg','poster.png','picture.mp4','voice.mp3']}<=names
    assert base+'private.py' not in names
    row['resources']['svg']['relative_path']='../private.svg'
    raw=('window.__DATA__='+json.dumps({'animations':{'CH08-S04-SS02':[row]}})+';').encode()
    assert not any('..' in path for path in reader_media_names(raw))
