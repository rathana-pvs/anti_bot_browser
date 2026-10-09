"""Map cloud attempts into the existing queue without another scheduler."""
import hashlib
import json
from pathlib import Path

from backend.config import PROFILES_DIR
from backend.services.queue_service import (
    find_queue_execution, load_posting_queue, save_posting_queue, with_queue_claim_lock,
)
from .protocol import utcnow

TERMINAL = {'published', 'completed', 'failed', 'failed_before_publish',
            'failed_after_publish', 'uncertain', 'needs_review', 'cancelled'}


def local_execution_id(worker_id, attempt_id):
    return 'cloud_' + hashlib.sha256((worker_id + ':' + attempt_id).encode()).hexdigest()


class QueueAdapter:
    def __init__(self, config):
        self.config = config

    def profile_catalog(self):
        from backend.services.profile_groups import list_groups
        groups = set(list_groups())
        accounts = []
        for account_id, profile_id in self.config.account_profiles.items():
            path = PROFILES_DIR / profile_id / 'config.json'
            try:
                profile = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            group = str(profile.get('group') or '').strip()
            if group:
                groups.add(group)
            accounts.append({'account_id': account_id,
                             'name': str(profile.get('name') or profile_id), 'group': group})
        # Only display metadata leaves this app; profile settings and sessions stay local.
        return {'groups': sorted(groups, key=str.casefold), 'accounts': accounts}

    def profile(self, account_id):
        profile = self.config.account_profiles.get(account_id)
        if not profile:
            raise ValueError('Account is not assigned to this worker')
        path = PROFILES_DIR / profile / 'config.json'
        if not path.is_file():
            raise ValueError('Assigned local profile does not exist')
        config = json.loads(path.read_text(encoding='utf-8'))
        if config.get('automation', {}).get('enabled') is False:
            raise ValueError('Local automation is disabled for this account')
        return profile

    def submit(self, attempt, media_filename):
        job = json.loads(attempt['job'])
        profile = self.profile(job['account_id'])
        now = utcnow()
        def update():
            queue = load_posting_queue()
            found = find_queue_execution(queue, attempt['local_id'])
            if found:
                if found['execution'].get('cloud_attempt_id') != attempt['id']:
                    raise ValueError('Cloud execution identity collision')
                return
            # An unresolved publication on an account blocks new cloud attempts.
            for batch in queue.get('daily_batches', []):
                for post in batch.get('posts', []):
                    for execution in post.get('executions', []):
                        if execution.get('profile_id') == profile and execution.get('status') in ('uncertain', 'needs_review'):
                            raise ValueError('Account has an unresolved publication')
            execution = {'execution_id': attempt['local_id'], 'profile_id': profile,
                         'scheduled_at': now, 'spun_caption': job['caption'],
                         'status': 'pending', 'stage': 'pending', 'stage_history': [],
                         'preparation_mode': 'off', 'preparation_status': 'not_requested',
                         'retry_count': 0, 'cloud_attempt_id': attempt['id'],
                         'cloud_account_id': job['account_id'], 'cloud_worker_id': self.config.worker_id}
            queue.setdefault('daily_batches', []).append({
                'batch_id': attempt['local_id'], 'name': 'Remote post', 'created_at': now,
                'target_profiles': [profile], 'profile_execution_order': [profile],
                'schedule_window': {'start_now': True, 'profile_stagger_seconds': 0},
                'posts': [{'post_id': attempt['local_id'], 'type': job['task'],
                           'base_caption': job['caption'], 'first_comment': job['first_comment'],
                           'media_file': media_filename, 'ai_spin': False,
                           'executions': [execution]}]})
            if not save_posting_queue(queue):
                raise RuntimeError('Cannot persist cloud execution')
        with_queue_claim_lock(update)

    def snapshot(self, local_id):
        found = find_queue_execution(load_posting_queue(), local_id)
        if not found:
            return None
        execution = dict(found['execution'])
        if execution.get('cloud_attempt_id') and execution.get('status') in ('failed', 'failed_before_publish'):
            from backend.config import DATA_DIR
            marker = DATA_DIR / 'worker' / 'publish-intents' / (local_id + '.json')
            if marker.exists():
                execution.update(status='needs_review', stage='publish_clicked')
        return execution

    def capacity(self):
        from backend.services.automation_service import current_scheduler_snapshot
        snapshot = current_scheduler_snapshot()
        return {'active': snapshot['active'], 'available': snapshot['available'],
                'limits': {key: snapshot['config'][key] for key in
                           ('max_publishers', 'max_preparers', 'max_total_automation_tasks')}}

    def cancel(self, local_id):
        def update():
            queue = load_posting_queue()
            match = find_queue_execution(queue, local_id)
            if not match:
                return 'not_submitted'
            execution = match['execution']
            if execution['status'] == 'cancelled':
                return 'cancelled'
            if execution['status'] != 'pending' or execution.get('scheduler_lease'):
                return 'too_late'
            execution.update(status='cancelled', stage='cancelled', ended_at=utcnow())
            if not save_posting_queue(queue):
                raise RuntimeError('Cannot persist cancellation')
            return 'cancelled'
        return with_queue_claim_lock(update)

    def evidence_file(self, attempt, filename):
        # Only screenshots inside this execution's evidence directory may be uploaded.
        snapshot = self.snapshot(attempt['local_id']) or {}
        profile = self.config.account_profiles.get(json.loads(attempt['job'])['account_id'])
        if not profile or not snapshot.get('evidence_dir'):
            raise ValueError('No evidence available')
        root = (PROFILES_DIR / profile / 'automation_evidence').resolve()
        directory = Path(snapshot['evidence_dir']).resolve()
        if not directory.is_relative_to(root) or directory == root:
            raise ValueError('Evidence directory is outside this profile')
        if Path(filename).name != filename or not filename.endswith('.png'):
            raise ValueError('Only named PNG screenshots are transferable')
        file = directory / filename
        if file.is_symlink() or not file.resolve().is_relative_to(directory) or not file.is_file():
            raise ValueError('Invalid evidence file')
        if file.stat().st_size > 20 * 1024 * 1024:
            raise ValueError('Evidence file exceeds size limit')
        return file

    def evidence_manifest(self, attempt):
        snapshot = self.snapshot(attempt['local_id']) or {}
        if not snapshot.get('evidence_dir'):
            return []
        try:
            directory = Path(snapshot['evidence_dir'])
            manifest = []
            for candidate in sorted(directory.glob('*.png'))[:100]:
                try:
                    file = self.evidence_file(attempt, candidate.name)
                except (ValueError, OSError):
                    continue
                manifest.append({'filename': file.name, 'size_bytes': file.stat().st_size,
                                 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()})
            return manifest
        except (ValueError, OSError):
            return []
