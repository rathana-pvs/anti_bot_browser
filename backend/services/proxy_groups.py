"""Proxy folders are fixed and derived from assignment and measured speed."""
from backend.services.proxy_quality import FOLDERS

AUTOMATIC_FOLDER_MESSAGE = 'Proxy folders are automatic: assignment controls Used; speed checks control Available and Poor proxy.'


def list_groups():
    return list(FOLDERS)


def create_group(name):
    raise ValueError(AUTOMATIC_FOLDER_MESSAGE)


def rename_group(name, replacement):
    raise ValueError(AUTOMATIC_FOLDER_MESSAGE)


def delete_group(name):
    raise ValueError(AUTOMATIC_FOLDER_MESSAGE)


def move_proxies(ids, name):
    raise ValueError(AUTOMATIC_FOLDER_MESSAGE)
