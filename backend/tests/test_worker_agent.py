"""Offline durability and simulated-server tests; never launches real browsers."""
import asyncio
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from websockets.asyncio.server import serve

from backend.worker.agent import WorkerAgent
from backend.worker.config import WorkerConfig
from backend.worker.journal import Journal
from backend.worker.protocol import Job, Message, envelope
from backend.worker.transfers import Transfers


def config(**overrides):
    return WorkerConfig(server_url='wss://server.example/ws/workers', worker_id='worker-1',
                        credential='secret', account_profiles={'account-1': 'profile-1'},
                        transfer_hosts=['storage.example'], **overrides).validate_connection()


def offer(attempt_id='attempt-1', **overrides):
    return Message.model_validate(envelope('job_offer', {
        'account_id': 'account-1', 'task': 'photo', 'caption': 'Hello', 'first_comment': None,
        'media': [{'asset_id': 'asset-1', 'download_url': 'https://storage.example/file',
                   'size_bytes': 5, 'sha256': hashlib.sha256(b'image').hexdigest(), 'extension': '.png'}]},
        attempt_id=attempt_id, lease_token='token-1', **overrides))


def grant(attempt_id='attempt-1', seconds=60):
    return {'attempt_id': attempt_id, 'lease_token': 'token-1',
            'expires_at': (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()}


class Adapter:
    def __init__(self):
        self.rows = {}
        self.submissions = 0

    def profile(self, account):
        if account != 'account-1':
            raise ValueError('unknown account')
        return 'profile-1'

    def snapshot(self, local_id):
        return self.rows.get(local_id)

    def submit(self, attempt, media):
        self.submissions += 1
        self.rows.setdefault(attempt['local_id'], {'status': 'pending', 'stage': 'pending'})

    def cancel(self, local_id):
        row = self.rows.get(local_id)
        if not row:
            return 'not_submitted'
        if row['status'] == 'pending':
            row.update(status='cancelled', stage='cancelled')
            return 'cancelled'
        return 'too_late'

    def evidence_manifest(self, attempt):
        return []


class Files:
    def validate_url(self, url):
        return url

    async def download(self, attempt_id, media):
        return 'asset.png'


@pytest.fixture
def agent(tmp_path):
    journal = Journal(tmp_path / 'journal.sqlite3')
    worker = WorkerAgent(config(), journal, Adapter(), Files())
    worker.ready = True
    yield worker
    journal.close()


def test_acceptance_survives_restart_and_duplicate_offer(tmp_path):
    path = tmp_path / 'journal.sqlite3'
    journal = Journal(path)
    message = offer()
    job = Job.model_validate(message.payload).model_dump()
    journal.accept(message, job, 'local-1')
    journal.close()
    journal = Journal(path)
    journal.accept(message, job, 'local-1')
    assert len(journal.attempts()) == 1
    assert journal.get('attempt-1')['state'] == 'accepted'
    assert journal.pending()[0]['type'] == 'job_accept'
    assert path.stat().st_mode & 0o077 == 0
    journal.close()


def test_offer_cannot_replace_attempt_token_or_content(agent):
    agent.offer(offer())
    changed = offer()
    changed.lease_token = 'stale-token'
    agent.offer(changed)
    assert agent.journal.get('attempt-1')['token'] == 'token-1'
    assert agent.journal.pending()[-1]['type'] == 'job_reject'
    changed = offer()
    changed.payload['caption'] = 'Different post'
    agent.offer(changed)
    assert json.loads(agent.journal.get('attempt-1')['job'])['caption'] == 'Hello'


def test_no_dispatch_before_cloud_confirmation(agent):
    agent.offer(offer())
    asyncio.run(agent.tick())
    assert agent.adapter.submissions == 0
    agent.renew([grant()])
    asyncio.run(agent.tick())
    assert agent.adapter.submissions == 1
    assert agent.journal.get('attempt-1')['state'] == 'submitted'
    asyncio.run(agent.tick())
    assert agent.adapter.submissions == 1


def test_expired_or_disconnected_lease_blocks_new_dispatch(agent):
    agent.offer(offer())
    agent.renew([grant(seconds=-1)])
    asyncio.run(agent.tick())
    assert agent.adapter.submissions == 0
    agent.renew([grant()])
    agent.ready = False
    asyncio.run(agent.tick())
    assert agent.adapter.submissions == 0


def test_pause_is_durable_and_replayed_command_does_not_undo_resume(agent):
    pause = Message.model_validate(envelope('command', {'action': 'pause_new_jobs'}))
    resume = Message.model_validate(envelope('command', {'action': 'resume_new_jobs'}))
    agent.command(pause)
    original = agent.journal.pending()[-1]
    assert agent.journal.setting('paused') is True
    agent.command(resume)
    agent.command(pause)
    assert agent.journal.setting('paused') is False
    assert sum(row['message_id'] == original['message_id'] for row in agent.journal.pending()) == 1


def test_ack_is_fenced_and_type_checked(agent):
    agent.offer(offer())
    accepted = agent.journal.pending()[0]
    with pytest.raises(ValueError):
        agent.journal.acknowledge(accepted['message_id'], 'job_accept', 'attempt-1', 'wrong')
    with pytest.raises(ValueError):
        agent.journal.acknowledge(accepted['message_id'], 'execution_result', 'attempt-1', 'token-1')
    assert agent.journal.acknowledge(accepted['message_id'], 'job_accept', 'attempt-1', 'token-1')
    assert not agent.journal.pending()


def test_ambiguous_outcome_is_preserved_and_not_reexecuted(agent):
    agent.offer(offer())
    agent.renew([grant()])
    asyncio.run(agent.tick())
    row = agent.journal.get('attempt-1')
    agent.adapter.rows[row['local_id']].update(status='uncertain', stage='publish_clicked')
    asyncio.run(agent.tick())
    result = agent.journal.pending()[-1]
    assert result['type'] == 'execution_result'
    assert result['payload']['status'] == 'needs_review'
    assert agent.journal.get('attempt-1')['state'] == 'terminal'
    agent.offer(offer())
    asyncio.run(agent.tick())
    assert agent.adapter.submissions == 1


def test_missing_submitted_execution_requires_review(agent):
    agent.offer(offer())
    agent.renew([grant()])
    asyncio.run(agent.tick())
    agent.adapter.rows.clear()
    asyncio.run(agent.tick())
    assert agent.journal.pending()[-1]['payload']['error_code'] == 'local_execution_missing'
    assert agent.adapter.submissions == 1


def test_events_are_durable_ordered_and_snapshot_deduplicated(agent):
    agent.offer(offer())
    agent.journal.observation('attempt-1', {'stage': 'running'})
    agent.journal.observation('attempt-1', {'stage': 'running'})
    agent.journal.observation('attempt-1', {'stage': 'verifying'})
    events = [row for row in agent.journal.pending() if row['type'] == 'execution_event']
    assert [row['payload']['sequence'] for row in events] == [1, 2]


def test_cancellation_only_before_execution(agent):
    agent.offer(offer())
    command = Message.model_validate(envelope('command', {'action': 'cancel_attempt'},
                                    attempt_id='attempt-1', lease_token='token-1'))
    agent.command(command)
    assert agent.journal.get('attempt-1')['state'] == 'terminal'
    assert agent.journal.pending()[-1]['payload']['status'] == 'cancelled'
    asyncio.run(agent.tick())
    assert agent.adapter.submissions == 0


def test_running_cancellation_does_not_interrupt_publish(agent):
    agent.offer(offer())
    agent.renew([grant()])
    asyncio.run(agent.tick())
    row = agent.journal.get('attempt-1')
    agent.adapter.rows[row['local_id']].update(status='running', stage='publish_clicked')
    agent.command(Message.model_validate(envelope('command', {'action': 'cancel_attempt'},
                  attempt_id='attempt-1', lease_token='token-1')))
    assert agent.journal.pending()[-1]['payload']['status'] == 'too_late'
    assert agent.adapter.rows[row['local_id']]['status'] == 'running'


def test_capacity_and_profile_assignment_are_enforced(agent):
    agent.offer(offer())
    agent.offer(offer('attempt-2'))
    assert agent.journal.get('attempt-2') is None
    assert agent.journal.pending()[-1]['type'] == 'job_reject'


@pytest.mark.parametrize('url', ['ws://public.example/ws', 'https://server.example/ws',
                                'wss://user:secret@server.example/ws', 'wss://server.example/ws?token=secret'])
def test_invalid_coordination_urls_rejected(url):
    with pytest.raises(ValueError):
        WorkerConfig(server_url=url, worker_id='one', credential='secret').validate_connection()


@pytest.mark.parametrize('url', ['http://storage.example/x', 'https://untrusted.example/x',
                                'https://user:secret@storage.example/x', 'https://127.0.0.1/x'])
def test_storage_host_and_transport_validation(tmp_path, url):
    async def run():
        transfers = Transfers(config(), tmp_path)
        try:
            with pytest.raises(ValueError):
                transfers.validate_url(url)
        finally:
            await transfers.close()
    asyncio.run(run())


@pytest.mark.parametrize('content', [b'wrong', b'image-extra'])
def test_download_hash_size_and_partial_cleanup(tmp_path, content):
    async def run():
        files = Transfers(config(), tmp_path)
        await files.client.aclose()
        files.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content)))
        try:
            with pytest.raises(ValueError):
                await files.download('attempt-1', Job.model_validate(offer().payload).media[0])
            assert not list(tmp_path.glob('*.part'))
            assert not list(tmp_path.glob('*.png'))
        finally:
            await files.close()
    asyncio.run(run())


def test_valid_download_is_verified_and_reused(tmp_path):
    async def run():
        files = Transfers(config(), tmp_path)
        await files.client.aclose()
        handler = Mock(return_value=httpx.Response(200, content=b'image'))
        files.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            media = Job.model_validate(offer().payload).media[0]
            first = await files.download('attempt-1', media)
            second = await files.download('attempt-1', media)
            assert first == second
            assert (tmp_path / first).read_bytes() == b'image'
            assert handler.call_count == 1
        finally:
            await files.close()
    asyncio.run(run())


def test_real_websocket_handshake_accept_progress_result_and_acks(tmp_path):
    async def run():
        done = asyncio.Event()
        received = []
        async def server(socket):
            hello = json.loads(await socket.recv())
            assert socket.request.headers['Authorization'] == 'Bearer secret'
            await socket.send(json.dumps(envelope('hello_ack', {}, reply_to=hello['message_id'])))
            reconcile = json.loads(await socket.recv())
            await socket.send(json.dumps(envelope('reconcile_ack', {'leases': []}, reply_to=reconcile['message_id'])))
            await socket.send(offer().model_dump_json())
            async for raw in socket:
                message = json.loads(raw)
                received.append(message['type'])
                if message['type'] == 'heartbeat':
                    await socket.send(json.dumps(envelope('heartbeat_ack', {'leases': []}, reply_to=message['message_id'])))
                elif message['type'] == 'job_accept':
                    await socket.send(json.dumps(envelope('job_start', {'expires_at': grant()['expires_at']},
                        attempt_id='attempt-1', lease_token='token-1', reply_to=message['message_id'])))
                elif message['type'] == 'execution_event':
                    row = worker.journal.get('attempt-1')
                    worker.adapter.rows[row['local_id']].update(status='published', stage='published')
                    await socket.send(json.dumps(envelope('event_ack', {}, reply_to=message['message_id'],
                        attempt_id='attempt-1', lease_token='token-1')))
                elif message['type'] == 'execution_result':
                    await socket.send(json.dumps(envelope('result_ack', {}, reply_to=message['message_id'],
                        attempt_id='attempt-1', lease_token='token-1')))
                    done.set()
        async with serve(server, '127.0.0.1', 0) as gateway:
            port = gateway.sockets[0].getsockname()[1]
            cfg = WorkerConfig(server_url=f'ws://127.0.0.1:{port}/ws', worker_id='worker-1',
                               credential='secret', account_profiles={'account-1': 'profile-1'},
                               allow_loopback_development=True, heartbeat_seconds=1).validate_connection()
            journal = Journal(tmp_path / 'journal.sqlite3')
            worker = WorkerAgent(cfg, journal, Adapter(), Files())
            task = asyncio.create_task(worker.run())
            try:
                await asyncio.wait_for(done.wait(), 8)
                # Give the worker reader a chance to persist the last acknowledgement.
                for _ in range(20):
                    if not journal.pending():
                        break
                    await asyncio.sleep(.05)
                assert not journal.pending()
                assert worker.adapter.submissions == 1
                assert {'job_accept', 'heartbeat', 'execution_event', 'execution_result'} <= set(received)
            finally:
                await worker.stop()
                await asyncio.wait_for(task, 3)
                journal.close()
    asyncio.run(run())


def test_disconnect_replays_unacknowledged_events_without_republishing(tmp_path):
    async def run():
        done = asyncio.Event()
        connections = 0
        event_ids = []
        async def server(socket):
            nonlocal connections
            connections += 1
            connection = connections
            hello = json.loads(await socket.recv())
            await socket.send(json.dumps(envelope('hello_ack', {}, reply_to=hello['message_id'])))
            reconciliation = json.loads(await socket.recv())
            leases = [] if connection == 1 else [grant()]
            if connection > 1:
                assert reconciliation['payload']['attempts'][0]['state'] == 'submitted'
            await socket.send(json.dumps(envelope('reconcile_ack', {'leases': leases}, reply_to=reconciliation['message_id'])))
            if connection == 1:
                await socket.send(offer().model_dump_json())
            async for raw in socket:
                message = json.loads(raw)
                kind = message['type']
                if kind == 'heartbeat':
                    await socket.send(json.dumps(envelope('heartbeat_ack', {'leases': []}, reply_to=message['message_id'])))
                elif kind == 'job_accept':
                    await socket.send(json.dumps(envelope('job_start', {'expires_at': grant()['expires_at']},
                        attempt_id='attempt-1', lease_token='token-1', reply_to=message['message_id'])))
                elif kind == 'execution_event':
                    event_ids.append(message['message_id'])
                    if connection == 1:
                        row = worker.journal.get('attempt-1')
                        worker.adapter.rows[row['local_id']].update(status='published', stage='published')
                        await socket.close()
                        return
                    await socket.send(json.dumps(envelope('event_ack', {}, reply_to=message['message_id'],
                        attempt_id='attempt-1', lease_token='token-1')))
                elif kind == 'execution_result':
                    await socket.send(json.dumps(envelope('result_ack', {}, reply_to=message['message_id'],
                        attempt_id='attempt-1', lease_token='token-1')))
                    done.set()
        async with serve(server, '127.0.0.1', 0) as gateway:
            cfg = WorkerConfig(server_url=f'ws://127.0.0.1:{gateway.sockets[0].getsockname()[1]}/ws',
                               worker_id='worker-1', credential='secret', account_profiles={'account-1': 'profile-1'},
                               allow_loopback_development=True, heartbeat_seconds=1).validate_connection()
            journal = Journal(tmp_path / 'journal.sqlite3')
            worker = WorkerAgent(cfg, journal, Adapter(), Files())
            task = asyncio.create_task(worker.run())
            try:
                await asyncio.wait_for(done.wait(), 15)
                assert connections >= 2
                assert event_ids[0] == event_ids[1]
                assert worker.adapter.submissions == 1
            finally:
                await worker.stop()
                await asyncio.wait_for(task, 3)
                journal.close()
    asyncio.run(run())


def test_heartbeat_timeout_requires_application_ack(agent):
    agent.last_heartbeat_ack = time.monotonic() - 100
    async def run():
        with pytest.raises(TimeoutError):
            await agent.heartbeat(None)
    asyncio.run(run())


def test_journal_cannot_be_reassigned_to_another_worker(agent):
    changed = agent.config.model_copy(update={'worker_id': 'different-worker'})
    with pytest.raises(ValueError, match='different worker'):
        WorkerAgent(changed, agent.journal, Adapter(), Files())


def test_unresolved_account_cannot_be_remapped(agent):
    agent.offer(offer())
    changed = agent.config.model_copy(update={'account_profiles': {'account-1': 'other-profile'}})
    with pytest.raises(ValueError, match='remap'):
        WorkerAgent(changed, agent.journal, Adapter(), Files())


def test_journal_cannot_be_migrated_to_unknown_server(agent):
    changed = agent.config.model_copy(update={'server_url': 'wss://other.example/ws'})
    with pytest.raises(ValueError, match='different server'):
        WorkerAgent(changed, agent.journal, Adapter(), Files())


def test_evidence_upload_uses_https_put_without_worker_credential(tmp_path):
    async def run():
        files = Transfers(config(), tmp_path)
        await files.client.aclose()
        observed = []
        def receive(request):
            observed.append(request)
            return httpx.Response(200)
        files.client = httpx.AsyncClient(transport=httpx.MockTransport(receive))
        screenshot = tmp_path / 'screen.png'
        screenshot.write_bytes(b'png-bytes')
        try:
            await files.upload('https://storage.example/signed-upload', screenshot)
            assert observed[0].method == 'PUT'
            assert observed[0].content == b'png-bytes'
            assert 'Authorization' not in observed[0].headers
            assert observed[0].headers['Content-Type'] == 'image/png'
        finally:
            await files.close()
    asyncio.run(run())
