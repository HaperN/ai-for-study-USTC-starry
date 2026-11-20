"""Balanced directory checks actually start local video playback in Chromium."""
from pathlib import Path
import shutil
import subprocess

import pytest

from aiforstudy.checks.offline import check_browser_execution, _browser_executable
from aiforstudy.core.review_mode import review_mode


@pytest.fixture(scope='module')
def short_video(tmp_path_factory):
    root = Path(__file__).resolve().parents[2]
    if not shutil.which('ffmpeg') or not shutil.which('node') or not _browser_executable(root):
        pytest.skip('real ffmpeg, Chromium and Node required')
    video = tmp_path_factory.mktemp('balanced-video')/'clip.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'color=c=navy:s=160x120:r=15', '-t', '1', '-an', '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p', str(video)], check=True, capture_output=True, timeout=30)
    return video


@pytest.mark.parametrize('variant', ['playable', 'working_player', 'corrupt', 'broken_player', 'missing_video', 'no_video'])
def test_balanced_browser_checks_video_playback_without_requiring_metadata(tmp_path, short_video, variant):
    root = Path(__file__).resolve().parents[2]
    video_html = ''
    if variant != 'no_video':
        target = tmp_path/'clip.mp4'
        if variant == 'corrupt':
            target.write_bytes(b'not an mp4')
        else:
            shutil.copyfile(short_video, target)
        video_html = '<video controls preload="metadata" src="clip.mp4"></video>'
        if variant in ('broken_player', 'working_player'):
            # Exercise the actual custom control, including a broken handler.
            video_html = '<section data-animation-player><button data-action="play">播放</button>' + video_html + '</section>'
            if variant == 'working_player':
                video_html += '<script>document.querySelector("button").onclick=()=>document.querySelector("video").play();</script>'
        if variant == 'missing_video':
            video_html = '<section data-animation-player>视频加载失败</section>'
    (tmp_path/'index.html').write_text('<!doctype html><meta charset="utf-8">'
        '<main><article class="reader-document">零和一在数轴上。</article>' + video_html + '</main>')
    with review_mode('balanced'):
        report = check_browser_execution(tmp_path, node=shutil.which('node'), workspace_root=root)
    assert report.status == ('failed' if variant in ('corrupt', 'broken_player', 'missing_video') else 'accepted'), report.as_dict()
    if variant in ('corrupt', 'broken_player', 'missing_video'):
        assert report.evidence['animation_errors']
    elif variant in ('playable', 'working_player'):
        playback = report.evidence['video_playback']
        assert len(playback) == 1 and playback[0]['currentTime'] > 0
        assert playback[0]['width'] == 160 and playback[0]['height'] == 120
