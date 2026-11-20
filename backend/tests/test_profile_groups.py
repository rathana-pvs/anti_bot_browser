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


def _account(root, profile_id, group='', phone=None):
    directory = root / 'profiles' / profile_id
    directory.mkdir(parents=True)
    config = {'id': profile_id, 'name': profile_id, 'schema_version': PROFILE_SCHEMA_VERSION,
              'configuration_revision': 1, 'restart_required': False, 'group': group}
    if phone:
        config['runtime'] = {'kind': profiles.ANDROID_RUNTIME, 'device_serial': phone, 'android_user_id': 11}
    (directory / 'config.json').write_text(json.dumps(config))
    return directory / 'config.json'


@pytest.fixture
def phones(monkeypatch):
    calls = {'checked': [], 'synced': []}
    monkeypatch.setattr(profiles.android_ownership, 'ensure_primary_offline_safe', calls['checked'].append)
    monkeypatch.setattr(profiles.android_ownership, 'sync_details_in_background', calls['synced'].append)
    return calls


def test_a_bulk_move_saves_every_account_and_leaves_phones_to_the_background(group_store, phones):
    pc = _account(group_store, 'profile_001')
    there = _account(group_store, 'profile_002', group='Travel')
    first = _account(group_store, 'profile_003', phone='PIXEL')
    second = _account(group_store, 'profile_004', phone='PIXEL')
    result = profiles.move_to_folder(profiles.MoveToFolderRequest(
        profile_ids=['profile_001', 'profile_002', 'profile_003', 'profile_004', 'profile_001'], group=' Travel '))
    assert result == {'moved': ['profile_001', 'profile_003', 'profile_004'], 'group': 'Travel'}
    for path in (pc, first, second):
        saved = json.loads(path.read_text())
        assert (saved['group'], saved['configuration_revision'], saved['restart_required']) == ('Travel', 2, False)
    assert json.loads(there.read_text())['configuration_revision'] == 1  # already there: not rewritten
    assert phones == {'checked': ['PIXEL'], 'synced': [['profile_003', 'profile_004']]}  # one check per phone


def test_nothing_moves_when_a_phone_is_managed_by_another_pc(group_store, monkeypatch):
    pc = _account(group_store, 'profile_001')
    _account(group_store, 'profile_002', phone='PIXEL')

    def secondary(serial):
        raise ValueError('This phone is managed by Office PC. Make this PC primary to change it.')
    monkeypatch.setattr(profiles.android_ownership, 'ensure_primary_offline_safe', secondary)
    with pytest.raises(HTTPException) as error:
        profiles.move_to_folder(profiles.MoveToFolderRequest(profile_ids=['profile_001', 'profile_002'], group='Travel'))
    assert error.value.status_code == 400 and 'Office PC' in error.value.detail
    assert json.loads(pc.read_text())['group'] == ''


def test_nothing_moves_when_an_account_is_missing(group_store, phones):
    pc = _account(group_store, 'profile_001')
    with pytest.raises(HTTPException) as error:
        profiles.move_to_folder(profiles.MoveToFolderRequest(profile_ids=['profile_001', 'profile_009'], group='Travel'))
    assert error.value.status_code == 404
    assert json.loads(pc.read_text())['group'] == ''


def test_details_a_phone_could_not_take_wait_for_the_account_to_open(group_store, monkeypatch):
    ownership = profiles.android_ownership
    monkeypatch.setattr(ownership, 'PROFILES_DIR', group_store / 'profiles')
    config = _account(group_store, 'profile_001', phone='PIXEL')

    def stopped_user(profile_id):
        raise ownership.AdbError('The companion app did not answer.')
    monkeypatch.setattr(ownership, 'sync_details', stopped_user)
    assert ownership.try_sync_details('profile_001') is False
    assert json.loads(config.read_text())[ownership.DETAILS_PENDING] is True
    monkeypatch.setattr(ownership, 'sync_details', lambda profile_id: None)
    assert ownership.try_sync_details('profile_001') is True
    assert ownership.DETAILS_PENDING not in json.loads(config.read_text())


def test_a_move_keeps_what_a_job_saved_while_the_phone_was_checked(group_store, monkeypatch):
    config = _account(group_store, 'profile_001', phone='PIXEL')

    def job_saves_meanwhile(serial):  # a job opening the account saves its proxy check during the move
        data = json.loads(config.read_text())
        config.write_text(json.dumps({**data, 'android_network': {'status': 'verified'}}))
    monkeypatch.setattr(profiles.android_ownership, 'ensure_primary_offline_safe', job_saves_meanwhile)
    monkeypatch.setattr(profiles.android_ownership, 'sync_details_in_background', lambda ids: None)
    profiles.move_to_folder(profiles.MoveToFolderRequest(profile_ids=['profile_001'], group='Travel'))
    saved = json.loads(config.read_text())
    assert saved['group'] == 'Travel' and saved['android_network'] == {'status': 'verified'}


def test_marking_details_never_raises_and_the_background_carries_on(group_store, monkeypatch):
    ownership = profiles.android_ownership
    monkeypatch.setattr(ownership, 'PROFILES_DIR', group_store / 'profiles')
    first = _account(group_store, 'profile_001', phone='PIXEL')
    second = _account(group_store, 'profile_002', phone='PIXEL')
    tried = []

    def sync(profile_id):
        tried.append(profile_id)
        if profile_id == 'profile_001':
            raise RuntimeError('unexpected')
    monkeypatch.setattr(ownership, 'sync_details', sync)
    ownership.sync_details_in_background(['profile_001', 'profile_002']).join(5)
    assert tried == ['profile_001', 'profile_002']
    assert json.loads(first.read_text())[ownership.DETAILS_PENDING] is True
    assert ownership.DETAILS_PENDING not in json.loads(second.read_text())

    def disk_full(*args, **kwargs):
        raise OSError('No space left on device')
    monkeypatch.setattr(ownership, 'atomic_write_json', disk_full)
    ownership._mark_details_pending('profile_002', True)  # does not raise
