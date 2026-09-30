from __future__ import annotations

import json
import os
import platform
import shutil
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from PIL import Image

from . import __version__
from .errors import InputError, ProcessingError
from .metadata import copy_metadata, read_output_metadata, read_source_metadata
from .phone_noise import phone_denoise_decision
from .phone_color_guard import preserve_blue_chroma
from .phone_surface import denoise_blue_surfaces
from .phone_raw_wb import preserve_raw_skin
from .raw import RAW_DEVELOPMENT_EV, develop_raw, phone_denoise_overlay, phone_detail_overlay, validate_raw_input
from .render import open_scene, render_pair
from .style import DEFAULT_STYLE_NAME, RAW_PHONE_STYLES, STYLE_PRESETS, StyleSettings, resolve_style
from .tools import resolve_tools, tool_version, ultrahdr_version
from .ultrahdr import encode_ultrahdr, validate_ultrahdr


@dataclass(frozen=True)
class RenderOptions:
    output: Path
    auto_look: bool = True
    auto_exposure: bool = True
    exposure_ev: float | None = None
    sdr_exposure_ev: float = 0.0
    white_balance: str | None = None
    temperature_k: int | None = None
    tint: float = 1.0
    highlight_ev: float = 0.0
    hdr_strength: float = 1.0
    peak_nits: int = 1000
    contrast: float | None = None
    saturation: float | None = None
    warm_color_separation: float = 0.0
    style: str = DEFAULT_STYLE_NAME
    midtone_lift_ev: float | None = None
    highlight_rolloff: float | None = None
    local_contrast: float | None = None
    vibrance: float | None = None
    sdr_gamut: str | None = None
    sdr_adaptation_strength: float | None = None
    hdr_midtone_gain: float | None = None
    hdr_shoulder_strength: float | None = None
    subject_adaptation_strength: float | None = None
    skin_protection_strength: float | None = None
    raw_denoise_strength: float | None = None
    raw_detail_strength: float | None = None
    surface_denoise_strength: float | None = None
    strip_metadata: bool = False
    keep_intermediates: bool = False
    overwrite: bool = False

    def validate(self) -> None:
        if self.exposure_ev is not None and not -5.0 <= self.exposure_ev <= 5.0:
            raise InputError("--exposure-ev must be between -5 and +5")
        if not -2.0 <= self.sdr_exposure_ev <= 2.0:
            raise InputError("--sdr-exposure-ev must be between -2 and +2")
        if self.white_balance not in {None, "camera", "auto", "custom"}:
            raise InputError("--white-balance must be camera, auto, or custom")
        if self.white_balance == "custom" and self.temperature_k is None:
            raise InputError("--temperature-k is required for custom white balance")
        if self.temperature_k is not None and not 2000 <= self.temperature_k <= 15000:
            raise InputError("--temperature-k must be between 2000 and 15000")
        if not 0.5 <= self.tint <= 2.0:
            raise InputError("--tint must be between 0.5 and 2.0")
        if not -2.0 <= self.highlight_ev <= 2.0:
            raise InputError("--highlight-ev must be between -2 and +2")
        if not 0.0 <= self.hdr_strength <= 1.0:
            raise InputError("--hdr-strength must be between 0 and 1")
        if not 400 <= self.peak_nits <= 2000:
            raise InputError("--peak-nits must be between 400 and 2000")
        if self.contrast is not None and not 1.0 <= self.contrast <= 2.0:
            raise InputError("--contrast must be between 1.0 and 2.0")
        if self.saturation is not None and not 0.8 <= self.saturation <= 1.5:
            raise InputError("--saturation must be between 0.8 and 1.5")
        if not 0.0 <= self.warm_color_separation <= 1.0:
            raise InputError("--warm-color-separation must be between 0 and 1")
        if self.style not in STYLE_PRESETS:
            raise InputError("--style must be natural, phone-natural, or phone-clear")
        for name, value, upper in (
            ("midtone-lift-ev", self.midtone_lift_ev, 1.0),
            ("highlight-rolloff", self.highlight_rolloff, 1.0),
            ("local-contrast", self.local_contrast, 0.3),
            ("vibrance", self.vibrance, 0.3),
        ):
            if value is not None and not 0.0 <= value <= upper:
                raise InputError(f"--{name} must be between 0 and {upper:g}")
        if self.sdr_gamut not in {None, "srgb", "display-p3"}:
            raise InputError("--sdr-gamut must be srgb or display-p3")
        for name, value in (
            ("sdr-adaptation-strength", self.sdr_adaptation_strength),
            ("hdr-shoulder-strength", self.hdr_shoulder_strength),
            ("subject-adaptation-strength", self.subject_adaptation_strength),
            ("skin-protection-strength", self.skin_protection_strength),
            ("raw-denoise-strength", self.raw_denoise_strength),
            ("raw-detail-strength", self.raw_detail_strength),
            ("surface-denoise-strength", self.surface_denoise_strength),
        ):
            if value is not None and not 0.0 <= value <= 1.0:
                raise InputError(f"--{name} must be between 0 and 1")
        if self.hdr_midtone_gain is not None and not 1.0 <= self.hdr_midtone_gain <= 3.0:
            raise InputError("--hdr-midtone-gain must be between 1 and 3")
        if self.raw_denoise_strength and self.style not in RAW_PHONE_STYLES:
            raise InputError("--raw-denoise-strength requires --style phone-clear or phone-natural")
        for name, value in (("raw-detail-strength", self.raw_detail_strength),
                            ("surface-denoise-strength", self.surface_denoise_strength)):
            if value and self.style not in RAW_PHONE_STYLES:
                raise InputError(f"--{name} requires --style phone-clear or phone-natural")

    def resolved_white_balance(self) -> str:
        if self.white_balance is not None:
            return self.white_balance
        return "auto" if self.style in RAW_PHONE_STYLES else "camera"

    def resolved_raw_skin_strength(self) -> float:
        if (self.style not in RAW_PHONE_STYLES or self.resolved_white_balance() != "auto"
                or not self.auto_look or self.contrast is not None or self.saturation is not None):
            return 0.0
        return 1.0 if self.skin_protection_strength is None else self.skin_protection_strength

    def resolved_raw_denoise_strength(self) -> float:
        if self.style not in RAW_PHONE_STYLES:
            return 0.0
        if self.raw_denoise_strength is not None:
            return self.raw_denoise_strength
        return float(self.auto_look and self.contrast is None and self.saturation is None)

    def resolved_raw_detail_strength(self) -> float:
        if self.style not in RAW_PHONE_STYLES:
            return 0.0
        if self.raw_detail_strength is not None:
            return self.raw_detail_strength
        return float(self.auto_look and self.contrast is None and self.saturation is None
                     and self.resolved_raw_denoise_strength() > 0)

    def resolved_surface_denoise_strength(self) -> float:
        if self.style not in RAW_PHONE_STYLES:
            return 0.0
        if self.surface_denoise_strength is not None:
            return self.surface_denoise_strength
        return self.resolved_raw_denoise_strength()


@dataclass(frozen=True)
class RenderResult:
    source: Path
    sdr: Path
    ultrahdr: Path
    manifest: Path
    look: dict[str, Any] | None = None
    scene: Path | None = None
    hdr_raw: Path | None = None


def _runtime_versions() -> dict[str, str]:
    packages: dict[str, str] = {}
    for package in ("numpy", "tifffile", "Pillow", "colour-science"):
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            packages[package] = "not installed"
    return {"python": platform.python_version(), **packages}


def _output_paths(source: Path, output: Path, keep: bool) -> dict[str, Path]:
    stem = source.stem
    paths = {
        "sdr": output / f"{stem}_sdr.jpg",
        "ultrahdr": output / f"{stem}_ultrahdr.jpg",
        "manifest": output / f"{stem}_render.json",
    }
    if keep:
        paths["scene"] = output / f"{stem}_scene.tif"
        paths["hdr_raw"] = output / f"{stem}_hdr.rgba16f"
    return paths


def _check_conflicts(paths: dict[str, Path], overwrite: bool) -> None:
    conflicts = [path for path in paths.values() if path.exists()]
    if conflicts and not overwrite:
        names = ", ".join(path.name for path in conflicts)
        raise InputError(f"Output already exists: {names}; use --overwrite to replace")


def _publish(staged: dict[str, Path], final: dict[str, Path]) -> None:
    for key in ("sdr", "ultrahdr", "scene", "hdr_raw", "manifest"):
        if key in staged and key in final:
            os.replace(staged[key], final[key])


def render_raw(source_path: Path, options: RenderOptions, *,
               _style_override: StyleSettings | None = None) -> RenderResult:
    options.validate()
    style = resolve_style(
        options.style,
        midtone_lift_ev=options.midtone_lift_ev,
        highlight_rolloff=options.highlight_rolloff,
        local_contrast=options.local_contrast,
        vibrance=options.vibrance,
        sdr_gamut=options.sdr_gamut,
    )
    if _style_override is not None:
        if _style_override.name != options.style:
            raise InputError("Internal recipe must match the requested style")
        style = replace(_style_override, **{
            name: getattr(options, name) for name in (
                "midtone_lift_ev", "highlight_rolloff", "local_contrast", "vibrance", "sdr_gamut"
            ) if getattr(options, name) is not None
        })
    source = validate_raw_input(source_path)
    output = options.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    final = _output_paths(source, output, options.keep_intermediates)
    _check_conflicts(final, options.overwrite)
    tools = resolve_tools(require_raw=True, require_exif=True)

    with tempfile.TemporaryDirectory(prefix=".hdrimg-", dir=output) as temp_name:
        work = Path(temp_name)
        scene = work / "scene.tif"
        sdr = work / "sdr.jpg"
        hdr_raw = work / "hdr.rgba16f"
        ultrahdr = work / "ultrahdr.jpg"
        manifest = work / "render.json"

        denoise_strength = options.resolved_raw_denoise_strength()
        detail_strength = options.resolved_raw_detail_strength()
        surface_strength = options.resolved_surface_denoise_strength()
        development_overlay = None
        white_balance = options.resolved_white_balance()
        if denoise_strength > 0:
            development_overlay = work / "phone-denoise.pp3"
            development_overlay.write_text(phone_denoise_overlay(denoise_strength))

        metering_decision = None
        metering_preview = None
        native_wb_bias = 0.0
        native_wb_record = {"reason": "explicit_white_balance_or_manual_look", "temperature_bias": 0.0}
        if style.clear_float and options.auto_look and options.contrast is None and options.saturation is None:
            from .phone_clear import scene_decision, raw_temperature_bias
            if style.clear_v8:
                from .phone_clear_v8 import scene_decision
            import numpy as np
            def preview_raw(path):
                values, _ = open_scene(path)
                scale = min(1., 1024/max(values.shape[:2]))
                size = (max(1, round(values.shape[1]*scale)), max(1, round(values.shape[0]*scale)))
                return np.stack([np.asarray(Image.fromarray(values[..., c]).resize(
                    size, Image.Resampling.BOX)) for c in range(3)], axis=-1)
            meter_work = work / "fixed-camera-metering"
            meter_work.mkdir()
            meter_scene = meter_work / "scene.tif"
            develop_raw(source, meter_scene, tools=tools, white_balance="camera",
                temperature_k=None, tint=1.0, work_dir=meter_work)
            metering_preview = preview_raw(meter_scene)
            metering_decision = scene_decision(metering_preview,
                development_ev=RAW_DEVELOPMENT_EV, peak_nits=options.peak_nits)
            meter_scene.unlink()
        develop_raw(
            source,
            scene,
            tools=tools,
            white_balance=white_balance,
            temperature_k=options.temperature_k,
            tint=options.tint,
            work_dir=work,
            profile_overlay=development_overlay,
        )
        denoise_decision = None
        extra_luma = 0.0
        if denoise_strength > 0:
            first_scene, _ = open_scene(scene)
            denoise_decision = phone_denoise_decision(first_scene, development_ev=RAW_DEVELOPMENT_EV)
            del first_scene
            extra_luma = denoise_decision["extra_denoise_weight"]
        if metering_decision is not None and white_balance == "auto":
            color_decision = metering_decision
            if style.clear_v8:
                # Keep the accepted RAW color decision independent of the
                # new tone-metering experiment; no creative WB change here.
                from .phone_clear import scene_decision as v7_color_scene_decision
                color_decision = v7_color_scene_decision(metering_preview,
                    development_ev=RAW_DEVELOPMENT_EV, peak_nits=options.peak_nits)
            native_wb_bias, native_wb_record = raw_temperature_bias(
                preview_raw(scene), metering_preview, color_decision)
        # Measure noise before sharpening. The final development composes the
        # selected denoise and detail profiles in a single second pass.
        if extra_luma > 0 or detail_strength > 0 or native_wb_bias > 0:
            development_overlay = work / "phone-denoise-adaptive.pp3"
            profile = phone_denoise_overlay(denoise_strength, extra_luma=extra_luma) if denoise_strength > 0 else ""
            if detail_strength > 0:
                profile += "\n" + phone_detail_overlay(detail_strength)
            if profile:
                development_overlay.write_text(profile)
            else:
                development_overlay = None
            adaptive_work = work / "adaptive-development"
            adaptive_work.mkdir()
            scene = work / "scene-adaptive.tif"
            develop_raw(
                source, scene, tools=tools, white_balance=white_balance,
                temperature_k=options.temperature_k, tint=options.tint,
                work_dir=adaptive_work, profile_overlay=development_overlay,
                **({"temperature_bias": native_wb_bias} if native_wb_bias else {}),
            )
        surface_record = None
        if surface_strength > 0:
            surface_scene = work / "scene-surface.tif"
            surface_record = denoise_blue_surfaces(scene, surface_scene,
                strength=surface_strength, development_ev=RAW_DEVELOPMENT_EV)
            if surface_record["applied"]:
                scene = surface_scene
        color_record = None
        if denoise_strength > 0:
            # Keep matching RAW color before thresholded denoiser chroma boosts.
            # The reference uses the same geometry, white balance and headroom.
            reference_work = work / "color-reference-development"
            reference_work.mkdir()
            color_reference = work / "scene-color-reference.tif"
            develop_raw(
                source, color_reference, tools=tools,
                white_balance=white_balance,
                temperature_k=options.temperature_k, tint=options.tint,
                work_dir=reference_work,
                **({"temperature_bias": native_wb_bias} if native_wb_bias else {}),
            )
            color_scene = work / "scene-color-preserved.tif"
            color_record = preserve_blue_chroma(scene, color_reference, color_scene,
                strength=denoise_strength, development_ev=RAW_DEVELOPMENT_EV)
            if color_record["applied"]:
                scene = color_scene
        raw_skin_record = {"applied": False, "reason": "disabled_or_explicit_white_balance"}
        skin_context = None
        camera_reference = None
        raw_skin_strength = options.resolved_raw_skin_strength()
        if raw_skin_strength > 0:
            # The main development already uses automatic RAW WB. A matching
            # camera-WB reference only bounds skin chroma loss before the look.
            camera_work = work / "camera-white-balance-reference"
            camera_work.mkdir()
            camera_reference = work / "scene-camera-reference.tif"
            develop_raw(source, camera_reference, tools=tools,
                white_balance="camera", temperature_k=None, tint=1.0,
                work_dir=camera_work, profile_overlay=development_overlay)
            guarded_scene = work / "scene-raw-skin.tif"
            raw_skin_record, skin_context = preserve_raw_skin(
                scene, camera_reference, guarded_scene, strength=raw_skin_strength,
                development_ev=RAW_DEVELOPMENT_EV,
                pale_boundaries=style.pale_boundaries)
            if raw_skin_record["applied"]:
                scene = guarded_scene
        render_info = render_pair(
            scene,
            sdr,
            hdr_raw,
            auto_exposure=options.auto_exposure,
            exposure_ev=options.exposure_ev,
            sdr_exposure_ev=options.sdr_exposure_ev,
            development_ev=RAW_DEVELOPMENT_EV,
            highlight_ev=options.highlight_ev,
            hdr_strength=options.hdr_strength,
            peak_nits=options.peak_nits,
            auto_look=options.auto_look,
            contrast=options.contrast,
            saturation=options.saturation,
            warm_color_separation=options.warm_color_separation,
            style=style,
            sdr_adaptation_strength=options.sdr_adaptation_strength,
            hdr_midtone_gain=options.hdr_midtone_gain,
            hdr_shoulder_strength=options.hdr_shoulder_strength,
            subject_adaptation_strength=options.subject_adaptation_strength,
            skin_protection_strength=options.skin_protection_strength,
            # V7's accepted look measures its rendering matte from the final
            # RAW skin-guarded scene. Reuse that matte inside render_pair's
            # SDR/HDR references, rather than the earlier WB-guard reference.
            _skin_context=skin_context if not style.pale_skin else None,
            **({"_scene_decision": metering_decision} if style.clear_float else {}),
        )
        if style.clear_float:
            render_info.tone_mapping["phone_clear_metering"] = {
                "source": "camera_wb_raw_reference" if metering_decision else "manual_or_disabled",
                "selected_color_white_balance": white_balance,
                "decision": metering_decision.as_record() if metering_decision else None,
                "raw_wb_policy": native_wb_record,
            }
        if not render_info.scene.has_icc_profile:
            raise ProcessingError("Developed scene TIFF is missing its linear Rec.2020 ICC profile")
        source_metadata = read_source_metadata(source, tools)
        if not options.strip_metadata:
            copy_metadata(source, sdr, tools, sdr_gamut=style.sdr_gamut)
        encode_ultrahdr(
            sdr,
            hdr_raw,
            ultrahdr,
            width=render_info.scene.width,
            height=render_info.scene.height,
            peak_nits=render_info.peak_nits,
            max_boost=render_info.max_content_boost,
            gainmap_quality=render_info.gainmap_quality,
            sdr_gamut=style.sdr_gamut,
            tools=tools,
        )
        validation = validate_ultrahdr(
            ultrahdr,
            width=render_info.scene.width,
            height=render_info.scene.height,
            work_dir=work,
            tools=tools,
        )
        with Image.open(sdr) as image:
            sdr_size = list(image.size)
            has_srgb_icc = bool(image.info.get("icc_profile"))
        output_metadata = read_output_metadata(ultrahdr, tools)
        if not options.strip_metadata and source_metadata.get("Make"):
            if output_metadata.get("Make") != source_metadata.get("Make"):
                raise ProcessingError("Camera metadata was not retained in Ultra HDR output")
        if output_metadata.get("Orientation") not in (None, 1):
            raise ProcessingError(
                "Ultra HDR output contains a non-normalized orientation tag"
            )

        record: dict[str, Any] = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "hdrimg_version": __version__,
            "source": {
                "path": os.fspath(source),
                "size_bytes": source.stat().st_size,
                "metadata": source_metadata,
            },
            "options": {
                **asdict(options),
                "output": os.fspath(output),
            },
            "render": asdict(render_info),
            "raw_development": {
                "white_balance": {
                    "requested": options.white_balance or "style-default",
                    "resolved": white_balance,
                    "temperature_k": options.temperature_k if white_balance == "custom" else None,
                    "tint": options.tint,
                    "stage": "raw",
                    "post_illuminant_correction": style.algorithm_version in (4, 5),
                    **({"native_temperature_bias": native_wb_record} if style.clear_float else {}),
                    "skin_guard": raw_skin_record,
                },
                "denoise_strength": denoise_strength,
                "denoise_profile": development_overlay.read_text() if development_overlay else None,
                "denoise_decision": denoise_decision,
                "detail_strength": detail_strength,
                "surface_denoise": surface_record,
                "color_preservation": color_record,
            },
            "outputs": {
                "sdr": {
                    "filename": final["sdr"].name,
                    "dimensions": sdr_size,
                    "icc_profile_present": has_srgb_icc,
                    "gamut": style.sdr_gamut,
                    "size_bytes": sdr.stat().st_size,
                },
                "ultrahdr": {
                    "filename": final["ultrahdr"].name,
                    "size_bytes": ultrahdr.stat().st_size,
                    "probe": validation.probe_text,
                    "decoded_sdr_bytes": validation.sdr_decoded_bytes,
                    "decoded_hdr_bytes": validation.hdr_decoded_bytes,
                    "decoded_hdr_peak_rgb": validation.hdr_peak_rgb,
                    "decoded_hdr_peak_luminance": validation.hdr_peak_luminance,
                    "decoded_hdr_above_reference_fraction": (
                        validation.hdr_above_reference_fraction
                    ),
                    "visible_hdr_content": validation.hdr_peak_luminance > 1.0,
                    "metadata": output_metadata,
                },
            },
            "tools": {
                "rawtherapee": tool_version(tools.rawtherapee, ["-v"]),
                "libultrahdr": ultrahdr_version(tools.ultrahdr),
                "exiftool": tool_version(tools.exiftool, ["-ver"]),
            },
            "runtime": _runtime_versions(),
            "validation": {"passed": True},
        }
        manifest.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        staged = {"sdr": sdr, "ultrahdr": ultrahdr, "manifest": manifest}
        if options.keep_intermediates:
            scene_staged = work / "scene.publish.tif"
            hdr_staged = work / "hdr.publish.rgba16f"
            shutil.copy2(scene, scene_staged)
            shutil.copy2(hdr_raw, hdr_staged)
            staged.update(scene=scene_staged, hdr_raw=hdr_staged)
        _publish(staged, final)

    return RenderResult(
        source=source,
        sdr=final["sdr"],
        ultrahdr=final["ultrahdr"],
        manifest=final["manifest"],
        look=render_info.look,
        scene=final.get("scene"),
        hdr_raw=final.get("hdr_raw"),
    )
