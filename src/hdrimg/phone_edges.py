"""Suppress contrast halos around people without repainting their contours."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter
from functools import lru_cache
import hashlib
import os
from pathlib import Path


def person_boundary_protection(person: Image.Image | None) -> Image.Image | None:
    """A feathered band on both sides of the independent person matte.

    Only attenuate added local contrast. The underlying light, shadow and color
    remain intact. Extending outside the matte avoids a bright/dark outline on
    the background when its segmentation is slightly uncertain.
    """
    if person is None:
        return None
    values = np.clip(np.asarray(person, dtype=np.float32), 0, 1)
    if np.ptp(values) < .1:
        return None
    pixels = np.rint(values * 255).astype(np.uint8)
    return _cached_boundary(pixels.tobytes(), pixels.shape).copy()


@lru_cache(maxsize=8)
def _cached_boundary(data: bytes, shape: tuple[int, int]) -> Image.Image:
    cache = os.environ.get('HDRIMG_DERIVED_CACHE')
    path = None
    if cache:
        key = hashlib.sha256(data + str(shape).encode()).hexdigest()
        path = Path(cache) / ('boundary-' + key + '.npy')
        if path.is_file():
            try:
                return Image.fromarray(np.load(path, allow_pickle=False))
            except (ValueError, OSError):
                pass
    mask = Image.fromarray(np.frombuffer(data, np.uint8).reshape(shape))
    radius = max(2, round(min(mask.size) * .025))
    outer = np.asarray(mask.filter(ImageFilter.MaxFilter(2 * radius + 1)), dtype=np.float32)
    inner = np.asarray(mask.filter(ImageFilter.MinFilter(2 * radius + 1)), dtype=np.float32)
    band = Image.fromarray(np.rint(outer - inner).astype(np.uint8))
    feathered = np.asarray(band.filter(ImageFilter.GaussianBlur(max(1, radius * .6))),
                           dtype=np.float32) / 255
    result = np.clip(feathered * 1.4, 0, 1) * np.float32(.95)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f'.{os.getpid()}.partial')
        with temporary.open('wb') as stream:
            np.save(stream, result)
        os.replace(temporary, path)
    return Image.fromarray(result)
