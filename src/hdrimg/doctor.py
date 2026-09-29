from __future__ import annotations

import base64
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from importlib.resources import files
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms

from .render import write_rgba16f
from .tools import ToolPaths, find_tool, tool_version, ultrahdr_version
from .ultrahdr import encode_ultrahdr, validate_ultrahdr


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def _version_tuple(text: str) -> tuple[int, ...]:
    match = re.search(r"(\d+(?:\.\d+)+)", text)
    return tuple(int(part) for part in match.group(1).split(".")) if match else ()


def run_doctor() -> tuple[bool, list[Check]]:
    checks: list[Check] = []
    raw = find_tool("rawtherapee-cli")
    ultra = find_tool("ultrahdr_app")
    exif = find_tool("exiftool")
    raw_version = tool_version(raw, ["-v"]) if raw else "not found"
    ultra_version = ultrahdr_version(ultra) if ultra else "not found"
    exif_version = tool_version(exif, ["-ver"]) if exif else "not found"
    checks.append(
        Check(
            "RawTherapee",
            bool(raw) and _version_tuple(raw_version) >= (5, 13),
            f"{raw_version} ({raw})" if raw else "not found",
        )
    )
    checks.append(
        Check(
            "libultrahdr",
            bool(ultra) and _version_tuple(ultra_version) >= (2, 0, 2),
            f"{ultra_version} ({ultra})" if ultra else "not found",
        )
    )
    checks.append(
        Check(
            "ExifTool",
            bool(exif) and bool(_version_tuple(exif_version)),
            f"{exif_version} ({exif})" if exif else "not found",
        )
    )
    packaged_profile = Path(str(files("hdrimg").joinpath("profiles/raw-unclipped.pp3")))
    checks.append(Check("RAW profile", packaged_profile.is_file(), os.fspath(packaged_profile)))
    icc_data = Path(
        str(files("hdrimg").joinpath("profiles/Rec2020-elle-V4-g10.icc.b64"))
    )
    try:
        decoded_icc = base64.b64decode(icc_data.read_text(encoding="ascii"))
        valid_icc = len(decoded_icc) >= 128 and decoded_icc[36:40] == b"acsp"
    except (OSError, ValueError):
        valid_icc = False
    checks.append(Check("linear Rec.2020 ICC", valid_icc, os.fspath(icc_data)))
    p3_profile = Path("/System/Library/ColorSync/Profiles/Display P3.icc")
    checks.append(Check("Display P3 ICC", p3_profile.is_file(), os.fspath(p3_profile)))

    try:
        with tempfile.TemporaryDirectory(prefix="hdrimg-doctor-") as name:
            work = Path(name)
            test_file = work / "write-test"
            test_file.write_text("ok", encoding="ascii")
            checks.append(Check("temporary storage", test_file.read_text() == "ok", name))
            if ultra:
                width, height = 32, 16
                ramp = np.linspace(0.0, 1.0, width, dtype=np.float32)
                sdr_gray = np.broadcast_to(ramp, (height, width))
                sdr_rgb = np.repeat(sdr_gray[..., None], 3, axis=-1)
                encoded = np.where(
                    sdr_rgb <= 0.0031308,
                    12.92 * sdr_rgb,
                    1.055 * np.power(sdr_rgb, 1 / 2.4) - 0.055,
                )
                sdr_bytes = np.clip(encoded * 255 + 0.5, 0, 255).astype(np.uint8)
                profile = (
                    p3_profile.read_bytes()
                    if p3_profile.is_file()
                    else ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
                )
                sdr_path = work / "doctor-sdr.jpg"
                Image.fromarray(sdr_bytes, mode="RGB").save(
                    sdr_path, quality=95, subsampling=0, icc_profile=profile
                )
                hdr_path = work / "doctor-hdr.rgba16f"
                hdr_rgb = sdr_rgb * 2.0
                write_rgba16f(hdr_path, hdr_rgb)
                output = work / "doctor-ultrahdr.jpg"
                tools = ToolPaths(
                    rawtherapee=raw or Path("rawtherapee-cli"),
                    ultrahdr=ultra,
                    exiftool=exif or Path("exiftool"),
                )
                encode_ultrahdr(
                    sdr_path,
                    hdr_path,
                    output,
                    width=width,
                    height=height,
                    peak_nits=1000,
                    max_boost=1000 / 203,
                    gainmap_quality=95,
                    sdr_gamut="display-p3" if p3_profile.is_file() else "srgb",
                    tools=tools,
                )
                validation = validate_ultrahdr(
                    output, width=width, height=height, work_dir=work, tools=tools
                )
                checks.append(
                    Check(
                        "Ultra HDR round trip",
                        validation.sdr_decoded_bytes == width * height * 4,
                        f"{output.stat().st_size} byte encoded image",
                    )
                )
    except Exception as exc:
        checks.append(Check("Ultra HDR round trip", False, str(exc)))

    return all(check.ok for check in checks), checks


def checks_as_json(checks: list[Check]) -> str:
    return json.dumps([asdict(check) for check in checks], indent=2, ensure_ascii=False)
