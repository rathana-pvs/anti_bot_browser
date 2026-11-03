"""Explicit batch removal is independent of publication outcome."""
from copy import deepcopy
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from backend.routers import queue as routes


@pytest.mark.parametrize('status,stage', [
    ('uncertain', 'verifying'), ('needs_review', 'publish_clicked'),
    ('failed', 'publish_clicked'), ('running', 'commenting'),
    ('preparing', 'preparing'), ('pending', 'pending'), ('published', 'published'),
])
def test_delete_batch_allows_any_status_without_modifying_other_batches(monkeypatch, status, stage):
    other = {'batch_id': 'keep', 'posts': [{'executions': [{'status': 'needs_review'}]}]}
    queue = {'daily_batches': [
        {'batch_id': 'remove', 'posts': [{'executions': [{'status': status, 'stage': stage}]}]},
        deepcopy(other),
    ]}
    save = Mock(return_value=True)
    lock = Mock(side_effect=lambda action: action())
    monkeypatch.setattr(routes, 'load_posting_queue', lambda: queue)
    monkeypatch.setattr(routes, 'save_posting_queue', save)
    monkeypatch.setattr(routes, 'with_queue_claim_lock', lock)
    assert routes.delete_batch('remove')['success'] is True
    assert queue['daily_batches'] == [other]
    save.assert_called_once_with(queue)
    lock.assert_called_once()


@pytest.mark.parametrize('exists,saved,code', [(False, True, 404), (True, False, 500)])
def test_delete_batch_reports_missing_batch_or_write_failure(monkeypatch, exists, saved, code):
    queue = {'daily_batches': [{'batch_id': 'remove'}] if exists else []}
    monkeypatch.setattr(routes, 'load_posting_queue', lambda: queue)
    monkeypatch.setattr(routes, 'save_posting_queue', Mock(return_value=saved))
    monkeypatch.setattr(routes, 'with_queue_claim_lock', lambda action: action())
    with pytest.raises(HTTPException) as error:
        routes.delete_batch('remove')
    assert error.value.status_code == code
