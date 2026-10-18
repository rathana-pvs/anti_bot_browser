"""Selected-framework device probe used by desktop setup, without loading weights."""
import contextlib
from importlib.metadata import version
import json
import sys
from engine.ocr_reader import selected_engine, probe_cuda

if __name__=='__main__':
    with contextlib.redirect_stdout(sys.stderr):
        engine = selected_engine()
        available,name = probe_cuda(engine)
        package = 'easyocr' if engine=='easyocr' else 'paddleocr'
        installed = version(package)
    if '--json' in sys.argv:
        print(json.dumps([available,name]))
    else:
        print(f'{engine}:{installed}')
        print(name or '')
