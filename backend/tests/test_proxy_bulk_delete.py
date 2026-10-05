import json

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.routers import proxies
from backend.services import proxy_service


@pytest.fixture
def pool(tmp_path, monkeypatch):
    path = tmp_path / 'pool.json'
    monkeypatch.setattr(proxy_service, 'PROXY_POOL_FILE', path)
    proxy_service.save_proxy_pool([
        {'id': 'a', 'assigned': False}, {'id': 'b', 'assigned': False},
        {'id': 'c', 'assigned': True, 'profile_id': 'profile_001'},
        {'id': 'd', 'assigned': False, 'profile_id': 'profile_002'},
    ])
    return path


def test_bulk_delete_removes_selected_and_protects_assignments(pool):
    result = proxies.remove_selected_proxies(proxies.ProxySelection(proxy_ids=['a', 'b', 'a', 'c', 'd', 'missing']))
    assert result == {'deleted_ids': ['a', 'b'], 'blocked_ids': ['c', 'd'], 'missing_ids': ['missing']}
    assert [item['id'] for item in json.loads(pool.read_text())] == ['c', 'd']


def test_single_delete_also_protects_assignments(pool):
    with pytest.raises(HTTPException) as error:
        proxies.remove_proxy('c')
    assert error.value.status_code == 409
    assert len(json.loads(pool.read_text())) == 4
    assert proxies.remove_proxy('a') == {'success': True}


def test_repeat_delete_is_safe_and_reports_missing(pool):
    assert proxy_service.delete_proxies(['a'])['deleted_ids'] == ['a']
    assert proxy_service.delete_proxies(['a']) == {'deleted_ids': [], 'blocked_ids': [], 'missing_ids': ['a']}
    assert len(json.loads(pool.read_text())) == 3


def test_write_failure_does_not_report_success(pool, monkeypatch):
    def failed_save(*_args, **_kwargs):
        raise OSError('Disk full')
    monkeypatch.setattr(proxy_service, 'save_proxy_pool', failed_save)
    with pytest.raises(OSError, match='Disk full'):
        proxies.remove_selected_proxies(proxies.ProxySelection(proxy_ids=['a', 'b']))
    assert len(json.loads(pool.read_text())) == 4


def test_empty_selection_is_invalid():
    with pytest.raises(ValidationError):
        proxies.ProxySelection(proxy_ids=[])
