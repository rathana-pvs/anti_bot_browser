"""Real X11 clipboard/Enter and visual verification, with no network or Facebook.

Run RUN_LIVE_COMMENT_OFFLINE=1 with the configured OCR Python environment.
"""
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import pytest

pytestmark = pytest.mark.skipif(os.environ.get('RUN_LIVE_COMMENT_OFFLINE') != '1',
                                reason='Opt-in real Chrome/X11/OCR fixture')

PAGE = '''<!doctype html><html><head><style>
body{margin:0;background:#f0f2f5;font-family:Arial;color:#111}
aside{position:fixed;left:0;top:140px;width:300px;padding:20px;background:white}
h1{font-size:26px}aside p{font-size:20px}
#end{position:fixed;left:15px;bottom:15px;background:#e21c34;color:white;border:0;padding:18px;font-size:20px}
section{position:fixed;right:20px;top:140px;bottom:20px;width:420px;background:white;padding:20px}
#comments{position:absolute;top:140px;font-size:20px;line-height:35px}
textarea{position:absolute;bottom:20px;left:20px;width:370px;height:55px;padding:12px;font:18px Arial;border:1px solid #888}
</style></head><body><aside><h1>Live dashboard</h1><p>Test Page is live now</p></aside>
<button id="end">End live video</button><section><h1>Comments</h1><div id="comments"></div>
<textarea placeholder="Write a comment..." autofocus></textarea></section>
<script>const input=document.querySelector('textarea');input.addEventListener('keydown',e=>{
if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();const row=document.createElement('div');
row.textContent=input.value;document.querySelector('#comments').append(row);input.value='';}});</script>
</body></html>'''

PROFILE_PAGE = '''<!doctype html><html><head><style>
body{margin:0;background:#f0f2f5;font-family:Arial;color:#111}
#toolbar{position:absolute;left:1050px;top:180px;width:800px;height:100px;background:white}
#toolbar b{position:absolute;left:20px;top:10px;font-size:24px}
#toolbar span{position:absolute;left:550px;top:10px;font-size:20px}
#toolbar em{position:absolute;left:40px;top:65px;font:20px Arial}
article{position:absolute;left:1050px;top:300px;width:800px;height:600px;background:white}
header{position:absolute;left:20px;top:10px;font-size:22px;font-weight:bold}
small{position:absolute;left:20px;top:42px;font-size:18px}
#comments{position:absolute;left:20px;top:380px;font-size:20px;line-height:35px}
textarea{position:absolute;bottom:20px;left:20px;width:730px;height:55px;padding:12px;font:18px Arial}
</style></head><body><div id="toolbar"><b>Posts</b><span>Filters</span><em>List view</em></div>
<article><header>Test Page</header><small>Just now</small><div id="comments"></div>
<textarea placeholder="Comment as Test Page"></textarea></article>
<script>const input=document.querySelector('textarea');input.addEventListener('keydown',e=>{
if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();const row=document.createElement('div');
row.textContent=input.value;document.querySelector('#comments').append(row);input.value='';}});</script></body></html>'''


@pytest.mark.parametrize('profile_fallback',[False,True])
def test_native_comment_is_submitted_once_and_visually_confirmed(tmp_path, monkeypatch, profile_fallback):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'automation'))
    from tasks.facebook_live import FacebookLiveTask
    from engine.live_brain import resolve_live_brain
    from modules.live_comment import LiveComment
    from engine.ocr_reader import selected_engine, model_root

    name = 'isolated_comment_fixture_' + uuid.uuid4().hex[:12]
    engine = selected_engine()
    monkeypatch.setenv('AUTOMATION_OCR_ENGINE',engine)
    monkeypatch.setenv('AUTOMATION_OCR_MODEL_DIR' if engine == 'easyocr' else 'AUTOMATION_PADDLE_MODEL_DIR',str(model_root(engine)))
    monkeypatch.setenv('AUTOMAT_FB_ROOT',str(tmp_path))
    (tmp_path / 'fixture.html').write_text(PAGE if not profile_fallback else PAGE.replace('<textarea placeholder="Write a comment..." autofocus></textarea>',''))
    (tmp_path / 'profile.html').write_text(PROFILE_PAGE)
    subprocess.run(['docker', 'run', '-d', '--rm', '--init', '--network', 'none',
        '--name', name, '--cpus', '2', '--memory', '1g', '-v', str(tmp_path)+':/work:ro',
        '--entrypoint', 'bash', 'isolated-chrome:latest', '-c',
        'Xvfb :99 -screen 0 1920x1080x24 >/tmp/xvfb.log 2>&1 & '
        'export DISPLAY=:99; sleep 1; openbox >/tmp/wm.log 2>&1 & '
        'exec google-chrome --no-sandbox --disable-gpu --disable-dev-shm-usage '
        '--no-first-run --no-default-browser-check --window-size=1920,1080 '
        '--start-maximized --user-data-dir=/tmp/local-profile file:///work/fixture.html'],
        check=True, capture_output=True)
    try:
        time.sleep(4)
        root = Path(__file__).resolve().parents[2] / 'automation'
        task = FacebookLiveTask(name, resolve_live_brain(root, root))
        events = []
        task.on_event = events.append
        keys = []
        original = task.client.key
        def key(value, **kwargs):
            keys.append(value)
            return original(value, **kwargs)
        monkeypatch.setattr(task.client, 'key', key)
        if profile_fallback:
            navigate = task.live_input.human.navigate
            def local_profile(url):
                assert url == 'https://www.facebook.com/me'
                navigate('file:///work/profile.html')
            monkeypatch.setattr(task.live_input.human,'navigate',local_profile)
        LiveComment(task,after_end=profile_fallback).post('Live comment offline check')
        assert events.count('live_comment_submit_requested') == 1
        assert events.count('live_comment_submit_sent') == 1
        assert keys.count('Return') == (2 if profile_fallback else 1)  # Navigation plus one comment.
        assert keys.count('ctrl+v') == 1
        if profile_fallback:
            assert events.count('live_profile_recent_post_verified') == 1
    finally:
        subprocess.run(['docker', 'stop', '--time', '2', name], capture_output=True, check=False)
