import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import ocr_reader
from engine.vision import VisionEngine
from engine.ocr_worker_client import read_text_with_shared_worker, SharedOcrUnavailable


class OcrSelectionTests(unittest.TestCase):
    def test_default_saved_selection_and_process_pin(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,{},clear=True), \
             patch.object(ocr_reader,'runtime_root',return_value=Path(directory)):
            self.assertEqual(ocr_reader.selected_engine(),'paddleocr-v5')
            data=Path(directory)/'data';data.mkdir()
            (data/'manager_settings.json').write_text(json.dumps({'ocr_engine':'paddleocr-v6'}))
            self.assertEqual(ocr_reader.selected_engine(),'paddleocr-v6')
            with patch.dict(os.environ,{'AUTOMATION_OCR_ENGINE':'easyocr'}):
                self.assertEqual(ocr_reader.selected_engine(),'easyocr')
                (data/'manager_settings.json').write_text(json.dumps({'ocr_engine':'paddleocr-v5'}))
                self.assertEqual(ocr_reader.selected_engine(),'easyocr')

    def test_engine_and_model_directory_isolate_caches(self):
        signatures={ocr_reader.configuration_signature(e) for e in ocr_reader.ENGINES}
        self.assertEqual(len(signatures),3)
        before=ocr_reader.configuration_signature('paddleocr-v5')
        with patch.dict(os.environ,{'AUTOMATION_PADDLE_MODEL_DIR':'/different/models'}):
            self.assertNotEqual(before,ocr_reader.configuration_signature('paddleocr-v5'))

    def test_unknown_explicit_engine_is_rejected(self):
        with patch.dict(os.environ,{'AUTOMATION_OCR_ENGINE':'typo'}):
            with self.assertRaises(ValueError):ocr_reader.selected_engine()

    def test_element_cache_is_separate_for_each_engine(self):
        paths=[]
        for engine in ocr_reader.ENGINES:
            with patch.dict(os.environ,{'AUTOMATION_OCR_ENGINE':engine}):
                paths.append(VisionEngine(Mock(profile_id='engine_isolation')).cache_file)
        self.assertEqual(len(set(paths)),3)

    def test_existing_vision_keeps_engine_after_configuration_changes(self):
        with patch.dict(os.environ, {"AUTOMATION_OCR_ENGINE": "paddleocr-v5"}):
            vision = VisionEngine(Mock(profile_id="engine_pin"))
            signature = vision._ocr_signature()
        with patch.dict(os.environ, {"AUTOMATION_OCR_ENGINE": "easyocr"}):
            self.assertEqual(vision._selected_ocr_engine(), "paddleocr-v5")
            self.assertEqual(vision._ocr_signature(), signature)

    def test_local_reader_cache_cannot_return_other_engine(self):
        with patch.object(VisionEngine,'_ocr_readers',{}), \
             patch('engine.vision.create_reader',side_effect=[Mock(device='cpu'),Mock(device='cpu')]) as create:
            with patch.dict(os.environ,{'AUTOMATION_OCR_ENGINE':'paddleocr-v5'}):
                first,_=VisionEngine._get_ocr_reader(requested_device='cpu')
                repeat,_=VisionEngine._get_ocr_reader(requested_device='cpu')
            with patch.dict(os.environ,{'AUTOMATION_OCR_ENGINE':'paddleocr-v6'}):
                second,_=VisionEngine._get_ocr_reader(requested_device='cpu')
        self.assertIs(first,repeat)
        self.assertIsNot(first,second)
        self.assertEqual(create.call_count,2)


class PaddleAdapterTests(unittest.TestCase):
    def test_both_models_keep_polygon_text_and_confidence_contract(self):
        polygon=np.array([[[1,2],[20,2],[20,12],[1,12]]])
        for engine in ('paddleocr-v5','paddleocr-v6'):
            model=Mock();model.predict.return_value=iter([{'rec_polys':polygon,'rec_texts':['Post'],'rec_scores':np.array([.9])}])
            result=ocr_reader.Reader(model,engine,'cpu').readtext(np.zeros((20,30,3),dtype=np.uint8))
            self.assertEqual(result[0][0],[[1.,2.],[20.,2.],[20.,12.],[1.,12.]])
            self.assertEqual(result[0][1],'Post')
            self.assertAlmostEqual(result[0][2],.9)

    def test_malformed_results_fail_instead_of_returning_partial_text(self):
        model=Mock();model.predict.return_value=iter([{'rec_polys':[],'rec_texts':['Post'],'rec_scores':[.9]}])
        with self.assertRaises(RuntimeError):ocr_reader.Reader(model,'paddleocr-v5','cpu').readtext(np.zeros((20,30,3)))

    def test_paddle_uses_offline_pinned_models_and_disables_document_features(self):
        model=Mock();model.predict.return_value=[{'rec_polys':[],'rec_texts':[],'rec_scores':[]}]
        constructor=Mock(return_value=model)
        with patch.dict(sys.modules,{'paddle':SimpleNamespace(__version__='3.2.2'),
                                    'paddleocr':SimpleNamespace(PaddleOCR=constructor)}), \
             patch.object(ocr_reader,'probe_cuda',return_value=(False,None)), \
             patch.object(ocr_reader,'verify_models') as verify, \
             patch('importlib.metadata.version',side_effect=lambda name:{'paddleocr':'3.7.0','paddlex':'3.7.2'}[name]):
            reader=ocr_reader.create_reader(engine='paddleocr-v5',threads=40)
        verify.assert_called_once_with('paddleocr-v5')
        args=constructor.call_args.kwargs
        self.assertEqual(args['device'],'cpu')
        self.assertEqual(args['cpu_threads'],4)
        self.assertFalse(args['use_doc_unwarping'])
        self.assertFalse(args['use_doc_orientation_classify'])
        self.assertFalse(args['use_textline_orientation'])
        self.assertEqual(args['text_recognition_model_name'],'en_PP-OCRv5_mobile_rec')
        self.assertEqual(reader.device,'cpu')

    def test_missing_weights_fail_before_paddleocr_constructor(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,{'AUTOMATION_PADDLE_MODEL_DIR':directory}):
            with self.assertRaisesRegex(RuntimeError,'Missing or invalid'):ocr_reader.verify_models('paddleocr-v5')

    def test_probe_uses_selected_framework_not_torch_gpu(self):
        with patch.dict(sys.modules,{'paddle':SimpleNamespace(is_compiled_with_cuda=lambda:False),
                                    'torch':SimpleNamespace(cuda=SimpleNamespace(is_available=lambda:True,get_device_name=lambda i:'Torch GPU'))}):
            self.assertEqual(ocr_reader.probe_cuda('paddleocr-v5'),(False,None))
            self.assertEqual(ocr_reader.probe_cuda('easyocr'),(True,'Torch GPU'))


class SharedContractTests(unittest.TestCase):
    def test_client_rejects_old_or_different_worker_even_with_valid_text(self):
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=json.dumps({'items':[],'engine':'easyocr','signature':'other'}).encode()
        with patch.dict(os.environ,{'AUTOMATION_OCR_WORKER_URL':'http://127.0.0.1:1','AUTOMATION_OCR_ENGINE':'paddleocr-v5'}), \
             patch('engine.ocr_worker_client.request.urlopen',return_value=response):
            with self.assertRaises(SharedOcrUnavailable):read_text_with_shared_worker(np.zeros((20,30,3),dtype=np.uint8))


if __name__=='__main__':unittest.main()
