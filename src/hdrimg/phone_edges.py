"""Suppress contrast halos around people without repainting their contours."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter


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
    mask = Image.fromarray(np.rint(values * 255).astype(np.uint8))
    radius = max(2, round(min(mask.size) * .025))
    outer = np.asarray(mask.filter(ImageFilter.MaxFilter(2 * radius + 1)), dtype=np.float32)
    inner = np.asarray(mask.filter(ImageFilter.MinFilter(2 * radius + 1)), dtype=np.float32)
    band = Image.fromarray(np.rint(outer - inner).astype(np.uint8))
    feathered = np.asarray(band.filter(ImageFilter.GaussianBlur(max(1, radius * .6))),
                           dtype=np.float32) / 255
    return Image.fromarray(np.clip(feathered * 1.4, 0, 1) * np.float32(.95))
