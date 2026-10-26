"""Where to center a photo when it is cropped to a phone screen. Faces stay on this machine."""
from PIL import Image, ImageOps

DEFAULT = dict(x=.5, y=.4, faces=0)


def focus_point(image):
    """Center of all detected faces as 0-1 fractions; a calm default if none or no detector."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return dict(DEFAULT)
    try:
        small = ImageOps.contain(image.convert('RGB'), (960, 960))
        classifier = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        if classifier.empty():
            return dict(DEFAULT)
        gray = cv2.cvtColor(np.asarray(small), cv2.COLOR_RGB2GRAY)
        found = classifier.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(30, 30))
    except (cv2.error, OSError, ValueError):
        return dict(DEFAULT)
    if not len(found):
        return dict(DEFAULT)
    left, right = min(x for x, y, w, h in found), max(x + w for x, y, w, h in found)
    top, bottom = min(y for x, y, w, h in found), max(y + h for x, y, w, h in found)
    return dict(x=round((left + right) / 2 / small.width, 3), y=round((top + bottom) / 2 / small.height, 3),
                faces=len(found))
