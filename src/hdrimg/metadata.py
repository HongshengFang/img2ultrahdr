from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .tools import ToolPaths, run_checked

MANIFEST_TAGS = (
    "Make",
    "Model",
    "LensModel",
    "ISO",
    "ExposureTime",
    "FNumber",
    "FocalLength",
    "DateTimeOriginal",
    "ImageWidth",
    "ImageHeight",
    "Orientation",
)


def read_source_metadata(source: Path, tools: ToolPaths) -> dict[str, Any]:
    result = run_checked(
        [tools.exiftool, "-j", "-n", *[f"-{tag}" for tag in MANIFEST_TAGS], source],
        label=f"metadata read for {source.name}",
        timeout=60,
    )
    records = json.loads(result.stdout)
    if not records:
        return {}
    record = records[0]
    record.pop("SourceFile", None)
    return record


def copy_metadata(source: Path, jpeg: Path, tools: ToolPaths) -> None:
    run_checked(
        [
            tools.exiftool,
            "-overwrite_original",
            "-TagsFromFile",
            source,
            "-EXIF:all",
            "-IPTC:all",
            "-XMP:all",
            # The trailing # forces numeric assignment. Without it ExifTool treats
            # "1" as a print-converted label and can resolve it to Rotate 180.
            "-Orientation#=1",
            "-PreviewImage=",
            "-JpgFromRaw=",
            "-OtherImage=",
            "-ThumbnailImage=",
            jpeg,
        ],
        label=f"metadata copy for {source.name}",
        timeout=120,
    )


def read_output_metadata(jpeg: Path, tools: ToolPaths) -> dict[str, Any]:
    result = run_checked(
        [tools.exiftool, "-j", "-n", "-Make", "-Model", "-Orientation", jpeg],
        label=f"metadata verification for {jpeg.name}",
        timeout=60,
    )
    records = json.loads(result.stdout)
    if not records:
        return {}
    record = records[0]
    record.pop("SourceFile", None)
    return record
