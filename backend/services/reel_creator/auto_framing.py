"""Local framing suggestions; face locations are never sent to an external service."""
from PIL import Image, ImageOps


def framing_for_faces(size, faces, aspect_ratio='9:16'):
    """Prefer a full canvas crop only when every face fits above the news panel."""
    width, height = size
    canvas_height = {'9:16':1920, '4:5':1350, '1:1':1080}[aspect_ratio]
    Y = lambda value: round(value * canvas_height / 1920)
    settings = dict(framing='fit', zoom=1, offset_x=0, offset_y=0, focal_x=.5, focal_y=.5)
    if not faces:
        return settings
    # Include breathing room around all faces, rather than choosing one person.
    left = min(x - w * .2 for x, y, w, h in faces)
    right = max(x + w * 1.2 for x, y, w, h in faces)
    top = min(y - h * .25 for x, y, w, h in faces)
    bottom = max(y + h * 1.3 for x, y, w, h in faces)
    scale = max(1080 / width, canvas_height / height)
    w, h = width * scale, height * scale
    low_x, high_x = max(1080 - w, 64 - left * scale), min(0, 1016 - right * scale)
    low_y, high_y = max(canvas_height - h, Y(230) - top * scale), min(0, Y(1100) - bottom * scale)
    # Keep the composition stable; automatic motion could undo a safe crop.
    settings['movement'] = 'still'
    if low_x <= high_x and low_y <= high_y:
        x = min(high_x, max(low_x, 540 - (left + right) / 2 * scale))
        y = min(high_y, max(low_y, Y(650) - (top + bottom) / 2 * scale))
        settings.update(framing='fill', focal_x=x / (1080 - w) if w > 1080 else .5,
                        focal_y=y / (canvas_height - h) if h > canvas_height else .5)
    return settings


def suggest_framing(image, aspect_ratio='9:16'):
    try:
        import cv2
        import numpy as np
    except ImportError:
        return dict(settings=framing_for_faces(image.size, [], aspect_ratio), faces=0, status='unavailable')
    try:
        small = ImageOps.contain(image.convert('RGB'), (960, 960))
        classifier = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        if classifier.empty():
            raise ValueError('Face detector unavailable')
        gray = cv2.cvtColor(np.asarray(small), cv2.COLOR_RGB2GRAY)
        found = classifier.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(30, 30))
        sx, sy = image.width / small.width, image.height / small.height
        faces = [(x * sx, y * sy, w * sx, h * sy) for x, y, w, h in found]
        return dict(settings=framing_for_faces(image.size, faces, aspect_ratio), faces=len(faces),
                    status='detected' if faces else 'none')
    except (cv2.error, OSError, ValueError):
        return dict(settings=framing_for_faces(image.size, [], aspect_ratio), faces=0, status='unavailable')
