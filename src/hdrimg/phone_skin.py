"""Bound automatic color removal on plausible skin inside a person matte.

This is a color heuristic, not a skin segmentation model. The optional local
Vision matte keeps similarly colored walls outside the protection. All cues
precede SDR exposure and are shared by the SDR and HDR renditions.
"""
from __future__ import annotations

import json
import hashlib
import subprocess
import tempfile
from collections import OrderedDict
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageFilter

from .color import linear_srgb_to_oklab, rec2020_to_linear_srgb, srgb_oetf
from .phone_subject import _vision_helper
from .tone import luminance_rec2020, scale_rgb_to_luminance


_PERSON_CACHE: OrderedDict[tuple, tuple[Image.Image, dict]] = OrderedDict()


def _smooth(value: np.ndarray) -> np.ndarray:
    value = np.clip(value, 0, 1)
    return value * value * (3 - 2 * value)


def skin_confidence(lab: np.ndarray) -> np.ndarray:
    """Soft, brightness-independent warm-skin plausibility, including pale skin."""
    lightness = np.maximum(lab[..., 0], .02)
    relative = np.hypot(lab[..., 1], lab[..., 2]) / lightness
    hue = np.degrees(np.arctan2(lab[..., 2], lab[..., 1])) % 360
    return (
        _smooth((hue - 8) / 17) * (1 - _smooth((hue - 72) / 23))
        * _smooth((relative - .025) / .025)
        * (1 - _smooth((relative - .30) / .15))
        * _smooth((lab[..., 1] / lightness - .008) / .017)
    ).astype(np.float32)


def protect_skin_illuminant(
    rgb: np.ndarray, gains: np.ndarray, person: np.ndarray | float, *, strength: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Apply WB while bounding chroma loss; never add color beyond the source.

    Strong original casts may still lose substantial chroma: the reference is
    capped, rather than preserving an arbitrary orange RAW color unchanged.
    Blending equal-luminance versions keeps tone and HDR headroom unchanged.
    """
    values = np.asarray(rgb, dtype=np.float32)
    y = luminance_rec2020(values)
    corrected = scale_rgb_to_luminance(values * gains, y)
    before = linear_srgb_to_oklab(rec2020_to_linear_srgb(values))
    after = linear_srgb_to_oklab(rec2020_to_linear_srgb(corrected))
    # Warm gray fabric can look skin-colored before WB. Require support on
    # both sides of the correction so neutral clothing can still lose its cast.
    confidence = (skin_confidence(before) * skin_confidence(after)
                  * np.clip(person, 0, 1) * strength)
    before_relative = np.hypot(before[..., 1], before[..., 2]) / np.maximum(before[..., 0], .02)
    after_relative = np.hypot(after[..., 1], after[..., 2]) / np.maximum(after[..., 0], .02)
    floor = .80 * np.minimum(before_relative, .11)
    restore = np.clip(
        (floor - after_relative) / np.maximum(before_relative - after_relative, 1e-6),
        0, .85,
    ) * confidence
    protected = corrected + (values - corrected) * restore[..., None]
    return protected, confidence


def build_skin_context(
    scene: np.ndarray, *, exposure_ev: float,
) -> tuple[Image.Image | None, dict]:
    """Detect people from an independent uncorrected preview, once per render."""
    record = {"method": "person matte with bounded skin chroma loss", "version": 1}
    try:
        helper = _vision_helper()
        if helper is None:
            raise OSError("macOS Vision helper unavailable")
        height, width = scene.shape[:2]
        scale = min(1., 768 / max(width, height))
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        preview = np.stack([
            np.asarray(Image.fromarray(scene[..., c]).resize(size, Image.Resampling.BOX))
            for c in range(3)
        ], axis=-1).astype(np.float32)
        preview = np.maximum(np.nan_to_num(preview, nan=0, posinf=0, neginf=0), 0)
        preview *= np.float32(2.0 ** exposure_ev)
        y = luminance_rec2020(preview)
        preview = scale_rgb_to_luminance(preview, y / (1 + y))
        encoded = np.rint(srgb_oetf(rec2020_to_linear_srgb(preview)) * 255).astype(np.uint8)
        key = (str(helper), size, hashlib.sha256(encoded.tobytes()).hexdigest())
        cached = _PERSON_CACHE.get(key)
        if cached is not None:
            _PERSON_CACHE.move_to_end(key)
            return cached[0].copy(), {**cached[1], "reference_cache_hit": True}
        with tempfile.TemporaryDirectory(prefix="hdrimg-skin-") as temp:
            work = Path(temp)
            reference, prefix = work / "reference.jpg", work / "person"
            Image.fromarray(encoded).save(reference, quality=95, subsampling=0,
                icc_profile=ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
            # Transient Vision failures receive one retry, as subject tone does.
            for attempt in range(2):
                try:
                    subprocess.run([str(helper), str(reference), str(prefix)],
                        check=True, capture_output=True, timeout=60)
                    break
                except subprocess.SubprocessError:
                    if attempt == 1:
                        raise
            observations = json.loads(prefix.with_suffix(".json").read_text())
            with Image.open(str(prefix) + "_person.png") as mask:
                person = np.asarray(mask.convert("L").resize(size, Image.Resampling.BILINEAR)
                    .filter(ImageFilter.GaussianBlur(1)), dtype=np.float32) / 255
        person = _smooth((person - .05) / .85).astype(np.float32)
        image = Image.fromarray(person)
        record = {
            **record, "status": "applied" if np.max(person) > .1 else "no_person",
            "person_fraction": float(np.mean(person)),
            "face_count": len(observations.get("faces", [])),
            "helper_attempts": attempt + 1,
        }
        _PERSON_CACHE[key] = (image.copy(), record.copy())
        while len(_PERSON_CACHE) > 4:
            _PERSON_CACHE.popitem(last=False)
        return image, record
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        # A modest color-only fallback avoids pretending that a person was found.
        return None, {**record, "status": "color_only_fallback", "reason": str(exc)[:400]}
