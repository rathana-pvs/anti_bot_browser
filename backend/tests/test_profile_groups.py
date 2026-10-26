import json

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.models.profile import ProfileCreateRequest, ProfileUpdateRequest
from backend.routers import profiles
from backend.services import profile_groups
from backend.services.profile_service import PROFILE_SCHEMA_VERSION


@pytest.fixture
def group_store(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_groups, 'GROUPS_FILE', tmp_path / 'groups.json')
    monkeypatch.setattr(profile_groups, 'PROFILES_DIR', tmp_path / 'profiles')
    monkeypatch.setattr(profiles, 'PROFILES_DIR', tmp_path / 'profiles')
    return tmp_path


def test_empty_group_persists_and_duplicate_is_rejected(group_store):
    request = profiles.GroupCreateRequest(name='  Travel  ')
    assert profiles.add_profile_group(request) == {'name': 'Travel'}
    assert profiles.get_profile_groups() == ['Travel']
    assert json.loads(profile_groups.GROUPS_FILE.read_text()) == {'groups': ['Travel']}
    with pytest.raises(HTTPException) as error:
        profiles.add_profile_group(profiles.GroupCreateRequest(name='travel'))
    assert error.value.status_code == 409
    assert profiles.get_profile_groups() == ['Travel']


@pytest.mark.parametrize('name', ['', '   ', 'x' * 81])
def test_group_names_are_validated(name):
    with pytest.raises(ValidationError):
        profiles.GroupCreateRequest(name=name)


def test_group_membership_can_be_set_and_cleared_without_restart(group_store):
    directory = group_store / 'profiles' / 'profile_001'
    directory.mkdir(parents=True)
    config = directory / 'config.json'
    config.write_text(json.dumps({
        'id': 'profile_001', 'name': 'Account', 'schema_version': PROFILE_SCHEMA_VERSION,
        'configuration_revision': 1, 'restart_required': False,
    }))
    profiles.update_profile('profile_001', ProfileUpdateRequest(group='  Travel  '))
    assert json.loads(config.read_text())['group'] == 'Travel'
    assert json.loads(config.read_text())['restart_required'] is False
    assert profiles.get_profile_groups() == ['Travel']
    # Retain a registered group even after its last profile leaves it.
    profile_groups.GROUPS_FILE.write_text(json.dumps({'groups': ['Travel']}))
    profiles.update_profile('profile_001', ProfileUpdateRequest(name='Renamed'))
    assert json.loads(config.read_text())['group'] == 'Travel'
    profiles.update_profile('profile_001', ProfileUpdateRequest(group=''))
    assert json.loads(config.read_text())['group'] == ''
    assert profiles.get_profile_groups() == ['Travel']


def test_profile_creation_group_is_optional():
    assert ProfileCreateRequest(name='Account').group == ''
    assert ProfileCreateRequest(name='Account', group='  Travel ').group == 'Travel'


def _member(root, profile_id, group):
    directory = root / 'profiles' / profile_id
    directory.mkdir(parents=True)
    config = directory / 'config.json'
    config.write_text(json.dumps({'id': profile_id, 'name': profile_id, 'group': group}))
    return config


def test_renaming_a_folder_moves_its_accounts(group_store):
    profile_groups.GROUPS_FILE.write_text(json.dumps({'groups': ['Empty', 'Food']}))
    food = _member(group_store, 'p1', 'Food')
    other = _member(group_store, 'p2', 'Shops')
    result = profiles.rename_profile_group('food', profiles.GroupCreateRequest(name='Food pages'))
    assert result == {'name': 'Food pages', 'moved': 1}
    assert json.loads(food.read_text())['group'] == 'Food pages'
    assert json.loads(other.read_text())['group'] == 'Shops'
    assert profiles.get_profile_groups() == ['Empty', 'Food pages', 'Shops']


def test_rename_rejects_unknown_and_duplicate_names(group_store):
    profile_groups.GROUPS_FILE.write_text(json.dumps({'groups': ['Food', 'Shops']}))
    with pytest.raises(HTTPException) as missing:
        profiles.rename_profile_group('Nope', profiles.GroupCreateRequest(name='X'))
    assert missing.value.status_code == 404
    with pytest.raises(HTTPException) as duplicate:
        profiles.rename_profile_group('Food', profiles.GroupCreateRequest(name='shops'))
    assert duplicate.value.status_code == 409
    # Changing only the letter case of the same folder is allowed.
    assert profiles.rename_profile_group('Food', profiles.GroupCreateRequest(name='FOOD'))['name'] == 'FOOD'


def test_deleting_a_folder_keeps_its_accounts_unfiled(group_store):
    profile_groups.GROUPS_FILE.write_text(json.dumps({'groups': ['Food', 'Shops']}))
    food = _member(group_store, 'p1', 'Food')
    assert profiles.delete_profile_group('Food') == {'name': 'Food', 'unfiled': 1}
    assert food.exists()
    assert json.loads(food.read_text())['group'] == ''
    assert profiles.get_profile_groups() == ['Shops']
    with pytest.raises(HTTPException) as missing:
        profiles.delete_profile_group('Food')
    assert missing.value.status_code == 404
