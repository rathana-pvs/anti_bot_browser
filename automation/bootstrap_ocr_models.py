"""Explicit setup/download step. Inference never downloads model weights."""
import argparse
import hashlib
import os
from urllib.request import urlopen
from engine.ocr_reader import ENGINES, MODELS, MODEL_PAIRS, model_root, verify_models


def install_paddle_models(engine):
    for name in MODEL_PAIRS[engine]:
        revision, files = MODELS[name]
        directory = model_root(engine)/name
        directory.mkdir(parents=True, exist_ok=True)
        for filename, expected in files.items():
            destination = directory/filename
            if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == expected:
                continue
            url = f'https://huggingface.co/PaddlePaddle/{name}/resolve/{revision}/{filename}'
            temporary = destination.with_suffix(destination.suffix+f'.{os.getpid()}.part')
            try:
                print(f'Downloading {name}/{filename}', flush=True)
                with urlopen(url,timeout=120) as response, temporary.open('wb') as output:
                    while chunk := response.read(1024*1024):
                        output.write(chunk)
                if hashlib.sha256(temporary.read_bytes()).hexdigest() != expected:
                    raise RuntimeError(f'Model checksum mismatch: {name}/{filename}')
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
    verify_models(engine)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine',choices=(*ENGINES,'all'),default='all')
    args = parser.parse_args()
    for engine in ENGINES if args.engine=='all' else (args.engine,):
        if engine == 'easyocr':
            import easyocr
            directory = model_root(engine)
            directory.mkdir(parents=True,exist_ok=True)
            easyocr.Reader(['en'],gpu=False,verbose=False,
                           model_storage_directory=str(directory),download_enabled=True)
            for name in ('craft_mlt_25k.pth','english_g2.pth'):
                if not (directory/name).is_file():
                    raise RuntimeError(f'Missing EasyOCR model: {name}')
        else:
            install_paddle_models(engine)
        print(f'{engine} models ready.',flush=True)


if __name__=='__main__':
    main()
