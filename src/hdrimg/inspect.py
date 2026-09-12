from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image

from .errors import InputError
from .metadata import read_output_metadata
from .tools import resolve_tools
from .ultrahdr import validate_ultrahdr


def inspect_file(path: Path) -> dict[str, Any]:
    source = path.expanduser().resolve()
    if not source.is_file():
        raise InputError(f"File does not exist: {path}")
    tools = resolve_tools(require_raw=False, require_exif=True)
    with Image.open(source) as image:
        width, height = image.size
        base = {
            "format": image.format,
            "dimensions": [width, height],
            "icc_profile_present": bool(image.info.get("icc_profile")),
        }
    with tempfile.TemporaryDirectory(prefix="hdrimg-inspect-") as name:
        validation = validate_ultrahdr(
            source,
            width=width,
            height=height,
            work_dir=Path(name),
            tools=tools,
        )
    return {
        "path": os.fspath(source),
        "size_bytes": source.stat().st_size,
        "base_image": base,
        "gain_map_probe": validation.probe_text,
        "decoded_sdr_bytes": validation.sdr_decoded_bytes,
        "decoded_hdr_bytes": validation.hdr_decoded_bytes,
        "decoded_hdr_peak_rgb": validation.hdr_peak_rgb,
        "decoded_hdr_peak_luminance": validation.hdr_peak_luminance,
        "decoded_hdr_above_reference_fraction": validation.hdr_above_reference_fraction,
        "visible_hdr_content": validation.hdr_peak_luminance > 1.0,
        "metadata": read_output_metadata(source, tools),
        "valid": True,
    }


def inspection_as_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)
