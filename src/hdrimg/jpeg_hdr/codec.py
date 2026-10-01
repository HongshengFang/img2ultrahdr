"""Thin ctypes binding to Google's libultrahdr C API (MSYS2 1.5.1)."""
import ctypes as C
import ctypes.util
import io, os
from pathlib import Path
import numpy as np
from PIL import Image

from .runtime import runtime_dir
from .metadata import normalize_iso_metadata
from .xmp import add_google_xmp, inspect_google_xmp
from ..errors import DependencyError

class Error(C.Structure):
    _fields_ = [('code', C.c_int), ('has_detail', C.c_int), ('detail', C.c_char * 256)]

class Compressed(C.Structure):
    _fields_ = [('data', C.c_void_p), ('size', C.c_size_t), ('capacity', C.c_size_t),
                ('cg', C.c_int), ('ct', C.c_int), ('range', C.c_int)]

class Block(C.Structure):
    _fields_ = [('data', C.c_void_p), ('size', C.c_size_t), ('capacity', C.c_size_t)]

class Metadata(C.Structure):
    _fields_ = [(name, C.c_float * 3) for name in
                ['max_content_boost', 'min_content_boost', 'gamma', 'offset_sdr', 'offset_hdr']]
    _fields_ += [('hdr_capacity_min', C.c_float), ('hdr_capacity_max', C.c_float), ('use_base_cg', C.c_int)]

class Raw(C.Structure):
    _fields_ = [('fmt', C.c_int), ('cg', C.c_int), ('ct', C.c_int), ('range', C.c_int),
                ('w', C.c_uint), ('h', C.c_uint), ('planes', C.c_void_p * 3), ('stride', C.c_uint * 3)]

class Codec:
    def __init__(self):
        native = runtime_dir() / 'native'
        override = os.environ.get('IMG2UHDR_LIBUHDR')
        self.dll_path = None
        if os.name == 'nt':
            library = Path(override) if override else native / 'libuhdr-1.dll'
            if library.parent.is_dir():
                self.dll_path = os.add_dll_directory(str(library.parent.resolve()))
        else:
            library = override or ctypes.util.find_library('uhdr')
        if not library:
            raise DependencyError('libultrahdr shared library missing; see docs/jpeg-hdr.md')
        try:
            self.lib = C.CDLL(str(library))
        except OSError as exc:
            raise DependencyError(
                f'Cannot load libultrahdr from {library}; see docs/jpeg-hdr.md'
            ) from exc
        specs = {
            'is_uhdr_image': (C.c_int, [C.c_void_p, C.c_int]),
            'uhdr_create_encoder': (C.c_void_p, []),
            'uhdr_release_encoder': (None, [C.c_void_p]),
            'uhdr_enc_set_compressed_image': (Error, [C.c_void_p, C.POINTER(Compressed), C.c_int]),
            'uhdr_enc_set_gainmap_image': (Error, [C.c_void_p, C.POINTER(Compressed), C.POINTER(Metadata)]),
            'uhdr_encode': (Error, [C.c_void_p]),
            'uhdr_get_encoded_stream': (C.POINTER(Compressed), [C.c_void_p]),
            'uhdr_create_decoder': (C.c_void_p, []),
            'uhdr_release_decoder': (None, [C.c_void_p]),
            'uhdr_dec_set_image': (Error, [C.c_void_p, C.POINTER(Compressed)]),
            'uhdr_dec_probe': (Error, [C.c_void_p]),
            'uhdr_dec_get_image_width': (C.c_int, [C.c_void_p]),
            'uhdr_dec_get_image_height': (C.c_int, [C.c_void_p]),
            'uhdr_dec_get_gainmap_width': (C.c_int, [C.c_void_p]),
            'uhdr_dec_get_gainmap_height': (C.c_int, [C.c_void_p]),
            'uhdr_dec_get_gainmap_metadata': (C.POINTER(Metadata), [C.c_void_p]),
            'uhdr_dec_get_base_image': (C.POINTER(Block), [C.c_void_p]),
            'uhdr_dec_get_gainmap_image': (C.POINTER(Block), [C.c_void_p]),
            'uhdr_dec_set_out_img_format': (Error, [C.c_void_p, C.c_int]),
            'uhdr_dec_set_out_color_transfer': (Error, [C.c_void_p, C.c_int]),
            'uhdr_dec_set_out_max_display_boost': (Error, [C.c_void_p, C.c_float]),
            'uhdr_decode': (Error, [C.c_void_p]),
            'uhdr_get_decoded_image': (C.POINTER(Raw), [C.c_void_p]),
        }
        for name, (result, args) in specs.items():
            fun = getattr(self.lib, name)
            fun.restype, fun.argtypes = result, args

    @staticmethod
    def check(error):
        if error.code:
            raise RuntimeError('libultrahdr: ' + error.detail.decode(errors='replace'))

    @staticmethod
    def compressed(data):
        buf = C.create_string_buffer(data)
        # BT709, sRGB, full range (enum values verified against the header).
        return buf, Compressed(C.cast(buf, C.c_void_p), len(data), len(data), 0, 3, 1)

    def encode(self, base_bytes, gain_bytes, max_ev, *, hdr_capacity_max=None):
        if not np.isfinite(max_ev) or max_ev <= 0:
            raise ValueError('max_ev must be finite and positive')
        capacity = 2 ** max_ev if hdr_capacity_max is None else hdr_capacity_max
        if not np.isfinite(capacity) or capacity <= 1:
            raise ValueError('hdr_capacity_max must be finite and greater than 1')
        base_buf, base = self.compressed(base_bytes)
        if self.lib.is_uhdr_image(base.data, base.size):
            raise ValueError('Input already contains an Ultra HDR gain map')
        gain_buf, gain = self.compressed(gain_bytes)
        meta = Metadata()
        meta.max_content_boost[:] = [2 ** max_ev] * 3
        meta.min_content_boost[:] = [1] * 3
        meta.gamma[:] = [1] * 3
        meta.offset_sdr[:] = [0] * 3
        meta.offset_hdr[:] = [0] * 3
        meta.hdr_capacity_min, meta.hdr_capacity_max, meta.use_base_cg = 1, capacity, 1
        enc = self.lib.uhdr_create_encoder()
        if not enc:
            raise MemoryError('Cannot allocate encoder')
        try:
            self.check(self.lib.uhdr_enc_set_compressed_image(enc, C.byref(base), 2))
            self.check(self.lib.uhdr_enc_set_gainmap_image(enc, C.byref(gain), C.byref(meta)))
            self.check(self.lib.uhdr_encode(enc))
            output = self.lib.uhdr_get_encoded_stream(enc).contents
            encoded = normalize_iso_metadata(C.string_at(output.data, output.size))
            return add_google_xmp(encoded, meta)
        finally:
            self.lib.uhdr_release_encoder(enc)

    def verify(self, data, original, max_ev, *, hdr_capacity_max=None):
        buf, compressed = self.compressed(data)
        dec = self.lib.uhdr_create_decoder()
        if not dec:
            raise MemoryError('Cannot allocate decoder')
        try:
            self.check(self.lib.uhdr_dec_set_image(dec, C.byref(compressed)))
            self.check(self.lib.uhdr_dec_set_out_img_format(dec, 4))
            self.check(self.lib.uhdr_dec_set_out_color_transfer(dec, 0))
            capacity = 2 ** max_ev if hdr_capacity_max is None else hdr_capacity_max
            self.check(self.lib.uhdr_dec_set_out_max_display_boost(dec, capacity))
            self.check(self.lib.uhdr_dec_probe(dec))
            base = self.lib.uhdr_dec_get_base_image(dec).contents
            base_bytes = C.string_at(base.data, base.size)
            original_image = Image.open(io.BytesIO(original))
            base_image = Image.open(io.BytesIO(base_bytes))
            pixels_equal = np.array_equal(np.asarray(original_image.convert('RGB')), np.asarray(base_image.convert('RGB')))
            # For progressive JPEGs this compares everything from first SOS to EOI.
            def scan(jpeg):
                pos = 2
                while pos < len(jpeg):
                    if jpeg[pos:pos+2] == b'\xff\xda':
                        return jpeg[pos:jpeg.rfind(b'\xff\xd9')+2]
                    if jpeg[pos] != 255:
                        raise ValueError('Malformed JPEG marker')
                    pos += 2 + int.from_bytes(jpeg[pos+2:pos+4], 'big')
                raise ValueError('No JPEG scan')
            entropy_equal = scan(original) == scan(base_bytes)
            meta = self.lib.uhdr_dec_get_gainmap_metadata(dec).contents
            if (not np.isclose(meta.hdr_capacity_max, capacity, rtol=1e-5)
                    or not np.allclose(list(meta.max_content_boost), 2 ** max_ev, rtol=1e-5)):
                raise RuntimeError('Encoded gain range / HDR headroom differ from requested metadata')
            gain_block = self.lib.uhdr_dec_get_gainmap_image(dec).contents
            gain_image = Image.open(io.BytesIO(C.string_at(gain_block.data, gain_block.size)))
            decoded_ev = np.asarray(gain_image.convert('L'), dtype=np.float32) / 255 * np.log2(meta.max_content_boost[0])
            result = dict(base_size=[self.lib.uhdr_dec_get_image_width(dec), self.lib.uhdr_dec_get_image_height(dec)],
                          gainmap_size=[self.lib.uhdr_dec_get_gainmap_width(dec), self.lib.uhdr_dec_get_gainmap_height(dec)],
                          sdr_pixels_identical=pixels_equal, jpeg_scan_identical=entropy_equal,
                          exif_identical=original_image.info.get('exif') == base_image.info.get('exif'),
                          icc_identical=original_image.info.get('icc_profile') == base_image.info.get('icc_profile'),
                          icc_added_to_untagged_srgb=not original_image.info.get('icc_profile') and bool(base_image.info.get('icc_profile')),
                          max_content_boost=list(meta.max_content_boost),
                          hdr_capacity_max=meta.hdr_capacity_max)
            result.update(inspect_google_xmp(data, meta))
            self.check(self.lib.uhdr_decode(dec))
            raw = self.lib.uhdr_get_decoded_image(dec).contents
            decoded = np.frombuffer(C.string_at(raw.planes[0], raw.stride[0] * raw.h * 8), dtype=np.float16)
            decoded = decoded.reshape(raw.h, raw.stride[0], 4)[:, :raw.w, :3]
            result['hdr_decode_finite'] = bool(np.isfinite(decoded).all())
            result['hdr_linear_rgb_max'] = float(decoded.max())
            # Compare the reference codec's reconstructed RGB with our intended
            # luminance-only operation, allowing its JPEG chroma conversion.
            sdr = np.asarray(original_image.convert('RGB'), dtype=np.float32) / 255
            linear = np.where(sdr <= 0.04045, sdr / 12.92, ((sdr + 0.055) / 1.055) ** 2.4)
            expected = linear * np.exp2(decoded_ev)[..., None]
            error = np.abs(decoded.astype(np.float32) - expected)
            result['hdr_rgb_abs_error_p99'] = float(np.percentile(error, 99))
            result['decoded_gain_ev_max'] = float(decoded_ev.max())
            if not pixels_equal or not entropy_equal or not result['exif_identical'] or not result['hdr_decode_finite']:
                raise RuntimeError('SDR preservation / HDR decode validation failed')
            if original_image.info.get('icc_profile') and not result['icc_identical']:
                raise RuntimeError('Original sRGB ICC profile was not preserved')
            return result
        finally:
            self.lib.uhdr_release_decoder(dec)
