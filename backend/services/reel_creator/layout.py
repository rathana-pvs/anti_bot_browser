"""Canvas dimensions shared by layout, framing and encoding."""

def canvas_size(spec):
    return 1080, {'9:16': 1920, '4:5': 1350, '1:1': 1080}[spec.aspect_ratio]


def output_size(spec, preview=False):
    width = 360 if preview else spec.resolution
    _, height = canvas_size(spec)
    return width, round(height * width / 1080)


def layout_y(spec, value):
    return round(value * canvas_size(spec)[1] / 1920)


def type_scale(spec):
    return (canvas_size(spec)[1] / 1920) ** .5
