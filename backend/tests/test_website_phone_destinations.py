"""Exercise both phone entry paths with a destination-specific link."""
import asyncio
from datetime import datetime, timezone
from backend.tests.test_android_jobs import world, runner, claim, execution, SERIAL
from backend.services import android_jobs, android_devices


def test_phone_post_uses_reviewed_destination_caption_and_comment(world, monkeypatch):
    monkeypatch.setattr(android_jobs, '_last_post_at', {})
    execution(world).update(spun_caption='Reviewed destination hook', first_comment='https://news.example/article/unique/park')
    runner(monkeypatch, world, [{'event': 'about_to_publish'}, {'event': 'publish_tapped'}], {'status': 'published', 'comment': 'deferred'})
    asyncio.run(android_jobs.execute_queue_phone(claim(world)))
    args = world['runs'][0]
    assert args[args.index('--caption') + 1] == 'Reviewed destination hook'
    assert args[args.index('--comment') + 1] == 'https://news.example/article/unique/park'


def test_phone_comment_followup_reuses_the_same_destination_link(world, monkeypatch):
    monkeypatch.setattr(android_jobs, '_last_post_at', {})
    ex = execution(world)
    ex.update(status='published', first_comment_status='phone_pending', first_comment='https://news.example/article/unique/park', publish_tapped_at=datetime.now(timezone.utc).isoformat())
    runner(monkeypatch, world, [{'event': 'about_to_comment'}], {'status': 'published', 'comment': 'posted'})
    android_devices.hold_for_job(SERIAL, 'comment:e1')
    asyncio.run(android_jobs.run_comment('e1', android_jobs.phone_of('phone_1'), world['queue']['daily_batches'][0]['posts'][0]))
    args = world['runs'][0]
    assert args[args.index('--comment') + 1] == 'https://news.example/article/unique/park'
