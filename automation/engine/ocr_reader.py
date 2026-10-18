"""One offline OCR contract for shared and local readers. No action decisions."""
import hashlib
import json
import os
from pathlib import Path
import threading

from .runtime_paths import automation_dir, runtime_root

ENGINES = ('paddleocr-v5', 'paddleocr-v6', 'easyocr')
DEFAULT_ENGINE = 'paddleocr-v5'
ENGINE_LABELS = {'paddleocr-v5': 'V5 + Tesseract (Default)',
                 'paddleocr-v6': 'V6 + Tesseract (Speed)',
                 'easyocr': 'EasyOCR + Tesseract (Legacy / Generic)'}
MODEL_PAIRS = {'paddleocr-v5': ('PP-OCRv5_mobile_det', 'en_PP-OCRv5_mobile_rec'),
               'paddleocr-v6': ('PP-OCRv6_tiny_det', 'PP-OCRv6_tiny_rec')}
# Official PaddlePaddle model revisions evaluated on the saved evidence.
MODELS = {
    'PP-OCRv5_mobile_det': ('0d63e78e2b680928f6b1747d76a08db6e645efb7', {
        'inference.json': '05feef1acb00aa4cd7362b15f7f501fc4f99d7b1fa73c1c871e0c7b1504b0f5c',
        'inference.pdiparams': 'afa1820cb16c1fd0dad589d0f8b389139061c1ef6d68019685fd07be997dda5b',
        'inference.yml': '98069072e1b6b37d727fd9d9f11725faa46d6ea0de012f2ed26caea011c37699'}),
    'en_PP-OCRv5_mobile_rec': ('267c36e24c331595590fe7bd72bde2436fd286f2', {
        'inference.json': 'fd1b6ec722ea841a72d3ba43e527df1d1066d5d7808e0503ee3eec7265188753',
        'inference.pdiparams': '3ec8a97ed6cefe8568d3e2ee90bb193299b566a7661aa4fd52d224b96b59f66b',
        'inference.yml': '27e91d0582f40168aa218303c76e184bc78fa7a5d105aad0cfbad8458b441067'}),
    'PP-OCRv6_tiny_det': ('d3177d4e5551463292a61e27cfca2b53e7c3fe9d', {
        'inference.json': '935c18972799dd8f2a2ff424b9f8b2c6adee4b89f3a76f0746210ec33ba9b3d8',
        'inference.pdiparams': '853f7ed317d4f2f80de646842e5bbc32f9d39c601562cbe466cda42e4bafb1b1',
        'inference.yml': '3ac018be6f97499a08faa3bbdeb33640968d9307f6736d152902747a9f259593'}),
    'PP-OCRv6_tiny_rec': ('0736086f72f666350ebcdc0c3a504eeac89cdfad', {
        'inference.json': 'b5b14770c7dcf092781e92f4278a2ae5f95048f08b4b8a04140e88cb2745f147',
        'inference.pdiparams': 'bb2f8f54d1e25f28c71b6fa4fe23f5940e159cae27fbee96155c99f822156e57',
        'inference.yml': '66170210bad538e83fff3c4a3867e547d6bf20b50d64b20347c4b913f3034ea1'}),
}


def validate_engine(value):
    if value not in ENGINES:
        raise ValueError(f'Unknown OCR engine: {value!r}')
    return value


def selected_engine():
    """Subprocesses receive a pinned environment; settings apply on restart."""
    configured = os.environ.get('AUTOMATION_OCR_ENGINE', '').strip()
    if configured:
        return validate_engine(configured)
    try:
        saved = json.loads((runtime_root()/'data/manager_settings.json').read_text())
        return validate_engine(saved.get('ocr_engine', DEFAULT_ENGINE))
    except (OSError, ValueError, TypeError, AttributeError):
        return DEFAULT_ENGINE


def model_root(engine):
    if engine == 'easyocr':
        return Path(os.environ.get('AUTOMATION_OCR_MODEL_DIR') or automation_dir()/'models/easyocr').resolve()
    return Path(os.environ.get('AUTOMATION_PADDLE_MODEL_DIR') or automation_dir()/'models/paddleocr').resolve()


def configuration_signature(engine=None):
    engine = validate_engine(engine or selected_engine())
    models = [(name, MODELS[name][0]) for name in MODEL_PAIRS.get(engine, ())]
    payload = [engine, str(model_root(engine)), models,
               'easyocr-1.7.2' if engine=='easyocr' else 'paddleocr-3.7.0/paddlex-3.7.2/paddle-3.2.2']
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()[:24]


def verify_models(engine):
    for name in MODEL_PAIRS[engine]:
        for filename, expected in MODELS[name][1].items():
            path = model_root(engine)/name/filename
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise RuntimeError(f'Missing or invalid OCR model: {path}. Run automation/bootstrap_ocr_models.py.')


def probe_cuda(engine=None):
    engine = engine or selected_engine()
    if engine == 'easyocr':
        import torch
        available = bool(torch.cuda.is_available())
        return available, torch.cuda.get_device_name(0) if available else None
    import paddle
    available = bool(paddle.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0)
    return available, paddle.device.cuda.get_device_name(0) if available else None


class Reader:
    """Expose EasyOCR-compatible tuples while keeping each model serialized."""
    def __init__(self, model, engine, device):
        self.model = model
        self.engine = engine
        self.device = device
        self.signature = configuration_signature(engine)
        self.lock = threading.Lock()

    def readtext(self, image, detail=1, paragraph=False):
        if detail != 1 or paragraph:
            raise ValueError('OCR adapter supports detail=1, paragraph=False only')
        with self.lock:
            if self.engine == 'easyocr':
                return self.model.readtext(image, detail=1, paragraph=False)
            result = next(iter(self.model.predict(image)))
            boxes, texts, scores = result['rec_polys'], result['rec_texts'], result['rec_scores']
            if not len(boxes) == len(texts) == len(scores):
                raise RuntimeError('PaddleOCR returned inconsistent recognition arrays')
            return [([[float(x),float(y)] for x,y in box],str(text),float(score))
                    for box,text,score in zip(boxes,texts,scores)]


def create_reader(languages=('en',), requested_device='auto', threads=4, engine=None):
    engine = validate_engine(engine or selected_engine())
    if engine != 'easyocr' and tuple(languages) != ('en',):
        raise ValueError('The configured PaddleOCR models support English UI reads only')
    requested = requested_device.strip().casefold()
    if requested not in ('auto','cpu','cuda','gpu'):
        raise ValueError(f'Invalid OCR device: {requested}')
    cuda = requested != 'cpu' and probe_cuda(engine)[0]
    device = 'cuda' if cuda else 'cpu'
    threads = max(1,min(4,int(threads)))
    if engine == 'easyocr':
        import torch
        import easyocr
        if not cuda:
            torch.set_num_threads(threads)
        model = easyocr.Reader(list(languages),gpu=cuda,verbose=False,
            model_storage_directory=str(model_root(engine)),download_enabled=False)
    else:
        from importlib.metadata import version
        import paddle
        if paddle.__version__.split('+')[0] != '3.2.2' or version('paddleocr') != '3.7.0' or version('paddlex') != '3.7.2':
            raise RuntimeError('OCR requires paddlepaddle 3.2.2, paddleocr 3.7.0, paddlex 3.7.2; run setup/repair.')
        verify_models(engine)
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'
        from paddleocr import PaddleOCR
        detector,recognizer = MODEL_PAIRS[engine]
        model = PaddleOCR(device='gpu:0' if cuda else 'cpu',cpu_threads=threads,
            text_detection_model_name=detector,text_recognition_model_name=recognizer,
            text_detection_model_dir=str(model_root(engine)/detector),
            text_recognition_model_dir=str(model_root(engine)/recognizer),
            use_doc_orientation_classify=False,use_doc_unwarping=False,use_textline_orientation=False,
            text_rec_score_thresh=0.0,enable_mkldnn=not cuda)
    reader = Reader(model,engine,device)
    import numpy as np
    reader.readtext(np.zeros((64,128,3),dtype=np.uint8))
    return reader
