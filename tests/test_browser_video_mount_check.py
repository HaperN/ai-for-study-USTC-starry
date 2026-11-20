"""Exercise the real page-check callbacks without starting a browser or loading files."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = r"""
const fs = require('node:fs'), path = require('node:path');
const {JSDOM} = require('jsdom');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const source = fs.readFileSync('src/aiforstudy/checks/browser_acceptance.cjs', 'utf8');
const playback = source.slice(source.indexOf('async function verifyVideoPlayback('),
  source.indexOf('// Runs inside the candidate page.'));
const wrapper = source.slice(source.indexOf(' async function checkVideos('),
  source.indexOf(' if(served){', source.indexOf(' async function checkVideos(')));
const dom = new JSDOM('<main>' + input.html + '</main>', {
  url:'https://reader.invalid/index.html', runScripts:'outside-only'
});
dom.window.__DATA__ = {animations:input.animations};
// JSDOM has no decoder. Only the already-mounted video's playback boundary is
// simulated; the production callback discovers and matches the actual DOM.
for (const video of dom.window.document.querySelectorAll('video')) {
  Object.defineProperties(video, {
    readyState:{value:4}, videoWidth:{value:160}, videoHeight:{value:120}
  });
  video.pause = () => {};
  video.play = () => {video.currentTime = 0.2; return Promise.resolve();};
  const button = video.closest('[data-animation-player]')?.querySelector('[data-action="play"]');
  if (button) button.onclick = () => video.play();
}
const evidence = {video_playback:[],animation_errors:[]};
const run = new Function('evaluate','evidence','path','root',
  playback + wrapper + '\nreturn checkVideos;')(
    expression => dom.window.eval(expression), evidence, path, '/reader');
run('/reader/index.html', input.unit).then(() => {
  console.log(JSON.stringify(evidence)); dom.window.close();
}).catch(error => {console.error(error); process.exitCode = 1;});
"""


def animation(request_id, relative_path):
    # MP4 alone is enough: captions, steps, review status and legacy placement
    # metadata must not become conditions for the balanced usability check.
    return {'request_id': request_id, 'base': 'assets/animations',
            'resources': {'mp4': {'relative_path': relative_path}}}


def video(relative_path, *, player=True, source_child=False):
    html = ('<video><source src="assets/animations/' + relative_path + '"></video>'
            if source_child else '<video src="assets/animations/' + relative_path + '"></video>')
    return ('<figure data-animation-player><button data-action="play">播放</button>'
            + html + '</figure>') if player else html


@pytest.mark.parametrize(('case', 'missing_ids', 'played'), [
    ('none_mounted', ['ANIM-A', 'ANIM-B'], 0),
    ('one_missing', ['ANIM-B'], 1),
    ('wrong_video', ['ANIM-B'], 2),
    ('all_mounted', [], 2),
    ('single_payload_native_video', [], 1),
    ('source_child', [], 1),
    ('other_unit_only', [], 0),
    ('no_mp4', [], 0),
    ('no_animation_data', [], 0),
])
def test_page_check_matches_current_unit_delivered_videos(case, missing_ids, played):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for production page-check callbacks')
    animations = {'U': [animation('ANIM-A', 'a.mp4'), animation('ANIM-B', 'b.mp4')],
                  'OTHER': [animation('UNRELATED', 'other.mp4')]}
    html = ''
    if case in ('one_missing', 'wrong_video', 'all_mounted'):
        html = video('a.mp4')
    if case == 'wrong_video':
        html += video('other.mp4')
    if case == 'all_mounted':
        html += video('b.mp4')
    if case in ('single_payload_native_video', 'source_child'):
        animations['U'] = animation('ANIM-A', './a.mp4')
        html = video('a.mp4', player=False, source_child=case == 'source_child')
    if case == 'other_unit_only':
        animations.pop('U')
    if case == 'no_mp4':
        animations['U'] = [{'request_id': 'NO-VIDEO', 'resources': {}}]
    if case == 'no_animation_data':
        animations = {}
    result = subprocess.run([node, '-e', SCRIPT], cwd=ROOT,
        input=json.dumps({'unit': 'U', 'animations': animations, 'html': html}),
        text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    assert len(evidence['animation_errors']) == len(missing_ids), evidence
    for request_id in missing_ids:
        assert any('index.html/U' in error and request_id in error and '未挂载' in error
                   for error in evidence['animation_errors']), evidence
    assert sum(row['ok'] for row in evidence['video_playback']) == played, evidence
    assert all(row['currentTime'] > 0 for row in evidence['video_playback'] if row['ok'])
