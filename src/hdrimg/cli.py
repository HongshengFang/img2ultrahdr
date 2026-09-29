from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .doctor import checks_as_json, run_doctor
from .errors import DependencyError, HdrImgError, InputError
from .inspect import inspect_file, inspection_as_json
from .pipeline import RenderOptions, render_raw
from .style import DEFAULT_STYLE_NAME, STYLE_PRESETS


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="img2uhdr",
        description="Develop CR2/RAF files into controlled SDR and Ultra HDR images.",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("doctor", help="check dependencies and an Ultra HDR round trip")

    render = commands.add_parser("render", help="render one or more CR2/RAF files")
    render.add_argument("inputs", nargs="+", type=Path)
    render.add_argument("--output", type=Path, default=Path("."))
    render.add_argument(
        "--auto-look",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="automatically choose exposure compensation, contrast, and saturation",
    )
    render.add_argument(
        "--auto-exposure", action=argparse.BooleanOptionalAction, default=True
    )
    render.add_argument(
        "--exposure-ev", type=float, help="override automatic look exposure compensation"
    )
    render.add_argument(
        "--sdr-exposure-ev",
        type=float,
        default=0.0,
        help="adjust SDR brightness independently of HDR (default: 0 EV)",
    )
    render.add_argument(
        "--white-balance", choices=("camera", "auto", "custom"),
        help="RAW white balance (default: auto for phone-clear/phone-natural, camera for natural)",
    )
    render.add_argument("--temperature-k", type=int)
    render.add_argument("--tint", type=float, default=1.0)
    render.add_argument("--highlight-ev", type=float, default=0.0)
    render.add_argument("--hdr-strength", type=float, default=1.0)
    render.add_argument("--peak-nits", type=int, default=1000)
    render.add_argument("--contrast", type=float, help="override automatic contrast")
    render.add_argument("--saturation", type=float, help="override automatic saturation")
    render.add_argument(
        "--style",
        choices=tuple(STYLE_PRESETS),
        default=DEFAULT_STYLE_NAME,
        help=f"rendering style (default: {DEFAULT_STYLE_NAME})",
    )
    render.add_argument("--midtone-lift-ev", type=float)
    render.add_argument("--highlight-rolloff", type=float)
    render.add_argument("--local-contrast", type=float)
    render.add_argument("--vibrance", type=float)
    render.add_argument("--sdr-gamut", choices=("srgb", "display-p3"))
    render.add_argument(
        "--sdr-adaptation-strength", type=float,
        help="phone-clear/phone-natural scene-based SDR adjustment (0..1; default: 1)",
    )
    render.add_argument(
        "--hdr-midtone-gain", type=float,
        help="phone-clear/phone-natural HDR/SDR midtone gain (1..3; default: scene-based)",
    )
    render.add_argument(
        "--hdr-shoulder-strength", type=float,
        help="phone-clear/phone-natural HDR highlight compression (0..1; default: 0.7)",
    )
    render.add_argument(
        "--subject-adaptation-strength", type=float,
        help="phone-clear/phone-natural local subject tone using macOS Vision (0..1; default: 1)",
    )
    render.add_argument(
        "--skin-protection-strength", type=float,
        help="phone-clear/phone-natural skin protection during automatic white correction (0..1; default: 1)",
    )
    render.add_argument(
        "--raw-denoise-strength", type=float,
        help="phone-clear/phone-natural RAW noise reduction (0..1; default: 1 with automatic look, otherwise 0)",
    )
    render.add_argument(
        "--raw-detail-strength", type=float,
        help="phone-clear/phone-natural restrained RAW deconvolution (0..1; default: 1 with automatic look and RAW denoise)",
    )
    render.add_argument(
        "--surface-denoise-strength", type=float,
        help="phone-clear/phone-natural smooth blue-surface noise reduction (0..1; default: follows RAW denoise)",
    )
    render.add_argument(
        "--warm-color-separation",
        type=float,
        default=0.0,
        help="separate lighter warm tones from darker auburn tones (0..1)",
    )
    render.add_argument("--strip-metadata", action="store_true")
    render.add_argument("--keep-intermediates", action="store_true")
    render.add_argument("--overwrite", action="store_true")

    inspect = commands.add_parser("inspect", help="validate an Ultra HDR image")
    inspect.add_argument("file", type=Path)
    return parser


def _render_command(args: argparse.Namespace) -> int:
    stems: dict[str, Path] = {}
    for source in args.inputs:
        key = source.stem.casefold()
        if key in stems and stems[key] != source:
            raise InputError(
                f"Inputs {stems[key]} and {source} would create the same output names"
            )
        stems[key] = source

    options = RenderOptions(
        output=args.output,
        auto_look=args.auto_look,
        auto_exposure=args.auto_exposure,
        exposure_ev=args.exposure_ev,
        sdr_exposure_ev=args.sdr_exposure_ev,
        white_balance=args.white_balance,
        temperature_k=args.temperature_k,
        tint=args.tint,
        highlight_ev=args.highlight_ev,
        hdr_strength=args.hdr_strength,
        peak_nits=args.peak_nits,
        contrast=args.contrast,
        saturation=args.saturation,
        warm_color_separation=args.warm_color_separation,
        style=args.style,
        midtone_lift_ev=args.midtone_lift_ev,
        highlight_rolloff=args.highlight_rolloff,
        local_contrast=args.local_contrast,
        vibrance=args.vibrance,
        sdr_gamut=args.sdr_gamut,
        sdr_adaptation_strength=args.sdr_adaptation_strength,
        hdr_midtone_gain=args.hdr_midtone_gain,
        hdr_shoulder_strength=args.hdr_shoulder_strength,
        subject_adaptation_strength=args.subject_adaptation_strength,
        skin_protection_strength=args.skin_protection_strength,
        raw_denoise_strength=args.raw_denoise_strength,
        raw_detail_strength=args.raw_detail_strength,
        surface_denoise_strength=args.surface_denoise_strength,
        strip_metadata=args.strip_metadata,
        keep_intermediates=args.keep_intermediates,
        overwrite=args.overwrite,
    )
    options.validate()
    failures: list[tuple[Path, str]] = []
    for index, source in enumerate(args.inputs, start=1):
        print(f"[{index}/{len(args.inputs)}] Rendering {source} …", flush=True)
        try:
            result = render_raw(source, options)
        except HdrImgError as exc:
            failures.append((source, str(exc)))
            print(f"  Failed: {exc}", file=sys.stderr)
            continue
        print(f"  SDR:       {result.sdr}")
        print(f"  Ultra HDR: {result.ultrahdr}")
        print(f"  Manifest:  {result.manifest}")
        look = getattr(result, "look", None)
        if look:
            print(
                "  Look:      "
                f"{look['exposure_ev']:+.2f} EV "
                f"({look['exposure_source']}), "
                f"contrast {look['contrast']:.2f} "
                f"({look['contrast_source']}), "
                f"saturation {look['saturation']:.2f} "
                f"({look['saturation_source']})"
            )
    if failures:
        print(f"\n{len(failures)} input(s) failed:", file=sys.stderr)
        for source, detail in failures:
            print(f"- {source}: {detail}", file=sys.stderr)
        return 4
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "doctor":
            ok, checks = run_doctor()
            print(checks_as_json(checks))
            return 0 if ok else 3
        if args.command == "inspect":
            print(inspection_as_json(inspect_file(args.file)))
            return 0
        if args.command == "render":
            return _render_command(args)
    except DependencyError as exc:
        print(f"Dependency error: {exc}", file=sys.stderr)
        return 3
    except InputError as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2
    except HdrImgError as exc:
        print(f"Processing error: {exc}", file=sys.stderr)
        return 4
    return 1
