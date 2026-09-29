from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .errors import ProcessingError
from .tools import ToolPaths, run_checked


@dataclass(frozen=True)
class UltraHdrValidation:
    probe_text: str
    sdr_decoded_bytes: int
    hdr_decoded_bytes: int
    hdr_peak_rgb: float
    hdr_peak_luminance: float
    hdr_above_reference_fraction: float


def encode_ultrahdr(
    sdr_jpeg: Path,
    hdr_raw: Path,
    output: Path,
    *,
    width: int,
    height: int,
    peak_nits: float,
    max_boost: float,
    gainmap_quality: int,
    sdr_gamut: str = "srgb",
    tools: ToolPaths,
) -> None:
    run_checked(
        [
            tools.ultrahdr,
            "-m",
            "0",
            "-i",
            sdr_jpeg,
            "-p",
            hdr_raw,
            "-w",
            str(width),
            "-h",
            str(height),
            "-a",
            "4",
            "-C",
            "2",
            "-c",
            "1" if sdr_gamut == "display-p3" else "0",
            "-t",
            "0",
            "-R",
            "1",
            "-s",
            "1",
            "-M",
            "1",
            "-Q",
            str(gainmap_quality),
            "-D",
            "1",
            "-k",
            "1",
            "-K",
            f"{max_boost:.8f}",
            "-L",
            f"{peak_nits:.3f}",
            "-z",
            output,
        ],
        label="Ultra HDR encoding",
        timeout=600,
    )
    if not output.is_file() or output.stat().st_size == 0:
        raise ProcessingError("Ultra HDR encoder did not create a non-empty output")


def probe_ultrahdr(path: Path, tools: ToolPaths) -> str:
    result = run_checked(
        [tools.ultrahdr, "-m", "1", "-j", path, "-P"],
        label=f"Ultra HDR probe for {path.name}",
        timeout=120,
    )
    text = (result.stdout + "\n" + result.stderr).strip()
    if not re.search(r"gain\s*map|gainmap|maxContentBoost|hdrCapacity", text, re.I):
        raise ProcessingError("Ultra HDR probe succeeded but reported no gain map metadata")
    return text


def validate_ultrahdr(
    path: Path,
    *,
    width: int,
    height: int,
    work_dir: Path,
    tools: ToolPaths,
) -> UltraHdrValidation:
    probe = probe_ultrahdr(path, tools)
    sdr_raw = work_dir / "decoded_sdr.rgba8888"
    hdr_raw = work_dir / "decoded_hdr.rgba16f"
    run_checked(
        [tools.ultrahdr, "-m", "1", "-j", path, "-o", "3", "-O", "3", "-z", sdr_raw],
        label="Ultra HDR SDR decode",
        timeout=600,
    )
    run_checked(
        [tools.ultrahdr, "-m", "1", "-j", path, "-o", "0", "-O", "4", "-z", hdr_raw],
        label="Ultra HDR linear HDR decode",
        timeout=600,
    )
    expected_sdr = width * height * 4
    expected_hdr = width * height * 8
    actual_sdr = sdr_raw.stat().st_size if sdr_raw.exists() else 0
    actual_hdr = hdr_raw.stat().st_size if hdr_raw.exists() else 0
    if actual_sdr != expected_sdr:
        raise ProcessingError(
            f"Decoded SDR size mismatch: expected {expected_sdr}, received {actual_sdr}"
        )
    if actual_hdr != expected_hdr:
        raise ProcessingError(
            f"Decoded HDR size mismatch: expected {expected_hdr}, received {actual_hdr}"
        )
    decoded = np.memmap(hdr_raw, dtype="<f2", mode="r", shape=(height, width, 4))
    peak_rgb = 0.0
    peak_luminance = 0.0
    above_reference = 0
    for start in range(0, height, 512):
        rgb = np.asarray(decoded[start : start + 512, :, :3], dtype=np.float32)
        peak_rgb = max(peak_rgb, float(np.max(rgb)))
        y = rgb @ np.array([0.2627, 0.6780, 0.0593], dtype=np.float32)
        peak_luminance = max(peak_luminance, float(np.max(y)))
        above_reference += int(np.count_nonzero(y > 1.0))
    del decoded
    return UltraHdrValidation(
        probe,
        actual_sdr,
        actual_hdr,
        peak_rgb,
        peak_luminance,
        above_reference / float(width * height),
    )
