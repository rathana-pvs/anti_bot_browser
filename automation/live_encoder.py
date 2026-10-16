"""Bounded in-container sender. One RTMP connection spans startup and playback.

The destination arrives on stdin. Only phase markers and numeric progress leave
stdout; FFmpeg stderr never becomes application logs.
"""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--video', required=True)
    parser.add_argument('--duration', type=float, required=True)
    parser.add_argument('--startup-timeout', type=float, default=190)
    parser.add_argument('--pidfile', required=True)
    parser.add_argument('--control', required=True)
    parser.add_argument('--loop', action='store_true')
    parser.add_argument('--has-audio', action='store_true')
    args = parser.parse_args()
    if not 0 < args.duration <= 14400 or not 0 < args.startup_timeout <= 190:
        return 1
    destination = sys.stdin.readline().strip()
    if not destination:
        return 1
    pidfile, control = Path(args.pidfile), Path(args.control)
    control.unlink(missing_ok=True)
    pidfile.write_text(str(os.getpid()))
    stopped = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    sender = relay = None
    phase_started = None
    output_lock = threading.Lock()

    def emit(value):
        with output_lock:
            print(value, flush=True)

    def finish(proc, sig=signal.SIGINT):
        if proc is not None and proc.poll() is None:
            proc.send_signal(sig)
            try:
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    def produce(loop, duration):
        cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-re']
        if loop:
            cmd += ['-stream_loop', '-1']
        cmd += ['-i', args.video, '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo',
                '-map', '0:v:0', '-map', '0:a:0' if args.has_audio else '1:a:0',
                '-t', str(duration), '-vf',
                'scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=30',
                '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
                '-b:v', '3000k', '-maxrate', '3000k', '-bufsize', '6000k', '-g', '60', '-threads', '2',
                '-c:a', 'aac', '-b:a', '128k', '-ar', '44100', '-ac', '2',
                '-mpegts_flags', '+resend_headers+initial_discontinuity',
                '-muxdelay', '0', '-muxpreload', '0', '-f', 'mpegts', 'pipe:1']
        return subprocess.Popen(cmd, stdout=relay.stdin, stderr=subprocess.DEVNULL)

    try:
        # MPEG-TS discontinuities let the single relay adjust timestamps when the
        # playback encoder restarts at frame zero. Its output connection stays open.
        relay = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-fflags', '+genpts', '-dts_delta_threshold', '0.1', '-probesize', '1048576', '-analyzeduration', '1000000',
            '-f', 'mpegts', '-i', 'pipe:0', '-map', '0:v:0', '-map', '0:a:0', '-c', 'copy',
            '-progress', 'pipe:1', '-stats_period', '0.5', '-f', 'flv', destination],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        def progress():
            for line in relay.stdout:
                # Playback progress is independent of startup ingest timestamps.
                if line.startswith(b'out_time_us=') and phase_started is not None:
                    elapsed = min(args.duration, max(0, time.monotonic() - phase_started))
                    emit('out_time_us=' + str(int(elapsed * 1000000)))
        reader = threading.Thread(target=progress, daemon=True)
        reader.start()
        sender = produce(True, args.startup_timeout)
        emit('phase=startup')
        deadline = time.monotonic() + args.startup_timeout
        while not stopped.is_set() and not control.exists():
            if sender.poll() is not None or relay.poll() is not None or time.monotonic() >= deadline:
                emit('failure=startup')
                return 1
            stopped.wait(.05)
        if stopped.is_set():
            return 1
        finish(sender)
        if relay.poll() is not None:
            return 1
        sender = produce(args.loop, args.duration)
        phase_started = time.monotonic()
        emit('phase=playback')
        deadline = phase_started + args.duration + 30
        while sender.poll() is None and not stopped.is_set():
            if relay.poll() is not None or time.monotonic() >= deadline:
                return 1
            stopped.wait(.05)
        if stopped.is_set() or sender.poll() != 0:
            return 1
        relay.stdin.close()
        relay.wait(timeout=10)
        reader.join(timeout=2)
        if relay.returncode != 0:
            return 1
        emit('out_time_us=' + str(int(args.duration * 1000000)))
        emit('phase=complete')
        return 0
    except (OSError, subprocess.SubprocessError):
        return 1
    finally:
        finish(sender)
        finish(relay, signal.SIGTERM)
        control.unlink(missing_ok=True)
        pidfile.unlink(missing_ok=True)


if __name__ == '__main__':
    sys.exit(main())
