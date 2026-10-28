"""Local, single-file Facebook Live broadcasts. Keys never enter task state/logs."""
import asyncio
import hashlib
import json
import math
import os
import re
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from automation.engine.live_brain import resolve_live_brain

from backend.config import DATA_DIR, PROFILES_DIR, SHARED_MEDIA_DIR, AUTOMATION_PYTHON, AUTOMATION_RUNNER, ROOT_DIR, MANAGER_INSTANCE_ID
from backend.services import automation_service as automation, container_lifecycle as lifecycle

LIVE_DIR = DATA_DIR / 'live'
MAX_VIDEO_SECONDS = 4 * 60 * 60
ACTIVE_STAGES = {'preparing', 'stream_start_intent', 'sending', 'starting', 'live', 'ending', 'stopping'}
CONTROLS = {}


def now():
    return datetime.now(timezone.utc).isoformat()


def profile_path(profile_id):
    if not re.fullmatch(r'profile_\d+', profile_id):
        raise ValueError('Invalid profile ID')
    path = PROFILES_DIR / profile_id
    if not (path / 'config.json').is_file():
        raise FileNotFoundError('Profile not found')
    return path


def private_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path):
    return json.loads(path.read_text()) if path.exists() else {}


def validate_settings(value):
    url = str(value.get('server_url', '')).strip()
    parsed = urlsplit(url)
    if (parsed.scheme != 'rtmps' or parsed.hostname != 'live-api-s.facebook.com'
            or parsed.port not in (None, 443) or parsed.path.rstrip('/') != '/rtmp'
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('Use the Facebook RTMPS server URL ending in /rtmp/')
    key = str(value.get('stream_key', '')).strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{10,512}', key):
        raise ValueError('Enter a valid Facebook stream key')
    producer = str(value.get('producer_url') or '').strip() or 'https://www.facebook.com/live/producer/'
    p = urlsplit(producer)
    if (p.scheme != 'https' or p.hostname != 'www.facebook.com' or p.port not in (None, 443)
            or not p.path.startswith('/live/producer') or p.username or p.password or p.fragment):
        raise ValueError('Use an HTTPS Facebook Live Producer URL')
    return {'server_url': url.rstrip('/') + '/', 'stream_key': key, 'producer_url': producer}


def validate_details(title='', caption='', pinned_comment=None):
    if not isinstance(title, str) or not isinstance(caption, str):
        raise ValueError('Live title and caption must be text')
    title, caption = title.strip(), caption.strip()
    if len(title) > 255 or len(caption) > 5000 or '\x00' in title + caption:
        raise ValueError('Live title supports 255 characters and caption supports 5,000 characters')
    if bool(title) != bool(caption):
        raise ValueError('Enter both a Live title and caption')
    # Older jobs with no fields retain their manually prepared post details.
    result = {'title': title, 'caption': caption}
    if pinned_comment is not None:
        if not isinstance(pinned_comment, str) or len(pinned_comment.strip()) > 1000 or '\x00' in pinned_comment:
            raise ValueError('Pinned comment must be text with at most 1,000 characters')
        result['pinned_comment'] = pinned_comment.strip()
    return result


def validate_playback(loop=False, max_duration_seconds=None, muted=False, playback_mode=None):
    if playback_mode not in (None, 'auto', 'manual'):
        raise ValueError('Choose Automatic or Manual playback')
    if playback_mode == 'auto':
        loop, max_duration_seconds = False, None
    if not isinstance(loop, bool) or not isinstance(muted, bool):
        raise ValueError('Loop and muted must be true or false')
    if max_duration_seconds is not None and (isinstance(max_duration_seconds, bool)
            or not isinstance(max_duration_seconds, int) or not 1 <= max_duration_seconds <= MAX_VIDEO_SECONDS):
        raise ValueError('Set a duration limit between 1 second and 4 hours')
    if loop and max_duration_seconds is None:
        raise ValueError('Set a loop duration between 1 second and 4 hours')
    result = {'loop': loop, 'max_duration_seconds': max_duration_seconds, 'muted': muted}
    result['playback_mode'] = playback_mode or 'manual'
    return result


def settings_path(profile_id):
    profile_path(profile_id)
    return LIVE_DIR / profile_id / 'settings.json'


def get_settings(profile_id):
    settings = read_json(settings_path(profile_id))
    return {'server_url': settings.get('server_url', 'rtmps://live-api-s.facebook.com:443/rtmp/'),
            'producer_url': settings.get('producer_url', 'https://www.facebook.com/live/producer/'),
            'key_saved': bool(settings.get('stream_key'))}


def save_settings(profile_id, value):
    path = settings_path(profile_id)
    if profile_id in CONTROLS:
        raise RuntimeError('Stop the current broadcast before changing settings')
    previous = read_json(path)
    # Empty password input preserves a previously saved key; GET never returns it.
    settings = validate_settings({**value, 'stream_key': value.get('stream_key') or previous.get('stream_key', '')})
    private_write(path, settings)
    return get_settings(profile_id)


def state_path(profile_id):
    profile_path(profile_id)
    return LIVE_DIR / profile_id / 'session.json'


def get_status(profile_id):
    saved = read_json(state_path(profile_id))
    if not saved:
        return {'profile_id': profile_id, 'task': 'live', 'status': 'idle', 'stage': 'idle', 'logs': []}
    if profile_id not in CONTROLS and saved.get('stage') in ACTIVE_STAGES:
        # Crash/disconnect never authorizes restarting a broadcast.
        saved.update(status='needs_review', stage='needs_review', ended_at=now(),
                     error='The worker interrupted this session. Check Facebook and end any active broadcast before clearing it.')
        private_write(state_path(profile_id), saved)
    return saved


def archive_previous_session(profile_id):
    """Retain unresolved outcomes before a distinct session takes the status slot."""
    previous = get_status(profile_id)
    if previous.get('status') == 'idle':
        return
    encoded = json.dumps(previous, sort_keys=True).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    path = LIVE_DIR / profile_id / 'history' / (digest + '.json')
    if not path.exists():
        private_write(path, previous)


def resolve_video(filename):
    if not filename or Path(filename).name != filename or '\\' in filename:
        raise ValueError('Select an uploaded video')
    path = (SHARED_MEDIA_DIR / filename).resolve()
    if path.parent != SHARED_MEDIA_DIR.resolve() or not path.is_file():
        raise ValueError('Uploaded video not found')
    if path.suffix.lower() not in {'.mp4', '.mov', '.webm'}:
        raise ValueError('Select an MP4, MOV, or WebM video')
    return '/data/shared_media/' + filename


def update(record, stage=None, **fields):
    record.update(fields)
    if stage:
        record['stage'] = record['current_stage'] = stage
    record['last_activity_at'] = now()
    # Store only public fields; never serialize processes, keys, or settings.
    public = {k: record[k] for k in ('profile_id', 'task', 'session_id', 'status', 'stage', 'started_at',
              'ended_at', 'filename', 'duration_seconds', 'elapsed_seconds', 'error', 'logs', 'broadcast_confirmed', 'queue_execution_id', 'loop', 'max_duration_seconds', 'muted', 'brain', 'browser_template', 'browser_state', 'encoder_exit_code', 'final_browser_state', 'playback_mode', 'source_duration_seconds', 'startup_elapsed_seconds', 'playback_started_at', 'evidence_dir') if k in record}
    private_write(state_path(record['profile_id']), public)
    if record.get('queue_execution_id'):
        sync_queue(record)
    return public


def live_event(record, event):
    record.setdefault('logs', []).append({'timestamp': now(), 'event': event, 'level': 'STEP', 'message': event.replace('_', ' ').capitalize()})
    update(record)


def sync_queue(record, finalize=False):
    def persist():
        queue = automation.load_posting_queue()
        match = automation.find_queue_execution(queue, record['queue_execution_id'])
        lease = record.get('scheduler_lease') or {}
        if not match or (match['execution'].get('scheduler_lease') or {}).get('lease_id') != lease.get('lease_id'):
            raise RuntimeError('Live queue ownership lost; check Facebook')
        execution = match['execution']
        for key in ('elapsed_seconds', 'duration_seconds', 'broadcast_confirmed', 'error', 'session_id', 'brain', 'browser_template', 'browser_state', 'logs', 'encoder_exit_code', 'final_browser_state', 'playback_mode', 'source_duration_seconds', 'startup_elapsed_seconds', 'playback_started_at', 'evidence_dir'):
            if key in record:
                execution[key] = record[key]
        stage = record.get('stage')
        if stage != execution.get('stage'):
            execution.setdefault('stage_history', []).append({'stage': stage, 'timestamp': now()})
        execution['stage'] = stage
        execution['stage_updated_at'] = now()
        # Retain the slot until the encoder and container have both stopped.
        execution['status'] = 'running'
        if finalize:
            status = record['status']
            execution['status'] = ('completed' if status in ('completed', 'stopped') and record.get('broadcast_confirmed')
                                   else 'needs_review' if status == 'needs_review' else 'failed_before_publish')
            execution['ended_at'] = record.get('ended_at')
            execution['automation_container_owned'] = False
            automation.release_execution_lease(execution, lease['lease_id'])
        if not automation.save_posting_queue(queue):
            raise RuntimeError('Could not persist Live queue progress')
    automation.with_queue_claim_lock(persist)


async def execute_queue_live(claim):
    execution, post = claim['targetExec'], claim['targetPost']
    profile_id = execution['profile_id']
    record = dict(profile_id=profile_id, task='live', session_id=uuid.uuid4().hex, status='running',
                  stage='preparing', current_stage='preparing', started_at=now(), ended_at=None,
                  filename=post.get('media_file', ''), logs=[], error=None, elapsed_seconds=0,
                  broadcast_confirmed=False, process=None, lifecycle_active=True,
                  queue_execution_id=execution['execution_id'], scheduler_lease=claim['lease'],
                  container_owner_token=lifecycle.OWNER_TOKEN.get(), last_activity_at=now())
    try:
        if profile_id in CONTROLS:
            raise RuntimeError('This profile already has an active Live session')
        prior = get_status(profile_id)
        if prior.get('status') == 'needs_review' and prior.get('queue_execution_id') == execution['execution_id']:
            record.update(status='needs_review', stage='needs_review', error=prior.get('error'),
                          session_id=prior.get('session_id', record['session_id']), logs=prior.get('logs', []))
            sync_queue(record, finalize=True)
            return {'success': False, 'status': 'needs_review'}
        settings = validate_settings(read_json(settings_path(profile_id)))
        record.update(validate_details(post.get('title', ''), post.get('base_caption', ''), post.get('pinned_comment')))
        record.update(validate_playback(post.get('loop', False), post.get('max_duration_seconds'), post.get('muted', False), post.get('playback_mode')))
        video = resolve_video(record['filename'])
        archive_previous_session(profile_id)
    except Exception as error:
        record.update(status='failed', stage='failed', error=str(error), ended_at=now())
        sync_queue(record, finalize=True)
        return {'success': False, 'status': 'failed_before_publish'}
    stop_event = asyncio.Event()
    CONTROLS[profile_id] = stop_event
    automation.active_automation_tasks[profile_id] = record
    await run_live(record, settings, video, stop_event)
    return {'success': record['status'] in ('completed', 'stopped'), 'status': record['status']}


async def command(*args, timeout=30):
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        output, _ = await asyncio.wait_for(proc.communicate(), timeout)
        if proc.returncode:
            raise RuntimeError('Live preflight command failed; check Docker and the video file')
        return output
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


async def browser_action(profile_id, action, producer_url, stop_event):
    helper = AUTOMATION_RUNNER.parent / 'live_browser.py'
    record = automation.active_automation_tasks.get(profile_id, {})
    pin = record.get('brain')
    if not pin or not record.get('brain_package'):
        raise RuntimeError('Live browser workflow was not pinned before execution')
    details = validate_details(record.get('title', ''), record.get('caption', ''), record.get('pinned_comment')) if action == 'prepare' else {}
    worker_env = automation.automation_worker_env()
    worker_env['AUTOMATION_BEHAVIOR_RUN_ID'] = str(record.get('session_id') or profile_id)
    proc = await asyncio.create_subprocess_exec(str(AUTOMATION_PYTHON), str(helper), '--profile', profile_id,
            '--action', action, '--producer-url', producer_url,
            '--brain-package', record['brain_package'], '--brain-digest', pin['digest'], cwd=str(ROOT_DIR),
            env=worker_env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    async def receive():
        proc.stdin.write(json.dumps(details).encode())
        await proc.stdin.drain()
        proc.stdin.close()
        result = None
        while line := await proc.stdout.readline():
            try:
                message = json.loads(line)
            except (ValueError, TypeError):
                raise RuntimeError('Facebook Live verification failed') from None
            if not isinstance(message, dict):
                raise RuntimeError('Facebook Live verification failed')
            if message.get('event') in {'go_live_requested', 'go_live_click_sent', 'end_live_requested', 'end_live_click_sent',
                                        'end_confirmation_requested', 'end_confirmation_click_sent'}:
                live_event(record, message['event'])
            elif 'success' in message:
                result = message
        await proc.wait()
        return result
    communication = asyncio.create_task(receive())
    stopped = asyncio.create_task(stop_event.wait())
    try:
        live_event(record, action + '_requested')
        done, _ = await asyncio.wait([communication, stopped], timeout=190, return_when=asyncio.FIRST_COMPLETED)
        if communication not in done:
            raise RuntimeError('Live setup was stopped' if stop_event.is_set() else 'Facebook Live verification timed out')
        result = communication.result()
        if not isinstance(result, dict):
            raise RuntimeError('Facebook Live verification failed')
        if not result.get('success'):
            raw_evidence = result.get('evidence_dir')
            if isinstance(raw_evidence, str):
                directory = Path(raw_evidence).resolve()
                expected = (PROFILES_DIR / profile_id / 'automation_evidence').resolve()
                if directory.parent == expected and directory.is_dir():
                    update(record, evidence_dir=str(directory))
            raise RuntimeError(result.get('error') or 'Facebook Live verification failed')
        expected_state = result.get('state') if action == 'inspect' else {'prepare': 'prepared', 'start': 'live', 'end': 'ended'}[action]
        allowed_states = {'live', 'ended', 'unconfirmed'} if action == 'inspect' else {expected_state}
        if (expected_state not in allowed_states or result.get('state') != expected_state or result.get('brain') != pin):
            raise RuntimeError('Facebook Live returned an unverified browser workflow result')
        update(record, browser_template=result.get('template_id'), browser_state=expected_state)
        live_event(record, action + '_' + expected_state)
        return expected_state
    finally:
        stopped.cancel()
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        if not communication.done():
            communication.cancel()
        await asyncio.gather(communication, stopped, return_exceptions=True)


def encoder_command(profile_id, video, duration, has_audio, session_id, loop=False):
    script = (AUTOMATION_RUNNER.parent / 'live_encoder.py').read_text()
    args = ['docker', 'exec', '-i', '-u', 'chromeuser', f'isolated_{profile_id}',
            'python3', '-u', '-c', script, '--video', video, '--duration', str(duration),
            '--pidfile', f'/tmp/live-{session_id}.pid', '--control', f'/tmp/live-{session_id}.play']
    if loop:
        args.append('--loop')
    if has_audio:
        args.append('--has-audio')
    return args


async def start_live(profile_id, filename, loop=False, max_duration_seconds=None, muted=False, title='', caption='', pinned_comment=None, playback_mode=None):
    profile_path(profile_id)
    details = validate_details(title, caption, pinned_comment)
    playback = validate_playback(loop, max_duration_seconds, muted, playback_mode)
    video = resolve_video(filename)
    settings = validate_settings(read_json(settings_path(profile_id)))
    if profile_id in CONTROLS or automation.is_task_process_active(automation.active_automation_tasks.get(profile_id)):
        raise RuntimeError('This profile already has an active task')
    cfg = automation.get_scheduler_config()
    check = automation.can_acquire_scheduler_slot(automation.load_posting_queue(),
            automation.active_in_memory_scheduler_leases(), 'publisher', profile_id, cfg)
    if not check['allowed']:
        raise RuntimeError('Scheduler capacity unavailable: ' + check['reason'])
    if automation.get_container_status(profile_id) != 'running':
        admission = automation.claim_container_start_admission()
        if not admission['allowed']:
            raise RuntimeError('Container admission unavailable: ' + admission['reason'])
    timestamp = now()
    session_id = uuid.uuid4().hex
    token = lifecycle.acquire(profile_id, 'live:' + session_id, deadline_seconds=MAX_VIDEO_SECONDS + 600)
    record = dict(profile_id=profile_id, task='live', session_id=session_id, status='running', stage='preparing',
                  started_at=timestamp, ended_at=None, filename=filename, logs=[], error=None,
                  elapsed_seconds=0, broadcast_confirmed=False, process=None, lifecycle_active=True,
                  container_owner_token=token, last_activity_at=timestamp,
                  scheduler_lease=dict(lease_id=session_id, owner_id=MANAGER_INSTANCE_ID, kind='publisher',
                    profile_id=profile_id, claimed_at=timestamp, heartbeat_at=timestamp,
                    expires_at=datetime.fromtimestamp(time.time() + cfg['lease_ttl_ms']/1000, timezone.utc).isoformat()))
    record.update(playback, **details)
    stop_event = asyncio.Event()
    CONTROLS[profile_id] = stop_event
    automation.active_automation_tasks[profile_id] = record
    try:
        archive_previous_session(profile_id)
        public = update(record)
        context = lifecycle.OWNER_TOKEN.set(token)
        try:
            asyncio.create_task(run_live(record, settings, video, stop_event))
        finally:
            lifecycle.OWNER_TOKEN.reset(context)
        return public
    except BaseException:
        CONTROLS.pop(profile_id, None)
        record['lifecycle_active'] = False
        record['status'] = 'failed'
        await lifecycle.release(profile_id, token, automation._stop_container_with_retries)
        raise


def stop_live(profile_id, execution_id=None):
    stop = CONTROLS.get(profile_id)
    if not stop:
        raise RuntimeError('No active Live session')
    record = automation.active_automation_tasks[profile_id]
    if execution_id and record.get('queue_execution_id') != execution_id:
        raise RuntimeError('This Live execution is no longer active')
    stop.set()
    update(record, 'stopping')
    return {'success': True, 'message': 'Stopping the stream and ending the Facebook broadcast'}


def clear_review(profile_id, from_queue=False):
    if profile_id in CONTROLS or profile_id in lifecycle.snapshot():
        raise RuntimeError('Wait for the profile stream and container to stop before clearing review')
    state = get_status(profile_id)
    if state['status'] != 'needs_review':
        raise RuntimeError('This session does not need review')
    if state.get('queue_execution_id') and not from_queue:
        raise RuntimeError('Resolve this Live execution in the batch queue and confirm the broadcast has ended')
    state.update(status='stopped', stage='reviewed', error=None, reviewed_at=now())
    private_write(state_path(profile_id), state)
    return state


async def stop_encoder(record):
    proc = record.get('process')
    if proc:
        # Killing docker exec alone does not kill its in-container process.
        pidfile = f"/tmp/live-{record['session_id']}.pid"
        try:
            await command('docker', 'exec', '-u', 'chromeuser', f"isolated_{record['profile_id']}",
                'sh', '-c', 'if [ -f "$1" ]; then kill -TERM "$(cat "$1")" 2>/dev/null || true; fi', 'live', pidfile, timeout=5)
        except Exception:
            pass  # Container ownership cleanup below remains mandatory.
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()


async def run_live(record, settings, video, stop_event):
    profile_id = record['profile_id']
    readers = []
    stream_attempted = False
    try:
        # Pin one version for prepare/start/end. Changes in the catalog cannot
        # switch templates halfway through an irreversible broadcast.
        package = resolve_live_brain(AUTOMATION_RUNNER.parent, ROOT_DIR / 'automation')
        record.update(brain=package.metadata(), brain_package=str(package.root))
        update(record, 'preparing')
        await automation.ensure_profile_container_ready(profile_id)
        if stop_event.is_set():
            raise RuntimeError('Live setup was stopped')
        raw = await command('docker', 'exec', '-u', 'chromeuser', f'isolated_{profile_id}', 'ffprobe',
            '-v', 'error', '-show_format', '-show_streams', '-of', 'json', video)
        metadata = json.loads(raw)
        duration = float(metadata.get('format', {}).get('duration', 0))
        if not math.isfinite(duration) or duration < 1 or duration > MAX_VIDEO_SECONDS:
            raise ValueError('Live videos must have a known duration between 1 second and 4 hours')
        if not any(s.get('codec_type') == 'video' for s in metadata.get('streams', [])):
            raise ValueError('This file has no video track')
        has_audio = any(s.get('codec_type') == 'audio' for s in metadata.get('streams', []))
        playback = validate_playback(record.get('loop', False), record.get('max_duration_seconds'), record.get('muted', False), record.get('playback_mode'))
        if playback['max_duration_seconds'] is not None:
            duration = playback['max_duration_seconds'] if playback['loop'] else min(duration, playback['max_duration_seconds'])
        has_audio = has_audio and not playback['muted']
        update(record, duration_seconds=duration, source_duration_seconds=float(metadata['format']['duration']))
        await browser_action(profile_id, 'prepare', settings['producer_url'], stop_event)
        if stop_event.is_set():
            raise RuntimeError('Live setup was stopped')
        # Ingest itself may trigger configured Facebook automatic start. Persist before sending any bytes.
        update(record, 'stream_start_intent')
        stream_attempted = True
        proc = await asyncio.create_subprocess_exec(*encoder_command(profile_id, video, duration, has_audio, record['session_id'], loop=playback['loop']),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        record['process'] = proc
        proc.stdin.write((settings['server_url'] + settings['stream_key'] + '\n').encode())
        await proc.stdin.drain()
        proc.stdin.close()
        update(record, 'sending')

        playback_ready = asyncio.Event()
        startup_began = time.monotonic()

        async def progress():
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                value = line.decode(errors='replace').strip()
                if value == 'phase=playback':
                    update(record, 'live', elapsed_seconds=0, playback_started_at=now(),
                           startup_elapsed_seconds=time.monotonic() - startup_began)
                    live_event(record, 'playback_started_from_beginning')
                    playback_ready.set()
                if value.startswith('out_time_us='):
                    try:
                        update(record, elapsed_seconds=max(0, int(value.split('=', 1)[1])/1_000_000))
                    except ValueError:
                        pass
                    except Exception:
                        stop_event.set()
                        raise

        reader = asyncio.create_task(progress())
        readers.append(reader)
        stopped = asyncio.create_task(stop_event.wait())
        finished = asyncio.create_task(proc.wait())
        readers.extend([stopped, finished])
        starting = asyncio.create_task(browser_action(profile_id, 'start', settings['producer_url'], stop_event))
        readers.append(starting)
        update(record, 'starting')
        done, _ = await asyncio.wait([starting, finished, stopped], return_when=asyncio.FIRST_COMPLETED)
        if starting not in done:
            # Cancel and reap the click-capable helper before observing the final screen.
            starting.cancel()
            await asyncio.gather(starting, return_exceptions=True)
            if finished in done:
                update(record, encoder_exit_code=proc.returncode)
                live_event(record, 'video_finished' if proc.returncode == 0 else 'encoder_failed')
            else:
                live_event(record, 'stop_requested_before_confirmation')
            await stop_encoder(record)
            try:
                final_state = await asyncio.wait_for(
                    browser_action(profile_id, 'inspect', settings['producer_url'], asyncio.Event()), 30)
                update(record, final_browser_state=final_state)
                if final_state == 'live':
                    update(record, broadcast_confirmed=True)
                    await browser_action(profile_id, 'end', settings['producer_url'], asyncio.Event())
                    if finished not in done or proc.returncode == 0:
                        update(record, 'ended', status='stopped' if stop_event.is_set() else 'completed', ended_at=now())
                        return
            except Exception:
                live_event(record, 'final_state_check_failed')
            reason = ('Video finished before Facebook Live could be confirmed' if finished in done and proc.returncode == 0
                      else 'Stream stopped before Facebook confirmed the broadcast')
            raise RuntimeError(reason + '; check Facebook before retrying')
        await starting
        update(record, broadcast_confirmed=True)
        if not stop_event.is_set():
            # This only switches an already connected encoder. Never reconnect
            # or repeat Go Live after the durable publication boundary.
            await command('docker', 'exec', '-u', 'chromeuser', f'isolated_{profile_id}',
                          'touch', f"/tmp/live-{record['session_id']}.play")
            acknowledged = asyncio.create_task(playback_ready.wait())
            readers.append(acknowledged)
            ready, _ = await asyncio.wait([acknowledged, finished, stopped], timeout=15,
                                          return_when=asyncio.FIRST_COMPLETED)
            if not stop_event.is_set() and (acknowledged not in ready or (finished in ready and proc.returncode != 0)):
                raise RuntimeError('Live was confirmed but video playback could not start; check Facebook')
        else:
            update(record, 'live')
        done, _ = await asyncio.wait([stopped, finished], timeout=duration + 60, return_when=asyncio.FIRST_COMPLETED)
        if not done or (finished in done and proc.returncode != 0):
            raise RuntimeError('The video stream disconnected; check Facebook before starting another broadcast')
        if finished in done:
            update(record, encoder_exit_code=proc.returncode)
            live_event(record, 'playback_completed')
        update(record, 'ending')
        if stop_event.is_set():
            await stop_encoder(record)
        # End the existing session only; never create/retry a broadcast during cleanup.
        await browser_action(profile_id, 'end', settings['producer_url'], asyncio.Event())
        update(record, 'ended', status='stopped' if stop_event.is_set() else 'completed', ended_at=now())
    except BaseException as error:
        message = str(error) if isinstance(error, (ValueError, RuntimeError)) else 'Live session interrupted; check Facebook'
        try:
            update(record, 'needs_review' if stream_attempted else 'failed',
                   status='needs_review' if stream_attempted else ('stopped' if stop_event.is_set() else 'failed'),
                   error=message, ended_at=now())
        except RuntimeError:
            pass  # Local intent/review was saved; queue fencing must not prevent cleanup.
    finally:
        for reader in readers:
            if not reader.done():
                reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        await stop_encoder(record)
        try:
            await automation.stop_profile_container_after_report(profile_id)
        except Exception:
            try:
                update(record, 'needs_review', status='needs_review', error='Container cleanup failed; stop the profile and check Facebook')
            except RuntimeError:
                pass
        try:
            if record.get('queue_execution_id'):
                sync_queue(record, finalize=True)
        finally:
            record.update(process=None, lifecycle_active=False, scheduler_lease=None)
            CONTROLS.pop(profile_id, None)
