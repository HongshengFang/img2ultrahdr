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
XYZ_TO_SRGB = np.linalg.inv(SRGB_TO_XYZ).astype(np.float32)
SRGB_TO_REC2020 = (np.linalg.inv(REC2020_TO_XYZ) @ SRGB_TO_XYZ).astype(np.float32)
REC2020_TO_SRGB = (XYZ_TO_SRGB @ REC2020_TO_XYZ).astype(np.float32)

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
    return np.asarray(rgb, dtype=np.float32) @ matrix.T


def rec2020_to_linear_srgb(rgb: np.ndarray) -> np.ndarray:
    return _matmul(rgb, REC2020_TO_SRGB)


def linear_srgb_to_rec2020(rgb: np.ndarray) -> np.ndarray:
    return _matmul(rgb, SRGB_TO_REC2020)


def linear_srgb_to_oklab(rgb: np.ndarray) -> np.ndarray:
    lms = _matmul(rgb, _M1)
    return _matmul(np.cbrt(lms), _M2)


def oklab_to_linear_srgb(lab: np.ndarray) -> np.ndarray:
    lms_root = _matmul(lab, _M2_INV)
    return _matmul(lms_root * lms_root * lms_root, _M1_INV)


def adjust_oklab_chroma(
    rgb: np.ndarray, *, target: str, amount: float
) -> np.ndarray:
    """Scale OKLab chroma while preserving perceptual lightness and hue."""
    values = np.asarray(rgb, dtype=np.float32)
    if target == "srgb":
        srgb = values
        convert_back = lambda value: value
    elif target == "rec2020":
        srgb = rec2020_to_linear_srgb(values)
        convert_back = linear_srgb_to_rec2020
    else:
        raise ValueError(f"Unknown target gamut: {target}")
    lab = linear_srgb_to_oklab(srgb)
    lab[..., 1:] *= np.float32(amount)
    return convert_back(oklab_to_linear_srgb(lab))


def compress_gamut(
    rgb: np.ndarray, *, target: str, upper: float, iterations: int = 12
) -> np.ndarray:
    """Compress chroma in OKLab until every pixel fits the target RGB cube."""
    values = np.asarray(rgb, dtype=np.float32)
    if target == "srgb":
        to_srgb = values
        from_srgb = lambda value: value
    elif target == "rec2020":
        to_srgb = rec2020_to_linear_srgb(values)
        from_srgb = linear_srgb_to_rec2020
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
