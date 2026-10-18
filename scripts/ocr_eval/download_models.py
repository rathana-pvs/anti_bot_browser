#!/usr/bin/env python3
"""Explicit setup step: download only OCR model files from PaddlePaddle's HF repos."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

MODELS = ('PP-OCRv5_mobile_det', 'en_PP-OCRv5_mobile_rec',
          'PP-OCRv6_tiny_det', 'PP-OCRv6_tiny_rec')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--models', nargs='+', choices=MODELS, default=list(MODELS))
    args = parser.parse_args()
    for model in args.models:
        destination = args.output/model
        if destination.exists():
            raise ValueError(f'{destination} already exists; do not overwrite pinned models')
        with urllib.request.urlopen(f'https://huggingface.co/api/models/PaddlePaddle/{model}', timeout=30) as response:
            metadata = json.load(response)
        revision = metadata['sha']
        destination.mkdir(parents=True)
        hashes = {}
        for item in metadata['siblings']:
            name = item['rfilename']
            if name not in ('config.json','inference.json','inference.pdiparams','inference.yml'):
                continue
            url = f'https://huggingface.co/PaddlePaddle/{model}/resolve/{revision}/{name}'
            with urllib.request.urlopen(url, timeout=120) as response:
                data = response.read()
            (destination/name).write_bytes(data)
            hashes[name] = hashlib.sha256(data).hexdigest()
        (destination/'source.json').write_text(json.dumps(
            dict(repo=f'PaddlePaddle/{model}', revision=revision, hashes=hashes), indent=2)+'\n')
        print(model, revision, flush=True)


if __name__ == '__main__':
    main()
