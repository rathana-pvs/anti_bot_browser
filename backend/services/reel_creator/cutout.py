"""Local U²-Net small inference for Pop Art. Originals are never modified or uploaded.

Model/preprocessing contract: https://github.com/danielgatis/rembg/blob/main/rembg/sessions/u2netp.py
U²-Net: https://github.com/xuebinqin/U-2-Net (Apache-2.0).
"""
import hashlib
import io
import os
from pathlib import Path
import tempfile
import threading

import httpx
from PIL import Image, ImageChops, ImageOps

MODEL_URL = 'https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx'
MODEL_SHA256 = '309c8469258dda742793dce0ebea8e6dd393174f89934733ecc8b14c76f4ddd8'
CACHE_VERSION = 'u2netp-v1'
_lock = threading.Lock()  # OpenCV Net's setInput/forward are not safe to interleave.
_net = None


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _model_path(directory):
    path = directory / 'u2netp.onnx'
    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == MODEL_SHA256:
        return path
    # Download weights only. Photo data stays on this computer. Bound the download size and time.
    try:
        data = bytearray()
        with httpx.stream('GET', MODEL_URL, follow_redirects=True, timeout=30) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > 8 * 1024 * 1024:
                    raise ValueError('The background-removal model download is invalid. Try again.')
        if hashlib.sha256(data).hexdigest() != MODEL_SHA256:
            raise ValueError('The background-removal model download is invalid. Try again.')
        _atomic_write(path, data)
    except httpx.HTTPError as exc:
        raise ValueError('Pop Art needs a one-time background-removal model download. Check your internet connection and try again.') from exc
    return path


def _segment(image, model_dir):
    import cv2
    import numpy as np

    global _net
    if _net is None:
        _net = cv2.dnn.readNetFromONNX(str(_model_path(model_dir)))
        _net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        _net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
    pixels = np.asarray(image.convert('RGB').resize((320, 320), Image.Resampling.LANCZOS), dtype=np.float32)
    pixels /= max(float(pixels.max()), 1e-6)
    pixels = (pixels - np.array([.485, .456, .406], dtype=np.float32)) / np.array([.229, .224, .225], dtype=np.float32)
    _net.setInput(np.ascontiguousarray(pixels.transpose(2, 0, 1)[None]))
    prediction = _net.forward()[0, 0]
    low, high = float(prediction.min()), float(prediction.max())
    if not np.isfinite(prediction).all() or high - low < 1e-6:
        raise ValueError('Pop Art could not find a foreground subject. Try a clearer portrait or a transparent PNG.')
    mask = np.clip((prediction - low) / (high - low), 0, 1)
    # Suppress weak background haze without hardening the hair/edge transition.
    mask = np.clip((mask - .05) / .9, 0, 1)
    alpha = Image.fromarray((mask * 255).astype('uint8')).resize(image.size, Image.Resampling.LANCZOS)
    if alpha.getbbox() is None:
        raise ValueError('Pop Art could not find a foreground subject. Try a clearer portrait or a transparent PNG.')
    result = image.convert('RGBA')
    result.putalpha(ImageChops.multiply(result.getchannel('A'), alpha))
    return result


def prepare_cutout(source, cache_dir):
    """Return a durable derived PNG, or the original when it is already a cutout."""
    source, cache_dir = Path(source), Path(cache_dir)
    with _lock:
        with Image.open(source) as raw:
            image = ImageOps.exif_transpose(raw).convert('RGBA')
        if image.getchannel('A').getextrema()[0] < 255:
            return source
        fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
        target = cache_dir / f'{fingerprint}-{CACHE_VERSION}.png'
        if target.is_file():
            return target
        # 20 MP uploads need not occupy a full-sized inference/output buffer.
        image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
        try:
            result = _segment(image, cache_dir / 'models')
        except ImportError as exc:
            raise ValueError('Background removal needs OpenCV. Open Settings → Setup and repair → Install / update runtime.') from exc
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError('Pop Art could not remove this background. Try another portrait or a transparent PNG.') from exc
        output = io.BytesIO()
        result.save(output, 'PNG')
        _atomic_write(target, output.getvalue())
        return target
