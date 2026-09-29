"""Audit phone-clear against paired Pixel DNG/Ultra HDR JPEG references.

The optional scene cache contains reduced, temporary RAW developments. DNG is
not a production input format. All reports and contact sheets belong in the
ignored outputs directory; original photographs are never copied into Git.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import shutil
import hashlib
from dataclasses import asdict
from io import BytesIO
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image, ImageCms, ImageDraw

from hdrimg.color import (
    DISPLAY_P3_TO_SRGB,
    DISPLAY_P3_TO_XYZ,
    REC2020_TO_DISPLAY_P3,
    REC2020_TO_XYZ,
    linear_srgb_to_oklab,
    srgb_oetf,
)
from hdrimg.raw import RAW_DEVELOPMENT_EV, develop_raw
from hdrimg.render import open_scene, render_pair
from hdrimg.tools import resolve_tools, run_checked
from hdrimg.ultrahdr import encode_ultrahdr, validate_ultrahdr


def _decode_transfer(encoded: np.ndarray) -> np.ndarray:
    return np.where(
        encoded <= 0.04045, encoded / 12.92,
        ((encoded + 0.055) / 1.055) ** 2.4,
    )


def _center_frame(rgb: np.ndarray, portrait: bool) -> np.ndarray:
    height, width = rgb.shape[:2]
    ratio = 0.75 if portrait else 4.0 / 3.0
    if width / height > ratio:
        new_width = round(height * ratio)
        rgb = rgb[:, (width - new_width) // 2 : (width + new_width) // 2]
    else:
        new_height = round(width / ratio)
        rgb = rgb[(height - new_height) // 2 : (height + new_height) // 2]
    edge = round(min(rgb.shape[:2]) * 0.025)
    return rgb[edge:-edge, edge:-edge]


def _read_sdr(path: Path, *, step: int | None = None) -> tuple[np.ndarray, tuple[int, int]]:
    with Image.open(path) as image:
        if image.getexif().get(274, 1) != 1:
            raise ValueError(f"Non-normalized JPEG orientation: {path}")
        icc = image.info.get("icc_profile")
        if not icc or "Display P3" not in ImageCms.getProfileName(
            ImageCms.ImageCmsProfile(BytesIO(icc))
        ):
            raise ValueError(f"Expected a Display P3 SDR image: {path}")
        width, height = image.size
        stride = step or max(1, int(np.ceil(max(width, height) / 1024)))
        encoded = np.asarray(image.convert("RGB"), dtype=np.uint8)[::stride, ::stride]
    linear = _decode_transfer(encoded.astype(np.float32) / 255.0)
    return _center_frame(linear, height > width), (width, height)


def _read_hdr(path: Path, dimensions: tuple[int, int], *, step: int | None = None) -> np.ndarray:
    width, height = dimensions
    stride = step or max(1, int(np.ceil(max(width, height) / 1024)))
    raw = np.memmap(path, dtype="<f2", mode="r", shape=(height, width, 4))
    sample = np.asarray(raw[::stride, ::stride, :3], dtype=np.float32)
    return _center_frame(sample, height > width)


def _color_families(linear_p3: np.ndarray) -> dict[str, dict[str, float]]:
    lab = linear_srgb_to_oklab(linear_p3 @ DISPLAY_P3_TO_SRGB.T)
    lightness = lab[..., 0]
    chroma = np.hypot(lab[..., 1], lab[..., 2])
    hue = np.degrees(np.arctan2(lab[..., 2], lab[..., 1])) % 360.0
    masks = {
        "blue": (hue >= 200) & (hue <= 275) & (chroma > 0.035) & (lightness > 0.35),
        "warm": (hue >= 20) & (hue <= 85) & (chroma > 0.035) & (lightness > 0.25),
        "green": (hue >= 105) & (hue <= 190) & (chroma > 0.035),
        "near_white": (lightness > 0.72) & (chroma < 0.045),
    }
    result = {}
    for name, mask in masks.items():
        result[name] = {"pixel_fraction": float(np.mean(mask))}
        if np.any(mask):
            result[name].update(
                lightness_median=float(np.median(lightness[mask])),
                chroma_median=float(np.median(chroma[mask])),
                hue_median=float(np.median(hue[mask])),
            )
    return result


def _metrics(sdr_p3: np.ndarray, hdr: np.ndarray, *, hdr_gamut: str) -> dict[str, object]:
    sdr_y = sdr_p3 @ DISPLAY_P3_TO_XYZ[1]
    hdr_y = hdr @ (DISPLAY_P3_TO_XYZ[1] if hdr_gamut == "display-p3" else REC2020_TO_XYZ[1])
    middle = (sdr_y >= 0.1) & (sdr_y < 0.4)
    gain = (hdr_y + 1.0 / 64.0) / (sdr_y + 1.0 / 64.0)
    gain_bands = {}
    for name, low, high in (
        ("shadows", 0.02, 0.10), ("midtones", 0.10, 0.40),
        ("upper_midtones", 0.40, 0.80), ("highlights", 0.80, 1.01),
    ):
        mask = (sdr_y >= low) & (sdr_y < high)
        gain_bands[name] = {
            "fraction": float(np.mean(mask)),
            "median": float(np.median(gain[mask])) if np.any(mask) else None,
            "p90": float(np.percentile(gain[mask], 90)) if np.any(mask) else None,
        }
    return {
        "sdr_percentiles": [float(v) for v in np.percentile(sdr_y, [10, 50, 90, 95, 99.5])],
        "hdr_percentiles": [float(v) for v in np.percentile(hdr_y, [10, 50, 90, 95, 99.5])],
        "hdr_peak": float(np.max(hdr_y)),
        "hdr_above_white_fraction": float(np.mean(hdr_y > 1.0)),
        "hdr_above_2x_white_fraction": float(np.mean(hdr_y > 2.0)),
        "hdr_above_3x_white_fraction": float(np.mean(hdr_y > 3.0)),
        "midtone_gain_median": float(np.median(hdr_y[middle] / np.maximum(sdr_y[middle], 1e-5))) if np.any(middle) else None,
        "gain_map_p99": float(np.percentile(gain, 99)),
        "gain_bands": gain_bands,
        "color_families": _color_families(sdr_p3),
    }


def _small(rgb: np.ndarray, width: int = 48, height: int = 64) -> np.ndarray:
    """Coarse, geometry-tolerant grid for paired DNG/JPEG comparisons."""
    if rgb.shape[1] > rgb.shape[0]:
        width, height = height, width
    return np.stack([
        np.asarray(Image.fromarray(rgb[..., channel].astype(np.float32), mode="F").resize(
            (width, height), Image.Resampling.BOX
        ))
        for channel in range(3)
    ], axis=-1)


def _spatial_metrics(
    phone_sdr: np.ndarray, repo_sdr: np.ndarray,
    phone_hdr: np.ndarray, repo_hdr_p3: np.ndarray,
) -> dict[str, float]:
    """Compare matched coarse locations, including color and HDR gain placement.

    These are diagnostic distances, not a claim of pixel-perfect DNG/JPEG
    registration: phone JPEG may contain multi-frame processing and a crop.
    """
    ps, rs, ph, rh = (_small(image) for image in (
        phone_sdr, repo_sdr, phone_hdr, repo_hdr_p3
    ))
    ps_y, rs_y, ph_y, rh_y = (
        image @ DISPLAY_P3_TO_XYZ[1] for image in (ps, rs, ph, rh)
    )
    ps_lab = linear_srgb_to_oklab(ps @ DISPLAY_P3_TO_SRGB.T)
    rs_lab = linear_srgb_to_oklab(rs @ DISPLAY_P3_TO_SRGB.T)
    phone_gain = np.log2((ph_y + 1.0 / 64) / (ps_y + 1.0 / 64))
    repo_gain = np.log2((rh_y + 1.0 / 64) / (rs_y + 1.0 / 64))
    phone_detail = np.concatenate((
        np.diff(ps_lab[..., 0], axis=0).ravel(),
        np.diff(ps_lab[..., 0], axis=1).ravel(),
    ))
    repo_detail = np.concatenate((
        np.diff(rs_lab[..., 0], axis=0).ravel(),
        np.diff(rs_lab[..., 0], axis=1).ravel(),
    ))
    return {
        "sdr_spatial_luma_mae_ev": float(np.mean(np.abs(
            np.log2((rs_y + 0.02) / (ps_y + 0.02))
        ))),
        "sdr_spatial_lightness_mae": float(np.mean(np.abs(rs_lab[..., 0] - ps_lab[..., 0]))),
        "sdr_spatial_color_mae": float(np.mean(np.hypot(
            rs_lab[..., 1] - ps_lab[..., 1], rs_lab[..., 2] - ps_lab[..., 2]
        ))),
        "sdr_local_contrast_mae": float(np.mean(np.abs(repo_detail - phone_detail))),
        "hdr_spatial_luma_mae_ev": float(np.mean(np.abs(
            np.log2((rh_y + 0.02) / (ph_y + 0.02))
        ))),
        "hdr_spatial_gain_mae_ev": float(np.mean(np.abs(repo_gain - phone_gain))),
    }


def _panel(rgb_p3: np.ndarray, *, hdr: bool, width: int = 300, height: int = 390) -> Image.Image:
    linear = np.maximum(rgb_p3, 0.0)
    if hdr:
        luminance = linear @ DISPLAY_P3_TO_XYZ[1]
        linear = linear / (1.0 + luminance[..., None])
    # Contact sheets are ordinary sRGB PNGs; both input JPEGs are Display P3.
    encoded = srgb_oetf(np.clip(linear @ DISPLAY_P3_TO_SRGB.T, 0.0, 1.0))
    image = Image.fromarray(np.uint8(np.floor(encoded * 255 + 0.5)), "RGB")
    image.thumbnail((width - 10, height - 34), Image.Resampling.LANCZOS)
    return image


def _contact_sheet(rows: list[tuple[int, list[Image.Image]]], output: Path) -> None:
    width, height = 300, 390
    sheet = Image.new("RGB", (width * 4, height * len(rows)), "#eeeeee")
    draw = ImageDraw.Draw(sheet)
    for row, (number, panels) in enumerate(rows):
        for column, (label, panel) in enumerate(zip(
            ("phone SDR", "repo SDR", "phone HDR preview", "repo HDR preview"), panels
        )):
            x = column * width + (width - panel.width) // 2
            y = row * height + 4
            sheet.paste(panel, (x, y))
            draw.text((column * width + 8, (row + 1) * height - 24), f"{number:02} {label}", fill="#111111")
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)


def _region_metrics(sdr: np.ndarray, hdr_p3: np.ndarray, box: list[float]) -> dict[str, object]:
    """Fixed, manually reviewed regions; coordinates refer to the audit crop.

    Compare region distributions, not purported pixel correspondences. Texture
    diagnostics mix noise and detail and must be interpreted with the crops.
    """
    def crop(rgb: np.ndarray) -> np.ndarray:
        h, w = rgb.shape[:2]
        x0, y0, x1, y1 = box
        part = rgb[round(y0*h):round(y1*h), round(x0*w):round(x1*w)]
        return np.stack([
            np.asarray(Image.fromarray(part[..., c], mode="F").resize(
                (96, 96), Image.Resampling.BOX
            )) for c in range(3)
        ], axis=-1)
    s, h = crop(sdr), crop(hdr_p3)
    sy, hy = s @ DISPLAY_P3_TO_XYZ[1], h @ DISPLAY_P3_TO_XYZ[1]
    lab = linear_srgb_to_oklab(s @ DISPLAY_P3_TO_SRGB.T)
    l = lab[..., 0]
    detail = l[1:-1, 1:-1] - (
        l[:-2, 1:-1] + l[2:, 1:-1] + l[1:-1, :-2] + l[1:-1, 2:]
    ) / 4
    ab = np.median(lab[..., 1:].reshape(-1, 2), axis=0)
    return {
        "sdr_y_p10_p50_p90": np.percentile(sy, [10, 50, 90]).tolist(),
        "hdr_y_p10_p50_p90": np.percentile(hy, [10, 50, 90]).tolist(),
        "sdr_oklab_median": np.median(lab.reshape(-1, 3), axis=0).tolist(),
        "sdr_chroma_median": float(np.median(np.hypot(lab[..., 1], lab[..., 2]))),
        "sdr_hue_of_median_ab": float(np.degrees(np.arctan2(ab[1], ab[0])) % 360),
        "local_contrast_l_p90_p10": float(np.percentile(l, 90) - np.percentile(l, 10)),
        "texture_or_noise_l_mad": float(np.median(np.abs(detail))),
        "hdr_gain_ev_p10_p50_p90": np.percentile(np.log2((hy+1/64)/(sy+1/64)), [10, 50, 90]).tolist(),
        "hdr_above_white_fraction": float(np.mean(hy > 1)),
    }


def _regions(phone_sdr, repo_sdr, phone_hdr, repo_hdr_p3, spec):
    result = {}
    for name, region in spec.get("regions", {}).items():
        phone_box = region["phone"]
        repo_box = region.get("repo", phone_box)
        result[name] = {
            "category": region["category"], "phone_box": phone_box, "repo_box": repo_box,
            "phone": _region_metrics(phone_sdr, phone_hdr, phone_box),
            "repo": _region_metrics(repo_sdr, repo_hdr_p3, repo_box),
        }
    return result


def _scene_preview(dng: Path, target: Path, tools: object) -> Path:
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="phone-audit-raw-") as temp:
        work = Path(temp)
        raw_work = work / "raw-work"
        raw_work.mkdir()
        developed = work / "scene.tif"
        develop_raw(
            dng.resolve(), developed, tools=tools, white_balance="camera",
            temperature_k=None, tint=1.0, work_dir=raw_work,
        )
        scene, info = open_scene(developed)
        stride = max(1, int(np.ceil(max(info.width, info.height) / 1024)))
        tifffile.imwrite(target, np.asarray(scene[::stride, ::stride], dtype=np.float32), photometric="rgb")
    return target


def _paired_error_metrics(row: dict[str, object]) -> dict[str, float]:
    """Independent diagnostics; no single score hides a color or HDR regression."""
    phone, repo = row["phone"], row["repo"]
    sdr_p = np.asarray(phone["sdr_percentiles"])
    sdr_r = np.asarray(repo["sdr_percentiles"])
    hdr_p = np.asarray(phone["hdr_percentiles"])
    hdr_r = np.asarray(repo["hdr_percentiles"])
    bands = []
    for name in ("shadows", "midtones", "upper_midtones", "highlights"):
        a = phone["gain_bands"][name]["median"]
        b = repo["gain_bands"][name]["median"]
        if a is not None and b is not None:
            bands.append(abs(np.log2(b / a)))
    return {
        **row["spatial"],
        "sdr_distribution_mae_ev": float(np.mean(np.abs(np.log2((sdr_r + 0.02) / (sdr_p + 0.02))))),
        "hdr_distribution_mae_ev": float(np.mean(np.abs(np.log2((hdr_r + 0.02) / (hdr_p + 0.02))))),
        "hdr_above_white_fraction_abs_error": abs(
            repo["hdr_above_white_fraction"] - phone["hdr_above_white_fraction"]
        ),
        "gain_band_mae_ev": float(np.mean(bands)) if bands else 0.0,
    }


def compare_audits(current: dict[str, object], reference: dict[str, object]) -> dict[str, object]:
    old = {row["stem"]: row for row in reference["pairs"]}
    by_scene = {}
    for row in current["pairs"]:
        if row["stem"] not in old:
            raise ValueError(f"Missing reference pair: {row['stem']}")
        before = _paired_error_metrics(old[row["stem"]])
        after = _paired_error_metrics(row)
        by_scene[row["stem"]] = {key: {"before": before[key], "after": after[key]} for key in before}
    keys = list(next(iter(by_scene.values())))
    aggregate = {
        key: {
            "before_median": float(np.median([item[key]["before"] for item in by_scene.values()])),
            "after_median": float(np.median([item[key]["after"] for item in by_scene.values()])),
        }
        for key in keys
    }
    return {"aggregate": aggregate, "by_scene": by_scene}


def audit(
    samples: Path, output: Path, cache: Path, baseline: Path | None,
    reference_audit: Path | None = None,
    *, keep_renders: bool = False, encode_check: bool = False,
    regions_path: Path | None = None,
) -> dict[str, object]:
    if (output / "audit.json").exists():
        raise FileExistsError(f"Refusing to overwrite an earlier audit: {output}")
    output.mkdir(parents=True, exist_ok=True)
    region_specs = json.loads(regions_path.read_text()) if regions_path else {}
    tools = resolve_tools(require_raw=True, require_exif=True)
    groups = {}
    for path in samples.iterdir():
        if ".RAW-" in path.name and path.suffix.lower() in {".dng", ".jpg"}:
            groups.setdefault(path.name.split(".RAW-", 1)[0], {})[path.suffix.lower()] = path
    records = []
    sheets: list[tuple[int, list[Image.Image]]] = []
    for number, (stem, paths) in enumerate(sorted(groups.items()), 1):
        if set(paths) != {".dng", ".jpg"}:
            raise ValueError(f"Incomplete pair: {stem}")
        scene = _scene_preview(paths[".dng"], cache / f"{stem}.tif", tools)
        with tempfile.TemporaryDirectory(prefix="phone-audit-") as temp:
            work = Path(temp)
            phone_hdr_path = work / "phone.rgba16f"
            run_checked(
                [tools.ultrahdr, "-m", "1", "-j", paths[".jpg"].resolve(), "-o", "0", "-O", "4", "-z", phone_hdr_path],
                label="decode phone Ultra HDR", timeout=600,
            )
            phone_sdr, phone_dimensions = _read_sdr(paths[".jpg"])
            phone_hdr = _read_hdr(phone_hdr_path, phone_dimensions)
            repo_sdr_path = work / "repo.jpg"
            repo_hdr_path = work / "repo.rgba16f"
            info = render_pair(
                scene, repo_sdr_path, repo_hdr_path,
                auto_exposure=True, exposure_ev=None,
                development_ev=RAW_DEVELOPMENT_EV,
                highlight_ev=0.0, hdr_strength=1.0, peak_nits=1000,
            )
            repo_sdr, repo_dimensions = _read_sdr(repo_sdr_path, step=1)
            repo_hdr = _read_hdr(repo_hdr_path, repo_dimensions, step=1)
            repo_hdr_p3 = repo_hdr @ REC2020_TO_DISPLAY_P3.T
            record = {
                "id": number, "stem": stem,
                "phone": _metrics(phone_sdr, phone_hdr, hdr_gamut="display-p3"),
                "repo": _metrics(repo_sdr, repo_hdr, hdr_gamut="rec2020"),
                "spatial": _spatial_metrics(
                    phone_sdr, repo_sdr, phone_hdr,
                    repo_hdr @ REC2020_TO_DISPLAY_P3.T,
                ),
                "resolved_tone": info.tone_mapping,
                "render": asdict(info),
                "scene": region_specs.get(str(number), {}).get("scene"),
                "regions": _regions(phone_sdr, repo_sdr, phone_hdr, repo_hdr_p3,
                                    region_specs.get(str(number), {})),
                "not_present": region_specs.get(str(number), {}).get("not_present", []),
            }
            if keep_renders or encode_check:
                target = output / f"{number:02}_{stem}"
                target.mkdir(parents=True, exist_ok=False)
                shutil.copy2(repo_sdr_path, target / "sdr.jpg")
                shutil.copy2(repo_hdr_path, target / "hdr.rgba16f")
                np.savez_compressed(target / "reference_preview.npz", sdr=phone_sdr, hdr=phone_hdr)
                panels = [
                    _panel(phone_sdr, hdr=False, width=520, height=720),
                    _panel(repo_sdr, hdr=False, width=520, height=720),
                    _panel(phone_hdr, hdr=True, width=520, height=720),
                    _panel(repo_hdr_p3, hdr=True, width=520, height=720),
                ]
                individual = Image.new("RGB", (520*4, 720), "#eeeeee")
                draw = ImageDraw.Draw(individual)
                for col, (label, panel) in enumerate(zip(
                    ("Phone SDR", "Candidate SDR", "Phone HDR preview", "Candidate HDR preview"), panels
                )):
                    individual.paste(panel, (col*520+(520-panel.width)//2, 4))
                    draw.text((col*520+10, 697), f"{number:02} {label}", fill="#111111")
                individual.save(target / "comparison.png")
                if encode_check:
                    encoded = target / "ultrahdr.jpg"
                    encode_ultrahdr(
                        repo_sdr_path, repo_hdr_path, encoded,
                        width=info.scene.width, height=info.scene.height,
                        peak_nits=info.peak_nits, max_boost=info.max_content_boost,
                        gainmap_quality=info.gainmap_quality,
                        sdr_gamut=info.style["sdr_gamut"], tools=tools,
                    )
                    validated = validate_ultrahdr(
                        encoded, width=info.scene.width, height=info.scene.height,
                        work_dir=work, tools=tools,
                    )
                    decoded = _read_hdr(work / "decoded_hdr.rgba16f", repo_dimensions, step=1)
                    desired_y, decoded_y = repo_hdr @ REC2020_TO_XYZ[1], decoded @ REC2020_TO_XYZ[1]
                    record["codec"] = {
                        **asdict(validated),
                        "decoded_luma_mae_ev": float(np.mean(np.abs(np.log2((decoded_y+0.02)/(desired_y+0.02))))),
                        "decoded_finite": bool(np.all(np.isfinite(decoded))),
                        "encoded_sha256": hashlib.sha256(encoded.read_bytes()).hexdigest(),
                    }
                (target / "record.json").write_text(json.dumps(record, indent=2)+"\n")
            records.append(record)
            sheets.append((number, [
                _panel(phone_sdr, hdr=False), _panel(repo_sdr, hdr=False),
                _panel(phone_hdr, hdr=True),
                _panel(repo_hdr @ REC2020_TO_DISPLAY_P3.T, hdr=True),
            ]))
        print(f"Audited {number:02} {stem}", flush=True)
    for start in range(0, len(sheets), 6):
        _contact_sheet(sheets[start:start + 6], output / f"contact_{start + 1:02}_{min(start + 6, len(sheets)):02}.png")
    report: dict[str, object] = {"pair_count": len(records), "pairs": records}
    if reference_audit is not None:
        report["comparison_to_reference"] = compare_audits(
            report, json.loads(reference_audit.read_text())
        )
    if baseline is not None:
        if baseline.is_file():
            old_by_stem = {
                item["stem"]: (
                    item["sdr_p50"], item["midtone_gain"], item["hdr_p995"]
                )
                for item in json.loads(baseline.read_text())["pairs"]
            }
        else:
            old_pairs = json.loads((baseline / "phone_clear_sample_analysis.json").read_text())["pairs"]
            old_gain = json.loads((baseline / "rendered_gain_report.json").read_text())["pairs"]
            old_by_stem = {
                item["stem"]: (
                    item["rendered_phone_clear"]["sdr"]["linear_luminance_percentiles"][1],
                    old_gain[index]["gain_bins"]["midtone"]["median_gain"],
                    item["rendered_phone_clear"]["hdr"]["linear_luminance_percentiles"][-1],
                )
                for index, item in enumerate(old_pairs)
            }
        errors = {key: {"before": [], "after": []} for key in ("sdr_p50", "mid_gain", "hdr_p995")}
        for row in records:
            old_sdr, old_mid, old_high = old_by_stem[row["stem"]]
            phone, repo = row["phone"], row["repo"]
            comparisons = {
                "sdr_p50": (old_sdr, repo["sdr_percentiles"][1], phone["sdr_percentiles"][1]),
                "mid_gain": (old_mid, repo["midtone_gain_median"], phone["midtone_gain_median"]),
                "hdr_p995": (old_high, repo["hdr_percentiles"][-1], phone["hdr_percentiles"][-1]),
            }
            for key, (before, after, reference) in comparisons.items():
                if before is not None and after is not None and reference > 0:
                    errors[key]["before"].append(abs(float(np.log2(before / reference))))
                    errors[key]["after"].append(abs(float(np.log2(after / reference))))
        report["comparison_to_baseline"] = {
            key: {
                "before_median_log2_error": float(np.median(values["before"])),
                "after_median_log2_error": float(np.median(values["after"])),
                "relative_error_reduction": float(1.0 - np.median(values["after"]) / np.median(values["before"])),
            }
            for key, values in errors.items()
        }
    output.mkdir(parents=True, exist_ok=True)
    (output / "audit.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("samples", type=Path)
    parser.add_argument("--output", type=Path, default=Path("outputs/phone_clear_audit"))
    parser.add_argument("--scene-cache", type=Path, default=Path("outputs/phone_clear_audit/scenes"))
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--reference-audit", type=Path,
                        help="Full previous audit.json, for spatial/color/HDR comparison")
    parser.add_argument("--keep-renders", action="store_true")
    parser.add_argument("--encode-check", action="store_true")
    parser.add_argument("--regions", type=Path)
    parser.add_argument("--skip-phone-color-refinement", action="store_true",
                        help="experimental ablation only; disables downstream phone hue corrections")
    parser.add_argument("--residual-models",type=Path,help="experimental held-out residual models")
    args = parser.parse_args()
    if args.skip_phone_color_refinement:
        import hdrimg.render as rendering
        rendering.refine_phone_color = lambda rgb, **kwargs: rgb
    if args.residual_models:
        from fit_phone_residual import apply_model
        original_render=render_pair
        def calibrated_render(scene,sdr,hdr,**kwargs):
            info=original_render(scene,sdr,hdr,**kwargs)
            return apply_model(info,sdr,hdr,args.residual_models/f'{scene.stem}.json')
        render_pair=calibrated_render
    result = audit(args.samples, args.output, args.scene_cache, args.baseline, args.reference_audit,
                   keep_renders=args.keep_renders, encode_check=args.encode_check, regions_path=args.regions)
    if "comparison_to_baseline" in result:
        print(json.dumps(result["comparison_to_baseline"], indent=2))
