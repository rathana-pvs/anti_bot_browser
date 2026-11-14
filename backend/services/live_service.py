"""Local, single-file Facebook Live broadcasts. Keys never enter task state/logs."""
import asyncio
import hashlib
import json
import math
import os
import re
import random
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from automation.engine.live_brain import resolve_live_brain
from automation.live_encoder import ENCODER_FAILURE_REASONS
from automation.live_comment_status import COMMENT_CODES, COMMENT_ACTION_SECONDS
from automation.live_account_status import ACCOUNT_BLOCK_ERRORS

from backend.config import DATA_DIR, PROFILES_DIR, SHARED_MEDIA_DIR, AUTOMATION_PYTHON, AUTOMATION_RUNNER, ROOT_DIR, MANAGER_INSTANCE_ID
from backend.services import automation_service as automation, container_lifecycle as lifecycle
from backend.services.android_devices import ensure_not_android

LIVE_DIR = DATA_DIR / 'live'
DEFAULT_SERVER_URL = 'rtmps://live-api-s.facebook.com:443/rtmp/'
MAX_VIDEO_SECONDS = 4 * 60 * 60
LIVE_SESSION_LIMIT_SECONDS = 120
LIVE_END_RESERVE_SECONDS = 20
ACTIVE_STAGES = {'preparing', 'stream_start_intent', 'sending', 'starting', 'live', 'ending', 'commenting', 'stopping'}
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


def launch_settings(profile_id):
    ensure_not_android(profile_id, 'Live video')
    # Stored keys are legacy configuration and must never be a sender fallback.
    # The key is copied from Live Producer, so a new account needs no saved settings.
    return validate_settings({'server_url': DEFAULT_SERVER_URL, **read_json(settings_path(profile_id)), 'stream_key': ''}, require_key=False)


def acquire_stream_destination(settings):
    """Exclude competing senders after the actual prepared key is known."""
    digest = hashlib.sha256((settings['server_url'] + settings['stream_key']).encode()).hexdigest()
    root = LIVE_DIR / 'destination_locks'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    stream = (root / (digest + '.lock')).open('a+b')
    os.chmod(stream.name, 0o600)
    try:
        if os.name == 'nt':
            import msvcrt
            if not stream.tell():
                stream.write(b'\0'); stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return stream
    except OSError:
        stream.close()
        raise RuntimeError('Another local sender owns this Facebook broadcast; no video was sent') from None


def validate_settings(value, *, require_key=True):
    url = str(value.get('server_url', '')).strip()
    parsed = urlsplit(url)
    if (parsed.scheme != 'rtmps' or parsed.hostname != 'live-api-s.facebook.com'
            or parsed.port not in (None, 443) or parsed.path.rstrip('/') != '/rtmp'
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('Use the Facebook RTMPS server URL ending in /rtmp/')
    key = str(value.get('stream_key', '')).strip()
    if (key or require_key) and not re.fullmatch(r'[A-Za-z0-9_-]{10,512}', key):
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


def validate_end_after_comment(enabled, comment):
    if not isinstance(enabled, bool):
        raise ValueError('End after comment must be true or false')
    if enabled and (not isinstance(comment, str) or not comment.strip()):
        raise ValueError('Enter a Live comment before enabling End Live after comment succeeds')
    # A supplied comment is the completion target, including older queued jobs
    # whose optional end-after-comment flag was saved as false.
    return isinstance(comment, str) and bool(comment.strip())


def settings_path(profile_id):
    profile_path(profile_id)
    return LIVE_DIR / profile_id / 'settings.json'


def get_settings(profile_id):
    settings = read_json(settings_path(profile_id))
    return {'server_url': settings.get('server_url', DEFAULT_SERVER_URL),
            'producer_url': settings.get('producer_url', 'https://www.facebook.com/live/producer/'),
            'key_saved': bool(settings.get('stream_key'))}


def save_settings(profile_id, value):
    path = settings_path(profile_id)
    if profile_id in CONTROLS:
        raise RuntimeError('Stop the current broadcast before changing settings')
    previous = read_json(path)
    # Empty password input preserves a previously saved key; GET never returns it.
    settings = validate_settings({**value, 'stream_key': value.get('stream_key') or previous.get('stream_key', '')}, require_key=False)
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
        # stream_start_intent is durably recorded before sending any bytes.
        # A setup-only interruption must not become an unresolved broadcast and
        # lock a queue attempt already recovered as failed_before_publish.
        if saved.get('stage') == 'preparing' and not saved.get('broadcast_confirmed'):
            saved.update(status='failed', stage='failed', ended_at=now(),
                         error='Live setup was interrupted before streaming began')
        else:
            # Once ingest might have started, a crash never authorizes replay.
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
              'ended_at', 'filename', 'duration_seconds', 'elapsed_seconds', 'error', 'logs', 'broadcast_confirmed', 'queue_execution_id', 'loop', 'max_duration_seconds', 'muted', 'brain', 'browser_template', 'browser_state', 'encoder_exit_code', 'final_browser_state', 'playback_mode', 'source_duration_seconds', 'startup_elapsed_seconds', 'playback_started_at', 'evidence_dir', 'comment_status', 'comment_error_code', 'end_after_comment', 'completion_reason') if k in record}
    private_write(state_path(record['profile_id']), public)
    if record.get('queue_execution_id'):
        sync_queue(record)
    return public


def live_event(record, event, *, message=None):
    record.setdefault('logs', []).append({'timestamp': now(), 'event': event, 'level': 'STEP', 'message': message or event.replace('_', ' ').capitalize()})
    update(record)


def sync_queue(record, finalize=False):
    def persist():
        queue = automation.load_posting_queue()
        match = automation.find_queue_execution(queue, record['queue_execution_id'])
        if not match:
            raise RuntimeError('Live queue ownership lost; check Facebook')
        execution = match['execution']
        lease = record.get('scheduler_lease') or {}
        lease_id = lease.get('lease_id')
        current = (execution.get('scheduler_lease') or {}).get('lease_id')
        # A confirmed terminal success must still be recorded if a delayed
        # heartbeat let the scheduler reclaim this attempt's own lease during the
        # long End/comment/verify/finish tail, as long as no other owner has
        # taken over: a new owner holds a different lease, never an absent one.
        # This records an outcome that already happened; it never republishes.
        confirmed_success = (finalize and record.get('status') in ('completed', 'stopped')
                             and record.get('broadcast_confirmed'))
        reclaimed_by_self = (current is None and lease_id is not None
                             and (execution.get('last_scheduler_lease') or {}).get('lease_id') == lease_id)
        if current != lease_id and not (confirmed_success and reclaimed_by_self):
            raise RuntimeError('Live queue ownership lost; check Facebook')
        for key in ('elapsed_seconds', 'duration_seconds', 'broadcast_confirmed', 'error', 'session_id', 'brain', 'browser_template', 'browser_state', 'logs', 'encoder_exit_code', 'final_browser_state', 'playback_mode', 'source_duration_seconds', 'startup_elapsed_seconds', 'playback_started_at', 'evidence_dir', 'comment_status', 'comment_error_code', 'end_after_comment', 'completion_reason'):
            if key in record:
                execution[key] = record[key]
        stage = record.get('stage')
        if stage != execution.get('stage'):
            execution.setdefault('stage_history', []).append({'stage': stage, 'timestamp': now()})
        execution['stage'] = stage
        execution['stage_updated_at'] = now()
        # Retain the slot until the encoder and container have both stopped.
        execution['status'] = 'running'
        # A progress write is proof of liveness. Renew this attempt's own lease
        # so the TTL cannot lapse mid-tail and let the scheduler reclaim an
        # attempt that is still running. Only ever extends a lease we hold.
        if not finalize and current == lease_id and lease_id is not None:
            if automation.refresh_execution_lease(execution, lease_id, automation.get_scheduler_config()):
                record['scheduler_lease'] = dict(execution['scheduler_lease'])
        if finalize:
            status = record['status']
            execution['status'] = ('completed' if status in ('completed', 'stopped') and record.get('broadcast_confirmed')
                                   else 'skipped_auth_required' if status == 'skipped_auth_required'
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
        settings = launch_settings(profile_id)
        record.update(validate_details(post.get('title', ''), execution.get('spun_caption', post.get('base_caption', '')), execution.get('pinned_comment', post.get('pinned_comment'))))
        record['end_after_comment'] = validate_end_after_comment(execution.get('end_after_comment', post.get('end_after_comment', False)), record.get('pinned_comment'))
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


def live_evidence_folder(record, profile_id):
    """The Live job's one screenshot folder, made at its first browser step. Every step (ready,
    live, ended, comment, check) saves into it, so the job shows them together and in order."""
    root = (PROFILES_DIR / profile_id / 'automation_evidence').resolve()
    current = Path(record['evidence_dir']).resolve() if record.get('evidence_dir') else None
    if current and current.parent == root and current.is_dir():
        return str(current)
    if not record:
        return None
    directory = root / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ') + '_live')
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    update(record, evidence_dir=str(directory))
    return str(directory)


async def browser_action(profile_id, action, producer_url, stop_event, *, prepared_settings=None):
    helper = AUTOMATION_RUNNER.parent / 'live_browser.py'
    record = automation.active_automation_tasks.get(profile_id, {})
    pin = record.get('brain')
    if not pin or not record.get('brain_package'):
        raise RuntimeError('Live browser workflow was not pinned before execution')
    details = validate_details(record.get('title', ''), record.get('caption', ''), record.get('pinned_comment')) if action in {'prepare', 'comment'} else {}
    if action == 'comment':
        details['comment_after_end'] = record.get('end_before_comment') is True
    worker_env = automation.automation_worker_env()
    worker_env['AUTOMATION_BEHAVIOR_RUN_ID'] = str(record.get('session_id') or profile_id)
    folder = live_evidence_folder(record, profile_id)
    if folder:
        worker_env['LIVE_EVIDENCE_DIR'] = folder
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
            if action == 'finish' and message.get('event') in {
                    'optional_finish_news_feed_opened', 'optional_finish_warming_started',
                    'optional_finish_warming_completed'}:
                live_event(record, message['event'])
            if message.get('event') in {'go_live_requested', 'go_live_click_sent', 'go_live_dialog_requested',
                                        'go_live_dialog_click_sent', 'end_live_requested', 'end_live_click_sent',
                                        'end_confirmation_requested', 'end_confirmation_click_sent',
                                        'live_comment_submit_requested', 'live_comment_submit_sent',
                                        'live_comment_accepted_while_posting',
                                        'live_profile_comment_fallback_requested', 'live_profile_recent_post_verified',
                                        'live_profile_comment_input_found'}:
                live_event(record, message['event'])
                if (action == 'end' and (record.get('comment_status') == 'confirmed'
                                        or record.get('end_before_comment') is True)
                        and message['event'] == 'end_confirmation_click_sent'):
                    # End is submitted either before the comment or after a
                    # confirmed comment. Do not wait for dashboard recognition.
                    record['end_action_sent'] = True
                    return {'success': True, 'state': 'ended', 'brain': pin,
                            'end_action_only': True}
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
        raw_evidence = result.get('evidence_dir')
        if isinstance(raw_evidence, str) and not folder:  # the job's own folder, once made, stays its folder
            directory = Path(raw_evidence).resolve()
            expected = (PROFILES_DIR / profile_id / 'automation_evidence').resolve()
            if directory.parent == expected and directory.is_dir():
                update(record, evidence_dir=str(directory))
        if not result.get('success'):
            if result.get('account_block') in ACCOUNT_BLOCK_ERRORS:
                record['account_block'] = result['account_block']
                raise RuntimeError(ACCOUNT_BLOCK_ERRORS[result['account_block']])
            if action == 'comment':
                code = result.get('failure_code')
                update(record, comment_error_code=code if code in COMMENT_CODES else 'comment_failed')
            raise RuntimeError(result.get('error') or 'Facebook Live verification failed')
        expected_state = result.get('state') if action == 'inspect' else {'prepare': 'prepared', 'start': 'live', 'end': 'ended', 'comment': 'commented', 'finish': 'finished'}[action]
        allowed_states = {'live', 'ended', 'unconfirmed'} if action == 'inspect' else {expected_state}
        if (expected_state not in allowed_states or result.get('state') != expected_state or result.get('brain') != pin):
            raise RuntimeError('Facebook Live returned an unverified browser workflow result')
        if action == 'prepare' and prepared_settings is not None:
            key = result.get('prepared_stream_key')
            if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{10,512}', key):
                raise RuntimeError('The prepared Facebook stream key could not be verified; no video was sent')
            # Current-attempt memory only. Never save the copied key to disk or
            # put it in task state, queue, metadata, events or API responses.
            prepared_settings['stream_key'] = key
        action_only = action == 'end' and result.get('end_action_only') is True
        if action != 'finish':
            update(record, browser_template=result.get('template_id'),
                   browser_state='unconfirmed' if action_only else expected_state)
        if action == 'finish':
            outcome = result.get('finish_outcome')
            return outcome if outcome in {'logo_missing', 'feed_not_ready'} else expected_state
        live_event(record, 'end_action_completed' if action_only else action + '_' + expected_state)
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
    args += ['--max-session-seconds', str(LIVE_SESSION_LIMIT_SECONDS)]
    if loop:
        args.append('--loop')
    if has_audio:
        args.append('--has-audio')
    return args


async def start_live(profile_id, filename, loop=False, max_duration_seconds=None, muted=False, title='', caption='', pinned_comment=None, playback_mode=None, end_after_comment=False):
    profile_path(profile_id)
    ensure_not_android(profile_id, 'Live video')
    details = validate_details(title, caption, pinned_comment)
    details['end_after_comment'] = validate_end_after_comment(end_after_comment, pinned_comment)
    playback = validate_playback(loop, max_duration_seconds, muted, playback_mode)
    video = resolve_video(filename)
    settings = launch_settings(profile_id)
    if profile_id in CONTROLS or automation.is_task_process_active(automation.active_automation_tasks.get(profile_id)):
        raise RuntimeError('This profile already has an active task')
    cfg = automation.get_scheduler_config()
    check = automation.can_acquire_scheduler_slot(automation.load_posting_queue(),
            automation.active_in_memory_scheduler_leases(), 'publisher', profile_id, cfg, workload='live')
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
                  scheduler_lease=dict(lease_id=session_id, owner_id=MANAGER_INSTANCE_ID, kind='publisher', workload='live',
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


def failure_code(error):
    if isinstance(error, asyncio.CancelledError):
        return 'task_cancelled'
    if isinstance(error, TimeoutError):
        return 'action_timeout'
    if isinstance(error, (ValueError, RuntimeError)):
        return 'workflow_error'
    if isinstance(error, (TypeError, KeyError, AttributeError, OSError)):
        return type(error).__name__
    return 'unexpected_error' if error is not None else None


async def save_failure_diagnostics(record, failure_stage=None, error=None, cleanup_error=None):
    """Persist allowlisted diagnostics before cleanup, even without a browser.

    Never copy exception text, encoder stderr, settings or container logs here:
    they can contain credentials. Evidence failure must preserve the Live error.
    """
    try:
        reason = None
        encoder_details = {}
        if record.get('encoder_exit_code') not in (None, 0):
            try:
                raw = await command('docker', 'exec', '-u', 'chromeuser',
                    f"isolated_{record['profile_id']}", 'cat',
                    f"/tmp/live-{record['session_id']}.pid.diagnostic.json", timeout=5)
                diagnostic = json.loads(raw)
                candidate = diagnostic.get('failure_reason')
                if candidate in ENCODER_FAILURE_REASONS:
                    reason = candidate
                component = diagnostic.get('failure_component')
                if component in {'sender', 'relay'}:
                    encoder_details['failure_component'] = component
                for key in ('sender_exit_code', 'relay_exit_code'):
                    if type(diagnostic.get(key)) is int:
                        encoder_details[key] = diagnostic[key]
            except Exception:
                pass
        root = (PROFILES_DIR / record['profile_id'] / 'automation_evidence').resolve()
        directory = Path(record['evidence_dir']).resolve() if record.get('evidence_dir') else None
        if directory is None or directory.parent != root or not directory.is_dir():
            directory = root / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ') + '_live_failure')
            private_write(directory / 'result.json', {
                'run_id': directory.name, 'status': record['status'], 'finished_at': now(),
                'screenshot_available': False,
                'screenshot_unavailable_reason': 'no_browser_observation',
            })
        private_write(directory / 'diagnostics.json', {
            'captured_at': now(), 'status': record['status'],
            'failure_stage': failure_stage,
            'failure_code': failure_code(error),
            'cleanup_failure_code': failure_code(cleanup_error),
            'encoder_exit_code': record.get('encoder_exit_code'),
            'encoder_failure_reason': reason or ('unknown_encoder_failure'
                if record.get('encoder_exit_code') not in (None, 0) else None),
            'final_browser_state': record.get('final_browser_state'),
            'stream_attempted': record.get('stage') == 'needs_review',
            **encoder_details,
        })
        update(record, evidence_dir=str(directory))
    except Exception:
        pass


async def optional_finish(record, settings, stop_event):
    """Bounded idle/navigation after success; errors cannot change the outcome."""
    if record.get('comment_status') != 'confirmed' or stop_event.is_set():
        return
    def log(event, message):
        try:
            live_event(record, 'optional_finish_' + event, message=message)
        except Exception:
            pass  # Even a log-write failure must not change publication success.
    action = 'unknown'
    try:
        action = random.choice(('close', 'pause', 'news_feed'))
        log(action + '_selected', 'Optional finish selected: ' + {'close': 'close immediately', 'pause': 'brief pause', 'news_feed': 'open News Feed'}[action])
        if action == 'pause':
            seconds = random.uniform(2, 5)
            log('pause_started', f'Optional finish: waiting {seconds:.1f} seconds')
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=seconds)
            except asyncio.TimeoutError:
                pass
            else:
                log('pause_skipped', 'Optional pause interrupted; Live remains successful')
                return
        elif action == 'news_feed':
            outcome = await asyncio.wait_for(browser_action(record['profile_id'], 'finish',
                settings['producer_url'], stop_event), timeout=12)
            if outcome == 'logo_missing':
                log('news_feed_skipped', 'Optional News Feed action skipped: Facebook logo not found; Live remains successful')
                return
            if outcome == 'feed_not_ready':
                log('warming_skipped', 'Optional warming skipped: News Feed did not open; Live remains successful')
                return
        log(action + '_completed', 'Optional finish completed: ' + {'close': 'close immediately', 'pause': 'brief pause', 'news_feed': 'opened News Feed and completed brief warming'}[action])
    except (Exception, asyncio.CancelledError) as error:
        reason = 'timed out' if isinstance(error, asyncio.TimeoutError) else 'cancelled' if isinstance(error, asyncio.CancelledError) else 'action error'
        log(action + '_failed', f'Optional finish {action.replace("_", " ")}: {reason}; ignored, Live remains successful')


async def comment_after_live(record, settings, stop_event):
    if not (record.get('pinned_comment') or '').strip() or record.get('comment_status') or stop_event.is_set():
        return
    update(record, comment_status='attempted')
    live_event(record, 'live_comment_requested')
    try:
        await asyncio.wait_for(browser_action(record['profile_id'], 'comment', settings['producer_url'], stop_event),
                               COMMENT_ACTION_SECONDS)
        update(record, comment_status='confirmed', comment_error_code=None)
        live_event(record, 'live_comment_confirmed')
    except Exception as error:
        code = ('action_timeout' if isinstance(error, asyncio.TimeoutError)
                else record.get('comment_error_code') or 'comment_failed')
        update(record, comment_status='needs_review', comment_error_code=code)
        live_event(record, 'live_comment_needs_review')


async def end_broadcast(record, settings, stop_event):
    """Finish the existing broadcast despite cancellation of its caller.

    The helper has its own bounded action deadline. Keep ingest alive and
    profile ownership held until it finishes; never issue another Go Live.
    """
    update(record, 'ending')
    record['end_cleanup_attempted'] = True
    ending = asyncio.create_task(browser_action(record['profile_id'], 'end',
                                settings['producer_url'], asyncio.Event()))
    while True:
        try:
            await asyncio.shield(ending)
            break
        except asyncio.CancelledError:
            if ending.cancelled():
                raise
            stop_event.set()
            live_event(record, 'end_cleanup_cancellation_deferred')
    update(record, final_browser_state='unconfirmed' if record.get('end_action_sent') else 'ended')


async def run_live(record, settings, video, stop_event):
    profile_id = record['profile_id']
    readers = []
    stream_attempted = False
    browser_ready = False
    inspected = False
    destination_lock = None
    completion_pending = False
    limit_task = None
    try:
        # Pin one version for prepare/start/end. Changes in the catalog cannot
        # switch templates halfway through an irreversible broadcast.
        package = resolve_live_brain(AUTOMATION_RUNNER.parent, ROOT_DIR / 'automation')
        record.update(brain=package.metadata(), brain_package=str(package.root))
        update(record, 'preparing')
        await automation.ensure_profile_container_ready(profile_id)
        browser_ready = True
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
        duration = min(duration, LIVE_SESSION_LIMIT_SECONDS)
        has_audio = has_audio and not playback['muted']
        update(record, duration_seconds=duration, source_duration_seconds=float(metadata['format']['duration']))
        await browser_action(profile_id, 'prepare', settings['producer_url'], stop_event, prepared_settings=settings)
        if stop_event.is_set():
            raise RuntimeError('Live setup was stopped')
        if not re.fullmatch(r'[A-Za-z0-9_-]{10,512}', settings.get('stream_key', '')):
            raise RuntimeError('The prepared Facebook stream key could not be verified; no video was sent')
        destination_lock = acquire_stream_destination(settings)
        # Ingest itself may trigger configured Facebook automatic start. Persist before sending any bytes.
        update(record, 'stream_start_intent')
        stream_attempted = True
        async def enforce_live_limit():
            await asyncio.sleep(LIVE_SESSION_LIMIT_SECONDS - LIVE_END_RESERVE_SECONDS)
            stop_event.set()
            live_event(record, 'live_duration_limit_reached', message='Two-minute Live limit: ending the broadcast now')
        limit_task = asyncio.create_task(enforce_live_limit())
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
            try:
                inspected = True
                final_state = await asyncio.wait_for(
                    browser_action(profile_id, 'inspect', settings['producer_url'], asyncio.Event()), 30)
                update(record, final_browser_state=final_state)
                if final_state == 'live' and not record.get('end_cleanup_attempted'):
                    update(record, broadcast_confirmed=True)
                    await end_broadcast(record, settings, stop_event)
                    if stop_event.is_set() or finished not in done or proc.returncode == 0:
                        update(record, 'ended', status='stopped' if stop_event.is_set() else 'completed', ended_at=now())
                        return
            except Exception:
                live_event(record, 'final_state_check_failed')
            reason = ('Video finished before Facebook Live could be confirmed' if finished in done and proc.returncode == 0
                      else 'Stream stopped before Facebook confirmed the broadcast')
            raise RuntimeError(reason + '; check Facebook before retrying')
        await starting
        update(record, broadcast_confirmed=True)
        if (record.get('pinned_comment') or '').strip() and not stop_event.is_set():
            record['end_before_comment'] = True
            # End is requested now, so the broadcast limit must not stop the comment.
            limit_task.cancel()
            update(record, 'ending')
            live_event(record, 'ending_before_comment')
            await end_broadcast(record, settings, stop_event)
            await stop_encoder(record)
            # Keep the browser/container open for the ended video's comment.
            # Streaming is already stopped, so its startup budget no longer
            # limits comment entry or verification.
            update(record, 'commenting')
            live_event(record, 'comment_after_end_requested')
            await comment_after_live(record, settings, stop_event)
            if record.get('comment_status') != 'confirmed':
                raise RuntimeError('Live ended, but its comment was not confirmed; check the saved evidence before retrying the comment')
            completion_pending = True
            update(record, 'stopping', completion_reason='comment_verified_after_end')
            await optional_finish(record, settings, stop_event)
            return
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
        if not done or (finished in done and proc.returncode != 0 and not stop_event.is_set()):
            raise RuntimeError('The video stream disconnected; check Facebook before starting another broadcast')
        if finished in done:
            update(record, encoder_exit_code=proc.returncode)
            live_event(record, 'playback_completed')
        update(record, 'ending')
        # End the existing session only; never create/retry a broadcast during cleanup.
        await end_broadcast(record, settings, stop_event)
        update(record, 'ended', status='stopped' if stop_event.is_set() else 'completed', ended_at=now())
    except BaseException as error:
        failure_stage = record.get('stage')
        message = (str(error) if isinstance(error, (ValueError, RuntimeError)) else
                   f'Live interrupted during {failure_stage or "setup"} ({failure_code(error)}); check Facebook')
        encoder = record.get('process')
        if encoder is not None and encoder.returncode is not None:
            record['encoder_exit_code'] = encoder.returncode
        # Reap every click-capable helper before inspecting, including when the
        # service itself was cancelled while waiting for startup/playback.
        for reader in readers:
            if not reader.done():
                reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        # Capture the existing page only; inspection never navigates or clicks.
        cleanup_error = None
        if browser_ready and not inspected:
            try:
                final_state = await asyncio.wait_for(
                    browser_action(profile_id, 'inspect', settings['producer_url'], asyncio.Event()), 30)
                update(record, final_browser_state=final_state)
                # Cancellation before End began must still close an observed
                # broadcast. An ordinary failed End is not blindly retried.
                if final_state == 'live' and not record.get('end_cleanup_attempted'):
                    await end_broadcast(record, settings, stop_event)
                    final_state = 'ended'
                if (isinstance(error, asyncio.CancelledError) and final_state == 'ended'
                        and not record.get('end_before_comment')):
                    update(record, 'ended', status='stopped', error=None, ended_at=now())
                    return
            except Exception as inspection_error:
                cleanup_error = inspection_error
        try:
            blocked = record.get('account_block') in ACCOUNT_BLOCK_ERRORS
            skip = blocked and (not record.get('broadcast_confirmed') or record.get('end_action_sent')
                                or record.get('final_browser_state') == 'ended')
            if blocked:
                message = ACCOUNT_BLOCK_ERRORS[record['account_block']]
            update(record, 'skipped_auth_required' if skip else 'needs_review' if stream_attempted else 'failed',
                   status='skipped_auth_required' if skip else 'needs_review' if stream_attempted else ('stopped' if stop_event.is_set() else 'failed'),
                   error=message, ended_at=now())
        except RuntimeError:
            pass  # Local intent/review was saved; queue fencing must not prevent cleanup.
        await save_failure_diagnostics(record, failure_stage, error, cleanup_error)
    finally:
        if limit_task is not None:
            limit_task.cancel()
            await asyncio.gather(limit_task, return_exceptions=True)
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
        else:
            if completion_pending:
                update(record, 'ended', status='completed', error=None, ended_at=now())
        try:
            if record.get('queue_execution_id'):
                sync_queue(record, finalize=True)
        finally:
            if destination_lock is not None:
                destination_lock.close()
            record.update(process=None, lifecycle_active=False, scheduler_lease=None)
            CONTROLS.pop(profile_id, None)
