from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
import tempfile
from typing import Any

import numpy as np
import tifffile
from PIL import Image, ImageCms

from .color import (
    DISPLAY_P3_TO_XYZ,
    REC2020_TO_DISPLAY_P3,
    linear_srgb_to_rec2020,
    DISPLAY_P3_TO_SRGB,
    SRGB_TO_XYZ,
    adjust_oklab_chroma,
    compress_gamut,
    rec2020_to_linear_display_p3,
    rec2020_to_linear_srgb,
    refine_phone_color,
    srgb_oetf,
)
from .errors import ProcessingError
from .look import LookDecision, resolve_look
from .phone_subject import detect_subject_fields
from .phone_skin import build_skin_context, protect_skin_illuminant, refine_phone_skin_color
from .phone_edges import person_boundary_protection
from .phone_tone import (
    apply_local_contrast,
    correct_phone_shadow_red,
    guided_log_luminance_base,
    highlight_shoulder,
    lift_midtones,
    phone_hdr_luminance,
    phone_illuminant_gains,
    phone_scene_decision,
    PhoneSceneDecision,
    phone_shadow_red_offset,
    phone_sdr_luminance,
    resize_base_rows,
    restore_display_detail,
    compress_dark_scene_illumination,
)
from .style import DEFAULT_STYLE, StyleSettings
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
class RenderPolicy:
    """Feature decisions are independent from the provenance of slider values."""
    automatic_tone: bool = True
    automatic_color: bool = True
    manual_exposure_bonus: bool = False
    parallel_preview: bool = True
    preview_workers: int = 3

    @classmethod
    def from_legacy_look(cls, look: LookDecision) -> "RenderPolicy":
        return cls(look.contrast_source == "auto", look.saturation_source == "auto",
                   look.exposure_source == "manual", False)


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
    tone_mapping: dict[str, Any]
    style: dict[str, str | float]


def _tone_mapping_parameters(
    scene: np.ndarray,
    stats: ExposureStats,
    *,
    highlight_ev: float,
    peak_nits: float,
    contrast: float,
    style: StyleSettings,
    sdr_exposure_ev: float = 0.0,
    sdr_adaptation_strength: float | None = None,
    hdr_midtone_gain: float | None = None,
    hdr_shoulder_strength: float | None = None,
    manual_exposure_ev: float = 0.0,
    automatic_look: bool = False,
    max_preview_edge: int = 2048,
    scene_decision: PhoneSceneDecision | None = None,
    shadow_ev: float = 0.0,
    white_ev: float = 0.0,
    black_ev: float = 0.0,
) -> dict[str, float]:
    height, width = scene.shape[:2]
    step = max(1, int(np.ceil(max(height, width) / max_preview_edge)))
    preview = apply_exposure_and_highlights(
        scene[::step, ::step, :3],
        total_ev=stats.scene_adjustment_ev,
        highlight_ev=highlight_ev, shadow_ev=shadow_ev, white_ev=white_ev, black_ev=black_ev,
    )
    y = luminance_rec2020(preview)
    y = y[np.isfinite(y) & (y >= 0.0)]
    if y.size < 16:
        black = 0.0
        low, center, middle_high = 0.05, 0.18, 0.65
        shoulder_start, shoulder_span = 0.5, 0.5
        high_start, high_anchor = 0.5, 1.0
    else:
        p1, p95, p995 = (float(value) for value in np.percentile(y, [1.0, 95.0, 99.5]))
        black = min(0.09, 0.5 * p1)
        contrasted = contrast_curve(
            y,
            black_luminance=black,
            contrast=contrast,
        )
        low, center, middle_high = (
            float(value) for value in np.percentile(contrasted, [10.0, 50.0, 95.0])
        )
        mapped = lift_midtones(
            contrasted,
            low=low,
            center=center,
            high=middle_high,
            ev=style.midtone_lift_ev,
        )
        p90, p95_mapped, p995_mapped = (
            float(value) for value in np.percentile(mapped, [90.0, 95.0, 99.5])
        )
        shoulder_start = p90
        shoulder_span = max(p995_mapped - p90, 0.1)
        high_start = max(0.35, p95_mapped)
        high_anchor = max(high_start + 0.05, p995_mapped)
    peak = float(peak_nits / 203.0)
    target = min(peak, 1.0 + 0.30 * (peak - 1.0))
    anchor_base = peak * -np.expm1(-high_anchor / peak)
    lift = max(1.0, target / max(float(anchor_base), 1e-6))
    shoulder_strength = style.highlight_rolloff
    if shoulder_strength > 0.0:
        # Phone SDR bases keep bright surfaces below display white, even in
        # scenes with a few extremely luminous lights. The control adjusts
        # the asymptotic scene value; zero disables the shoulder entirely.
        ceiling = 3.0 - 2.0 * shoulder_strength
        shoulder_start = min(shoulder_start, 0.75 * ceiling)
        shoulder_span = max(p995_mapped - shoulder_start, 0.1) if y.size >= 16 else 0.1
        shoulder_strength = shoulder_span / max(ceiling - shoulder_start, 0.1)
    result = {
        "black_luminance": float(black),
        "contrast": float(contrast),
        "contrast_pivot": 0.18,
        "hdr_highlight_start": float(high_start),
        "hdr_highlight_anchor": float(high_anchor),
        "hdr_anchor_target": float(target),
        "hdr_highlight_lift": float(lift),
        "midtone_low": float(low),
        "midtone_center": float(center),
        "midtone_high": float(middle_high),
        "sdr_shoulder_start": float(shoulder_start),
        "sdr_shoulder_span": float(shoulder_span),
        "sdr_shoulder_strength": float(shoulder_strength),
    }
    if style.algorithm_version >= 2:
        decision = scene_decision or phone_scene_decision(
            scene[::step, ::step, :3],
            development_ev=stats.development_ev,
            peak_nits=peak_nits,
            refine_diffuse=style.algorithm_version >= 4,
        )
        adaptation = 1.0 if sdr_adaptation_strength is None else sdr_adaptation_strength
        resolved_sdr_ev = sdr_exposure_ev + adaptation * decision.sdr_auto_ev
        resolved_mid_gain = decision.hdr_midtone_gain if hdr_midtone_gain is None else hdr_midtone_gain
        if style.algorithm_version >= 4 and hdr_midtone_gain is None:
            resolved_mid_gain = min(resolved_mid_gain, decision.hdr_peak_ratio)
        resolved_shoulder = 0.70 if hdr_shoulder_strength is None else hdr_shoulder_strength
        # Indoor shadows need a stronger toe. A broad extra contrast curve
        # worsened several outdoor references, so leave those at baseline.
        sdr_contrast = 0.35 * decision.indoor_weight if automatic_look else 0.0
        sdr_pivot = 0.32 - 0.10 * decision.indoor_weight
        shadow_lift = decision.high_key_weight if automatic_look else 0.0
        dark_shoulder = 2.5 * decision.dark_weight if automatic_look else 0.0
        clear_daylight = 0.0
        compress_illumination = compress_dark_scene_illumination
        display_curve = sdr_curve
        if style.clear_float and automatic_look:
            from .phone_clear import daylit_dark_weight, compress_clear_illumination, clear_display_curve
            clear_daylight = daylit_dark_weight(decision.dark_weight, decision.dark_fraction)
            dark_shoulder *= 1-clear_daylight
            compress_illumination = compress_clear_illumination
            display_curve = lambda values: clear_display_curve(values, clear_daylight)
            result['phone_clear_daylit_dark_weight'] = clear_daylight
        restore = float(np.clip((decision.dark_fraction - 0.08) / 0.14, 0.0, 1.0))
        restore = restore * restore * (3.0 - 2.0 * restore)
        hdr_dark_restore = decision.dark_weight * restore if automatic_look else 0.0
        source_anchor = float(np.percentile(mapped, decision.hdr_anchor_percentile)) if y.size >= 16 else 1.0
        preview_input = mapped * np.float32(2.0**(adaptation * decision.sdr_auto_ev)) if y.size >= 16 else np.array([.5])
        if style.algorithm_version >= 4 and automatic_look and y.size >= 16:
            spatial_mapped = lift_midtones(
                contrast_curve(luminance_rec2020(preview), black_luminance=black, contrast=contrast),
                low=low, center=center, high=middle_high, ev=style.midtone_lift_ev,
            )
            spatial_base = (np.log2(np.maximum(spatial_mapped, 1e-4)) if style.clear_float else
                            guided_log_luminance_base(spatial_mapped, radius_fraction=.08, epsilon=1.0))
            valid_spatial = np.isfinite(spatial_mapped) & (spatial_mapped >= 0)
            preview_input = compress_illumination(
                spatial_mapped[valid_spatial], base_log=spatial_base[valid_spatial],
                exposure_ev=adaptation*decision.sdr_auto_ev,
                dark_weight=decision.dark_weight, dark_fraction=decision.dark_fraction,
                highlight_ratio=decision.raw_p99/max(decision.raw_p90, .01), high_key_weight=decision.high_key_weight,
            )
        preview_sdr = display_curve(highlight_shoulder(
            preview_input,
            start=result["sdr_shoulder_start"],
            span=result["sdr_shoulder_span"],
            strength=result["sdr_shoulder_strength"],
        )) if y.size >= 16 else np.array([0.5], dtype=np.float32)
        preview_sdr = phone_sdr_luminance(
            preview_sdr, contrast=sdr_contrast, pivot=sdr_pivot,
            shadow_lift=shadow_lift,
            source=mapped if y.size >= 16 else None,
            dark_shoulder=dark_shoulder,
            source_start=p90 if y.size >= 16 else 0.0,
            source_anchor=source_anchor,
        )
        result.update({
            **{f"phone_{key}": value for key, value in decision.as_record().items()},
            "sdr_adaptation_strength": float(adaptation),
            "sdr_effective_exposure_ev": float(resolved_sdr_ev),
            "phone_sdr_contrast": float(sdr_contrast),
            "phone_sdr_pivot": float(sdr_pivot),
            "phone_sdr_shadow_lift": float(shadow_lift),
            "phone_sdr_dark_shoulder": float(dark_shoulder),
            "phone_hdr_dark_restore": float(hdr_dark_restore),
            "hdr_midtone_gain": float(resolved_mid_gain),
            "hdr_shoulder_strength": float(resolved_shoulder),
            "hdr_sdr_highlight_anchor": float(max(
                np.percentile(preview_sdr, decision.hdr_anchor_percentile), 0.50
            )),
            "hdr_source_highlight_start": float(p90 if y.size >= 16 else 0.5),
            "hdr_source_highlight_anchor": source_anchor,
            "hdr_manual_highlight_bonus": float(min(max(manual_exposure_ev, 0.0) * 2.0, 2.0)),
            "hdr_manual_highlight_start": float(p95_mapped if y.size >= 16 else 0.7),
            "hdr_manual_highlight_anchor": float(
                np.percentile(mapped, 99.9) if y.size >= 16 else 1.0
            ),
        })
    return result


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


def _sdr_icc_bytes(gamut: str) -> bytes:
    if gamut == "srgb":
        return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    profile = Path("/System/Library/ColorSync/Profiles/Display P3.icc")
    try:
        return ImageCms.ImageCmsProfile(str(profile)).tobytes()
    except (OSError, ValueError) as exc:
        raise ProcessingError(f"Display P3 ICC profile is unavailable: {profile}") from exc


def _quantize_sdr(linear: np.ndarray) -> np.ndarray:
    # Display P3 uses the same transfer curve as sRGB.
    encoded = srgb_oetf(linear)
    return np.clip(np.floor(encoded * 255.0 + 0.5), 0, 255).astype(np.uint8)


def _render_pair(
    scene_path: Path,
    sdr_path: Path,
    hdr_raw_path: Path,
    *,
    auto_exposure: bool,
    exposure_ev: float | None,
    sdr_exposure_ev: float = 0.0,
    development_ev: float = 0.0,
    highlight_ev: float,
    hdr_strength: float,
    peak_nits: float,
    sdr_quality: int | None = None,
    gainmap_quality: int | None = None,
    chunk_rows: int = 512,
    auto_look: bool = True,
    contrast: float | None = None,
    saturation: float | None = None,
    warm_color_separation: float = 0.0,
    style: StyleSettings | None = None,
    sdr_adaptation_strength: float | None = None,
    hdr_midtone_gain: float | None = None,
    hdr_shoulder_strength: float | None = None,
    subject_adaptation_strength: float | None = None,
    skin_protection_strength: float | None = None,
    _allow_subject: bool = True,
    _allow_histogram: bool = True,
    _skin_context: tuple[Image.Image | None, dict] | None = None,
    _scene_decision: PhoneSceneDecision | None = None,
    _linear_sdr_output: Path | None = None,
    _preview_output: Path | None = None,
    _skip_sdr_jpeg: bool = False,
    _reference_sdr_only: bool = False,
    _float_work: Path | None = None,
    edit_exposure_ev: float = 0.0,
    shadow_ev: float = 0.0,
    white_ev: float = 0.0,
    black_ev: float = 0.0,
    saturation_scale: float = 1.0,
    _analysis: dict | None = None,
    _processing_policy: RenderPolicy | None = None,
) -> RenderInfo | None:
    if _reference_sdr_only and (_allow_subject or _allow_histogram or _linear_sdr_output is None):
        raise ValueError('SDR-only rendering is restricted to independent analysis references')
    style = style or DEFAULT_STYLE
    # Two-code JPEG reversals appeared on soft monotone edges at quality 95.
    # Clear's final base uses 98; explicit internal overrides and other styles
    # retain their previous behavior. Detection previews keep quality 95 below.
    if sdr_quality is None:
        sdr_quality = 98 if style.clear_float else 95
    if gainmap_quality is None:
        gainmap_quality = 98 if style.clear_v8 else 95
    refine_color = refine_phone_skin_color if style.pale_skin else refine_phone_color
    if style.clear_float:
        from .phone_clear import refine_clear_color
        refine_color = refine_clear_color
    scene, scene_info = open_scene(scene_path)
    if _analysis is None:
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
    else:
        base_stats = ExposureStats(**_analysis["base_stats"])
        look = LookDecision(**_analysis["look"])
    if edit_exposure_ev or saturation_scale != 1.0:
        look = replace(look, exposure_ev=look.exposure_ev + edit_exposure_ev,
                       saturation=look.saturation * saturation_scale)
    policy = _processing_policy or RenderPolicy.from_legacy_look(look)
    if style.clear_float and policy.automatic_tone:
        from .phone_clear import scene_decision as clear_scene_decision
        if style.clear_v8:
            from .phone_clear_v8 import scene_decision as clear_scene_decision
        from .phone_tone import _smoothstep
        if _scene_decision is None:
            step = max(1, int(np.ceil(max(scene.shape[:2])/1024)))
            _scene_decision = clear_scene_decision(scene[::step,::step],
                development_ev=development_ev, peak_nits=peak_nits)
        dynamic = np.log2(max(_scene_decision.raw_p90,1e-5)/max(_scene_decision.raw_p10,1e-5))
        weight = float(_smoothstep((dynamic-3)/2))*(1-_scene_decision.dark_weight)*(1-_scene_decision.indoor_weight)
        resolved = max(1.1, look.contrast-.20*weight)
        look = replace(look, contrast=resolved,
            metrics={**look.metrics, 'clear_high_dynamic_range_weight':weight},
            suggested={**look.suggested, 'contrast':resolved})
    total_ev = base_stats.auto_ev + look.exposure_ev
    stats = replace(
        base_stats, total_ev=total_ev,
        scene_adjustment_ev=total_ev - base_stats.development_ev)
    if stats.finite_fraction < 0.999:
        raise ProcessingError(
            f"Scene TIFF has too many invalid pixels ({stats.finite_fraction:.3%} finite)"
        )
    final_hdr_path = hdr_raw_path
    if style.clear_float:
        hdr_raw_path = _float_work / "hdr-float32.rgba"
        sdr_pixels = np.memmap(_float_work / "sdr-float32.rgb", mode="w+", dtype="<f4",
                              shape=(scene_info.height, scene_info.width, 3))
    else:
        sdr_pixels = np.empty((scene_info.height, scene_info.width, 3), dtype=np.uint8)
    boost = max_content_boost(peak_nits, hdr_strength)
    analysis_scene = np.load(_analysis["sample"], mmap_mode="r") if _analysis else scene
    tone_mapping = _tone_mapping_parameters(
        analysis_scene,
        stats,
        highlight_ev=highlight_ev, shadow_ev=shadow_ev, white_ev=white_ev, black_ev=black_ev,
        peak_nits=peak_nits,
        contrast=look.contrast,
        style=style,
        sdr_exposure_ev=sdr_exposure_ev,
        sdr_adaptation_strength=sdr_adaptation_strength,
        hdr_midtone_gain=hdr_midtone_gain,
        hdr_shoulder_strength=hdr_shoulder_strength,
        manual_exposure_ev=look.exposure_ev if policy.manual_exposure_bonus else 0.0,
        automatic_look=policy.automatic_tone,
        scene_decision=_scene_decision,
    )
    tone_mapping["saturation"] = look.saturation
    tone_mapping["warm_color_separation"] = warm_color_separation
    tone_mapping["sdr_exposure_ev"] = float(sdr_exposure_ev)
    effective_sdr_ev = tone_mapping.get("sdr_effective_exposure_ev", sdr_exposure_ev)
    compress_illumination = compress_dark_scene_illumination
    display_curve = sdr_curve
    if style.clear_float and policy.automatic_tone:
        from .phone_clear import compress_clear_illumination, clear_display_curve
        compress_illumination = compress_clear_illumination
        display_curve = lambda values: clear_display_curve(
            values, tone_mapping['phone_clear_daylit_dark_weight'])
    neutral_protection = style.algorithm_version >= 4 and policy.automatic_color
    if style.algorithm_version >= 4:
        tone_mapping["phone_neutral_protection"] = float(neutral_protection)
    measured_gain_max = 1.0
    illuminant_gains = np.ones(3, dtype=np.float32)
    shadow_red_offset = 0.0
    if (4 <= style.algorithm_version < 6 and policy.automatic_color
            and policy.automatic_tone):
        color_step = max(1, int(np.ceil(max(scene_info.height, scene_info.width) / 1024)))
        shadow_red_offset = phone_shadow_red_offset(
            np.asarray(scene[::color_step, ::color_step], dtype=np.float32)
            * np.float32(2.0**-stats.development_ev),
            dark_weight=tone_mapping["phone_dark_weight"],
            dark_fraction=tone_mapping["phone_dark_fraction"],
        )
        tone_mapping["phone_shadow_red_offset"] = shadow_red_offset
    if 4 <= style.algorithm_version < 6 and policy.automatic_color:
        color_step = max(1, int(np.ceil(max(scene_info.height, scene_info.width) / 1024)))
        illuminant_gains, illuminant_strength = phone_illuminant_gains(
            np.asarray(scene[::color_step, ::color_step], dtype=np.float32)
            * np.float32(2.0 ** -stats.development_ev),
            dark_weight=tone_mapping["phone_dark_weight"],
            dark_fraction=tone_mapping["phone_dark_fraction"], daylight_neutrals=True,
            indoor_weight=tone_mapping["phone_indoor_weight"],
        )
        tone_mapping.update({
            "phone_illuminant_strength": illuminant_strength,
            **{f"phone_illuminant_{channel}_gain": float(gain)
               for channel, gain in zip(("red", "green", "blue"), illuminant_gains)},
        })
    if style.algorithm_version >= 6:
        # V6 receives a white-balanced RAW scene, including explicit camera or
        # custom choices. Never estimate another global illuminant here.
        tone_mapping.update({
            "phone_white_balance_stage": "raw",
            "phone_shadow_red_offset": 0.0,
            "phone_illuminant_strength": 0.0,
            **{f"phone_illuminant_{channel}_gain": 1.0
               for channel in ("red", "green", "blue")},
        })
    shared_hdr_chroma = (style.algorithm_version >= 4 and policy.automatic_tone
                         and policy.automatic_color)
    skin_strength = 1.0 if skin_protection_strength is None else skin_protection_strength
    skin_enabled = (style.algorithm_version >= 5 and shared_hdr_chroma
                    and skin_strength > 0 and tone_mapping["phone_dark_weight"] < .5)
    edge_enabled = (style.algorithm_version >= 6 and shared_hdr_chroma
                    and style.local_contrast > 0 and tone_mapping["phone_dark_weight"] < .5)
    edge_image = None
    if skin_enabled or edge_enabled:
        if _skin_context is None:
            _skin_context = build_skin_context(scene, exposure_ev=stats.scene_adjustment_ev)
    if edge_enabled:
        edge_image = person_boundary_protection(_skin_context[0])
    if style.algorithm_version >= 6:
        tone_mapping["phone_edge_protection"] = {
            "method": "feathered person boundary local contrast attenuation", "version": 1,
            "status": "applied" if edge_image is not None else "inactive",
            "mean_weight": float(np.mean(edge_image)) if edge_image is not None else 0.0,
        }
    if skin_enabled:
        tone_mapping["phone_skin"] = {**_skin_context[1], "strength": float(skin_strength)}
        if style.pale_skin:
            tone_mapping["phone_skin"]["pale_color_protection"] = {
                "version": 2 if style.pale_boundaries else 1,
                "preserves_luminance": True,
                ("outside_person_support_unchanged" if style.pale_boundaries
                 else "outside_skin_support_unchanged"): True,
            }
    elif style.algorithm_version >= 5:
        tone_mapping["phone_skin"] = {"status": "disabled", "strength": 0.0}
    base_image: Image.Image | None = None
    detail_base_image: Image.Image | None = None
    illumination_image: Image.Image | None = None
    detail_strength = (
        0.90 * style.local_contrast / 0.22
        if style.algorithm_version >= 4 and policy.automatic_tone else 0.0
    )
    if style.clear_float:
        detail_strength = 0.0
    if style.algorithm_version >= 4:
        tone_mapping["phone_display_detail_strength"] = float(detail_strength)
    if style.local_contrast > 0.0 and not style.clear_float:
        step = max(1, int(np.ceil(max(scene_info.height, scene_info.width) / 1024)))
        preview = apply_exposure_and_highlights(
            scene[::step, ::step],
            total_ev=stats.scene_adjustment_ev,
            highlight_ev=highlight_ev, shadow_ev=shadow_ev, white_ev=white_ev, black_ev=black_ev,
        )
        preview = apply_scene_contrast(
            preview,
            black_luminance=tone_mapping["black_luminance"],
            contrast=look.contrast,
        )
        preview_y = luminance_rec2020(preview)
        preview_y = lift_midtones(
            preview_y,
            low=tone_mapping["midtone_low"],
            center=tone_mapping["midtone_center"],
            high=tone_mapping["midtone_high"],
            ev=style.midtone_lift_ev,
        )
        base_image = Image.fromarray(guided_log_luminance_base(preview_y), mode="F")
        if style.algorithm_version >= 4 and policy.automatic_tone:
            illumination_image = Image.fromarray(guided_log_luminance_base(
                preview_y, radius_fraction=.08, epsilon=1.0), mode="F")
        if detail_strength > 0.0:
            detail_base_image = Image.fromarray(guided_log_luminance_base(
                preview_y, radius_fraction=0.06, epsilon=0.60
            ), mode="F")

    skin_weight_sum = 0.0
    skin_supported_pixels = 0
    def render_chunk(start):
        measured_gain_max = 1.0
        skin_weight_sum = 0.0
        skin_supported_pixels = 0
        stop = min(scene_info.height, start + chunk_rows)
        shared = apply_exposure_and_highlights(
            correct_phone_shadow_red(scene[start:stop], offset=shadow_red_offset,
                                     development_ev=stats.development_ev),
            total_ev=stats.scene_adjustment_ev,
            highlight_ev=highlight_ev, shadow_ev=shadow_ev, white_ev=white_ev, black_ev=black_ev,
        )
        shared = apply_scene_contrast(
            shared,
            black_luminance=tone_mapping["black_luminance"],
            contrast=tone_mapping["contrast"],
            pivot=tone_mapping["contrast_pivot"],
        )
        y = luminance_rec2020(shared)
        edge_rows = (resize_base_rows(edge_image, width=scene_info.width,
            full_height=scene_info.height, start=start, stop=stop)
            if edge_image is not None else None)
        skin_rows = None
        if skin_enabled:
            person_image = _skin_context[0]
            person_rows = (resize_base_rows(person_image, width=scene_info.width,
                full_height=scene_info.height, start=start, stop=stop)
                if person_image is not None else .35)
            shared, skin_rows = protect_skin_illuminant(
                shared, illuminant_gains, person_rows, strength=skin_strength)
            skin_weight_sum += float(np.sum(skin_rows, dtype=np.float64))
            skin_supported_pixels += int(np.count_nonzero(skin_rows > .25))
        elif not np.array_equal(illuminant_gains, np.ones(3, dtype=np.float32)):
            shared = scale_rgb_to_luminance(shared * illuminant_gains, y)
        color_protection = {"skin_protection": skin_rows}
        if style.pale_boundaries:
            # Retain the RAW color of faint warm pixels on a detected
            # person, even where chroma-based skin confidence is zero.
            # No detector means no extra person-wide protection.
            color_protection["person_protection"] = (
                person_rows * skin_strength
                if skin_enabled and person_image is not None else None
            )
        if style.midtone_lift_ev:
            mapped = lift_midtones(
                y,
                low=tone_mapping["midtone_low"],
                center=tone_mapping["midtone_center"],
                high=tone_mapping["midtone_high"],
                ev=style.midtone_lift_ev,
            )
            shared = scale_rgb_to_luminance(shared, mapped)
            y = mapped
        if base_image is not None:
            base_rows = resize_base_rows(
                base_image,
                width=scene_info.width,
                full_height=scene_info.height,
                start=start,
                stop=stop,
            )
            mapped = apply_local_contrast(
                y, base_log=base_rows, strength=style.local_contrast, protection=edge_rows
            )
            shared = scale_rgb_to_luminance(shared, mapped)
            y = mapped

        sdr_input = y * np.float32(2.0**effective_sdr_ev)
        illumination_rows = None
        if illumination_image is not None or (style.clear_float and policy.automatic_tone):
            illumination_rows = (np.log2(np.maximum(y, 1e-4)) if style.clear_float else
                resize_base_rows(illumination_image, width=scene_info.width,
                                full_height=scene_info.height, start=start, stop=stop))
            sdr_input = compress_illumination(
                y, base_log=illumination_rows, exposure_ev=effective_sdr_ev,
                dark_weight=tone_mapping["phone_dark_weight"],
                dark_fraction=tone_mapping["phone_dark_fraction"],
                highlight_ratio=tone_mapping["phone_raw_p99"]/max(tone_mapping["phone_raw_p90"], .01),
                high_key_weight=tone_mapping["phone_high_key_weight"],
            )
        sdr_y = highlight_shoulder(
            sdr_input,
            start=tone_mapping["sdr_shoulder_start"],
            span=tone_mapping["sdr_shoulder_span"],
            strength=tone_mapping["sdr_shoulder_strength"],
        )
        sdr_target_before_style = display_curve(sdr_y)
        sdr_target = sdr_target_before_style
        if style.algorithm_version >= 2:
            sdr_target = phone_sdr_luminance(
                sdr_target,
                contrast=tone_mapping["phone_sdr_contrast"],
                pivot=tone_mapping["phone_sdr_pivot"],
                shadow_lift=tone_mapping["phone_sdr_shadow_lift"],
                source=y,
                dark_shoulder=tone_mapping["phone_sdr_dark_shoulder"],
                source_start=tone_mapping["hdr_source_highlight_start"],
                source_anchor=tone_mapping["hdr_source_highlight_anchor"],
            )
        detail_rows = None
        if detail_base_image is not None:
            detail_rows = resize_base_rows(
                detail_base_image, width=scene_info.width,
                full_height=scene_info.height, start=start, stop=stop,
            )
            sdr_target = restore_display_detail(
                sdr_target, source_y=y, base_log=detail_rows, strength=detail_strength,
                protection=edge_rows,
            )
        sdr_2020 = scale_rgb_to_luminance(shared, sdr_target)
        if style.sdr_gamut == "display-p3":
            sdr_linear = rec2020_to_linear_display_p3(sdr_2020)
        else:
            sdr_linear = rec2020_to_linear_srgb(sdr_2020)
        sdr_linear = adjust_oklab_chroma(
            sdr_linear,
            target=style.sdr_gamut,
            amount=look.saturation,
            warm_color_separation=warm_color_separation,
            vibrance=style.vibrance,
        )
        if style.algorithm_version >= 2:
            sdr_linear = refine_color(
                sdr_linear, target=style.sdr_gamut,
                dark_weight=tone_mapping["phone_dark_weight"],
                indoor_weight=tone_mapping["phone_indoor_weight"],
                neutral_protection=neutral_protection,
                **color_protection,
            )
        sdr_linear = compress_gamut(sdr_linear, target=style.sdr_gamut, upper=1.0)
        sdr_pixels[start:stop] = sdr_linear if style.clear_float else _quantize_sdr(sdr_linear)

        if _reference_sdr_only:
            return b'', 1.0, skin_weight_sum, skin_supported_pixels

        if style.algorithm_version >= 2:
            sdr_output_y = sdr_linear @ (
                DISPLAY_P3_TO_XYZ[1] if style.sdr_gamut == "display-p3" else SRGB_TO_XYZ[1]
            )
            if sdr_exposure_ev == 0.0:
                hdr_reference_y = sdr_output_y
                hdr_reference_color = sdr_linear
                reference_restore_ratio = sdr_target_before_style / np.maximum(sdr_target, 1e-5)
            else:
                # Keep the existing --sdr-exposure-ev contract: it alters
                # the fallback and gain map, not the desired HDR image.
                reference_input = y * np.float32(2.0 ** (effective_sdr_ev - sdr_exposure_ev))
                if illumination_rows is not None:
                    reference_input = compress_illumination(
                        y, base_log=illumination_rows, exposure_ev=effective_sdr_ev-sdr_exposure_ev,
                        dark_weight=tone_mapping["phone_dark_weight"],
                        dark_fraction=tone_mapping["phone_dark_fraction"],
                        highlight_ratio=tone_mapping["phone_raw_p99"]/max(tone_mapping["phone_raw_p90"], .01),
                        high_key_weight=tone_mapping["phone_high_key_weight"],
                    )
                reference_y = highlight_shoulder(
                    reference_input,
                    start=tone_mapping["sdr_shoulder_start"],
                    span=tone_mapping["sdr_shoulder_span"],
                    strength=tone_mapping["sdr_shoulder_strength"],
                )
                reference_target_before_style = display_curve(reference_y)
                reference_target = phone_sdr_luminance(
                    reference_target_before_style,
                    contrast=tone_mapping["phone_sdr_contrast"],
                    pivot=tone_mapping["phone_sdr_pivot"],
                    shadow_lift=tone_mapping["phone_sdr_shadow_lift"],
                    source=y,
                    dark_shoulder=tone_mapping["phone_sdr_dark_shoulder"],
                    source_start=tone_mapping["hdr_source_highlight_start"],
                    source_anchor=tone_mapping["hdr_source_highlight_anchor"],
                )
                if detail_rows is not None:
                    reference_target = restore_display_detail(
                        reference_target, source_y=y, base_log=detail_rows,
                        strength=detail_strength, protection=edge_rows,
                    )
                reference_2020 = scale_rgb_to_luminance(shared, reference_target)
                if style.sdr_gamut == "display-p3":
                    reference_linear = rec2020_to_linear_display_p3(reference_2020)
                else:
                    reference_linear = rec2020_to_linear_srgb(reference_2020)
                reference_linear = adjust_oklab_chroma(
                    reference_linear, target=style.sdr_gamut,
                    amount=look.saturation,
                    warm_color_separation=warm_color_separation,
                    vibrance=style.vibrance,
                )
                reference_linear = refine_color(
                    reference_linear, target=style.sdr_gamut,
                    dark_weight=tone_mapping["phone_dark_weight"],
                    indoor_weight=tone_mapping["phone_indoor_weight"],
                    neutral_protection=neutral_protection,
                    **color_protection,
                )
                reference_linear = compress_gamut(reference_linear, target=style.sdr_gamut, upper=1.0)
                hdr_reference_color = reference_linear
                hdr_reference_y = reference_linear @ (
                    DISPLAY_P3_TO_XYZ[1] if style.sdr_gamut == "display-p3" else SRGB_TO_XYZ[1]
                )
                reference_restore_ratio = reference_target_before_style / np.maximum(reference_target, 1e-5)
            if tone_mapping["phone_hdr_dark_restore"] > 0.0:
                hdr_reference_y *= np.power(
                    np.maximum(reference_restore_ratio, 1.0),
                    tone_mapping["phone_hdr_dark_restore"],
                )
            hdr_y = phone_hdr_luminance(
                hdr_reference_y,
                source_y=y,
                source_highlight_start=tone_mapping["hdr_source_highlight_start"],
                source_highlight_anchor=tone_mapping["hdr_source_highlight_anchor"],
                dark_weight=tone_mapping["phone_dark_weight"],
                dark_highlight_power=tone_mapping["phone_hdr_dark_highlight_power"],
                manual_highlight_bonus=tone_mapping["hdr_manual_highlight_bonus"],
                manual_highlight_start=tone_mapping["hdr_manual_highlight_start"],
                manual_highlight_anchor=tone_mapping["hdr_manual_highlight_anchor"],
                midtone_gain=tone_mapping["hdr_midtone_gain"],
                peak_target=tone_mapping["phone_hdr_peak_ratio"],
                peak_limit=peak_nits / 203.0,
                highlight_anchor=tone_mapping["hdr_sdr_highlight_anchor"],
                shoulder_strength=tone_mapping["hdr_shoulder_strength"],
                hdr_strength=hdr_strength,
                smooth_highlights=style.algorithm_version >= 4,
                dark_intermediate_lift=(0.12 * tone_mapping["phone_dark_weight"]
                    * np.clip((tone_mapping["phone_raw_p99"] / max(tone_mapping["phone_raw_p90"], .01)-4)/2, 0, 1)
                    * (1-tone_mapping["phone_hdr_dark_restore"])
                    if style.algorithm_version >= 4 and policy.automatic_tone else 0),
            )
            if shared_hdr_chroma:
                reference_srgb = (hdr_reference_color @ DISPLAY_P3_TO_SRGB.T
                                  if style.sdr_gamut == "display-p3" else hdr_reference_color)
                hdr_2020 = scale_rgb_to_luminance(linear_srgb_to_rec2020(reference_srgb), hdr_y)
            else:
                hdr_2020 = scale_rgb_to_luminance(shared, hdr_y)
        else:
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
        if not shared_hdr_chroma:
            hdr_2020 = adjust_oklab_chroma(
                hdr_2020,
                target="rec2020",
                amount=look.saturation,
                warm_color_separation=warm_color_separation,
                vibrance=style.vibrance,
            )
            if style.algorithm_version >= 2:
                hdr_2020 = refine_color(
                    hdr_2020, target="rec2020",
                    dark_weight=tone_mapping["phone_dark_weight"],
                    indoor_weight=tone_mapping["phone_indoor_weight"],
                    neutral_protection=neutral_protection,
                    **color_protection,
                )
        hdr_2020 = compress_gamut(
            hdr_2020, target="rec2020",
            upper=peak_nits / 203.0 if style.algorithm_version >= 2 else boost,
        )
        if style.algorithm_version >= 2:
            # Gain-map metadata is channel-wise for the selected encoder
            # mode; a luminance-only maximum can clip saturated colors.
            hdr_p3 = hdr_2020 @ REC2020_TO_DISPLAY_P3.T if style.sdr_gamut == "display-p3" else rec2020_to_linear_srgb(hdr_2020)
            ratio = (np.maximum(hdr_p3, 0.0) + 1.0 / 64.0) / (sdr_linear + 1.0 / 64.0)
            measured_gain_max = max(measured_gain_max, float(np.max(ratio)))
        alpha = np.ones((*hdr_2020.shape[:2], 1), dtype=np.float32)
        rgba = np.concatenate((hdr_2020, alpha), axis=-1).astype("<f4" if style.clear_float else "<f2")
        return rgba.tobytes(order="C"), measured_gain_max, skin_weight_sum, skin_supported_pixels

    starts = range(0, scene_info.height, chunk_rows)
    # Only preview-sized images use concurrent independent rows. Full-size
    # exports retain bounded serial allocation on a 16 GB Mac. Results and
    # floating-point summary reductions are consumed in original row order.
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import nullcontext
    parallel = policy.parallel_preview and scene_info.width * scene_info.height <= 4_000_000 and len(starts) > 1
    with ThreadPoolExecutor(max_workers=min(4, max(1, policy.preview_workers))) if parallel else nullcontext() as executor:
        chunks = executor.map(render_chunk, starts) if parallel else map(render_chunk, starts)
        with hdr_raw_path.open("wb") as hdr_file:
            for data, maximum, skin_sum, supported in chunks:
                hdr_file.write(data)
                measured_gain_max = max(measured_gain_max, maximum)
                skin_weight_sum += skin_sum
                skin_supported_pixels += supported

    if _reference_sdr_only:
        # Local-exposure analysis consumes this SDR reference only. Computing
        # and encoding its discarded HDR twin cannot affect the reference.
        if not style.clear_float:
            raise ValueError('The independent float reference requires a float SDR style')
        tifffile.imwrite(_linear_sdr_output, sdr_pixels, photometric='rgb')
        encoded = np.empty(sdr_pixels.shape, dtype=np.uint8)
        for start in range(0, scene_info.height, chunk_rows):
            encoded[start:start+chunk_rows] = _quantize_sdr(sdr_pixels[start:start+chunk_rows])
        Image.fromarray(encoded, mode='RGB').save(sdr_path, format='JPEG', quality=sdr_quality,
            subsampling=0, optimize=True, icc_profile=_sdr_icc_bytes(style.sdr_gamut))
        return None

    if skin_enabled:
        pixels = scene_info.width * scene_info.height
        tone_mapping["phone_skin"].update({
            "mean_skin_weight": skin_weight_sum / pixels,
            "supported_skin_fraction": skin_supported_pixels / pixels,
        })
    subject_strength = 1.0 if subject_adaptation_strength is None else subject_adaptation_strength
    if (not style.clear_float and style.algorithm_version >= 4 and _allow_subject and subject_strength > 0
            and policy.automatic_tone and policy.automatic_color
            and tone_mapping["phone_dark_weight"] < .5):
        # Build cues from an SDR-independent reference. Reusing the actual
        # fallback here would violate --sdr-exposure-ev's HDR independence.
        with tempfile.TemporaryDirectory(prefix="hdrimg-subject-reference-") as temp:
            work = Path(temp)
            scale = min(1.0, 1024 / max(scene_info.width, scene_info.height))
            size = (max(1, round(scene_info.width*scale)), max(1, round(scene_info.height*scale)))
            preview = np.stack([
                np.asarray(Image.fromarray(scene[..., c], mode="F").resize(size, Image.Resampling.BOX))
                for c in range(3)
            ], axis=-1)
            preview_scene = work / "scene.tif"
            tifffile.imwrite(preview_scene, preview, photometric="rgb")
            reference = work / "reference.jpg"
            render_pair(
                preview_scene, reference, work / "reference.rgba16f",
                auto_exposure=auto_exposure, exposure_ev=exposure_ev,
                sdr_exposure_ev=0, development_ev=development_ev,
                highlight_ev=highlight_ev, shadow_ev=shadow_ev, white_ev=white_ev, black_ev=black_ev, hdr_strength=hdr_strength, peak_nits=peak_nits,
                auto_look=auto_look, contrast=contrast, saturation=saturation,
                edit_exposure_ev=edit_exposure_ev, saturation_scale=saturation_scale,
                _processing_policy=policy,
                warm_color_separation=warm_color_separation, style=style,
                sdr_adaptation_strength=sdr_adaptation_strength,
                hdr_midtone_gain=hdr_midtone_gain, hdr_shoulder_strength=hdr_shoulder_strength,
                subject_adaptation_strength=0, _allow_subject=False, _allow_histogram=False,
                skin_protection_strength=skin_protection_strength, _skin_context=_skin_context,
            )
            hdr_field, sdr_field, subject_record = detect_subject_fields(
                reference, high_key_weight=tone_mapping["phone_high_key_weight"],
                strength=subject_strength, gamut=style.sdr_gamut,
                indoor_weight=tone_mapping["phone_indoor_weight"],
            )
        tone_mapping["phone_subject"] = subject_record
        if hdr_field is not None and sdr_field is not None:
            raw = np.memmap(hdr_raw_path, dtype="<f2", mode="r+",
                            shape=(scene_info.height, scene_info.width, 4))
            measured_gain_max = 1.0
            for start in range(0, scene_info.height, chunk_rows):
                stop = min(scene_info.height, start+chunk_rows)
                hdr_ratio = resize_base_rows(hdr_field, width=scene_info.width,
                    full_height=scene_info.height, start=start, stop=stop)
                sdr_odds = resize_base_rows(sdr_field, width=scene_info.width,
                    full_height=scene_info.height, start=start, stop=stop)
                encoded = sdr_pixels[start:stop].astype(np.float32)/255
                linear = np.where(encoded <= .04045, encoded/12.92, ((encoded+.055)/1.055)**2.4)
                coefficients = DISPLAY_P3_TO_XYZ[1] if style.sdr_gamut == "display-p3" else SRGB_TO_XYZ[1]
                before_y = linear @ coefficients
                after_y = before_y*sdr_odds/(1-before_y+before_y*sdr_odds)
                ratio = np.divide(after_y, before_y, out=np.ones_like(after_y), where=before_y > 1e-6)
                affected = np.abs(sdr_odds-1) > 1e-4
                corrected = compress_gamut(linear*ratio[..., None], target=style.sdr_gamut, upper=1)
                linear = np.where(affected[..., None], corrected, linear)
                sdr_pixels[start:stop] = _quantize_sdr(linear)
                hdr = np.asarray(raw[start:stop, :, :3], dtype=np.float32)
                corrected_hdr = compress_gamut(hdr*hdr_ratio[..., None], target="rec2020", upper=peak_nits/203)
                hdr = np.where((np.abs(hdr_ratio-1) > 1e-4)[..., None], corrected_hdr, hdr)
                raw[start:stop, :, :3] = hdr.astype("<f2")
                hdr_in_sdr = hdr @ REC2020_TO_DISPLAY_P3.T if style.sdr_gamut == "display-p3" else rec2020_to_linear_srgb(hdr)
                gains = (np.maximum(hdr_in_sdr, 0)+1/64)/(linear+1/64)
                measured_gain_max = max(measured_gain_max, float(np.max(gains)))
            raw.flush()
            del raw
    if (not style.clear_float and style.algorithm_version >= 4 and _allow_histogram and style.local_contrast > 0
            and policy.automatic_tone and policy.automatic_color):
        # Build cues from an SDR-independent reference. Reusing the actual
        # fallback here would violate --sdr-exposure-ev's HDR independence.
        with tempfile.TemporaryDirectory(prefix="hdrimg-histogram-reference-") as temp:
            work = Path(temp)
            scale = min(1.0, 1024 / max(scene_info.width, scene_info.height))
            size = (max(1, round(scene_info.width*scale)), max(1, round(scene_info.height*scale)))
            preview = np.stack([
                np.asarray(Image.fromarray(scene[..., c], mode="F").resize(size, Image.Resampling.BOX))
                for c in range(3)
            ], axis=-1)
            preview_scene = work / "scene.tif"
            tifffile.imwrite(preview_scene, preview, photometric="rgb")
            reference = work / "reference.jpg"
            reference_info = render_pair(
                preview_scene, reference, work / "reference.rgba16f",
                auto_exposure=auto_exposure, exposure_ev=exposure_ev,
                sdr_exposure_ev=0, development_ev=development_ev,
                highlight_ev=highlight_ev, shadow_ev=shadow_ev, white_ev=white_ev, black_ev=black_ev, hdr_strength=hdr_strength, peak_nits=peak_nits,
                auto_look=auto_look, contrast=contrast, saturation=saturation,
                edit_exposure_ev=edit_exposure_ev, saturation_scale=saturation_scale,
                _processing_policy=policy,
                warm_color_separation=warm_color_separation, style=style,
                sdr_adaptation_strength=sdr_adaptation_strength,
                hdr_midtone_gain=hdr_midtone_gain, hdr_shoulder_strength=hdr_shoulder_strength,
                subject_adaptation_strength=subject_adaptation_strength, _allow_subject=True, _allow_histogram=False,
                skin_protection_strength=skin_protection_strength, _skin_context=_skin_context,
            )
            from .phone_histogram import local_histogram_fields
            encoded = np.asarray(Image.open(reference).convert("RGB"),dtype=np.float32)/255
            reference_rgb = np.where(encoded <= .04045,encoded/12.92,((encoded+.055)/1.055)**2.4)
            coefficients = DISPLAY_P3_TO_XYZ[1] if style.sdr_gamut == "display-p3" else SRGB_TO_XYZ[1]
            hdr_field, sdr_field, histogram_record = local_histogram_fields(reference_rgb @ coefficients,
                strength=.65 * min(style.local_contrast / .22, 1.0),
                faces=reference_info.tone_mapping.get("phone_subject", {}).get("faces", []))
            from .phone_deveil import deveil_fields
            adjusted_rgb = reference_rgb * np.asarray(hdr_field)[..., None]
            de_h, de_s, de_record = deveil_fields(adjusted_rgb,
                strength=min(style.local_contrast/.22,1.0) * (1-float(reference_info.tone_mapping.get("phone_dark_weight", 0))) * (1-float(reference_info.tone_mapping.get("phone_indoor_weight", 0))),
                faces=reference_info.tone_mapping.get("phone_subject", {}).get("faces", []), gamut=style.sdr_gamut)
            hdr_field = Image.fromarray(np.asarray(hdr_field)*np.asarray(de_h), mode="F")
            sdr_field = Image.fromarray(np.asarray(sdr_field)*np.asarray(de_s), mode="F")
            histogram_record["deveil"] = de_record
            from .phone_sky import sky_fields
            adjusted_rgb = reference_rgb * np.asarray(hdr_field)[..., None]
            sky_h, sky_s, sky_record = sky_fields(adjusted_rgb,
                strength=min(style.local_contrast/.22,1.0)*(1-float(reference_info.tone_mapping.get("phone_dark_weight", 0))) * (1-float(reference_info.tone_mapping.get("phone_indoor_weight", 0))) * (1-float(reference_info.tone_mapping.get("phone_high_key_weight", 0))),
                faces=reference_info.tone_mapping.get("phone_subject", {}).get("faces", []), gamut=style.sdr_gamut)
            hdr_field = Image.fromarray(np.asarray(hdr_field)*np.asarray(sky_h), mode="F")
            sdr_field = Image.fromarray(np.asarray(sdr_field)*np.asarray(sky_s), mode="F")
            histogram_record["sky"] = sky_record
        tone_mapping["phone_histogram"] = histogram_record
        if hdr_field is not None and sdr_field is not None:
            raw = np.memmap(hdr_raw_path, dtype="<f2", mode="r+",
                            shape=(scene_info.height, scene_info.width, 4))
            measured_gain_max = 1.0
            for start in range(0, scene_info.height, chunk_rows):
                stop = min(scene_info.height, start+chunk_rows)
                hdr_ratio = resize_base_rows(hdr_field, width=scene_info.width,
                    full_height=scene_info.height, start=start, stop=stop)
                sdr_odds = resize_base_rows(sdr_field, width=scene_info.width,
                    full_height=scene_info.height, start=start, stop=stop)
                encoded = sdr_pixels[start:stop].astype(np.float32)/255
                linear = np.where(encoded <= .04045, encoded/12.92, ((encoded+.055)/1.055)**2.4)
                coefficients = DISPLAY_P3_TO_XYZ[1] if style.sdr_gamut == "display-p3" else SRGB_TO_XYZ[1]
                before_y = linear @ coefficients
                after_y = before_y*sdr_odds/(1-before_y+before_y*sdr_odds)
                ratio = np.divide(after_y, before_y, out=np.ones_like(after_y), where=before_y > 1e-6)
                affected = np.abs(sdr_odds-1) > 1e-4
                corrected = compress_gamut(linear*ratio[..., None], target=style.sdr_gamut, upper=1)
                linear = np.where(affected[..., None], corrected, linear)
                sdr_pixels[start:stop] = _quantize_sdr(linear)
                hdr = np.asarray(raw[start:stop, :, :3], dtype=np.float32)
                corrected_hdr = compress_gamut(hdr*hdr_ratio[..., None], target="rec2020", upper=peak_nits/203)
                hdr = np.where((np.abs(hdr_ratio-1) > 1e-4)[..., None], corrected_hdr, hdr)
                raw[start:stop, :, :3] = hdr.astype("<f2")
                hdr_in_sdr = hdr @ REC2020_TO_DISPLAY_P3.T if style.sdr_gamut == "display-p3" else rec2020_to_linear_srgb(hdr)
                gains = (np.maximum(hdr_in_sdr, 0)+1/64)/(linear+1/64)
                measured_gain_max = max(measured_gain_max, float(np.max(gains)))
            raw.flush()
            del raw
    if style.clear_float:
        from .phone_clear import finish_float_render
        if style.clear_v8:
            from .phone_clear_v8 import finish_float_render
        measured_gain_max, local_record = finish_float_render(
            scene=scene, sdr=sdr_pixels, hdr_path=hdr_raw_path, final_hdr=final_hdr_path,
            work=_float_work, chunk_rows=chunk_rows, gamut=style.sdr_gamut,
            peak_nits=peak_nits, tone=tone_mapping, person_context=_skin_context,
            local_strength=min(style.local_contrast/.22, 1),
            subject_strength=subject_strength,
            enabled=(_allow_subject or _allow_histogram) and shared_hdr_chroma,
            reference_options=dict(auto_exposure=auto_exposure, exposure_ev=exposure_ev,
                sdr_quality=95,
                development_ev=development_ev, highlight_ev=highlight_ev, shadow_ev=shadow_ev, white_ev=white_ev, black_ev=black_ev,
                hdr_strength=hdr_strength, peak_nits=peak_nits, auto_look=auto_look,
                contrast=contrast, saturation=saturation, warm_color_separation=warm_color_separation,
                edit_exposure_ev=edit_exposure_ev, saturation_scale=saturation_scale,
                _processing_policy=policy,
                style=style, sdr_adaptation_strength=sdr_adaptation_strength,
                hdr_midtone_gain=hdr_midtone_gain, hdr_shoulder_strength=hdr_shoulder_strength,
                skin_protection_strength=skin_protection_strength, _skin_context=_skin_context,
                _scene_decision=_scene_decision),
        )
        tone_mapping["phone_clear_local"] = local_record
        tone_mapping["precision"] = {"main_processing": "float32", "sdr_quantization": "after_all_adjustments",
                                     "hdr_quantization": "float16_after_all_adjustments"}
        if _linear_sdr_output is not None:
            tifffile.imwrite(_linear_sdr_output, sdr_pixels, photometric="rgb")
        if _preview_output is not None:
            write_rgba16f(_preview_output, sdr_pixels)
        if not _skip_sdr_jpeg:
            quantized = np.empty(sdr_pixels.shape, dtype=np.uint8)
            for start in range(0, scene_info.height, chunk_rows):
                quantized[start:start+chunk_rows] = _quantize_sdr(sdr_pixels[start:start+chunk_rows])
            del sdr_pixels
            sdr_pixels = quantized
    if _preview_output is not None and not style.clear_float:
        # Natural's accepted pipeline quantizes before its subject adjustment.
        # Preserve those exact pre-JPEG code values rather than changing its look.
        encoded = sdr_pixels.astype(np.float32) / 255
        write_rgba16f(_preview_output, np.where(encoded <= .04045,
            encoded / 12.92, ((encoded + .055) / 1.055) ** 2.4))
    if not _skip_sdr_jpeg:
        Image.fromarray(sdr_pixels, mode="RGB").save(
            sdr_path, format="JPEG", quality=sdr_quality, subsampling=0,
            optimize=True, icc_profile=_sdr_icc_bytes(style.sdr_gamut))
    exposure_dict = {key: float(value) for key, value in asdict(stats).items()}
    if style.algorithm_version >= 2:
        boost = float(np.clip(measured_gain_max * 1.03, 1.0, 64.0))
        tone_mapping["gainmap_max_boost"] = boost
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
        style=style.as_record(),
    )


def write_rgba16f(path: Path, rgb: np.ndarray) -> None:
    values = np.asarray(rgb, dtype=np.float32)
    alpha = np.ones((*values.shape[:2], 1), dtype=np.float32)
    rgba = np.concatenate((values, alpha), axis=-1).astype("<f2")
    path.write_bytes(rgba.tobytes(order="C"))


# Keep float scratch files scoped even for direct render callers and failures.
from functools import wraps

@wraps(_render_pair)
def render_pair(*args, **kwargs) -> RenderInfo | None:
    style = kwargs.get("style") or DEFAULT_STYLE
    if not style.clear_float:
        return _render_pair(*args, **kwargs)
    with tempfile.TemporaryDirectory(prefix="hdrimg-clear-float-") as directory:
        return _render_pair(*args, **kwargs, _float_work=Path(directory))
