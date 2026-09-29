"""Diagnostic near-black cast correction; no default processing uses this file."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import tifffile

from hdrimg.phone_tone import phone_scene_decision
from hdrimg.raw import RAW_DEVELOPMENT_EV
from hdrimg.render import open_scene
from hdrimg.tone import luminance_rec2020


def prepare(source: Path, output: Path, mode: str):
    output.mkdir(parents=True, exist_ok=False)
    records = []
    for path in sorted(source.glob("*.tif")):
        scene, _ = open_scene(path)
        raw = np.asarray(scene, dtype=np.float32) * 2**-RAW_DEVELOPMENT_EV
        y = luminance_rec2020(raw)
        cue = phone_scene_decision(scene, development_ev=RAW_DEVELOPMENT_EV, peak_nits=1000)
        t = np.clip((cue.dark_fraction - .10)/.15, 0, 1)
        weight = cue.dark_weight * t*t*(3-2*t)
        dest = output / path.name
        offset = np.zeros(3, dtype=np.float32)
        if weight:
            lo, hi = np.percentile(y, [1, 5])
            near_black = np.median(raw[(y >= lo) & (y <= hi)], axis=0)
            if mode == "red":
                offset[0] = max(0, near_black[0] - near_black[1])
            else:
                offset = np.maximum(near_black - np.min(near_black), 0)
            offset = np.minimum(offset, .012) * weight
            # This is a bounded look correction, not a sensor black-level claim.
            t = np.clip((y - .02)/.06, 0, 1)
            fade = 1 - t*t*(3-2*t)
            corrected = np.maximum(raw - fade[..., None]*offset, 0) * 2**RAW_DEVELOPMENT_EV
            tifffile.imwrite(dest, corrected.astype(np.float32), photometric="rgb")
        else:
            dest.symlink_to(path.resolve())
        records.append({"source": str(path.resolve()), "mode": mode,
                        "strength": float(weight), "offset_rec2020": offset.tolist(),
                        "unchanged": not bool(weight)})
    (output / "transform.json").write_text(json.dumps(records, indent=2)+"\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--mode", choices=["red", "neutral"], default="red")
    a = p.parse_args()
    prepare(a.source, a.output, a.mode)
