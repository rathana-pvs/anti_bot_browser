import json
from unittest.mock import patch

import pytest
from backend.services.resource_service import ResourceSettingsManager


def test_default_selection_and_persisted_choice_keep_active_engine_pinned(tmp_path):
    path=tmp_path/'settings.json'
    path.write_text(json.dumps({'resource_mode':'low','other_setting':'keep'}))
    manager=ResourceSettingsManager(path)
    with patch('backend.services.resource_service.OCR_ENGINE','paddleocr-v5'):
        assert manager.ocr_selection()['selected']=='paddleocr-v5'
        result=manager.update_ocr_engine('paddleocr-v6')
        assert result['active']=='paddleocr-v5'
        assert result['selected']=='paddleocr-v6'
        assert result['restart_required'] is True
        assert ResourceSettingsManager(path).ocr_engine=='paddleocr-v6'
        assert manager.resource_mode=='low'
        assert json.loads(path.read_text())['other_setting']=='keep'
        manager.update_resource_mode('medium')
        assert json.loads(path.read_text())['ocr_engine']=='paddleocr-v6'


def test_invalid_engine_cannot_modify_saved_selection(tmp_path):
    path=tmp_path/'settings.json';manager=ResourceSettingsManager(path)
    manager.update_ocr_engine('easyocr');before=path.read_bytes()
    with pytest.raises(ValueError):manager.update_ocr_engine('unknown')
    assert path.read_bytes()==before


def test_all_three_choices_are_exposed(tmp_path):
    manager=ResourceSettingsManager(tmp_path/'settings.json')
    assert [x['value'] for x in manager.ocr_selection()['options']]==['paddleocr-v5','paddleocr-v6','easyocr']


def test_launch_override_locks_selector_without_changing_saved_choice(tmp_path):
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'ocr_engine': 'paddleocr-v6'}))
    manager = ResourceSettingsManager(path)
    with patch('backend.services.resource_service.OCR_ENGINE_OVERRIDE', True), \
         patch('backend.services.resource_service.OCR_ENGINE', 'easyocr'):
        selection = manager.ocr_selection()
        assert selection['selected'] == 'easyocr'
        assert selection['locked'] is True
        assert selection['restart_required'] is False
        with pytest.raises(ValueError, match='launch configuration'):
            manager.update_ocr_engine('paddleocr-v5')
    assert json.loads(path.read_text())['ocr_engine'] == 'paddleocr-v6'
