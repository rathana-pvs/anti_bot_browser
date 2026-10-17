"""Secondary OCR may recover bounded misses but never replace primary success."""
import subprocess
from unittest.mock import Mock, patch

import numpy as np
import pytest

from engine.tesseract_ocr import read_crop
from engine.vision import VisionEngine
from modules.reel_top_post import retry_unreadable_header


def record(text='1m', confidence=.9, bounds=(5, 5, 25, 15)):
    left, top, right, bottom = bounds
    return dict(text=text, confidence=confidence, bounds=bounds,
                center=((left+right)/2, (top+bottom)/2), engine='tesseract')


def vision():
    value = VisionEngine.__new__(VisionEngine)
    value.telemetry = None
    value._secondary_ocr_cache = {}
    value.read_text = Mock(return_value=[])
    return value


SCREEN = np.zeros((1080, 1920, 3), dtype=np.uint8)


def test_primary_success_is_returned_unchanged():
    v = vision()
    primary = [record('Post')]
    with patch('engine.tesseract_ocr.read_crop') as secondary:
        result = v.read_text_with_fallback(SCREEN, region=(100,100,200,50), primary=primary,
                                          accept=lambda i: i['text'] == 'Post')
    assert result is primary
    secondary.assert_not_called()


def test_two_scale_consensus_coordinates_cache_and_provenance():
    v = vision()
    with patch('engine.tesseract_ocr.read_crop', return_value=[record()]) as secondary:
        result = v.read_text_with_fallback(SCREEN, region=(100,200,80,30), primary=[], accept=lambda i: True)
        again = v.read_text_with_fallback(SCREEN, region=(300,400,80,30), primary=[], accept=lambda i: True)
    assert secondary.call_count == 2
    assert [c.kwargs['scale'] for c in secondary.call_args_list] == [3,4]
    assert result[0]['bounds'] == (105,205,125,215)
    assert again[0]['bounds'] == (305,405,325,415)
    assert result[0]['engine'] == 'tesseract'


@pytest.mark.parametrize('second', [record('Im'), record(confidence=.59), record(bounds=(40,5,60,15))])
def test_disagreement_low_confidence_or_different_location_is_rejected(second):
    v = vision()
    with patch('engine.tesseract_ocr.read_crop', side_effect=[[record()], [second]]):
        assert v.read_text_with_fallback(SCREEN, region=(100,100,80,30), primary=[], accept=lambda i: True) == []


def test_unbounded_or_oversized_crop_skips_secondary():
    v = vision()
    with patch('engine.tesseract_ocr.read_crop') as secondary:
        for region in (None, (0,0,1920,1080)):
            assert v.read_text_with_fallback(SCREEN, region=region, primary=[], accept=lambda i: True) == []
    secondary.assert_not_called()


def test_secondary_telemetry_and_bounded_cache():
    v = vision()
    v.telemetry = Mock()
    with patch('engine.tesseract_ocr.read_crop', return_value=[record()]):
        for psm in range(40):
            v.read_text_with_fallback(SCREEN, region=(100,100,80,30), primary=[],
                                     accept=lambda i: True, psm=psm)
    assert len(v._secondary_ocr_cache) == 32
    assert v.telemetry.record_ocr.call_args.kwargs['outcome'] == 'tesseract_consensus'


def test_label_fallback_requires_whole_expected_label():
    v = vision()
    with patch('engine.tesseract_ocr.read_crop', return_value=[record('Post review')]):
        assert v.find_text('Post', screen=SCREEN, region=(100,100,200,40)) is None
    v._secondary_ocr_cache.clear()
    with patch('engine.tesseract_ocr.read_crop', return_value=[record('Comment as')]):
        assert v.find_text('Comment as', screen=SCREEN, region=(100,100,200,40))['engine'] == 'tesseract'


def test_existing_label_success_does_not_invoke_secondary():
    v = vision()
    v.read_text.return_value = [record('Next')]
    with patch('engine.tesseract_ocr.read_crop') as secondary:
        assert v.find_text('Next', screen=SCREEN, region=(100,100,200,40))['text'] == 'Next'
    secondary.assert_not_called()


@pytest.mark.parametrize('failure', [FileNotFoundError(), subprocess.TimeoutExpired('tesseract', 3),
                                   subprocess.CalledProcessError(1,'tesseract')])
def test_adapter_failure_returns_no_text(failure):
    with patch('engine.tesseract_ocr.shutil.which', return_value='/fake/tesseract'), \
         patch('engine.tesseract_ocr.subprocess.run', side_effect=failure):
        assert read_crop(SCREEN[:20,:50]) == []


def test_missing_or_disabled_engine_skips_process():
    with patch('engine.tesseract_ocr.shutil.which', return_value=None), \
         patch('engine.tesseract_ocr.subprocess.run') as process:
        assert read_crop(SCREEN[:20,:50]) == []
        process.assert_not_called()
    with patch.dict('os.environ', {'AUTOMATION_TESSERACT_ENABLED':'0'}), \
         patch('engine.tesseract_ocr.subprocess.run') as process:
        assert read_crop(SCREEN[:20,:50]) == []
        process.assert_not_called()


def test_adapter_tsv_line_assembly_and_scaling():
    tsv = ('level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
           '5\t1\t1\t1\t1\t1\t12\t15\t30\t24\t92\tJust\n'
           '5\t1\t1\t1\t1\t2\t45\t15\t30\t24\t88\tnow\n')
    with patch('engine.tesseract_ocr.shutil.which', return_value='/fake/tesseract'), \
         patch('engine.tesseract_ocr.subprocess.run', return_value=Mock(stdout=tsv.encode())) as process:
        result = read_crop(SCREEN[:20,:50], scale=3)
    assert result[0]['text'] == 'Just now'
    assert result[0]['bounds'] == (4,5,25,13)
    assert result[0]['confidence'] == .88
    assert process.call_args.kwargs['timeout'] == 3
    assert 'stdin' in process.call_args.args[0]


def test_timestamp_recovery_uses_candidate_geometry_without_rewriting_im():
    v = vision()
    v.read_text.return_value = [record('Im', bounds=(150,100,196,126))]
    # Enlarged header OCR maps Im to (1105, 625, 1128, 638).
    region = (1030,575,600,129)
    with patch('engine.tesseract_ocr.read_crop', return_value=[record('1m',bounds=(3,3,26,16))]):
        reads, result = retry_unreadable_header(v, SCREEN, region, [])
    assert result['accepted']
    assert result['age_seconds'] == 60
    assert any(i['text'] == 'Im' for i in reads)
    assert any(i.get('engine') == 'tesseract' for i in reads)


@pytest.mark.parametrize('text', ['2h', '2h . 0', '1m'])
def test_timestamp_recognized_age_bypasses_both_retries(text):
    v = vision()
    header = [record(text,bounds=(1105,625,1130,640))]
    with patch('engine.tesseract_ocr.read_crop') as secondary:
        _, result = retry_unreadable_header(v, SCREEN, (1030,575,600,129), header)
    v.read_text.assert_not_called()
    secondary.assert_not_called()
    assert result['accepted'] is (text == '1m')


def test_conflicting_header_never_reaches_secondary():
    v = vision()
    header = [record('1m',bounds=(1105,625,1130,640)),
              record('2h',bounds=(1305,625,1330,640))]
    with patch('engine.tesseract_ocr.read_crop') as secondary:
        _, result = retry_unreadable_header(v, SCREEN, (1030,575,600,129), header)
    assert result['reason'] == 'ambiguous_top_timestamp'
    secondary.assert_not_called()


def test_short_caption_hint_outside_top_header_does_not_reach_secondary():
    v = vision()
    v.read_text.return_value = [record('Im', bounds=(150,200,196,226))]
    with patch('engine.tesseract_ocr.read_crop') as secondary:
        _, result = retry_unreadable_header(v, SCREEN, (1030,575,600,129), [])
    assert not result['accepted']
    secondary.assert_not_called()
