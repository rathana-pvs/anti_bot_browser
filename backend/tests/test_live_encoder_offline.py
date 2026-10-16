"""Real FFmpeg checks in a disposable, network-disabled container.

Run with RUN_LIVE_ENCODER_OFFLINE=1. No profiles, credentials or Facebook access.
"""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(os.environ.get('RUN_LIVE_ENCODER_OFFLINE') != '1',
                                reason='Opt-in real FFmpeg container tests')

CHECK = r'''
import json, subprocess, sys, threading, time
from pathlib import Path
kind = sys.argv[1]
cmd = ['ffmpeg', '-v', 'error']
for color in ['red', 'green', 'blue']:
    cmd += ['-f', 'lavfi', '-i', 'color=c='+color+':s=320x180:r=30:d=0.4']
cmd += ['-filter_complex', '[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]', '-map', '[v]']
if kind in ('audio', 'muted'):
    # Add audio separately to keep the color timeline identical.
    cmd += ['-an']
cmd += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-y', '/work/silent.mp4']
subprocess.run(cmd, check=True)
video = '/work/silent.mp4'
if kind in ('audio', 'muted'):
    video = '/work/audio.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-i', '/work/silent.mp4', '-f', 'lavfi', '-i',
        'sine=frequency=440:sample_rate=44100', '-t', '1.2', '-c:v', 'copy', '-c:a', 'aac', '-y', video], check=True)
duration = 2.4 if kind == 'loop' else 1.0 if kind == 'limited' else 1.2
args = [sys.executable, '-u', '/work/live_encoder.py', '--video', video, '--duration', str(duration),
        '--startup-timeout', '6' if kind != 'timeout' else '1',
        '--pidfile', '/work/live.pid', '--control', '/work/play']
if kind == 'loop': args += ['--loop']
if kind == 'audio': args += ['--has-audio']
p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
p.stdin.write('/work/out.flv\n'); p.stdin.flush(); p.stdin.close()
lines = []
started = threading.Event()
def receive():
    for line in p.stdout:
        lines.append(line.strip())
        if line.strip() == 'phase=startup': started.set()
t = threading.Thread(target=receive); t.start()
assert started.wait(5)
if kind == 'timeout':
    assert p.wait(timeout=10) != 0
elif kind == 'stop':
    time.sleep(1)
    p.terminate()
    assert p.wait(timeout=10) != 0
else:
    time.sleep(2.3)  # Longer than the entire source video.
    assert p.poll() is None, 'Short source ended startup instead of repeating'
    Path('/work/play').touch()
    assert p.wait(timeout=15) == 0
t.join(timeout=2)
assert not Path('/work/live.pid').exists()
assert not Path('/work/play').exists()
if kind in ('stop', 'timeout'):
    assert 'phase=playback' not in lines
else:
    assert lines.count('phase=playback') == 1 and 'phase=complete' in lines
    meta = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', '/work/out.flv']))
    assert [s['codec_name'] for s in meta['streams']] == ['h264', 'aac']
    if kind in ('audio', 'muted'):
        import re
        volume = subprocess.run(['ffmpeg', '-hide_banner', '-i', '/work/out.flv', '-vn',
            '-af', 'volumedetect', '-f', 'null', '-'], capture_output=True, text=True, check=True)
        mean = float(re.search(r'mean_volume: (-?[0-9.]+)', volume.stderr).group(1))
        assert (mean < -70) if kind == 'muted' else (mean > -40)
    frames = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', '/work/out.flv', '-vf', 'scale=1:1',
        '-vsync', '0', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'])
    colors = [max(range(3), key=lambda j: frames[i+j]) for i in range(0, len(frames), 3)]
    count = round(duration*30)
    assert len(colors) > count
    expected = (([0]*12 + [1]*12 + [2]*12) * (2 if kind == 'loop' else 1))[:count]
    assert colors[-count:] == expected, 'Playback did not restart with the full red/green/blue sequence'
    packets = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_packets', '-of', 'json', '/work/out.flv']))['packets']
    for stream in (0, 1):
        stamps = [float(x['dts_time']) for x in packets if x['stream_index'] == stream and 'dts_time' in x]
        assert stamps == sorted(stamps), 'Timestamp reset broke the continuous output'
print('Verified', kind)
'''


@pytest.mark.parametrize('kind', ['once', 'loop', 'audio', 'muted', 'limited', 'stop', 'timeout'])
def test_continuous_connection_restarts_full_clip_and_is_bounded(tmp_path, kind):
    shutil.copyfile(Path(__file__).resolve().parents[2] / 'automation/live_encoder.py', tmp_path / 'live_encoder.py')
    (tmp_path / 'check.py').write_text(CHECK)
    result = subprocess.run(['docker', 'run', '--rm', '--init', '--pull', 'never', '--network', 'none',
        '--cpus', '2', '--memory', '1g', '--entrypoint', 'python3',
        '-v', f'{tmp_path}:/work', 'isolated-chrome:latest', '/work/check.py', kind],
        capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr
