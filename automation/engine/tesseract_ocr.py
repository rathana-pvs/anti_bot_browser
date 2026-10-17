"""Optional local OCR adapter. It reads bounded crops, never performs actions."""
import csv
import io
import os
import shutil
import subprocess

import cv2
import numpy as np


def read_crop(image, *, scale=3, psm=7, timeout=3.0):
    """Return line records in crop coordinates, or [] if unavailable/unreadable."""
    if os.environ.get('AUTOMATION_TESSERACT_ENABLED', '1').casefold() in {'0', 'false', 'off'}:
        return []
    executable = shutil.which(os.environ.get('AUTOMATION_TESSERACT_BIN', 'tesseract'))
    if not executable or image is None or not image.size:
        return []
    height, width = image.shape[:2]
    if width * height > 600_000 or max(width, height) > 1600:
        return []
    # Tesseract expects dark lettering on a light background. Binarize before
    # enlargement so interpolation does not invent grey strokes in tiny glyphs.
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if np.median(gray) < 128:
        binary = 255 - binary
    resized = cv2.resize(binary, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    ok, encoded = cv2.imencode('.png', resized)
    if not ok:
        return []
    try:
        result = subprocess.run(
            [executable, 'stdin', 'stdout', '-l', 'eng', '--psm', str(psm), 'tsv'],
            input=encoded.tobytes(), capture_output=True, timeout=timeout, check=True,
        )
        lines = {}
        for row in csv.DictReader(io.StringIO(result.stdout.decode('utf-8')), delimiter='\t'):
            if row.get('level') != '5' or not row.get('text', '').strip():
                continue
            confidence = float(row['conf']) / 100
            if not 0 <= confidence <= 1:
                continue
            left, top, w, h = (int(row[k]) / scale for k in ('left', 'top', 'width', 'height'))
            key = tuple(row[k] for k in ('page_num', 'block_num', 'par_num', 'line_num'))
            lines.setdefault(key, []).append((row['text'].strip(), confidence, (left, top, left+w, top+h)))
        records = []
        for words in lines.values():
            left = max(0, min(w[2][0] for w in words))
            top = max(0, min(w[2][1] for w in words))
            right = min(width, max(w[2][2] for w in words))
            bottom = min(height, max(w[2][3] for w in words))
            if right <= left or bottom <= top:
                continue
            records.append(dict(text=' '.join(w[0] for w in words),
                                confidence=min(w[1] for w in words), bounds=(left, top, right, bottom),
                                center=((left+right)/2, (top+bottom)/2), engine='tesseract'))
        return records
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError, KeyError, TypeError):
        return []
