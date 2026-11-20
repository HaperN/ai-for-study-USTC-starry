"""Bounded image decoding, orientation and metadata-free publication bytes."""
from dataclasses import dataclass
import hashlib
from io import BytesIO
import threading
import warnings

from .store import StoreError

MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_OUTPUT_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 24_000_000
MAX_SIDE = 12_000
_DECODERS = threading.BoundedSemaphore(2)


@dataclass(frozen=True)
class NormalizedImage:
    data: bytes
    mime: str
    width: int
    height: int
    sha256: str


def image_capabilities():
    try:
        from PIL import features
        types = []
        if features.check('zlib'): types.append('image/png')
        if features.check('jpg'): types.append('image/jpeg')
        if features.check('webp'): types.append('image/webp')
        return {'available': bool(types), 'mime_types': types}
    except ImportError:
        return {'available': False, 'mime_types': []}


def _check_dimensions(width, height):
    if width <= 0 or height <= 0:
        raise StoreError(400, '图片尺寸无效。')
    if width * height > MAX_PIXELS or max(width, height) > MAX_SIDE:
        raise StoreError(413, '图片尺寸过大，请缩小到 2400 万像素以内。')


def _webp_preflight(data):
    # Pillow's WebPAnimDecoder allocates its canvas in Image.open(). Inspect
    # both RIFF canvas and bitstream dimensions before invoking that decoder.
    if not data.startswith(b'RIFF'): return
    if len(data) < 12 or data[8:12] != b'WEBP':
        raise StoreError(400, '图片格式无效。')
    end = int.from_bytes(data[4:8], 'little') + 8
    if end != len(data): raise StoreError(400, 'WebP 文件长度无效。')
    position, canvas, frames = 12, None, []
    while position < end:
        if position + 8 > end: raise StoreError(400, 'WebP 数据不完整。')
        kind = data[position:position+4]; length = int.from_bytes(data[position+4:position+8], 'little')
        start = position + 8; finish = start + length
        if finish > end: raise StoreError(400, 'WebP 数据不完整。')
        chunk = data[start:finish]
        if kind in (b'ANIM', b'ANMF'): raise StoreError(415, '请使用静态图片。')
        if kind == b'VP8X':
            if length != 10 or canvas is not None: raise StoreError(400, 'WebP 画布标记无效。')
            if chunk[0] & 2: raise StoreError(415, '请使用静态图片。')
            canvas = (int.from_bytes(chunk[4:7], 'little')+1, int.from_bytes(chunk[7:10], 'little')+1)
            _check_dimensions(*canvas)
        elif kind == b'VP8 ':
            if length < 10 or chunk[0] & 1 or chunk[3:6] != b'\x9d\x01\x2a':
                raise StoreError(400, 'WebP 图像标记无效。')
            shape = (int.from_bytes(chunk[6:8], 'little') & 0x3fff, int.from_bytes(chunk[8:10], 'little') & 0x3fff)
            _check_dimensions(*shape); frames.append(shape)
        elif kind == b'VP8L':
            if length < 5 or chunk[0] != 0x2f: raise StoreError(400, 'WebP 图像标记无效。')
            bits = int.from_bytes(chunk[1:5], 'little')
            if bits >> 29: raise StoreError(400, 'WebP 版本无效。')
            shape = ((bits & 0x3fff)+1, ((bits >> 14) & 0x3fff)+1)
            _check_dimensions(*shape); frames.append(shape)
        position = finish + (length & 1)
    if position != end or len(frames) != 1 or (canvas is not None and canvas != frames[0]):
        raise StoreError(400, 'WebP 画布与图像内容不一致。')


def normalize_image(data: bytes) -> NormalizedImage:
    if not isinstance(data, bytes) or not data:
        raise StoreError(400, '请选择有效图片。')
    if len(data) > MAX_INPUT_BYTES:
        raise StoreError(413, '每张图片最多 10 MB，请缩小后重试。')
    _webp_preflight(data)
    if not _DECODERS.acquire(blocking=False):
        raise StoreError(429, '图片正在处理中，请稍后重试。')
    try:
        try:
            from PIL import Image, ImageOps, UnidentifiedImageError
        except ImportError as exc:
            raise StoreError(503, '图片处理环境尚未配置。') from exc
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(BytesIO(data)) as probe:
                    fmt = probe.format
                    if fmt not in ('PNG', 'JPEG', 'WEBP') or getattr(probe, 'n_frames', 1) != 1:
                        raise StoreError(415, '请上传静态 PNG、JPG 或 WebP 图片。')
                    width, height = probe.size
                    _check_dimensions(width, height)
                    probe.verify()
                with Image.open(BytesIO(data)) as original:
                    original.load()
                    oriented = ImageOps.exif_transpose(original)
                    mode = 'RGBA' if 'A' in oriented.getbands() or 'transparency' in oriented.info else 'RGB'
                    if fmt == 'JPEG': mode = 'RGB'
                    # A new pixel image drops EXIF, GPS, text, comments and ICC payloads.
                    clean = Image.new(mode, oriented.size)
                    clean.paste(oriented.convert(mode))
                    out = BytesIO()
                    if fmt == 'JPEG': clean.save(out, format='JPEG', quality=95, subsampling=0)
                    elif fmt == 'WEBP': clean.save(out, format='WEBP', lossless=True, method=4)
                    else: clean.save(out, format='PNG')
                    width, height = clean.size
                    clean.close(); oriented.close()
            normalized = out.getvalue()
            if len(normalized) > MAX_OUTPUT_BYTES:
                raise StoreError(413, '处理后的图片过大，请缩小图片。')
            return NormalizedImage(normalized, {'JPEG':'image/jpeg','PNG':'image/png','WEBP':'image/webp'}[fmt],
                                   width, height, hashlib.sha256(normalized).hexdigest())
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise StoreError(413, '图片尺寸过大，请缩小图片。') from exc
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
            raise StoreError(400, '图片无法读取，请重新截图或导出 PNG/JPG 后上传。') from exc
    finally:
        _DECODERS.release()
