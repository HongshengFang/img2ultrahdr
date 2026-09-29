"""Measure paired Pixel DNG and Ultra HDR JPEG files without publishing samples.

Usage: python scripts/analyze_phone_pairs.py jpg_hdr_sample_effect --output outputs/phone_sample_analysis.json
This is a reference analysis; DNG is not a supported production render input.
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from io import BytesIO
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image, ImageCms, ImageOps

from hdrimg.color import (
    DISPLAY_P3_TO_SRGB,
    DISPLAY_P3_TO_XYZ,
    linear_srgb_to_oklab,
    rec2020_to_linear_srgb,
)
from hdrimg.raw import RAW_DEVELOPMENT_EV, develop_raw
from hdrimg.render import open_scene, render_pair
from hdrimg.style import resolve_style
from hdrimg.tone import luminance_rec2020
from hdrimg.tools import resolve_tools, run_checked


def _percentiles(values: np.ndarray) -> list[float]:
    values = values[np.isfinite(values)]
    return [float(v) for v in np.percentile(values, [10, 50, 90, 95, 99.5])]


def _decode_transfer(rgb: np.ndarray) -> np.ndarray:
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)


def _common_center_frame(rgb: np.ndarray, *, portrait: bool) -> np.ndarray:
    """Normalize the 4:3 frame after orientation and exclude edge crop differences."""
    height, width = rgb.shape[:2]
    target_ratio = 0.75 if portrait else 4.0 / 3.0
    current_ratio = width / height
    if current_ratio > target_ratio:
        cropped_width = round(height * target_ratio)
        left = (width - cropped_width) // 2
        rgb = rgb[:, left : left + cropped_width]
    else:
        cropped_height = round(width / target_ratio)
        top = (height - cropped_height) // 2
        rgb = rgb[top : top + cropped_height]
    edge = round(min(rgb.shape[:2]) * 0.025)
    return rgb[edge:-edge, edge:-edge]


def _color_summary(linear_srgb: np.ndarray) -> dict[str, float]:
    lab = linear_srgb_to_oklab(linear_srgb)
    lightness = lab[..., 0]
    relative_chroma = np.hypot(lab[..., 1], lab[..., 2]) / np.maximum(lightness, 1e-3)
    valid = (lightness >= 0.25) & (lightness <= 0.85) & (relative_chroma >= 0.025)
    return {
        "midtone_relative_chroma_median": float(np.median(relative_chroma[valid])),
        "midtone_color_pixel_fraction": float(np.mean(valid)),
    }


def _sample_jpeg(path: Path) -> tuple[dict[str, object], tuple[int, int]]:
    with Image.open(path) as image:
        icc = image.info.get("icc_profile")
        if not icc or "Display P3" not in ImageCms.getProfileName(
            ImageCms.ImageCmsProfile(BytesIO(icc))
        ):
            raise ValueError(f"Expected a Display P3 JPEG: {path.name}")
        sample = ImageOps.exif_transpose(image)
        dimensions = sample.size
        sample.thumbnail((1024, 1024))
        encoded = np.asarray(sample.convert("RGB"), dtype=np.float32) / 255.0
    encoded = _common_center_frame(encoded, portrait=dimensions[1] > dimensions[0])
    p3 = _decode_transfer(encoded)
    y = p3 @ DISPLAY_P3_TO_XYZ[1]
    srgb = p3 @ DISPLAY_P3_TO_SRGB.T
    result = {
        "linear_luminance_percentiles": _percentiles(y),
        "near_white_fraction": float(np.mean(y >= 0.95)),
        "outside_srgb_fraction": float(np.mean(np.any((srgb < 0) | (srgb > 1), axis=-1))),
        **_color_summary(srgb),
    }
    return result, dimensions


def _sample_dng(path: Path, work: Path, tools: object) -> tuple[dict[str, object], tuple[int, int]]:
    scene_path = work / "scene.tif"
    raw_work = work / "raw-work"
    raw_work.mkdir()
    develop_raw(
        path,
        scene_path,
        tools=tools,
        white_balance="camera",
        temperature_k=None,
        tint=1.0,
        work_dir=raw_work,
    )
    scene, info = open_scene(scene_path)
    step = max(1, int(np.ceil(max(info.width, info.height) / 1024)))
    # RawTherapee reserves two stops during development; restore only for the
    # distribution comparison. No visual look or tone mapping is applied here.
    linear = np.asarray(scene[::step, ::step], dtype=np.float32) * 4.0
    linear = _common_center_frame(linear, portrait=info.height > info.width)
    y = luminance_rec2020(linear)
    result = {
        "linear_luminance_percentiles": _percentiles(y),
        "above_scene_white_fraction": float(np.mean(y > 1.0)),
        **_color_summary(rec2020_to_linear_srgb(linear)),
    }
    del scene, linear
    return result, (info.width, info.height)


def _sample_hdr(
    path: Path, dimensions: tuple[int, int], work: Path, tools: object
) -> dict[str, object]:
    probe = run_checked(
        [tools.ultrahdr, "-m", "1", "-j", path, "-P"],
        label="phone Ultra HDR probe",
        timeout=120,
    ).stdout
    match = re.search(r"--maxContentBoost\s+([0-9.]+)", probe)
    raw_path = work / "hdr.rgba16f"
    run_checked(
        [tools.ultrahdr, "-m", "1", "-j", path, "-o", "0", "-O", "4", "-z", raw_path],
        label="phone Ultra HDR decode",
        timeout=600,
    )
    width, height = dimensions
    raw = np.memmap(raw_path, dtype="<f2", mode="r", shape=(height, width, 4))
    step = max(1, int(np.ceil(max(width, height) / 1024)))
    rgb = np.asarray(raw[::step, ::step, :3], dtype=np.float32)
    y = rgb @ DISPLAY_P3_TO_XYZ[1]
    return {
        "linear_luminance_percentiles": _percentiles(y),
        "above_sdr_white_fraction": float(np.mean(y > 1.0)),
        "peak_luminance": float(np.max(y)),
        "max_content_boost": float(match.group(1)) if match else None,
    }


def _audit_phone_style(
    scene_path: Path, work: Path, style_name: str
) -> dict[str, object]:
    """Render a small DNG preview without adding DNG to the public input API."""
    scene, info = open_scene(scene_path)
    step = max(1, int(np.ceil(max(info.width, info.height) / 1024)))
    preview_path = work / "preview.tif"
    tifffile.imwrite(preview_path, np.asarray(scene[::step, ::step], dtype=np.float32))
    sdr_path = work / "phone-style.jpg"
    hdr_path = work / "phone-style.rgba16f"
    style = resolve_style(
        style_name, midtone_lift_ev=None, highlight_rolloff=None,
        local_contrast=None, vibrance=None, sdr_gamut=None,
    )
    info = render_pair(
        preview_path, sdr_path, hdr_path,
        auto_exposure=True, exposure_ev=None, development_ev=RAW_DEVELOPMENT_EV,
        highlight_ev=0.0, hdr_strength=1.0, peak_nits=1000, style=style,
    )
    sdr, dimensions = _sample_jpeg(sdr_path)
    width, height = dimensions
    hdr = np.memmap(hdr_path, dtype="<f2", mode="r", shape=(height, width, 4))
    y = np.asarray(hdr[..., :3], dtype=np.float32) @ np.array(
        [0.2627, 0.6780, 0.0593], dtype=np.float32
    )
    return {
        "sdr": sdr,
        "hdr": {
            "linear_luminance_percentiles": _percentiles(y),
            "above_sdr_white_fraction": float(np.mean(y > 1.0)),
            "peak_luminance": float(np.max(y)),
        },
        "auto_look": info.look,
    }


def analyze(directory: Path, *, audit_style: str | None = None) -> dict[str, object]:
    tools = resolve_tools(require_raw=True, require_exif=True)
    groups: dict[str, dict[str, Path]] = {}
    for path in directory.iterdir():
        if path.suffix.lower() not in {".dng", ".jpg"} or ".RAW-" not in path.name:
            continue
        stem = path.name.split(".RAW-", 1)[0]
        groups.setdefault(stem, {})[path.suffix.lower()] = path.resolve()
    pairs = []
    for stem, paths in sorted(groups.items()):
        if set(paths) != {".dng", ".jpg"}:
            raise ValueError(f"Incomplete RAW/JPEG pair: {stem}")
        with tempfile.TemporaryDirectory(prefix="phone-pair-") as temp:
            work = Path(temp)
            dng_stats, dng_size = _sample_dng(paths[".dng"], work, tools)
            sdr_stats, jpeg_size = _sample_jpeg(paths[".jpg"])
            hdr_stats = _sample_hdr(paths[".jpg"], jpeg_size, work, tools)
            style_audit = (
                _audit_phone_style(work / "scene.tif", work, audit_style)
                if audit_style else None
            )
        pairs.append(
            {
                "stem": stem,
                "dng_dimensions": dng_size,
                "jpeg_dimensions": jpeg_size,
                "dng": dng_stats,
                "sdr": sdr_stats,
                "hdr": hdr_stats,
                **({f"rendered_{audit_style.replace('-', '_')}": style_audit} if style_audit else {}),
            }
        )
        print(f"Analyzed {stem}", flush=True)
    return {"pair_count": len(pairs), "percentiles": [10, 50, 90, 95, 99.5], "pairs": pairs}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--audit-style", nargs="?", const="phone-natural",
        choices=("phone-natural", "phone-clear"),
    )
    args = parser.parse_args()
    result = analyze(args.directory, audit_style=args.audit_style)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
