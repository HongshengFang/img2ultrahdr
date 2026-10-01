"""CLI registration without importing optional inference dependencies."""
from __future__ import annotations

import argparse
from pathlib import Path

from ..errors import DependencyError, InputError, ProcessingError


def add_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser(
        "jpeg-hdr", help="experimental JPEG luminance expansion (optional research models)"
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="output Ultra HDR JPEG filename")
    parser.add_argument("--ai-size", type=int, default=768)
    parser.add_argument("--max-ev", type=float, default=2.5)
    parser.add_argument("--strength", type=float, default=1.0)
    parser.add_argument("--look", choices=("conservative", "phone"), default="conservative",
                        help="phone lifts midtones and pale materials in HDR only")
    parser.add_argument("--peak-nits", type=float, default=1000.,
                        help="phone look's authored peak, relative to 203-nit SDR white")
    parser.add_argument("--protect", type=Path, help="SAM2 point/box protection JSON")
    parser.add_argument("--fp32", action="store_true")
    parser.add_argument("--overwrite", action="store_true")


def run_command(args: argparse.Namespace) -> int:
    try:
        from .pipeline import run
    except ImportError as exc:
        raise DependencyError(
            "JPEG research dependencies are missing. See docs/jpeg-hdr.md and "
            "scripts/jpeg_hdr/setup_windows.ps1."
        ) from exc
    try:
        run(args)
    except (ValueError, FileExistsError, FileNotFoundError) as exc:
        raise InputError(str(exc)) from exc
    except ImportError as exc:
        raise DependencyError(f"JPEG research model dependency missing: {exc}") from exc
    except (RuntimeError, OSError) as exc:
        raise ProcessingError(str(exc)) from exc
    return 0
