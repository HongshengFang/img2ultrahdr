"""Optional, local-only subject tone cues for phone-clear.

Vision estimates where people and faces are. It does not identify people. A
missing tool, failed request, or unreliable detection leaves the image alone.
The tone fields are built from an independent SDR reference so changing the
fallback exposure cannot change the desired HDR rendition.
"""
from __future__ import annotations

import hashlib
from collections import OrderedDict
from copy import deepcopy
import json
import os
import platform
import shutil
import subprocess
import tempfile
from importlib.resources import files
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFilter

from .color import DISPLAY_P3_TO_SRGB, DISPLAY_P3_TO_XYZ, SRGB_TO_XYZ, linear_srgb_to_oklab


def _vision_helper() -> Path | None:
    bundled = os.environ.get("HDRIMG_VISION_HELPER")
    if bundled:
        path = Path(bundled)
        if path.is_file() and os.access(path, os.X_OK):
            return path
    if platform.system() != "Darwin":
        return None
    compiler = shutil.which("swiftc")
    if not compiler:
        return None
    source = Path(str(files("hdrimg").joinpath("profiles/phone-subject.swift")))
    key = hashlib.sha256(source.read_bytes() + platform.mac_ver()[0].encode()).hexdigest()[:20]
    cache = Path.home() / "Library/Caches/hdrimg/vision" / key
    binary = cache / "phone-subject"
    if binary.is_file():
        return binary
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="build-", dir=cache) as temp:
        staged = Path(temp) / "phone-subject"
        subprocess.run([compiler, "-O", str(source), "-o", str(staged)],
                       check=True, capture_output=True, timeout=120)
        os.replace(staged, binary)
    return binary


def subject_tone_fields(
    sdr: np.ndarray, matte: np.ndarray, faces: list[dict[str, float]], *,
    high_key_weight: float, strength: float, gamut: str, indoor_weight: float = 0,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Return HDR luminance ratio and SDR odds ratio from fixed reference data."""
    h, w = sdr.shape[:2]
    matte = np.clip(np.nan_to_num(matte, nan=0, posinf=0, neginf=0), 0, 1)
    yy, xx = np.mgrid[:h, :w].astype(np.float32)
    xx /= w
    yy /= h
    luma = sdr @ (DISPLAY_P3_TO_XYZ[1] if gamut == "display-p3" else SRGB_TO_XYZ[1])
    srgb = sdr @ DISPLAY_P3_TO_SRGB.T if gamut == "display-p3" else sdr
    lab = linear_srgb_to_oklab(srgb)
    relative_chroma = np.hypot(lab[..., 1], lab[..., 2]) / np.maximum(lab[..., 0], .05)
    head = np.zeros((h, w), np.float32)
    face_weight = head.copy()
    medians = []
    reliable = []
    for face in sorted(faces, key=lambda f: f.get("confidence", 0), reverse=True):
        x, y, bw, bh = (float(face[k]) for k in ("x", "y", "width", "height"))
        if (not np.all(np.isfinite([x, y, bw, bh, face["confidence"]]))
                or min(x,y) < 0 or x+bw > 1.001 or y+bh > 1.001
                or bw <= 0 or bh <= 0 or face["confidence"] < .55 or bw * bh < .0005):
            continue
        duplicate = False
        for previous in reliable:
            px,py,pw,ph = (previous[k] for k in ("x","y","width","height"))
            intersection = max(0,min(x+bw,px+pw)-max(x,px))*max(0,min(y+bh,py+ph)-max(y,py))
            if intersection/max(bw*bh+pw*ph-intersection,1e-8) > .5:
                duplicate = True
                break
        if duplicate:
            continue
        sy = slice(max(0, round((y+.2*bh)*h)),min(h, round((y+.9*bh)*h)))
        sx = slice(max(0, round((x+.2*bw)*w)),min(w, round((x+.8*bw)*w)))
        area = luma[sy,sx]
        if area.size < 4 or float(np.mean(matte[sy,sx])) < .3:
            continue
        medians.append(float(np.median(area)))
        reliable.append(face)
        dx, dy = (xx-(x+bw/2))/bw, (yy-(y+.35*bh))/bh
        head = np.maximum(head, np.exp(-1.6*(dx*dx+(dy/1.6)**2)))
        face_weight = np.maximum(face_weight, np.exp(-2*(dx*dx+((yy-(y+.60*bh))/bh)**2)))
    ones = np.ones((h, w), dtype=np.float32)
    if not medians or strength <= 0:
        return ones, ones.copy(), {"status": "no_reliable_subject", "face_count": 0}
    median = float(np.median(medians))
    shadow = np.clip((median-luma)/max(median, .03), 0, 1)
    face_excess_ev = float(np.clip(np.log2(max(median, .01)/.27), 0, .65)) * (1-indoor_weight)
    darken = (-.30*face_weight-.50*head*shadow
              -face_excess_ev*(face_weight+.5*head*shadow))*matte*strength
    neutral = np.clip((.09-relative_chroma)/.06, 0, 1)
    light = np.clip((luma-.10)/.14, 0, 1)
    garment = matte*(1-np.clip(head*1.7, 0, 1))*neutral*light
    gain = np.exp2(1.25*high_key_weight*garment*strength)
    desired = luma*gain/(1-luma+luma*gain)*np.exp2(darken)
    hdr_ratio = np.divide(desired, luma, out=ones.copy(), where=luma > 1e-6)
    sdr_odds = hdr_ratio*(1-luma)/np.maximum(1-desired, 1e-5)
    sdr_odds = np.where(luma >= 1-1e-5, 1, sdr_odds).astype(np.float32)
    return hdr_ratio, sdr_odds, {
        "status": "applied", "face_count": len(reliable), "faces": reliable,
        "reference_face_y": median, "strength": float(strength),
        "face_excess_ev": face_excess_ev,
        "hdr_ratio_range": [float(np.min(hdr_ratio)), float(np.max(hdr_ratio))],
    }


def _detect_subject_fields_uncached(
    reference: Path, *, high_key_weight: float, strength: float, gamut: str, indoor_weight: float = 0,
) -> tuple[Image.Image | None, Image.Image | None, dict[str, Any]]:
    try:
        helper = _vision_helper()
        if helper is None:
            return None, None, {"status": "unavailable", "reason": "macOS Vision helper unavailable"}
        with tempfile.TemporaryDirectory(prefix="hdrimg-subject-") as temp:
            prefix = Path(temp) / "subject"
            subprocess.run([str(helper), str(reference.resolve()), str(prefix)],
                           check=True, capture_output=True, timeout=60)
            observations = json.loads(prefix.with_suffix(".json").read_text())
            with Image.open(reference) as image:
                original = image.convert("RGB")
                icc = image.info.get("icc_profile")
                encoded = np.asarray(image.convert("RGB"), dtype=np.float32)/255
            sdr = np.where(encoded <= .04045, encoded/12.92, ((encoded+.055)/1.055)**2.4)
            with Image.open(str(prefix)+"_person.png") as image:
                matte = np.asarray(image.convert("L").resize(
                    (sdr.shape[1], sdr.shape[0]), Image.Resampling.BILINEAR
                ).filter(ImageFilter.GaussianBlur(1)), dtype=np.float32)/255
            retry_count = 0
            if not observations["faces"]:
                # Whole-image detectors can miss a small person. Retry four
                # overlapping views and accept mattes only where a face also
                # supports a person. No reference-image regions are used.
                for index, (x0,y0,x1,y1) in enumerate((
                    (0,0,2/3,2/3),(1/3,0,1,2/3),
                    (0,1/3,2/3,1),(1/3,1/3,1,1),
                )):
                    w,h = original.size
                    box = tuple(round(v*s) for v,s in zip((x0,y0,x1,y1),(w,h,w,h)))
                    tile = original.crop(box)
                    tile_path = Path(temp)/f"tile-{index}.jpg"
                    tile.save(tile_path,quality=95,subsampling=0,icc_profile=icc)
                    tile_prefix = Path(temp)/f"tile-{index}"
                    extra = ['--tile-faces-first'] if os.environ.get('HDRIMG_VISION_FAST_TILES') == '1' else []
                    subprocess.run([str(helper),str(tile_path),str(tile_prefix),*extra],
                                   check=True,capture_output=True,timeout=60)
                    found = json.loads(tile_prefix.with_suffix(".json").read_text())["faces"]
                    retry_count += 1
                    accepted = [f for f in found if f["confidence"] >= .65]
                    if not accepted:
                        continue
                    with Image.open(str(tile_prefix)+"_person.png") as image:
                        local = np.asarray(image.convert("L").resize(tile.size,Image.Resampling.BILINEAR)
                                           .filter(ImageFilter.GaussianBlur(1)),dtype=np.float32)/255
                    for face in accepted:
                        observations["faces"].append({
                            **face,
                            "x":(box[0]+face["x"]*tile.width)/w,
                            "y":(box[1]+face["y"]*tile.height)/h,
                            "width":face["width"]*tile.width/w,
                            "height":face["height"]*tile.height/h,
                        })
                    matte[box[1]:box[3],box[0]:box[2]] = np.maximum(
                        matte[box[1]:box[3],box[0]:box[2]],local)
            hdr, odds, record = subject_tone_fields(
                sdr, matte, observations["faces"], high_key_weight=high_key_weight,
                strength=strength, gamut=gamut, indoor_weight=indoor_weight,
            )
            record["tile_retry_count"] = retry_count
            if record["status"] != "applied":
                return None, None, record
            return Image.fromarray(hdr, mode="F"), Image.fromarray(odds, mode="F"), record
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        return None, None, {"status": "unavailable", "reason": str(exc)[:400]}


# A renderer uses the same independent reference more than once, and a batch
# may vary only SDR exposure. Keep at most four successful cue sets in memory.
# Unavailable helpers are never cached, so a transient failure can recover.
_SUBJECT_CACHE: OrderedDict[tuple, tuple] = OrderedDict()


def detect_subject_fields(
    reference: Path, *, high_key_weight: float, strength: float, gamut: str,
    indoor_weight: float = 0,
) -> tuple[Image.Image | None, Image.Image | None, dict[str, Any]]:
    try:
        helper = _vision_helper()
        if helper is None:
            return None, None, {"status": "unavailable", "reason": "macOS Vision helper unavailable"}
        key = (str(helper), hashlib.sha256(reference.read_bytes()).hexdigest(),
               high_key_weight, strength, gamut, indoor_weight)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, None, {"status": "unavailable", "reason": str(exc)[:400]}
    cached = _SUBJECT_CACHE.get(key)
    if cached is not None:
        _SUBJECT_CACHE.move_to_end(key)
        h, s, record = cached
        return (h.copy() if h is not None else None,
                s.copy() if s is not None else None,
                {**deepcopy(record), "reference_cache_hit": True})
    errors = []
    for attempt in range(2):
        h, s, record = _detect_subject_fields_uncached(
            reference, high_key_weight=high_key_weight, strength=strength,
            gamut=gamut, indoor_weight=indoor_weight)
        if record.get("status") != "unavailable":
            record = {**record, "helper_attempts": attempt + 1}
            if errors:
                record["recovered_helper_errors"] = errors
            _SUBJECT_CACHE[key] = (h.copy() if h is not None else None,
                                   s.copy() if s is not None else None, deepcopy(record))
            while len(_SUBJECT_CACHE) > 4:
                _SUBJECT_CACHE.popitem(last=False)
            return h, s, record
        errors.append(record.get("reason", "unknown helper failure"))
    return h, s, {**record, "helper_attempts": 2, "helper_errors": errors}
