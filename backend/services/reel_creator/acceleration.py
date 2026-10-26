"""Optional, thread-local GPU scaling; a failed GPU never prevents CPU rendering."""
import logging
import sys
import threading

from PIL import Image

logger = logging.getLogger(__name__)
_local = threading.local()


class GpuScaler:
    def __init__(self):
        import moderngl

        self.ctx = moderngl.create_standalone_context(
            require=330, **({'backend': 'egl'} if sys.platform.startswith('linux') else {}))
        self.textures = []
        self.target = self.fbo = self.program = self.vao = None
        renderer = self.ctx.info.get('GL_RENDERER', '').lower()
        if not renderer or any(name in renderer for name in ('llvmpipe', 'softpipe', 'software', 'swiftshader')):
            self.close()
            raise RuntimeError('OpenGL is using a software renderer')
        try:
            self.program = self.ctx.program(vertex_shader='''
                #version 330
                out vec2 uv;
                void main() {
                    vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
                    uv = p;
                    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
                }
            ''', fragment_shader='''
                #version 330
                uniform sampler2D photo;
                uniform vec4 box;
                uniform vec2 output_size;
                in vec2 uv;
                out vec4 color;
                void main() {
                    // Pillow widens its bilinear kernel when reducing a photo.
                    // Match that filtering to avoid shimmer on detailed images.
                    vec2 dimensions = vec2(textureSize(photo, 0));
                    vec2 center = mix(box.xy, box.zw, uv) * dimensions - 0.5;
                    vec2 scale = max((box.zw - box.xy) * dimensions / output_size, vec2(1.0));
                    ivec2 origin = ivec2(floor(center));
                    vec3 sum = vec3(0.0);
                    float total = 0.0;
                    for (int y = -1; y <= 2; y++) {
                        for (int x = -1; x <= 2; x++) {
                            ivec2 pixel = origin + ivec2(x, y);
                            vec2 weight = max(vec2(0.0), 1.0 - abs(vec2(pixel) - center) / scale);
                            float w = weight.x * weight.y;
                            sum += texelFetch(photo, clamp(pixel, ivec2(0), ivec2(dimensions) - 1), 0).rgb * w;
                            total += w;
                        }
                    }
                    color = vec4(sum / total, 1.0);
                }
            ''')
            self.vao = self.ctx.vertex_array(self.program, [])
            self.program['photo'].value = 0
        except Exception:
            self.close()
            raise
        logger.info('Reel photo scaling uses %s', self.ctx.info['GL_RENDERER'])

    def resize(self, image, size, box):
        import moderngl

        texture = next((tex for source, tex in self.textures if source is image), None)
        if texture is None:
            # Retain the image with its texture: Python IDs may be reused between previews.
            if len(self.textures) >= 3:
                self.textures.pop(0)[1].release()
            texture = self.ctx.texture(image.size, 3, image.tobytes(), alignment=1)
            texture.filter = (moderngl.LINEAR, moderngl.LINEAR)
            texture.repeat_x = texture.repeat_y = False
            self.textures.append((image, texture))
        if self.target is None or self.target.size != size:
            if self.fbo:
                self.fbo.release()
                self.target.release()
            self.target = self.ctx.texture(size, 3)
            self.fbo = self.ctx.framebuffer(color_attachments=[self.target])
        texture.use(0)
        self.program['box'].value = (box[0] / image.width, box[1] / image.height,
                                     box[2] / image.width, box[3] / image.height)
        self.program['output_size'].value = size
        self.fbo.use()
        self.ctx.viewport = (0, 0, *size)
        self.vao.render(mode=moderngl.TRIANGLES, vertices=3)
        # Upload and read use the same row order, preserving Pillow's top-left origin.
        return Image.frombytes('RGB', size, self.fbo.read(components=3, alignment=1))

    def close(self):
        for _, texture in self.textures:
            texture.release()
        self.textures.clear()
        for resource in (self.fbo, self.target, self.vao, self.program):
            if resource is not None:
                resource.release()
        self.ctx.release()


def resize_photo(image, size, box):
    """Use the GPU in this rendering thread, falling back for this and later frames."""
    if not getattr(_local, 'disabled', False):
        try:
            if getattr(_local, 'scaler', None) is None:
                _local.scaler = GpuScaler()
            return _local.scaler.resize(image, size, box)
        except Exception as exc:
            logger.info('Reel photo scaling falls back to CPU: %s', exc)
            _local.disabled = True
            scaler = getattr(_local, 'scaler', None)
            _local.scaler = None
            if scaler is not None:
                try:
                    scaler.close()
                except Exception:
                    pass
    return image.resize(size, Image.Resampling.BILINEAR, box=box)
