from __future__ import annotations

import os
import shutil
import subprocess
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

# Optional per-process observer; CLI behavior is unchanged when unset.
PROGRESS_CALLBACK = None

from .errors import DependencyError, ProcessingError


@dataclass(frozen=True)
class ToolPaths:
    rawtherapee: Path
    ultrahdr: Path
    exiftool: Path


def _brew_prefix(formula: str) -> Path | None:
    brew = shutil.which("brew")
    if not brew:
        return None
    result = subprocess.run(
        [brew, "--prefix", formula], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip())


def find_tool(name: str) -> Path | None:
    override = os.environ.get("HDRIMG_TOOL_" + name.upper().replace("-", "_"))
    if override:
        path = Path(override)
        return path.resolve() if path.is_file() and os.access(path, os.X_OK) else None
    direct = shutil.which(name)
    if direct:
        return Path(direct).resolve()
    if name == "ultrahdr_app":
        prefix = _brew_prefix("libultrahdr")
        candidate = prefix / "bin" / name if prefix else None
        if candidate and candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    return None


def resolve_tools(require_raw: bool = True, require_exif: bool = True) -> ToolPaths:
    raw = find_tool("rawtherapee-cli")
    ultra = find_tool("ultrahdr_app")
    exif = find_tool("exiftool")
    missing: list[str] = []
    if require_raw and not raw:
        missing.append("rawtherapee-cli")
    if not ultra:
        missing.append("ultrahdr_app (Homebrew libultrahdr)")
    if require_exif and not exif:
        missing.append("exiftool")
    if missing:
        raise DependencyError("Missing required tools: " + ", ".join(missing))
    return ToolPaths(
        rawtherapee=raw or Path("rawtherapee-cli"),
        ultrahdr=ultra or Path("ultrahdr_app"),
        exiftool=exif or Path("exiftool"),
    )


def run_checked(
    args: Sequence[str | os.PathLike[str]],
    *,
    label: str,
    timeout: int | None = None,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [os.fspath(part) for part in args]
    if PROGRESS_CALLBACK is not None:
        PROGRESS_CALLBACK(label)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env={**os.environ, **env} if env else None,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProcessingError(f"{label} failed: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ProcessingError(f"{label} failed ({result.returncode}): {detail}")
    return result


def tool_version(path: Path, args: Sequence[str]) -> str:
    try:
        result = subprocess.run(
            [os.fspath(path), *args], capture_output=True, text=True, check=False, timeout=15
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    text = (result.stdout + "\n" + result.stderr).strip()
    return next((line.strip() for line in text.splitlines() if line.strip()), "unknown")


def ultrahdr_version(path: Path) -> str:
    try:
        result = subprocess.run(
            [os.fspath(path), "-m", "-1"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    match = re.search(r"lib version:\s*v([^\s]+)", result.stdout + result.stderr)
    return match.group(1) if match else "unknown"
