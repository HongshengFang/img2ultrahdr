"""Bound skin chroma loss after RAW white balance and before the phone look.

The camera reference supplies a capped color reference, not luminance or detail.
It is not a colorimetric ground truth or an exact skin segmentation model.
"""
from pathlib import Path

import numpy as np
from PIL import Image
import tifffile

from .color import linear_srgb_to_oklab, rec2020_to_linear_srgb
from .phone_skin import build_skin_context, skin_confidence
from .phone_tone import phone_scene_decision, resize_base_rows
from .tone import exposure_statistics, luminance_rec2020, scale_rgb_to_luminance


def guard_skin_chroma(
    automatic: np.ndarray, camera: np.ndarray, person: np.ndarray | float, *, strength: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Retain some plausible skin color without restoring a strong camera cast."""
    target = np.asarray(automatic, dtype=np.float32)
    y = luminance_rec2020(target)
    reference = scale_rgb_to_luminance(np.maximum(camera, 0), y)
    before = linear_srgb_to_oklab(rec2020_to_linear_srgb(reference))
    after = linear_srgb_to_oklab(rec2020_to_linear_srgb(target))
    before_c = np.hypot(before[..., 1], before[..., 2]) / np.maximum(before[..., 0], .02)
    after_c = np.hypot(after[..., 1], after[..., 2]) / np.maximum(after[..., 0], .02)
    floor = .80 * np.minimum(before_c, .11)
    restore = np.clip((floor - after_c) / np.maximum(before_c - after_c, 1e-6), 0, .85)
    weight = (restore * skin_confidence(before) * skin_confidence(after)
              * np.clip(person, 0, 1) * strength)
    # Both versions have the automatic development's luminance. Background,
    # neutral clothing and pixels without chroma loss therefore stay unchanged.
    return target + weight[..., None] * (reference - target), weight


def preserve_raw_skin(
    source: Path, camera_reference: Path, destination: Path, *, strength: float = 1.0,
    development_ev: float = -2.0, chunk_rows: int = 256,
) -> tuple[dict, tuple[Image.Image | None, dict] | None]:
    if not np.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError("RAW skin protection strength must be 0..1")
    if not isinstance(chunk_rows, int) or isinstance(chunk_rows, bool) or chunk_rows < 1:
        raise ValueError("RAW skin chunk_rows must be a positive integer")
    if destination.exists():
        raise FileExistsError(destination)
    record = {"method": "bounded camera-reference skin chroma before phone look",
              "version": 1, "strength": strength, "applied": False}
    if strength == 0:
        return {**record, "reason": "disabled"}, None
    scene = tifffile.memmap(source)
    camera = tifffile.memmap(camera_reference)
    if scene.shape != camera.shape:
        raise ValueError("RAW white balance reference geometry mismatch")
    if scene.ndim != 3 or scene.shape[-1] != 3 or scene.dtype != np.float32 or camera.dtype != np.float32:
        raise ValueError("RAW white balance guard requires float32 RGB scenes")
    h, w = scene.shape[:2]
    step = max(1, int(np.ceil(max(h, w) / 768)))
    preview = np.asarray(scene[::step, ::step])
    if not np.all(np.isfinite(preview)):
        raise ValueError("Non-finite RAW white balance preview")
    if phone_scene_decision(preview, development_ev=development_ev, peak_nits=1000).dark_weight >= .5:
        return {**record, "reason": "dark_scene"}, None
    stats = exposure_statistics(preview, auto_exposure=True, exposure_ev=0,
                                development_ev=development_ev)
    context = build_skin_context(scene, exposure_ev=stats.scene_adjustment_ev)
    person_image, detection = context
    record["person_detection"] = detection
    if detection.get("status") == "no_person":
        return {**record, "reason": "no_person"}, context
    # A smooth color-only reference cannot reintroduce camera-reference noise
    # or texture. Full-resolution automatic-WB color still determines support.
    scale = min(1., 768 / max(h, w))
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    reference_images = [Image.fromarray(camera[..., c]).resize(size, Image.Resampling.BOX)
                        .convert("F") for c in range(3)]
    del camera
    with tifffile.TiffFile(source) as tif:
        tag = tif.pages[0].tags.get(34675)
        icc = tag.value if tag else None
    tags = [(34675, 'B', len(icc), icc, False)] if icc else []
    output = tifffile.memmap(destination, shape=scene.shape, dtype='float32',
                            photometric='rgb', extratags=tags)
    weight_sum, affected, maximum = 0., 0, 0.
    for start in range(0, h, chunk_rows):
        stop = min(h, start + chunk_rows)
        target = np.asarray(scene[start:stop])
        reference = np.stack([resize_base_rows(im, width=w, full_height=h, start=start, stop=stop)
                              for im in reference_images], axis=-1)
        person = (resize_base_rows(person_image, width=w, full_height=h, start=start, stop=stop)
                  if person_image is not None else .35)
        guarded, weight = guard_skin_chroma(target, reference, person, strength=strength)
        output[start:stop] = guarded
        weight_sum += float(np.sum(weight, dtype=np.float64))
        affected += int(np.count_nonzero(weight > .01))
        maximum = max(maximum, float(np.max(weight)))
    output.flush()
    del output, scene
    return {**record, "applied": True, "mean_restore_weight": weight_sum / (h * w),
            "affected_fraction": affected / (h * w), "max_restore_weight": maximum}, context
