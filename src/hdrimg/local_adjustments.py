"""Persisted selections and a bounded, display-linear manual lighting pass."""
from __future__ import annotations

from dataclasses import dataclass, fields
import hashlib
import math
from pathlib import Path
import re

import numpy as np
from PIL import Image

from .errors import InputError, LocalSelectionError

MAX_REGIONS = 8
LOCAL_VERSION = 'local-light-1'


@dataclass(frozen=True)
class LocalAdjustment:
    id: str
    enabled: bool = True
    mode: str = 'soft'
    shape: str = 'circle'
    center_x: float = .5
    center_y: float = .5
    radius_x: float = .2
    radius_y: float = .2
    rotation: float = 0.
    direction: str = 'brighten'
    direction_chosen: bool = False
    amount: float = 0.
    mask_ref: str | None = None

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or set(data) - {f.name for f in fields(cls)}:
            raise InputError('Invalid local adjustment record')
        try:
            result = cls(**data)
        except TypeError as exc:
            raise InputError('Invalid local adjustment record') from exc
        result.validate()
        return result

    def validate(self):
        if not isinstance(self.id, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,64}', self.id):
            raise InputError('Invalid local region ID')
        if type(self.enabled) is not bool or type(self.direction_chosen) is not bool or self.mode not in ('smart', 'soft') or self.shape not in ('circle', 'ellipse') or self.direction not in ('brighten', 'darken'):
            raise InputError('Invalid local region mode')
        for name, low, high in [('center_x', 0, 1), ('center_y', 0, 1),
                                ('radius_x', .00001, 4), ('radius_y', .00001, 4),
                                ('rotation', -180, 180), ('amount', 0, 1)]:
            number = getattr(self, name)
            if isinstance(number, bool) or not isinstance(number, (float, int)) or not math.isfinite(number) or not low <= number <= high:
                raise InputError(f'Invalid local {name}')
        if self.mask_ref is not None and (not isinstance(self.mask_ref, str) or not re.fullmatch(r'[0-9a-f]{64}', self.mask_ref)):
            raise InputError('Invalid selection asset reference')
        if self.mode == 'smart' and self.mask_ref is None:
            raise InputError('Smart region needs a selection asset')

    @property
    def ev(self):
        return .5 * self.amount * (1 if self.direction == 'brighten' else -1) if self.enabled else 0.


def resolve_masks(regions, support: Path, source_sha: str):
    """Check durable assets even when a completed render happens to be cached."""
    result = {}
    for region in regions:
        if region.mode != 'smart':
            continue
        path = support / 'selection-assets' / source_sha / (region.mask_ref + '.png')
        try:
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != region.mask_ref:
                raise ValueError('checksum mismatch')
            with Image.open(path) as image:
                if max(image.size) > 1536 or min(image.size) < 1 or image.mode not in ('I;16', 'I;16B', 'I'):
                    raise ValueError('invalid grayscale mask')
                values = np.asarray(image, np.float32) / 65535
            if not np.isfinite(values).all() or values.min() < 0 or values.max() > 1:
                raise ValueError('invalid mask values')
            result[region.id] = values
        except (OSError, ValueError) as exc:
            if region.ev:
                raise LocalSelectionError(region.id, exc) from exc
    return result


def sample_mask_rows(mask, width, height, start, stop):
    """Pixel-centred bilinear sampling, matching Metal's normalized sampler."""
    mh, mw = mask.shape
    xx = np.clip((np.arange(width, dtype=np.float32) + .5)*mw/width-.5, 0, mw-1)
    yy = np.clip((np.arange(start, stop, dtype=np.float32) + .5)*mh/height-.5, 0, mh-1)
    x0, y0 = xx.astype(np.int32), yy.astype(np.int32)
    fx, fy = xx-x0, yy-y0
    x1, y1 = np.minimum(x0+1, mw-1), np.minimum(y0+1, mh-1)
    top = mask[y0[:, None], x0]*(1-fx) + mask[y0[:, None], x1]*fx
    bottom = mask[y1[:, None], x0]*(1-fx) + mask[y1[:, None], x1]*fx
    return (top*(1-fy[:, None])+bottom*fy[:, None]).astype(np.float32)


def field_rows(regions, masks, width, height, start, stop):
    total = np.zeros((stop-start, width), np.float32)
    xx = (np.arange(width, dtype=np.float32)+.5)[None, :]
    yy = (np.arange(start, stop, dtype=np.float32)+.5)[:, None]
    # A stable order also makes reordering rows bitwise reproducible.
    for region in sorted(regions, key=lambda r: r.id):
        if not region.ev:
            continue
        if region.mode == 'smart':
            mask = sample_mask_rows(masks[region.id], width, height, start, stop)
        else:
            angle = np.float32(region.rotation*math.pi/180)
            c, s = np.cos(angle), np.sin(angle)
            dx, dy = xx-region.center_x*width, yy-region.center_y*height
            radius = np.sqrt(((c*dx+s*dy)/(region.radius_x*width))**2
                             + ((-s*dx+c*dy)/(region.radius_y*height))**2)
            t = np.clip((radius-.35)/.65, 0, 1)
            mask = 1-t*t*(3-2*t)
        total += mask*np.float32(region.ev)
    return np.clip(total, -.5, .5)


def adjust_rgb(rgb, field, upper):
    gain = np.exp2(field)
    headroom = np.clip(np.max(rgb, axis=-1)/upper, 0, 1)
    gain = np.where(field > 0, gain/(1+headroom*(gain-1)), gain)
    return np.where((field != 0)[..., None], rgb*gain[..., None], rgb).astype(np.float32)


def apply_manual_pair(sdr, hdr_path, *, regions, masks, width, height, hdr_strength,
                      gamut, chunk_rows=512, base_output=None):
    """Apply once after the accepted base, before final JPEG/gain-map encoding."""
    from .color import REC2020_TO_DISPLAY_P3, rec2020_to_linear_srgb
    hdr = np.memmap(hdr_path, dtype='<f2', mode='r+', shape=(height, width, 4))
    encoded = sdr.dtype == np.uint8
    destination = np.memmap(Path(hdr_path).parent / 'local-sdr-float32.rgb', dtype='<f4', mode='w+',
                            shape=(height, width, 3)) if encoded else sdr
    maximum = 1.
    base_sdr = None
    if base_output is not None:
        import shutil
        shutil.copyfile(hdr_path, base_output / 'base-hdr.rgba16f')
        base_sdr = (base_output / 'base-sdr.rgba16f').open('wb')
    try:
        for start in range(0, height, chunk_rows):
            stop = min(height, start+chunk_rows)
            if encoded:
                values = sdr[start:stop].astype(np.float32)/255
                a = np.where(values <= .04045, values/12.92, ((values+.055)/1.055)**2.4)
            else:
                a = np.asarray(sdr[start:stop], np.float32)
            b = np.asarray(hdr[start:stop, :, :3], np.float32)
            if base_sdr:
                rgba = np.ones((stop-start, width, 4), np.float32); rgba[..., :3] = a
                base_sdr.write(rgba.astype('<f2').tobytes())
            field = field_rows(regions, masks, width, height, start, stop)
            a = adjust_rgb(a, field, 1.)
            b = adjust_rgb(b, field, 1+(1000/203-1)*hdr_strength)
            destination[start:stop] = a
            hdr[start:stop, :, :3] = b.astype('<f2')
            in_sdr = b @ REC2020_TO_DISPLAY_P3.T if gamut == 'display-p3' else rec2020_to_linear_srgb(b)
            maximum = max(maximum, float(np.max((np.maximum(in_sdr, 0)+1/64)/(a+1/64))))
        hdr.flush()
    finally:
        if base_sdr: base_sdr.close()
        del hdr
    return maximum, destination
