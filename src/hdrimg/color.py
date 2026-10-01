from __future__ import annotations

import numpy as np

# D65 matrices from ITU-R BT.2020 and IEC 61966-2-1 definitions.
REC2020_TO_XYZ = np.array(
    [
        [0.63695805, 0.14461690, 0.16888098],
        [0.26270021, 0.67799807, 0.05930172],
        [0.00000000, 0.02807269, 1.06098506],
    ],
    dtype=np.float32,
)
SRGB_TO_XYZ = np.array(
    [
        [0.41239080, 0.35758434, 0.18048079],
        [0.21263901, 0.71516868, 0.07219232],
        [0.01933082, 0.11919478, 0.95053215],
    ],
    dtype=np.float32,
)
DISPLAY_P3_TO_XYZ = np.array(
    [
        [0.48657095, 0.26566769, 0.19821729],
        [0.22897456, 0.69173852, 0.07928691],
        [0.00000000, 0.04511338, 1.04394437],
    ],
    dtype=np.float32,
)
XYZ_TO_SRGB = np.linalg.inv(SRGB_TO_XYZ).astype(np.float32)
SRGB_TO_REC2020 = (np.linalg.inv(REC2020_TO_XYZ) @ SRGB_TO_XYZ).astype(np.float32)
REC2020_TO_SRGB = (XYZ_TO_SRGB @ REC2020_TO_XYZ).astype(np.float32)
REC2020_TO_DISPLAY_P3 = (
    np.linalg.inv(DISPLAY_P3_TO_XYZ) @ REC2020_TO_XYZ
).astype(np.float32)
DISPLAY_P3_TO_SRGB = (XYZ_TO_SRGB @ DISPLAY_P3_TO_XYZ).astype(np.float32)
SRGB_TO_DISPLAY_P3 = np.linalg.inv(DISPLAY_P3_TO_SRGB).astype(np.float32)

_M1 = np.array(
    [
        [0.4122214708, 0.5363325363, 0.0514459929],
        [0.2119034982, 0.6806995451, 0.1073969566],
        [0.0883024619, 0.2817188376, 0.6299787005],
    ],
    dtype=np.float32,
)
_M2 = np.array(
    [
        [0.2104542553, 0.7936177850, -0.0040720468],
        [1.9779984951, -2.4285922050, 0.4505937099],
        [0.0259040371, 0.7827717662, -0.8086757660],
    ],
    dtype=np.float32,
)
_M1_INV = np.linalg.inv(_M1).astype(np.float32)
_M2_INV = np.linalg.inv(_M2).astype(np.float32)


def _matmul(rgb: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    from .accelerator import matmul
    accelerated = matmul(np.asarray(rgb, dtype=np.float32), matrix)
    if accelerated is not None:
        return accelerated
    return np.asarray(rgb, dtype=np.float32) @ matrix.T


def rec2020_to_linear_srgb(rgb: np.ndarray) -> np.ndarray:
    return _matmul(rgb, REC2020_TO_SRGB)


def linear_srgb_to_rec2020(rgb: np.ndarray) -> np.ndarray:
    return _matmul(rgb, SRGB_TO_REC2020)


def rec2020_to_linear_display_p3(rgb: np.ndarray) -> np.ndarray:
    return _matmul(rgb, REC2020_TO_DISPLAY_P3)


def linear_srgb_to_oklab(rgb: np.ndarray) -> np.ndarray:
    lms = _matmul(rgb, _M1)
    return _matmul(np.cbrt(lms), _M2)


def oklab_to_linear_srgb(lab: np.ndarray) -> np.ndarray:
    lms_root = _matmul(lab, _M2_INV)
    return _matmul(lms_root * lms_root * lms_root, _M1_INV)


def adjust_oklab_chroma(
    rgb: np.ndarray,
    *,
    target: str,
    amount: float,
    warm_color_separation: float = 0.0,
    vibrance: float = 0.0,
) -> np.ndarray:
    """Adjust OKLab chroma, with optional separation of warm light and dark tones."""
    values = np.asarray(rgb, dtype=np.float32)
    if target == "srgb":
        srgb = values
        convert_back = lambda value: value
    elif target == "rec2020":
        srgb = rec2020_to_linear_srgb(values)
        convert_back = linear_srgb_to_rec2020
    elif target == "display-p3":
        srgb = _matmul(values, DISPLAY_P3_TO_SRGB)
        convert_back = lambda value: _matmul(value, SRGB_TO_DISPLAY_P3)
    else:
        raise ValueError(f"Unknown target gamut: {target}")
    lab = linear_srgb_to_oklab(srgb)
    lab[..., 1:] *= np.float32(amount)
    if vibrance:
        lightness = lab[..., 0]
        chroma = np.hypot(lab[..., 1], lab[..., 2])
        hue = np.degrees(np.arctan2(lab[..., 2], lab[..., 1])) % 360.0
        low_chroma = _smoothstep((chroma - 0.015) / 0.025)
        high_chroma = 1.0 - _smoothstep((chroma - 0.10) / 0.10)
        middle = _smoothstep((lightness - 0.18) / 0.20) * (
            1.0 - _smoothstep((lightness - 0.78) / 0.16)
        )
        skin = (
            _smoothstep((hue - 20.0) / 15.0)
            * (1.0 - _smoothstep((hue - 80.0) / 15.0))
            * _smoothstep((lightness - 0.40) / 0.15)
        )
        gain = 1.0 + np.float32(vibrance) * low_chroma * high_chroma * middle * (
            1.0 - 0.75 * skin
        )
        lab[..., 1:] *= gain[..., None]
    if warm_color_separation:
        # Separate light skin/wood from darker auburn tones without shifting
        # neutral pixels or cool colors. This is an optional creative look.
        lightness = lab[..., 0]
        a = lab[..., 1].copy()
        b = lab[..., 2].copy()
        hue = np.degrees(np.arctan2(b, a)) % 360.0
        chroma = np.hypot(a, b)
        warm = (
            _smoothstep((hue - 20.0) / 15.0)
            * (1.0 - _smoothstep((hue - 80.0) / 15.0))
            * _smoothstep((chroma - 0.025) / 0.03)
        )
        strength = np.float32(warm_color_separation)
        light_mix = _smoothstep((lightness - 0.55) / 0.20)
        angle = np.deg2rad((-5.0 + 15.0 * light_mix) * warm * strength)
        chroma_boost = (
            1.0
            + 0.16 * np.exp(-((lightness - 0.56) / 0.16) ** 2) * warm * strength
        )
        lab[..., 0] = (
            lightness
            + 0.04 * _smoothstep((lightness - 0.62) / 0.16) * warm * strength
        )
        lab[..., 1] = (a * np.cos(angle) - b * np.sin(angle)) * chroma_boost
        lab[..., 2] = (a * np.sin(angle) + b * np.cos(angle)) * chroma_boost
    return convert_back(oklab_to_linear_srgb(lab))


def refine_phone_color(
    rgb: np.ndarray, *, target: str, dark_weight: float, indoor_weight: float,
    neutral_protection: bool = False,
    skin_protection: np.ndarray | None = None,
) -> np.ndarray:
    """Apply small, hue-selective corrections measured from paired phone scenes.

    Neutral pixels stay fixed. The blue and warm corrections are deliberately
    smooth in hue and chroma so skin and color boundaries have no hard mask.
    """
    values = np.asarray(rgb, dtype=np.float32)
    if target == "srgb":
        srgb = values
        convert_back = lambda value: value
    elif target == "rec2020":
        srgb = rec2020_to_linear_srgb(values)
        convert_back = linear_srgb_to_rec2020
    elif target == "display-p3":
        srgb = _matmul(values, DISPLAY_P3_TO_SRGB)
        convert_back = lambda value: _matmul(value, SRGB_TO_DISPLAY_P3)
    else:
        raise ValueError(f"Unknown target gamut: {target}")
    lab = linear_srgb_to_oklab(srgb)
    hue = np.degrees(np.arctan2(lab[..., 2], lab[..., 1])) % 360.0
    chroma = np.hypot(lab[..., 1], lab[..., 2])
    colorful = _smoothstep((chroma - 0.015) / 0.020)
    blue = (
        _smoothstep((hue - 185.0) / 35.0)
        * (1.0 - _smoothstep((hue - 270.0) / 30.0))
        * colorful
    )
    warm = (
        _smoothstep((hue - 10.0) / 20.0)
        * (1.0 - _smoothstep((hue - 85.0) / 20.0))
        * colorful
    )
    indoor_rotation = -5.0 if neutral_protection else -15.0
    warm_rotation = np.full_like(hue, 15.0)
    if neutral_protection:
        relative = chroma / np.maximum(lab[..., 0], .05)
        warm_rotation -= (12 * _smoothstep((hue-25)/25)
            * (1-_smoothstep((hue-70)/20))
            * _smoothstep((relative-.045)/.035) * (1-dark_weight))
    angle = np.deg2rad(
        21.0 * blue + (warm_rotation * (1.0 - indoor_weight) + indoor_rotation * indoor_weight) * warm
    )
    gain = (1.0 + 0.27 * blue) * (1.0 - 0.25 * dark_weight * (1.0 - 0.7 * blue))
    gain *= 1.0 - 0.18 * indoor_weight * warm
    if neutral_protection:
        # Use chroma relative to lightness so gain-map brightness does not turn
        # a weak warm cast into a different color category. Skin with stronger
        # relative chroma stays outside this gently desaturated neutral zone.
        relative_chroma = chroma / np.maximum(lab[..., 0], 0.05)
        weak_color = 1.0 - _smoothstep((relative_chroma - 0.025) / 0.055)
        warm_neutral = _smoothstep((hue + 5.0) / 20.0) * (
            1.0 - _smoothstep((hue - 90.0) / 30.0)
        )
        skin = 0.0 if skin_protection is None else np.clip(skin_protection, 0, 1)
        gain *= 1.0 - 0.60 * weak_color * warm_neutral * (1.0 - skin)
    adjusted_chroma = chroma * gain
    lab[..., 1] = adjusted_chroma * np.cos(np.deg2rad(hue) + angle)
    lab[..., 2] = adjusted_chroma * np.sin(np.deg2rad(hue) + angle)
    return convert_back(oklab_to_linear_srgb(lab))


def compress_gamut(
    rgb: np.ndarray, *, target: str, upper: float, iterations: int = 12
) -> np.ndarray:
    """Compress chroma in OKLab until every pixel fits the target RGB cube."""
    values = np.asarray(rgb, dtype=np.float32)
    from .accelerator import library, matrix_kernel_compatible
    if library() is not None and matrix_kernel_compatible() and values.ndim >= 2:
        width = values.shape[-2]
        aligned = width - width % 4
        if aligned and aligned != width:
            # Accelerate rounds complete four-pixel tiles and scalar row tails
            # differently. Split those independent pixels before packing the
            # active gamut search, preserving both original arithmetic paths.
            prefix = compress_gamut(values[..., :aligned, :], target=target,
                                    upper=upper, iterations=iterations)
            tail = compress_gamut(values[..., aligned:, :], target=target,
                                  upper=upper, iterations=iterations)
            return np.concatenate((prefix, tail), axis=-2)
    if target == "srgb":
        to_srgb = values
        from_srgb = lambda value: value
    elif target == "rec2020":
        to_srgb = rec2020_to_linear_srgb(values)
        from_srgb = linear_srgb_to_rec2020
    elif target == "display-p3":
        to_srgb = _matmul(values, DISPLAY_P3_TO_SRGB)
        from_srgb = lambda value: _matmul(value, SRGB_TO_DISPLAY_P3)
    else:
        raise ValueError(f"Unknown target gamut: {target}")

    def convert(lab_value: np.ndarray) -> np.ndarray:
        return from_srgb(oklab_to_linear_srgb(lab_value))

    lab = linear_srgb_to_oklab(to_srgb)
    max_l = np.cbrt(np.float32(upper))
    lab[..., 0] = np.clip(lab[..., 0], 0.0, max_l)
    original = convert(lab)
    valid = np.all((original >= 0.0) & (original <= upper), axis=-1)
    if np.all(valid):
        return original

    # In the app, complete four-pixel tiles use the same fused arithmetic as
    # Accelerate. Only out-of-gamut pixels need the twelve-step chroma search.
    # Pad packed tiles so their rounding remains the original vector rounding;
    # retain the reference path for rows with scalar tails.
    if library() is not None and matrix_kernel_compatible() and values.ndim >= 2 and values.shape[-2] % 4 == 0:
        active = ~valid
        count = int(np.count_nonzero(active))
        packed = np.zeros(((count + 3) // 4 * 4, 3), dtype=np.float32)
        packed[:count] = lab[active]
        lo = np.zeros(len(packed), np.float32)
        hi = np.ones(len(packed), np.float32)
        for _ in range(iterations):
            mid = (lo + hi) * 0.5
            candidate_lab = packed.copy()
            candidate_lab[:, 1:] *= mid[:, None]
            candidate = convert(candidate_lab)
            inside = np.all((candidate >= 0.0) & (candidate <= upper), axis=-1)
            lo = np.where(inside, mid, lo)
            hi = np.where(inside, hi, mid)
        packed[:, 1:] *= lo[:, None]
        original[active] = convert(packed)[:count]
        return np.clip(original, 0.0, upper)

    lo = np.zeros(lab.shape[:-1], dtype=np.float32)
    hi = np.ones(lab.shape[:-1], dtype=np.float32)
    lo[valid] = 1.0
    for _ in range(iterations):
        mid = (lo + hi) * 0.5
        candidate_lab = lab.copy()
        candidate_lab[..., 1:] *= mid[..., None]
        candidate = convert(candidate_lab)
        inside = np.all((candidate >= 0.0) & (candidate <= upper), axis=-1)
        lo = np.where(inside, mid, lo)
        hi = np.where(inside, hi, mid)
    result_lab = lab.copy()
    result_lab[..., 1:] *= lo[..., None]
    return np.clip(convert(result_lab), 0.0, upper)


def srgb_oetf(linear: np.ndarray) -> np.ndarray:
    value = np.clip(np.asarray(linear, dtype=np.float32), 0.0, 1.0)
    return np.where(
        value <= 0.0031308,
        12.92 * value,
        1.055 * np.power(value, 1.0 / 2.4) - 0.055,
    )


def _smoothstep(value: np.ndarray) -> np.ndarray:
    t = np.clip(value, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)
