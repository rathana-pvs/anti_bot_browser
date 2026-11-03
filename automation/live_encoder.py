"""Bounded in-container sender. One RTMP connection spans startup and playback.

The destination arrives on stdin. Only phase markers and numeric progress leave
stdout; FFmpeg stderr never becomes application logs.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time


ENCODER_FAILURE_REASONS = frozenset({
    'invalid_stream_key', 'server_rejected_stream', 'connection_refused', 'connection_timeout',
    'tls_failed', 'input_missing', 'encoder_unavailable', 'invalid_media', 'startup_timeout',
    'encoder_startup_failed', 'rtmp_handshake_failed', 'connection_closed', 'dns_failed',
    'unsupported_ffmpeg_option', 'encoder_initialization_failed', 'filter_initialization_failed',
    'resource_exhausted', 'output_io_failed', 'playback_failed', 'playback_timeout',
    'encoder_launch_failed', 'relay_failed', 'unknown_encoder_failure', 'live_duration_limit',
})
GENERIC_FAILURES = {'encoder_startup_failed', 'output_io_failed', 'playback_failed',
                    'relay_failed', 'unknown_encoder_failure'}


def classify_encoder_error(line):
    """Return fixed diagnostics; never persist FFmpeg's credential-bearing text."""
    text = line.lower()
    for markers, reason in [
        (('invalid stream key', 'invalid streamkey'), 'invalid_stream_key'),
        (('server error', 'publishing failed', 'authentication failed'), 'server_rejected_stream'),
        (('connection refused',), 'connection_refused'),
        (('connection timed out', 'operation timed out'), 'connection_timeout'),
        (('certificate verify failed', 'tls handshake failed'), 'tls_failed'),
        (('error completing handshake', 'handshake failed', 'cannot read rtmp handshake response'), 'rtmp_handshake_failed'),
        (('error in the pull function', 'connection reset by peer', 'broken pipe'), 'connection_closed'),
        (('failed to resolve hostname', 'name or service not known', 'temporary failure in name resolution'), 'dns_failed'),
        (('no such file or directory',), 'input_missing'),
        (('unknown encoder',), 'encoder_unavailable'),
        (('unrecognized option', 'option not found'), 'unsupported_ffmpeg_option'),
        (('error initializing output stream', 'error while opening encoder'), 'encoder_initialization_failed'),
        (('failed to configure output pad', 'error reinitializing filters'), 'filter_initialization_failed'),
        (('cannot allocate memory',), 'resource_exhausted'),
        (('invalid data found',), 'invalid_media'),
        (('input/output error', 'error opening output'), 'output_io_failed'),
    ]:
        if any(marker in text for marker in markers):
            return reason
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--video', required=True)
    parser.add_argument('--duration', type=float, required=True)
    parser.add_argument('--startup-timeout', type=float, default=105)
    parser.add_argument('--max-session-seconds', type=float, default=120)
    parser.add_argument('--pidfile', required=True)
    parser.add_argument('--control', required=True)
    parser.add_argument('--loop', action='store_true')
    parser.add_argument('--has-audio', action='store_true')
    args = parser.parse_args()
    if (not 0 < args.duration <= 14400 or not 0 < args.startup_timeout <= 190
            or not 0 < args.max_session_seconds <= 120):
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
    diagnostic_lock = threading.Lock()
    failure_reason = None
    failure_component = None
    phase = 'startup'
    error_readers = []

    def note_failure(reason, component=None):
        nonlocal failure_reason, failure_component
        with diagnostic_lock:
            # Cleanup/EPIPE and a generic exit must not replace the real cause.
            if failure_reason is None or (reason == 'invalid_stream_key') or (
                    failure_reason in GENERIC_FAILURES and reason not in GENERIC_FAILURES):
                failure_reason, failure_component = reason, component
            path = Path(str(pidfile) + '.diagnostic.json')
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'failure_reason': failure_reason,
                'failure_component': failure_component, 'phase': phase,
                'sender_exit_code': sender.poll() if sender is not None else None,
                'relay_exit_code': relay.poll() if relay is not None else None}))
            temporary.replace(path)

    def watch_errors(proc, component):
        def receive():
            for line in iter(lambda: proc.stderr.readline(4096), b''):
                reason = classify_encoder_error(line.decode(errors='replace'))
                if reason:
                    note_failure(reason, component)
        thread = threading.Thread(target=receive, daemon=True)
        error_readers.append((proc, thread))
        thread.start()

    def fail(reason):
        # poll() can observe exit before the stderr reader sees its final line.
        for proc, thread in error_readers:
            if proc.poll() is not None:
                thread.join(timeout=1)
        component = ('relay' if relay is not None and relay.poll() not in (None, 0)
                     else 'sender' if sender is not None and sender.poll() not in (None, 0) else None)
        note_failure(reason, component)
        emit('failure=' + phase)
        return 1

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
        proc = subprocess.Popen(cmd, stdout=relay.stdin, stderr=subprocess.PIPE)
        watch_errors(proc, 'sender')
        return proc

    def expire_session():
        # Independent of desktop/backend responsiveness and playback phase.
        stopped.set()
        for proc in (sender, relay):
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass
        note_failure('live_duration_limit')
        emit('phase=limit_reached')

    session_timer = threading.Timer(args.max_session_seconds, expire_session)
    session_timer.daemon = True
    session_timer.start()
    try:
        # MPEG-TS discontinuities let the single relay adjust timestamps when the
        # playback encoder restarts at frame zero. Its output connection stays open.
        relay = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-fflags', '+genpts', '-dts_delta_threshold', '0.1', '-probesize', '1048576', '-analyzeduration', '1000000',
            '-f', 'mpegts', '-i', 'pipe:0', '-map', '0:v:0', '-map', '0:a:0', '-c', 'copy',
            '-progress', 'pipe:1', '-stats_period', '0.5', '-f', 'flv', destination],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        watch_errors(relay, 'relay')
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
                return fail('startup_timeout' if time.monotonic() >= deadline else 'encoder_startup_failed')
            stopped.wait(.05)
        if stopped.is_set():
            return 1
        finish(sender)
        if relay.poll() is not None:
            return fail('relay_failed')
        sender = produce(args.loop, args.duration)
        phase = 'playback'
        phase_started = time.monotonic()
        emit('phase=playback')
        deadline = phase_started + args.duration + 30
        while sender.poll() is None and not stopped.is_set():
            if relay.poll() is not None or time.monotonic() >= deadline:
                return fail('playback_timeout' if time.monotonic() >= deadline else 'relay_failed')
            stopped.wait(.05)
        if stopped.is_set():
            return 1
        if sender.poll() != 0:
            return fail('playback_failed')
        relay.stdin.close()
        relay.wait(timeout=10)
        reader.join(timeout=2)
        if relay.returncode != 0:
            return fail('relay_failed')
        emit('out_time_us=' + str(int(args.duration * 1000000)))
        emit('phase=complete')
        return 0
    except (OSError, subprocess.SubprocessError):
        return fail('encoder_launch_failed')
    finally:
        session_timer.cancel()
        session_timer.join(timeout=2)
        finish(sender)
        finish(relay, signal.SIGTERM)
        control.unlink(missing_ok=True)
        pidfile.unlink(missing_ok=True)


if __name__ == '__main__':
    sys.exit(main())
