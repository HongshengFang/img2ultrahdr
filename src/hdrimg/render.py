from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import tifffile
from PIL import Image, ImageCms

from .color import (
    adjust_oklab_chroma,
    compress_gamut,
    rec2020_to_linear_srgb,
    srgb_oetf,
)
from .errors import ProcessingError
from .look import resolve_look
from .tone import (
    ExposureStats,
    apply_exposure_and_highlights,
    apply_scene_contrast,
    contrast_curve,
    exposure_statistics,
    hdr_curve_with_highlight_expansion,
    luminance_rec2020,
    max_content_boost,
    scale_rgb_to_luminance,
    sdr_curve,
)


@dataclass(frozen=True)
class SceneInfo:
    width: int
    height: int
    dtype: str
    channels: int
    has_icc_profile: bool


@dataclass(frozen=True)
class RenderInfo:
    scene: SceneInfo
    exposure: dict[str, float]
    look: dict[str, Any]
    peak_nits: float
    hdr_strength: float
    max_content_boost: float
    sdr_quality: int
    gainmap_quality: int
    tone_mapping: dict[str, float]


def _tone_mapping_parameters(
    scene: np.ndarray,
    stats: ExposureStats,
    *,
    highlight_ev: float,
    peak_nits: float,
    contrast: float,
    max_preview_edge: int = 2048,
) -> dict[str, float]:
    height, width = scene.shape[:2]
    step = max(1, int(np.ceil(max(height, width) / max_preview_edge)))
    preview = apply_exposure_and_highlights(
        scene[::step, ::step, :3],
        total_ev=stats.scene_adjustment_ev,
        highlight_ev=highlight_ev,
    )
    y = luminance_rec2020(preview)
    y = y[np.isfinite(y) & (y >= 0.0)]
    if y.size < 16:
        black = 0.0
        high_start = 0.5
        high_anchor = 1.0
    else:
        p1, p95, p995 = (float(value) for value in np.percentile(y, [1.0, 95.0, 99.5]))
        black = min(0.09, 0.5 * p1)
        mapped = contrast_curve(
            np.array([p95, p995], dtype=np.float32),
            black_luminance=black,
            contrast=contrast,
        )
        high_start = max(0.35, float(mapped[0]))
        high_anchor = max(high_start + 0.05, float(mapped[1]))
    peak = float(peak_nits / 203.0)
    target = min(peak, 1.0 + 0.30 * (peak - 1.0))
    anchor_base = peak * -np.expm1(-high_anchor / peak)
    lift = max(1.0, target / max(float(anchor_base), 1e-6))
    return {
        "black_luminance": float(black),
        "contrast": float(contrast),
        "contrast_pivot": 0.18,
        "hdr_highlight_start": float(high_start),
        "hdr_highlight_anchor": float(high_anchor),
        "hdr_anchor_target": float(target),
        "hdr_highlight_lift": float(lift),
    }


def open_scene(path: Path) -> tuple[np.ndarray, SceneInfo]:
    try:
        image = tifffile.memmap(path)
    except (ValueError, OSError):
        image = tifffile.imread(path)
    if image.ndim != 3 or image.shape[-1] not in (3, 4):
        raise ProcessingError(f"Expected RGB TIFF, received shape {image.shape}")
    if image.dtype.kind != "f" or image.dtype.itemsize != 4:
        raise ProcessingError(f"Expected 32-bit float TIFF, received {image.dtype}")
    try:
        with tifffile.TiffFile(path) as tif:
            tags = tif.pages[0].tags
            has_icc = 34675 in tags or "InterColorProfile" in tags
    except (tifffile.TiffFileError, OSError) as exc:
        raise ProcessingError(f"Cannot inspect scene TIFF: {exc}") from exc
    height, width = image.shape[:2]
    return image[..., :3], SceneInfo(
        width=width,
        height=height,
        dtype=str(image.dtype),
        channels=image.shape[-1],
        has_icc_profile=has_icc,
    )


def _srgb_icc_bytes() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _quantize_srgb(linear: np.ndarray) -> np.ndarray:
    encoded = srgb_oetf(linear)
    return np.clip(np.floor(encoded * 255.0 + 0.5), 0, 255).astype(np.uint8)


def render_pair(
    scene_path: Path,
    sdr_path: Path,
    hdr_raw_path: Path,
    *,
    auto_exposure: bool,
    exposure_ev: float | None,
    development_ev: float = 0.0,
    highlight_ev: float,
    hdr_strength: float,
    peak_nits: float,
    sdr_quality: int = 95,
    gainmap_quality: int = 95,
    chunk_rows: int = 512,
    auto_look: bool = True,
    contrast: float | None = None,
    saturation: float | None = None,
    warm_color_separation: float = 0.0,
) -> RenderInfo:
    scene, scene_info = open_scene(scene_path)
    base_stats = exposure_statistics(
        scene,
        auto_exposure=auto_exposure,
        exposure_ev=0.0,
        development_ev=development_ev,
    )
    look = resolve_look(
        scene,
        base_scene_adjustment_ev=base_stats.scene_adjustment_ev,
        enabled=auto_look,
        auto_exposure=auto_exposure,
        exposure_ev=exposure_ev,
        contrast=contrast,
        saturation=saturation,
    )
    stats = exposure_statistics(
        scene,
        auto_exposure=auto_exposure,
        exposure_ev=look.exposure_ev,
        development_ev=development_ev,
    )
    if stats.finite_fraction < 0.999:
        raise ProcessingError(
            f"Scene TIFF has too many invalid pixels ({stats.finite_fraction:.3%} finite)"
        )
    sdr_pixels = np.empty((scene_info.height, scene_info.width, 3), dtype=np.uint8)
    boost = max_content_boost(peak_nits, hdr_strength)
    tone_mapping = _tone_mapping_parameters(
        scene,
        stats,
        highlight_ev=highlight_ev,
        peak_nits=peak_nits,
        contrast=look.contrast,
    )
    tone_mapping["saturation"] = look.saturation
    tone_mapping["warm_color_separation"] = warm_color_separation

    with hdr_raw_path.open("wb") as hdr_file:
        for start in range(0, scene_info.height, chunk_rows):
            stop = min(scene_info.height, start + chunk_rows)
            shared = apply_exposure_and_highlights(
                scene[start:stop],
                total_ev=stats.scene_adjustment_ev,
                highlight_ev=highlight_ev,
            )
            shared = apply_scene_contrast(
                shared,
                black_luminance=tone_mapping["black_luminance"],
                contrast=tone_mapping["contrast"],
                pivot=tone_mapping["contrast_pivot"],
            )
            y = luminance_rec2020(shared)

            sdr_2020 = scale_rgb_to_luminance(shared, sdr_curve(y))
            sdr_linear = rec2020_to_linear_srgb(sdr_2020)
            sdr_linear = adjust_oklab_chroma(
                sdr_linear,
                target="srgb",
                amount=look.saturation,
                warm_color_separation=warm_color_separation,
            )
            sdr_linear = compress_gamut(sdr_linear, target="srgb", upper=1.0)
            sdr_pixels[start:stop] = _quantize_srgb(sdr_linear)

            hdr_2020 = scale_rgb_to_luminance(
                shared,
                hdr_curve_with_highlight_expansion(
                    y,
                    peak_nits=peak_nits,
                    hdr_strength=hdr_strength,
                    highlight_start=tone_mapping["hdr_highlight_start"],
                    highlight_anchor=tone_mapping["hdr_highlight_anchor"],
                    highlight_lift=tone_mapping["hdr_highlight_lift"],
                ),
            )
            hdr_2020 = adjust_oklab_chroma(
                hdr_2020,
                target="rec2020",
                amount=look.saturation,
                warm_color_separation=warm_color_separation,
            )
            hdr_2020 = compress_gamut(hdr_2020, target="rec2020", upper=boost)
            alpha = np.ones((*hdr_2020.shape[:2], 1), dtype=np.float32)
            rgba = np.concatenate((hdr_2020, alpha), axis=-1).astype("<f2")
            hdr_file.write(rgba.tobytes(order="C"))

    Image.fromarray(sdr_pixels, mode="RGB").save(
        sdr_path,
        format="JPEG",
        quality=sdr_quality,
        subsampling=0,
        optimize=True,
        icc_profile=_srgb_icc_bytes(),
    )
    exposure_dict = {key: float(value) for key, value in asdict(stats).items()}
    return RenderInfo(
        scene=scene_info,
        exposure=exposure_dict,
        look=asdict(look),
        peak_nits=float(peak_nits),
        hdr_strength=float(hdr_strength),
        max_content_boost=boost,
        sdr_quality=sdr_quality,
        gainmap_quality=gainmap_quality,
        tone_mapping=tone_mapping,
    )


def write_rgba16f(path: Path, rgb: np.ndarray) -> None:
    values = np.asarray(rgb, dtype=np.float32)
    alpha = np.ones((*values.shape[:2], 1), dtype=np.float32)
    rgba = np.concatenate((values, alpha), axis=-1).astype("<f2")
    path.write_bytes(rgba.tobytes(order="C"))
