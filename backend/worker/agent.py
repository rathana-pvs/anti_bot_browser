"""Outbound WSS connection with durable acceptance and reconnect reconciliation."""
import asyncio
import hashlib
import json
import logging
import random
import time
from datetime import datetime, timezone

import httpx
from websockets.asyncio.client import connect

from .adapter import TERMINAL, local_execution_id
from .protocol import Job, Message, envelope, utcnow

logger = logging.getLogger(__name__)


class WorkerAgent:
    def __init__(self, config, journal, adapter, transfers):
        self.config, self.journal = config, journal
        self.adapter, self.transfers = adapter, transfers
        self.ready = False
        self.connected = False
        self.grants = {}
        self.last_heartbeat_ack = 0.0
        self.last_error = None
        self._stop = asyncio.Event()
        self._heartbeat_ids = set()
        self._uploads = asyncio.Queue(maxsize=100)
        identity = journal.setting('worker_id')
        if identity and identity != config.worker_id:
            raise ValueError('Journal belongs to a different worker; reconcile before changing identity')
        endpoint = journal.setting('server_url')
        if endpoint and endpoint != config.server_url:
            raise ValueError('Journal belongs to a different server; reconcile before migrating')
        for row in journal.attempts():
            if row['state'] != 'terminal' and row['profile_id']:
                account_id = json.loads(row['job'])['account_id']
                if config.account_profiles.get(account_id) != row['profile_id']:
                    raise ValueError('Cannot remap an account with an unresolved local attempt')
        with journal.db:
            journal.set_setting('worker_id', config.worker_id)
            journal.set_setting('server_url', config.server_url)

    def status(self):
        return {'enabled': True, 'worker_id': self.config.worker_id,
                'connected': self.connected, 'reconciled': self.ready,
                'paused': self.journal.setting('paused', False),
                'last_error': self.last_error,
                'active_attempts': sum(row['state'] != 'terminal' for row in self.journal.attempts())}

    def authorized(self, attempt_id):
        return (self.ready and not self.journal.setting('paused', False)
                and self.grants.get(attempt_id, 0) > time.time())

    def cloud_execution_allowed(self, execution):
        if execution.get('cloud_worker_id') != self.config.worker_id:
            return False
        attempt = self.journal.get(execution.get('cloud_attempt_id'))
        return bool(attempt and attempt['state'] != 'terminal' and self.authorized(attempt['id'])
                    and attempt['local_id'] == execution.get('execution_id'))

    async def stop(self):
        self.ready = False
        self._stop.set()

    async def run(self):
        failures = 0
        while not self._stop.is_set():
            try:
                async with connect(self.config.server_url,
                                   additional_headers={'Authorization': 'Bearer ' + self.config.credential.get_secret_value()},
                                   proxy=None, ping_interval=20, ping_timeout=20,
                                   open_timeout=15, close_timeout=5, max_size=256 * 1024) as socket:
                    self.connected = True
                    await self.session(socket)
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # Exceptions can include credential-bearing URLs; log the class only.
                self.last_error = type(error).__name__
                logger.warning('Worker disconnected (%s)', self.last_error)
                failures += 1
            finally:
                self.connected = self.ready = False
                self.grants.clear()
            delay = min(60, 2 ** min(failures, 6)) * random.uniform(.75, 1.25)
            try:
                await asyncio.wait_for(self._stop.wait(), delay)
            except asyncio.TimeoutError:
                pass

    async def receive(self, socket):
        return Message.model_validate_json(await socket.recv())

    async def session(self, socket):
        self.ready = False
        self.grants.clear()
        self._heartbeat_ids.clear()
        hello = envelope('hello', {'worker_id': self.config.worker_id,
                                  'capabilities': ['photo', 'reel', 'png_evidence', 'pause_resume', 'cancel_pending'],
                                  'accounts': list(self.config.account_profiles),
                                  'profile_catalog': self.adapter.profile_catalog() if hasattr(self.adapter, 'profile_catalog') else None})
        await socket.send(json.dumps(hello))
        ack = await asyncio.wait_for(self.receive(socket), self.config.acknowledgement_timeout)
        if ack.type != 'hello_ack' or ack.reply_to != hello['message_id']:
            raise ValueError('Invalid protocol handshake')
        reconcile = envelope('reconcile', {'attempts': [
            {'attempt_id': row['id'], 'lease_token': row['token'],
             'local_execution_id': row['local_id'], 'state': row['state'], 'sequence': row['sequence']}
            for row in self.journal.attempts()]})
        await socket.send(json.dumps(reconcile))
        ack = await asyncio.wait_for(self.receive(socket), self.config.acknowledgement_timeout)
        if ack.type != 'reconcile_ack' or ack.reply_to != reconcile['message_id']:
            raise ValueError('Reconciliation required before dispatch')
        self.renew(ack.payload.get('leases', []))
        self.ready = True
        self.last_error = None
        self.last_heartbeat_ack = time.monotonic()
        tasks = [asyncio.create_task(function(socket)) for function in
                 (self.reader, self.heartbeat, self.sender, self.maintenance)]
        stopping = asyncio.create_task(self._stop.wait())
        try:
            done, _ = await asyncio.wait([*tasks, stopping], return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            self.ready = False
            for task in [*tasks, stopping]:
                task.cancel()
            await asyncio.gather(*tasks, stopping, return_exceptions=True)

    def renew(self, leases):
        if not isinstance(leases, list):
            raise ValueError('Invalid leases')
        for lease in leases:
            row = self.journal.get(lease['attempt_id'])
            if not row or row['token'] != lease['lease_token']:
                raise ValueError('Unknown or fenced lease renewal')
            expires = datetime.fromisoformat(lease['expires_at'].replace('Z', '+00:00'))
            if expires.tzinfo is None:
                raise ValueError('Lease expiry must include timezone')
            timestamp = expires.timestamp()
            if timestamp > time.time() + 300:
                raise ValueError('Lease expiry exceeds five-minute maximum')
            self.grants[row['id']] = timestamp
            if row['state'] == 'accepted' and timestamp > time.time():
                self.journal.state(row['id'], 'authorized')

    async def reader(self, socket):
        async for raw in socket:
            message = Message.model_validate_json(raw)
            await self.handle(message)

    async def handle(self, message):
        kind = message.type
        if kind == 'heartbeat_ack':
            if message.reply_to not in self._heartbeat_ids:
                raise ValueError('Unknown heartbeat acknowledgement')
            self._heartbeat_ids.remove(message.reply_to)
            self.renew(message.payload.get('leases', []))
            self.last_heartbeat_ack = time.monotonic()
        elif kind in ('event_ack', 'result_ack', 'command_result_ack', 'evidence_ack', 'job_reject_ack'):
            expected = {'event_ack': 'execution_event', 'result_ack': 'execution_result',
                        'command_result_ack': 'command_result', 'evidence_ack': 'evidence_uploaded',
                        'job_reject_ack': 'job_reject'}[kind]
            self.journal.acknowledge(message.reply_to, expected, message.attempt_id, message.lease_token)
        elif kind == 'job_offer':
            self.offer(message)
        elif kind == 'job_start':
            attempt_id, token = message.attempt()
            row = self.journal.get(attempt_id)
            if not row or row['token'] != token:
                raise ValueError('Unknown or fenced job start')
            if message.reply_to:
                self.journal.acknowledge(message.reply_to, 'job_accept', attempt_id, token)
            self.renew([{'attempt_id': attempt_id, 'lease_token': token,
                         'expires_at': message.payload['expires_at']}])
        elif kind == 'command':
            self.command(message)
        elif kind == 'evidence_upload':
            attempt_id, token = message.attempt()
            row = self.journal.get(attempt_id)
            if not row or row['token'] != token or row['state'] != 'terminal':
                raise ValueError('Evidence requires the completed attempt and current token')
            if set(message.payload) != {'filename', 'upload_url'}:
                raise ValueError('Invalid evidence upload request')
            self.transfers.validate_url(message.payload['upload_url'])
            self.adapter.evidence_file(row, message.payload['filename'])
            self._uploads.put_nowait(message)
        else:
            raise ValueError('Unsupported worker message')

    def offer(self, message):
        attempt_id, token = message.attempt()
        try:
            job = Job.model_validate(message.payload)
            # Capability intentionally mirrors the current single-file runner.
            if (job.task == 'reel') != (job.media[0].extension in ('.mp4', '.mov')):
                raise ValueError('Media type does not match task')
            previous = self.journal.get(attempt_id)
            if not previous:
                if not self.ready or self.journal.setting('paused', False):
                    raise ValueError('Worker is not accepting work')
                if sum(row['state'] != 'terminal' for row in self.journal.attempts()) >= self.config.max_accepted_jobs:
                    raise ValueError('Accepted-work capacity reached')
                self.adapter.profile(job.account_id)
                self.transfers.validate_url(job.media[0].download_url)
            self.journal.accept(message, job.model_dump(), local_execution_id(self.config.worker_id, attempt_id),
                                previous['profile_id'] if previous else self.config.account_profiles[job.account_id])
        except ValueError:
            self.journal.queue(envelope('job_reject', {'reason': 'invalid_or_unavailable_job'},
                                        attempt_id=attempt_id, lease_token=token, reply_to=message.message_id))

    def command(self, message):
        action = message.payload.get('action')
        if set(message.payload) != {'action'}:
            raise ValueError('Invalid command payload')
        if action in ('pause_new_jobs', 'resume_new_jobs'):
            if message.attempt_id or message.lease_token:
                raise ValueError('Pause/resume is a worker-level command')
            def apply():
                paused = action == 'pause_new_jobs'
                self.journal.set_setting('paused', paused)
                return {'status': 'applied', 'paused': paused}
        elif action == 'cancel_attempt':
            attempt_id, token = message.attempt()
            row = self.journal.get(attempt_id)
            if not row or row['token'] != token:
                raise ValueError('Unknown or fenced cancellation')
            def apply():
                if row['state'] == 'terminal':
                    return {'status': 'too_late'}
                outcome = self.adapter.cancel(row['local_id'])
                if outcome in ('not_submitted', 'cancelled'):
                    self.journal.observation(attempt_id, {'status': 'cancelled', 'stage': 'cancelled',
                        'evidence': []}, terminal=True)
                    self.grants.pop(attempt_id, None)
                    return {'status': 'cancelled'}
                return {'status': 'too_late'}
        else:
            raise ValueError('Unsupported command')
        self.journal.command(message, apply)

    async def heartbeat(self, socket):
        while not self._stop.is_set():
            if time.monotonic() - self.last_heartbeat_ack > self.config.acknowledgement_timeout:
                raise TimeoutError('Application heartbeat acknowledgement expired')
            rows = [row for row in self.journal.attempts() if row['state'] != 'terminal']
            message = envelope('heartbeat', {'worker_id': self.config.worker_id, 'timestamp': utcnow(),
                'paused': self.journal.setting('paused', False),
                'local_capacity': self.adapter.capacity() if hasattr(self.adapter, 'capacity') else {},
                'profile_catalog': self.adapter.profile_catalog() if hasattr(self.adapter, 'profile_catalog') else None,
                'available_slots': 0 if self.journal.setting('paused', False) else max(0, self.config.max_accepted_jobs - len(rows)),
                'attempts': [{'attempt_id': row['id'], 'lease_token': row['token'],
                              'state': row['state']} for row in rows]})
            self._heartbeat_ids.add(message['message_id'])
            await socket.send(json.dumps(message))
            await asyncio.sleep(self.config.heartbeat_seconds)

    async def sender(self, socket):
        sent = {}
        while not self._stop.is_set():
            for message in self.journal.pending():
                identifier = message['message_id']
                if time.monotonic() - sent.get(identifier, -100) >= 5:
                    await socket.send(json.dumps(message))
                    sent[identifier] = time.monotonic()
            pending_ids = {message['message_id'] for message in self.journal.pending()}
            sent = {key: value for key, value in sent.items() if key in pending_ids}
            await asyncio.sleep(.25)

    async def maintenance(self, socket):
        while not self._stop.is_set():
            await self.tick()
            if not self._uploads.empty():
                message = self._uploads.get_nowait()
                row = self.journal.get(message.attempt_id)
                try:
                    file = self.adapter.evidence_file(row, message.payload['filename'])
                    await self.transfers.upload(message.payload['upload_url'], file)
                    self.journal.queue(envelope('evidence_uploaded', {
                        'filename': file.name, 'size_bytes': file.stat().st_size,
                        'sha256': hashlib.sha256(file.read_bytes()).hexdigest()},
                        attempt_id=row['id'], lease_token=row['token'], reply_to=message.message_id))
                except (httpx.HTTPError, ValueError, OSError, RuntimeError) as error:
                    # The server may reissue a fresh grant; publication result is unchanged.
                    self.last_error = type(error).__name__
            await asyncio.sleep(1)

    async def tick(self):
        for row in self.journal.attempts():
            if row['state'] == 'terminal':
                continue
            snapshot = self.adapter.snapshot(row['local_id'])
            if row['state'] in ('submitting', 'submitted') and not snapshot:
                self.journal.observation(row['id'], {'status': 'needs_review', 'stage': 'unknown',
                    'error_code': 'local_execution_missing', 'evidence': []}, terminal=True)
                continue
            if row['state'] == 'authorized' and self.authorized(row['id']):
                try:
                    job = Job.model_validate_json(row['job'])
                    filename = 'worker/' + await self.transfers.download(row['id'], job.media[0])
                    if not self.authorized(row['id']):
                        continue
                    self.journal.state(row['id'], 'submitting')
                    self.adapter.submit(row, filename)
                    self.journal.state(row['id'], 'submitted')
                    snapshot = self.adapter.snapshot(row['local_id'])
                except (httpx.HTTPError, ValueError, OSError, RuntimeError) as error:
                    self.journal.observation(row['id'], {'status': 'failed_before_publish',
                        'stage': 'media_or_admission_failed', 'error_code': type(error).__name__, 'evidence': []}, terminal=True)
                    continue
            if snapshot:
                status = snapshot.get('status', 'unknown')
                terminal = status in TERMINAL or status.startswith('skipped_')
                mapped = 'needs_review' if status in ('uncertain', 'needs_review', 'failed_after_publish') else (
                    'failed_before_publish' if status.startswith('skipped_') else status)
                payload = {'status': mapped, 'stage': snapshot.get('stage', 'unknown'),
                           'history': [{'stage': item.get('stage'), 'timestamp': item.get('timestamp')}
                                       for item in snapshot.get('stage_history', [])[-100:]
                                       if isinstance(item, dict)]}
                if terminal:
                    payload.update(evidence=self.adapter.evidence_manifest(row),
                                   first_comment_status=snapshot.get('first_comment_status'),
                                   error_code='local_execution_failure' if snapshot.get('error') else None)
                    # Permalinks are validated by the existing backend; no raw logs/paths leave the host.
                    if snapshot.get('post_url') and snapshot.get('permalink_status') == 'verified':
                        payload['permalink'] = snapshot['post_url']
                self.journal.observation(row['id'], payload, terminal=terminal)
