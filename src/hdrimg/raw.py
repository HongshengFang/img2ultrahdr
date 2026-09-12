from __future__ import annotations

import base64
from importlib.resources import files
from pathlib import Path

from .errors import InputError, ProcessingError
from .tools import ToolPaths, run_checked

SUPPORTED_RAW_EXTENSIONS = {".cr2", ".raf"}
LINEAR_REC2020_PROFILE_NAME = "Rec2020-elle-V4-g10"
# RawTherapee's ICC output stage can clamp values at its nominal white even for
# float TIFF. Develop with two stops of headroom, then compensate for it in the
# scene-linear exposure calculation. This preserves real sensor highlight data.
RAW_DEVELOPMENT_EV = -2.0


def validate_raw_input(path: Path) -> Path:
    source = path.expanduser().resolve()
    if not source.is_file():
        raise InputError(f"Input does not exist or is not a file: {path}")
    if source.suffix.lower() not in SUPPORTED_RAW_EXTENSIONS:
        raise InputError(
            f"Unsupported input extension {source.suffix!r}; v1 supports CR2 and RAF"
        )
    return source


def _white_balance_overlay(
    mode: str, temperature_k: int | None, tint: float
) -> str:
    setting = {"camera": "Camera", "auto": "Auto", "custom": "Custom"}[mode]
    lines = ["[White Balance]", f"Setting={setting}", f"Green={tint:.6f}"]
    if mode == "custom":
        if temperature_k is None:
            raise InputError("--temperature-k is required with --white-balance custom")
        lines.append(f"Temperature={temperature_k}")
    lines.extend(("", "[Exposure]", f"Compensation={RAW_DEVELOPMENT_EV:.6f}"))
    return "\n".join(lines) + "\n"


def develop_raw(
    source: Path,
    destination: Path,
    *,
    tools: ToolPaths,
    white_balance: str,
    temperature_k: int | None,
    tint: float,
    work_dir: Path,
) -> None:
    profile = Path(str(files("hdrimg").joinpath("profiles/raw-unclipped.pp3")))
    profile_data = Path(
        str(files("hdrimg").joinpath("profiles/Rec2020-elle-V4-g10.icc.b64"))
    )
    settings = work_dir / "rawtherapee-settings"
    cache = work_dir / "rawtherapee-cache"
    settings.mkdir()
    cache.mkdir()
    icc_path = settings / f"{LINEAR_REC2020_PROFILE_NAME}.icc"
    try:
        icc_path.write_bytes(base64.b64decode(profile_data.read_text(encoding="ascii")))
    except (OSError, ValueError) as exc:
        raise ProcessingError(f"Cannot prepare linear Rec.2020 profile: {exc}") from exc
    overlay = work_dir / "white-balance.pp3"
    overlay.write_text(
        _white_balance_overlay(white_balance, temperature_k, tint), encoding="utf-8"
    )
    run_checked(
        [
            tools.rawtherapee,
            "-o",
            destination,
            "-p",
            profile,
            "-p",
            overlay,
            "-t",
            "-b32",
            "-Y",
            "-c",
            source,
        ],
        label=f"RAW development for {source.name}",
        timeout=600,
        env={"RT_SETTINGS": str(settings), "RT_CACHE": str(cache)},
    )
    if not destination.is_file():
        alternate = destination.with_suffix(".tif")
        if alternate.is_file():
            alternate.replace(destination)
        else:
            raise ProcessingError(
                f"RawTherapee completed without creating {destination.name}"
            )
    # RawTherapee applies custom output profiles but does not embed them in
    # float TIFF exports. Attach the exact profile so the intermediate remains
    # self-describing and can be independently inspected.
    run_checked(
        [
            tools.exiftool,
            "-overwrite_original",
            f"-icc_profile<={icc_path}",
            destination,
        ],
        label="linear Rec.2020 ICC attachment",
        timeout=120,
    )
