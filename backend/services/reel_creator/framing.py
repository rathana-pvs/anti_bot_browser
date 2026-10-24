"""Bounded photo transforms in the selected 1080-pixel-wide canvas."""
from PIL import Image
from .layout import canvas_size, layout_y

WIDTH, HEIGHT = 1080, 1920


def crop_image(image, crop):
    if crop is None:
        return image
    width, height = image.size
    left, top = round(crop.left * width), round(crop.top * height)
    right, bottom = round(crop.right * width), round(crop.bottom * height)
    left, top = min(left, width - 1), min(top, height - 1)
    return image.crop((left, top, max(left + 1, right), max(top + 1, bottom)))


def photo_bounds(spec, photo, image_size):
    WIDTH, HEIGHT = canvas_size(spec)
    width, height = image_size
    treatment = photo.framing if photo.framing != 'inherit' else spec.treatment
    has_text = bool(spec.headline or (spec.show_subtitle and spec.supporting) or spec.category)
    if treatment == 'fit':
        bottom = layout_y(spec, 1210 if has_text else 1730)
        top = layout_y(spec, 230)
        scale = min(880 / width, (bottom - top) / height) * photo.zoom
        w, h = width * scale, height * scale
        x, y = (WIDTH - w) / 2, (top + bottom - h) / 2
    else:
        scale = max(WIDTH / width, HEIGHT / height) * photo.zoom
        w, h = width * scale, height * scale
        x, y = (WIDTH - w) * photo.focal_x, (HEIGHT - h) * photo.focal_y
    return treatment, (x + photo.offset_x * WIDTH, y + photo.offset_y * HEIGHT, w, h)


def transformed_photo(image, bounds, size=(WIDTH, HEIGHT)):
    """Transform into a fixed-size canvas instead of allocating a huge zoomed image."""
    x, y, width, height = bounds
    sx, sy = image.width / width, image.height / height
    return image.convert('RGBA').transform(
        size, Image.Transform.AFFINE, (sx, 0, -x * sx, 0, sy, -y * sy),
        resample=Image.Resampling.BICUBIC, fillcolor=(0, 0, 0, 0),
    )
